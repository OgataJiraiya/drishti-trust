from __future__ import annotations

import base64
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, select

from backend.core.canonical import canonical_json_bytes
from backend.core.hashing import sha256_bytes
from backend.database.models import (
    AuditLogRecord,
    FindingRecord,
    ModuleProducerRecord,
    ModuleRunRecord,
    ProducerKeyRecord,
)
from backend.integrations.signing import sign_module_run
from backend.schemas.evidence import Finding
from backend.schemas.integration import ModuleRunSubmission
from backend.tests.test_evidence import (
    dataset_finding,
    drift_finding,
    inference_finding,
    model_finding,
)


def public_pem(private_key: Ed25519PrivateKey) -> str:
    return private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def producer_body(
    producer_id: str = "data-integrity", module: str = "dataset_integrity"
) -> dict:
    return {
        "producer_id": producer_id,
        "display_name": f"{producer_id} module",
        "module": module,
        "metadata": {"offline": True},
    }


def run_body(
    run_id: str = "RUN-DATA-0001",
    module: str = "dataset_integrity",
    producer: str = "data-integrity",
    findings: list[dict] | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "module": module,
        "producer": producer,
        "producer_version": "0.1.0",
        "findings": findings or [dataset_finding()],
    }


def signed_body(run: dict, key_id: str, private_key: Ed25519PrivateKey) -> dict:
    parsed = ModuleRunSubmission.model_validate(run)
    return {"run": run, "key_id": key_id, "signature": sign_module_run(parsed, private_key)}


async def register_identity(
    client, private_key: Ed25519PrivateKey, producer_id: str = "data-integrity",
    module: str = "dataset_integrity", key_id: str = "KEY-DATA-001",
):
    producer = await client.post("/api/producers", json=producer_body(producer_id, module))
    assert producer.status_code == 200
    key = await client.post(
        f"/api/producers/{producer_id}/keys",
        json={"key_id": key_id, "public_key_pem": public_pem(private_key)},
    )
    assert key.status_code == 200
    return key.json()


@pytest.mark.anyio
async def test_producer_registry_create_retrieve_list_and_idempotency(client):
    body = producer_body()
    first = await client.post("/api/producers", json=body)
    second = await client.post("/api/producers", json=body)
    assert first.json()["result"] == "CREATED"
    assert second.json()["result"] == "EXISTS"
    assert (await client.get("/api/producers/data-integrity")).json()["module"] == "dataset_integrity"
    listing = (await client.get("/api/producers")).json()
    assert listing["total"] == 1 and listing["items"][0]["producer_id"] == "data-integrity"


@pytest.mark.anyio
async def test_changed_producer_identity_conflicts(client):
    body = producer_body()
    await client.post("/api/producers", json=body)
    changed = deepcopy(body)
    changed["display_name"] = "Changed"
    assert (await client.post("/api/producers", json=changed)).status_code == 409


@pytest.mark.anyio
@pytest.mark.parametrize("change", [
    {"producer_id": " "},
    {"module": "dataset_integrty"},
    {"extra": True},
])
async def test_invalid_producer_schema_rejected(client, change):
    body = producer_body()
    body.update(change)
    assert (await client.post("/api/producers", json=body)).status_code == 422


@pytest.mark.anyio
async def test_valid_key_registration_fingerprint_retrieval_and_idempotency(client):
    private_key = Ed25519PrivateKey.generate()
    await client.post("/api/producers", json=producer_body())
    body = {"key_id": "KEY-DATA-001", "public_key_pem": public_pem(private_key)}
    first = await client.post("/api/producers/data-integrity/keys", json=body)
    second = await client.post("/api/producers/data-integrity/keys", json=body)
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    assert first.json()["key"]["public_key_fingerprint"] == sha256_bytes(raw)
    assert second.json()["result"] == "EXISTS"
    keys = (await client.get("/api/producers/data-integrity/keys")).json()["items"]
    assert len(keys) == 1 and keys[0]["status"] == "ACTIVE"


