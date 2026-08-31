"""Verify retained checkpoint signature against explicitly pinned public key."""
import argparse, json
from pathlib import Path

from backend.core.canonical import canonical_json_bytes
from backend.core.hashing import sha256_bytes
from backend.core.signing import load_public_key, public_key_id, verify_signature
from backend.services.audit_checkpoint_service import DOMAIN
from drishti_sdk import DrishtiClient, DrishtiError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True,
                        help="Explicitly pinned Ed25519 public key; never downloaded implicitly")
    parser.add_argument("--base-url", help="Optionally also compare against current audit history")
    args = parser.parse_args()
    try:
        material = json.loads(args.checkpoint.read_text())
        bundle = material.get("checkpoint_bundle", material)
        payload = bundle["checkpoint"]
        encoded = canonical_json_bytes(payload)
        key = load_public_key(args.public_key)
        if sha256_bytes(encoded) != bundle["checkpoint_hash"]:
            raise ValueError("INVALID_CHECKPOINT_HASH")
        if public_key_id(key) != payload["signing_key_fingerprint"]:
            raise ValueError("INVALID_SIGNING_KEY")
        if not verify_signature(key, DOMAIN + encoded, bundle["signature"]):
            raise ValueError("INVALID_SIGNATURE")
        if args.base_url:
            with DrishtiClient(args.base_url) as client:
                result = client.verify_checkpoint(bundle)
            if not result.get("valid"): raise ValueError(str(result.get("status", "INVALID")))
    except (OSError, KeyError, TypeError, ValueError, DrishtiError) as exc:
        print(f"INVALID: {str(exc)[:256]}"); return 1
    print("VALID")
    return 0


if __name__ == "__main__": raise SystemExit(main())
