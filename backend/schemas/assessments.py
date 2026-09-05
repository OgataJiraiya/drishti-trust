"""Strict public contracts for assessment lifecycle and sealed snapshots."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from backend.core.canonical import canonical_json_bytes
from backend.schemas.inference import HEX_64, StrictSchema
from backend.schemas.integration import ModuleRunDetails

AssessmentStatus = Literal["DRAFT", "ACTIVE", "SEALED"]


class AssessmentCreate(StrictSchema):
    assessment_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4096)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("assessment_id", "name")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must contain non-whitespace text")
        return value

    @field_validator("description")
    @classmethod
    def reject_blank_description(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("description must contain non-whitespace text when supplied")
        return value

    @field_validator("metadata")
    @classmethod
    def bound_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(canonical_json_bytes(value)) > 16_384:
            raise ValueError("metadata exceeds the 16384-byte canonical JSON limit")
        return value


class AssessmentDetails(StrictSchema):
    assessment_id: str
    name: str
    description: str | None
    status: AssessmentStatus
    metadata: dict[str, Any]
    created_at: datetime
    activated_at: datetime | None
    sealed_at: datetime | None


class AssessmentListResponse(StrictSchema):
    total: int
    limit: int
    offset: int
    items: list[AssessmentDetails]


class AssessmentRunListResponse(StrictSchema):
    total: int
    limit: int
    offset: int
    items: list[ModuleRunDetails]


class AssessmentSnapshot(StrictSchema):
    assessment_id: str
    schema_version: Literal["1"]
    payload: dict[str, Any]
    summary_hash: str = Field(pattern=HEX_64)
    run_set_hash: str = Field(pattern=HEX_64)
    run_count: int
    trusted_finding_count: int
    excluded_untrusted_finding_count: int
    created_at: datetime


class SnapshotVerification(StrictSchema):
    assessment_id: str
    status: Literal["VALID", "INVALID"]
    stored_summary_hash: str
    recomputed_summary_hash: str
    stored_run_set_hash: str
    recomputed_run_set_hash: str
