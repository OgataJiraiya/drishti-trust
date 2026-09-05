"""Domain-separated Ed25519 audit-head checkpoints and external verification."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.core.canonical import canonical_json_bytes, canonical_json_text
from backend.core.hashing import sha256_bytes
from backend.core.signing import public_key_id, sign_bytes, verify_signature
from backend.database.models import AuditCheckpointRecord, AuditLogRecord, AuditOutboxRecord
from backend.schemas.audit import AuditCheckpointBundle, CheckpointCreationResponse, CheckpointVerification
from backend.services.audit_service import AuditService
from backend.services.audit_outbox_service import AuditOutboxService

DOMAIN = b"DRISHTI-TRUST:AUDIT-CHECKPOINT:v1\n"


class CheckpointConflict(RuntimeError):
    pass


class AuditCheckpointService:
    def __init__(self, factory: sessionmaker[Session], private_key: Ed25519PrivateKey,
                 public_key: Ed25519PublicKey, outbox: AuditOutboxService) -> None:
        self.factory, self.private_key, self.public_key, self.outbox = factory, private_key, public_key, outbox
        self.fingerprint = public_key_id(public_key)
        if public_key_id(private_key.public_key()) != self.fingerprint:
            raise RuntimeError("Audit checkpoint private/public key pair is inconsistent")

    def create(self) -> CheckpointCreationResponse:
        self.outbox.drain_pending()
        if self.outbox.status()["pending_count"]:
            raise CheckpointConflict("pending audit intents prevent checkpoint creation")
        with self.factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            pending = session.scalar(select(AuditOutboxRecord.outbox_id).where(
                AuditOutboxRecord.status == "PENDING").limit(1))
            if pending is not None:
                session.rollback()
                raise CheckpointConflict("pending audit intents prevent checkpoint creation")
            head = session.scalar(select(AuditLogRecord).order_by(AuditLogRecord.sequence.desc()).limit(1))
            if head is None:
                session.rollback(); raise CheckpointConflict("cannot checkpoint an empty audit chain")
            latest = session.scalar(select(AuditCheckpointRecord).order_by(
                AuditCheckpointRecord.audit_sequence.desc()).limit(1))
            if latest and latest.signing_key_fingerprint != self.fingerprint:
                session.rollback()
                raise CheckpointConflict("checkpoint signing-key continuity is broken")
            if latest and latest.audit_sequence == head.sequence and latest.audit_record_hash == head.current_hash:
                bundle = self._bundle(latest); session.rollback()
                return CheckpointCreationResponse(result="EXISTS", bundle=bundle)
            checkpoint_id = f"ACP-{head.sequence:08d}-{head.current_hash[:16]}"
            now = datetime.now(timezone.utc)
            payload = {"schema_version": "1", "checkpoint_id": checkpoint_id,
                       "audit_sequence": head.sequence, "audit_record_id": head.audit_id,
                       "audit_record_hash": head.current_hash,
                       "previous_checkpoint_hash": latest.checkpoint_hash if latest else None,
                       "created_at": now.isoformat().replace("+00:00", "Z"),
                       "signing_key_fingerprint": self.fingerprint}
            encoded = canonical_json_bytes(payload)
            record = AuditCheckpointRecord(
                checkpoint_id=checkpoint_id, audit_sequence=head.sequence,
                audit_record_id=head.audit_id, audit_record_hash=head.current_hash,
                checkpoint_payload_json=encoded.decode(), checkpoint_hash=sha256_bytes(encoded),
                signature=sign_bytes(self.private_key, DOMAIN + encoded),
                signing_key_fingerprint=self.fingerprint,
                previous_checkpoint_hash=latest.checkpoint_hash if latest else None, created_at=now,
            )
            session.add(record); session.commit()
            return CheckpointCreationResponse(result="CREATED", bundle=self._bundle(record))

    def verify(self, bundle: AuditCheckpointBundle) -> CheckpointVerification:
        payload = bundle.checkpoint.model_dump(mode="json")
        encoded = canonical_json_bytes(payload)
        with self.factory() as session:
            current = session.scalar(select(AuditLogRecord).order_by(AuditLogRecord.sequence.desc()).limit(1))
            current_seq = current.sequence if current else 0
            def result(valid: bool, status: str) -> CheckpointVerification:
                return CheckpointVerification(valid=valid, status=status,
                    audit_sequence=bundle.checkpoint.audit_sequence, current_audit_sequence=current_seq)
            if sha256_bytes(encoded) != bundle.checkpoint_hash:
                return result(False, "INVALID_CHECKPOINT_HASH")
            if bundle.checkpoint.signing_key_fingerprint != self.fingerprint:
                return result(False, "INVALID_SIGNING_KEY")
            if not verify_signature(self.public_key, DOMAIN + encoded, bundle.signature):
                return result(False, "INVALID_SIGNATURE")
            chain = AuditService().verify(session)
            if not chain.valid: return result(False, "AUDIT_CHAIN_INVALID")
            if current_seq < bundle.checkpoint.audit_sequence:
                return result(False, "AUDIT_HISTORY_SHORTER_THAN_CHECKPOINT")
            record = session.scalar(select(AuditLogRecord).where(
                AuditLogRecord.sequence == bundle.checkpoint.audit_sequence))
            if record is None or record.audit_id != bundle.checkpoint.audit_record_id or record.current_hash != bundle.checkpoint.audit_record_hash:
                return result(False, "AUDIT_HEAD_MISMATCH")
            return result(True, "VALID")

    def get(self, checkpoint_id: str) -> AuditCheckpointBundle | None:
        with self.factory() as session:
            record = session.get(AuditCheckpointRecord, checkpoint_id)
            return self._bundle(record) if record else None

    def verify_stored(self, checkpoint_id: str) -> CheckpointVerification:
        with self.factory() as session:
            current = session.scalar(select(AuditLogRecord).order_by(AuditLogRecord.sequence.desc()).limit(1))
            current_seq = current.sequence if current else 0
            record = session.get(AuditCheckpointRecord, checkpoint_id)
            if record is None:
                raise KeyError(checkpoint_id)
            try:
                bundle = self._bundle(record)
            except Exception:
                return CheckpointVerification(valid=False, status="INVALID_CHECKPOINT_HASH",
                    audit_sequence=record.audit_sequence, current_audit_sequence=current_seq)
            prior = list(session.scalars(select(AuditCheckpointRecord).where(
                AuditCheckpointRecord.audit_sequence <= record.audit_sequence
            ).order_by(AuditCheckpointRecord.audit_sequence)))
            previous_hash = None
            for item in prior:
                if item.previous_checkpoint_hash != previous_hash:
                    return CheckpointVerification(valid=False, status="CHECKPOINT_CHAIN_INVALID",
                        audit_sequence=record.audit_sequence, current_audit_sequence=current_seq)
                try:
                    item_payload = json.loads(item.checkpoint_payload_json)
                    encoded = canonical_json_bytes(item_payload)
                except Exception:
                    return CheckpointVerification(valid=False, status="CHECKPOINT_CHAIN_INVALID",
                        audit_sequence=record.audit_sequence, current_audit_sequence=current_seq)
                if sha256_bytes(encoded) != item.checkpoint_hash:
                    return CheckpointVerification(valid=False, status="CHECKPOINT_CHAIN_INVALID",
                        audit_sequence=record.audit_sequence, current_audit_sequence=current_seq)
                if (item_payload.get("checkpoint_id") != item.checkpoint_id
                        or item_payload.get("audit_sequence") != item.audit_sequence
                        or item_payload.get("audit_record_id") != item.audit_record_id
                        or item_payload.get("audit_record_hash") != item.audit_record_hash
                        or item_payload.get("previous_checkpoint_hash") != item.previous_checkpoint_hash
                        or item_payload.get("signing_key_fingerprint") != item.signing_key_fingerprint
                        or item.signing_key_fingerprint != self.fingerprint
                        or not verify_signature(self.public_key, DOMAIN + encoded, item.signature)):
                    return CheckpointVerification(valid=False, status="CHECKPOINT_CHAIN_INVALID",
                        audit_sequence=record.audit_sequence, current_audit_sequence=current_seq)
                previous_hash = item.checkpoint_hash
        return self.verify(bundle)

    def latest(self) -> AuditCheckpointBundle | None:
        with self.factory() as session:
            record = session.scalar(select(AuditCheckpointRecord).order_by(AuditCheckpointRecord.audit_sequence.desc()).limit(1))
            return self._bundle(record) if record else None

    def list(self) -> list[AuditCheckpointBundle]:
        with self.factory() as session:
            return [self._bundle(r) for r in session.scalars(select(AuditCheckpointRecord).order_by(AuditCheckpointRecord.audit_sequence))]

    def public_key_info(self) -> dict[str, str]:
        pem = self.public_key.public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
        return {"algorithm": "Ed25519", "public_key_pem": pem, "fingerprint": self.fingerprint}

    @staticmethod
    def _bundle(record: AuditCheckpointRecord) -> AuditCheckpointBundle:
        return AuditCheckpointBundle(checkpoint=json.loads(record.checkpoint_payload_json),
            checkpoint_hash=record.checkpoint_hash, signature=record.signature)
