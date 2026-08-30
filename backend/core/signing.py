"""Ed25519 key lifecycle and fail-closed signature operations."""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from backend.core.hashing import sha256_bytes


@dataclass(frozen=True)
class KeyPairPaths:
    private_key: Path
    public_key: Path


def generate_key_pair(key_dir: Path, name: str = "receipt_signing") -> KeyPairPaths:
    """Create an unencrypted local development key pair without overwriting keys."""
    key_dir.mkdir(parents=True, exist_ok=True)
    private_path = key_dir / f"{name}.private.pem"
    public_path = key_dir / f"{name}.public.pem"
    if private_path.exists() or public_path.exists():
        raise FileExistsError("Refusing to overwrite an existing signing key")
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_path.write_bytes(private_bytes)
    os.chmod(private_path, 0o600)
    public_path.write_bytes(public_bytes)
    return KeyPairPaths(private_path, public_path)


def load_private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("Signing key is not Ed25519")
    return key


def load_public_key(path: Path) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("Verification key is not Ed25519")
    return key


def public_key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return f"ed25519:{sha256_bytes(raw)}"


def sign_bytes(private_key: Ed25519PrivateKey, payload: bytes) -> str:
    return base64.b64encode(private_key.sign(payload)).decode("ascii")


def verify_signature(public_key: Ed25519PublicKey, payload: bytes, signature: str) -> bool:
    try:
        decoded = base64.b64decode(signature, validate=True)
        public_key.verify(decoded, payload)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
