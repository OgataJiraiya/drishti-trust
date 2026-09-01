from modules.data_integrity.risk import aggregate_contributor_risk


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