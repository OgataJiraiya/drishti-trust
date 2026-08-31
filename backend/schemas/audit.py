"""Internal audit-chain API contracts for Milestone 9."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from backend.schemas.inference import HEX_64, StrictSchema, VerificationFinding


class AuditRecord(StrictSchema):
    audit_id: str = Field(pattern=r"^AUD-[0-9]{8,}$")
    sequence: int = Field(ge=1)
    event_type: str
    asset_type: str
    asset_id: str
    payload: dict[str, Any]
    timestamp: datetime
    previous_hash: str = Field(pattern=HEX_64)
    current_hash: str = Field(pattern=HEX_64)


class AuditListResponse(StrictSchema):
    total: int
    page: int
    page_size: int
    items: list[AuditRecord]


class AuditChainVerification(StrictSchema):
    valid: bool
    status: Literal["VALID", "COMPROMISED"]
    records_checked: int
    first_broken_audit_id: str | None
    checks: dict[str, Literal["VALID", "INVALID", "UNAVAILABLE"]]
    findings: list[VerificationFinding]


class AuditCheckpointPayload(StrictSchema):
    schema_version: Literal["1"]
    checkpoint_id: str
    audit_sequence: int = Field(ge=1)
    audit_record_id: str
    audit_record_hash: str = Field(pattern=HEX_64)
    previous_checkpoint_hash: str | None = Field(default=None, pattern=HEX_64)
    created_at: datetime
    signing_key_fingerprint: str


class AuditCheckpointBundle(StrictSchema):
    checkpoint: AuditCheckpointPayload
    checkpoint_hash: str = Field(pattern=HEX_64)
    signature: str


class CheckpointCreationResponse(StrictSchema):
    result: Literal["CREATED", "EXISTS"]
    bundle: AuditCheckpointBundle


class CheckpointVerification(StrictSchema):
    valid: bool
    status: str
    audit_sequence: int | None = None
    current_audit_sequence: int


class CheckpointListResponse(StrictSchema):
    items: list[AuditCheckpointBundle]
