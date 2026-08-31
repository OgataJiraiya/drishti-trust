from __future__ import annotations
import copy

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.integrations.signing import module_run_signing_bytes
from drishti_sdk import (
    DatasetIntegrityAdapter, DistributionShiftAdapter, DrishtiClient, DrishtiError,
    InferenceIntegrityAdapter, ModelIntegrityAdapter, ValidationError,
    deterministic_finding_id,
)


def _sdk() -> DrishtiClient:
    return DrishtiClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))


def _finding(sdk: DrishtiClient, module="model_integrity"):
    return sdk.build_finding(module=module, asset_type="model", asset_id="MODEL-1",
        category="PARAMETER_OUTLIER", severity="HIGH", confidence=.94,
        reason="Stable detector output", evidence=["layer=4"], recommendation="REVIEW",
        limitations=["Demo"], stable_evidence_key={"layer": 4})


def test_sdk_build_finding_and_frozen_fields():
    finding = _finding(_sdk())
    assert list(finding.model_dump()) == ["finding_id", "module", "asset_type", "asset_id",
        "category", "severity", "confidence", "reason", "evidence", "recommendation", "limitations"]


@pytest.mark.parametrize("field,value", [("confidence", 1.1), ("severity", "EXTREME")])
def test_sdk_rejects_invalid_finding(field, value):
    sdk = _sdk()
    kwargs = dict(module="model_integrity", asset_type="model", asset_id="M", category="C",
        severity="LOW", confidence=.5, reason="r", evidence=["e"], recommendation="REVIEW",
        stable_evidence_key="k")
    kwargs[field] = value
    with pytest.raises(ValidationError): sdk.build_finding(**kwargs)


def test_deterministic_finding_id_is_stable_and_sensitive():
    first = deterministic_finding_id("model_integrity", "M", "C", {"layer": 4})
    assert first == deterministic_finding_id("model_integrity", "M", "C", {"layer": 4})
    assert first != deterministic_finding_id("model_integrity", "M", "C", {"layer": 5})


def test_build_run_rejects_module_mismatch_and_requires_assessment():
    sdk = _sdk(); finding = _finding(sdk)
    with pytest.raises(ValidationError):
        sdk.build_run(module="dataset_integrity", assessment_id="A", producer="p",
                      producer_version="1", findings=[finding])
    with pytest.raises(ValidationError):
        sdk.build_run(module="model_integrity", assessment_id="", producer="p",
                      producer_version="1", findings=[finding])


def test_sign_run_binds_assessment_id():
    sdk = _sdk(); key = Ed25519PrivateKey.generate(); finding = _finding(sdk)
    run = sdk.build_run(module="model_integrity", assessment_id="A", producer="p",
                        producer_version="1", findings=[finding])
    signature = sdk.sign_run(run, private_key=key)
    changed = run.model_copy(update={"assessment_id": "B"})
    assert module_run_signing_bytes(run) != module_run_signing_bytes(changed)
    with pytest.raises(Exception): key.public_key().verify(__import__("base64").b64decode(signature), module_run_signing_bytes(changed))


def test_helpers_and_bounded_connection_error():
    def handler(request):
        if request.url.path == "/api/assessments/current": return httpx.Response(200, json={"assessment_id": "A"})
        if request.url.path == "/api/summary": return httpx.Response(200, json={"overall": {"score_status": "COMPLETE"}})
        raise httpx.ConnectError("secret huge request body", request=request)
    sdk = DrishtiClient(transport=httpx.MockTransport(handler))
    assert sdk.get_current_assessment()["assessment_id"] == "A"
    assert sdk.get_summary("A")["overall"]["score_status"] == "COMPLETE"
    with pytest.raises(DrishtiError, match="backend is unavailable") as error: sdk.health()
    assert "secret" not in str(error.value)


