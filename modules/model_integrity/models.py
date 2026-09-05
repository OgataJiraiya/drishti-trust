"""Strict internal value models with deterministic JSON-safe serialization."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class InspectionLevel(StrEnum):
    ARTIFACT_ONLY = "ARTIFACT_ONLY"
    STRUCTURAL = "STRUCTURAL"
    UNSUPPORTED = "UNSUPPORTED"


class AnalysisStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class AnalysisMode(StrEnum):
    FULL = "FULL"
    SAMPLED = "SAMPLED"


@dataclass(frozen=True)
class ArtifactReport:
    artifact_id: str
    filename: str
    extension: str
    sha256: str
    byte_size: int
    claimed_format: str
    inspection_level: InspectionLevel


@dataclass(frozen=True)
class TensorMetadata:
    name: str
    dtype: str
    shape: list[int | str | None]
    element_count: int | None
    raw_data_byte_length: int | None = None
    external_data: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValueMetadata:
    name: str
    dtype: str
    shape: list[int | str | None]


@dataclass(frozen=True)
class StructuralIssue:
    code: str
    severity_hint: str
    evidence: list[str]
    explanation: str


@dataclass(frozen=True)
class OnnxStructure:
    ir_version: int
    producer_name: str
    producer_version: str
    domain: str
    model_version: int
    opsets: list[dict[str, Any]]
    graph_name: str
    inputs: list[ValueMetadata]
    outputs: list[ValueMetadata]
    nodes: list[dict[str, Any]]
    node_count: int
    operator_profile: dict[str, int]
    unique_operator_count: int
    initializers: list[TensorMetadata]
    initializer_count: int
    total_parameter_count: int | None


@dataclass(frozen=True)
class Fingerprints:
    artifact_sha256: str
    structural_sha256: str | None
    parameter_metadata_sha256: str | None
    parameter_value_sha256: str | None = None
    parameter_value_status: AnalysisStatus = AnalysisStatus.UNAVAILABLE


@dataclass(frozen=True)
class TensorStatistics:
    tensor_name: str
    dtype: str
    shape: list[int]
    analysis_mode: AnalysisMode
    analyzed_element_count: int
    total_element_count: int
    finite_count: int
    nan_count: int
    positive_infinity_count: int
    negative_infinity_count: int
    minimum: float | int | None
    maximum: float | int | None
    mean: float | None
    standard_deviation: float | None
    rms: float | None
    absolute_maximum: float | int | None
    l1_norm: float | None
    l2_norm: float | None
    zero_fraction: float | None
    near_zero_fraction: float | None
    median: float | None
    mad: float | None


@dataclass(frozen=True)
class ChannelStatistics:
    channel_index: int
    element_count: int
    l2_norm: float
    rms: float
    absolute_maximum: float
    zero_fraction: float


@dataclass(frozen=True)
class ParameterIssue:
    code: str
    severity_hint: str
    tensor_name: str | None
    channel_index: int | None
    evidence: list[str]
    explanation: str


@dataclass(frozen=True)
class ParameterCoverage:
    total_parameter_tensors: int
    analyzable_parameter_tensors: int
    analyzed_parameter_tensors: int
    total_parameter_elements: int
    analyzed_parameter_elements: int
    total_parameter_bytes: int
    analyzed_parameter_bytes: int
    tensor_coverage: float
    element_coverage: float


@dataclass(frozen=True)
class ParameterAnalysisReport:
    status: AnalysisStatus
    coverage: ParameterCoverage
    summary: dict[str, int | float]
    tensor_statistics: list[TensorStatistics]
    issues: list[ParameterIssue]
    limitations: list[str]


@dataclass(frozen=True)
class ModelManifest:
    schema_version: str
    artifact: ArtifactReport
    structure: OnnxStructure | None
    fingerprints: Fingerprints
    issues: list[StructuralIssue]
    limitations: list[str]
    parameter_analysis: ParameterAnalysisReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
