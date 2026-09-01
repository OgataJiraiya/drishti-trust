import numpy as np

from modules.data_integrity.ood import (
    calculate_ood_scores,
    calibrate_ood_threshold,
    create_ood_findings,
    find_ood_outliers,
)


def test_calculate_ood_scores():
    embeddings = {
        "image_a.jpg": np.array([1.0, 0.0]),
        "image_b.jpg": np.array([1.0, 0.0]),
        "image_c.jpg": np.array([0.0, 1.0]),
    }

    scores = calculate_ood_scores(embeddings)

    assert set(scores) == {
        "image_a.jpg",
        "image_b.jpg",
        "image_c.jpg",
    }

    assert scores["image_a.jpg"] == scores["image_b.jpg"]
    assert scores["image_c.jpg"] > scores["image_a.jpg"]


def test_empty_embeddings():
    assert calculate_ood_scores({}) == {}


def test_calibrate_ood_threshold_for_normal_distribution():
    threshold, metadata = calibrate_ood_threshold(
        [0.10, 0.11, 0.12, 0.12, 0.13, 0.13, 0.14, 0.15]
    )

    assert metadata["method"] == "median_mad"
    assert metadata["sample_count"] == 8
    assert metadata["median"] == 0.125
    assert metadata["mad"] == 0.01
    assert threshold == metadata["threshold"]
    assert threshold >= metadata["p95"]


def test_calibrate_ood_threshold_detects_clear_outlier():
    scores = [0.10, 0.11, 0.11, 0.12, 0.12, 0.13, 0.14, 0.40]

    threshold, metadata = calibrate_ood_threshold(scores)

    assert metadata["method"] == "median_mad"
    assert 0.14 < threshold < 0.40
    assert [score for score in scores if score >= threshold] == [0.40]


def test_calibrate_ood_threshold_for_empty_scores():
    threshold, metadata = calibrate_ood_threshold([])

    assert threshold is None
    assert metadata == {
        "method": "empty",
        "threshold": None,
        "sample_count": 0,
        "median": None,
        "mad": None,
        "p95": None,
    }


def test_calibrate_ood_threshold_for_small_dataset_uses_percentile():
    threshold, metadata = calibrate_ood_threshold([0.10, 0.20, 0.30])

    assert metadata["method"] == "percentile_fallback"
    assert threshold == 0.29
    assert metadata["p95"] == 0.29


def test_create_ood_finding():
    outliers = [
        {
            "image": "images/unusual.jpg",
            "ood_score": 0.30,
        }
    ]

    findings = create_ood_findings(outliers)

    assert len(findings) == 1

    finding = findings[0]

    assert finding["finding_id"] == "F-DATA-001"
    assert finding["module"] == "dataset_integrity"
    assert finding["asset_type"] == "sample"
    assert finding["asset_id"] == "images/unusual.jpg"
    assert finding["category"] == "OOD_OUTLIER"
    assert finding["severity"] == "LOW"
    assert finding["confidence"] == 0.6
    assert finding["recommendation"] == "REVIEW"


def test_zero_score_finding():
    outliers = [
        {
            "image": "images/test.jpg",
            "ood_score": 0.0,
        }
    ]

    findings = create_ood_findings(outliers)

    assert findings[0]["confidence"] == 0.0
    assert findings[0]["severity"] == "LOW"


def test_find_ood_outliers_fixed_threshold(monkeypatch, tmp_path):
    image_path = tmp_path / "sample.jpg"
    image_path.touch()

    monkeypatch.setattr(
        "modules.data_integrity.ood.calculate_image_embedding",
        lambda *_: np.array([1.0, 0.0]),
    )
    monkeypatch.setattr(
        "modules.data_integrity.ood.calculate_ood_scores",
        lambda _: {"sample.jpg": 0.30},
    )

    outliers = find_ood_outliers(
        str(tmp_path),
        calibration="fixed",
        threshold=0.20,
        processor=object(),
        model=object(),
    )

    assert outliers == [{"image": "sample.jpg", "ood_score": 0.30}]


def test_find_ood_outliers_auto_calibration(monkeypatch, tmp_path):
    for filename in ("a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"):
        (tmp_path / filename).touch()

    monkeypatch.setattr(
        "modules.data_integrity.ood.calculate_image_embedding",
        lambda *_: np.array([1.0, 0.0]),
    )
    monkeypatch.setattr(
        "modules.data_integrity.ood.calculate_ood_scores",
        lambda _: {
            "a.jpg": 0.10,
            "b.jpg": 0.11,
            "c.jpg": 0.12,
            "d.jpg": 0.13,
            "e.jpg": 0.40,
        },
    )

    outliers = find_ood_outliers(
        str(tmp_path),
        processor=object(),
        model=object(),
    )

    assert outliers == [{"image": "e.jpg", "ood_score": 0.40}]


def test_find_ood_outliers_requires_threshold_in_fixed_mode(tmp_path):
    with np.testing.assert_raises_regex(ValueError, "threshold is required"):
        find_ood_outliers(
            str(tmp_path),
            calibration="fixed",
            processor=object(),
            model=object(),
        )
