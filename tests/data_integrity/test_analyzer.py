from pathlib import Path

import pytest

from modules.data_integrity.analyzer import analyze_dataset


def _dataset(tmp_path: Path, with_labels: bool = True) -> Path:
    images = tmp_path / "images" / "train"
    images.mkdir(parents=True)
    (images / "one.jpg").touch()
    (images / "two.jpg").touch()
    if with_labels:
        (tmp_path / "labels" / "train").mkdir(parents=True)
        (tmp_path / "classes.txt").write_text("plane\nship\n")
    return tmp_path


def test_unified_analysis_runs_all_detectors(monkeypatch, tmp_path):
    dataset = _dataset(tmp_path)
    samples = [{"sample_id": "one.jpg", "labels": ["plane"], "contributor_id": None}]
    monkeypatch.setattr("modules.data_integrity.analyzer.load_yolo", lambda *_: samples)
    monkeypatch.setattr("modules.data_integrity.analyzer.find_exact_duplicates", lambda _: {"hash": ["one.jpg", "two.jpg"]})
    monkeypatch.setattr("modules.data_integrity.analyzer.find_near_duplicates", lambda *_: [{"image_a": "one.jpg", "image_b": "two.jpg", "phash_distance": 1}])
    monkeypatch.setattr("modules.data_integrity.analyzer.find_label_anomalies", lambda *_: [{"label": "plane", "count": 1}])
    monkeypatch.setattr("modules.data_integrity.analyzer.analyze_ood", lambda *_ , **__: {"outliers": [{"image": "two.jpg", "ood_score": 0.4}], "calibration": {"method": "median_mad", "threshold": 0.3}})

    result = analyze_dataset(str(dataset))

    assert result["image_count"] == 2
    assert result["finding_count"] == 4
    assert result["findings_by_category"] == {"EXACT_DUPLICATE": 1, "LABEL_ANOMALY": 1, "NEAR_DUPLICATE": 1, "OOD_OUTLIER": 1}
    assert result["ood_calibration"]["threshold"] == 0.3
    assert [finding["finding_id"] for finding in result["findings"]] == ["F-DATA-001", "F-DATA-002", "F-DATA-003", "F-DATA-004"]


@pytest.mark.parametrize(
    ("option", "detector"),
    [("run_duplicates", "duplicates"), ("run_phash", "phash"), ("run_ood", "ood")],
)
def test_detector_can_be_disabled(monkeypatch, tmp_path, option, detector):
    dataset = _dataset(tmp_path, with_labels=False)
    monkeypatch.setattr("modules.data_integrity.analyzer.find_exact_duplicates", lambda _: {})
    monkeypatch.setattr("modules.data_integrity.analyzer.find_near_duplicates", lambda *_: [])
    monkeypatch.setattr("modules.data_integrity.analyzer.analyze_ood", lambda *_ , **__: {"outliers": [], "calibration": {}})

    result = analyze_dataset(str(dataset), run_labels=False, **{option: False})

    assert result["skipped_detectors"][detector] == "Disabled by configuration."


def test_missing_labels_are_skipped(monkeypatch, tmp_path):
    dataset = _dataset(tmp_path, with_labels=False)
    monkeypatch.setattr("modules.data_integrity.analyzer.find_exact_duplicates", lambda _: {})
    monkeypatch.setattr("modules.data_integrity.analyzer.find_near_duplicates", lambda *_: [])
    monkeypatch.setattr("modules.data_integrity.analyzer.analyze_ood", lambda *_ , **__: {"outliers": [], "calibration": {}})

    result = analyze_dataset(str(dataset))

    assert "labels" in result["skipped_detectors"]
    assert result["finding_count"] == 0


def test_empty_dataset_returns_empty_summary(monkeypatch, tmp_path):
    monkeypatch.setattr("modules.data_integrity.analyzer.find_exact_duplicates", lambda _: {})
    monkeypatch.setattr("modules.data_integrity.analyzer.find_near_duplicates", lambda *_: [])
    monkeypatch.setattr("modules.data_integrity.analyzer.analyze_ood", lambda *_ , **__: {"outliers": [], "calibration": {"method": "empty"}})

    result = analyze_dataset(str(tmp_path))

    assert result["image_count"] == 0
    assert result["findings_by_category"] == {}
    assert result["findings_by_severity"] == {}


def test_invalid_dataset_path_raises():
    with pytest.raises(ValueError, match="Dataset path does not exist"):
        analyze_dataset("missing-dataset")


def test_risk_summary_uses_real_contributor_metadata(monkeypatch, tmp_path):
    dataset = _dataset(tmp_path)
    monkeypatch.setattr("modules.data_integrity.analyzer.load_yolo", lambda *_: [{"sample_id": "one.jpg", "labels": [], "contributor_id": "source-a", "batch_id": "batch-1"}])
    monkeypatch.setattr("modules.data_integrity.analyzer.find_exact_duplicates", lambda _: {"hash": ["one.jpg", "two.jpg"]})
    monkeypatch.setattr("modules.data_integrity.analyzer.find_near_duplicates", lambda *_: [])
    monkeypatch.setattr("modules.data_integrity.analyzer.analyze_ood", lambda *_ , **__: {"outliers": [], "calibration": {}})

    result = analyze_dataset(str(dataset))

    assert result["risk_summary"] == [{"contributor_id": "source-a", "finding_count": 1, "average_confidence": 1.0, "affected_batches": ["batch-1"]}]
