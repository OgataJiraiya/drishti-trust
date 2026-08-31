"""SQLAlchemy storage models for the first provenance vertical slice."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class InferenceReceiptRecord(Base):
    __tablename__ = "inference_receipts"

    receipt_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    receipt_json: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    nonce: Mapped[str] = mapped_column(String(256), unique=True, index=True, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AcceptedInferenceRecord(Base):
    """Replay state, deliberately separate from receipt archival storage."""

    __tablename__ = "accepted_inferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    nonce: Mapped[str] = mapped_column(String(256), unique=True, index=True, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    receipt_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class RegisteredModelRecord(Base):
    """Approved byte-level model identity; artifacts are never deserialized."""

    __tablename__ = "registered_models"

    model_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(512))
    expected_sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    artifact_size_bytes: Mapped[int | None] = mapped_column(Integer)
    declared_format: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="APPROVED")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    registration_source: Mapped[str] = mapped_column(String(32), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class AuditLogRecord(Base):
    """Append-only application audit hash-chain record."""

    __tablename__ = "audit_log"

    audit_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)


class FindingRecord(Base):
    """Immutable persisted copy of the frozen cross-team Finding JSON."""

    __tablename__ = "findings"

    finding_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    module: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    asset_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    asset_id: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    limitations_json: Mapped[str] = mapped_column(Text, nullable=False)
    finding_json: Mapped[str] = mapped_column(Text, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ModuleRunRecord(Base):
    """Immutable integration metadata kept outside frozen Finding Schema v1."""

    __tablename__ = "module_runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    module: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    producer: Mapped[str] = mapped_column(String(128), nullable=False)
    producer_version: Mapped[str | None] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False)
    existing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ModuleRunAuthenticationRecord(Base):
    """Durable run trust provenance, kept outside Finding Schema v1."""

    __tablename__ = "module_run_authentication"

    run_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("module_runs.run_id"), primary_key=True
    )
    authentication_mode: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    producer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    key_id: Mapped[str | None] = mapped_column(String(128))
    key_fingerprint: Mapped[str | None] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    authenticated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ModuleProducerRecord(Base):
    """Approved identity allowed to submit findings for exactly one module."""

    __tablename__ = "module_producers"

    producer_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    module: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="APPROVED")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ProducerKeyRecord(Base):
    """Public Ed25519 verification key; producer private keys are never stored."""

    __tablename__ = "producer_keys"

    key_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    producer_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("module_producers.producer_id"), index=True, nullable=False
    )
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    public_key_fingerprint: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
