"""Reusable SHA-256 helpers."""
from __future__ import annotations

import hashlib
from pathlib import Path
from collections.abc import AsyncIterable
from typing import Any, BinaryIO

from backend.core.canonical import canonical_json_bytes

CHUNK_SIZE = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(stream: BinaryIO, chunk_size: int = CHUNK_SIZE) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(chunk_size):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path | str) -> str:
    with Path(path).open("rb") as artifact:
        return sha256_stream(artifact)


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


hash_inference_output = sha256_json
hash_configuration = sha256_json
hash_receipt_payload = sha256_json
hash_model_artifact = sha256_file


class ArtifactTooLarge(ValueError):
    pass


async def sha256_async_chunks(chunks: AsyncIterable[bytes], max_bytes: int) -> tuple[str, int]:
    """Hash an asynchronous byte stream without buffering the artifact in memory."""
    digest = hashlib.sha256()
    total = 0
    async for chunk in chunks:
        total += len(chunk)
        if total > max_bytes:
            raise ArtifactTooLarge(f"artifact exceeds the configured {max_bytes}-byte limit")
        digest.update(chunk)
    return digest.hexdigest(), total
