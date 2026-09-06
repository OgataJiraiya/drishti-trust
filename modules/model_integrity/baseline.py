"""Deterministic, bounded M4 reference-baseline comparison."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
from typing import Any

import numpy as np

from backend.core.canonical import canonical_json_bytes
from .baseline_models import (
    ArtifactComparison, BaselineComparisonIssue, BaselineComparisonLimits,
    BaselineComparisonReport, BaselineVerification, BehavioralComparison,
    BehavioralIssueDelta, BehavioralProtocol, BehavioralTriggerDelta, ChangeState,
    ComparisonStatus, DeltaState, IssueDelta, NamedChange, ParameterComparison,
    ReferenceBaseline, ReferenceStatus, StructureComparison,
)
from .behavioral import BehavioralIntegrityService, BehavioralLimits, _canonical_array_bytes
from .behavioral_models import (
    BehavioralAnalysisReport, InputContract, OutputContract, TriggerSpec,
)
from .fingerprint import fingerprint
from .models import AnalysisStatus, ModelManifest
from .parameter_analysis import ParameterAnalysisLimits, tensor_value_commitments
from .service import ModelIntegrityService

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BASELINE_KEYS = {"schema_version", "reference_artifact_id", "artifact_sha256",
    "structural_sha256", "parameter_metadata_sha256", "parameter_value_sha256",
    "parameter_value_status", "baseline_payload_sha256", "baseline_id"}


def _baseline_payload(manifest: ModelManifest) -> dict[str, Any]:
    fp = manifest.fingerprints
    return {"schema_version": 1, "reference_artifact_id": manifest.artifact.artifact_id,
        "artifact_sha256": fp.artifact_sha256, "structural_sha256": fp.structural_sha256,
        "parameter_metadata_sha256": fp.parameter_metadata_sha256,
        "parameter_value_sha256": fp.parameter_value_sha256,
        "parameter_value_status": fp.parameter_value_status.value}


def create_baseline(path: Path | str, *, service: ModelIntegrityService | None = None
                    ) -> ReferenceBaseline:
    """Create a deterministic non-executing M1/M2 reference record."""
    manifest = (service or ModelIntegrityService()).inspect(path)
    payload = _baseline_payload(manifest); digest = sha256(canonical_json_bytes(payload)).hexdigest()
    return ReferenceBaseline(**payload, baseline_payload_sha256=digest,
        baseline_id=f"baseline:sha256:{digest}")


def verify_baseline(value: dict[str, Any]) -> BaselineVerification:
    errors: list[str] = []
    if not isinstance(value, dict) or set(value) != _BASELINE_KEYS:
        return BaselineVerification(False, None, ["BASELINE_FIELDS_INVALID"])
    if any(isinstance(item, str) and len(item) > 512 for item in value.values()):
        errors.append("BASELINE_STRING_LIMIT_EXCEEDED")
    if type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        errors.append("BASELINE_SCHEMA_UNSUPPORTED")
    required_strings = ("reference_artifact_id", "artifact_sha256", "parameter_value_status",
                        "baseline_payload_sha256", "baseline_id")
    if any(not isinstance(value.get(key), str) for key in required_strings):
        errors.append("BASELINE_FIELD_TYPES_INVALID")
    optional_hashes = ("structural_sha256", "parameter_metadata_sha256", "parameter_value_sha256")
    if any(value.get(key) is not None and not isinstance(value.get(key), str) for key in optional_hashes):
        errors.append("BASELINE_FIELD_TYPES_INVALID")
    hashes = ["artifact_sha256", "baseline_payload_sha256"]
    hashes += [key for key in ("structural_sha256", "parameter_metadata_sha256",
                               "parameter_value_sha256") if value.get(key) is not None]
    if any(not isinstance(value.get(key), str) or not _SHA256.fullmatch(value[key]) for key in hashes):
        errors.append("BASELINE_FINGERPRINT_INVALID")
    artifact = value.get("artifact_sha256")
    if value.get("reference_artifact_id") != f"model:sha256:{artifact}":
        errors.append("BASELINE_ARTIFACT_ID_INVALID")
    status = value.get("parameter_value_status")
    if not isinstance(status, str) or status not in {item.value for item in AnalysisStatus}:
        errors.append("BASELINE_PARAMETER_STATUS_INVALID")
    if (isinstance(status, str) and
            ((status in {AnalysisStatus.COMPLETE, AnalysisStatus.PARTIAL}
              and value.get("parameter_value_sha256") is None)
             or (status == AnalysisStatus.UNAVAILABLE
                 and value.get("parameter_value_sha256") is not None))):
        errors.append("BASELINE_PARAMETER_COMPLETENESS_INVALID")
    payload = {key: value[key] for key in _BASELINE_KEYS
               if key not in {"baseline_payload_sha256", "baseline_id"}}
    try: digest = sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError):
        return BaselineVerification(False, None, sorted(set(errors + ["BASELINE_FIELD_TYPES_INVALID"])))
    if value.get("baseline_payload_sha256") != digest: errors.append("BASELINE_DIGEST_MISMATCH")
    if value.get("baseline_id") != f"baseline:sha256:{digest}": errors.append("BASELINE_ID_INVALID")
    if errors: return BaselineVerification(False, None, sorted(set(errors)))
    return BaselineVerification(True, ReferenceBaseline(**value), [])


def load_baseline(path: Path | str, *, limits: BaselineComparisonLimits | None = None
                  ) -> BaselineVerification:
    limits = limits or BaselineComparisonLimits(); candidate = Path(path)
    try:
        if not candidate.is_file() or candidate.is_symlink() \
                or candidate.stat().st_size > limits.max_baseline_file_bytes:
            return BaselineVerification(False, None, ["BASELINE_FILE_INVALID_OR_OVERSIZED"])
        def no_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ValueError("duplicate JSON field")
                result[key] = value
            return result
        value = json.loads(candidate.read_text("utf-8"), object_pairs_hook=no_duplicates,
                           parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        return BaselineVerification(False, None, ["BASELINE_JSON_INVALID"])
    return verify_baseline(value)


def _state(reference: str | None, candidate: str | None) -> ChangeState:
    if reference is None or candidate is None: return ChangeState.UNAVAILABLE
    return ChangeState.SAME if reference == candidate else ChangeState.CHANGED


def _named_map(items: list[Any]) -> tuple[dict[str, Any], bool]:
    result = {}; duplicate = False
    for item in items:
        if item.name in result: duplicate = True
        else: result[item.name] = item
    return result, duplicate


def _changes(reference: dict[str, Any], candidate: dict[str, Any], render,
             maximum: int, limitation: str, limitations: list[str]) -> list[NamedChange]:
    names = sorted(set(reference) | set(candidate))
    if any(len(name) > 512 for name in names): limitations.append("COMPARISON_STRINGS_TRUNCATED")
    result = [NamedChange(name[:512], render(reference.get(name)), render(candidate.get(name)))
              for name in names
              if render(reference.get(name)) != render(candidate.get(name))]
    if len(result) > maximum:
        limitations.append(limitation); return result[:maximum]
    return result


def _structure(reference: ModelManifest, candidate: ModelManifest,
               limits: BaselineComparisonLimits, limitations: list[str]) -> StructureComparison:
    rf, cf = reference.fingerprints.structural_sha256, candidate.fingerprints.structural_sha256
    state = _state(rf, cf); codes: list[str] = []
    empty: list[NamedChange] = []
    if reference.structure is None or candidate.structure is None:
        return StructureComparison(state, rf, cf, [], empty, empty, empty, empty, empty)
    r, c = reference.structure, candidate.structure
    operators = _changes(r.operator_profile, c.operator_profile, lambda item: item,
        limits.max_operator_deltas, "OPERATOR_DELTAS_TRUNCATED", limitations)
    ro = {item["domain"]: item["version"] for item in r.opsets}
    co = {item["domain"]: item["version"] for item in c.opsets}
    opsets = _changes(ro, co, lambda item: item, limits.max_operator_deltas,
        "OPSET_DELTAS_TRUNCATED", limitations)
    ri, rd = _named_map(r.inputs); ci, cd = _named_map(c.inputs)
    inputs = _changes(ri, ci, lambda item: None if item is None else [item.dtype, item.shape],
        limits.max_operator_deltas, "INPUT_DELTAS_TRUNCATED", limitations)
    ro2, rd2 = _named_map(r.outputs); co2, cd2 = _named_map(c.outputs)
    outputs = _changes(ro2, co2, lambda item: None if item is None else [item.dtype, item.shape],
        limits.max_operator_deltas, "OUTPUT_DELTAS_TRUNCATED", limitations)
    rt, rd3 = _named_map(r.initializers); ct, cd3 = _named_map(c.initializers)
    initializers = _changes(rt, ct, lambda item: None if item is None else
        [item.dtype, item.shape, item.element_count], limits.max_initializer_deltas,
        "INITIALIZER_DELTAS_TRUNCATED", limitations)
    if any((rd, cd, rd2, cd2, rd3, cd3)):
        limitations.append("DUPLICATE_TENSOR_NAMES_INCOMPARABLE")
        state = ChangeState.INCOMPARABLE
    if state == ChangeState.CHANGED: codes.append("STRUCTURAL_FINGERPRINT_CHANGED")
    for present, code in ((opsets, "OPSET_CHANGED"), (inputs, "INPUT_CONTRACT_CHANGED"),
            (outputs, "OUTPUT_CONTRACT_CHANGED"), (operators, "OPERATOR_PROFILE_CHANGED"),
            (initializers, "INITIALIZER_METADATA_CHANGED")):
        if present: codes.append(code)
    if set(rt) != set(ct): codes.append("INITIALIZER_SET_CHANGED")
    return StructureComparison(state, rf, cf, sorted(set(codes)), operators, opsets,
                               inputs, outputs, initializers)


def _tensor_commitments(path: Path | str, manifest: ModelManifest,
                        parameter_limits: ParameterAnalysisLimits | None = None) -> list[dict[str, Any]] | None:
    if manifest.artifact.claimed_format != "onnx" or manifest.structure is None: return None
    try:
        import onnx
        return tensor_value_commitments(onnx.load_model(path, load_external_data=False), parameter_limits)
    except Exception: return None


def _parameters(reference_path: Path | str, candidate_path: Path | str,
                reference: ModelManifest, candidate: ModelManifest,
                limits: BaselineComparisonLimits, limitations: list[str],
                parameter_limits: ParameterAnalysisLimits | None = None) -> ParameterComparison:
    rfp, cfp = reference.fingerprints, candidate.fingerprints
    metadata = _state(rfp.parameter_metadata_sha256, cfp.parameter_metadata_sha256)
    if rfp.parameter_value_status == AnalysisStatus.COMPLETE \
            and cfp.parameter_value_status == AnalysisStatus.COMPLETE:
        values = _state(rfp.parameter_value_sha256, cfp.parameter_value_sha256)
    else: values = ChangeState.UNAVAILABLE
    rc = _tensor_commitments(reference_path, reference, parameter_limits)
    cc = _tensor_commitments(candidate_path, candidate, parameter_limits)
    if rc is None or cc is None:
        limitations.append("TENSOR_VALUE_DETAILS_INCOMPARABLE")
        return ParameterComparison(metadata, values, rfp.parameter_metadata_sha256,
            cfp.parameter_metadata_sha256, rfp.parameter_value_sha256, cfp.parameter_value_sha256,
            rfp.parameter_value_status.value, cfp.parameter_value_status.value, [], [], [], [], [])
    duplicate_commitments = (len({item["name"] for item in rc}) != len(rc)
                             or len({item["name"] for item in cc}) != len(cc))
    if duplicate_commitments:
        limitations.append("DUPLICATE_TENSOR_VALUE_IDENTITIES_INCOMPARABLE")
        values = ChangeState.INCOMPARABLE
        return ParameterComparison(metadata, values, rfp.parameter_metadata_sha256,
            cfp.parameter_metadata_sha256, rfp.parameter_value_sha256, cfp.parameter_value_sha256,
            rfp.parameter_value_status.value, cfp.parameter_value_status.value, [], [], [], [], [])
    rm = {item["name"]: item for item in rc}; cm = {item["name"]: item for item in cc}
    added = sorted(set(cm) - set(rm)); removed = sorted(set(rm) - set(cm))
    shared = sorted(set(rm) & set(cm))
    changed = [name for name in shared if rm[name]["value_sha256"] != cm[name]["value_sha256"]
               and rm[name]["shape"] == cm[name]["shape"] and rm[name]["dtype"] == cm[name]["dtype"]]
    shapes = [name for name in shared if rm[name]["shape"] != cm[name]["shape"]]
    dtypes = [name for name in shared if rm[name]["dtype"] != cm[name]["dtype"]]
    lists = [added, removed, changed, shapes, dtypes]
    if any(len(name) > 512 for items in lists for name in items):
        limitations.append("COMPARISON_STRINGS_TRUNCATED")
    if any(len(items) > limits.max_changed_tensors for items in lists):
        limitations.append("CHANGED_TENSORS_TRUNCATED")
    lists = [[name[:512] for name in items[:limits.max_changed_tensors]] for items in lists]
    return ParameterComparison(metadata, values, rfp.parameter_metadata_sha256,
        cfp.parameter_metadata_sha256, rfp.parameter_value_sha256, cfp.parameter_value_sha256,
        rfp.parameter_value_status.value, cfp.parameter_value_status.value, *lists)


def _issue_deltas(reference: ModelManifest, candidate: ModelManifest,
                  limits: BaselineComparisonLimits, limitations: list[str]) -> list[IssueDelta]:
    def identities(manifest):
        report = manifest.parameter_analysis
        return {(item.code, item.tensor_name, item.channel_index) for item in (report.issues if report else [])
                if item.code not in {"PARAMETER_ANALYSIS_PARTIAL", "PARAMETER_ANALYSIS_UNAVAILABLE"}}
    r, c = identities(reference), identities(candidate)
    if any(len(key[0]) > 128 or (key[1] is not None and len(key[1]) > 512) for key in r | c):
        limitations.append("COMPARISON_STRINGS_TRUNCATED")
    result = [IssueDelta(DeltaState.PERSISTING if key in r and key in c else
             DeltaState.NEW if key in c else DeltaState.RESOLVED,
             key[0][:128], key[1][:512] if key[1] else None, key[2])
              for key in sorted(r | c, key=lambda item: (item[1] or "", item[2] or -1, item[0]))]
    if len(result) > limits.max_issue_deltas:
        limitations.append("PARAMETER_ISSUE_DELTAS_TRUNCATED")
    return result[:limits.max_issue_deltas]


def _interpret(artifact: ArtifactComparison, structure: StructureComparison,
               parameters: ParameterComparison) -> str:
    if artifact.state == ChangeState.SAME:
        return "The reference and candidate are the exact same artifact bytes."
    if structure.state == ChangeState.SAME and parameters.value_state == ChangeState.SAME:
        return "Artifact serialization or non-structural metadata changed without an observed structure or parameter-value change."
    if structure.state == ChangeState.SAME and parameters.value_state == ChangeState.CHANGED:
        return "The candidate preserves the observed graph structure but changes parameter values; review changed tensors and provenance."
    if structure.state == ChangeState.CHANGED:
        return "The observed model structure changed; review the bounded structural and parameter-metadata deltas."
    return "Static evidence is incomplete; absence of reported differences must not be interpreted as identity or safety."


class BaselineComparisonService:
    def __init__(self, *, limits: BaselineComparisonLimits | None = None,
                 inspection_service: ModelIntegrityService | None = None) -> None:
        self.limits = limits or BaselineComparisonLimits()
        self.inspection_service = inspection_service or ModelIntegrityService()

    def compare(self, reference_path: Path | str, candidate_path: Path | str, *,
                expected_reference_sha256: str | None = None,
                reference_registry_state: str | None = None) -> BaselineComparisonReport:
        """Compare M1/M2 evidence only. This method never constructs or invokes M3."""
        reference = self.inspection_service.inspect(reference_path)
        candidate = self.inspection_service.inspect(candidate_path)
        if expected_reference_sha256 is not None \
                and (not isinstance(expected_reference_sha256, str)
                     or not _SHA256.fullmatch(expected_reference_sha256)):
            expected_reference_valid = False
        else: expected_reference_valid = True
        if reference_registry_state is not None and not isinstance(reference_registry_state, str):
            raise ValueError("reference_registry_state must be a string")
        limitations: list[str] = []
        artifact = ArtifactComparison(reference.artifact.sha256, candidate.artifact.sha256,
            _state(reference.artifact.sha256, candidate.artifact.sha256))
        structure = _structure(reference, candidate, self.limits, limitations)
        parameters = _parameters(reference_path, candidate_path, reference, candidate,
                                 self.limits, limitations, self.inspection_service.parameter_limits)
        deltas = _issue_deltas(reference, candidate, self.limits, limitations)
        verified = (expected_reference_sha256 is not None and expected_reference_valid
                    and expected_reference_sha256 == reference.artifact.sha256)
        reference_status = ReferenceStatus.VERIFIED_REFERENCE if verified else ReferenceStatus.DESIGNATED_ONLY
        if expected_reference_sha256 is not None and not expected_reference_valid:
            limitations.append("EXPECTED_REFERENCE_SHA256_INVALID")
        elif expected_reference_sha256 is not None and not verified:
            limitations.append("EXPECTED_REFERENCE_SHA256_MISMATCH")
        if reference_registry_state is not None and len(reference_registry_state) > 128:
            limitations.append("REFERENCE_REGISTRY_STATE_TRUNCATED")
        registry_state = reference_registry_state[:128] if reference_registry_state is not None else None
        expected_value = (expected_reference_sha256 if expected_reference_valid else
                          expected_reference_sha256[:128]
                          if isinstance(expected_reference_sha256, str) else None)
        dimensions = [structure.state, parameters.metadata_state, parameters.value_state]
        status = ComparisonStatus.COMPLETE if all(item != ChangeState.UNAVAILABLE for item in dimensions) else ComparisonStatus.PARTIAL
        if any("TRUNCATED" in item or "INCOMPARABLE" in item
               or item.startswith("EXPECTED_REFERENCE_SHA256_") for item in limitations):
            status = ComparisonStatus.PARTIAL
        limitations = sorted(set(limitations))[:self.limits.max_limitations]
        interpretation = _interpret(artifact, structure, parameters)[:self.limits.max_explanation_length]
        core = {"schema_version": 1, "reference_artifact_id": reference.artifact.artifact_id,
            "candidate_artifact_id": candidate.artifact.artifact_id, "artifact": asdict(artifact),
            "structure": asdict(structure), "parameters": asdict(parameters),
            "parameter_issue_deltas": [asdict(item) for item in deltas],
            "reference_status": reference_status.value, "behavior": None,
            "expected_reference_sha256": expected_value,
            "reference_registry_state": registry_state,
            "interpretation": interpretation, "limitations": limitations}
        comparison_id = f"comparison:sha256:{fingerprint(core)}"
        return BaselineComparisonReport(1, comparison_id, status, reference_status,
            expected_value, registry_state,
            reference.artifact.artifact_id, candidate.artifact.artifact_id, artifact, structure,
            parameters, deltas, None, interpretation, [], limitations)


def corpus_fingerprint(samples: list[np.ndarray]) -> str:
    commitments = []
    for index, sample in enumerate(samples):
        array = np.asarray(sample)
        commitments.append({"index": index, "dtype": array.dtype.str, "shape": list(array.shape),
            "value_sha256": sha256(_canonical_array_bytes(array)).hexdigest()})
    return fingerprint(commitments)


def behavior_protocol(samples: list[np.ndarray], input_contract: InputContract,
                      output_contract: OutputContract, triggers: list[TriggerSpec],
                      limits: BehavioralLimits, runtime_identity: str) -> BehavioralProtocol:
    if not isinstance(runtime_identity, str) or not runtime_identity or len(runtime_identity) > 512:
        raise ValueError("runtime identity must be a bounded non-empty string")
    if any(not item.trigger_id or len(item.trigger_id) > 128 for item in triggers):
        raise ValueError("trigger identifiers must be bounded non-empty strings")
    corpus = corpus_fingerprint(samples); input_fp = fingerprint(asdict(input_contract))
    output_fp = fingerprint(asdict(output_contract)); trigger_fp = fingerprint([asdict(item) for item in triggers])
    limits_fp = fingerprint(asdict(limits))
    payload = {"corpus": corpus, "input": input_fp, "output": output_fp, "triggers": trigger_fp,
               "limits": limits_fp, "runtime": runtime_identity}
    return BehavioralProtocol(f"behavior-protocol:sha256:{fingerprint(payload)}", corpus,
        input_fp, output_fp, trigger_fp, limits_fp, runtime_identity)


def _protocol_id(protocol: BehavioralProtocol) -> str:
    payload = {"corpus": protocol.corpus_sha256, "input": protocol.input_contract_sha256,
        "output": protocol.output_contract_sha256, "triggers": protocol.trigger_suite_sha256,
        "limits": protocol.limits_sha256, "runtime": protocol.runtime_identity}
    return f"behavior-protocol:sha256:{fingerprint(payload)}"


def _behavior_issue_identity(issue) -> tuple[str, str]:
    trigger = next((item.split("=", 1)[1] for item in issue.evidence if item.startswith("trigger=")), "")
    return issue.code, trigger


def compare_behavior_reports(reference: BehavioralAnalysisReport, candidate: BehavioralAnalysisReport,
                             reference_protocol: BehavioralProtocol,
                             candidate_protocol: BehavioralProtocol,
                             *, limits: BaselineComparisonLimits | None = None
                             ) -> BehavioralComparison:
    limits = limits or BaselineComparisonLimits()
    if (reference_protocol.protocol_id != _protocol_id(reference_protocol)
            or candidate_protocol.protocol_id != _protocol_id(candidate_protocol)
            or reference_protocol.protocol_id != candidate_protocol.protocol_id):
        mismatch_fields = [("corpus_sha256", "BEHAVIOR_CORPUS_MISMATCH"),
            ("input_contract_sha256", "BEHAVIOR_INPUT_CONTRACT_MISMATCH"),
            ("output_contract_sha256", "BEHAVIOR_OUTPUT_CONTRACT_MISMATCH"),
            ("trigger_suite_sha256", "BEHAVIOR_TRIGGER_SUITE_MISMATCH"),
            ("limits_sha256", "BEHAVIOR_LIMITS_MISMATCH"),
            ("runtime_identity", "BEHAVIOR_RUNTIME_MISMATCH")]
        reasons = [code for field, code in mismatch_fields
                   if getattr(reference_protocol, field) != getattr(candidate_protocol, field)]
        return BehavioralComparison(ComparisonStatus.INCOMPARABLE, ChangeState.INCOMPARABLE, None,
            reference.status.value, candidate.status.value, ChangeState.INCOMPARABLE, [], [],
            ["BEHAVIOR_PROTOCOL_INCOMPARABLE"], reasons or ["BEHAVIOR_PROTOCOL_MISMATCH"])
    if (reference.runtime != candidate.runtime
            or not reference_protocol.runtime_identity.startswith(reference.runtime)
            or not candidate_protocol.runtime_identity.startswith(candidate.runtime)):
        return BehavioralComparison(ComparisonStatus.INCOMPARABLE, ChangeState.INCOMPARABLE, None,
            reference.status.value, candidate.status.value, ChangeState.INCOMPARABLE, [], [],
            ["BEHAVIOR_PROTOCOL_INCOMPARABLE"], ["BEHAVIOR_RUNTIME_MISMATCH"])
    if (reference.coverage.successful_clean_samples == 0
            or candidate.coverage.successful_clean_samples == 0
            or reference.coverage.successful_trigger_runs == 0
            or candidate.coverage.successful_trigger_runs == 0):
        return BehavioralComparison(ComparisonStatus.UNAVAILABLE, ChangeState.UNAVAILABLE,
            reference_protocol.protocol_id, reference.status.value, candidate.status.value,
            ChangeState.UNAVAILABLE, [], [], [], ["ZERO_BEHAVIORAL_COVERAGE"])
    if (any(len(item.trigger.trigger_id) > 128 for item in reference.triggers + candidate.triggers)
            or any(len(item.code) > 128 or any(len(evidence) > 512 for evidence in item.evidence)
                   for item in reference.issues + candidate.issues)):
        return BehavioralComparison(ComparisonStatus.INCOMPARABLE, ChangeState.INCOMPARABLE,
            reference_protocol.protocol_id, reference.status.value, candidate.status.value,
            ChangeState.INCOMPARABLE, [], [], ["BEHAVIOR_REPORT_INCOMPARABLE"],
            ["BEHAVIOR_REPORT_STRING_LIMIT_EXCEEDED"])
    if (len({item.trigger.trigger_id for item in reference.triggers}) != len(reference.triggers)
            or len({item.trigger.trigger_id for item in candidate.triggers}) != len(candidate.triggers)):
        return BehavioralComparison(ComparisonStatus.INCOMPARABLE, ChangeState.INCOMPARABLE,
            reference_protocol.protocol_id, reference.status.value, candidate.status.value,
            ChangeState.INCOMPARABLE, [], [], ["BEHAVIOR_REPORT_INCOMPARABLE"],
            ["DUPLICATE_BEHAVIOR_TRIGGER_IDENTITIES"])
    rm = {item.trigger.trigger_id: item for item in reference.triggers}
    cm = {item.trigger.trigger_id: item for item in candidate.triggers}
    trigger_deltas = []
    shared_triggers = sorted(set(rm) & set(cm))
    for trigger_id in shared_triggers[:limits.max_behavior_trigger_deltas]:
        r, c = rm[trigger_id], cm[trigger_id]
        compatible_support = (r.samples_successful > 0
                              and r.samples_successful == c.samples_successful)
        def subtract(left, right):
            if not compatible_support or left is None or right is None \
                    or not isfinite(left) or not isfinite(right): return None
            result = right-left
            return result if isfinite(result) else None
        trigger_deltas.append(BehavioralTriggerDelta(trigger_id,
            subtract(r.prediction_flip_rate, c.prediction_flip_rate),
            subtract(r.target_concentration_lift, c.target_concentration_lift),
            subtract(r.control_relative_flip_rate, c.control_relative_flip_rate)))
    ri = {_behavior_issue_identity(item) for item in reference.issues}
    ci = {_behavior_issue_identity(item) for item in candidate.issues}
    issue_deltas = [BehavioralIssueDelta(DeltaState.PERSISTING if key in ri and key in ci else
        DeltaState.NEW if key in ci else DeltaState.RESOLVED, key[0], key[1])
        for key in sorted(ri | ci)]
    behavior_limitations = []
    if len(shared_triggers) > limits.max_behavior_trigger_deltas:
        behavior_limitations.append("BEHAVIOR_TRIGGER_DELTAS_TRUNCATED")
    if len(issue_deltas) > limits.max_issue_deltas:
        behavior_limitations.append("BEHAVIOR_ISSUE_DELTAS_TRUNCATED")
    issue_deltas = issue_deltas[:limits.max_issue_deltas]
    codes = []
    for item in issue_deltas:
        if item.code == "TRIGGER_SENSITIVITY":
            codes.append({DeltaState.NEW: "NEW_TRIGGER_SENSITIVITY",
                          DeltaState.RESOLVED: "RESOLVED_TRIGGER_SENSITIVITY",
                          DeltaState.PERSISTING: "PERSISTING_TRIGGER_SENSITIVITY"}[item.state])
        if item.code == "NONDETERMINISTIC_OUTPUT" and item.state == DeltaState.NEW:
            codes.append("NEW_NONDETERMINISM")
        if item.code == "OUTPUT_NONFINITE" and item.state == DeltaState.NEW:
            codes.append("NEW_OUTPUT_NONFINITE")
    partial = (reference.status.value != "COMPLETE" or candidate.status.value != "COMPLETE"
               or bool(behavior_limitations))
    state = ChangeState.CHANGED if any(item.state != DeltaState.PERSISTING for item in issue_deltas) \
        or any(any(value not in (None, 0) for value in (item.flip_rate_delta,
            item.concentration_lift_delta, item.control_relative_delta)) for item in trigger_deltas) else ChangeState.SAME
    if partial: state = ChangeState.UNAVAILABLE
    return BehavioralComparison(ComparisonStatus.PARTIAL if partial else ComparisonStatus.COMPLETE,
        state, reference_protocol.protocol_id, reference.status.value, candidate.status.value,
        ChangeState.SAME if reference.repeatability_stable == candidate.repeatability_stable else ChangeState.CHANGED,
        trigger_deltas, issue_deltas, sorted(set(codes)),
        ((["BEHAVIORAL_EVIDENCE_PARTIAL"] if partial else []) + behavior_limitations))


def compare_behavior(reference_path: Path | str, candidate_path: Path | str,
                     samples: list[np.ndarray], input_contract: InputContract,
                     output_contract: OutputContract, triggers: list[TriggerSpec], *,
                     behavioral_service: BehavioralIntegrityService | None = None,
                     limits: BaselineComparisonLimits | None = None) -> BehavioralComparison:
    """Explicitly execute both models using the same M3 service and matched protocol."""
    service = behavioral_service or BehavioralIntegrityService()
    runtime_identity = service.runtime.name
    try:
        import onnx
        import sys
        runtime_identity += f"|python={sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        runtime_identity += f"|onnx={onnx.__version__}|numpy={np.__version__}"
    except ImportError: pass
    protocol = behavior_protocol(samples, input_contract, output_contract, triggers,
                                 service.limits, runtime_identity)
    reference = service.analyze(reference_path, samples, input_contract, output_contract, triggers)
    candidate = service.analyze(candidate_path, samples, input_contract, output_contract, triggers)
    return compare_behavior_reports(reference, candidate, protocol, protocol, limits=limits)
