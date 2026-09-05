"""Approved model registry API contracts."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator

from backend.core.canonical import canonical_json_bytes
from backend.schemas.inference import HEX_64, StrictSchema, VerificationFinding


class ModelStatus(StrEnum):
    APPROVED = "APPROVED"
    REVOKED = "REVOKED"
    QUARANTINED = "QUARANTINED"


class RegisterModelRequest(StrictSchema):
    model_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    original_filename: str | None = Field(default=None, max_length=512)
    expected_sha256: str = Field(pattern=HEX_64)
    artifact_size_bytes: int | None = Field(default=None, ge=0)
    declared_format: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expected_sha256", mode="before")
    @classmethod
    def normalize_digest(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        canonical_json_bytes(value)
        return value


class ModelRegistryEntry(StrictSchema):
    model_id: str
    display_name: str
    original_filename: str | None
    expected_sha256: str
    artifact_size_bytes: int | None
    declared_format: str | None
    version: str | None
    status: ModelStatus
    metadata: dict[str, Any]
    registration_source: Literal["PROVIDED_DIGEST", "HASHED_ARTIFACT"]
    registered_at: datetime
    updated_at: datetime


class ModelRegistrationResponse(StrictSchema):
    entry: ModelRegistryEntry
    idempotent: bool


class ModelListResponse(StrictSchema):
    total: int
    page: int
    page_size: int
    items: list[ModelRegistryEntry]


class VerifyModelRequest(StrictSchema):
    model_id: str = Field(min_length=1, max_length=128)
    observed_sha256: str = Field(pattern=HEX_64)
    filename: str | None = Field(default=None, max_length=512)

    @field_validator("observed_sha256", mode="before")
    @classmethod
    def normalize_digest(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value


class ModelVerificationResponse(StrictSchema):
    model_id: str
    valid: bool
    status: Literal["ACCEPT", "REVIEW", "QUARANTINE", "REJECT"]
    checks: dict[str, str]
    expected_sha256: str | None
    observed_sha256: str
    findings: list[VerificationFinding]
