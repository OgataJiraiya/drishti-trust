from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import delete, select, update

from backend.core.audit_chain import GENESIS_HASH
from backend.database.models import AcceptedInferenceRecord, AuditLogRecord
from backend.main import create_app


def append(app, event_type="TEST_EVENT", asset_id="asset-1", payload=None):
    with app.state.session_factory() as session:
        return app.state.audit_service.append(
            session, event_type, "test_asset", asset_id, payload or {"safe": True}
        )


def verify(app):
    with app.state.session_factory() as session:
        return app.state.audit_service.verify(session)


def test_first_event_uses_documented_genesis_and_clean_chain(app):
    event = append(app)
    result = verify(app)
    assert event.audit_id == "AUD-00000001"
    assert event.sequence == 1
    assert event.previous_hash == GENESIS_HASH
    assert result.valid is True and result.status == "VALID"
    assert result.records_checked == 1


def test_multiple_events_are_linked_in_order(app):
    first = append(app, asset_id="one")
    second = append(app, asset_id="two")
    third = append(app, asset_id="three")
    assert second.previous_hash == first.current_hash
    assert third.previous_hash == second.current_hash
    assert verify(app).records_checked == 3


@pytest.mark.anyio
async def test_audit_persists_after_reopen(app, client, test_settings):
    append(app, "PERSISTED")
    reopened = create_app(test_settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=reopened), base_url="http://reopened") as new_client:
        result = (await new_client.get("/api/audit/verify")).json()
    assert result["status"] == "VALID" and result["records_checked"] == 1


def test_modified_payload_detects_earliest_record(app):
    append(app, asset_id="one")
    append(app, asset_id="two")
    with app.state.session_factory() as session:
        session.execute(update(AuditLogRecord).where(AuditLogRecord.audit_id == "AUD-00000001").values(payload_json='{"safe":false}'))
        session.commit()
    result = verify(app)
    assert result.status == "COMPROMISED"
    assert result.first_broken_audit_id == "AUD-00000001"
    assert result.findings[0].attack_class == "AUDIT_RECORD_TAMPERING"
    assert {"stored_hash", "recomputed_hash"} <= result.findings[0].evidence.keys()


def test_modified_previous_hash_detects_chain_link_failure(app):
    append(app, asset_id="one")
    append(app, asset_id="two")
    with app.state.session_factory() as session:
        session.execute(update(AuditLogRecord).where(AuditLogRecord.audit_id == "AUD-00000002").values(previous_hash="f" * 64))
        session.commit()
    result = verify(app)
    assert result.first_broken_audit_id == "AUD-00000002"
    assert result.checks["chain_link"] == "INVALID"
    assert result.findings[0].attack_class == "AUDIT_CHAIN_LINK_FAILURE"


def test_modified_current_hash_detects_record_tampering(app):
    append(app, asset_id="one")
    append(app, asset_id="two")
    with app.state.session_factory() as session:
        session.execute(update(AuditLogRecord).where(AuditLogRecord.audit_id == "AUD-00000001").values(current_hash="e" * 64))
        session.commit()
    result = verify(app)
    assert result.first_broken_audit_id == "AUD-00000001"
    assert result.checks["record_hash"] == "INVALID"


def test_modified_sequence_detects_sequence_and_hash_failure(app):
    append(app, asset_id="one")
    append(app, asset_id="two")
    with app.state.session_factory() as session:
        session.execute(update(AuditLogRecord).where(AuditLogRecord.audit_id == "AUD-00000002").values(sequence=9))
        session.commit()
    result = verify(app)
    assert result.status == "COMPROMISED"
    assert result.checks["sequence"] == "INVALID"
    assert result.findings[0].attack_class == "AUDIT_SEQUENCE_VIOLATION"


def test_deleting_interior_record_breaks_chain(app):
    append(app, asset_id="one")
    append(app, asset_id="two")
    append(app, asset_id="three")
    with app.state.session_factory() as session:
        session.execute(delete(AuditLogRecord).where(AuditLogRecord.audit_id == "AUD-00000002"))
        session.commit()
    result = verify(app)
    assert result.status == "COMPROMISED"
    assert result.first_broken_audit_id == "AUD-00000003"
    assert result.checks["chain_link"] == "INVALID"


def test_reordered_sequences_break_chain(app):
    append(app, asset_id="one")
    append(app, asset_id="two")
    append(app, asset_id="three")
    with app.state.session_factory() as session:
        session.execute(update(AuditLogRecord).where(AuditLogRecord.sequence == 2).values(sequence=99))
        session.execute(update(AuditLogRecord).where(AuditLogRecord.sequence == 3).values(sequence=2))
        session.execute(update(AuditLogRecord).where(AuditLogRecord.sequence == 99).values(sequence=3))
        session.commit()
    result = verify(app)
    assert result.status == "COMPROMISED"
    assert result.checks["chain_link"] == "INVALID"


