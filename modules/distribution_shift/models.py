"""Typed, immutable, strict-JSON-safe D1 profile evidence models."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


PROFILE_SCHEMA_VERSION = 1


class WindowRole(StrEnum):
    REFERENCE = "REFERENCE"
    CURRENT = "CURRENT"


class ReferenceSemantics(StrEnum):
    DESIGNATED_REFERENCE = "DESIGNATED_REFERENCE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ProfileStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class FailureCode(StrEnum):
    SYMLINK_REJECTED = "SYMLINK_REJECTED"
    PATH_ESCAPE_REJECTED = "PATH_ESCAPE_REJECTED"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    INVALID_IMAGE = "INVALID_IMAGE"
    UNSUPPORTED_IMAGE_MODE = "UNSUPPORTED_IMAGE_MODE"
    UNSUPPORTED_MULTIFRAME = "UNSUPPORTED_MULTIFRAME"
    IO_ERROR = "IO_ERROR"
    FILE_CHANGED = "FILE_CHANGED"


@dataclass(frozen=True)
class ProfileLimits:
    max_samples: int = 10_000
    max_file_bytes: int = 64 * 1024 * 1024
    max_pixels_per_image: int = 50_000_000
    max_failures_reported: int = 20
    max_relative_path_length: int = 256
    max_formats_reported: int = 16
    histogram_bins: int = 256
    hash_chunk_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.histogram_bins > 4096:
            raise ValueError("histogram_bins exceeds the supported bound")

    def identity_dict(self) -> dict[str, int]: return asdict(self)


@dataclass(frozen=True)
class SampleFailure:
    code: FailureCode
    relative_path: str


@dataclass(frozen=True)
class FeatureSummary:
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
class ImageWindowProfile:
    schema_version: int
    profile_id: str
    role: WindowRole
    reference_semantics: ReferenceSemantics
    status: ProfileStatus
    sample_count_discovered: int
    sample_count_selected: int
    sample_count_profiled: int
    sample_count_failed: int
    sample_count_skipped: int
    sample_set_commitment: str
    failure_counts: tuple[tuple[str, int], ...]
    failures: tuple[SampleFailure, ...]
    format_counts: tuple[tuple[str, int], ...]
    mode_counts: tuple[tuple[str, int], ...]
    width_summary: FeatureSummary | None
    height_summary: FeatureSummary | None
    pixel_count_summary: FeatureSummary | None
    aspect_ratio_summary: FeatureSummary | None
    brightness_summary: FeatureSummary | None
    contrast_summary: FeatureSummary | None
    sharpness_summary: FeatureSummary | None
    entropy_summary: FeatureSummary | None
    saturation_summary: FeatureSummary | None
    brightness_histogram: tuple[int, ...] | None
    saturation_histogram: tuple[int, ...] | None
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]: return asdict(self)
