"""Stable Finding identity helpers for detector reruns."""
from backend.core.canonical import canonical_json_bytes
from backend.core.hashing import sha256_bytes

_PREFIX = {
    "dataset_integrity": "DATASET", "model_integrity": "MODEL",
    "inference_integrity": "INFERENCE", "distribution_shift": "SHIFT",
}


def deterministic_finding_id(module: str, asset_id: str, category: str,
                             stable_evidence_key: object) -> str:
    """Derive an ID only from stable detector identity; never time or randomness."""
    if module not in _PREFIX:
        raise ValueError("invalid Finding module")
    if not asset_id.strip() or not category.strip():
        raise ValueError("asset_id and category must not be blank")
    digest = sha256_bytes(canonical_json_bytes({
        "module": module, "asset_id": asset_id, "category": category,
        "stable_evidence_key": stable_evidence_key,
    }))
    return f"F-{_PREFIX[module]}-{digest[:24]}"
