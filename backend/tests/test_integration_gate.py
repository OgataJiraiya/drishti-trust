from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from backend.database.models import AuditLogRecord, FindingRecord, ModuleRunRecord
from backend.schemas.evidence import Finding
from backend.tests.test_evidence import dataset_finding, drift_finding, inference_finding, model_finding


def run_submission(
    run_id: str = "RUN-DATA-0001",
    module: str = "dataset_integrity",
    producer: str = "data-integrity",
    findings=None,
):
    return {
        "run_id": run_id,
        "module": module,
        "producer": producer,
        "producer_version": "0.1.0",
        "findings": findings if findings is not None else [dataset_finding()],
    }


def numbered_dataset_finding(index: int):
    item = dataset_finding()
    item["finding_id"] = f"F-DATA-{index:03d}"
    item["asset_id"] = f"sample:img_{index:04d}"
    return item


@pytest.mark.anyio
async def test_valid_run_schema_is_accepted(client):
    response = await client.post("/api/integration/runs", json=run_submission())
    assert response.status_code == 200
    assert response.json()["result"] == "CREATED"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutator",
    [
        lambda body: body.update({"unexpected": True}),
        lambda body: body["findings"][0].update({"unexpected": True}),
        lambda body: body.update({"run_id": "   "}),
        lambda body: body.update({"producer": "   "}),
        lambda body: body.update({"producer_version": "   "}),
        lambda body: body.update({"findings": []}),
        lambda body: body.update({"module": "dataset_integrty"}),
        lambda body: body["findings"][0].update({"severity": "SEVERE"}),
        lambda body: body["findings"][0].update({"recommendation": "ALLOW"}),
        lambda body: body["findings"][0].update({"confidence": 1.1}),
        lambda body: body["findings"][0].update({"evidence": []}),
        lambda body: body["findings"][0].update({"evidence": [" "]}),
    ],
)
async def test_invalid_outer_or_finding_schema_fails_before_mutation(client, app, mutator):
    body = run_submission()
    mutator(body)
    response = await client.post("/api/integration/runs", json=body)
    assert response.status_code == 422
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModuleRunRecord)) == 0
        assert session.scalar(select(func.count()).select_from(FindingRecord)) == 0


@pytest.mark.anyio
async def test_more_than_100_findings_is_rejected(client):
    body = run_submission(findings=[numbered_dataset_finding(index) for index in range(1, 102)])
    assert (await client.post("/api/integration/runs", json=body)).status_code == 422


@pytest.mark.anyio
async def test_module_mismatch_is_rejected(client):
    body = run_submission(findings=[model_finding()])
    assert (await client.post("/api/integration/runs", json=body)).status_code == 422


@pytest.mark.anyio
async def test_duplicate_finding_ids_inside_run_are_rejected(client):
    duplicate = dataset_finding()
    body = run_submission(findings=[duplicate, deepcopy(duplicate)])
    assert (await client.post("/api/integration/runs", json=body)).status_code == 422


@pytest.mark.anyio
async def test_successful_run_populates_evidence_retrieval_listing_and_summary(client):
    findings = [numbered_dataset_finding(1), numbered_dataset_finding(2)]
    response = (await client.post("/api/integration/runs", json=run_submission(findings=findings))).json()
    assert response == {
        "run_id": "RUN-DATA-0001",
        "module": "dataset_integrity",
        "producer": "data-integrity",
        "producer_version": "0.1.0",
        "result": "CREATED",
        "total_findings": 2,
        "created_findings": 2,
        "existing_findings": 0,
        "finding_ids": ["F-DATA-001", "F-DATA-002"],
    }
    assert (await client.get("/api/evidence")).json()["total"] == 2
    details = (await client.get("/api/integration/runs/RUN-DATA-0001")).json()
    assert details["request_hash"] and details["finding_ids"] == response["finding_ids"]
    listing = (await client.get("/api/integration/runs")).json()
    assert listing["total"] == 1 and listing["items"][0]["run_id"] == "RUN-DATA-0001"
    summary = (await client.get("/api/summary")).json()
    assert summary["modules"]["dataset_integrity"]["finding_count"] == 2


