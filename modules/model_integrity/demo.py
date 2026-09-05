"""M6: deterministic product orchestration over the frozen M1--M5 APIs.

Synthetic artifacts are caller-owned temporary files.  Nothing in this module
persists a model, baseline, report, key, or backend object.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from onnx import TensorProto, helper, numpy_helper

from backend.core.canonical import canonical_json_bytes
from backend.schemas.evidence import Finding
from drishti_sdk.adapters import ModelIntegrityAdapter
from drishti_sdk.client import DrishtiClient
from .baseline import BaselineComparisonService, compare_behavior
from .baseline_models import BaselineComparisonReport, ChangeState
from .behavioral import default_triggers
from .behavioral_models import ClassificationKind, InputContract, InputLayout, OutputContract, TriggerKind
from .findings import FindingMappingContext, ModelIntegrityEvidenceBundle, ModelIntegrityFindingMapper
from .integration import ModelIntegrityRunBuilder
from .service import ModelIntegrityService


class ScenarioKind(StrEnum):
    CLEAN = "clean"
    WEIGHT_CHANGE = "weight-change"
    STRUCTURE_CHANGE = "structure-change"
    PARAMETER_NAN = "parameter-nan"
    TRIGGER_SENSITIVE = "trigger-sensitive"


@dataclass(frozen=True)
class ModelIntegrityScenario:
    scenario_id: str
    name: str
    kind: ScenarioKind
    behavioral: bool = False


@dataclass(frozen=True)
class ModelIntegrityScenarioResult:
    scenario_id: str
    scenario_name: str
    reference_artifact_id: str
    candidate_artifact_id: str
    reference_status: str
    comparison_id: str
    artifact_state: str
    structural_state: str
    parameter_metadata_state: str
    parameter_value_state: str
    changed_tensors: tuple[str, ...]
    new_parameter_issue_count: int
    behavioral_state: str
    behavioral_protocol_status: str
    finding_ids: tuple[str, ...]
    finding_categories: tuple[str, ...]
    recommendations: tuple[str, ...]
    backend_submission_status: str
    backend_module_status: str
    backend_system_status: str
    interpretation: str
    limitations: tuple[str, ...]

    @property
    def finding_count(self) -> int: return len(self.finding_ids)
    def to_dict(self) -> dict[str, Any]:
        value = asdict(self); value["finding_count"] = self.finding_count
        return value


@dataclass(frozen=True)
class ModelIntegrityDemoReport:
    report_id: str
    schema_version: int
    results: tuple[ModelIntegrityScenarioResult, ...]
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"report_id": self.report_id, "schema_version": self.schema_version,
                "results": [item.to_dict() for item in self.results],
                "limitations": list(self.limitations)}

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), allow_nan=False, indent=indent, sort_keys=True)

    def to_text(self) -> str:
        lines = ["DRISHTI-TRUST — MODEL INTEGRITY FINAL DEMO", "",
            "Scenario | Artifact | Structure | Params | Behavior | Findings | Backend",
            "---|---|---|---|---|---:|---"]
        for item in self.results:
            lines.append(f"{item.scenario_name} | {item.artifact_state} | {item.structural_state} | "
                f"{item.parameter_value_state} | {item.behavioral_state} | {item.finding_count} | "
                f"{item.backend_submission_status}")
        for item in self.results:
            lines += ["", f"Scenario: {item.scenario_name}",
                f"Reference: {item.reference_artifact_id}", f"Candidate: {item.candidate_artifact_id}",
                f"Exact static changes: {', '.join(item.changed_tensors) or 'none'}",
                f"Behavior: {item.behavioral_state} ({item.behavioral_protocol_status})",
                f"Mapped Findings: {', '.join(item.finding_categories) or 'none'}",
                f"Recommendation: {', '.join(item.recommendations) or 'none'}",
                f"Backend: {item.backend_submission_status}", f"Interpretation: {item.interpretation}",
                f"Limitations: {'; '.join(item.limitations) or 'none'}"]
        return "\n".join(lines)


SCENARIOS = {
    ScenarioKind.CLEAN: ModelIntegrityScenario("M6-A", "Clean identical candidate", ScenarioKind.CLEAN),
    ScenarioKind.WEIGHT_CHANGE: ModelIntegrityScenario("M6-B", "Weight-modified candidate", ScenarioKind.WEIGHT_CHANGE),
    ScenarioKind.STRUCTURE_CHANGE: ModelIntegrityScenario("M6-C", "Structure-modified candidate", ScenarioKind.STRUCTURE_CHANGE),
    ScenarioKind.PARAMETER_NAN: ModelIntegrityScenario("M6-D", "Non-finite parameter candidate", ScenarioKind.PARAMETER_NAN),
    ScenarioKind.TRIGGER_SENSITIVE: ModelIntegrityScenario("M6-E", "Trigger-sensitive candidate", ScenarioKind.TRIGGER_SENSITIVE, True),
}


def create_static_specimen(path: Path, kind: ScenarioKind) -> Path:
    """Create a tiny deterministic ONNX specimen under caller-provided storage."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == ScenarioKind.TRIGGER_SENSITIVE: return create_behavioral_specimen(path, True)
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 2])
    y = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 2])
    values = [1.0, 2.0, 3.0, 4.0]
    if kind == ScenarioKind.WEIGHT_CHANGE: values[0] = 1.5
    if kind == ScenarioKind.PARAMETER_NAN: values[0] = float("nan")
    weight = helper.make_tensor("weight", TensorProto.FLOAT, [2, 2], values)
    nodes = [helper.make_node("MatMul", ["input", "weight"], ["output"], name="projection")]
    if kind == ScenarioKind.STRUCTURE_CHANGE:
        nodes = [helper.make_node("Add", ["input", "bias"], ["output"], name="offset")]
        weight = helper.make_tensor("bias", TensorProto.FLOAT, [2], [1.0, 2.0])
    graph = helper.make_graph(nodes, "m6-static", [x], [y], initializer=[weight])
    model = helper.make_model(graph, producer_name="drishti-m6", opset_imports=[helper.make_opsetid("", 18)])
    path.write_bytes(model.SerializeToString()); return path


