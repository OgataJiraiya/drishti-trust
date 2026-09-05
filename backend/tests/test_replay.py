from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from backend.core.canonical import canonical_json_bytes
from backend.core.signing import sign_bytes
from backend.database.models import AcceptedInferenceRecord
from backend.main import create_app


def artifacts(payload):
    return {
        "input_base64": payload["input_base64"],
        "model_sha256": payload["model_sha256"],
        "preprocessing": payload["preprocessing"],
        "config": payload["config"],
        "output": payload["output"],
    }


def request_body(receipt, payload):
    return {"receipt": receipt, "artifacts": artifacts(payload)}


def resign(app, receipt):
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    receipt["signature"] = sign_bytes(app.state.provenance_service.private_key, canonical_json_bytes(payload))
    return receipt


async def create_receipt(client, payload):
    response = await client.post("/api/inference/receipt", json=payload)
    assert response.status_code == 201
    return response.json()


@pytest.mark.anyio
async def test_first_accept_succeeds_and_second_is_replay(client, creation_payload):
    receipt = await create_receipt(client, creation_payload)
    first = (await client.post("/api/inference/accept", json=request_body(receipt, creation_payload))).json()
    second = (await client.post("/api/inference/accept", json=request_body(receipt, creation_payload))).json()
    assert first["status"] == "ACCEPT"
    assert first["checks"]["nonce"] == "VALID"
    assert first["checks"]["sequence"] == "VALID"
    assert first["checks"]["timestamp"] == "VALID"
    assert second["status"] == "REJECT"
    assert second["checks"]["signature"] == "VALID"
    assert second["checks"]["nonce"] == "INVALID"
    assert {item["attack_class"] for item in second["findings"]} >= {"NONCE_REUSE", "REPLAY_DETECTED"}


@pytest.mark.anyio
async def test_verify_repeatedly_is_read_only(client, app, creation_payload):
    receipt = await create_receipt(client, creation_payload)
    for _ in range(100):
        result = (await client.post("/api/inference/verify", json=request_body(receipt, creation_payload))).json()
        assert result["status"] == "ACCEPT"
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AcceptedInferenceRecord)) == 0
    assert (await client.post("/api/inference/accept", json=request_body(receipt, creation_payload))).json()["status"] == "ACCEPT"


@pytest.mark.anyio
async def test_reused_nonce_on_new_valid_signed_receipt_is_rejected(client, app, creation_payload):
    first = await create_receipt(client, creation_payload)
    assert (await client.post("/api/inference/accept", json=request_body(first, creation_payload))).json()["status"] == "ACCEPT"
    second = await create_receipt(client, creation_payload)
    second["security"]["nonce"] = first["security"]["nonce"]
    resign(app, second)
    result = (await client.post("/api/inference/accept", json=request_body(second, creation_payload))).json()
    assert result["checks"]["signature"] == "VALID"
    assert result["status"] == "REJECT"
    assert any(item["attack_class"] == "NONCE_REUSE" for item in result["findings"])


@pytest.mark.anyio
async def test_duplicate_sequence_is_rejected(client, app, creation_payload):
    first = await create_receipt(client, creation_payload)
    assert (await client.post("/api/inference/accept", json=request_body(first, creation_payload))).json()["status"] == "ACCEPT"
    second = await create_receipt(client, creation_payload)
    second["security"]["sequence"] = first["security"]["sequence"]
    resign(app, second)
    result = (await client.post("/api/inference/accept", json=request_body(second, creation_payload))).json()
    assert result["status"] == "REJECT"
    assert any(item["attack_class"] == "DUPLICATE_SEQUENCE" for item in result["findings"])