def _pem(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()


async def _register(client, producer, key_id, key, module="model_integrity"):
    assert (await client.post("/api/producers", json={"producer_id": producer,
        "display_name": producer, "module": module, "metadata": {}})).status_code == 200
    assert (await client.post(f"/api/producers/{producer}/keys", json={
        "key_id": key_id, "public_key_pem": _pem(key)})).status_code == 200


@pytest.mark.anyio
async def test_sdk_signed_envelope_accepted_idempotent_and_wrong_key_rejected(client):
    sdk = _sdk(); key = Ed25519PrivateKey.generate(); wrong = Ed25519PrivateKey.generate()
    await _register(client, "sdk-model", "sdk-key", key)
    await client.post("/api/assessments", json={"assessment_id": "SDK-A", "name": "SDK", "metadata": {}})
    await client.post("/api/assessments/SDK-A/activate")
    run = sdk.build_run(module="model_integrity", assessment_id="SDK-A", producer="sdk-model",
        producer_version="1", findings=[_finding(sdk)], run_id="SDK-RUN")
    envelope = {"run": run.model_dump(mode="json"), "key_id": "sdk-key",
                "signature": sdk.sign_run(run, private_key=key)}
    assert (await client.post("/api/integration/signed-runs", json=envelope)).json()["result"] == "CREATED"
    assert (await client.post("/api/integration/signed-runs", json=envelope)).json()["result"] == "EXISTS"
    bad = {**envelope, "signature": sdk.sign_run(run, private_key=wrong)}
    assert (await client.post("/api/integration/signed-runs", json=bad)).status_code == 401


@pytest.mark.anyio
async def test_sdk_run_rejected_for_draft_revoked_key_and_sealed(client):
    sdk = _sdk(); key = Ed25519PrivateKey.generate()
    await _register(client, "sdk-state", "sdk-state-key", key)
    await client.post("/api/assessments", json={"assessment_id": "SDK-D", "name": "SDK", "metadata": {}})
    run = sdk.build_run(module="model_integrity", assessment_id="SDK-D", producer="sdk-state",
        producer_version="1", findings=[_finding(sdk)], run_id="SDK-DRAFT-RUN")
    envelope = {"run": run.model_dump(mode="json"), "key_id": "sdk-state-key",
                "signature": sdk.sign_run(run, private_key=key)}
    assert (await client.post("/api/integration/signed-runs", json=envelope)).status_code == 409
    await client.post("/api/assessments/SDK-D/activate")
    assert (await client.post("/api/integration/signed-runs", json=envelope)).status_code == 200
    await client.post("/api/assessments/SDK-D/seal")
    assert (await client.post("/api/integration/signed-runs", json=envelope)).status_code == 409
    await client.post("/api/producers/sdk-state/keys/sdk-state-key/revoke")
    assert (await client.post("/api/integration/signed-runs", json=envelope)).status_code == 403


@pytest.mark.anyio
async def test_all_four_sdk_adapters_reach_complete_trusted_summary(client):
    sdk = _sdk()
    await client.post("/api/assessments", json={"assessment_id": "SDK-ALL", "name": "All", "metadata": {}})
    await client.post("/api/assessments/SDK-ALL/activate")
    adapters = [DatasetIntegrityAdapter, ModelIntegrityAdapter,
                InferenceIntegrityAdapter, DistributionShiftAdapter]
    for index, adapter_type in enumerate(adapters):
        adapter = adapter_type(sdk); module = adapter.module
        key = Ed25519PrivateKey.generate(); producer = f"sdk-{module}"
        await _register(client, producer, f"key-{index}", key, module)
        finding = adapter.finding(asset_type="asset", asset_id=f"A-{index}", category="DEMO",
            severity="INFO", confidence=.9, reason="Adapter integration evidence",
            evidence=[f"signal={index}"], recommendation="ACCEPT",
            stable_evidence_key={"index": index})
        run = sdk.build_run(module=module, assessment_id="SDK-ALL", producer=producer,
            producer_version="1", findings=[finding], run_id=f"SDK-ALL-{index}")
        envelope = {"run": run.model_dump(mode="json"), "key_id": f"key-{index}",
                    "signature": sdk.sign_run(run, private_key=key)}
        assert (await client.post("/api/integration/signed-runs", json=envelope)).status_code == 200
    summary = (await client.get("/api/summary?assessment_id=SDK-ALL")).json()
    assert summary["overall"]["assessment_coverage"] == 1.0
    assert summary["overall"]["score_status"] == "COMPLETE"
