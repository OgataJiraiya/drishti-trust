from __future__ import annotations

import pytest

from backend.schemas.evidence import Finding
from backend.tests.test_summary import finding, ingest


def test_finding_schema_v1_public_contract_is_exactly_frozen():
    assert list(Finding.model_fields) == [
        "finding_id",
        "module",
        "asset_type",
        "asset_id",
        "category",
        "severity",
        "confidence",
        "reason",
        "evidence",
        "recommendation",
        "limitations",
    ]
    evidence_annotation = Finding.model_fields["evidence"].annotation
    assert getattr(evidence_annotation, "__origin__", None) is list


@pytest.mark.anyio
async def test_asset_reject_alone_does_not_globally_reject_system(client):
    await ingest(client, finding(
        "F-ASSET-REJECT",
        category="LOCAL_ASSET_FAILURE",
        severity="INFO",
        confidence=0.1,
        recommendation="REJECT",
    ))
    summary = (await client.get("/api/summary")).json()
    assert summary["modules"]["dataset_integrity"]["top_findings"][0]["recommendation"] == "REJECT"
    assert summary["overall"]["disposition"] == "REVIEW"
    assert summary["overall"]["disposition"] != "REJECT"


@pytest.mark.anyio
async def test_replay_asset_reject_is_distinct_from_system_quarantine(client):
    await ingest(client, finding(
        "F-REPLAY-HARDENING",
        module="inference_integrity",
        category="REPLAY_DETECTED",
        severity="CRITICAL",
        recommendation="REJECT",
    ))
    summary = (await client.get("/api/summary")).json()
    detail = summary["overall"]["critical_override_details"][0]
    assert detail["asset_disposition"] == "REJECT"
    assert summary["modules"]["inference_integrity"]["disposition"] == "QUARANTINE"
    assert summary["overall"]["disposition"] == "QUARANTINE"