def test_concurrent_appends_have_unique_monotonic_sequences(app):
    def write(index):
        return append(app, asset_id=f"concurrent-{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(write, range(16)))
    assert sorted(record.sequence for record in records) == list(range(1, 17))
    assert len({record.current_hash for record in records}) == 16
    assert verify(app).status == "VALID"


@pytest.mark.anyio
async def test_security_actions_create_expected_audit_events(client, creation_payload):
    model_digest = creation_payload["model_sha256"]
    await client.post("/api/models/register", json={
        "model_id": "MODEL-001", "display_name": "Detector", "expected_sha256": model_digest
    })
    receipt = (await client.post("/api/inference/receipt", json=creation_payload)).json()
    body = {"receipt": receipt, "artifacts": {
        "input_base64": creation_payload["input_base64"], "model_sha256": model_digest,
        "preprocessing": creation_payload["preprocessing"], "config": creation_payload["config"],
        "output": creation_payload["output"],
    }}
    await client.post("/api/inference/accept", json=body)
    await client.post("/api/inference/accept", json=body)
    await client.post("/api/models/verify", json={"model_id": "MODEL-001", "observed_sha256": "f" * 64})
    events = (await client.get("/api/audit?page_size=100")).json()["items"]
    assert [event["event_type"] for event in events] == [
        "MODEL_REGISTERED", "INFERENCE_RECEIPT_CREATED", "INFERENCE_ACCEPTED",
        "REPLAY_DETECTED", "MODEL_SUBSTITUTION_DETECTED",
    ]
    assert (await client.get("/api/audit/verify")).json()["status"] == "VALID"


@pytest.mark.anyio
async def test_model_verification_and_revocation_are_audited(client):
    digest = "a" * 64
    await client.post("/api/models/register", json={"model_id": "M", "display_name": "M", "expected_sha256": digest})
    await client.post("/api/models/verify", json={"model_id": "M", "observed_sha256": digest})
    await client.post("/api/models/M/revoke")
    events = (await client.get("/api/audit?page_size=100")).json()["items"]
    assert [event["event_type"] for event in events] == ["MODEL_REGISTERED", "MODEL_VERIFIED", "MODEL_REVOKED"]


@pytest.mark.anyio
async def test_receipt_verification_audits_without_mutating_replay_state(client, app, creation_payload):
    receipt = (await client.post("/api/inference/receipt", json=creation_payload)).json()
    body = {"receipt": receipt, "artifacts": {
        "input_base64": creation_payload["input_base64"], "model_sha256": creation_payload["model_sha256"],
        "preprocessing": creation_payload["preprocessing"], "config": creation_payload["config"],
        "output": creation_payload["output"],
    }}
    for _ in range(3):
        assert (await client.post("/api/inference/verify", json=body)).json()["status"] == "ACCEPT"
    with app.state.session_factory() as session:
        assert len(list(session.scalars(select(AcceptedInferenceRecord)))) == 0
    filtered = (await client.get("/api/audit?event_type=INFERENCE_VERIFIED")).json()
    assert filtered["total"] == 3


@pytest.mark.anyio
async def test_output_and_signature_failures_are_audited(client, creation_payload):
    receipt = (await client.post("/api/inference/receipt", json=creation_payload)).json()
    artifacts = {
        "input_base64": creation_payload["input_base64"], "model_sha256": creation_payload["model_sha256"],
        "preprocessing": creation_payload["preprocessing"], "config": creation_payload["config"],
        "output": {"class": "tree", "confidence": 0.94},
    }
    await client.post("/api/inference/verify", json={"receipt": receipt, "artifacts": artifacts})
    receipt["signature"] = "AAAA"
    await client.post("/api/inference/verify", json={"receipt": receipt, "artifacts": artifacts})
    event_types = [item["event_type"] for item in (await client.get("/api/audit?page_size=100")).json()["items"]]
    assert "OUTPUT_TAMPERING_DETECTED" in event_types
    assert "SIGNATURE_INVALID" in event_types


@pytest.mark.anyio
async def test_audit_api_pagination_detail_and_detailed_verification(client, app):
    for index in range(5):
        append(app, asset_id=f"asset-{index}")
    page = (await client.get("/api/audit?page=2&page_size=2&asset_type=test_asset")).json()
    assert page["total"] == 5 and [item["sequence"] for item in page["items"]] == [3, 4]
    detail = (await client.get("/api/audit/AUD-00000003")).json()
    assert detail["asset_id"] == "asset-2"
    result = (await client.get("/api/audit/verify")).json()
    assert result["valid"] is True
    assert result["checks"] == {"genesis": "VALID", "sequence": "VALID", "record_hash": "VALID", "chain_link": "VALID"}


def test_audit_payload_does_not_expose_private_key(app):
    append(app, payload={"key_id": "public-id", "result": "VALID"})
    with app.state.session_factory() as session:
        stored = session.scalar(select(AuditLogRecord))
    assert stored is not None
    assert "PRIVATE KEY" not in stored.payload_json
    assert "private_key" not in stored.payload_json


def test_tail_truncation_is_honestly_unsupported_without_checkpoint(app):
    append(app, asset_id="one")
    append(app, asset_id="two")
    with app.state.session_factory() as session:
        session.execute(delete(AuditLogRecord).where(AuditLogRecord.audit_id == "AUD-00000002"))
        session.commit()
    # A remaining valid prefix is indistinguishable from the original shorter chain.
    result = verify(app)
    assert result.status == "VALID" and result.records_checked == 1