@pytest.mark.anyio
async def test_malformed_private_and_rsa_keys_are_rejected(client):
    await client.post("/api/producers", json=producer_body())
    ed_private = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    rsa_public = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")
    for index, pem in enumerate(("not pem", ed_private, rsa_public), start=1):
        response = await client.post("/api/producers/data-integrity/keys", json={
            "key_id": f"BAD-{index}", "public_key_pem": pem,
        })
        assert response.status_code == 422


@pytest.mark.anyio
async def test_key_id_change_and_cross_producer_key_reuse_conflict(client):
    key_a = Ed25519PrivateKey.generate()
    key_b = Ed25519PrivateKey.generate()
    await register_identity(client, key_a)
    changed = await client.post("/api/producers/data-integrity/keys", json={
        "key_id": "KEY-DATA-001", "public_key_pem": public_pem(key_b),
    })
    assert changed.status_code == 409
    await client.post("/api/producers", json=producer_body("model-integrity", "model_integrity"))
    reused = await client.post("/api/producers/model-integrity/keys", json={
        "key_id": "KEY-MODEL-001", "public_key_pem": public_pem(key_a),
    })
    assert reused.status_code == 409


@pytest.mark.anyio
async def test_valid_signed_run_is_ingested_and_audited(client, app):
    private_key = Ed25519PrivateKey.generate()
    await register_identity(client, private_key)
    run = run_body()
    response = await client.post(
        "/api/integration/signed-runs", json=signed_body(run, "KEY-DATA-001", private_key)
    )
    assert response.status_code == 200 and response.json()["result"] == "CREATED"
    assert (await client.get("/api/evidence/F-DATA-001")).status_code == 200
    assert (await client.get("/api/integration/runs/RUN-DATA-0001")).status_code == 200
    assert (await client.get("/api/producers/data-integrity")).json()["status"] == "APPROVED"
    assert (await client.get("/api/audit/verify")).json()["status"] == "VALID"
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModuleProducerRecord)) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("tamper", ["reason", "category", "producer_version", "order", "asset_id"])
async def test_signed_payload_tampering_fails_without_mutation(client, app, tamper):
    private_key = Ed25519PrivateKey.generate()
    await register_identity(client, private_key)
    first = dataset_finding()
    second = deepcopy(first)
    second["finding_id"] = "F-DATA-002"
    second["asset_id"] = "sample:img_0043"
    run = run_body(findings=[first, second])
    wrapper = signed_body(run, "KEY-DATA-001", private_key)
    if tamper == "order":
        wrapper["run"]["findings"].reverse()
    elif tamper == "producer_version":
        wrapper["run"]["producer_version"] = "0.2.0"
    else:
        wrapper["run"]["findings"][0][tamper] = "TAMPERED"
    assert (await client.post("/api/integration/signed-runs", json=wrapper)).status_code == 401
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FindingRecord)) == 0
        assert session.scalar(select(func.count()).select_from(ModuleRunRecord)) == 0
        success_types = ("MODULE_RUN_INGESTED", "MODULE_RUN_AUTHENTICATED", "EVIDENCE_CREATED")
        assert session.scalar(select(func.count()).select_from(AuditLogRecord).where(
            AuditLogRecord.event_type.in_(success_types)
        )) == 0


@pytest.mark.anyio
async def test_signature_tamper_and_wrong_signing_key_fail(client, app):
    key_a = Ed25519PrivateKey.generate()
    key_b = Ed25519PrivateKey.generate()
    await register_identity(client, key_a)
    run = run_body()
    random_signature = base64.b64encode(b"x" * 64).decode("ascii")
    bad = {"run": run, "key_id": "KEY-DATA-001", "signature": random_signature}
    wrong = signed_body(run, "KEY-DATA-001", key_b)
    assert (await client.post("/api/integration/signed-runs", json=bad)).status_code == 401
    assert (await client.post("/api/integration/signed-runs", json=wrong)).status_code == 401
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FindingRecord)) == 0


