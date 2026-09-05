import pytest


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