def create_behavioral_specimen(path: Path, trigger_sensitive: bool) -> Path:
    """The controlled M3 classifier pattern, retained as demo specimen support."""
    path.parent.mkdir(parents=True, exist_ok=True)
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 1, 8, 8])
    y = helper.make_tensor_value_info("scores", TensorProto.FLOAT, [1, 2])
    mean = helper.make_node("ReduceMean", ["input"], ["global"], axes=[2, 3], keepdims=0)
    initializers = []
    if not trigger_sensitive:
        nodes = [mean, helper.make_node("Neg", ["global"], ["negative"]),
                 helper.make_node("Concat", ["negative", "global"], ["scores"], axis=1)]
    else:
        arrays = [("starts", [0, 0, 7, 7]), ("ends", [1, 1, 8, 8]),
                  ("axes", [0, 1, 2, 3]), ("steps", [1, 1, 1, 1])]
        initializers = [numpy_helper.from_array(np.asarray(value, dtype=np.int64), name)
                        for name, value in arrays]
        initializers += [numpy_helper.from_array(np.asarray([10], np.float32), "scale"),
                         numpy_helper.from_array(np.asarray([-5], np.float32), "offset")]
        nodes = [mean, helper.make_node("Slice", ["input", "starts", "ends", "axes", "steps"], ["patch"]),
            helper.make_node("ReduceMean", ["patch"], ["patch_mean"], axes=[2, 3], keepdims=0),
            helper.make_node("Mul", ["patch_mean", "scale"], ["scaled"]),
            helper.make_node("Add", ["scaled", "offset"], ["target"]),
            helper.make_node("Concat", ["global", "target"], ["scores"], axis=1)]
    graph = helper.make_graph(nodes, "m6-classifier", [x], [y], initializer=initializers)
    model = helper.make_model(graph, producer_name="drishti-m6", opset_imports=[helper.make_opsetid("", 17)])
    path.write_bytes(model.SerializeToString()); return path


