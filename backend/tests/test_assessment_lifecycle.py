from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, select

from backend.database.models import (
    AssessmentRecord, AssessmentRunMembershipRecord, AssessmentSnapshotRecord,
    FindingRecord, ModuleRunRecord,
)
from backend.integrations.signing import module_run_signing_bytes
from backend.schemas.evidence import Finding
from backend.schemas.integration import ModuleRunSubmission
from backend.tests.test_evidence import dataset_finding, model_finding
from backend.tests.test_module_auth import register_identity, signed_body


async def create(client, assessment_id: str, name: str | None = None):
    return await client.post("/api/assessments", json={
        "assessment_id": assessment_id, "name": name or assessment_id,
        "description": "Disposable assessment", "metadata": {"purpose": "test"},
    })


@pytest.mark.anyio
async def test_create_strict_admin_retrieve_list_and_current(client, app):
    unauth = await client.post("/api/assessments", headers={"Authorization": "Bearer wrong"},
                               json={"assessment_id": "A", "name": "A"})
    assert unauth.status_code == 401
    assert (await create(client, "A")).status_code == 201
    assert (await client.get("/api/assessments/A")).json()["status"] == "DRAFT"
    listing = (await client.get("/api/assessments?status=DRAFT&limit=1&offset=0")).json()
    assert listing["total"] == 1 and listing["items"][0]["assessment_id"] == "A"
    assert (await client.get("/api/assessments/current")).status_code == 404
    activated = await client.post("/api/assessments/A/activate")
    assert activated.status_code == 200 and activated.json()["status"] == "ACTIVE"
    assert (await client.get("/api/assessments/current")).json()["assessment_id"] == "A"
    events = (await client.get("/api/audit")).json()["items"]
    assert [item["event_type"] for item in events].count("ASSESSMENT_CREATED") == 1
    assert [item["event_type"] for item in events].count("ASSESSMENT_ACTIVATED") == 1
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AssessmentRecord)) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("body", [
    {"assessment_id": " ", "name": "A"},
    {"assessment_id": "A", "name": " "},
    {"assessment_id": "A", "name": "A", "status": "ACTIVE"},
    {"assessment_id": "A", "name": "A", "unexpected": True},
    {"assessment_id": "X" * 129, "name": "A"},
])
async def test_assessment_create_schema_is_strict(client, body):
    assert (await client.post("/api/assessments", json=body)).status_code == 422


@pytest.mark.anyio
async def test_only_one_active_and_no_reactivation(client):
    await create(client, "A")
    await create(client, "B")
    assert (await client.post("/api/assessments/A/activate")).status_code == 200
    assert (await client.post("/api/assessments/B/activate")).status_code == 409
    await client.post("/api/assessments/A/seal")
    assert (await client.post("/api/assessments/A/activate")).status_code == 409
    assert (await client.post("/api/assessments/B/activate")).status_code == 200


@pytest.mark.anyio
async def test_concurrent_activation_leaves_exactly_one_active(client):
    await create(client, "A")
    await create(client, "B")
    responses = await asyncio.gather(
        client.post("/api/assessments/A/activate"),
        client.post("/api/assessments/B/activate"),
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert (await client.get("/api/assessments?status=ACTIVE")).json()["total"] == 1


@pytest.mark.anyio
async def test_signed_run_binds_active_assessment_and_exact_rerun(client, app):
    key = Ed25519PrivateKey.generate()
    await register_identity(client, key)
    await create(client, "A")
    await client.post("/api/assessments/A/activate")
    run = {"run_id": "RUN-A", "assessment_id": "A", "module": "dataset_integrity",
           "producer": "data-integrity", "producer_version": "1", "findings": [dataset_finding()]}
    first = await client.post("/api/integration/signed-runs", json=signed_body(run, "KEY-DATA-001", key))
    second = await client.post("/api/integration/signed-runs", json=signed_body(run, "KEY-DATA-001", key))
    assert first.json()["result"] == "CREATED" and second.json()["result"] == "EXISTS"
    details = (await client.get("/api/integration/runs/RUN-A")).json()
    assert details["assessment_id"] == "A" and details["authentication"]["mode"] == "ED25519"
    assert (await client.get("/api/assessments/A/runs")).json()["total"] == 1
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AssessmentRunMembershipRecord)) == 1