@pytest.mark.anyio
async def test_malformed_signature_encoding_is_schema_error(client):
    run = run_body()
    response = await client.post("/api/integration/signed-runs", json={
        "run": run, "key_id": "KEY-DATA-001", "signature": "not-base64!",
    })
    assert response.status_code == 422


@pytest.mark.anyio
async def test_unknown_producer_and_unknown_key_fail_closed(client):
    private_key = Ed25519PrivateKey.generate()
    run = run_body()
    unknown_producer = signed_body(run, "UNKNOWN", private_key)
    assert (await client.post("/api/integration/signed-runs", json=unknown_producer)).status_code == 403
    await client.post("/api/producers", json=producer_body())
    unknown_key = signed_body(run, "UNKNOWN", private_key)
    assert (await client.post("/api/integration/signed-runs", json=unknown_key)).status_code == 403


@pytest.mark.anyio
async def test_producer_module_binding_fails_before_ingestion(client):
    private_key = Ed25519PrivateKey.generate()
    await register_identity(client, private_key)
    run = run_body(module="model_integrity", findings=[model_finding()])
    response = await client.post(
        "/api/integration/signed-runs", json=signed_body(run, "KEY-DATA-001", private_key)
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_revoked_producer_fails_but_historical_run_remains(client):
    private_key = Ed25519PrivateKey.generate()
    await register_identity(client, private_key)
    first_run = run_body()
    await client.post("/api/integration/signed-runs", json=signed_body(first_run, "KEY-DATA-001", private_key))
    assert (await client.post("/api/producers/data-integrity/revoke")).json()["producer"]["status"] == "REVOKED"
    second_run = run_body("RUN-DATA-0002", findings=[{**dataset_finding(), "finding_id": "F-DATA-002"}])
    assert (await client.post(
        "/api/integration/signed-runs", json=signed_body(second_run, "KEY-DATA-001", private_key)
    )).status_code == 403
    assert (await client.get("/api/integration/runs/RUN-DATA-0001")).status_code == 200


@pytest.mark.anyio
async def test_key_revocation_and_rotation(client):
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    await register_identity(client, old_key, key_id="KEY-OLD")
    assert (await client.post("/api/producers/data-integrity/keys", json={
        "key_id": "KEY-NEW", "public_key_pem": public_pem(new_key),
    })).status_code == 200
    assert (await client.post("/api/producers/data-integrity/keys/KEY-OLD/revoke")).json()["key"]["status"] == "REVOKED"
    run = run_body()
    assert (await client.post(
        "/api/integration/signed-runs", json=signed_body(run, "KEY-OLD", old_key)
    )).status_code == 403
    accepted = await client.post(
        "/api/integration/signed-runs", json=signed_body(run, "KEY-NEW", new_key)
    )
    assert accepted.status_code == 200 and accepted.json()["result"] == "CREATED"


@pytest.mark.anyio
async def test_exact_signed_rerun_is_idempotent_without_duplicate_success_audit(client, app):
    private_key = Ed25519PrivateKey.generate()
    await register_identity(client, private_key)
    body = signed_body(run_body(), "KEY-DATA-001", private_key)
    first = await client.post("/api/integration/signed-runs", json=body)
    second = await client.post("/api/integration/signed-runs", json=body)
    assert first.json()["result"] == "CREATED" and second.json()["result"] == "EXISTS"
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FindingRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ModuleRunRecord)) == 1
        for event_type in ("MODULE_RUN_INGESTED", "MODULE_RUN_AUTHENTICATED"):
            assert session.scalar(select(func.count()).select_from(AuditLogRecord).where(
                AuditLogRecord.event_type == event_type
            )) == 1


