"""Generate a local producer key and optionally register its public half."""
from __future__ import annotations
import argparse
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from backend.core.hashing import sha256_bytes
from backend.core.signing import generate_key_pair, load_public_key
from drishti_sdk import DrishtiClient, DrishtiError


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate DEMO/LOCAL Ed25519 producer keys")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--name", default="demo_producer")
    parser.add_argument("--producer-id")
    parser.add_argument("--key-id")
    parser.add_argument("--module", choices=["dataset_integrity", "model_integrity", "inference_integrity", "distribution_shift"])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--admin-token", help="Runtime token; prefer environment/shell prompting in shared environments")
    args = parser.parse_args()
    paths = generate_key_pair(args.output_dir, args.name)
    public = load_public_key(paths.public_key)
    print("DEMO/LOCAL key pair generated; do not commit it.")
    print(f"Private key: {paths.private_key} (not displayed or transmitted)")
    print(f"Public key:  {paths.public_key}")
    raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    print(f"Fingerprint: {sha256_bytes(raw)}")
    requested = any((args.producer_id, args.key_id, args.module, args.admin_token))
    if requested:
        if not all((args.producer_id, args.key_id, args.module, args.admin_token)):
            parser.error("registration requires --producer-id, --key-id, --module and --admin-token")
        try:
            with DrishtiClient(args.base_url, admin_token=args.admin_token) as client:
                client.register_producer(args.producer_id, args.producer_id, args.module)
                client.register_producer_key(args.producer_id, args.key_id, paths.public_key.read_text())
            print("Producer and public key registered; private key stayed local.")
        except DrishtiError as exc:
            print(f"Registration failed: {exc}")
            return 2
    return 0


if __name__ == "__main__": raise SystemExit(main())