@pytest.mark.anyio
async def test_signature_mutation_unknown_and_draft_reject_without_state(client, app):
    key = Ed25519PrivateKey.generate()
    await register_identity(client, key)
    await create(client, "A")
    await create(client, "B")
    run = {"run_id": "RUN-A", "assessment_id": "A", "module": "dataset_integrity",
           "producer": "data-integrity", "producer_version": "1", "findings": [dataset_finding()]}
    signed = signed_body(run, "KEY-DATA-001", key)
    signed["run"]["assessment_id"] = "B"
    assert (await client.post("/api/integration/signed-runs", json=signed)).status_code == 401
    missing = deepcopy(run); missing["assessment_id"] = "MISSING"
    assert (await client.post("/api/integration/signed-runs", json=signed_body(missing, "KEY-DATA-001", key))).status_code == 404
    assert (await client.post("/api/integration/signed-runs", json=signed_body(run, "KEY-DATA-001", key))).status_code == 409
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModuleRunRecord)) == 0
        assert session.scalar(select(func.count()).select_from(FindingRecord)) == 0
        assert session.scalar(select(func.count()).select_from(AssessmentRunMembershipRecord)) == 0


def _low_dataset(finding_id: str) -> dict:
    finding = dataset_finding()
    finding.update({"finding_id": finding_id, "severity": "LOW", "confidence": .5,
                    "category": "LOW_DRIFT", "recommendation": "REVIEW"})
    return finding


@pytest.mark.anyio
async def test_summary_isolation_seal_snapshot_verify_and_idempotency(client, app):
    key = Ed25519PrivateKey.generate()
    await register_identity(client, key)
    await create(client, "A"); await client.post("/api/assessments/A/activate")
    critical = model_finding(); critical.update({"severity": "CRITICAL", "category": "MODEL_BACKDOOR",
                                                "recommendation": "QUARANTINE"})
    # Producer authorization requires a matching model identity.
    model_key = Ed25519PrivateKey.generate()
    await register_identity(client, model_key, "model-integrity", "model_integrity", "KEY-MODEL")
    run_a = {"run_id": "RUN-A", "assessment_id": "A", "module": "model_integrity",
             "producer": "model-integrity", "producer_version": "1", "findings": [critical]}
    assert (await client.post("/api/integration/signed-runs", json=signed_body(run_a, "KEY-MODEL", model_key))).status_code == 200
    snapshot_a = (await client.post("/api/assessments/A/seal")).json()
    assert len(snapshot_a["summary_hash"]) == 64 and snapshot_a["run_count"] == 1
    assert (await client.get("/api/assessments/A/snapshot/verify")).json()["status"] == "VALID"
    repeated = (await client.post("/api/assessments/A/seal")).json()
    assert repeated == snapshot_a
    assert (await client.post("/api/integration/signed-runs", json=signed_body(
        {**run_a, "run_id": "RUN-A2"}, "KEY-MODEL", model_key))).status_code == 409

    await create(client, "B"); await client.post("/api/assessments/B/activate")
    run_b = {"run_id": "RUN-B", "assessment_id": "B", "module": "dataset_integrity",
             "producer": "data-integrity", "producer_version": "1", "findings": [_low_dataset("F-B")]}
    await client.post("/api/integration/signed-runs", json=signed_body(run_b, "KEY-DATA-001", key))
    current = (await client.get("/api/summary?trust_scope=authenticated")).json()
    historical = (await client.get("/api/summary?assessment_id=A&trust_scope=authenticated")).json()
    assert current["assessment_id"] == "B" and current["scope_mode"] == "ACTIVE_ASSESSMENT"
    assert current["overall"]["disposition"] != "QUARANTINE"
    assert historical["assessment_id"] == "A" and historical["overall"]["disposition"] == "QUARANTINE"
    assert (await client.get("/api/assessments/A/snapshot")).json() == snapshot_a
    audit = (await client.get("/api/audit?event_type=ASSESSMENT_SEALED")).json()
    assert audit["total"] == 1
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AssessmentSnapshotRecord)) == 1


