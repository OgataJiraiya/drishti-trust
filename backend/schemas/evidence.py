"""Frozen cross-team Finding JSON contract and API envelopes."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator

from backend.schemas.common import FindingModule, Recommendation, Severity
from backend.schemas.inference import StrictSchema

EvidenceText = Annotated[str, Field(min_length=1, max_length=1024)]
LimitationText = Annotated[str, Field(min_length=1, max_length=1024)]


class Finding(StrictSchema):
    """Finding Schema v1. The outer shape is frozen for all four modules."""

    finding_id: str = Field(min_length=1, max_length=128)
    module: FindingModule
    asset_type: str = Field(min_length=1, max_length=64)
    asset_id: str = Field(min_length=1, max_length=256)
    category: str = Field(min_length=1, max_length=128)
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=2048)
    evidence: list[EvidenceText] = Field(min_length=1, max_length=50)
    recommendation: Recommendation
    limitations: list[LimitationText] = Field(default_factory=list, max_length=20)

    @field_validator("finding_id", "asset_type", "asset_id", "category", "reason")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must contain non-whitespace text")
        return value

    @field_validator("evidence", "limitations")
    @classmethod
    def reject_blank_list_items(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("list items must contain non-whitespace text")
        return values


class EvidenceIngestionResponse(StrictSchema):
    finding: Finding
    result: Literal["CREATED", "EXISTS"]


class EvidenceListResponse(StrictSchema):
    total: int
    page: int
    page_size: int
    items: list[Finding]
