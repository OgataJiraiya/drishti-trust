"""Immutable public models for D3 representation-shift evidence."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Any

from backend.core.canonical import canonical_json_bytes

REPRESENTATION_SCHEMA_VERSION = 1


class RepresentationRole(StrEnum):
    REFERENCE = "REFERENCE"
    CURRENT = "CURRENT"


class NormalizationMode(StrEnum):
    NONE = "NONE"
    L2_PER_SAMPLE = "L2_PER_SAMPLE"


class ExtractorIdentityBasis(StrEnum):
    CALLER_DECLARED = "CALLER_DECLARED"
    DIGEST_DECLARED = "DIGEST_DECLARED"


class RepresentationProfileStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class RepresentationFeatureState(StrEnum):
    STABLE = "STABLE"
    SHIFTED = "SHIFTED"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPARABLE = "INCOMPARABLE"


class RepresentationReportStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPARABLE = "INCOMPARABLE"


@dataclass(frozen=True)
class RepresentationLimits:
    max_samples: int = 10_000
    max_dimensions: int = 4096
    max_total_values: int = 10_000_000
    max_pairwise_samples: int = 2000
    max_covariance_dimensions: int = 512
    max_nearest_reference_points: int = 2000
    max_sample_id_length: int = 256

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    def to_dict(self) -> dict[str, int]: return asdict(self)


@dataclass(frozen=True)
class RepresentationSpaceDescriptor:
    extractor_name: str
    extractor_version: str
    output_dimension: int
    normalization: NormalizationMode = NormalizationMode.NONE
    extractor_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.extractor_name, str) or not self.extractor_name.strip():
            raise ValueError("extractor_name must be a non-empty string")
        if not isinstance(self.extractor_version, str) or not self.extractor_version.strip():
            raise ValueError("extractor_version must be a non-empty string")
        if type(self.output_dimension) is not int or self.output_dimension <= 0:
            raise ValueError("output_dimension must be a positive integer")
        if not isinstance(self.normalization, NormalizationMode):
            raise ValueError("normalization must be a NormalizationMode")
        if self.extractor_digest is not None:
            value = self.extractor_digest
            if not (isinstance(value, str) and value.startswith("sha256:") and len(value) == 71
                    and all(c in "0123456789abcdef" for c in value[7:])):
                raise ValueError("extractor_digest must be sha256:<64 lowercase hex>")

    @property
    def identity_basis(self) -> ExtractorIdentityBasis:
        return (ExtractorIdentityBasis.DIGEST_DECLARED if self.extractor_digest
                else ExtractorIdentityBasis.CALLER_DECLARED)

    def semantics_dict(self) -> dict[str, Any]:
        return {"extractor_name": self.extractor_name, "extractor_version": self.extractor_version,
                "output_dimension": self.output_dimension, "normalization": self.normalization.value,
                "extractor_digest": self.extractor_digest,
                "identity_basis": self.identity_basis.value}

    @property
    def space_id(self) -> str:
        return "representation-space:sha256:" + sha256(
            canonical_json_bytes({"schema_version": 1, **self.semantics_dict()})).hexdigest()


@dataclass(frozen=True)
class RepresentationSummary:
    count: int
    minimum: float
    maximum: float
    mean: float
    population_std: float
    median: float
    q05: float
    q25: float
    q75: float
    q95: float


@dataclass(frozen=True)
class VarianceSummary:
    mean: float
    median: float
    maximum: float
    total: float


@dataclass(frozen=True)
class RepresentationProfile:
    schema_version: int
    profile_id: str
    role: RepresentationRole
    space_id: str
    descriptor: RepresentationSpaceDescriptor
    status: RepresentationProfileStatus
    sample_count_supplied: int
    sample_count: int
    dimension: int
    sample_set_commitment: str
    centroid: tuple[float, ...] | None
    centroid_norm: float | None
    norm_summary: RepresentationSummary | None
    radial_summary: RepresentationSummary | None
    variance_diagonal: tuple[float, ...] | None
    variance_summary: VarianceSummary | None
    covariance_commitment: str | None
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class RepresentationMetric:
    name: str
    value: float | None
    threshold: float | None
    exceeded: bool | None


@dataclass(frozen=True)
class RepresentationFeatureComparison:
    name: str
    state: RepresentationFeatureState
    reference_count: int
    current_count: int
    metrics: tuple[RepresentationMetric, ...]
    exceeded_metrics: tuple[str, ...]
    reason: str
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class RepresentationComparisonPolicy:
    min_reference_samples: int = 20
    min_current_samples: int = 20
    centroid_cosine_distance_threshold: float = 0.10
    normalized_centroid_shift_threshold: float = 0.50
    dispersion_shift_threshold: float = 0.50
    variance_shift_threshold: float = 0.50
    mmd_threshold: float = 0.05
    nearest_support_shift_threshold: float = 1.50
    bandwidth_strategy: str = "MEDIAN_SQUARED_DISTANCE"
    fixed_bandwidth: float = 1.0
    epsilon: float = 1e-12

    def __post_init__(self) -> None:
        if type(self.min_reference_samples) is not int or self.min_reference_samples <= 0:
            raise ValueError("min_reference_samples must be positive")
        if type(self.min_current_samples) is not int or self.min_current_samples <= 0:
            raise ValueError("min_current_samples must be positive")
        if self.bandwidth_strategy not in {"MEDIAN_SQUARED_DISTANCE", "FIXED"}:
            raise ValueError("unsupported bandwidth_strategy")
        for name, value in asdict(self).items():
            if name.startswith("min_") or name == "bandwidth_strategy": continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")

    def to_dict(self) -> dict[str, Any]: return asdict(self)

    @property
    def policy_id(self) -> str:
        return "representation-policy:sha256:" + sha256(
            canonical_json_bytes({"schema_version": 1, **self.to_dict()})).hexdigest()


@dataclass(frozen=True)
class RepresentationShiftReport:
    schema_version: int
    comparison_id: str
    policy_id: str
    reference_profile_id: str
    current_profile_id: str
    representation_space_id: str | None
    status: RepresentationReportStatus
    reference_sample_count: int
    current_sample_count: int
    dimension: int | None
    resolved_bandwidth: float | None
    feature_comparisons: tuple[RepresentationFeatureComparison, ...]
    shifted_features: tuple[str, ...]
    stable_features: tuple[str, ...]
    partial_features: tuple[str, ...]
    unavailable_features: tuple[str, ...]
    incomparable_features: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]: return asdict(self)
