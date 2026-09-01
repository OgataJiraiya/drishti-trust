"""Immutable, strict-JSON-safe D2 statistical comparison evidence."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Any

from backend.core.canonical import canonical_json_bytes
from .models import FeatureSummary


COMPARISON_SCHEMA_VERSION = 1


class FeatureDriftState(StrEnum):
    STABLE = "STABLE"
    SHIFTED = "SHIFTED"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPARABLE = "INCOMPARABLE"


class ComparisonStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPARABLE = "INCOMPARABLE"


class ChangeDirection(StrEnum):
    INCREASED = "INCREASED"
    DECREASED = "DECREASED"
    MIXED = "MIXED"
    SAME = "SAME"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DistributionComparisonPolicy:
    min_reference_samples: int = 20
    min_current_samples: int = 20
    standardized_mean_difference_threshold: float = 0.8
    robust_quantile_shift_threshold: float = 0.5
    symmetric_median_change_threshold: float = 0.25
    jensen_shannon_threshold: float = 0.10
    total_variation_threshold: float = 0.25
    histogram_wasserstein_threshold: float = 0.10
    epsilon: float = 1e-12

    def __post_init__(self) -> None:
        if type(self.min_reference_samples) is not int or self.min_reference_samples <= 0:
            raise ValueError("min_reference_samples must be a positive integer")
        if type(self.min_current_samples) is not int or self.min_current_samples <= 0:
            raise ValueError("min_current_samples must be a positive integer")
        values = asdict(self)
        for name, value in values.items():
            if name.startswith("min_"): continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("jensen_shannon_threshold", "total_variation_threshold",
                     "histogram_wasserstein_threshold"):
            if getattr(self, name) > 1: raise ValueError(f"{name} must be at most 1")
        if self.symmetric_median_change_threshold > 2:
            raise ValueError("symmetric_median_change_threshold must be at most 2")

    def to_dict(self) -> dict[str, Any]: return asdict(self)

    @property
    def policy_id(self) -> str:
        digest = sha256(canonical_json_bytes({"schema_version": 1, **self.to_dict()})).hexdigest()
        return f"drift-policy:sha256:{digest}"


@dataclass(frozen=True)
class DriftMetric:
    name: str
    value: float | None
    threshold: float | None
    exceeded: bool | None


@dataclass(frozen=True)
class FeatureDriftComparison:
    feature: str
    state: FeatureDriftState
    direction: ChangeDirection
    reference_count: int
    current_count: int
    reference_summary: FeatureSummary | None
    current_summary: FeatureSummary | None
    reference_mean: float | None
    current_mean: float | None
    mean_delta: float | None
    reference_median: float | None
    current_median: float | None
    median_delta: float | None
    reference_std: float | None
    current_std: float | None
    constant_distribution_shift: bool
    metrics: tuple[DriftMetric, ...]
    exceeded_metrics: tuple[str, ...]
    reason: str
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class DistributionShiftComparisonReport:
    schema_version: int
    comparison_id: str
    reference_profile_id: str
    current_profile_id: str
    status: ComparisonStatus
    reference_sample_count: int
    current_sample_count: int
    policy_id: str
    policy: DistributionComparisonPolicy
    feature_comparisons: tuple[FeatureDriftComparison, ...]
    shifted_features: tuple[str, ...]
    stable_features: tuple[str, ...]
    partial_features: tuple[str, ...]
    unavailable_features: tuple[str, ...]
    incomparable_features: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]: return asdict(self)
