"""Typed JSON-safe value models for deterministic M4 baseline comparison."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ComparisonStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPARABLE = "INCOMPARABLE"


class ChangeState(StrEnum):
    SAME = "SAME"
    CHANGED = "CHANGED"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPARABLE = "INCOMPARABLE"
    NOT_ASSESSED = "NOT_ASSESSED"


class ReferenceStatus(StrEnum):
    DESIGNATED_ONLY = "DESIGNATED_ONLY"
    VERIFIED_REFERENCE = "VERIFIED_REFERENCE"


class DeltaState(StrEnum):
    NEW = "NEW"
    PERSISTING = "PERSISTING"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True)
class ReferenceBaseline:
    schema_version: int
    reference_artifact_id: str
    artifact_sha256: str
    structural_sha256: str | None
    parameter_metadata_sha256: str | None
    parameter_value_sha256: str | None
    parameter_value_status: str
    baseline_payload_sha256: str
    baseline_id: str

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class BaselineVerification:
    valid: bool
    baseline: ReferenceBaseline | None
    errors: list[str]

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class ArtifactComparison:
    reference_sha256: str
    candidate_sha256: str
    state: ChangeState


@dataclass(frozen=True)
class NamedChange:
    name: str
    reference: str | int | list[Any] | None
    candidate: str | int | list[Any] | None


@dataclass(frozen=True)
class StructureComparison:
    state: ChangeState
    reference_fingerprint: str | None
    candidate_fingerprint: str | None
    codes: list[str]
    operator_changes: list[NamedChange]
    opset_changes: list[NamedChange]
    input_changes: list[NamedChange]
    output_changes: list[NamedChange]
    initializer_changes: list[NamedChange]


@dataclass(frozen=True)
class TensorValueIdentity:
    name: str
    dtype: int
    shape: list[int]
    value_sha256: str


@dataclass(frozen=True)
class ParameterComparison:
    metadata_state: ChangeState
    value_state: ChangeState
    reference_metadata_sha256: str | None
    candidate_metadata_sha256: str | None
    reference_value_sha256: str | None
    candidate_value_sha256: str | None
    reference_value_status: str
    candidate_value_status: str
    tensors_added: list[str]
    tensors_removed: list[str]
    tensors_value_changed: list[str]
    tensors_shape_changed: list[str]
    tensors_dtype_changed: list[str]


@dataclass(frozen=True)
class IssueDelta:
    state: DeltaState
    code: str
    tensor_name: str | None
    channel_index: int | None


@dataclass(frozen=True)
class BaselineComparisonIssue:
    code: str
    explanation: str


@dataclass(frozen=True)
class BehavioralProtocol:
    protocol_id: str
    corpus_sha256: str
    input_contract_sha256: str
    output_contract_sha256: str
    trigger_suite_sha256: str
    limits_sha256: str
    runtime_identity: str


@dataclass(frozen=True)
class BehavioralTriggerDelta:
    trigger_id: str
    flip_rate_delta: float | None
    concentration_lift_delta: float | None
    control_relative_delta: float | None


@dataclass(frozen=True)
class BehavioralIssueDelta:
    state: DeltaState
    code: str
    identity: str


@dataclass(frozen=True)
class BehavioralComparison:
    status: ComparisonStatus
    state: ChangeState
    protocol_id: str | None
    reference_status: str
    candidate_status: str
    repeatability_state: ChangeState
    trigger_deltas: list[BehavioralTriggerDelta]
    issue_deltas: list[BehavioralIssueDelta]
    codes: list[str]
    limitations: list[str]


@dataclass(frozen=True)
class BaselineComparisonReport:
    schema_version: int
    comparison_id: str
    status: ComparisonStatus
    reference_status: ReferenceStatus
    expected_reference_sha256: str | None
    reference_registry_state: str | None
    reference_artifact_id: str
    candidate_artifact_id: str
    artifact: ArtifactComparison
    structure: StructureComparison
    parameters: ParameterComparison
    parameter_issue_deltas: list[IssueDelta]
    behavior: BehavioralComparison | None
    interpretation: str
    issues: list[BaselineComparisonIssue]
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class BaselineComparisonLimits:
    max_changed_tensors: int = 256
    max_operator_deltas: int = 128
    max_initializer_deltas: int = 256
    max_issue_deltas: int = 200
    max_behavior_trigger_deltas: int = 64
    max_limitations: int = 100
    max_explanation_length: int = 512
    max_baseline_file_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if any(value <= 0 for value in asdict(self).values()):
            raise ValueError("baseline comparison limits must be positive")
