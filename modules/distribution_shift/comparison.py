"""Pure D2 comparison of frozen D1 aggregate profiles."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from math import isfinite
from typing import Callable

from backend.core.canonical import canonical_json_bytes
from .comparison_models import (COMPARISON_SCHEMA_VERSION, ChangeDirection, ComparisonStatus,
    DistributionComparisonPolicy, DistributionShiftComparisonReport, DriftMetric,
    FeatureDriftComparison, FeatureDriftState)
from .models import FeatureSummary, ImageWindowProfile, PROFILE_SCHEMA_VERSION, ProfileStatus, WindowRole
from .statistics import (histogram_wasserstein, jensen_shannon, robust_quantile_shift,
    standardized_mean_difference, symmetric_median_change, total_variation)


FEATURE_ORDER = ("WIDTH", "HEIGHT", "PIXEL_COUNT", "ASPECT_RATIO", "BRIGHTNESS",
    "CONTRAST", "SHARPNESS", "ENTROPY", "SATURATION",
    "IMAGE_MODE_DISTRIBUTION", "IMAGE_FORMAT_DISTRIBUTION")
SUMMARY_FIELDS = {"WIDTH": "width_summary", "HEIGHT": "height_summary",
    "PIXEL_COUNT": "pixel_count_summary", "ASPECT_RATIO": "aspect_ratio_summary",
    "BRIGHTNESS": "brightness_summary", "CONTRAST": "contrast_summary",
    "SHARPNESS": "sharpness_summary", "ENTROPY": "entropy_summary",
    "SATURATION": "saturation_summary"}
HISTOGRAM_FIELDS = {"BRIGHTNESS": "brightness_histogram", "SATURATION": "saturation_histogram"}


def _valid_summary(value: FeatureSummary | None) -> bool:
    if value is None or type(value.count) is not int or value.count <= 0: return False
    numbers = (value.minimum, value.maximum, value.mean, value.population_std, value.median,
               value.q05, value.q25, value.q75, value.q95)
    return (all(isinstance(item, (int, float)) and not isinstance(item, bool) and isfinite(item)
                for item in numbers) and value.population_std >= 0
        and value.minimum <= value.q05 <= value.q25 <= value.median <= value.q75 <= value.q95 <= value.maximum)


def _direction(reference: float, current: float, epsilon: float) -> ChangeDirection:
    if abs(current-reference) <= epsilon: return ChangeDirection.SAME
    return ChangeDirection.INCREASED if current > reference else ChangeDirection.DECREASED


def _metric(name: str, value: float | None, threshold: float) -> DriftMetric:
    return DriftMetric(name, value, threshold, None if value is None else value >= threshold)


def _empty(feature: str, state: FeatureDriftState, reference_count: int, current_count: int,
           reason: str, limitations: tuple[str, ...]) -> FeatureDriftComparison:
    return FeatureDriftComparison(feature, state, ChangeDirection.UNKNOWN, reference_count,
        current_count, None, None, None, None, None, None, None, None, None, None, False,
        (), (), reason, limitations)


class DistributionShiftComparator:
    def __init__(self, policy: DistributionComparisonPolicy | None = None) -> None:
        self.policy = policy or DistributionComparisonPolicy()

    def compare(self, reference_profile: ImageWindowProfile,
                current_profile: ImageWindowProfile) -> DistributionShiftComparisonReport:
        if reference_profile.role != WindowRole.REFERENCE:
            raise ValueError("reference_profile must have REFERENCE role")
        if current_profile.role != WindowRole.CURRENT:
            raise ValueError("current_profile must have CURRENT role")
        limitations: list[str] = []
        if (reference_profile.schema_version != PROFILE_SCHEMA_VERSION
                or current_profile.schema_version != PROFILE_SCHEMA_VERSION):
            limitations.append("D1_PROFILE_SCHEMA_INCOMPATIBLE")
            features = tuple(_empty(name, FeatureDriftState.INCOMPARABLE,
                reference_profile.sample_count_profiled, current_profile.sample_count_profiled,
                "Input profile schema is incompatible with D2.", ("PROFILE_SCHEMA_INCOMPATIBLE",))
                for name in FEATURE_ORDER)
            return self._report(reference_profile, current_profile, ComparisonStatus.INCOMPARABLE,
                                features, limitations)
        if (reference_profile.status == ProfileStatus.UNAVAILABLE
                or current_profile.status == ProfileStatus.UNAVAILABLE):
            limitations.append("INPUT_PROFILE_UNAVAILABLE")
            features = tuple(_empty(name, FeatureDriftState.UNAVAILABLE,
                reference_profile.sample_count_profiled, current_profile.sample_count_profiled,
                "Insufficient usable profile evidence for comparison.",
                ("INPUT_PROFILE_UNAVAILABLE",)) for name in FEATURE_ORDER)
            return self._report(reference_profile, current_profile, ComparisonStatus.UNAVAILABLE,
                                features, limitations)
        support = (reference_profile.sample_count_profiled >= self.policy.min_reference_samples
                   and current_profile.sample_count_profiled >= self.policy.min_current_samples)
        if not support: limitations.append("INSUFFICIENT_SAMPLE_SUPPORT")
        upstream_partial = (reference_profile.status == ProfileStatus.PARTIAL
                            or current_profile.status == ProfileStatus.PARTIAL)
        if upstream_partial: limitations.append("UPSTREAM_D1_PROFILE_PARTIAL")
        features = []
        for name in FEATURE_ORDER:
            if name in SUMMARY_FIELDS:
                features.append(self._continuous(name, reference_profile, current_profile, support))
            else:
                field = "mode_counts" if name == "IMAGE_MODE_DISTRIBUTION" else "format_counts"
                features.append(self._categorical(name, getattr(reference_profile, field),
                    getattr(current_profile, field), reference_profile.sample_count_profiled,
                    current_profile.sample_count_profiled, support))
        degraded = upstream_partial or not support or any(item.state in {
            FeatureDriftState.PARTIAL, FeatureDriftState.UNAVAILABLE,
            FeatureDriftState.INCOMPARABLE} for item in features)
        status = ComparisonStatus.PARTIAL if degraded else ComparisonStatus.COMPLETE
        return self._report(reference_profile, current_profile, status, tuple(features), limitations)

    def _continuous(self, name: str, reference: ImageWindowProfile, current: ImageWindowProfile,
                    support: bool) -> FeatureDriftComparison:
        left = getattr(reference, SUMMARY_FIELDS[name]); right = getattr(current, SUMMARY_FIELDS[name])
        left_count = left.count if isinstance(left, FeatureSummary) else 0
        right_count = right.count if isinstance(right, FeatureSummary) else 0
        if not _valid_summary(left) or not _valid_summary(right):
            return _empty(name, FeatureDriftState.INCOMPARABLE, left_count, right_count,
                f"{name.replace('_', ' ').title()} summaries are malformed or unavailable.",
                ("SUMMARY_INCOMPARABLE",))
        if left.count != reference.sample_count_profiled or right.count != current.sample_count_profiled:
            return _empty(name, FeatureDriftState.INCOMPARABLE, left.count, right.count,
                f"{name.replace('_', ' ').title()} summary counts are inconsistent with profile support.",
                ("SUMMARY_COUNT_INCONSISTENT",))
        if not support:
            return FeatureDriftComparison(name, FeatureDriftState.PARTIAL,
                _direction(left.median, right.median, self.policy.epsilon), left.count, right.count,
                left, right, left.mean, right.mean, right.mean-left.mean, left.median, right.median,
                right.median-left.median, left.population_std, right.population_std, False, (), (),
                f"{name.replace('_', ' ').title()} comparison has insufficient sample support for a drift conclusion.",
                ("INSUFFICIENT_SAMPLE_SUPPORT",))
        smd, constant_mean = standardized_mean_difference(left, right, self.policy.epsilon)
        quantile, constant_quantile = robust_quantile_shift(left, right, self.policy.epsilon)
        constant_shift = constant_mean or constant_quantile
        metrics = [_metric("STANDARDIZED_MEAN_DIFFERENCE", smd,
            self.policy.standardized_mean_difference_threshold),
            _metric("ROBUST_QUANTILE_SHIFT", quantile, self.policy.robust_quantile_shift_threshold),
            _metric("SYMMETRIC_MEDIAN_CHANGE", symmetric_median_change(left, right, self.policy.epsilon),
                self.policy.symmetric_median_change_threshold)]
        limitations = ["SUMMARY_ONLY_EFFECT_SIZES_DO_NOT_ESTABLISH_STATISTICAL_SIGNIFICANCE"]
        if name in HISTOGRAM_FIELDS:
            left_hist, right_hist = getattr(reference, HISTOGRAM_FIELDS[name]), getattr(current, HISTOGRAM_FIELDS[name])
            histogram_valid = (left_hist is not None and right_hist is not None
                and len(left_hist) == len(right_hist) and len(left_hist) > 0
                and all(type(item) is int and item >= 0 for item in left_hist + right_hist)
                and sum(left_hist) == left.count and sum(right_hist) == right.count)
            if not histogram_valid:
                return FeatureDriftComparison(name, FeatureDriftState.INCOMPARABLE,
                    _direction(left.median, right.median, self.policy.epsilon), left.count, right.count,
                    left, right, left.mean, right.mean, right.mean-left.mean, left.median, right.median,
                    right.median-left.median, left.population_std, right.population_std, constant_shift,
                    tuple(metrics), tuple(sorted(item.name for item in metrics if item.exceeded)),
                    f"{name.title()} histogram support is incompatible or malformed.",
                    ("HISTOGRAM_SUPPORT_INCOMPARABLE",))
            metrics += [_metric("JENSEN_SHANNON", jensen_shannon(left_hist, right_hist),
                self.policy.jensen_shannon_threshold),
                _metric("TOTAL_VARIATION", total_variation(left_hist, right_hist),
                    self.policy.total_variation_threshold),
                _metric("HISTOGRAM_WASSERSTEIN", histogram_wasserstein(left_hist, right_hist),
                    self.policy.histogram_wasserstein_threshold)]
        exceeded = tuple(sorted(item.name for item in metrics if item.exceeded))
        state = FeatureDriftState.SHIFTED if constant_shift or len(exceeded) >= 2 else FeatureDriftState.STABLE
        label = name.replace("_", " ").lower()
        reason = (f"Current {label} distribution differs materially from the designated reference under the configured D2 effect-size policy."
                  if state == FeatureDriftState.SHIFTED else
                  f"Observed {label} statistics remain within the configured D2 comparison thresholds.")
        return FeatureDriftComparison(name, state, _direction(left.median, right.median,
            self.policy.epsilon), left.count, right.count, left, right, left.mean, right.mean,
            right.mean-left.mean, left.median, right.median, right.median-left.median,
            left.population_std, right.population_std, constant_shift, tuple(metrics), exceeded,
            reason, tuple(sorted(limitations)))

    def _categorical(self, name: str, reference_counts: tuple[tuple[str, int], ...],
                     current_counts: tuple[tuple[str, int], ...], reference_count: int,
                     current_count: int, support: bool) -> FeatureDriftComparison:
        def mapping(values):
            result = {}
            for key, value in values:
                if not isinstance(key, str) or not key or key in result or type(value) is not int or value < 0:
                    return None
                result[key] = value
            return result
        left, right = mapping(reference_counts), mapping(current_counts)
        if left is None or right is None or sum(left.values()) != reference_count or sum(right.values()) != current_count:
            return _empty(name, FeatureDriftState.INCOMPARABLE, reference_count, current_count,
                "Categorical profile counts are malformed or inconsistent.",
                ("CATEGORICAL_COUNTS_INCOMPARABLE",))
        if not support:
            return _empty(name, FeatureDriftState.PARTIAL, reference_count, current_count,
                "Categorical comparison has insufficient sample support for a drift conclusion.",
                ("INSUFFICIENT_SAMPLE_SUPPORT",))
        categories = sorted(set(left) | set(right)); p = tuple(left.get(key, 0) for key in categories)
        q = tuple(right.get(key, 0) for key in categories)
        metrics = (_metric("JENSEN_SHANNON", jensen_shannon(p, q), self.policy.jensen_shannon_threshold),
            _metric("TOTAL_VARIATION", total_variation(p, q), self.policy.total_variation_threshold))
        exceeded = tuple(sorted(item.name for item in metrics if item.exceeded))
        state = FeatureDriftState.SHIFTED if len(exceeded) == 2 else FeatureDriftState.STABLE
        subject = "image mode" if name == "IMAGE_MODE_DISTRIBUTION" else "decoded image format"
        limitations = (("ENCODING_FORMAT_SHIFT_IS_OPERATIONAL_NOT_SEMANTIC",) if
                       name == "IMAGE_FORMAT_DISTRIBUTION" else ())
        reason = (f"Observed {subject} distribution differs under the configured D2 categorical policy."
                  if state == FeatureDriftState.SHIFTED else
                  f"Observed {subject} distribution remains within the configured D2 comparison thresholds.")
        return FeatureDriftComparison(name, state, ChangeDirection.MIXED if state == FeatureDriftState.SHIFTED
            else ChangeDirection.SAME, reference_count, current_count, None, None, None, None, None,
            None, None, None, None, None, False, metrics, exceeded, reason, limitations)

    def _report(self, reference: ImageWindowProfile, current: ImageWindowProfile,
                status: ComparisonStatus, features: tuple[FeatureDriftComparison, ...],
                limitations: list[str]) -> DistributionShiftComparisonReport:
        groups = {state: tuple(item.feature for item in features if item.state == state) for state in FeatureDriftState}
        core = {"schema_version": COMPARISON_SCHEMA_VERSION,
            "reference_profile_id": reference.profile_id, "current_profile_id": current.profile_id,
            "status": status.value, "reference_sample_count": reference.sample_count_profiled,
            "current_sample_count": current.sample_count_profiled, "policy_id": self.policy.policy_id,
            "policy": self.policy.to_dict(), "feature_comparisons": [asdict(item) for item in features],
            "shifted_features": groups[FeatureDriftState.SHIFTED],
            "stable_features": groups[FeatureDriftState.STABLE],
            "partial_features": groups[FeatureDriftState.PARTIAL],
            "unavailable_features": groups[FeatureDriftState.UNAVAILABLE],
            "incomparable_features": groups[FeatureDriftState.INCOMPARABLE],
            "limitations": tuple(sorted(set(limitations)))}
        digest = sha256(canonical_json_bytes(core)).hexdigest()
        report = DistributionShiftComparisonReport(COMPARISON_SCHEMA_VERSION,
            f"drift-comparison:sha256:{digest}", reference.profile_id, current.profile_id, status,
            reference.sample_count_profiled, current.sample_count_profiled, self.policy.policy_id,
            self.policy, features, core["shifted_features"], core["stable_features"],
            core["partial_features"], core["unavailable_features"], core["incomparable_features"],
            core["limitations"])
        json.dumps(report.to_dict(), allow_nan=False); return report
