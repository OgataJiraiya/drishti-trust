from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from modules.distribution_shift.image_features import SampleProcessingError, extract_image_features
from modules.distribution_shift.intake import enumerate_image_window, streaming_sha256
from modules.distribution_shift.models import FailureCode, ProfileLimits, ProfileStatus, WindowRole
from modules.distribution_shift.profiling import ImageWindowProfiler


def save(path: Path, value, *, mode="RGB", format="PNG") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(value, dtype=np.uint8), mode=mode).save(path, format=format)
    return path


def solid(path: Path, level: int, *, size=(8, 8), mode="RGB", format="PNG") -> Path:
    if mode == "L": array = np.full((size[1], size[0]), level, np.uint8)
    elif mode == "RGBA": array = np.full((size[1], size[0], 4), [level, level, level, 17], np.uint8)
    else: array = np.full((size[1], size[0], 3), level, np.uint8)
    return save(path, array, mode=mode, format=format)


def test_missing_and_non_directory_roots_rejected(tmp_path):
    with pytest.raises(FileNotFoundError): ImageWindowProfiler().profile_directory(tmp_path / "missing")
    file = tmp_path / "file"; file.write_bytes(b"x")
    with pytest.raises(NotADirectoryError): ImageWindowProfiler().profile_directory(file)


def test_supported_discovery_unsupported_and_hidden_ignored(tmp_path):
    solid(tmp_path / "b.png", 2); solid(tmp_path / "a.jpg", 1, format="JPEG")
    (tmp_path / "notes.txt").write_text("not image"); solid(tmp_path / ".hidden.png", 3)
    window = enumerate_image_window(tmp_path, ProfileLimits())
    assert [item.name for item in window.files] == ["a.jpg", "b.png"]


def test_streaming_hash_and_duplicate_content_identity(tmp_path):
    first = solid(tmp_path / "one.png", 42); second = tmp_path / "renamed.png"
    second.write_bytes(first.read_bytes())
    expected = sha256(first.read_bytes()).hexdigest()
    assert streaming_sha256(first, 3) == streaming_sha256(second, 5) == expected
    assert extract_image_features(first, ProfileLimits()).content_sha256 == expected


def test_symlink_file_and_directory_never_followed(tmp_path):
    outside = tmp_path / "outside"; outside.mkdir(); solid(outside / "secret.png", 1)
    root = tmp_path / "window"; root.mkdir()
    (root / "escape.png").symlink_to(outside / "secret.png")
    (root / "escape-dir").symlink_to(outside, target_is_directory=True)
    profile = ImageWindowProfiler().profile_directory(root)
    assert profile.status == ProfileStatus.UNAVAILABLE and profile.sample_count_profiled == 0
    assert dict(profile.failure_counts)["SYMLINK_REJECTED"] == 2
    assert "secret.png" not in json.dumps(profile.to_dict())


def test_file_size_checked_before_decoder(tmp_path, monkeypatch):
    path = tmp_path / "large.png"; path.write_bytes(b"x" * 20)
    monkeypatch.setattr("PIL.Image.open", lambda *args: (_ for _ in ()).throw(AssertionError("decoded")))
    profile = ImageWindowProfiler(ProfileLimits(max_file_bytes=10)).profile_directory(tmp_path)
    assert dict(profile.failure_counts) == {"RESOURCE_LIMIT": 1}


def test_pixel_limit_before_conversion(tmp_path, monkeypatch):
    solid(tmp_path / "wide.png", 1, size=(5, 4))
    original = Image.Image.convert
    monkeypatch.setattr(Image.Image, "convert", lambda *args, **kwargs:
        (_ for _ in ()).throw(AssertionError("converted")))
    profile = ImageWindowProfiler(ProfileLimits(max_pixels_per_image=19)).profile_directory(tmp_path)
    monkeypatch.setattr(Image.Image, "convert", original)
    assert dict(profile.failure_counts) == {"RESOURCE_LIMIT": 1}


@pytest.mark.parametrize("payload", [b"not-jpeg", b"", b"\x89PNG\r\n\x1a\nfalse"])
def test_corrupt_and_spoofed_images_are_bounded(tmp_path, payload):
    (tmp_path / "fake.jpg").write_bytes(payload)
    profile = ImageWindowProfiler().profile_directory(tmp_path)
    assert profile.status == ProfileStatus.UNAVAILABLE
    assert dict(profile.failure_counts) == {"INVALID_IMAGE": 1}
    assert "/tmp/" not in json.dumps(profile.to_dict())


