from modules.data_integrity.label_anomaly import (
    create_label_anomaly_findings,
    find_label_anomalies,
)


def test_find_rare_labels():
    samples = [
        {"sample_id": "1", "labels": ["car", "person"]},
        {"sample_id": "2", "labels": ["car"]},
        {"sample_id": "3", "labels": ["person"]},
        {"sample_id": "4", "labels": ["aircraft"]},
    ]

    anomalies = find_label_anomalies(samples, min_count=2)

    assert anomalies == [
        {
            "label": "aircraft",
            "count": 1,
        }
    ]


def test_no_label_anomalies():
    samples = [
        {"sample_id": "1", "labels": ["car", "person"]},
        {"sample_id": "2", "labels": ["car", "person"]},
    ]

    assert find_label_anomalies(samples, min_count=2) == []


def test_create_label_anomaly_finding():
    anomalies = [
        {
            "label": "aircraft",
            "count": 1,
        }
    ]

    findings = create_label_anomaly_findings(anomalies)

    assert len(findings) == 1

    finding = findings[0]

    assert finding["finding_id"].startswith("F-DATASET-")
    assert finding["module"] == "dataset_integrity"
    assert finding["asset_type"] == "dataset"
    assert finding["asset_id"] == "label:aircraft"
    assert finding["category"] == "LABEL_ANOMALY"
    assert finding["severity"] == "MEDIUM"
    assert finding["confidence"] == 0.75
    assert finding["recommendation"] == "REVIEW"
