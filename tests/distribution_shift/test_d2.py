from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
from math import inf, nan
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from modules.distribution_shift import (ChangeDirection, ComparisonStatus,
    DistributionComparisonPolicy, DistributionShiftComparator, FeatureDriftState,
    ImageWindowProfiler, ProfileLimits, ProfileStatus, WindowRole)
from modules.distribution_shift.comparison import FEATURE_ORDER, _metric
from modules.distribution_shift.models import FeatureSummary
from modules.distribution_shift.statistics import (histogram_wasserstein, jensen_shannon,
    robust_quantile_shift, standardized_mean_difference, symmetric_median_change,
    total_variation)


TEST_POLICY = DistributionComparisonPolicy(min_reference_samples=2, min_current_samples=2)


def save_solid(path: Path, value, *, size=(8, 8), mode="RGB", format="PNG"):
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode == "L": array = np.full((size[1], size[0]), value, np.uint8)
    else: array = np.full((size[1], size[0], 3), value, np.uint8)
    Image.fromarray(array, mode=mode).save(path, format=format)


def save_array(path: Path, array, *, mode="RGB", format="PNG"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(array, np.uint8), mode=mode).save(path, format=format)


def windows(tmp_path, reference_writer, current_writer, count=3, limits=None):
    reference_dir, current_dir = tmp_path / "reference", tmp_path / "current"
    for index in range(count):
        reference_writer(reference_dir / f"{index}.png", index)
        current_writer(current_dir / f"{index}.png", index)
    profiler = ImageWindowProfiler(limits)
    return profiler.profile_reference(reference_dir), profiler.profile_current(current_dir)


def feature(report, name): return next(item for item in report.feature_comparisons if item.feature == name)


def identical_profiles(tmp_path, count=3):
    return windows(tmp_path, lambda path, index: save_solid(path, 40+index),
                   lambda path, index: save_solid(path, 40+index), count)


def summary(mean=1.0, std=1.0, median=None, spread=1.0):
    median = mean if median is None else median
    return FeatureSummary(20, mean-spread, mean+spread, mean, std, median,
                          mean-spread*.9, mean-spread*.5, mean+spread*.5, mean+spread*.9)


def test_branch_and_frozen_base_documented():
    text = Path("docs/DISTRIBUTION_SHIFT.md").read_text()
    assert "feat/distribution-statistical-drift" in text and "44ae21f" in text


def test_roles_accepted_wrong_roles_rejected(tmp_path):
    reference, current = identical_profiles(tmp_path)
    assert DistributionShiftComparator(TEST_POLICY).compare(reference, current)
    with pytest.raises(ValueError, match="REFERENCE"):
        DistributionShiftComparator(TEST_POLICY).compare(replace(reference, role=WindowRole.CURRENT), current)
    with pytest.raises(ValueError, match="CURRENT"):
        DistributionShiftComparator(TEST_POLICY).compare(reference, replace(current, role=WindowRole.REFERENCE))


def test_identical_is_complete_stable_deterministic_and_immutable(tmp_path):
    reference, current = identical_profiles(tmp_path); before = (reference, current, TEST_POLICY)
    comparator = DistributionShiftComparator(TEST_POLICY)
    first = comparator.compare(reference, current); second = comparator.compare(reference, current)
    assert first == second and first.status == ComparisonStatus.COMPLETE
    assert tuple(item.feature for item in first.feature_comparisons) == FEATURE_ORDER
    assert all(item.state == FeatureDriftState.STABLE for item in first.feature_comparisons)
    assert before == (reference, current, TEST_POLICY)
    assert first.comparison_id.startswith("drift-comparison:sha256:") and len(first.comparison_id) == 88


def test_insufficient_support_never_stable_or_shifted(tmp_path):
    reference, current = identical_profiles(tmp_path, 1)
    report = DistributionShiftComparator().compare(reference, current)
    assert report.status == ComparisonStatus.PARTIAL
    assert not report.stable_features and not report.shifted_features
    assert set(report.partial_features) == set(FEATURE_ORDER)
    assert "INSUFFICIENT_SAMPLE_SUPPORT" in report.limitations


@pytest.mark.parametrize("side", ["reference", "current"])
def test_partial_profile_propagates_partial(tmp_path, side):
    reference, current = identical_profiles(tmp_path)
    target = reference if side == "reference" else current
    changed = replace(target, status=ProfileStatus.PARTIAL)
    report = DistributionShiftComparator(TEST_POLICY).compare(
        changed if side == "reference" else reference,
        changed if side == "current" else current)
    assert report.status == ComparisonStatus.PARTIAL
    assert "UPSTREAM_D1_PROFILE_PARTIAL" in report.limitations


