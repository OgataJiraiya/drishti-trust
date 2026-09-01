"""Strict internal value models with deterministic JSON-safe serialization."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class InspectionLevel(StrEnum):
    ARTIFACT_ONLY = "ARTIFACT_ONLY"
    STRUCTURAL = "STRUCTURAL"
    UNSUPPORTED = "UNSUPPORTED"


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


@dataclass(frozen=True)
class ModelManifest:
    schema_version: str
    artifact: ArtifactReport
    structure: OnnxStructure | None
    fingerprints: Fingerprints
    issues: list[StructuralIssue]
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
