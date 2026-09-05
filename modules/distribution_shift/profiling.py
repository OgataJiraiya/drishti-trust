"""D1 window profiling orchestration; no comparison or drift judgment."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

import numpy as np

from backend.core.canonical import canonical_json_bytes
from .image_features import ImageFeatures, SampleProcessingError, extract_image_features
from .intake import enumerate_image_window
from .models import (PROFILE_SCHEMA_VERSION, FeatureSummary, ImageWindowProfile, ProfileLimits,
    ProfileStatus, ReferenceSemantics, SampleFailure, WindowRole)


def _summary(values: Iterable[float]) -> FeatureSummary | None:
    array = np.asarray(tuple(values), dtype=np.float64)
    if array.size == 0: return None
    quantiles = np.quantile(array, [.05, .25, .5, .75, .95], method="linear")
    result = FeatureSummary(int(array.size), float(np.min(array)), float(np.max(array)),
        float(np.mean(array)), float(np.std(array, ddof=0)), float(quantiles[2]),
        float(quantiles[0]), float(quantiles[1]), float(quantiles[3]), float(quantiles[4]))
    json.dumps(asdict(result), allow_nan=False); return result


def _counts(values: Iterable[str], maximum: int | None = None) -> tuple[tuple[str, int], ...]:
    counter = Counter(values)
    items = sorted(counter.items())
    if maximum is not None and len(items) > maximum:
        retained = items[:max(0, maximum - 1)]; retained.append(("OTHER", sum(value for _, value in items[len(retained):])))
        items = retained
    return tuple(items)


class ImageWindowProfiler:
    def __init__(self, limits: ProfileLimits | None = None) -> None:
        self.limits = limits or ProfileLimits()

    def profile_directory(self, root: Path | str, *, role: WindowRole = WindowRole.CURRENT
                          ) -> ImageWindowProfile:
        if not isinstance(role, WindowRole): raise ValueError("role must be WindowRole")
        window = enumerate_image_window(root, self.limits)
        discovered = len(window.files) + len(window.failures)
        selected_paths = window.files[:self.limits.max_samples]
        truncated = max(0, len(window.files) - len(selected_paths))
        failures = list(window.failures); features: list[ImageFeatures] = []
        for path in selected_paths:
            relative = path.relative_to(window.root).as_posix()[:self.limits.max_relative_path_length]
            try: features.append(extract_image_features(path, self.limits))
            except SampleProcessingError as exc: failures.append(SampleFailure(exc.code, relative))
        failures.sort(key=lambda item: (item.relative_path, item.code.value))
        failure_counts = _counts(item.code.value for item in failures)
        limitations = []
        if truncated: limitations.append("SAMPLE_SELECTION_TRUNCATED")
        if failures: limitations.append("SAMPLES_EXCLUDED_BY_SAFE_INTAKE")
        if len(failures) > self.limits.max_failures_reported: limitations.append("FAILURE_EXAMPLES_TRUNCATED")
        if not features: status = ProfileStatus.UNAVAILABLE
        elif truncated or failures: status = ProfileStatus.PARTIAL
        else: status = ProfileStatus.COMPLETE
        commitments = sorted(({"content_sha256": item.content_sha256, "byte_size": item.byte_size,
            "detected_format": item.detected_format, "width": item.width, "height": item.height}
            for item in features), key=lambda value: canonical_json_bytes(value))
        # The list retains duplicate entries: duplicate files are separate observations.
        sample_set = sha256(canonical_json_bytes(commitments)).hexdigest()
        def values(name): return (getattr(item, name) for item in features)
        brightness_hist = tuple(int(value) for value in np.histogram(tuple(values("brightness")),
            bins=self.limits.histogram_bins, range=(0, 1))[0]) if features else None
        saturation_hist = tuple(int(value) for value in np.histogram(tuple(values("saturation")),
            bins=self.limits.histogram_bins, range=(0, 1))[0]) if features else None
        summaries = {name: _summary(values(name)) for name in ("width", "height", "pixel_count",
            "aspect_ratio", "brightness", "contrast", "sharpness", "entropy", "saturation")}
        core = {"schema_version": PROFILE_SCHEMA_VERSION, "status": status.value,
            "limits": self.limits.identity_dict(), "sample_count_discovered": discovered,
            "sample_count_selected": len(selected_paths), "sample_count_profiled": len(features),
            "sample_count_failed": len(failures), "sample_count_skipped": truncated,
            "sample_set_commitment": sample_set, "failure_counts": failure_counts,
            "format_counts": _counts((item.detected_format for item in features), self.limits.max_formats_reported),
            "mode_counts": _counts(item.mode_group for item in features),
            "summaries": {name: asdict(value) if value is not None else None
                          for name, value in summaries.items()}, "brightness_histogram": brightness_hist,
            "saturation_histogram": saturation_hist, "limitations": tuple(sorted(limitations))}
        digest = sha256(canonical_json_bytes(core)).hexdigest()
        profile = ImageWindowProfile(PROFILE_SCHEMA_VERSION, f"drift-profile:sha256:{digest}", role,
            ReferenceSemantics.DESIGNATED_REFERENCE if role == WindowRole.REFERENCE else ReferenceSemantics.NOT_APPLICABLE,
            status, discovered, len(selected_paths), len(features), len(failures), truncated, sample_set,
            failure_counts, tuple(failures[:self.limits.max_failures_reported]), core["format_counts"],
            core["mode_counts"], summaries["width"], summaries["height"], summaries["pixel_count"],
            summaries["aspect_ratio"], summaries["brightness"], summaries["contrast"],
            summaries["sharpness"], summaries["entropy"], summaries["saturation"],
            brightness_hist, saturation_hist, tuple(sorted(limitations)))
        json.dumps(profile.to_dict(), allow_nan=False); return profile

    def profile_reference(self, root: Path | str) -> ImageWindowProfile:
        return self.profile_directory(root, role=WindowRole.REFERENCE)

    def profile_current(self, root: Path | str) -> ImageWindowProfile:
        return self.profile_directory(root, role=WindowRole.CURRENT)
