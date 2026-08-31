"""Durable approved-producer and public-key lifecycle management."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.canonical import canonical_json_text
from backend.core.hashing import sha256_bytes
from backend.database.models import ModuleProducerRecord, ProducerKeyRecord, utc_now
from backend.database.repository import ProducerKeyRepository, ProducerRepository
from backend.schemas.producers import (
    ProducerDetails,
    ProducerKeyDetails,
    ProducerKeyRegistration,
    ProducerRegistration,
)
from backend.services.audit_outbox_service import AuditOutboxService


class ProducerConflict(ValueError):
    """An immutable producer or key identity conflicts with stored content."""


class ProducerNotFound(ValueError):
    """An administrative operation referenced an unknown producer or key."""


class InvalidProducerKey(ValueError):
    """Submitted material is not an Ed25519 public key."""


class ProducerService:
    def __init__(self, outbox: AuditOutboxService | None = None) -> None:
        self.outbox = outbox

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    def register_producer(
        self, body: ProducerRegistration, session: Session
    ) -> tuple[str, ProducerDetails]:
        metadata_json = canonical_json_text(body.metadata)
        session.execute(text("BEGIN IMMEDIATE"))
        repository = ProducerRepository(session)
        existing = repository.get(body.producer_id)
        if existing is not None:
            identical = (
                existing.display_name == body.display_name
                and existing.module == body.module.value
                and existing.metadata_json == metadata_json
            )
            session.rollback()
            if not identical:
                raise ProducerConflict(
                    "producer_id already exists with different immutable registration content"
                )
            return "EXISTS", self.to_producer(existing)
        record = ModuleProducerRecord(
            producer_id=body.producer_id,
            display_name=body.display_name,
            module=body.module.value,
            status="APPROVED",
            metadata_json=metadata_json,
        )
        repository.add_pending(record)
        if self.outbox:
            self.outbox.stage(session, f"PRODUCER_REGISTERED:{body.producer_id}",
                "PRODUCER_REGISTERED", "module_producer", body.producer_id,
                {"producer_id": body.producer_id, "module": body.module,
                 "status": "APPROVED"})
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ProducerConflict("producer identity already exists") from exc
        return "CREATED", self.to_producer(record)

    def register_key(
        self, producer_id: str, body: ProducerKeyRegistration, session: Session
    ) -> tuple[str, ProducerKeyDetails]:
        public_key, canonical_pem, fingerprint = self.parse_public_key(body.public_key_pem)
        del public_key
        session.execute(text("BEGIN IMMEDIATE"))
        if ProducerRepository(session).get(producer_id) is None:
            session.rollback()
            raise ProducerNotFound("Producer not found")
        repository = ProducerKeyRepository(session)
        existing = repository.get(body.key_id)
        if existing is not None:
            identical = (
                existing.producer_id == producer_id
                and existing.public_key_fingerprint == fingerprint
                and existing.public_key_pem == canonical_pem
            )
            session.rollback()
            if not identical:
                raise ProducerConflict(
                    "key_id already exists with different immutable key or producer association"
                )
            return "EXISTS", self.to_key(existing)
        reused = repository.by_fingerprint(fingerprint)
        if reused is not None and reused.producer_id != producer_id:
            session.rollback()
            raise ProducerConflict("public key is already bound to a different producer")
        record = ProducerKeyRecord(
            key_id=body.key_id,
            producer_id=producer_id,
            public_key_pem=canonical_pem,
            public_key_fingerprint=fingerprint,
            status="ACTIVE",
        )
        repository.add_pending(record)
        if self.outbox:
            self.outbox.stage(session, f"PRODUCER_KEY_REGISTERED:{body.key_id}",
                "PRODUCER_KEY_REGISTERED", "producer_key", body.key_id,
                {"key_id": body.key_id, "producer_id": producer_id,
                 "key_fingerprint": fingerprint, "status": "ACTIVE"})
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ProducerConflict("producer key identity already exists") from exc
        return "CREATED", self.to_key(record)

    def revoke_producer(
        self, producer_id: str, session: Session
    ) -> tuple[bool, ProducerDetails]:
        session.execute(text("BEGIN IMMEDIATE"))
        record = ProducerRepository(session).get(producer_id)
        if record is None:
            session.rollback()
            raise ProducerNotFound("Producer not found")
        changed = record.status != "REVOKED"
        if changed:
            record.status = "REVOKED"
            record.updated_at = utc_now()
            if self.outbox:
                self.outbox.stage(session, f"PRODUCER_REVOKED:{producer_id}",
                    "PRODUCER_REVOKED", "module_producer", producer_id,
                    {"producer_id": producer_id, "module": record.module, "status": "REVOKED"})
            session.commit()
        else:
            session.rollback()
        return changed, self.to_producer(record)

    def revoke_key(
        self, producer_id: str, key_id: str, session: Session
    ) -> tuple[bool, ProducerKeyDetails]:
        session.execute(text("BEGIN IMMEDIATE"))
        record = ProducerKeyRepository(session).get(key_id)
        if record is None or record.producer_id != producer_id:
            session.rollback()
            raise ProducerNotFound("Producer key not found")
        changed = record.status != "REVOKED"
        if changed:
            record.status = "REVOKED"
            record.revoked_at = utc_now()
            if self.outbox:
                self.outbox.stage(session, f"PRODUCER_KEY_REVOKED:{key_id}",
                    "PRODUCER_KEY_REVOKED", "producer_key", key_id,
                    {"key_id": key_id, "producer_id": producer_id,
                     "key_fingerprint": record.public_key_fingerprint, "status": "REVOKED"})
            session.commit()
        else:
            session.rollback()
        return changed, self.to_key(record)

    @staticmethod
    def parse_public_key(pem: str) -> tuple[Ed25519PublicKey, str, str]:
        try:
            key = serialization.load_pem_public_key(pem.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise InvalidProducerKey("public_key_pem must contain an Ed25519 public key") from exc
        if not isinstance(key, Ed25519PublicKey):
            raise InvalidProducerKey("Only Ed25519 public keys are accepted")
        raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        canonical_pem = key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("ascii")
        return key, canonical_pem, sha256_bytes(raw)

    def to_producer(self, record: ModuleProducerRecord) -> ProducerDetails:
        return ProducerDetails(
            producer_id=record.producer_id,
            display_name=record.display_name,
            module=record.module,
            metadata=json.loads(record.metadata_json),
            status=record.status,
            created_at=self._utc(record.created_at),
            updated_at=self._utc(record.updated_at),
        )

    def to_key(self, record: ProducerKeyRecord) -> ProducerKeyDetails:
        return ProducerKeyDetails(
            key_id=record.key_id,
            producer_id=record.producer_id,
            public_key_pem=record.public_key_pem,
            public_key_fingerprint=record.public_key_fingerprint,
            status=record.status,
            created_at=self._utc(record.created_at),
            revoked_at=self._utc(record.revoked_at) if record.revoked_at else None,
        )
