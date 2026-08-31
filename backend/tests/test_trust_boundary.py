from __future__ import annotations

import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, select

from backend.core.config import Settings
from backend.database.models import AuditLogRecord, FindingRecord, ModuleRunRecord
from backend.main import create_app
from backend.schemas.evidence import Finding
from backend.tests.test_integration_gate import run_submission
from backend.tests.test_module_auth import (
    producer_body,
    public_pem,
    run_body,
    signed_body,
)

ADMIN = "TEST-ADMIN-SUPER-SECRET-DO-NOT-LEAK"
INTERNAL = "TEST-INTERNAL-SUPER-SECRET-DO-NOT-LEAK"


def boundary_app(tmp_path, *, admin=ADMIN, internal=INTERNAL, allow=False):
    return create_app(Settings(
        data_dir=tmp_path / "data",
        key_dir=tmp_path / "keys",
        admin_bearer_token=admin,
        internal_ingest_bearer_token=internal,
        allow_unsigned_ingestion=allow,
    ))


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def register(client, private_key, producer_id="data-integrity", module="dataset_integrity", key_id="KEY-DATA-001"):
    assert (await client.post(
        "/api/producers", json=producer_body(producer_id, module), headers=auth(ADMIN)
    )).status_code == 200
    assert (await client.post(
        f"/api/producers/{producer_id}/keys",
        json={"key_id": key_id, "public_key_pem": public_pem(private_key)},
        headers=auth(ADMIN),
    )).status_code == 200


@pytest.mark.anyio
async def test_admin_mutations_fail_closed_and_valid_token_preserves_semantics(tmp_path):
    app = boundary_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        body = producer_body()
        assert (await client.post("/api/producers", json=body)).status_code == 401
        assert (await client.post("/api/producers", json=body, headers=auth("wrong"))).status_code == 401
        created = await client.post("/api/producers", json=body, headers=auth(ADMIN))
        assert created.status_code == 200 and created.json()["result"] == "CREATED"
        key = Ed25519PrivateKey.generate()
        key_url = "/api/producers/data-integrity/keys"
        key_body = {"key_id": "KEY-DATA-001", "public_key_pem": public_pem(key)}
        assert (await client.post(key_url, json=key_body)).status_code == 401
        assert (await client.post(key_url, json=key_body, headers=auth(ADMIN))).status_code == 200
        assert (await client.post(f"{key_url}/KEY-DATA-001/revoke")).status_code == 401
        assert (await client.post(f"{key_url}/KEY-DATA-001/revoke", headers=auth(ADMIN))).status_code == 200
        assert (await client.post("/api/producers/data-integrity/revoke")).status_code == 401
        assert (await client.post("/api/producers/data-integrity/revoke", headers=auth(ADMIN))).status_code == 200

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditLogRecord)) == 4
        serialized = " ".join(row.payload_json for row in session.scalars(select(AuditLogRecord)))
        assert ADMIN not in serialized
    assert ADMIN not in json.dumps(app.openapi())


@pytest.mark.anyio
async def test_admin_unconfigured_is_503_without_mutation(tmp_path):
    app = boundary_app(tmp_path, admin=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/producers", json=producer_body())
        assert response.status_code == 503
        assert response.json()["detail"] == "Administrative authentication is not configured"
        assert (await client.get("/api/producers")).json()["total"] == 0


@pytest.mark.anyio
async def test_unsigned_and_direct_ingestion_are_disabled_by_default(tmp_path):
    app = boundary_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/integration/runs", json=run_submission())).status_code == 403
        assert (await client.post("/api/evidence", json=run_submission()["findings"][0])).status_code == 403
        assert (await client.get("/api/evidence")).json()["total"] == 0
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModuleRunRecord)) == 0
        assert session.scalar(select(func.count()).select_from(FindingRecord)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLogRecord)) == 0