@pytest.mark.anyio
async def test_sequence_regression_and_increasing_gap(client, app, creation_payload):
    first = await create_receipt(client, creation_payload)
    first["security"]["sequence"] = 10
    resign(app, first)
    assert (await client.post("/api/inference/accept", json=request_body(first, creation_payload))).json()["status"] == "ACCEPT"

    regressed = await create_receipt(client, creation_payload)
    regressed["security"]["sequence"] = 9
    resign(app, regressed)
    rejected = (await client.post("/api/inference/accept", json=request_body(regressed, creation_payload))).json()
    assert rejected["checks"]["sequence"] == "INVALID"
    assert any(item["attack_class"] == "SEQUENCE_REGRESSION" for item in rejected["findings"])

    increasing = await create_receipt(client, creation_payload)
    increasing["security"]["sequence"] = 12
    resign(app, increasing)
    accepted = (await client.post("/api/inference/accept", json=request_body(increasing, creation_payload))).json()
    assert accepted["status"] == "ACCEPT"
    assert accepted["checks"]["sequence"] == "VALID"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("offset", "attack"),
    [(-301, "STALE_RECEIPT"), (31, "FUTURE_TIMESTAMP")],
)
async def test_timestamp_window_is_enforced(client, app, creation_payload, offset, attack):
    receipt = await create_receipt(client, creation_payload)
    receipt["security"]["timestamp"] = (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()
    resign(app, receipt)
    result = (await client.post("/api/inference/accept", json=request_body(receipt, creation_payload))).json()
    assert result["status"] == "REJECT"
    assert result["checks"]["timestamp"] == "INVALID"
    assert any(item["attack_class"] == attack for item in result["findings"])


@pytest.mark.anyio
async def test_failed_signature_does_not_consume_nonce(client, app, creation_payload):
    receipt = await create_receipt(client, creation_payload)
    valid_signature = receipt["signature"]
    receipt["signature"] = "AAAA"
    failed = (await client.post("/api/inference/accept", json=request_body(receipt, creation_payload))).json()
    assert failed["status"] == "REJECT"
    assert failed["checks"]["signature"] == "INVALID"
    receipt["signature"] = valid_signature
    corrected = (await client.post("/api/inference/accept", json=request_body(receipt, creation_payload))).json()
    assert corrected["status"] == "ACCEPT"
    assert corrected["checks"]["nonce"] == "VALID"


@pytest.mark.anyio
async def test_invalid_signature_with_replay_values_never_updates_state(client, app, creation_payload):
    original = await create_receipt(client, creation_payload)
    assert (await client.post("/api/inference/accept", json=request_body(original, creation_payload))).json()["status"] == "ACCEPT"
    replay_like = await create_receipt(client, creation_payload)
    replay_like["security"]["nonce"] = original["security"]["nonce"]
    replay_like["security"]["sequence"] = original["security"]["sequence"]
    replay_like["signature"] = "AAAA"
    result = (await client.post("/api/inference/accept", json=request_body(replay_like, creation_payload))).json()
    assert result["status"] == "REJECT"
    assert result["checks"]["signature"] == "INVALID"
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AcceptedInferenceRecord)) == 1


@pytest.mark.anyio
async def test_replay_state_survives_application_reopen(client, test_settings, creation_payload):
    receipt = await create_receipt(client, creation_payload)
    assert (await client.post("/api/inference/accept", json=request_body(receipt, creation_payload))).json()["status"] == "ACCEPT"
    reopened = create_app(test_settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=reopened), base_url="http://reopened") as new_client:
        result = (await new_client.post("/api/inference/accept", json=request_body(receipt, creation_payload))).json()
    assert result["status"] == "REJECT"
    assert any(item["attack_class"] == "REPLAY_DETECTED" for item in result["findings"])


@pytest.mark.anyio
async def test_two_acceptance_attempts_cannot_both_succeed(app, creation_payload):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://race") as setup:
        receipt = await create_receipt(setup, creation_payload)
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://race-a") as first,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://race-b") as second,
    ):
        responses = await asyncio.gather(
            first.post("/api/inference/accept", json=request_body(receipt, creation_payload)),
            second.post("/api/inference/accept", json=request_body(receipt, creation_payload)),
        )
    assert sorted(response.json()["status"] for response in responses) == ["ACCEPT", "REJECT"]
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AcceptedInferenceRecord)) == 1