def test_empty_complete_partial_and_all_corrupt_statuses(tmp_path):
    empty = ImageWindowProfiler().profile_directory(tmp_path)
    assert empty.status == ProfileStatus.UNAVAILABLE and empty.brightness_summary is None
    solid(tmp_path / "valid.png", 10)
    complete = ImageWindowProfiler().profile_directory(tmp_path)
    assert complete.status == ProfileStatus.COMPLETE and complete.sample_count_profiled == 1
    (tmp_path / "bad.jpg").write_bytes(b"bad")
    partial = ImageWindowProfiler().profile_directory(tmp_path)
    assert partial.status == ProfileStatus.PARTIAL and partial.sample_count_failed == 1
    (tmp_path / "valid.png").unlink()
    assert ImageWindowProfiler().profile_directory(tmp_path).status == ProfileStatus.UNAVAILABLE


@pytest.mark.parametrize("count,expected", [(2, "COMPLETE"), (3, "COMPLETE"), (4, "PARTIAL")])
def test_max_sample_n_boundaries_and_counts(tmp_path, count, expected):
    for index in range(count): solid(tmp_path / f"{index}.png", index)
    profile = ImageWindowProfiler(ProfileLimits(max_samples=3)).profile_directory(tmp_path)
    assert profile.status.value == expected
    assert profile.sample_count_discovered == count
    assert profile.sample_count_selected == min(3, count)
    assert profile.sample_count_profiled == min(3, count)
    assert profile.sample_count_skipped == max(0, count - 3)


def test_truncation_is_deterministic(tmp_path):
    for name, level in (("z.png", 2), ("a.png", 1), ("m.png", 3)): solid(tmp_path / name, level)
    profiler = ImageWindowProfiler(ProfileLimits(max_samples=2))
    assert profiler.profile_directory(tmp_path) == profiler.profile_directory(tmp_path)


def test_failure_cap_preserves_totals(tmp_path):
    for index in range(7): (tmp_path / f"bad-{index}.jpg").write_bytes(b"bad")
    profile = ImageWindowProfiler(ProfileLimits(max_failures_reported=2)).profile_directory(tmp_path)
    assert profile.sample_count_failed == 7 and dict(profile.failure_counts)["INVALID_IMAGE"] == 7
    assert len(profile.failures) == 2 and "FAILURE_EXAMPLES_TRUNCATED" in profile.limitations


def test_feature_relative_behavior_and_numeric_safety(tmp_path):
    black = solid(tmp_path / "black.png", 0); gray = solid(tmp_path / "gray.png", 127)
    white = solid(tmp_path / "white.png", 255); flat = extract_image_features(gray, ProfileLimits())
    checker = np.indices((8, 8)).sum(axis=0) % 2 * 255
    checker_path = save(tmp_path / "checker.png", np.repeat(checker[..., None], 3, axis=2))
    varied = extract_image_features(checker_path, ProfileLimits())
    brightness = [extract_image_features(item, ProfileLimits()).brightness for item in (black, gray, white)]
    assert brightness[0] < brightness[1] < brightness[2]
    assert flat.contrast == pytest.approx(0) and flat.sharpness < varied.sharpness
    assert flat.entropy < varied.entropy and all(np.isfinite(value) for value in brightness)


def test_grayscale_rgb_rgba_saturation_and_alpha(tmp_path):
    gray = solid(tmp_path / "gray.png", 50, mode="L")
    red = save(tmp_path / "red.png", np.full((3, 3, 3), [255, 0, 0], np.uint8))
    rgba = solid(tmp_path / "rgba.png", 50, mode="RGBA")
    assert extract_image_features(gray, ProfileLimits()).saturation == 0
    assert extract_image_features(red, ProfileLimits()).saturation == 1
    assert extract_image_features(rgba, ProfileLimits()).brightness == pytest.approx(
        extract_image_features(gray, ProfileLimits()).brightness)
    profile = ImageWindowProfiler().profile_directory(tmp_path)
    assert dict(profile.mode_counts) == {"GRAYSCALE": 1, "RGB": 1, "RGBA": 1}


def test_dimensions_summaries_quantiles_and_population_std(tmp_path):
    solid(tmp_path / "a.png", 1, size=(1, 1)); solid(tmp_path / "b.png", 2, size=(3, 2))
    profile = ImageWindowProfiler().profile_directory(tmp_path)
    width = profile.width_summary
    assert (width.minimum, width.maximum, width.mean, width.median) == (1, 3, 2, 2)
    assert width.population_std == 1 and width.q25 == 1.5 and width.q75 == 2.5
    assert profile.height_summary.mean == 1.5
    assert profile.pixel_count_summary.maximum == 6
    assert profile.aspect_ratio_summary.minimum == 1 and profile.aspect_ratio_summary.maximum == 1.5


