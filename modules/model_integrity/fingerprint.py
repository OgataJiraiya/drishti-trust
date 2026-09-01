"""Fingerprints using the backend's single canonical JSON implementation."""
from backend.core.canonical import canonical_json_bytes
from backend.core.hashing import sha256_bytes


def fingerprint(value: object) -> str:
    return sha256_bytes(canonical_json_bytes(value))