@pytest.mark.anyio
async def test_enabled_internal_ingestion_requires_configured_valid_token(tmp_path):
    unconfigured = boundary_app(tmp_path / "unconfigured", internal=None, allow=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=unconfigured), base_url="http://test") as client:
        assert (await client.post("/api/integration/runs", json=run_submission())).status_code == 503

    app = boundary_app(tmp_path / "configured", allow=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/integration/runs", json=run_submission())).status_code == 401
        assert (await client.post("/api/integration/runs", json=run_submission(), headers=auth("wrong"))).status_code == 401
        created = await client.post("/api/integration/runs", json=run_submission(), headers=auth(INTERNAL))
        assert created.json()["result"] == "CREATED"
        details = (await client.get("/api/integration/runs/RUN-DATA-0001")).json()
        assert details["authentication"]["authenticated"] is False
        assert details["authentication"]["mode"] == "TRUSTED_INTERNAL"


@pytest.mark.anyio
async def test_direct_finding_is_excluded_then_promoted_by_signed_attestation(tmp_path):
    app = boundary_app(tmp_path, allow=True)
    finding = run_submission()["findings"][0]
    private_key = Ed25519PrivateKey.generate()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/evidence", json=finding)).status_code == 401
        assert (await client.post("/api/evidence", json=finding, headers=auth("wrong"))).status_code == 401
        assert (await client.post("/api/evidence", json=finding, headers=auth(INTERNAL))).json()["result"] == "CREATED"
        trusted = (await client.get("/api/summary")).json()
        assert trusted["trust_scope"] == "authenticated"
        assert trusted["trusted_finding_count"] == 0
        assert trusted["excluded_untrusted_finding_count"] == 1
        assert trusted["overall"]["assessment_coverage"] == 0.0
        assert (await client.get("/api/summary?trust_scope=all")).json()["finding_counts"]["total"] == 1

        await register(client, private_key)
        signed = signed_body(run_body("RUN-SIGNED", findings=[finding]), "KEY-DATA-001", private_key)
        response = await client.post("/api/integration/signed-runs", json=signed)
        assert response.status_code == 200 and response.json()["result"] == "CREATED"
        promoted = (await client.get("/api/summary")).json()
        assert promoted["trusted_finding_count"] == 1
        assert promoted["excluded_untrusted_finding_count"] == 0
        assert promoted["overall"]["assessment_coverage"] == 0.25


@pytest.mark.anyio
async def test_signed_provenance_hash_anchor_and_exact_rerun_audit(tmp_path):
    app = boundary_app(tmp_path)
    private_key = Ed25519PrivateKey.generate()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await register(client, private_key)
        body = signed_body(run_body(), "KEY-DATA-001", private_key)
        assert (await client.post("/api/integration/signed-runs", json=body)).json()["result"] == "CREATED"
        assert (await client.post("/api/integration/signed-runs", json=body)).json()["result"] == "EXISTS"
        details = (await client.get("/api/integration/runs/RUN-DATA-0001")).json()
        provenance = details["authentication"]
        assert provenance["authenticated"] is True and provenance["mode"] == "ED25519"
        assert provenance["key_id"] == "KEY-DATA-001"
        assert provenance["key_fingerprint"]
        assert provenance["request_hash"] == details["request_hash"]
        ingested = (await client.get("/api/audit?event_type=MODULE_RUN_INGESTED")).json()
        authenticated = (await client.get("/api/audit?event_type=MODULE_RUN_AUTHENTICATED")).json()
        evidence = (await client.get("/api/audit?event_type=EVIDENCE_CREATED")).json()
        assert ingested["total"] == authenticated["total"] == evidence["total"] == 1
        assert ingested["items"][0]["payload"]["request_hash"] == details["request_hash"]
        assert (await client.get("/api/audit/verify")).json()["status"] == "VALID"


@pytest.mark.anyio
async def test_validation_errors_are_bounded_and_omit_input(tmp_path):
    app = boundary_app(tmp_path)
    payload = run_submission()
    payload["findings"] = [
        {**payload["findings"][0], "finding_id": f"F-LIMIT-{index:03d}"}
        for index in range(101)
    ]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/integration/signed-runs", json={"run": payload, "key_id": "K", "signature": "x"})
        assert response.status_code == 422
        result = response.json()
        assert result["errors_returned"] <= 20
        assert result["error_count"] >= result["errors_returned"]
        assert all(set(error) == {"loc", "msg", "type"} for error in result["detail"])
        assert "F-LIMIT-100" not in response.text
        assert len(response.content) < 10_000


def test_finding_contract_and_openapi_security(tmp_path):
    assert list(Finding.model_fields) == [
        "finding_id", "module", "asset_type", "asset_id", "category", "severity",
        "confidence", "reason", "evidence", "recommendation", "limitations",
    ]
    document = boundary_app(tmp_path).openapi()
    for path in ("/api/integration/signed-runs", "/api/integration/runs", "/api/evidence", "/api/producers"):
        assert path in document["paths"]
    assert document["paths"]["/api/producers"]["post"]["security"]
    assert document["paths"]["/api/integration/runs"]["post"]["security"]
    assert "security" not in document["paths"]["/api/integration/signed-runs"]["post"]