@pytest.mark.anyio
async def test_exact_rerun_is_perfectly_idempotent(client, app):
    body = run_submission()
    first = (await client.post("/api/integration/runs", json=body)).json()
    second = (await client.post("/api/integration/runs", json=body)).json()
    assert first["result"] == "CREATED"
    assert second["result"] == "EXISTS"
    assert second["created_findings"] == 0 and second["existing_findings"] == 1
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FindingRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ModuleRunRecord)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLogRecord).where(
            AuditLogRecord.event_type == "MODULE_RUN_INGESTED"
        )) == 1
        assert session.scalar(select(func.count()).select_from(AuditLogRecord).where(
            AuditLogRecord.event_type == "EVIDENCE_CREATED"
        )) == 1


@pytest.mark.anyio
async def test_existing_identical_finding_is_reused_with_one_new_finding(client):
    existing = numbered_dataset_finding(1)
    new = numbered_dataset_finding(2)
    assert (await client.post("/api/evidence", json=existing)).json()["result"] == "CREATED"
    result = (await client.post(
        "/api/integration/runs", json=run_submission(findings=[existing, new])
    )).json()
    assert result["total_findings"] == 2
    assert result["existing_findings"] == 1
    assert result["created_findings"] == 1
    assert (await client.get("/api/evidence")).json()["total"] == 2


@pytest.mark.anyio
async def test_same_run_id_changed_request_conflicts_and_preserves_original(client):
    original = run_submission()
    await client.post("/api/integration/runs", json=original)
    changed = deepcopy(original)
    changed["producer"] = "different-producer"
    response = await client.post("/api/integration/runs", json=changed)
    assert response.status_code == 409
    stored = (await client.get("/api/integration/runs/RUN-DATA-0001")).json()
    assert stored["producer"] == "data-integrity"


@pytest.mark.anyio
async def test_changed_finding_conflict_rolls_back_entire_multi_finding_batch(client, app):
    original = numbered_dataset_finding(1)
    await client.post("/api/evidence", json=original)
    new_finding = numbered_dataset_finding(2)
    conflicting = deepcopy(original)
    conflicting["reason"] = "Changed immutable reason."
    body = run_submission(run_id="RUN-CONFLICT", findings=[new_finding, conflicting])
    response = await client.post("/api/integration/runs", json=body)
    assert response.status_code == 409
    assert (await client.get("/api/evidence/F-DATA-001")).json() == original
    assert (await client.get("/api/evidence/F-DATA-002")).status_code == 404
    assert (await client.get("/api/integration/runs/RUN-CONFLICT")).status_code == 404
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditLogRecord).where(
            AuditLogRecord.event_type == "MODULE_RUN_INGESTED"
        )) == 0
        assert session.scalar(select(func.count()).select_from(AuditLogRecord).where(
            AuditLogRecord.asset_id == "F-DATA-002"
        )) == 0


@pytest.mark.anyio
async def test_conflict_at_finding_100_leaves_first_99_uncommitted(client, app):
    existing = numbered_dataset_finding(100)
    assert (await client.post("/api/evidence", json=existing)).status_code == 200
    batch = [numbered_dataset_finding(index) for index in range(1, 100)]
    conflicting = deepcopy(existing)
    conflicting["category"] = "CHANGED_IMMUTABLE_CATEGORY"
    batch.append(conflicting)

    response = await client.post(
        "/api/integration/runs",
        json=run_submission(run_id="RUN-CONFLICT-100", findings=batch),
    )

    assert response.status_code == 409
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FindingRecord)) == 1
        assert session.get(ModuleRunRecord, "RUN-CONFLICT-100") is None


