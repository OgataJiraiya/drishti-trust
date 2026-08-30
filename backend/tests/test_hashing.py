from backend.core.canonical import canonical_json_bytes
from backend.core.hashing import sha256_json


def test_canonical_json_and_hash_are_deterministic():
    left = {"z": "नमस्ते", "a": [2, {"b": True}]}
    right = {"a": [2, {"b": True}], "z": "नमस्ते"}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert sha256_json(left) == sha256_json(right)
