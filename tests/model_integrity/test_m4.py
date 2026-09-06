from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper

from backend.schemas.evidence import Finding
from modules.model_integrity.baseline import (
    BaselineComparisonService, _issue_deltas, _parameters, _state, behavior_protocol, compare_behavior,
    compare_behavior_reports, corpus_fingerprint, create_baseline, load_baseline,
    verify_baseline,
)
from modules.model_integrity.baseline_models import (
    BaselineComparisonLimits, ChangeState, ComparisonStatus, DeltaState, ReferenceStatus,
)
from modules.model_integrity.behavioral import BehavioralIntegrityService, BehavioralLimits, default_triggers
from modules.model_integrity.behavioral_models import (
    BehavioralAnalysisReport, BehavioralAnalysisStatus, BehavioralCoverage, ClassificationKind,
    InputContract, InputLayout, OutputContract, RuntimeStatus, TriggerKind,
)
from modules.model_integrity.parameter_analysis import tensor_value_commitments
from modules.model_integrity.models import AnalysisStatus
from modules.model_integrity.service import ModelIntegrityService
from tests.model_integrity.test_m3 import FakeRuntime, good_output, make_classifier


def make_static(path: Path, *, weight=1.0, op="MatMul", doc="", dtype=TensorProto.FLOAT,
                shape=(2, 2), extra_initializer=False, opset=17) -> Path:
    x = helper.make_tensor_value_info("input", dtype, [1, 2])
    y = helper.make_tensor_value_info("output", dtype, [1, shape[1]])
    np_dtype = np.float32 if dtype == TensorProto.FLOAT else np.float64
    tensor = numpy_helper.from_array(np.full(shape, weight, np_dtype), "weight")
    initializers = [tensor]
    if extra_initializer: initializers.append(numpy_helper.from_array(np.asarray([1], np_dtype), "unused"))
    graph = helper.make_graph([helper.make_node(op, ["input", "weight"], ["output"])],
                              "static", [x], [y], initializers)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", opset)])
    model.doc_string = doc
    path.write_bytes(model.SerializeToString()); return path


def compare(tmp_path, **candidate):
    reference = make_static(tmp_path / "reference.onnx")
    changed = make_static(tmp_path / "candidate.onnx", **candidate)
    return BaselineComparisonService().compare(reference, changed)


def test_static_compare_and_baseline_paths_never_construct_behavior(tmp_path, monkeypatch):
    reference = make_static(tmp_path / "r.onnx"); candidate = make_static(tmp_path / "c.onnx", weight=2)
    monkeypatch.setattr("modules.model_integrity.behavioral.BehavioralIntegrityService.__init__",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("M3 constructed")))
    assert BaselineComparisonService().compare(reference, candidate).artifact.state == ChangeState.CHANGED
    baseline = create_baseline(reference); assert verify_baseline(baseline.to_dict()).valid


def test_all_static_paths_trip_runtime_and_evaluator_spies_if_touched(tmp_path, monkeypatch):
    reference = make_static(tmp_path / "r.onnx"); candidate = make_static(tmp_path / "c.onnx")
    fail = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("execution boundary crossed"))
    monkeypatch.setattr("modules.model_integrity.behavioral.ReferenceOnnxRuntime.__init__", fail)
    monkeypatch.setattr("modules.model_integrity.behavioral.ReferenceOnnxRuntime.run", fail)
    monkeypatch.setattr("onnx.reference.ReferenceEvaluator", fail)
    baseline = create_baseline(reference)
    assert verify_baseline(baseline.to_dict()).valid
    report = BaselineComparisonService().compare(reference, candidate)
    json.dumps(report.to_dict(), allow_nan=False)


@pytest.mark.parametrize("command", ["baseline-create", "baseline-verify", "compare"])
def test_static_cli_commands_never_construct_behavior(tmp_path, monkeypatch, capsys, command):
    from modules.model_integrity import cli
    reference = make_static(tmp_path / "r.onnx"); candidate = make_static(tmp_path / "c.onnx")
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(create_baseline(reference).to_dict()))
    argv = {"baseline-create": ["model-integrity", command, str(reference)],
            "baseline-verify": ["model-integrity", command, str(baseline_path)],
            "compare": ["model-integrity", command, str(reference), str(candidate), "--json"]}[command]
    monkeypatch.setattr("modules.model_integrity.behavioral.BehavioralIntegrityService.__init__",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("M3 constructed")))
    monkeypatch.setattr("sys.argv", argv)
    assert cli.main() == 0
    assert "Traceback" not in capsys.readouterr().out


