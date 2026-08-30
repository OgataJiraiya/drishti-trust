"""Strict contracts for atomic cross-module Finding Schema v1 submissions."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from backend.schemas.common import FindingModule
from backend.schemas.evidence import Finding
from backend.schemas.inference import HEX_64, StrictSchema


class ModuleRunSubmission(StrictSchema):
    run_id: str = Field(min_length=1, max_length=128)
    module: FindingModule
    producer: str = Field(min_length=1, max_length=128)
    producer_version: str | None = Field(default=None, min_length=1, max_length=64)
    findings: list[Finding] = Field(min_length=1, max_length=100)

    @field_validator("run_id", "producer")
    @classmethod
    def reject_blank_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must contain non-whitespace text")
        return value

    @field_validator("producer_version")
    @classmethod
    def reject_blank_version(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("producer_version must contain non-whitespace text when supplied")
        return value

    @model_validator(mode="after")
    def validate_finding_batch(self) -> "ModuleRunSubmission":
        ids = [finding.finding_id for finding in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate finding_id values are not allowed within one module run")
        mismatched = [finding.finding_id for finding in self.findings if finding.module != self.module]
        if mismatched:
            raise ValueError("every finding.module must match the outer module")
        return self


class ModuleRunDetails(StrictSchema):
    run_id: str
    module: FindingModule
    producer: str
    producer_version: str | None
    request_hash: str = Field(pattern=HEX_64)
    total_findings: int
    created_findings: int
    existing_findings: int
    finding_ids: list[str]
    created_at: datetime


class ModuleRunIngestionResponse(StrictSchema):
    run_id: str
    module: FindingModule
    producer: str
    producer_version: str | None
    result: Literal["CREATED", "EXISTS"]
    total_findings: int
    created_findings: int
    existing_findings: int
    finding_ids: list[str]


class ModuleRunListResponse(StrictSchema):
    total: int
    page: int
    page_size: int
    items: list[ModuleRunDetails]