@pytest.mark.parametrize("side", ["reference", "current"])
def test_unavailable_profile_produces_no_conclusion(tmp_path, side):
    reference, current = identical_profiles(tmp_path)
    target = replace(reference if side == "reference" else current, status=ProfileStatus.UNAVAILABLE)
    report = DistributionShiftComparator(TEST_POLICY).compare(
        target if side == "reference" else reference, target if side == "current" else current)
    assert report.status == ComparisonStatus.UNAVAILABLE
    assert set(report.unavailable_features) == set(FEATURE_ORDER) and not report.shifted_features


def test_schema_incompatibility_is_conservative(tmp_path):
    reference, current = identical_profiles(tmp_path)
    report = DistributionShiftComparator(TEST_POLICY).compare(replace(reference, schema_version=99), current)
    assert report.status == ComparisonStatus.INCOMPARABLE
    assert set(report.incomparable_features) == set(FEATURE_ORDER)


@pytest.mark.parametrize("function", [jensen_shannon, total_variation, histogram_wasserstein])
def test_histogram_metrics_identical_symmetric_finite(function):
    assert function((1, 2, 3), (1, 2, 3)) == 0
    assert function((1, 2, 3), (3, 2, 1)) == function((3, 2, 1), (1, 2, 3))
    assert np.isfinite(function((1, 0, 2), (0, 2, 1)))


def test_js_and_tv_disjoint_bounds():
    assert jensen_shannon((1, 0), (0, 1)) == 1
    assert total_variation((1, 0), (0, 1)) == 1


def test_wasserstein_displacement_monotonic():
    near = histogram_wasserstein((1, 0, 0, 0), (0, 1, 0, 0))
    far = histogram_wasserstein((1, 0, 0, 0), (0, 0, 0, 1))
    assert 0 < near <= far <= 1


def test_histogram_invalid_negative_zero_and_length():
    for values in (((0, 0), (0, 0)), ((1, -1), (1, 0)), ((1,), (1, 0))):
        assert jensen_shannon(*values) is None
        assert total_variation(*values) is None
        assert histogram_wasserstein(*values) is None


def test_smd_ordinary_equal_and_constant_cases():
    value, constant = standardized_mean_difference(summary(1, 1), summary(2, 1), 1e-12)
    assert value == 1 and not constant
    assert standardized_mean_difference(summary(1, 0), summary(1, 0), 1e-12) == (0, False)
    assert standardized_mean_difference(summary(1, 0), summary(2, 0), 1e-12) == (None, True)


def test_quantile_shift_equal_shifted_and_constant_cases():
    assert robust_quantile_shift(summary(1), summary(1), 1e-12) == (0, False)
    value, constant = robust_quantile_shift(summary(1), summary(2), 1e-12)
    assert value > 0 and not constant
    constant_one = summary(1, 0, spread=0); constant_two = summary(2, 0, spread=0)
    assert robust_quantile_shift(constant_one, constant_one, 1e-12) == (0, False)
    assert robust_quantile_shift(constant_one, constant_two, 1e-12) == (None, True)


def test_symmetric_median_safe_symmetric_and_bounded():
    zero = summary(0, median=0); one = summary(1, median=1)
    assert symmetric_median_change(zero, zero, 1e-12) == 0
    assert symmetric_median_change(zero, one, 1e-12) == symmetric_median_change(one, zero, 1e-12)
    assert 0 < symmetric_median_change(zero, one, 1e-12) <= 2


@pytest.mark.parametrize("field,value", [
    ("min_reference_samples", 0), ("min_current_samples", -1),
    ("standardized_mean_difference_threshold", 0),
    ("robust_quantile_shift_threshold", -1), ("epsilon", 0),
    ("jensen_shannon_threshold", 1.1), ("total_variation_threshold", nan),
    ("histogram_wasserstein_threshold", inf), ("symmetric_median_change_threshold", 2.1)])
def test_policy_validation(field, value):
    with pytest.raises(ValueError): DistributionComparisonPolicy(**{field: value})


def test_threshold_boundary_uses_greater_than_or_equal():
    assert _metric("X", .499999, .5).exceeded is False
    assert _metric("X", .5, .5).exceeded is True
    assert _metric("X", .500001, .5).exceeded is True


def test_policy_identity_and_immutability():
    first = DistributionComparisonPolicy(); second = DistributionComparisonPolicy()
    changed = DistributionComparisonPolicy(jensen_shannon_threshold=.2)
    assert first.policy_id == second.policy_id != changed.policy_id
    assert first.policy_id.startswith("drift-policy:sha256:") and len(first.policy_id) == 84
    with pytest.raises(FrozenInstanceError): first.epsilon = 1