def test_identical_artifact_and_deterministic_ids(tmp_path):
    path = make_static(tmp_path / "same.onnx")
    first = BaselineComparisonService().compare(path, path)
    second = BaselineComparisonService().compare(path, path)
    assert first.artifact.state == ChangeState.SAME
    assert first.comparison_id == second.comparison_id
    assert first.to_dict() == second.to_dict()
    assert create_baseline(path) == create_baseline(path)
    json.dumps(first.to_dict(), allow_nan=False)


def test_metadata_only_product_demo(tmp_path):
    report = compare(tmp_path, doc="untrusted documentation changed")
    assert report.artifact.state == ChangeState.CHANGED
    assert report.structure.state == ChangeState.SAME
    assert report.parameters.metadata_state == ChangeState.SAME
    assert report.parameters.value_state == ChangeState.SAME


def test_weight_modified_product_demo_identifies_tensor(tmp_path):
    report = compare(tmp_path, weight=2.0)
    assert report.structure.state == ChangeState.SAME
    assert report.parameters.metadata_state == ChangeState.SAME
    assert report.parameters.value_state == ChangeState.CHANGED
    assert report.parameters.tensors_value_changed == ["weight"]
    assert "parameter values" in report.interpretation


@pytest.mark.parametrize(("changes", "code"), [
    ({"op": "Mul"}, "OPERATOR_PROFILE_CHANGED"), ({"opset": 18}, "OPSET_CHANGED"),
])
def test_structural_operator_and_opset_changes(tmp_path, changes, code):
    report = compare(tmp_path, **changes)
    assert report.structure.state == ChangeState.CHANGED and code in report.structure.codes


def test_input_output_and_initializer_metadata_deltas(tmp_path):
    reference = make_static(tmp_path / "r.onnx")
    candidate = make_static(tmp_path / "c.onnx")
    model = onnx.load(candidate)
    model.graph.input[0].type.tensor_type.shape.dim[1].dim_value = 3
    model.graph.output[0].name = "changed-output"
    model.graph.node[0].output[0] = "changed-output"
    model.graph.initializer[0].dims[0] = 1; model.graph.initializer[0].dims[1] = 4
    candidate.write_bytes(model.SerializeToString())
    report = BaselineComparisonService().compare(reference, candidate)
    assert {"INPUT_CONTRACT_CHANGED", "OUTPUT_CONTRACT_CHANGED",
            "INITIALIZER_METADATA_CHANGED"} <= set(report.structure.codes)
    assert report.parameters.metadata_state == ChangeState.CHANGED


def test_initializer_addition_and_removal(tmp_path):
    added = compare(tmp_path, extra_initializer=True)
    assert added.parameters.tensors_added == ["unused"]
    reference = make_static(tmp_path / "r2.onnx", extra_initializer=True)
    candidate = make_static(tmp_path / "c2.onnx")
    removed = BaselineComparisonService().compare(reference, candidate)
    assert removed.parameters.tensors_removed == ["unused"]


def test_initializer_dtype_and_shape_changes(tmp_path):
    dtype = compare(tmp_path, dtype=TensorProto.DOUBLE)
    assert dtype.parameters.tensors_dtype_changed == ["weight"]
    shape = compare(tmp_path, shape=(1, 2))
    assert shape.parameters.tensors_shape_changed == ["weight"]


def test_per_tensor_commitment_deterministic_and_order_independent(tmp_path):
    path = make_static(tmp_path / "m.onnx", extra_initializer=True)
    model = onnx.load(path); first = tensor_value_commitments(model)
    model.graph.initializer.reverse(); second = tensor_value_commitments(model)
    assert first == second and all(set(item) == {"name", "dtype", "shape", "value_sha256"} for item in first)
    assert all(not isinstance(value, (bytes, bytearray, np.ndarray))
               for item in first for value in item.values())


