from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select

from backend.database.models import AuditLogRecord, FindingRecord
from backend.main import create_app
from backend.schemas.inference import VerificationFinding
from backend.services.finding_adapters import (
    model_substitution_to_common_finding,
    output_tampering_to_common_finding,
    replay_to_common_finding,
)


def dataset_finding():
    return {
        "finding_id": "F-DATA-001",
        "module": "dataset_integrity",
        "asset_type": "sample",
        "asset_id": "sample:img_0042",
        "category": "NEAR_DUPLICATE",
        "severity": "HIGH",
        "confidence": 0.91,
        "reason": "Sample is near-identical to 37 other samples.",
        "evidence": ["phash_distance=3", "cluster_id=17"],
        "recommendation": "REVIEW",
        "limitations": ["Similarity heuristic; visually similar legitimate images may be flagged."],
    }


def model_finding():
    return {
        "finding_id": "F-MODEL-001",
        "module": "model_integrity",
        "asset_type": "model",
        "asset_id": "model:MODEL-001",
        "category": "BACKDOOR_BEHAVIOR",
        "severity": "CRITICAL",
        "confidence": 0.89,
        "reason": "Reference challenge battery produced anomalous target-class behavior.",
        "evidence": ["trigger_success_rate=0.87", "target_class=background"],
        "recommendation": "QUARANTINE",
        "limitations": ["Behavioral anomaly does not by itself identify the exact training-time attack."],
    }


def inference_finding():
    return {
        "finding_id": "F-INF-001",
        "module": "inference_integrity",
        "asset_type": "inference",
        "asset_id": "inference:INF-0042",
        "category": "REPLAY_DETECTED",
        "severity": "CRITICAL",
        "confidence": 1.0,
        "reason": "A previously accepted inference receipt was submitted again.",
        "evidence": ["signature=VALID", "nonce=REUSED", "sequence=DUPLICATE"],
        "recommendation": "REJECT",
        "limitations": ["Replay detection depends on retained acceptance state and configured freshness controls."],
    }


def drift_finding():
    return {
        "finding_id": "F-DRIFT-001",
        "module": "distribution_shift",
        "asset_type": "distribution",
        "asset_id": "distribution:RUN-001",
        "category": "ENVIRONMENTAL_DRIFT",
        "severity": "MEDIUM",
        "confidence": 0.82,
        "reason": "Current image embeddings materially deviate from the declared reference distribution.",
        "evidence": ["drift_score=0.73", "suspected_factor=illumination"],
        "recommendation": "REVIEW",
        "limitations": ["Available evidence cannot conclusively distinguish environmental drift from intentional manipulation."],
    }


@pytest.mark.anyio
async def test_ingest_and_retrieve_exact_finalized_dataset_contract(client):
    source = dataset_finding()
    response = await client.post("/api/evidence", json=source)
    assert response.status_code == 200
    assert response.json() == {"finding": source, "result": "CREATED"}
    retrieved = (await client.get("/api/evidence/F-DATA-001")).json()
    assert retrieved == source
    assert retrieved["evidence"] == ["phash_distance=3", "cluster_id=17"]
    assert retrieved["limitations"] == source["limitations"]
    assert isinstance(retrieved["evidence"], list) and all(isinstance(item, str) for item in retrieved["evidence"])


@pytest.mark.anyio
async def test_all_four_official_modules_validate_and_persist(client):
    findings = [dataset_finding(), model_finding(), inference_finding(), drift_finding()]
    for finding in findings:
        response = await client.post("/api/evidence", json=finding)
        assert response.status_code == 200 and response.json()["result"] == "CREATED"
    listing = (await client.get("/api/evidence?page_size=100")).json()
    assert listing["total"] == 4
    assert [item["finding_id"] for item in listing["items"]] == [item["finding_id"] for item in findings]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("module", "dataset_integrty"),
        ("severity", "SEVERE"),
        ("recommendation", "ALLOW"),
        ("confidence", -0.1),
        ("confidence", 1.4),
        ("reason", "   "),
        ("evidence", []),
        ("evidence", [""]),
    ],
)
async def test_invalid_finding_values_return_clean_4xx(client, field, value):
    finding = dataset_finding()
    finding[field] = value
    response = await client.post("/api/evidence", json=finding)
    assert response.status_code == 422
    assert "detail" in response.json()


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", [
    lambda body: body.pop("category"),
    lambda body: body.update(timestamp="2026-09-10T00:00:00Z"),
    lambda body: (body.pop("category"), body.update(attack_class="NEAR_DUPLICATE")),
    lambda body: body.update(evidence="phash_distance=3"),
    lambda body: body.update(evidence=["valid", 7]),
    lambda body: body.update(reason="x" * 2049),
])
async def test_finding_shape_and_bounds_reject_contract_drift(client, mutation):
    body = dataset_finding()
    mutation(body)
    response = await client.post("/api/evidence", json=body)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_unicode_and_markup_are_data_not_schema_extensions(client):
    body = dataset_finding()
    body.update(
        finding_id="F-UNICODE-001",
        asset_id="sample:दृष्टि-👁️",
        reason="<script>alert('xss')</script> Δοκιμή 数据",
        evidence=["<img src=x onerror=alert(1)>", "unicode=✓"],
    )
    response = await client.post("/api/evidence", json=body)
    assert response.status_code == 200
    assert response.json()["finding"] == body


