"""Typed, task-agnostic inference receipt contracts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HEX_64 = r"^[0-9a-f]{64}$"


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputEvidence(StrictSchema):
    filename: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=HEX_64)


class ModelEvidence(StrictSchema):
    model_id: str = Field(min_length=1, max_length=128)
    sha256: str = Field(pattern=HEX_64)
    behavior_id: str | None = Field(default=None, max_length=128)


class ProcessingEvidence(StrictSchema):
    preprocessing_sha256: str = Field(pattern=HEX_64)
    config_sha256: str = Field(pattern=HEX_64)


class InferenceEvidence(StrictSchema):
    output: Any
    output_sha256: str = Field(pattern=HEX_64)

    @field_validator("output")
    @classmethod
    def output_must_be_json_compatible(cls, value: Any) -> Any:
        from backend.core.canonical import canonical_json_bytes
        canonical_json_bytes(value)
        return value


class SecurityEvidence(StrictSchema):
    sequence: int = Field(ge=1)
    nonce: str = Field(min_length=32, max_length=256)
    timestamp: datetime
    previous_receipt_hash: str | None = Field(default=None, pattern=HEX_64)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a UTC offset")
        return value


class SignerEvidence(StrictSchema):
    key_id: str = Field(min_length=1, max_length=128)


class InferenceReceipt(StrictSchema):
    receipt_id: str = Field(pattern=r"^INF-[0-9]{8,}$")
    input: InputEvidence
    model: ModelEvidence
    processing: ProcessingEvidence
    inference: InferenceEvidence
    security: SecurityEvidence
    signer: SignerEvidence
    signature: str = Field(min_length=1)

    def signed_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"signature"})


class CreateReceiptRequest(StrictSchema):
    filename: str = Field(min_length=1, max_length=512)
    input_base64: str = Field(min_length=1, description="Base64-encoded input image bytes")
    model_id: str = Field(min_length=1, max_length=128)
    model_sha256: str = Field(pattern=HEX_64)
    behavior_id: str | None = Field(default=None, max_length=128)
    preprocessing: Any = Field(default_factory=dict)
    config: Any = Field(default_factory=dict)
    output: Any


class VerificationArtifacts(StrictSchema):
    input_base64: str | None = None
    model_sha256: str | None = Field(default=None, pattern=HEX_64)
    preprocessing: Any | None = None
    config: Any | None = None
    output: Any | None = None


class VerifyReceiptRequest(StrictSchema):
    """Read-only integrity verification; does not update replay state."""

    receipt: InferenceReceipt
    artifacts: VerificationArtifacts = Field(default_factory=VerificationArtifacts)


class AcceptReceiptRequest(VerifyReceiptRequest):
    """Process a signed receipt as a new event and enforce replay controls."""


class VerificationFinding(StrictSchema):
    attack_class: str
    severity: Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    confidence: float = Field(ge=0, le=1)
    reason: str
    evidence: dict[str, Any]
    recommendation: Literal["ACCEPT", "REVIEW", "QUARANTINE", "REJECT"]


class VerificationResponse(StrictSchema):
    valid: bool
    status: Literal["ACCEPT", "REVIEW", "QUARANTINE", "REJECT"]
    checks: dict[str, Literal["VALID", "INVALID", "UNAVAILABLE"]]
    findings: list[VerificationFinding]


class InferenceListResponse(StrictSchema):
    total: int
    page: int
    page_size: int
    items: list[InferenceReceipt]