def test_partial_and_unavailable_parameter_evidence_never_equal(tmp_path):
    reference = make_static(tmp_path / "r.onnx")
    external = make_static(tmp_path / "external.onnx")
    model = onnx.load(external); tensor = model.graph.initializer[0]
    tensor.ClearField("raw_data"); tensor.data_location = TensorProto.EXTERNAL
    entry = tensor.external_data.add(); entry.key = "location"; entry.value = "never.bin"
    external.write_bytes(model.SerializeToString())
    report = BaselineComparisonService().compare(reference, external)
    assert report.parameters.value_state == ChangeState.UNAVAILABLE
    assert report.status == ComparisonStatus.PARTIAL
    json.dumps(report.to_dict(), allow_nan=False)
    opaque1 = tmp_path / "a.pt"; opaque2 = tmp_path / "b.pt"
    opaque1.write_bytes(b"a"); opaque2.write_bytes(b"b")
    unavailable = BaselineComparisonService().compare(opaque1, opaque2)
    assert unavailable.parameters.value_state == ChangeState.UNAVAILABLE
    assert "identical" not in unavailable.interpretation.lower()
    json.dumps(unavailable.to_dict(), allow_nan=False)


def test_parameter_issue_new_persisting_resolved(tmp_path):
    normal = make_static(tmp_path / "normal.onnx", weight=1)
    anomalous = make_static(tmp_path / "anomaly.onnx", weight=0)
    new = BaselineComparisonService().compare(normal, anomalous).parameter_issue_deltas
    persisting = BaselineComparisonService().compare(anomalous, anomalous).parameter_issue_deltas
    resolved = BaselineComparisonService().compare(anomalous, normal).parameter_issue_deltas
    assert DeltaState.NEW in {item.state for item in new}
    assert DeltaState.PERSISTING in {item.state for item in persisting}
    assert DeltaState.RESOLVED in {item.state for item in resolved}


def test_baseline_self_digest_tamper_malformed_and_schema(tmp_path):
    path = make_static(tmp_path / "m.onnx"); baseline = create_baseline(path)
    assert verify_baseline(baseline.to_dict()).valid
    tampered = baseline.to_dict(); tampered["artifact_sha256"] = "0" * 64
    assert not verify_baseline(tampered).valid
    malformed = baseline.to_dict(); malformed.pop("structural_sha256")
    assert not verify_baseline(malformed).valid
    schema = baseline.to_dict(); schema["schema_version"] = "99"
    assert not verify_baseline(schema).valid


def test_baseline_digest_is_canonical_nonrecursive_and_path_independent(tmp_path):
    first_path = make_static(tmp_path / "one.onnx")
    second_path = tmp_path / "renamed.onnx"; second_path.write_bytes(first_path.read_bytes())
    first = create_baseline(first_path); second = create_baseline(second_path)
    assert first == second
    reordered = dict(reversed(list(first.to_dict().items())))
    assert verify_baseline(reordered).valid
    changed = first.to_dict(); changed["parameter_metadata_sha256"] = "0" * 64
    assert not verify_baseline(changed).valid
    assert first.baseline_payload_sha256 not in json.dumps({key: value for key, value in first.to_dict().items()
        if key not in {"baseline_payload_sha256", "baseline_id"}})


@pytest.mark.parametrize("version", [0, 2, 10**100, "1", True, None])
def test_baseline_schema_versions_fail_closed(tmp_path, version):
    value = create_baseline(make_static(tmp_path / "m.onnx")).to_dict()
    value["schema_version"] = version
    assert not verify_baseline(value).valid


@pytest.mark.parametrize(("field", "bad"), [
    ("reference_artifact_id", None), ("artifact_sha256", {}),
    ("structural_sha256", []), ("parameter_value_status", True),
    ("baseline_payload_sha256", 1), ("baseline_id", False),
])
def test_baseline_type_confusion_is_bounded(tmp_path, field, bad):
    value = create_baseline(make_static(tmp_path / "m.onnx")).to_dict(); value[field] = bad
    result = verify_baseline(value)
    assert not result.valid and result.errors


@pytest.mark.parametrize("digest", ["A"*64, "a"*63, "a"*65, "g"*64, "sha256:"+"a"*64])
def test_baseline_hash_format_is_strict_lowercase(tmp_path, digest):
    value = create_baseline(make_static(tmp_path / "m.onnx")).to_dict(); value["artifact_sha256"] = digest
    assert not verify_baseline(value).valid


def test_baseline_file_duplicate_oversized_and_strict_json(tmp_path):
    duplicate = tmp_path / "duplicate.json"; duplicate.write_text('{"schema_version":"1","schema_version":"1"}')
    assert not load_baseline(duplicate).valid
    nested = tmp_path / "nested-duplicate.json"; nested.write_text('{"x":{"a":1,"a":2}}')
    assert not load_baseline(nested).valid
    deep = tmp_path / "deep.json"; deep.write_text("[" * 2000 + "0" + "]" * 2000)
    assert not load_baseline(deep).valid
    oversized = tmp_path / "large.json"; oversized.write_bytes(b" " * 1025)
    assert not load_baseline(oversized, limits=BaselineComparisonLimits(max_baseline_file_bytes=1024)).valid
    baseline = create_baseline(make_static(tmp_path / "m.onnx"))
    json.dumps(baseline.to_dict(), allow_nan=False)


