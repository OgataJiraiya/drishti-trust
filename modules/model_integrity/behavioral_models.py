"""Typed, bounded value models for explicit behavioral execution reports."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class BehavioralAnalysisStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class RuntimeStatus(StrEnum):
    SUCCESS = "SUCCESS"
    TIMEOUT = "TIMEOUT"
    FAILURE = "FAILURE"


class InputLayout(StrEnum):
    NCHW = "NCHW"
    NHWC = "NHWC"


class TriggerKind(StrEnum):
    SOLID_PATCH = "SOLID_PATCH"
    ZERO_PATCH = "ZERO_PATCH"
    CHECKERBOARD_PATCH = "CHECKERBOARD_PATCH"


class TriggerLocation(StrEnum):
    TOP_LEFT = "TOP_LEFT"
    TOP_RIGHT = "TOP_RIGHT"
    BOTTOM_LEFT = "BOTTOM_LEFT"
    BOTTOM_RIGHT = "BOTTOM_RIGHT"
    CENTER = "CENTER"


class ClassificationKind(StrEnum):
    LOGITS = "LOGITS"
    PROBABILITIES = "PROBABILITIES"


@dataclass(frozen=True)
class InputContract:
    name: str
    dtype: str
    shape: list[int]
    layout: InputLayout
    channels: int
    value_min: float
    value_max: float


@dataclass(frozen=True)
class OutputContract:
    name: str
    classification_kind: ClassificationKind | None = None
    class_axis: int = -1


@dataclass(frozen=True)
class TriggerSpec:
    trigger_id: str
    kind: TriggerKind
    location: TriggerLocation
    patch_height: int
    patch_width: int


@dataclass(frozen=True)
class OutputTensorSummary:
    name: str
    dtype: str
    shape: list[int]
    element_count: int
    finite_count: int
    nan_count: int
    positive_infinity_count: int
    negative_infinity_count: int
    minimum: float | int | None
    maximum: float | int | None
    mean: float | None
    rms: float | None
    fingerprint: str


@dataclass(frozen=True)
class OutputDelta:
    maximum_absolute_difference: float | None
    mean_absolute_difference: float | None
    normalized_l2_delta: float | None
    cosine_similarity: float | None


@dataclass(frozen=True)
class BehavioralIssue:
    code: str
    review_level: str
    evidence: list[str]
    explanation: str


@dataclass(frozen=True)
class TriggerBehavior:
    trigger: TriggerSpec
    samples_attempted: int
    samples_successful: int
    mean_normalized_output_delta: float | None
    median_normalized_output_delta: float | None
    prediction_flip_count: int | None
    prediction_flip_rate: float | None
    dominant_triggered_class: int | None
    dominant_class_rate: float | None
    clean_dominant_class_rate: float | None
    target_concentration_lift: float | None
    mean_top1_score_shift: float | None
    control_median_flip_rate: float | None
    control_relative_flip_rate: float | None


@dataclass(frozen=True)
class BehavioralCoverage:
    requested_samples: int
    successful_clean_samples: int
    requested_trigger_runs: int
    successful_trigger_runs: int
    failed_runs: int
    timed_out_runs: int
    triggers_requested: int
    triggers_analyzed: int
    output_tensors_expected: int
    output_tensors_analyzed: int
    clean_sample_coverage: float
    trigger_run_coverage: float


@dataclass(frozen=True)
class BehavioralAnalysisReport:
    status: BehavioralAnalysisStatus
    runtime: str
    coverage: BehavioralCoverage
    repeatability_stable: bool | None
    clean_outputs: list[OutputTensorSummary]
    triggers: list[TriggerBehavior]
    issues: list[BehavioralIssue]
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
