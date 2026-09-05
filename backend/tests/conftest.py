from __future__ import annotations

import base64

import httpx
import pytest

from backend.core.config import Settings
from backend.core.hashing import sha256_bytes
from backend.main import create_app


@pytest.fixture
def test_settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        key_dir=tmp_path / "keys",
        admin_bearer_token="TEST-RUNTIME-BEARER",
        internal_ingest_bearer_token="TEST-RUNTIME-BEARER",
        allow_unsigned_ingestion=True,
    )


@pytest.fixture
def app(test_settings):
    return create_app(test_settings)


@pytest.fixture
async def client(app):
    async def legacy_summary_scope(request: httpx.Request) -> None:
        # Pre-M13.1 scoring tests intentionally exercise the compatibility/all view.
        if request.url.path == "/api/summary" and not request.url.query:
            request.url = request.url.copy_with(query=b"trust_scope=all")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer TEST-RUNTIME-BEARER"},
        event_hooks={"request": [legacy_summary_scope]},
    ) as test_client:
        yield test_client


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def creation_payload():
    image = b"deterministic-test-image-bytes"
    return {
        "filename": "frame_00192.jpg",
        "input_base64": base64.b64encode(image).decode(),
        "model_id": "MODEL-001",
        "model_sha256": sha256_bytes(b"genuine-model"),
        "preprocessing": {"resize": [640, 640], "normalize": True},
        "config": {"threshold": 0.5},
        "output": {"class": "tank", "confidence": 0.94},
    }
