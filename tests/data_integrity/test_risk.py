import pytest

from modules.data_integrity.risk import (
    aggregate_contributor_risk,
    aggregate_dataset_risk,
)


def test_aggregate_contributor_risk():
    samples = [
        {
            "sample_id": "img_001",
            "contributor_id": "CONTRIB-A",
            "batch_id": "BATCH-001",
        },
        {
            "sample_id": "img_002",
            "contributor_id": "CONTRIB-A",
            "batch_id": "BATCH-001",
        },
        {
            "sample_id": "img_003",
            "contributor_id": "CONTRIB-B",
            "batch_id": "BATCH-002",
        },
    ]

    findings = [
        {
            "asset_id": "img_001",
            "confidence": 1.0,
        },
        {
            "asset_id": "img_002",
            "confidence": 0.8,
        },
        {
            "asset_id": "img_003",
            "confidence": 0.6,
        },
    ]

    results = aggregate_contributor_risk(findings, samples)

    assert len(results) == 2

    assert results[0]["contributor_id"] == "CONTRIB-A"
    assert results[0]["finding_count"] == 2
    assert results[0]["average_confidence"] == 0.9
    assert results[0]["affected_batches"] == ["BATCH-001"]

    assert results[1]["contributor_id"] == "CONTRIB-B"
    assert results[1]["finding_count"] == 1
    assert results[1]["average_confidence"] == 0.6
    assert results[1]["affected_batches"] == ["BATCH-002"]


def test_findings_without_contributor_are_ignored():
    samples = [
        {
            "sample_id": "img_001",
            "contributor_id": None,
            "batch_id": "BATCH-001",
        }
    ]

    findings = [
        {
            "asset_id": "img_001",
            "confidence": 1.0,
        }
    ]

    results = aggregate_contributor_risk(findings, samples)

    assert results == []


def _finding(
    severity="LOW",
    confidence=1.0,
    category="OOD_OUTLIER",
    asset_id="image.jpg",
    evidence=None,
):
    return {
        "severity": severity,
        "confidence": confidence,
        "category": category,
        "asset_id": asset_id,
        "evidence": evidence or [],
    }


def test_dataset_risk_empty_findings():
    result = aggregate_dataset_risk([])

    assert result["risk_score"] == 0.0
    assert result["severity"] == "NONE"
    assert result["finding_count"] == 0


@pytest.mark.parametrize(
    ("severity", "expected_score"),
    [("LOW", 10.0), ("MEDIUM", 30.0), ("HIGH", 60.0)],
)
def test_dataset_risk_single_finding_severity(severity, expected_score):
    result = aggregate_dataset_risk([_finding(severity=severity)])

    assert result["risk_score"] == expected_score
    assert result["severity"] == severity


def test_dataset_risk_confidence_affects_score():
    result = aggregate_dataset_risk([_finding(severity="HIGH", confidence=0.5)])

    assert result["risk_score"] == 30.0
    assert result["severity"] == "MEDIUM"


def test_dataset_risk_aggregates_multiple_findings():
    result = aggregate_dataset_risk([
        _finding(severity="LOW", confidence=0.5, asset_id="one.jpg"),
        _finding(severity="HIGH", confidence=0.5, asset_id="two.jpg"),
    ])

    assert result["risk_score"] == 35.0
    assert result["severity"] == "MEDIUM"


def test_dataset_risk_deduplicates_repeated_events():
    finding = _finding(severity="HIGH", evidence=["sha256=abc"])

    result = aggregate_dataset_risk([finding, {**finding, "confidence": 0.2}])

    assert result["risk_score"] == 60.0
    assert result["unique_finding_count"] == 1
    assert result["deduplicated_finding_count"] == 1


def test_dataset_risk_reports_category_and_severity_counts():
    result = aggregate_dataset_risk([
        _finding(category="EXACT_DUPLICATE", severity="HIGH"),
        _finding(category="OOD_OUTLIER", severity="LOW", asset_id="two.jpg"),
    ])

    assert result["findings_by_category"] == {
        "EXACT_DUPLICATE": 1,
        "OOD_OUTLIER": 1,
    }
    assert result["findings_by_severity"] == {"HIGH": 1, "LOW": 1}


def test_dataset_risk_rejects_unknown_severity():
    with pytest.raises(ValueError, match="Unknown finding severity"):
        aggregate_dataset_risk([_finding(severity="NOTICE")])


def test_dataset_risk_does_not_require_contributor_metadata():
    result = aggregate_dataset_risk([_finding()])

    assert result["risk_score"] == 10.0


def test_dataset_risk_handles_missing_optional_confidence():
    finding = _finding()
    del finding["confidence"]

    result = aggregate_dataset_risk([finding])

    assert result["risk_score"] == 0.0
    assert result["severity"] == "NONE"


def test_dataset_risk_is_deterministic():
    findings = [
        _finding(severity="MEDIUM", asset_id="one.jpg"),
        _finding(severity="LOW", asset_id="two.jpg"),
    ]

    assert aggregate_dataset_risk(findings) == aggregate_dataset_risk(
        list(reversed(findings))
    )