@pytest.mark.anyio
async def test_authenticated_immutable_run_conflict_returns_409(client):
    private_key = Ed25519PrivateKey.generate()
    await register_identity(client, private_key)
    original = run_body()
    await client.post("/api/integration/signed-runs", json=signed_body(original, "KEY-DATA-001", private_key))
    changed = deepcopy(original)
    changed["producer_version"] = "0.2.0"
    response = await client.post(
        "/api/integration/signed-runs", json=signed_body(changed, "KEY-DATA-001", private_key)
    )
    assert response.status_code == 409
    assert (await client.get("/api/evidence")).json()["total"] == 1


@pytest.mark.anyio
async def test_request_hash_is_anchored_in_authenticated_audit(client, app):
    private_key = Ed25519PrivateKey.generate()
    await register_identity(client, private_key)
    run = run_body()
    await client.post("/api/integration/signed-runs", json=signed_body(run, "KEY-DATA-001", private_key))
    stored = (await client.get("/api/integration/runs/RUN-DATA-0001")).json()
    event = (await client.get("/api/audit?event_type=MODULE_RUN_INGESTED")).json()["items"][0]
    assert event["payload"]["request_hash"] == stored["request_hash"]
    assert event["payload"]["request_hash"] == sha256_bytes(canonical_json_bytes(run))
    assert event["payload"]["authenticated"] is True
    assert "signature" not in event["payload"] and "public_key_pem" not in event["payload"]
    assert (await client.get("/api/audit/verify")).json()["status"] == "VALID"


@pytest.mark.anyio
async def test_all_four_modules_authenticate_and_complete_summary(client):
    cases = [
        ("data-integrity", "dataset_integrity", "KEY-DATA", dataset_finding()),
        ("model-integrity", "model_integrity", "KEY-MODEL", model_finding()),
        ("security-backend", "inference_integrity", "KEY-INF", inference_finding()),
        ("distribution-shift", "distribution_shift", "KEY-DRIFT", drift_finding()),
    ]
    for index, (producer, module, key_id, finding) in enumerate(cases, start=1):
        private_key = Ed25519PrivateKey.generate()
        await register_identity(client, private_key, producer, module, key_id)
        run = run_body(f"RUN-{index}", module, producer, [finding])
        response = await client.post(
            "/api/integration/signed-runs", json=signed_body(run, key_id, private_key)
        )
        assert response.status_code == 200
    summary = (await client.get("/api/summary")).json()
    assert summary["overall"]["assessment_coverage"] == 1.0
    assert summary["overall"]["score_status"] == "COMPLETE"
    assert (await client.get("/api/audit/verify")).json()["status"] == "VALID"


def test_finding_schema_v1_remains_frozen():
    assert list(Finding.model_fields) == [
        "finding_id", "module", "asset_type", "asset_id", "category",
        "severity", "confidence", "reason", "evidence", "recommendation", "limitations",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize('revoked', ['producer', 'key'])
async def test_persistence_rechecks_current_approval(client, app, revoked):
    from backend.schemas.module_auth import SignedModuleRunSubmission
    from backend.services.integration_service import RunAuthentication
    from backend.services.module_auth_service import ModuleAuthorizationDenied
    key = Ed25519PrivateKey.generate()
    await register_identity(client, key)
    signed = SignedModuleRunSubmission.model_validate(signed_body(run_body(), 'KEY-DATA-001', key))
    with app.state.session_factory() as session:
        auth = app.state.module_auth_service.authenticate(signed, session)
        session.rollback()
        if revoked == 'producer':
            app.state.producer_service.revoke_producer(auth.producer_id, session)
        else:
            app.state.producer_service.revoke_key(auth.producer_id, auth.key_id, session)
        session.rollback()
        with pytest.raises(ModuleAuthorizationDenied):
            app.state.integration_service.ingest_run(signed.run, session, RunAuthentication(
                mode='ED25519', producer_id=auth.producer_id, key_id=auth.key_id,
                key_fingerprint=auth.key_fingerprint))
        assert session.scalar(select(func.count()).select_from(ModuleRunRecord)) == 0
        assert session.scalar(select(func.count()).select_from(FindingRecord)) == 0