def test_baseline_path_rejects_directory_and_symlink(tmp_path):
    directory = tmp_path / "directory"; directory.mkdir()
    assert not load_baseline(directory).valid
    target = tmp_path / "baseline.json"
    target.write_text(json.dumps(create_baseline(make_static(tmp_path / "m.onnx")).to_dict()))
    link = tmp_path / "link.json"; link.symlink_to(target)
    assert not load_baseline(link).valid


def test_reference_designated_and_optional_verified_evidence(tmp_path):
    path = make_static(tmp_path / "m.onnx")
    service = BaselineComparisonService()
    assert service.compare(path, path).reference_status == ReferenceStatus.DESIGNATED_ONLY
    digest = ModelIntegrityService().inspect(path).artifact.sha256
    verified = service.compare(path, path, expected_reference_sha256=digest,
                               reference_registry_state="APPROVED")
    assert verified.reference_status == ReferenceStatus.VERIFIED_REFERENCE
    assert verified.expected_reference_sha256 == digest
    assert verified.reference_registry_state == "APPROVED"
    mismatch = service.compare(path, path, expected_reference_sha256="0"*64)
    assert mismatch.status == ComparisonStatus.PARTIAL
    assert "EXPECTED_REFERENCE_SHA256_MISMATCH" in mismatch.limitations
    malformed = service.compare(path, path, expected_reference_sha256="bad")
    assert malformed.status == ComparisonStatus.PARTIAL
    assert "EXPECTED_REFERENCE_SHA256_INVALID" in malformed.limitations
    assert malformed.reference_status == ReferenceStatus.DESIGNATED_ONLY
    assert verified.comparison_id != mismatch.comparison_id != malformed.comparison_id


def test_same_opaque_artifact_does_not_manufacture_structure_or_parameter_equality(tmp_path):
    path = tmp_path / "opaque.pt"; path.write_bytes(b"same opaque artifact")
    report = BaselineComparisonService().compare(path, path)
    assert report.artifact.state == ChangeState.SAME
    assert report.structure.state == ChangeState.UNAVAILABLE
    assert report.parameters.metadata_state == ChangeState.UNAVAILABLE
    assert report.parameters.value_state == ChangeState.UNAVAILABLE


def test_collection_limits_make_partial(tmp_path):
    reference = make_static(tmp_path / "r.onnx")
    candidate = make_static(tmp_path / "c.onnx", extra_initializer=True)
    model = onnx.load(candidate)
    for index in range(4):
        model.graph.initializer.append(numpy_helper.from_array(np.asarray([index], np.float32), f"x{index}"))
    candidate.write_bytes(model.SerializeToString())
    report = BaselineComparisonService(limits=BaselineComparisonLimits(max_changed_tensors=1,
        max_initializer_deltas=1)).compare(reference, candidate)
    assert report.status == ComparisonStatus.PARTIAL
    assert any("TRUNCATED" in item for item in report.limitations)
    assert len(report.parameters.tensors_added) == 1
    json.dumps(report.to_dict(), allow_nan=False)


def test_huge_tensor_names_are_deterministically_bounded_and_partial(tmp_path):
    reference = make_static(tmp_path / "r.onnx")
    candidate = make_static(tmp_path / "c.onnx", extra_initializer=True)
    model = onnx.load(candidate); model.graph.initializer[-1].name = "z" * 10_000
    candidate.write_bytes(model.SerializeToString())
    first = BaselineComparisonService().compare(reference, candidate)
    second = BaselineComparisonService().compare(reference, candidate)
    assert first.status == ComparisonStatus.PARTIAL and first == second
    assert "COMPARISON_STRINGS_TRUNCATED" in first.limitations
    assert all(len(name) <= 512 for name in first.parameters.tensors_added)


