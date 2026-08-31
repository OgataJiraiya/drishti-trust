"""Strict public contracts for offline module producer administration."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from backend.schemas.common import FindingModule
from backend.schemas.inference import HEX_64, StrictSchema


class ProducerRegistration(StrictSchema):
    producer_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    module: FindingModule
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("producer_id", "display_name")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must contain non-whitespace text")
        return value

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_json_compatible(cls, value: dict[str, Any]) -> dict[str, Any]:
        from backend.core.canonical import canonical_json_bytes
        canonical_json_bytes(value)
        return value


class ProducerDetails(ProducerRegistration):
    status: Literal["APPROVED", "REVOKED"]
    created_at: datetime
    updated_at: datetime


class ProducerMutationResponse(StrictSchema):
    result: Literal["CREATED", "EXISTS", "REVOKED"]
    producer: ProducerDetails


class ProducerListResponse(StrictSchema):
    total: int
    page: int
    page_size: int
    items: list[ProducerDetails]


class ProducerKeyRegistration(StrictSchema):
    key_id: str = Field(min_length=1, max_length=128)
    public_key_pem: str = Field(min_length=1, max_length=4096)

    @field_validator("key_id", "public_key_pem")
    @classmethod
    def reject_blank_key_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must contain non-whitespace text")
        return value


class ProducerKeyDetails(StrictSchema):
    key_id: str
    producer_id: str
    public_key_pem: str
    public_key_fingerprint: str = Field(pattern=HEX_64)
    status: Literal["ACTIVE", "REVOKED"]
    created_at: datetime
    revoked_at: datetime | None


class ProducerKeyMutationResponse(StrictSchema):
    result: Literal["CREATED", "EXISTS", "REVOKED"]
    key: ProducerKeyDetails


class ProducerKeyListResponse(StrictSchema):
    producer_id: str
    items: list[ProducerKeyDetails]