def test_policy_change_changes_comparison_identity(tmp_path):
    reference, current = identical_profiles(tmp_path)
    first = DistributionShiftComparator(TEST_POLICY).compare(reference, current)
    second = DistributionShiftComparator(replace(TEST_POLICY, jensen_shannon_threshold=.2)).compare(reference, current)
    assert first.comparison_id != second.comparison_id


def test_proportional_resolution_shift_separates_aspect_ratio(tmp_path):
    reference, current = windows(tmp_path,
        lambda path, _: save_solid(path, 40, size=(32, 24)),
        lambda path, _: save_solid(path, 40, size=(64, 48)))
    report = DistributionShiftComparator(TEST_POLICY).compare(reference, current)
    for name in ("WIDTH", "HEIGHT", "PIXEL_COUNT"):
        assert feature(report, name).state == FeatureDriftState.SHIFTED
        assert feature(report, name).direction == ChangeDirection.INCREASED
    assert feature(report, "ASPECT_RATIO").state == FeatureDriftState.STABLE


def test_aspect_ratio_shift(tmp_path):
    reference, current = windows(tmp_path,
        lambda path, _: save_solid(path, 40, size=(32, 24)),
        lambda path, _: save_solid(path, 40, size=(32, 18)))
    assert feature(DistributionShiftComparator(TEST_POLICY).compare(reference, current),
                   "ASPECT_RATIO").state == FeatureDriftState.SHIFTED


def test_brightness_shift_and_direction(tmp_path):
    reference, current = windows(tmp_path, lambda path, i: save_solid(path, 30+i),
        lambda path, i: save_solid(path, 220+i))
    comparator = DistributionShiftComparator(TEST_POLICY); report = comparator.compare(reference, current)
    brightness = feature(report, "BRIGHTNESS")
    assert brightness.state == FeatureDriftState.SHIFTED and brightness.direction == ChangeDirection.INCREASED
    reverse = comparator.compare(replace(current, role=WindowRole.REFERENCE),
                                 replace(reference, role=WindowRole.CURRENT))
    reversed_brightness = feature(reverse, "BRIGHTNESS")
    assert reversed_brightness.direction == ChangeDirection.DECREASED
    assert [item.value for item in brightness.metrics] == [item.value for item in reversed_brightness.metrics]
    assert report.comparison_id != reverse.comparison_id


def quality_windows(tmp_path):
    def flat(path, index): save_solid(path, 127+index)
    def checker(path, index):
        grid = (np.indices((16, 16)).sum(axis=0) + index) % 2 * 255
        save_array(path, np.repeat(grid[..., None], 3, axis=2))
    return windows(tmp_path, flat, checker)


def test_contrast_sharpness_and_entropy_shift(tmp_path):
    reference, current = quality_windows(tmp_path)
    report = DistributionShiftComparator(TEST_POLICY).compare(reference, current)
    for name in ("CONTRAST", "SHARPNESS", "ENTROPY"):
        assert feature(report, name).state == FeatureDriftState.SHIFTED


def test_saturation_shift(tmp_path):
    def gray(path, index): save_solid(path, 80+index)
    def red(path, index): save_array(path, np.full((8, 8, 3), [200+index, 0, 0], np.uint8))
    reference, current = windows(tmp_path, gray, red)
    saturation = feature(DistributionShiftComparator(TEST_POLICY).compare(reference, current), "SATURATION")
    assert saturation.state == FeatureDriftState.SHIFTED


def test_mode_distribution_shift(tmp_path):
    reference, current = windows(tmp_path,
        lambda path, i: save_solid(path, 50+i, mode="L"),
        lambda path, i: save_solid(path, 50+i, mode="RGB"))
    assert feature(DistributionShiftComparator(TEST_POLICY).compare(reference, current),
                   "IMAGE_MODE_DISTRIBUTION").state == FeatureDriftState.SHIFTED


def test_format_distribution_shift_and_limitation(tmp_path):
    reference_dir, current_dir = tmp_path / "reference", tmp_path / "current"
    for index in range(3):
        save_solid(reference_dir / f"{index}.png", 50+index, format="PNG")
        save_solid(current_dir / f"{index}.png", 50+index, format="JPEG")
    profiler = ImageWindowProfiler(); report = DistributionShiftComparator(TEST_POLICY).compare(
        profiler.profile_reference(reference_dir), profiler.profile_current(current_dir))
    result = feature(report, "IMAGE_FORMAT_DISTRIBUTION")
    assert result.state == FeatureDriftState.SHIFTED
    assert "ENCODING_FORMAT_SHIFT_IS_OPERATIONAL_NOT_SEMANTIC" in result.limitations