@pytest.mark.anyio
async def test_assessment_trust_scope_excludes_unscoped_and_direct_evidence(client):
    key = Ed25519PrivateKey.generate()
    await register_identity(client, key)
    await create(client, "A"); await client.post("/api/assessments/A/activate")
    signed = {"run_id": "SIGNED", "assessment_id": "A", "module": "dataset_integrity",
              "producer": "data-integrity", "findings": [_low_dataset("F-SIGNED")]}
    assert (await client.post("/api/integration/signed-runs",
                              json=signed_body(signed, "KEY-DATA-001", key))).status_code == 200
    internal = {"run_id": "INTERNAL", "assessment_id": "A", "module": "dataset_integrity",
                "producer": "internal", "findings": [_low_dataset("F-INTERNAL")]}
    assert (await client.post("/api/integration/runs", json=internal)).status_code == 200
    unscoped = {"run_id": "LEGACY", "module": "dataset_integrity", "producer": "internal",
                "findings": [_low_dataset("F-LEGACY")]}
    assert (await client.post("/api/integration/runs", json=unscoped)).status_code == 200
    direct = _low_dataset("F-DIRECT")
    assert (await client.post("/api/evidence", json=direct)).status_code == 200
    authenticated = (await client.get("/api/summary?assessment_id=A&trust_scope=authenticated")).json()
    all_scope = (await client.get("/api/summary?assessment_id=A&trust_scope=all")).json()
    assert authenticated["finding_counts"]["total"] == 1
    assert authenticated["excluded_untrusted_finding_count"] == 1
    assert all_scope["finding_counts"]["total"] == 2
    assert {item["finding_id"] for item in all_scope["latest_findings"]} == {"F-SIGNED", "F-INTERNAL"}


@pytest.mark.anyio
async def test_seal_run_race_never_leaves_post_snapshot_membership(client, app):
    key = Ed25519PrivateKey.generate()
    await register_identity(client, key)
    await create(client, "A"); await client.post("/api/assessments/A/activate")
    run = {"run_id": "RACE", "assessment_id": "A", "module": "dataset_integrity",
           "producer": "data-integrity", "findings": [_low_dataset("F-RACE")]}
    seal, ingest = await asyncio.gather(
        client.post("/api/assessments/A/seal"),
        client.post("/api/integration/signed-runs", json=signed_body(run, "KEY-DATA-001", key)),
    )
    assert seal.status_code == 200 and ingest.status_code in {200, 409}
    snapshot = seal.json()
    with app.state.session_factory() as session:
        memberships = int(session.scalar(select(func.count()).select_from(
            AssessmentRunMembershipRecord
        ).where(AssessmentRunMembershipRecord.assessment_id == "A")) or 0)
    assert memberships == snapshot["run_count"]
    assert (await client.get("/api/assessments/A/snapshot/verify")).json()["status"] == "VALID"


def test_finding_v1_and_signing_bytes_bind_assessment():
    assert list(Finding.model_fields) == [
        "finding_id", "module", "asset_type", "asset_id", "category", "severity",
        "confidence", "reason", "evidence", "recommendation", "limitations",
    ]
    base = {"run_id": "R", "assessment_id": "A", "module": "dataset_integrity",
            "producer": "p", "findings": [dataset_finding()]}
    a = ModuleRunSubmission.model_validate(base)
    b = ModuleRunSubmission.model_validate({**base, "assessment_id": "B"})
    assert module_run_signing_bytes(a) != module_run_signing_bytes(b)
