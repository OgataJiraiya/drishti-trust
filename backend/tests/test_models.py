from __future__ import annotations

import hashlib
from io import BytesIO

import httpx
import pytest

from backend.core.hashing import sha256_bytes, sha256_stream
from backend.core.config import Settings
from backend.main import create_app


def registration(model_id="MODEL-001", digest=None, filename="detector.onnx"):
    return {
        "model_id": model_id,
        "display_name": "Approved detector",
        "original_filename": filename,
        "expected_sha256": digest or sha256_bytes(b"genuine-model"),
        "declared_format": "ONNX",
        "version": "1.0",
        "metadata": {"owner": "model_integrity"},
    }


@pytest.mark.anyio
async def test_register_retrieve_and_list_model(client):
    created = await client.post("/api/models/register", json=registration())
    assert created.status_code == 200
    assert created.json()["entry"]["status"] == "APPROVED"
    assert created.json()["entry"]["registration_source"] == "PROVIDED_DIGEST"
    retrieved = (await client.get("/api/models/MODEL-001")).json()
    assert retrieved["expected_sha256"] == registration()["expected_sha256"]
    listing = (await client.get("/api/models")).json()
    assert listing["total"] == 1
    assert listing["items"][0]["model_id"] == "MODEL-001"


@pytest.mark.anyio
async def test_registration_is_idempotent_but_trust_anchor_cannot_change(client):
    first = (await client.post("/api/models/register", json=registration())).json()
    same = await client.post("/api/models/register", json=registration(filename="renamed.onnx"))
    conflict = await client.post("/api/models/register", json=registration(digest=sha256_bytes(b"replacement")))
    assert first["idempotent"] is False
    assert same.status_code == 200 and same.json()["idempotent"] is True
    assert conflict.status_code == 409
    assert (await client.get("/api/models/MODEL-001")).json()["expected_sha256"] == registration()["expected_sha256"]


@pytest.mark.anyio
async def test_verify_correct_and_substituted_digest_same_filename(client):
    approved = registration()
    await client.post("/api/models/register", json=approved)
    genuine = (await client.post("/api/models/verify", json={
        "model_id": "MODEL-001", "observed_sha256": approved["expected_sha256"], "filename": "detector.onnx"
    })).json()
    substituted = (await client.post("/api/models/verify", json={
        "model_id": "MODEL-001", "observed_sha256": sha256_bytes(b"malicious-model"), "filename": "detector.onnx"
    })).json()
    assert genuine["status"] == "ACCEPT" and genuine["checks"]["model_digest"] == "VALID"
    assert substituted["status"] == "QUARANTINE"
    finding = next(item for item in substituted["findings"] if item["attack_class"] == "MODEL_SUBSTITUTION")
    assert finding["severity"] == "CRITICAL" and finding["recommendation"] == "QUARANTINE"


@pytest.mark.anyio
async def test_register_and_verify_streamed_opaque_artifact(client):
    genuine = b"\x00opaque\xffmodel\x80bytes"
    query = "model_id=MODEL-ART&display_name=Opaque&original_filename=../models/genuine.onnx"
    registered = (await client.post(f"/api/models/register/artifact?{query}", content=genuine)).json()
    assert registered["entry"]["expected_sha256"] == sha256_bytes(genuine)
    assert registered["entry"]["artifact_size_bytes"] == len(genuine)
    assert registered["entry"]["original_filename"] == "genuine.onnx"
    assert registered["entry"]["registration_source"] == "HASHED_ARTIFACT"

    matching = (await client.post("/api/models/verify/artifact?model_id=MODEL-ART&filename=different-name.bin", content=genuine)).json()
    modified = (await client.post("/api/models/verify/artifact?model_id=MODEL-ART&filename=genuine.onnx", content=b"different bytes")).json()
    assert matching["status"] == "ACCEPT"
    assert modified["status"] == "QUARANTINE"
    assert any(item["attack_class"] == "MODEL_SUBSTITUTION" for item in modified["findings"])


@pytest.mark.anyio
async def test_unknown_model_is_fail_closed(client):
    result = (await client.post("/api/models/verify", json={
        "model_id": "UNKNOWN", "observed_sha256": sha256_bytes(b"anything")
    })).json()
    assert result["valid"] is False and result["status"] == "QUARANTINE"
    assert result["checks"]["registry_entry"] == "UNAVAILABLE"
    assert any(item["attack_class"] == "UNREGISTERED_MODEL" and item["severity"] == "HIGH" for item in result["findings"])


@pytest.mark.anyio
async def test_revoked_matching_model_is_not_accepted(client):
    approved = registration()
    await client.post("/api/models/register", json=approved)
    assert (await client.post("/api/models/MODEL-001/revoke")).json()["status"] == "REVOKED"
    result = (await client.post("/api/models/verify", json={
        "model_id": "MODEL-001", "observed_sha256": approved["expected_sha256"]
    })).json()
    assert result["checks"]["model_digest"] == "VALID"
    assert result["checks"]["registry_status"] == "REVOKED"
    assert result["status"] == "QUARANTINE"
    assert any(item["attack_class"] == "MODEL_REVOKED" for item in result["findings"])


@pytest.mark.anyio
async def test_invalid_digest_returns_clean_validation_error(client):
    response = await client.post("/api/models/register", json=registration(digest="not-sha256"))
    assert response.status_code == 422 and "detail" in response.json()


@pytest.mark.anyio
async def test_uppercase_digest_is_safely_normalized(client):
    payload = registration()
    payload["expected_sha256"] = payload["expected_sha256"].upper()
    result = (await client.post("/api/models/register", json=payload)).json()
    assert result["entry"]["expected_sha256"] == payload["expected_sha256"].lower()


@pytest.mark.anyio
async def test_registry_persists_after_application_reopen(client, test_settings):
    await client.post("/api/models/register", json=registration())
    reopened = create_app(test_settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=reopened), base_url="http://reopened") as reopened_client:
        result = await reopened_client.get("/api/models/MODEL-001")
    assert result.status_code == 200
    assert result.json()["expected_sha256"] == registration()["expected_sha256"]


@pytest.mark.anyio
async def test_oversized_artifact_is_rejected_cleanly(tmp_path):
    settings = Settings(data_dir=tmp_path / "limited-data", key_dir=tmp_path / "limited-keys", max_model_bytes=4)
    limited = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=limited), base_url="http://limited") as client:
        response = await client.post(
            "/api/models/register/artifact?model_id=LIMITED&display_name=Limited",
            content=b"five!",
        )
    assert response.status_code == 413
    assert "detail" in response.json()


def test_streaming_file_hash_small_and_large():
    small = b"small model"
    large = (b"model-block" * 200_000) + b"tail"
    assert sha256_stream(BytesIO(small), chunk_size=3) == hashlib.sha256(small).hexdigest()
    assert sha256_stream(BytesIO(large), chunk_size=4096) == hashlib.sha256(large).hexdigest()
