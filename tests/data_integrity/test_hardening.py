from pathlib import Path
import pytest

from modules.data_integrity.ood import load_clip_model
from modules.data_integrity.safe_images import SafeImageError, safe_image_files
from modules.data_integrity.analyzer import analyze_dataset
from modules.data_integrity.duplicates import create_duplicate_findings
from modules.data_integrity.label_anomaly import create_label_anomaly_findings
from modules.data_integrity.ood import create_ood_findings
from modules.data_integrity.phash import create_near_duplicate_findings


def test_clip_loading_is_strictly_local(monkeypatch):
    calls = []
    class Processor:
        @classmethod
        def from_pretrained(cls, *args, **kwargs): calls.append(kwargs); return object()
    class Model:
        @classmethod
        def from_pretrained(cls, *args, **kwargs): calls.append(kwargs); return cls()
        def eval(self): pass
    monkeypatch.setattr("modules.data_integrity.ood.CLIPProcessor", Processor)
    monkeypatch.setattr("modules.data_integrity.ood.CLIPModel", Model)
    load_clip_model("local-model")
    assert calls == [{"local_files_only": True}, {"local_files_only": True}]


def test_safe_image_order_limit_and_size(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.jpg").write_bytes(b"bb")
    (tmp_path / "a.png").write_bytes(b"a")
    (tmp_path / "notes.txt").write_text("x")
    assert [p.relative_to(tmp_path).as_posix() for p in safe_image_files(tmp_path, max_images=1)] == ["a.png"]
    with pytest.raises(SafeImageError, match="file-size"):
        safe_image_files(tmp_path, max_file_size=1)


def test_safe_image_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside.jpg"
    outside.write_bytes(b"x")
    link = tmp_path / "escape.jpg"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(SafeImageError, match="Symlinked"):
        safe_image_files(tmp_path)


def test_public_finding_ids_are_stable_when_input_order_changes():
    duplicate = {"hash": ["b.jpg", "a.jpg"]}
    assert create_duplicate_findings(duplicate)[0]["finding_id"] == create_duplicate_findings({"hash": ["a.jpg", "b.jpg"]})[0]["finding_id"]
    matches = [{"image_a": "a.jpg", "image_b": "b.jpg", "phash_distance": 3}, {"image_a": "b.jpg", "image_b": "c.jpg", "phash_distance": 4}]
    assert [item["finding_id"] for item in create_near_duplicate_findings(matches)] == [item["finding_id"] for item in create_near_duplicate_findings(list(reversed(matches)))]
    anomalies = [{"label": "z", "count": 1}, {"label": "a", "count": 1}]
    assert {item["finding_id"] for item in create_label_anomaly_findings(anomalies)} == {item["finding_id"] for item in create_label_anomaly_findings(list(reversed(anomalies)))}
    outliers = [{"image": "b.jpg", "ood_score": 0.4}, {"image": "a.jpg", "ood_score": 0.5}]
    assert {item["finding_id"] for item in create_ood_findings(outliers)} == {item["finding_id"] for item in create_ood_findings(list(reversed(outliers)))}


def test_finding_evidence_bounds_are_explicit():
    finding = create_duplicate_findings(
        {"hash": [f"{index}-" + "x" * 40 for index in range(4)]},
        max_duplicate_paths=2,
        max_evidence_length=30,
    )[0]
    assert finding["truncated"] is True
    assert any("truncated" in item for item in finding["evidence"])
    assert all(len(item) <= 30 for item in finding["evidence"])


def test_analyzer_reports_exact_duplicate_suppression_and_pair_limit(monkeypatch, tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (images / name).write_bytes(b"image")
    monkeypatch.setattr("modules.data_integrity.analyzer.find_exact_duplicates", lambda *_, **__: {"hash": ["a.jpg", "b.jpg"]})
    monkeypatch.setattr("modules.data_integrity.analyzer.find_near_duplicates", lambda *_, **__: {"matches": [{"image_a": "b.jpg", "image_b": "a.jpg", "phash_distance": 0}], "partial": True, "reason": "max_pair_comparisons reached."})
    monkeypatch.setattr("modules.data_integrity.analyzer.analyze_ood", lambda *_, **__: {"outliers": [], "calibration": {}})
    result = analyze_dataset(str(tmp_path), run_labels=False, max_pair_comparisons=0)
    assert result["findings_by_category"] == {"EXACT_DUPLICATE": 1}
    assert result["partial"] is True
    assert result["resource_limits_reached"]["phash"] == "max_pair_comparisons reached."


def test_analyzer_marks_small_ood_calibration_partial(monkeypatch, tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "only.jpg").write_bytes(b"image")
    monkeypatch.setattr("modules.data_integrity.analyzer.find_exact_duplicates", lambda *_, **__: {})
    monkeypatch.setattr("modules.data_integrity.analyzer.find_near_duplicates", lambda *_, **__: [])
    monkeypatch.setattr("modules.data_integrity.analyzer.analyze_ood", lambda *_, **__: {"outliers": [], "calibration": {"method": "insufficient_samples", "reason": "too few", "threshold": None}})
    result = analyze_dataset(str(tmp_path), run_labels=False)
    assert result["partial"] is True
    assert result["ood_calibration"]["threshold"] is None
    assert result["resource_limits_reached"]["ood_calibration"] == "too few"


def test_dota128_analysis_skips_unsupported_label_metadata(monkeypatch):
    dataset = Path("datasets/dota128")
    if not dataset.is_dir():
        pytest.skip("DOTA128 dataset is unavailable")
    monkeypatch.setattr("modules.data_integrity.analyzer.find_exact_duplicates", lambda *_, **__: {})
    monkeypatch.setattr("modules.data_integrity.analyzer.find_near_duplicates", lambda *_, **__: [])
    monkeypatch.setattr("modules.data_integrity.analyzer.analyze_ood", lambda *_, **__: {"outliers": [], "calibration": {"method": "median_mad", "sample_count": 128, "threshold": 0.2}})
    result = analyze_dataset(str(dataset), run_duplicates=False, run_phash=False)
    assert result["image_count"] == 128
    assert "labels" in result["skipped_detectors"]
    assert result["detector_errors"] == {}