def test_corpus_and_protocol_fingerprints_are_order_and_contract_sensitive():
    a = np.zeros((1,1,2,2), np.float32); b = np.ones_like(a)
    assert corpus_fingerprint([a, b]) == corpus_fingerprint([a.copy(), b.copy()])
    assert corpus_fingerprint([a, b]) != corpus_fingerprint([b, a])
    contract = InputContract("input", "float32", [1,1,2,2], InputLayout.NCHW, 1, 0, 1)
    output = OutputContract("scores", ClassificationKind.LOGITS)
    triggers = default_triggers(contract); limits = BehavioralLimits()
    base = behavior_protocol([a, b], contract, output, triggers, limits, "runtime-a")
    variants = [behavior_protocol([b, a], contract, output, triggers, limits, "runtime-a"),
        behavior_protocol([a,b], replace(contract, value_max=2), output, triggers, limits, "runtime-a"),
        behavior_protocol([a,b], contract, replace(output, class_axis=0), triggers, limits, "runtime-a"),
        behavior_protocol([a,b], contract, output, triggers[:-1], limits, "runtime-a"),
        behavior_protocol([a,b], contract, output, triggers, limits, "runtime-b")]
    assert all(item.protocol_id != base.protocol_id for item in variants)
    assert corpus_fingerprint([a]) != corpus_fingerprint([a.astype(np.float64)])
    assert corpus_fingerprint([a]) != corpus_fingerprint([a.reshape(1, 2, 2, 1)])
    changed = a.copy(); changed.flat[0] = 1
    assert corpus_fingerprint([a]) != corpus_fingerprint([changed])
    assert behavior_protocol([a,b], contract, output, triggers, replace(limits, repeatability_runs=3),
                             "runtime-a").protocol_id != base.protocol_id
    assert behavior_protocol([a,b], contract, output, triggers,
        replace(limits, flip_rate_threshold=.8), "runtime-a").protocol_id != base.protocol_id


def _behavior_reports(tmp_path):
    contract = InputContract("input", "float32", [1,1,8,8], InputLayout.NCHW, 1, 0, 1)
    output = OutputContract("scores", ClassificationKind.LOGITS)
    corpus = [np.full((1,1,8,8), .2 + i*.01, np.float32) for i in range(8)]
    triggers = [item for item in default_triggers(contract) if item.kind == TriggerKind.SOLID_PATCH]
    reference = make_classifier(tmp_path / "reference.onnx")
    candidate = make_classifier(tmp_path / "candidate.onnx", trigger_sensitive=True)
    service = BehavioralIntegrityService()
    protocol = behavior_protocol(corpus, contract, output, triggers, service.limits, service.runtime.name)
    return (service.analyze(reference, corpus, contract, output, triggers),
            service.analyze(candidate, corpus, contract, output, triggers), protocol,
            reference, candidate, corpus, contract, output, triggers)


def test_explicit_behavioral_product_demo_and_issue_deltas(tmp_path):
    reference, candidate, protocol, *_ = _behavior_reports(tmp_path)
    comparison = compare_behavior_reports(reference, candidate, protocol, protocol)
    assert comparison.status == ComparisonStatus.COMPLETE
    assert "NEW_TRIGGER_SENSITIVITY" in comparison.codes
    assert any(item.state == DeltaState.NEW and item.code == "TRIGGER_SENSITIVITY"
               for item in comparison.issue_deltas)
    bottom = next(item for item in comparison.trigger_deltas if "bottom_right" in item.trigger_id)
    assert bottom.flip_rate_delta == 1 and bottom.control_relative_delta == 1
    persistent = compare_behavior_reports(candidate, candidate, protocol, protocol)
    assert "NEW_TRIGGER_SENSITIVITY" not in persistent.codes
    assert "PERSISTING_TRIGGER_SENSITIVITY" in persistent.codes
    resolved = compare_behavior_reports(candidate, reference, protocol, protocol)
    assert "RESOLVED_TRIGGER_SENSITIVITY" in resolved.codes
    json.dumps(asdict(comparison), allow_nan=False)


def test_behavior_protocol_mismatch_is_incomparable(tmp_path):
    reference, candidate, protocol, *_ = _behavior_reports(tmp_path)
    mismatch = replace(protocol, protocol_id="behavior-protocol:sha256:" + "0"*64)
    result = compare_behavior_reports(reference, candidate, protocol, mismatch)
    assert result.status == ComparisonStatus.INCOMPARABLE
    assert result.state == ChangeState.INCOMPARABLE
    json.dumps(asdict(result), allow_nan=False)


