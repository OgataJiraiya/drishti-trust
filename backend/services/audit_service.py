"""Serialized audit append and deterministic full-chain verification."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.audit_chain import GENESIS_HASH, compute_audit_hash
from backend.core.canonical import canonical_json_text
from backend.database.models import AuditLogRecord
from backend.database.repository import AuditRepository
from backend.schemas.audit import AuditChainVerification, AuditRecord
from backend.schemas.inference import VerificationFinding


class AuditService:
    def append(self, session: Session, event_type: str, asset_type: str,
               asset_id: str, payload: dict[str, Any]) -> AuditRecord:
        # Validate payload before taking the database write reservation.
        payload_json = canonical_json_text(payload)
        session.execute(text("BEGIN IMMEDIATE"))
        repository = AuditRepository(session)
        latest = repository.latest()
        sequence = latest.sequence + 1 if latest else 1
        audit_id = f"AUD-{sequence:08d}"
        previous_hash = latest.current_hash if latest else GENESIS_HASH
        timestamp = datetime.now(timezone.utc)
        current_hash = compute_audit_hash(
            audit_id=audit_id, sequence=sequence, event_type=event_type,
            asset_type=asset_type, asset_id=asset_id, payload=payload,
            timestamp=timestamp, previous_hash=previous_hash,
        )
        record = AuditLogRecord(
            audit_id=audit_id, sequence=sequence, event_type=event_type,
            asset_type=asset_type, asset_id=asset_id, payload_json=payload_json,
            timestamp=timestamp, previous_hash=previous_hash, current_hash=current_hash,
        )
        repository.add(record)
        return self.to_schema(record)

    def verify(self, session: Session) -> AuditChainVerification:
        records = AuditRepository(session).all_ordered()
        if not records:
            return AuditChainVerification(
                valid=True, status="VALID", records_checked=0, first_broken_audit_id=None,
                checks={"genesis": "UNAVAILABLE", "sequence": "VALID", "record_hash": "VALID", "chain_link": "VALID"},
                findings=[],
            )
        expected_previous = GENESIS_HASH
        for position, record in enumerate(records, start=1):
            payload = self._load_payload(record, position)
            if isinstance(payload, VerificationFinding):
                return self._failure(record, position, "record_hash", payload)
            if record.previous_hash != expected_previous:
                return self._failure(record, position, "chain_link", self._finding(
                    "AUDIT_CHAIN_LINK_FAILURE",
                    "Audit record does not link to the immediately preceding stored hash.",
                    {"audit_id": record.audit_id, "expected_previous_hash": expected_previous, "observed_previous_hash": record.previous_hash},
                ))
            expected_id = f"AUD-{position:08d}"
            if record.sequence != position or record.audit_id != expected_id:
                return self._failure(record, position, "sequence", self._finding(
                    "AUDIT_SEQUENCE_VIOLATION",
                    "Audit sequence or audit identifier is not contiguous and monotonic.",
                    {"audit_id": record.audit_id, "observed_sequence": record.sequence, "expected_sequence": position},
                ))
            recomputed = compute_audit_hash(
                audit_id=record.audit_id, sequence=record.sequence,
                event_type=record.event_type, asset_type=record.asset_type,
                asset_id=record.asset_id, payload=payload, timestamp=record.timestamp,
                previous_hash=record.previous_hash,
            )
            if recomputed != record.current_hash:
                return self._failure(record, position, "record_hash", self._finding(
                    "AUDIT_RECORD_TAMPERING",
                    "Stored audit hash does not match the canonical record contents.",
                    {"audit_id": record.audit_id, "stored_hash": record.current_hash, "recomputed_hash": recomputed},
                ))
            expected_previous = record.current_hash
        return AuditChainVerification(
            valid=True, status="VALID", records_checked=len(records), first_broken_audit_id=None,
            checks={"genesis": "VALID", "sequence": "VALID", "record_hash": "VALID", "chain_link": "VALID"},
            findings=[],
        )

    def _load_payload(self, record: AuditLogRecord, _position: int) -> dict[str, Any] | VerificationFinding:
        try:
            payload = json.loads(record.payload_json)
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
            canonical_json_text(payload)
            return payload
        except (json.JSONDecodeError, ValueError, TypeError):
            return self._finding(
                "AUDIT_RECORD_MALFORMED", "Audit payload is malformed or not a JSON object.",
                {"audit_id": record.audit_id},
            )

    def _failure(self, record: AuditLogRecord, checked: int, failed_check: str,
                 finding: VerificationFinding) -> AuditChainVerification:
        checks = {"genesis": "VALID", "sequence": "VALID", "record_hash": "VALID", "chain_link": "VALID"}
        checks[failed_check] = "INVALID"
        if record.sequence == 1 and failed_check == "chain_link":
            checks["genesis"] = "INVALID"
        return AuditChainVerification(
            valid=False, status="COMPROMISED", records_checked=checked,
            first_broken_audit_id=record.audit_id, checks=checks, findings=[finding],
        )

    @staticmethod
    def to_schema(record: AuditLogRecord) -> AuditRecord:
        timestamp = record.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return AuditRecord(
            audit_id=record.audit_id, sequence=record.sequence,
            event_type=record.event_type, asset_type=record.asset_type,
            asset_id=record.asset_id, payload=json.loads(record.payload_json),
            timestamp=timestamp, previous_hash=record.previous_hash,
            current_hash=record.current_hash,
        )

    @staticmethod
    def _finding(attack_class: str, reason: str, evidence: dict[str, Any]) -> VerificationFinding:
        return VerificationFinding(
            attack_class=attack_class, severity="CRITICAL", confidence=1.0,
            reason=reason, evidence=evidence, recommendation="QUARANTINE",
        )
