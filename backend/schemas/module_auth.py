"""Authenticated wrapper kept strictly outside frozen Finding Schema v1."""
from __future__ import annotations

import base64
import binascii

from pydantic import Field, field_validator

from backend.schemas.inference import StrictSchema
from backend.schemas.integration import ModuleRunSubmission


class SignedModuleRunSubmission(StrictSchema):
    run: ModuleRunSubmission
    key_id: str = Field(min_length=1, max_length=128)
    signature: str = Field(min_length=1, max_length=256)

    @field_validator("key_id")
    @classmethod
    def reject_blank_key_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("key_id must contain non-whitespace text")
        return value

    @field_validator("signature")
    @classmethod
    def validate_ed25519_signature_encoding(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("signature must be valid base64") from exc
        if len(decoded) != 64:
            raise ValueError("signature must encode a 64-byte Ed25519 signature")
        return value