class ModelIntegrityFinalOrchestrator:
    """Thin M1--M5 coordinator. Static calls cannot execute inference."""
    def __init__(self, *, inspection_service: ModelIntegrityService | None = None,
                 comparison_service: BaselineComparisonService | None = None) -> None:
        self.inspection_service = inspection_service or ModelIntegrityService()
        self.comparison_service = comparison_service or BaselineComparisonService(
            inspection_service=self.inspection_service)

    @staticmethod
    def _mapper() -> ModelIntegrityFindingMapper:
        # DrishtiClient supplies frozen Finding construction only; it performs no I/O here.
        return ModelIntegrityFindingMapper(ModelIntegrityAdapter(DrishtiClient()))

    def run_static_scenario(self, scenario: ModelIntegrityScenario, reference_path: Path,
                            candidate_path: Path, *, caller_candidate_artifact_id: str | None = None,
                            expected_reference_sha256: str | None = None) -> ModelIntegrityScenarioResult:
        if scenario.behavioral: raise ValueError("behavioral scenarios require explicit run_behavioral_scenario")
        reference = self.inspection_service.inspect(reference_path)
        candidate = self.inspection_service.inspect(candidate_path)
        comparison = self.comparison_service.compare(reference_path, candidate_path,
            expected_reference_sha256=expected_reference_sha256)
        if comparison.reference_artifact_id != reference.artifact.artifact_id:
            raise ValueError("reference artifact identities disagree")
        expected_candidate = caller_candidate_artifact_id or candidate.artifact.artifact_id
        findings = self._mapper().map(ModelIntegrityEvidenceBundle(manifest=candidate,
            comparison=comparison), FindingMappingContext(expected_candidate))
        return self._result(scenario, comparison, findings)

    def run_behavioral_scenario(self, scenario: ModelIntegrityScenario, reference_path: Path,
                                candidate_path: Path, *, caller_candidate_artifact_id: str | None = None
                                ) -> ModelIntegrityScenarioResult:
        if not scenario.behavioral: raise ValueError("use run_static_scenario for non-executing scenarios")
        reference = self.inspection_service.inspect(reference_path)
        candidate = self.inspection_service.inspect(candidate_path)
        comparison = self.comparison_service.compare(reference_path, candidate_path)
        contract = InputContract("input", "float32", [1, 1, 8, 8], InputLayout.NCHW, 1, 0.0, 1.0)
        output = OutputContract("scores", ClassificationKind.LOGITS, -1)
        samples = [np.full((1, 1, 8, 8), .2 + index * .01, np.float32) for index in range(8)]
        triggers = [item for item in default_triggers(contract) if item.kind == TriggerKind.SOLID_PATCH]
        behavior = compare_behavior(reference_path, candidate_path, samples, contract, output, triggers)
        comparison = replace(comparison, behavior=behavior)
        expected_candidate = caller_candidate_artifact_id or candidate.artifact.artifact_id
        findings = self._mapper().map(ModelIntegrityEvidenceBundle(manifest=candidate,
            comparison=comparison, behavioral_comparison=behavior), FindingMappingContext(expected_candidate))
        return self._result(scenario, comparison, findings)

    @staticmethod
    def _result(scenario: ModelIntegrityScenario, comparison: BaselineComparisonReport,
                findings: list[Finding]) -> ModelIntegrityScenarioResult:
        behavior = comparison.behavior
        limitations = list(comparison.limitations)
        if not findings:
            limitations += ["No mapped anomaly Findings were produced.",
                "Authenticated zero-Finding completion remains UNKNOWN and does not establish safety."]
        else: limitations.append("A model difference does not establish malicious intent.")
        return ModelIntegrityScenarioResult(scenario.scenario_id, scenario.name,
            comparison.reference_artifact_id, comparison.candidate_artifact_id,
            comparison.reference_status.value, comparison.comparison_id, comparison.artifact.state.value,
            comparison.structure.state.value, comparison.parameters.metadata_state.value,
            comparison.parameters.value_state.value, tuple(comparison.parameters.tensors_value_changed),
            sum(item.state.value == "NEW" for item in comparison.parameter_issue_deltas),
            behavior.state.value if behavior else ChangeState.NOT_ASSESSED.value,
            behavior.status.value if behavior else "NOT_ASSESSED",
            tuple(item.finding_id for item in findings), tuple(item.category for item in findings),
            tuple(sorted({item.recommendation.value for item in findings})),
            "OFFLINE_NOT_SUBMITTED",
            "UNKNOWN", "UNKNOWN", comparison.interpretation, tuple(sorted(set(limitations))))

    @staticmethod
    def report(results: Iterable[ModelIntegrityScenarioResult]) -> ModelIntegrityDemoReport:
        ordered = tuple(sorted(results, key=lambda item: item.scenario_id))
        core = {"schema_version": 1, "results": [item.to_dict() for item in ordered]}
        digest = sha256(canonical_json_bytes(core)).hexdigest()
        report = ModelIntegrityDemoReport(f"model-demo:sha256:{digest}", 1, ordered,
            ("Model Integrity evidence alone does not establish complete whole-system coverage.",))
        json.dumps(report.to_dict(), allow_nan=False)
        return report

    @staticmethod
    def submit_signed_result(result: ModelIntegrityScenarioResult, findings: list[Finding], *,
                             builder: ModelIntegrityRunBuilder, assessment_id: str,
                             producer: str, key_id: str, private_key: Any
                             ) -> ModelIntegrityScenarioResult:
        """Use the frozen M5/SDK signing pipeline; clean results are never fabricated."""
        if any(item.asset_id != result.candidate_artifact_id for item in findings):
            raise ValueError("candidate artifact identity must match every mapped Finding")
        run = builder.build_run(assessment_id=assessment_id, producer=producer,
            producer_version="M6", findings=findings)
        submitted = builder.submit_signed_run(run=run, key_id=key_id,
            candidate_artifact_id=result.candidate_artifact_id, private_key=private_key)
        persisted = builder.client.get_run(submitted.signed_run_id)
        if persisted.get("run_id") != submitted.signed_run_id:
            raise ValueError("persisted signed run identity mismatch")
        summary = builder.client.get_summary(assessment_id)
        modules = summary.get("modules", {})
        module = modules.get("model_integrity", {}) if isinstance(modules, dict) else next(
            (item for item in modules if item.get("module") == "model_integrity"), {})
        return replace(result, backend_submission_status=submitted.backend_result,
            backend_module_status=str(module.get("band", module.get("status", "UNKNOWN"))),
            backend_system_status=str(summary.get("system_band", summary.get("status", "UNKNOWN"))))
