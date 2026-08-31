"""Transactional audit intent staging and exactly-once ordered materialization."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.core.audit_chain import GENESIS_HASH, compute_audit_hash
from backend.core.canonical import canonical_json_text
from backend.core.hashing import sha256_bytes
from backend.database.models import (
    AuditLogRecord, AuditOutboxDeliveryRecord, AuditOutboxRecord,
)


class OutboxIntegrityError(RuntimeError):
    pass


class AuditOutboxService:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory
        self.last_error: str | None = None

    @staticmethod
    def stage(session: Session, event_key: str, event_type: str, asset_type: str,
              asset_id: str, payload: dict[str, Any]) -> AuditOutboxRecord:
        body = {"event_type": event_type, "asset_type": asset_type,
                "asset_id": asset_id, "payload": payload}
        payload_json = canonical_json_text(body)
        payload_hash = sha256_bytes(payload_json.encode())
        existing = session.scalar(select(AuditOutboxRecord).where(
            AuditOutboxRecord.event_key == event_key))
        if existing is not None:
            if existing.payload_hash != payload_hash or existing.payload_json != payload_json:
                raise OutboxIntegrityError("event_key is already bound to different audit intent")
            return existing
        record = AuditOutboxRecord(
            event_key=event_key, event_type=event_type, asset_type=asset_type,
            asset_id=asset_id, payload_json=payload_json, payload_hash=payload_hash,
            status="PENDING",
        )
        session.add(record)
        return record

    def drain_pending(self, limit: int | None = None) -> int:
        delivered = 0
        while limit is None or delivered < limit:
            with self.factory() as session:
                try:
                    session.execute(text("BEGIN IMMEDIATE"))
                    row = session.scalar(select(AuditOutboxRecord).where(
                        AuditOutboxRecord.status == "PENDING"
                    ).order_by(AuditOutboxRecord.outbox_id).limit(1))
                    if row is None:
                        session.rollback()
                        self.last_error = None
                        return delivered
                    recomputed = sha256_bytes(row.payload_json.encode())
                    if recomputed != row.payload_hash:
                        raise OutboxIntegrityError("pending audit intent payload hash mismatch")
                    try:
                        body = json.loads(row.payload_json)
                    except json.JSONDecodeError as exc:
                        raise OutboxIntegrityError("pending audit intent is malformed") from exc
                    expected = {"event_type": row.event_type, "asset_type": row.asset_type,
                                "asset_id": row.asset_id, "payload": body.get("payload")}
                    if body != expected or canonical_json_text(body) != row.payload_json:
                        raise OutboxIntegrityError("pending audit intent columns/body mismatch")
                    latest = session.scalar(select(AuditLogRecord).order_by(
                        AuditLogRecord.sequence.desc()).limit(1))
                    sequence = latest.sequence + 1 if latest else 1
                    audit_id = f"AUD-{sequence:08d}"
                    previous_hash = latest.current_hash if latest else GENESIS_HASH
                    timestamp = datetime.now(timezone.utc)
                    current_hash = compute_audit_hash(
                        audit_id=audit_id, sequence=sequence, event_type=row.event_type,
                        asset_type=row.asset_type, asset_id=row.asset_id,
                        payload=body["payload"], timestamp=timestamp,
                        previous_hash=previous_hash,
                    )
                    session.add(AuditLogRecord(
                        audit_id=audit_id, sequence=sequence, event_type=row.event_type,
                        asset_type=row.asset_type, asset_id=row.asset_id,
                        payload_json=canonical_json_text(body["payload"]), timestamp=timestamp,
                        previous_hash=previous_hash, current_hash=current_hash,
                    ))
                    session.flush()
                    session.add(AuditOutboxDeliveryRecord(outbox_id=row.outbox_id, audit_id=audit_id))
                    row.status = "DELIVERED"
                    row.delivered_at = timestamp
                    row.delivered_audit_id = audit_id
                    session.commit()
                    delivered += 1
                    self.last_error = None
                except Exception as exc:
                    session.rollback()
                    self.last_error = type(exc).__name__
                    raise
        return delivered

    def status(self) -> dict[str, Any]:
        with self.factory() as session:
            pending = int(session.scalar(select(func.count()).select_from(AuditOutboxRecord)
                          .where(AuditOutboxRecord.status == "PENDING")) or 0)
            delivered = int(session.scalar(select(func.count()).select_from(AuditOutboxRecord)
                            .where(AuditOutboxRecord.status == "DELIVERED")) or 0)
            oldest = session.scalar(select(func.min(AuditOutboxRecord.created_at)).where(
                AuditOutboxRecord.status == "PENDING"))
            return {"pending_count": pending, "delivered_count": delivered,
                    "oldest_pending_at": oldest, "healthy": self.last_error is None,
                    "last_error": self.last_error}
