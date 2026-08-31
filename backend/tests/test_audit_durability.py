from __future__ import annotations

import copy

import pytest
from sqlalchemy import delete, func, select, text

from backend.database.models import AuditLogRecord, AuditOutboxRecord, AssessmentRecord
from backend.services.audit_outbox_service import OutboxIntegrityError


def _stage(app, key="TEST:1", payload=None):
    with app.state.session_factory() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        app.state.audit_outbox_service.stage(
            session, key, "TEST_STATE_CHANGED", "test", key, payload or {"value": 1}
        )
        session.commit()


def test_pending_recovery_exactly_once_and_order(app):
    _stage(app, "TEST:1")
    _stage(app, "TEST:2", {"value": 2})
    assert app.state.audit_outbox_service.status()["pending_count"] == 2
    assert app.state.audit_outbox_service.drain_pending() == 2
    assert app.state.audit_outbox_service.drain_pending() == 0
    with app.state.session_factory() as session:
        rows = list(session.scalars(select(AuditLogRecord).order_by(AuditLogRecord.sequence)))
        assert [row.asset_id for row in rows] == ["TEST:1", "TEST:2"]
        assert session.scalar(select(func.count()).select_from(AuditLogRecord)) == 2
        assert all(row.status == "DELIVERED" for row in session.scalars(select(AuditOutboxRecord)))


def test_event_key_idempotency_and_conflict(app):
    _stage(app)
    _stage(app)
    with pytest.raises(OutboxIntegrityError):
        _stage(app, payload={"value": 999})


@pytest.mark.parametrize("column,value", [("payload_json", "{}"), ("payload_hash", "0" * 64)])
def test_pending_tamper_refuses_delivery(app, column, value):
    _stage(app)
    with app.state.engine.begin() as connection:
        connection.execute(text(f"UPDATE audit_outbox SET {column}=:value"), {"value": value})
    with pytest.raises(OutboxIntegrityError):
        app.state.audit_outbox_service.drain_pending()
    assert app.state.audit_outbox_service.status()["pending_count"] == 1


@pytest.mark.anyio
async def test_assessment_state_and_intent_are_delivered(client, app):
    response = await client.post("/api/assessments", json={
        "assessment_id": "AST-M15", "name": "M15", "metadata": {}})
    assert response.status_code == 201
    with app.state.session_factory() as session:
        assert session.get(AssessmentRecord, "AST-M15") is not None
        row = session.scalar(select(AuditOutboxRecord).where(
            AuditOutboxRecord.event_key == "ASSESSMENT_CREATED:AST-M15"))
        assert row is not None and row.status == "DELIVERED"


@pytest.mark.anyio
async def test_checkpoint_tail_truncation_and_external_bundle(client, app):
    for number in range(1, 6):
        _stage(app, f"TAIL:{number}", {"number": number})
    app.state.audit_outbox_service.drain_pending()
    created = await client.post("/api/audit/checkpoints")
    assert created.status_code == 200
    bundle = created.json()["bundle"]
    assert (await client.post("/api/audit/checkpoints/verify", json=bundle)).json()["status"] == "VALID"
    with app.state.engine.begin() as connection:
        connection.execute(text("DELETE FROM audit_outbox_deliveries WHERE audit_id IN (SELECT audit_id FROM audit_log WHERE sequence >= 4)"))
        connection.execute(delete(AuditLogRecord).where(AuditLogRecord.sequence >= 4))
    assert (await client.get("/api/audit/verify")).json()["status"] == "VALID"
    verified = await client.post("/api/audit/checkpoints/verify", json=bundle)
    assert verified.json()["status"] == "AUDIT_HISTORY_SHORTER_THAN_CHECKPOINT"


@pytest.mark.anyio
async def test_checkpoint_tamper_signature_and_idempotency(client, app):
    _stage(app); app.state.audit_outbox_service.drain_pending()
    first = (await client.post("/api/audit/checkpoints")).json()
    second = (await client.post("/api/audit/checkpoints")).json()
    assert first["result"] == "CREATED" and second["result"] == "EXISTS"
    tampered = copy.deepcopy(first["bundle"])
    tampered["checkpoint"]["audit_sequence"] += 1
    result = await client.post("/api/audit/checkpoints/verify", json=tampered)
    assert result.json()["status"] == "INVALID_CHECKPOINT_HASH"
    tampered = copy.deepcopy(first["bundle"])
    tampered["signature"] = "AAAA"
    result = await client.post("/api/audit/checkpoints/verify", json=tampered)
    assert result.json()["status"] == "INVALID_SIGNATURE"


@pytest.mark.anyio
async def test_checkpoint_chain_links_and_stored_tamper(client, app):
    _stage(app, "A:1"); app.state.audit_outbox_service.drain_pending()
    a = (await client.post("/api/audit/checkpoints")).json()["bundle"]
    _stage(app, "B:1"); app.state.audit_outbox_service.drain_pending()
    b = (await client.post("/api/audit/checkpoints")).json()["bundle"]
    assert b["checkpoint"]["previous_checkpoint_hash"] == a["checkpoint_hash"]
    with app.state.engine.begin() as connection:
        connection.execute(text("UPDATE audit_checkpoints SET checkpoint_hash=:hash WHERE checkpoint_id=:id"),
                           {"hash": "f" * 64, "id": a["checkpoint"]["checkpoint_id"]})
    result = await client.get(f"/api/audit/checkpoints/{b['checkpoint']['checkpoint_id']}/verify")
    assert result.json()["status"] == "CHECKPOINT_CHAIN_INVALID"