def test_detected_format_not_suffix(tmp_path):
    solid(tmp_path / "actually-jpeg.png", 2, format="JPEG")
    profile = ImageWindowProfiler().profile_directory(tmp_path)
    assert dict(profile.format_counts) == {"JPEG": 1}


def test_multiframe_rejected(tmp_path):
    frames = [Image.new("RGB", (2, 2), color) for color in ("black", "white")]
    frames[0].save(tmp_path / "animated.webp", save_all=True, append_images=frames[1:], duration=1)
    profile = ImageWindowProfiler().profile_directory(tmp_path)
    assert dict(profile.failure_counts) == {"UNSUPPORTED_MULTIFRAME": 1}


def test_one_pixel_and_extreme_aspect_are_safe(tmp_path):
    solid(tmp_path / "tiny.png", 9, size=(1, 1)); solid(tmp_path / "wide.png", 9, size=(100, 1))
    profile = ImageWindowProfiler(ProfileLimits(max_pixels_per_image=100)).profile_directory(tmp_path)
    assert profile.status == ProfileStatus.COMPLETE
    assert profile.sharpness_summary.minimum == 0 and profile.aspect_ratio_summary.maximum == 100


def test_profile_determinism_rename_role_and_duplicate_multiplicity(tmp_path):
    first = solid(tmp_path / "first.png", 55); copy = tmp_path / "copy.png"; copy.write_bytes(first.read_bytes())
    profiler = ImageWindowProfiler(); current = profiler.profile_current(tmp_path)
    repeated = profiler.profile_current(tmp_path); reference = profiler.profile_reference(tmp_path)
    assert current == repeated and current.profile_id == reference.profile_id
    assert current.sample_count_profiled == 2
    first.rename(tmp_path / "renamed-unicode-नमस्ते.png")
    renamed = profiler.profile_current(tmp_path)
    assert renamed.profile_id == current.profile_id and renamed.sample_set_commitment == current.sample_set_commitment
    assert renamed.role == WindowRole.CURRENT and reference.role == WindowRole.REFERENCE


def test_profile_id_limits_and_strict_json(tmp_path):
    solid(tmp_path / "image.png", 1)
    first = ImageWindowProfiler(ProfileLimits(histogram_bins=8)).profile_directory(tmp_path)
    second = ImageWindowProfiler(ProfileLimits(histogram_bins=16)).profile_directory(tmp_path)
    assert first.profile_id.startswith("drift-profile:sha256:") and len(first.profile_id) == 85
    assert first.profile_id != second.profile_id
    json.dumps(first.to_dict(), allow_nan=False)


def test_no_private_or_raw_metadata_serialized(tmp_path):
    image = Image.new("RGB", (2, 2), "red")
    exif = Image.Exif(); exif[0x010E] = "private author"; exif[0x0132] = "secret timestamp"
    image.save(tmp_path / "exif.jpg", exif=exif)
    encoded = json.dumps(ImageWindowProfiler().profile_directory(tmp_path).to_dict())
    for forbidden in (str(tmp_path), "private author", "secret timestamp", "pixel array", "raw image"):
        assert forbidden not in encoded


def test_limits_validation_and_immutability():
    for field in ProfileLimits.__dataclass_fields__:
        with pytest.raises(ValueError): ProfileLimits(**{field: 0})
    limits = ProfileLimits()
    with pytest.raises(FrozenInstanceError): limits.max_samples = 2


def test_role_validation_does_not_mutate_config(tmp_path):
    limits = ProfileLimits(max_samples=2); before = limits.identity_dict()
    ImageWindowProfiler(limits).profile_directory(tmp_path)
    assert limits.identity_dict() == before
    with pytest.raises(ValueError): ImageWindowProfiler(limits).profile_directory(tmp_path, role="REFERENCE")


def test_public_schema_has_no_drift_or_trust_decision_fields(tmp_path):
    fields = ImageWindowProfiler().profile_directory(tmp_path).to_dict()
    prohibited = {"drift_score", "risk_score", "trust_score", "severity", "disposition", "findings"}
    assert prohibited.isdisjoint(fields)


def test_decoder_warning_becomes_explicit_sample_failure(tmp_path, monkeypatch):
    import warnings
    from PIL import Image
    from modules.distribution_shift.profiling import ImageWindowProfiler
    path = tmp_path / 'image.png'
    Image.new('RGB', (2, 2)).save(path)
    def warned(*args, **kwargs):
        warnings.warn('decoder resource warning', Image.DecompressionBombWarning)
    monkeypatch.setattr(Image, 'open', warned)
    report = ImageWindowProfiler().profile_current(tmp_path)
    assert report.status.value == 'UNAVAILABLE'
    assert report.sample_count_failed == 1
    assert report.sample_count_profiled == 0