def test_rename_only_does_not_shift(tmp_path):
    reference_dir, current_dir = tmp_path / "reference", tmp_path / "current"
    for index in range(3):
        save_solid(reference_dir / f"old-{index}.png", 70+index)
        save_solid(current_dir / f"renamed-नमस्ते-{index}.png", 70+index)
    profiler = ImageWindowProfiler(); report = DistributionShiftComparator(TEST_POLICY).compare(
        profiler.profile_reference(reference_dir), profiler.profile_current(current_dir))
    assert not report.shifted_features


def test_histogram_bin_incompatibility_only_degrades_backed_features(tmp_path):
    reference, _ = identical_profiles(tmp_path / "a")
    _, current = identical_profiles(tmp_path / "b")
    current = replace(current, brightness_histogram=current.brightness_histogram[:128],
                      saturation_histogram=current.saturation_histogram[:128])
    report = DistributionShiftComparator(TEST_POLICY).compare(reference, current)
    assert report.status == ComparisonStatus.PARTIAL
    assert feature(report, "BRIGHTNESS").state == FeatureDriftState.INCOMPARABLE
    assert feature(report, "SATURATION").state == FeatureDriftState.INCOMPARABLE
    assert feature(report, "WIDTH").state == FeatureDriftState.STABLE


@pytest.mark.parametrize("mutation", [
    lambda profile: replace(profile, brightness_histogram=(-1,) + profile.brightness_histogram[1:]),
    lambda profile: replace(profile, brightness_histogram=(0,) * len(profile.brightness_histogram)),
    lambda profile: replace(profile, brightness_summary=replace(profile.brightness_summary, count=999)),
    lambda profile: replace(profile, width_summary=replace(profile.width_summary, q25=999))])
def test_malformed_profile_evidence_is_incomparable(tmp_path, mutation):
    reference, current = identical_profiles(tmp_path)
    report = DistributionShiftComparator(TEST_POLICY).compare(reference, mutation(current))
    assert report.status == ComparisonStatus.PARTIAL
    assert report.incomparable_features
    json.dumps(report.to_dict(), allow_nan=False)


def test_categorical_alignment_missing_category_is_zero(tmp_path):
    reference, current = identical_profiles(tmp_path)
    reference = replace(reference, mode_counts=(("GRAYSCALE", 1), ("RGB", 2)))
    current = replace(current, mode_counts=(("RGB", 3),))
    result = feature(DistributionShiftComparator(TEST_POLICY).compare(reference, current),
                     "IMAGE_MODE_DISTRIBUTION")
    assert result.state in {FeatureDriftState.STABLE, FeatureDriftState.SHIFTED}
    assert all(item.value is not None for item in result.metrics)


def test_tiny_float_difference_stable(tmp_path):
    reference, current = identical_profiles(tmp_path)
    source = current.width_summary; delta = 1e-13
    changed = replace(current, width_summary=replace(source,
        minimum=source.minimum+delta, maximum=source.maximum+delta, mean=source.mean+delta,
        median=source.median+delta, q05=source.q05+delta, q25=source.q25+delta,
        q75=source.q75+delta, q95=source.q95+delta))
    result = feature(DistributionShiftComparator(TEST_POLICY).compare(reference, changed), "WIDTH")
    assert result.state == FeatureDriftState.STABLE and result.direction == ChangeDirection.SAME


def test_reports_strict_json_and_contain_no_raw_or_global_fields(tmp_path):
    reference, current = identical_profiles(tmp_path)
    reports = [DistributionShiftComparator(TEST_POLICY).compare(reference, current),
        DistributionShiftComparator().compare(reference, current),
        DistributionShiftComparator(TEST_POLICY).compare(replace(reference, status=ProfileStatus.PARTIAL), current),
        DistributionShiftComparator(TEST_POLICY).compare(replace(reference, status=ProfileStatus.UNAVAILABLE), current),
        DistributionShiftComparator(TEST_POLICY).compare(replace(reference, schema_version=99), current)]
    for report in reports:
        encoded = json.dumps(report.to_dict(), allow_nan=False)
        for forbidden in ("/home/", "/tmp/", "raw image", "pixel array", "exif", "embedding",
                          "prediction", "drift_score", "risk_score", "trust_score", "severity", "disposition"):
            assert forbidden not in encoded.lower()


def test_comparator_is_pure_and_does_not_call_d1_or_decoder(tmp_path, monkeypatch):
    reference, current = identical_profiles(tmp_path)
    monkeypatch.setattr("modules.distribution_shift.profiling.ImageWindowProfiler.profile_directory",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("D1 called")))
    monkeypatch.setattr("PIL.Image.open", lambda *args, **kwargs:
        (_ for _ in ()).throw(AssertionError("decoder called")))
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs:
        (_ for _ in ()).throw(AssertionError("file read")))
    assert DistributionShiftComparator(TEST_POLICY).compare(reference, current)