@pytest.mark.parametrize(("field", "code"), [
    ("corpus_sha256", "BEHAVIOR_CORPUS_MISMATCH"),
    ("input_contract_sha256", "BEHAVIOR_INPUT_CONTRACT_MISMATCH"),
    ("output_contract_sha256", "BEHAVIOR_OUTPUT_CONTRACT_MISMATCH"),
    ("trigger_suite_sha256", "BEHAVIOR_TRIGGER_SUITE_MISMATCH"),
    ("limits_sha256", "BEHAVIOR_LIMITS_MISMATCH"),
    ("runtime_identity", "BEHAVIOR_RUNTIME_MISMATCH"),
])
def test_each_protocol_dimension_mismatch_is_incomparable(field, code):
    coverage = BehavioralCoverage(1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 1.0, 0.0)
    report = BehavioralAnalysisReport(BehavioralAnalysisStatus.COMPLETE, "runtime", coverage,
                                      True, [], [], [], [])
    sample = np.zeros((1,1,2,2), np.float32)
    contract = InputContract("input", "float32", [1,1,2,2], InputLayout.NCHW, 1, 0, 1)
    protocol = behavior_protocol([sample], contract, OutputContract("out"), [],
                                 BehavioralLimits(), "runtime")
    changed = replace(protocol, **{field: getattr(protocol, field) + "-changed",
        "protocol_id": "behavior-protocol:sha256:" + "0"*64})
    result = compare_behavior_reports(report, report, protocol, changed)
    assert result.status == ComparisonStatus.INCOMPARABLE and code in result.limitations
    forged_same_id = replace(protocol, **{field: getattr(protocol, field) + "-forged"})
    forged = compare_behavior_reports(report, report, protocol, forged_same_id)
    assert forged.status == ComparisonStatus.INCOMPARABLE and code in forged.limitations


def test_explicit_compare_behavior_invokes_m3_twice(tmp_path):
    reference = make_classifier(tmp_path / "r.onnx"); candidate = make_classifier(tmp_path / "c.onnx")
    contract = InputContract("input", "float32", [1,1,8,8], InputLayout.NCHW, 1, 0, 1)
    output = OutputContract("scores", ClassificationKind.LOGITS)
    sample = [np.zeros((1,1,8,8), np.float32)]
    runtime = FakeRuntime(lambda count, value: (RuntimeStatus.SUCCESS, good_output(value), None))
    triggers = [item for item in default_triggers(contract) if item.kind == TriggerKind.SOLID_PATCH][:1]
    result = compare_behavior(reference, candidate, sample, contract, output, triggers,
                              behavioral_service=BehavioralIntegrityService(runtime=runtime))
    assert runtime.calls == 6 and result.status == ComparisonStatus.COMPLETE


def test_zero_or_partial_behavioral_coverage_not_equal(tmp_path):
    reference, candidate, protocol, *_ = _behavior_reports(tmp_path)
    unavailable = replace(reference, status=BehavioralAnalysisStatus.UNAVAILABLE,
        coverage=replace(reference.coverage, successful_clean_samples=0))
    result = compare_behavior_reports(unavailable, candidate, protocol, protocol)
    assert result.status == ComparisonStatus.UNAVAILABLE and result.state == ChangeState.UNAVAILABLE
    partial = replace(reference, status=BehavioralAnalysisStatus.PARTIAL)
    assert compare_behavior_reports(partial, candidate, protocol, protocol).status == ComparisonStatus.PARTIAL


def test_no_raw_values_and_all_reports_strict_json(tmp_path):
    report = compare(tmp_path, weight=2)
    encoded = json.dumps(report.to_dict(), allow_nan=False)
    assert "raw_data" not in encoded and "sample" not in encoded and "tensor_values" not in encoded


@pytest.mark.parametrize("reference_status", list(AnalysisStatus))
@pytest.mark.parametrize("candidate_status", list(AnalysisStatus))
def test_parameter_completeness_truth_table(tmp_path, monkeypatch, reference_status, candidate_status):
    path = make_static(tmp_path / "m.onnx")
    manifest = ModelIntegrityService().inspect(path)
    def with_status(status, digest="1"*64):
        value = digest if status != AnalysisStatus.UNAVAILABLE else None
        return replace(manifest, fingerprints=replace(manifest.fingerprints,
            parameter_value_status=status, parameter_value_sha256=value))
    monkeypatch.setattr("modules.model_integrity.baseline._tensor_commitments", lambda *args: [])
    result = _parameters(path, path, with_status(reference_status), with_status(candidate_status),
                         BaselineComparisonLimits(), [])
    if reference_status == candidate_status == AnalysisStatus.COMPLETE:
        assert result.value_state == ChangeState.SAME
    else:
        assert result.value_state == ChangeState.UNAVAILABLE
    assert _state(None, None) == ChangeState.UNAVAILABLE