@pytest.mark.anyio
async def test_all_four_modules_reach_complete_assurance_coverage(client):
    submissions = [
        run_submission("RUN-DATA", "dataset_integrity", "data-integrity", [dataset_finding()]),
        run_submission("RUN-MODEL", "model_integrity", "model-integrity", [model_finding()]),
        run_submission("RUN-INF", "inference_integrity", "security-backend", [inference_finding()]),
        run_submission("RUN-DRIFT", "distribution_shift", "distribution-shift", [drift_finding()]),
    ]
    for submission in submissions:
        assert (await client.post("/api/integration/runs", json=submission)).json()["result"] == "CREATED"
    assert (await client.get("/api/integration/runs?page_size=100")).json()["total"] == 4
    summary = (await client.get("/api/summary")).json()
    assert summary["overall"]["assessment_coverage"] == 1.0
    assert summary["overall"]["score_status"] == "COMPLETE"
    assert summary["overall"]["disposition"] == "QUARANTINE"


def test_frozen_finding_schema_remains_exact():
    assert list(Finding.model_fields) == [
        "finding_id", "module", "asset_type", "asset_id", "category",
        "severity", "confidence", "reason", "evidence", "recommendation", "limitations",
    ]


@pytest.mark.anyio
async def test_successful_run_audit_payload_and_chain_are_valid(client):
    body = run_submission(findings=[numbered_dataset_finding(1), numbered_dataset_finding(2)])
    await client.post("/api/integration/runs", json=body)
    events = (await client.get("/api/audit?event_type=MODULE_RUN_INGESTED")).json()
    assert events["total"] == 1
    event = events["items"][0]
    assert event["asset_type"] == "module_run" and event["asset_id"] == "RUN-DATA-0001"
    assert event["payload"] == {
        "run_id": "RUN-DATA-0001",
        "module": "dataset_integrity",
        "producer": "data-integrity",
        "producer_version": "0.1.0",
        "total_findings": 2,
        "created_findings": 2,
        "existing_findings": 0,
    }
    assert (await client.get("/api/audit/verify")).json()["status"] == "VALID"


@pytest.mark.anyio
async def test_run_retrieval_and_listing_are_read_only(client, app):
    await client.post("/api/integration/runs", json=run_submission())
    with app.state.session_factory() as session:
        before = (
            session.scalar(select(func.count()).select_from(FindingRecord)),
            session.scalar(select(func.count()).select_from(ModuleRunRecord)),
            session.scalar(select(func.count()).select_from(AuditLogRecord)),
        )
    for _ in range(5):
        await client.get("/api/integration/runs/RUN-DATA-0001")
        await client.get("/api/integration/runs")
    with app.state.session_factory() as session:
        after = (
            session.scalar(select(func.count()).select_from(FindingRecord)),
            session.scalar(select(func.count()).select_from(ModuleRunRecord)),
            session.scalar(select(func.count()).select_from(AuditLogRecord)),
        )
    assert after == before


@pytest.mark.anyio
async def test_run_pagination_and_module_filter(client):
    submissions = [
        run_submission("RUN-DATA-1", "dataset_integrity", findings=[numbered_dataset_finding(1)]),
        run_submission("RUN-DATA-2", "dataset_integrity", findings=[numbered_dataset_finding(2)]),
        run_submission("RUN-MODEL-1", "model_integrity", "model", [model_finding()]),
    ]
    for submission in submissions:
        await client.post("/api/integration/runs", json=submission)
    page = (await client.get("/api/integration/runs?page=2&page_size=1")).json()
    assert page["total"] == 3 and len(page["items"]) == 1
    filtered = (await client.get("/api/integration/runs?module=dataset_integrity&page_size=100")).json()
    assert filtered["total"] == 2
    assert [item["run_id"] for item in filtered["items"]] == ["RUN-DATA-1", "RUN-DATA-2"]


@pytest.mark.anyio
async def test_concurrent_exact_submissions_cannot_duplicate_state(client, app):
    body = run_submission()
    responses = await asyncio.gather(
        client.post("/api/integration/runs", json=body),
        client.post("/api/integration/runs", json=body),
    )
    assert sorted(response.json()["result"] for response in responses) == ["CREATED", "EXISTS"]
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModuleRunRecord)) == 1
        assert session.scalar(select(func.count()).select_from(FindingRecord)) == 1