@pytest.mark.anyio
async def test_identical_duplicate_is_idempotent_without_duplicate_row_or_audit(client, app):
    first = await client.post("/api/evidence", json=dataset_finding())
    second = await client.post("/api/evidence", json=dataset_finding())
    assert first.json()["result"] == "CREATED"
    assert second.json()["result"] == "EXISTS"
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FindingRecord)) == 1
        assert session.scalar(
            select(func.count()).select_from(AuditLogRecord).where(AuditLogRecord.event_type == "EVIDENCE_CREATED")
        ) == 1


@pytest.mark.anyio
async def test_same_id_changed_body_conflicts_without_overwrite(client):
    original = dataset_finding()
    await client.post("/api/evidence", json=original)
    changed = {**original, "reason": "Changed assertion."}
    response = await client.post("/api/evidence", json=changed)
    assert response.status_code == 409
    assert (await client.get("/api/evidence/F-DATA-001")).json() == original


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("module=dataset_integrity", ["F-DATA-001"]),
        ("asset_type=model", ["F-MODEL-001"]),
        ("asset_id=inference%3AINF-0042", ["F-INF-001"]),
        ("category=ENVIRONMENTAL_DRIFT", ["F-DRIFT-001"]),
        ("severity=CRITICAL", ["F-MODEL-001", "F-INF-001"]),
        ("recommendation=REVIEW", ["F-DATA-001", "F-DRIFT-001"]),
    ],
)
async def test_all_supported_filters(client, query, expected):
    for finding in (dataset_finding(), model_finding(), inference_finding(), drift_finding()):
        await client.post("/api/evidence", json=finding)
    result = (await client.get(f"/api/evidence?{query}&page_size=100")).json()
    assert [item["finding_id"] for item in result["items"]] == expected


@pytest.mark.anyio
async def test_pagination_is_deterministic(client):
    for finding in (dataset_finding(), model_finding(), inference_finding(), drift_finding()):
        await client.post("/api/evidence", json=finding)
    page = (await client.get("/api/evidence?page=2&page_size=2")).json()
    assert page["total"] == 4 and page["page"] == 2
    assert [item["finding_id"] for item in page["items"]] == ["F-INF-001", "F-DRIFT-001"]


@pytest.mark.anyio
async def test_finding_persists_after_application_reopen(client, test_settings):
    await client.post("/api/evidence", json=dataset_finding())
    reopened = create_app(test_settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=reopened), base_url="http://reopened") as new_client:
        result = await new_client.get("/api/evidence/F-DATA-001")
    assert result.status_code == 200 and result.json() == dataset_finding()


@pytest.mark.anyio
async def test_new_findings_audit_once_and_chain_remains_valid(client):
    for finding in (dataset_finding(), model_finding()):
        await client.post("/api/evidence", json=finding)
    await client.post("/api/evidence", json=dataset_finding())
    events = (await client.get("/api/audit?event_type=EVIDENCE_CREATED&page_size=100")).json()
    assert events["total"] == 2
    assert [event["payload"]["finding_id"] for event in events["items"]] == ["F-DATA-001", "F-MODEL-001"]
    assert (await client.get("/api/audit/verify")).json()["status"] == "VALID"


def test_replay_adapter_produces_stable_frozen_contract():
    internal = [VerificationFinding(
        attack_class="REPLAY_DETECTED", severity="CRITICAL", confidence=1.0,
        reason="replayed", evidence={}, recommendation="REJECT",
    )]
    checks = {"signature": "VALID", "nonce": "INVALID", "sequence": "INVALID"}
    first = replay_to_common_finding("INF-0042", checks, internal)
    second = replay_to_common_finding("INF-0042", checks, internal)
    assert first == second
    assert first.category == "REPLAY_DETECTED"
    assert first.evidence == ["signature=VALID", "nonce=INVALID", "sequence=INVALID"]


def test_model_substitution_adapter_produces_common_contract():
    internal = VerificationFinding(
        attack_class="MODEL_SUBSTITUTION", severity="CRITICAL", confidence=1.0,
        reason="mismatch", evidence={"expected_sha256": "a" * 64, "observed_sha256": "b" * 64},
        recommendation="QUARANTINE",
    )
    finding = model_substitution_to_common_finding("MODEL-001", internal)
    assert finding.module == "inference_integrity"
    assert finding.recommendation == "QUARANTINE"
    assert finding.evidence == [f"expected_sha256={'a' * 64}", f"observed_sha256={'b' * 64}"]


def test_output_tampering_adapter_produces_common_contract():
    internal = VerificationFinding(
        attack_class="OUTPUT_TAMPERING", severity="CRITICAL", confidence=1.0,
        reason="mismatch", evidence={"expected_sha256": "c" * 64, "observed_sha256": "d" * 64},
        recommendation="REJECT",
    )
    finding = output_tampering_to_common_finding("INF-0042", internal)
    assert finding.category == "OUTPUT_TAMPERING"
    assert finding.asset_id == "inference:INF-0042"
    assert finding.limitations == []
