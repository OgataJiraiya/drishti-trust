"""Canonical audit-chain hashing shared by append and verification."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.core.hashing import sha256_json

GENESIS_HASH = "0" * 64


def utc_iso(timestamp: datetime) -> str:
    """Normalize SQLite/Python datetimes to one deterministic UTC representation."""
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def audit_hash_payload(
    *, audit_id: str, sequence: int, event_type: str, asset_type: str,
    asset_id: str, payload: dict[str, Any], timestamp: datetime, previous_hash: str,
) -> dict[str, Any]:
    return {
        "audit_id": audit_id,
        "sequence": sequence,
        "event_type": event_type,
        "asset_type": asset_type,
        "asset_id": asset_id,
        "payload": payload,
        "timestamp": utc_iso(timestamp),
        "previous_hash": previous_hash,
    }


def compute_audit_hash(**fields: Any) -> str:
    """Hash all security-relevant record fields except current_hash itself."""
    return sha256_json(audit_hash_payload(**fields))