def test_duplicate_initializer_names_are_incomparable_not_last_wins(tmp_path):
    path = make_static(tmp_path / "duplicate.onnx")
    model = onnx.load(path)
    model.graph.initializer.append(numpy_helper.from_array(np.full((2,2), 9, np.float32), "weight"))
    path.write_bytes(model.SerializeToString())
    report = BaselineComparisonService().compare(path, path)
    assert report.status == ComparisonStatus.PARTIAL
    assert report.structure.state == ChangeState.INCOMPARABLE
    assert report.parameters.value_state == ChangeState.INCOMPARABLE
    assert report.parameters.tensors_value_changed == []
    assert "DUPLICATE_TENSOR_VALUE_IDENTITIES_INCOMPARABLE" in report.limitations


@pytest.mark.parametrize(("onnx_dtype", "values"), [
    (TensorProto.FLOAT, [1.0, -2.0]),
    (TensorProto.INT64, [-(2**63), 2**63-1]),
    (TensorProto.UINT64, [0, 2**64-1]),
    (TensorProto.BOOL, [False, True]),
])
def test_m4_tensor_commitments_reuse_m2_typed_raw_canonicalization(onnx_dtype, values):
    typed = helper.make_tensor("w", onnx_dtype, [2], values, raw=False)
    np_dtype = {TensorProto.FLOAT: np.float32, TensorProto.INT64: np.int64,
                TensorProto.UINT64: np.uint64, TensorProto.BOOL: np.bool_}[onnx_dtype]
    raw = numpy_helper.from_array(np.asarray(values, dtype=np_dtype), "w")
    graph = lambda tensor: helper.make_model(helper.make_graph([], "g", [], [], [tensor]))
    assert tensor_value_commitments(graph(typed)) == tensor_value_commitments(graph(raw))


def test_m4_commitments_preserve_signed_zero_and_nan_payloads():
    arrays = [np.asarray([0.0], np.float32), np.asarray([-0.0], np.float32),
        np.asarray([0x7fc00001], np.uint32).view(np.float32),
        np.asarray([0x7fc00002], np.uint32).view(np.float32)]
    digests = []
    for array in arrays:
        tensor = numpy_helper.from_array(array, "w")
        model = helper.make_model(helper.make_graph([], "g", [], [], [tensor]))
        digests.append(tensor_value_commitments(model)[0]["value_sha256"])
    assert len(set(digests)) == 4


def test_parameter_issue_identity_ignores_issue_order_and_wording(tmp_path):
    normal = ModelIntegrityService().inspect(make_static(tmp_path / "normal.onnx", weight=1))
    anomalous = ModelIntegrityService().inspect(make_static(tmp_path / "anomaly.onnx", weight=0))
    original = _issue_deltas(normal, anomalous, BaselineComparisonLimits(), [])
    reversed_report = replace(anomalous.parameter_analysis,
                              issues=list(reversed(anomalous.parameter_analysis.issues)))
    reordered = replace(anomalous, parameter_analysis=reversed_report)
    assert original == _issue_deltas(normal, reordered, BaselineComparisonLimits(), [])


def test_behavior_numeric_nonfinite_and_support_mismatch_are_unavailable(tmp_path):
    reference, candidate, protocol, *_ = _behavior_reports(tmp_path)
    rtrigger = reference.triggers[0]; ctrigger = candidate.triggers[0]
    hostile = replace(candidate, triggers=[replace(ctrigger, prediction_flip_rate=float("nan"))]
                      + candidate.triggers[1:])
    result = compare_behavior_reports(reference, hostile, protocol, protocol)
    assert next(item for item in result.trigger_deltas
                if item.trigger_id == ctrigger.trigger.trigger_id).flip_rate_delta is None
    json.dumps(asdict(result), allow_nan=False)
    mismatched = replace(candidate, status=BehavioralAnalysisStatus.PARTIAL,
        triggers=[replace(ctrigger, samples_successful=max(1, ctrigger.samples_successful-1))]
                 + candidate.triggers[1:])
    partial = compare_behavior_reports(reference, mismatched, protocol, protocol)
    assert partial.status == ComparisonStatus.PARTIAL
    assert partial.state == ChangeState.UNAVAILABLE
    assert next(item for item in partial.trigger_deltas
                if item.trigger_id == ctrigger.trigger.trigger_id).flip_rate_delta is None


