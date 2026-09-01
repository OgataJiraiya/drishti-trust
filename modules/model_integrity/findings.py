"""Conservative offline mapping of M1-M4 observations into frozen Finding v1."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import re
from typing import Any

from backend.schemas.evidence import Finding
from drishti_sdk.adapters import ModelIntegrityAdapter
from .baseline_models import (
    BaselineComparisonReport, BehavioralComparison, ChangeState, ComparisonStatus,
    DeltaState, ReferenceStatus,
)
from .behavioral_models import BehavioralAnalysisReport
from .models import ModelManifest

MODEL_INTEGRITY_MAPPING_POLICY_V1 = "MODEL_INTEGRITY_MAPPING_POLICY_V1"
_MODEL_ID = re.compile(r"^model:sha256:[0-9a-f]{64}$")
_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


@dataclass(frozen=True)
class ModelIntegrityEvidenceBundle:
    manifest: ModelManifest | None = None
    behavioral: BehavioralAnalysisReport | None = None
    comparison: BaselineComparisonReport | None = None
    behavioral_comparison: BehavioralComparison | None = None


@dataclass(frozen=True)
class FindingMappingContext:
    candidate_artifact_id: str | None = None


@dataclass(frozen=True)
class FindingMappingPolicy:
    max_findings: int = 100
    max_evidence: int = 20
    max_limitations: int = 10
    max_text_length: int = 1024

    def __post_init__(self) -> None:
        if min(self.max_findings, self.max_evidence, self.max_limitations,
               self.max_text_length) <= 0 or self.max_findings > 100:
            raise ValueError("mapping limits must be positive and module-run compatible")


@dataclass(frozen=True)
class _Policy:
    severity: str
    confidence: float
    recommendation: str
    reason: str
    limitation: str


_STATISTICAL = "Statistical parameter anomalies may arise from legitimate pruning, specialization, or architecture choices."
_INCOMPLETE = "Evidence coverage was incomplete; absence of additional findings must not be interpreted as model safety."
_BEHAVIOR = "Behavioral sensitivity is review evidence and does not by itself prove malicious intent or a backdoor."
_DIFFERENCE = "A model difference does not establish that the change was unauthorized or malicious."

_POLICIES: dict[str, _Policy] = {
    # M1
    "CUSTOM_OPERATOR_DOMAIN": _Policy("HIGH", .98, "QUARANTINE", "A custom ONNX operator domain was observed.",
        "Custom operators require a separately reviewed implementation and are not executed by Model Integrity."),
    "OPERATOR_NOT_APPROVED": _Policy("MEDIUM", .95, "REVIEW", "An operator outside the caller-approved set was observed.", _DIFFERENCE),
    "EXTERNAL_DATA_PATH_UNSAFE": _Policy("HIGH", .99, "QUARANTINE", "An unsafe external tensor-data path was declared.",
        "External tensor data was identified structurally and was not opened."),
    "EXTERNAL_DATA_LOCATION_MISSING": _Policy("HIGH", .99, "QUARANTINE",
        "External tensor data was declared without a usable location.",
        "External tensor data was identified structurally and was not opened."),
    "EXTERNAL_DATA_NOT_VERIFIED": _Policy("MEDIUM", .99, "REVIEW", "External tensor data was declared but not verified.", _INCOMPLETE),
    "NO_GRAPH_INPUTS": _Policy("HIGH", .99, "QUARANTINE", "The model graph declares no inputs.",
        "A missing graph boundary can indicate a malformed or specialized model and requires review."),
    "NO_GRAPH_OUTPUTS": _Policy("HIGH", .99, "QUARANTINE", "The model graph declares no outputs.",
        "A missing graph boundary can indicate a malformed or specialized model and requires review."),
    "INITIALIZER_NAME_MISSING": _Policy("MEDIUM", .99, "REVIEW",
        "A model initializer has no stable tensor name.",
        "Unnamed initializers reduce parameter traceability but do not establish malicious intent."),
    "DYNAMIC_OR_UNKNOWN_SHAPE": _Policy("LOW", .95, "REVIEW", "A dynamic or unknown model boundary shape was observed.",
        "Dynamic shapes can be legitimate but reduce static comparability."),
    "EMPTY_GRAPH": _Policy("HIGH", .99, "QUARANTINE", "The model graph contains no operator nodes.", _DIFFERENCE),
    # M2
    "PARAMETER_NAN": _Policy("HIGH", .99, "QUARANTINE", "Non-finite NaN values were observed in model parameters.",
        "Non-finite values can indicate corruption or numerical failure but do not establish malicious intent."),
    "PARAMETER_POSITIVE_INFINITY": _Policy("HIGH", .99, "QUARANTINE", "Positive infinity values were observed in model parameters.",
        "Non-finite values can indicate corruption or numerical failure but do not establish malicious intent."),
    "PARAMETER_NEGATIVE_INFINITY": _Policy("HIGH", .99, "QUARANTINE", "Negative infinity values were observed in model parameters.",
        "Non-finite values can indicate corruption or numerical failure but do not establish malicious intent."),
    "ALL_ZERO_TENSOR": _Policy("MEDIUM", .93, "REVIEW", "An all-zero parameter tensor was observed.", _STATISTICAL),
    "CONSTANT_TENSOR": _Policy("LOW", .92, "REVIEW", "A constant parameter tensor was observed.", _STATISTICAL),
    "EXTREME_ZERO_FRACTION": _Policy("LOW", .9, "REVIEW", "Extreme parameter sparsity was observed.", _STATISTICAL),
    "TENSOR_SCALE_OUTLIER": _Policy("MEDIUM", .82, "REVIEW", "A model-relative tensor scale outlier was observed.", _STATISTICAL),
    "CHANNEL_NORM_OUTLIER": _Policy("MEDIUM", .84, "REVIEW", "A model parameter channel-norm outlier was observed.", _STATISTICAL),
    "DEAD_CHANNEL": _Policy("MEDIUM", .9, "REVIEW", "An all-zero output channel was observed.", _STATISTICAL),
    "EXTREME_CHANNEL_SCALE": _Policy("HIGH", .88, "REVIEW", "Extreme parameter channel scale was observed.", _STATISTICAL),
    "PARAMETER_ENERGY_CONCENTRATION": _Policy("MEDIUM", .84, "REVIEW", "Parameter energy is concentrated in few channels.", _STATISTICAL),
    "PARAMETER_ANALYSIS_PARTIAL": _Policy("LOW", 1.0, "REVIEW", "Parameter analysis completed with partial coverage.", _INCOMPLETE),
    "PARAMETER_ANALYSIS_UNAVAILABLE": _Policy("MEDIUM", 1.0, "REVIEW", "Parameter analysis was unavailable.", _INCOMPLETE),
    # M3
    "RUNTIME_TIMEOUT": _Policy("MEDIUM", .99, "REVIEW", "Bounded behavioral execution timed out.", _INCOMPLETE),
    "RUNTIME_FAILURE": _Policy("MEDIUM", .98, "REVIEW", "Bounded behavioral execution failed.", _INCOMPLETE),
    "OUTPUT_NONFINITE": _Policy("HIGH", .99, "QUARANTINE", "Non-finite behavioral model output was observed.",
        "Non-finite output prevents reliable behavioral interpretation and does not establish malicious intent."),
    "OUTPUT_CONTRACT_CHANGED": _Policy("HIGH", .99, "QUARANTINE", "Runtime output violated the declared output contract.", _INCOMPLETE),
    "NONDETERMINISTIC_OUTPUT": _Policy("MEDIUM", .95, "REVIEW", "Repeated bounded execution produced different output commitments.",
        "Nondeterminism can arise from stochastic graphs or runtime implementations and is not proof of malicious behavior."),
    "TRIGGER_OUTPUT_DIVERGENCE": _Policy("MEDIUM", .9, "REVIEW", "A controlled trigger caused extreme output divergence.", _BEHAVIOR),
    "TRIGGER_PREDICTION_FLIP": _Policy("MEDIUM", .9, "REVIEW", "A controlled trigger caused consistent prediction flips.", _BEHAVIOR),
    "TRIGGER_TARGET_CONCENTRATION": _Policy("MEDIUM", .9, "REVIEW", "Triggered outputs concentrated on one class beyond baseline behavior.", _BEHAVIOR),
    "TRIGGER_SENSITIVITY": _Policy("HIGH", .95, "QUARANTINE",
        "Strong trigger-specific behavioral sensitivity was observed under the declared bounded test protocol.", _BEHAVIOR),
    "BEHAVIOR_ANALYSIS_PARTIAL": _Policy("LOW", 1.0, "REVIEW", "Behavioral analysis completed with partial coverage.", _INCOMPLETE),
    "BEHAVIOR_ANALYSIS_UNAVAILABLE": _Policy("MEDIUM", 1.0, "REVIEW", "Behavioral analysis was unavailable.", _INCOMPLETE),
    # M4
    "REFERENCE_ARTIFACT_MISMATCH": _Policy("MEDIUM", .99, "REVIEW", "Candidate artifact bytes differ from the designated reference.", _DIFFERENCE),
    "STRUCTURAL_FINGERPRINT_CHANGED": _Policy("HIGH", .99, "REVIEW", "The observed candidate model structure differs from the reference.", _DIFFERENCE),
    "PARAMETER_METADATA_CHANGED": _Policy("HIGH", .99, "REVIEW", "Candidate parameter metadata differs from the reference.", _DIFFERENCE),
    "PARAMETER_VALUE_CHANGED": _Policy("HIGH", .99, "REVIEW", "Candidate parameter values differ from the reference.", _DIFFERENCE),
    "NEW_PARAMETER_ANOMALY": _Policy("MEDIUM", .9, "REVIEW", "A candidate parameter anomaly was not observed in the reference.", _STATISTICAL),
    "NEW_TRIGGER_SENSITIVITY": _Policy("HIGH", .95, "QUARANTINE",
        "Candidate trigger-specific sensitivity was not observed in the reference under the matched bounded protocol.", _BEHAVIOR),
    "NEW_NONDETERMINISM": _Policy("MEDIUM", .95, "REVIEW", "Candidate nondeterminism was not observed in the reference.", _BEHAVIOR),
    "NEW_OUTPUT_NONFINITE": _Policy("HIGH", .99, "QUARANTINE", "Candidate non-finite output was not observed in the reference.", _BEHAVIOR),
    "BEHAVIOR_PROTOCOL_INCOMPARABLE": _Policy("LOW", 1.0, "REVIEW", "Reference and candidate behavioral evidence was incomparable.", _INCOMPLETE),
    "BEHAVIOR_COMPARISON_PARTIAL": _Policy("LOW", 1.0, "REVIEW",
        "Reference and candidate behavioral comparison had partial coverage.", _INCOMPLETE),
    "BEHAVIOR_COMPARISON_UNAVAILABLE": _Policy("MEDIUM", 1.0, "REVIEW",
        "Reference and candidate behavioral comparison was unavailable.", _INCOMPLETE),
    "FINDING_MAPPING_TRUNCATED": _Policy("LOW", 1.0, "REVIEW", "Model Integrity Finding output was truncated by its configured bound.", _INCOMPLETE),
}


def mapping_policy_inventory() -> dict[str, dict[str, object]]:
    """Stable documentation/test inventory; unmapped codes are intentionally omitted."""
    return {code: {"category": code, "severity": item.severity, "confidence": item.confidence,
        "recommendation": item.recommendation, "rationale": item.reason,
        "limitation": item.limitation} for code, item in sorted(_POLICIES.items())}


class ModelIntegrityFindingMapper:
    def __init__(self, adapter: ModelIntegrityAdapter, *, policy: FindingMappingPolicy | None = None) -> None:
        self.adapter = adapter
        self.policy = policy or FindingMappingPolicy()

    def map(self, bundle: ModelIntegrityEvidenceBundle,
            context: FindingMappingContext | None = None) -> list[Finding]:
        context = context or FindingMappingContext()
        asset_id = self._asset_id(bundle, context)
        candidates: list[tuple[Finding, tuple[Any, ...]]] = []
        if bundle.manifest:
            for issue in bundle.manifest.issues:
                self._append(candidates, asset_id, issue.code, issue.evidence,
                             ("M1", issue.code, tuple(sorted(issue.evidence))))
            if bundle.manifest.parameter_analysis:
                for issue in bundle.manifest.parameter_analysis.issues:
                    stable = ("M2", issue.code, issue.tensor_name, issue.channel_index)
                    self._append(candidates, asset_id, issue.code, issue.evidence, stable)
        if bundle.behavioral:
            for issue in bundle.behavioral.issues:
                trigger = self._evidence_value(issue.evidence, "trigger")
                self._append(candidates, asset_id, issue.code, issue.evidence,
                             ("M3", issue.code, trigger))
        if bundle.comparison:
            comparison = bundle.comparison
            base = [f"comparison_id={comparison.comparison_id}",
                    f"reference_status={comparison.reference_status.value}"]
            if comparison.artifact.state == ChangeState.CHANGED:
                self._append(candidates, asset_id, "REFERENCE_ARTIFACT_MISMATCH", base,
                             ("M4", comparison.comparison_id, "artifact"))
            for code in comparison.structure.codes:
                self._append(candidates, asset_id, code, base,
                             ("M4", comparison.comparison_id, code))
            if comparison.parameters.metadata_state == ChangeState.CHANGED:
                self._append(candidates, asset_id, "PARAMETER_METADATA_CHANGED", base,
                             ("M4", comparison.comparison_id, "parameter_metadata"))
            if comparison.parameters.value_state == ChangeState.CHANGED:
                evidence = base + [f"tensor={name}" for name in comparison.parameters.tensors_value_changed]
                self._append(candidates, asset_id, "PARAMETER_VALUE_CHANGED", evidence,
                             ("M4", comparison.comparison_id, "parameter_values"))
            for delta in comparison.parameter_issue_deltas:
                if delta.state == DeltaState.NEW:
                    evidence = base + [f"internal_code={delta.code}"]
                    if delta.tensor_name: evidence.append(f"tensor={delta.tensor_name}")
                    if delta.channel_index is not None: evidence.append(f"channel={delta.channel_index}")
                    self._append(candidates, asset_id, "NEW_PARAMETER_ANOMALY", evidence,
                        ("M4", comparison.comparison_id, delta.code, delta.tensor_name, delta.channel_index))
        if bundle.behavioral_comparison:
            behavior = bundle.behavioral_comparison
            if behavior.status == ComparisonStatus.INCOMPARABLE:
                self._append(candidates, asset_id, "BEHAVIOR_PROTOCOL_INCOMPARABLE",
                    behavior.limitations, ("M4B", "incomparable", tuple(behavior.limitations)))
            elif behavior.status == ComparisonStatus.PARTIAL:
                self._append(candidates, asset_id, "BEHAVIOR_COMPARISON_PARTIAL",
                    behavior.limitations, ("M4B", "partial", behavior.protocol_id))
            elif behavior.status == ComparisonStatus.UNAVAILABLE:
                self._append(candidates, asset_id, "BEHAVIOR_COMPARISON_UNAVAILABLE",
                    behavior.limitations, ("M4B", "unavailable", behavior.protocol_id))
            elif behavior.status == ComparisonStatus.COMPLETE:
                for code in behavior.codes:
                    identities = sorted({item.identity for item in behavior.issue_deltas
                        if ((code == "NEW_TRIGGER_SENSITIVITY" and item.code == "TRIGGER_SENSITIVITY"
                             and item.state == DeltaState.NEW)
                            or (code == "NEW_NONDETERMINISM" and item.code == "NONDETERMINISTIC_OUTPUT"
                                and item.state == DeltaState.NEW)
                            or (code == "NEW_OUTPUT_NONFINITE" and item.code == "OUTPUT_NONFINITE"
                                and item.state == DeltaState.NEW))}) or [""]
                    for identity in identities:
                        evidence = [f"protocol_id={behavior.protocol_id or 'unavailable'}"]
                        if identity: evidence.append(f"trigger_id={identity}")
                        trigger = next((item for item in behavior.trigger_deltas
                                        if item.trigger_id == identity), None)
                        if trigger:
                            for key, value in (("flip_rate_delta", trigger.flip_rate_delta),
                                ("concentration_lift_delta", trigger.concentration_lift_delta),
                                ("control_relative_delta", trigger.control_relative_delta)):
                                evidence.append(f"{key}={self._number(value)}")
                        self._append(candidates, asset_id, code, evidence,
                            ("M4B", behavior.protocol_id, code, identity))
        unique: dict[str, tuple[Finding, tuple[Any, ...]]] = {}
        for finding, sort_key in candidates: unique.setdefault(finding.finding_id, (finding, sort_key))
        ordered = sorted(unique.values(), key=lambda pair: (_SEVERITY_RANK[pair[0].severity.value],
            pair[0].category, pair[0].asset_id, pair[0].finding_id))
        if len(ordered) > self.policy.max_findings:
            retained = ordered[:self.policy.max_findings - 1]
            dropped = len(ordered) - len(retained)
            truncated: list[tuple[Finding, tuple[Any, ...]]] = []
            self._append(truncated, asset_id, "FINDING_MAPPING_TRUNCATED",
                         [f"retained={len(retained)}", f"dropped={dropped}"],
                         ("M5", "truncated", len(ordered)))
            ordered = retained + truncated
        return [item[0] for item in ordered]

    def _append(self, output, asset_id: str, code: str, evidence: list[str], stable: object) -> None:
        policy = _POLICIES.get(code)
        if policy is None: return
        cleaned = self._strings(evidence, self.policy.max_evidence)
        if not cleaned: cleaned = [f"observation={code}"]
        confidence = policy.confidence if isfinite(policy.confidence) else 0.0
        finding = self.adapter.finding(asset_type="model", asset_id=asset_id, category=code,
            severity=policy.severity, confidence=min(1.0, max(0.0, confidence)), reason=policy.reason,
            evidence=cleaned, recommendation=policy.recommendation,
            limitations=self._strings([policy.limitation], self.policy.max_limitations),
            stable_evidence_key=stable)
        output.append((finding, stable if isinstance(stable, tuple) else (str(stable),)))

    def _strings(self, values: list[str], maximum: int) -> list[str]:
        result = []
        for raw in values:
            value = str(raw).replace("\n", " ").replace("\r", " ")
            key = value.split("=", 1)[0].lower()
            normalized_key = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
            lower = value.lower()
            sensitive_key = (normalized_key in {"api_key", "authorization", "private_key"}
                or any(token in normalized_key.split("_")
                       for token in ("private", "password", "secret", "token", "bearer", "authorization"))
                or normalized_key.startswith("bearer") or normalized_key.endswith("token"))
            if sensitive_key:
                value = f"{key}=<redacted>"
            elif key in {"path", "location", "file", "filename"}:
                value = f"{key}=<redacted-path>"
            elif any(marker in lower for marker in ("bearer ", "token=", "password=", "private_key=")):
                value = "evidence=<redacted>"
            value = value[:self.policy.max_text_length].strip()
            if value and value not in result: result.append(value)
        return sorted(result)[:maximum]

    @staticmethod
    def _evidence_value(evidence: list[str], key: str) -> str | None:
        prefix = f"{key}="
        return next((item[len(prefix):] for item in evidence if item.startswith(prefix)), None)

    @staticmethod
    def _number(value: float | None) -> str:
        return "unavailable" if value is None or not isfinite(value) else format(value, ".12g")

    @staticmethod
    def _asset_id(bundle: ModelIntegrityEvidenceBundle, context: FindingMappingContext) -> str:
        identities = []
        if bundle.manifest: identities.append(bundle.manifest.artifact.artifact_id)
        if bundle.comparison: identities.append(bundle.comparison.candidate_artifact_id)
        if context.candidate_artifact_id: identities.append(context.candidate_artifact_id)
        if not identities or any(not _MODEL_ID.fullmatch(item) for item in identities):
            raise ValueError("an exact candidate model artifact identity is required")
        if len(set(identities)) != 1: raise ValueError("candidate artifact identities disagree")
        return identities[0]
