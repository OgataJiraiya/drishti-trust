from concurrent.futures import ThreadPoolExecutor
import time

import pytest
from sqlalchemy import func, select

from backend.database.models import InferenceReceiptRecord
from backend.database.repository import ReceiptRepository
from backend.schemas.inference import CreateReceiptRequest


async def _verify(client, receipt, payload, **artifact_changes):
    artifacts = {
        "input_base64": payload["input_base64"],
        "model_sha256": payload["model_sha256"],
        "preprocessing": payload["preprocessing"],
        "config": payload["config"],
        "output": payload["output"],
    }
    artifacts.update(artifact_changes)
    return await client.post("/api/inference/verify", json={"receipt": receipt, "artifacts": artifacts})


@pytest.mark.anyio
async def test_create_store_and_verify_valid_receipt(client, creation_payload):
    created = await client.post("/api/inference/receipt", json=creation_payload)
    assert created.status_code == 201
    receipt = created.json()
    verified = await _verify(client, receipt, creation_payload)
    assert verified.status_code == 200
    result = verified.json()
    assert result["valid"] is True
    assert result["status"] == "ACCEPT"
    assert set(result["checks"].values()) == {"VALID"}
    assert (await client.get(f"/api/inference/{receipt['receipt_id']}")).json() == receipt


@pytest.mark.anyio
async def test_altered_output_is_critical_tampering(client, creation_payload):
    receipt = (await client.post("/api/inference/receipt", json=creation_payload)).json()
    result = (await _verify(client, receipt, creation_payload, output={"class": "tree", "confidence": 0.94})).json()
    assert result["status"] == "REJECT"
    assert result["checks"]["signature"] == "VALID"
    assert result["checks"]["output_digest"] == "INVALID"
    finding = next(item for item in result["findings"] if item["attack_class"] == "OUTPUT_TAMPERING")
    assert finding["severity"] == "CRITICAL"


@pytest.mark.anyio
async def test_corrupt_signature_is_rejected(client, creation_payload):
    receipt = (await client.post("/api/inference/receipt", json=creation_payload)).json()
    receipt["signature"] = "AAAA"
    result = (await _verify(client, receipt, creation_payload)).json()
    assert result["status"] == "REJECT"
    assert result["checks"]["signature"] == "INVALID"
    assert any(item["attack_class"] == "SIGNATURE_INVALID" for item in result["findings"])


@pytest.mark.anyio
async def test_validation_is_clean_4xx(client):
    response = await client.post("/api/inference/receipt", json={})
    assert response.status_code == 422
    assert "detail" in response.json()


def test_concurrent_receipt_creation_allocates_distinct_sequences(app, creation_payload, monkeypatch):
    original = ReceiptRepository.next_sequence

    def synchronized_read(repository):
        value = original(repository)
        # Widen the real read-then-write race without changing its ordering.
        time.sleep(0.2)
        return value

    monkeypatch.setattr(ReceiptRepository, "next_sequence", synchronized_read)

    def create_one():
        with app.state.session_factory() as session:
            return app.state.provenance_service.create_receipt(
                CreateReceiptRequest.model_validate(creation_payload), session
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _index: create_one(), range(2)))

    assert sorted(receipt.security.sequence for receipt in receipts) == [1, 2]
    assert len({receipt.receipt_id for receipt in receipts}) == 2
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(InferenceReceiptRecord)) == 2