def test_zero_trigger_coverage_is_unavailable_not_same():
    coverage = BehavioralCoverage(1, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1.0, 0.0)
    report = BehavioralAnalysisReport(BehavioralAnalysisStatus.PARTIAL, "runtime", coverage,
                                      True, [], [], [], [])
    sample = np.zeros((1,1,2,2), np.float32)
    contract = InputContract("input", "float32", [1,1,2,2], InputLayout.NCHW, 1, 0, 1)
    protocol = behavior_protocol([sample], contract, OutputContract("out"), [],
                                 BehavioralLimits(), "runtime")
    result = compare_behavior_reports(report, report, protocol, protocol)
    assert result.status == ComparisonStatus.UNAVAILABLE and result.state == ChangeState.UNAVAILABLE


def test_cli_failure_exit_semantics_and_help_consent(tmp_path, monkeypatch, capsys):
    from modules.model_integrity import cli
    baseline = create_baseline(make_static(tmp_path / "m.onnx")).to_dict()
    baseline["artifact_sha256"] = "0"*64
    path = tmp_path / "tampered.json"; path.write_text(json.dumps(baseline))
    monkeypatch.setattr("sys.argv", ["model-integrity", "baseline-verify", str(path)])
    assert cli.main() == 2
    opaque = tmp_path / "opaque.pt"; opaque.write_bytes(b"opaque")
    monkeypatch.setattr("sys.argv", ["model-integrity", "compare", str(opaque), str(opaque), "--json"])
    assert cli.main() == 3
    monkeypatch.setattr("sys.argv", ["model-integrity", "compare-behavioral", str(opaque), str(opaque),
        "--input-npy", str(tmp_path / "missing.npy"), "--layout", "NCHW",
        "--value-min", "0", "--value-max", "1", "--output", "out"])
    assert cli.main() == 2
    output = capsys.readouterr().out
    assert "Traceback" not in output


def test_cli_help_distinguishes_static_and_executing_compare(monkeypatch, capsys):
    from modules.model_integrity import cli
    monkeypatch.setattr("sys.argv", ["model-integrity", "--help"])
    with pytest.raises(SystemExit) as exc: cli.main()
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "non-executing static" in output
    assert "explicitly executes both" in output


def test_frozen_fingerprints_and_contracts_unchanged(tmp_path):
    path = make_static(tmp_path / "m.onnx")
    before = ModelIntegrityService().inspect(path)
    BaselineComparisonService().compare(path, path)
    after = ModelIntegrityService().inspect(path)
    assert before.fingerprints == after.fingerprints
    assert list(Finding.model_fields) == ["finding_id", "module", "asset_type", "asset_id",
        "category", "severity", "confidence", "reason", "evidence", "recommendation", "limitations"]


def test_comparison_does_not_reparse_artifact_only_onnx(tmp_path, monkeypatch):
    from modules.model_integrity.service import ModelIntegrityService
    from modules.model_integrity.baseline import BaselineComparisonService
    path = tmp_path / 'bounded.onnx'
    path.write_bytes(b'oversized opaque bytes')
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('parse ceiling bypassed')
    monkeypatch.setattr(onnx, 'load_model', forbidden)
    result = BaselineComparisonService(inspection_service=ModelIntegrityService(max_onnx_parse_bytes=1)).compare(path, path)
    assert calls == []
    assert result.structure.state.value == 'UNAVAILABLE'
    assert result.parameters.value_state.value == 'UNAVAILABLE'
    assert result.parameters.tensors_added == result.parameters.tensors_removed == []


def test_missing_comparison_details_do_not_invent_removed_tensors(tmp_path, monkeypatch):
    from modules.model_integrity import baseline
    path = make_static(tmp_path / 'model.onnx')
    original = baseline._tensor_commitments
    calls = 0
    def unavailable_once(*args):
        nonlocal calls
        calls += 1
        return None if calls == 1 else original(*args)
    monkeypatch.setattr(baseline, '_tensor_commitments', unavailable_once)
    result = BaselineComparisonService().compare(path, path)
    assert result.parameters.tensors_added == result.parameters.tensors_removed == []
    assert result.status == ComparisonStatus.PARTIAL
    assert 'TENSOR_VALUE_DETAILS_INCOMPARABLE' in result.limitations
