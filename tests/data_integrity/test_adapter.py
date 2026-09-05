from __future__ import annotations

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.core.config import Settings
from backend.main import create_app
from backend.schemas.evidence import Finding
from drishti_sdk import DrishtiClient
from modules.data_integrity.adapter import DatasetIntegrityRunBuilder
from modules.data_integrity.analyzer import analyze_dataset


FROZEN_FIELDS = [
    "finding_id", "module", "asset_type", "asset_id", "category", "severity",
    "confidence", "reason", "evidence", "recommendation", "limitations",
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path):
    app = create_app(Settings(
        data_dir=tmp_path / "data", key_dir=tmp_path / "keys",
        admin_bearer_token="DATA-INTEGRATION", internal_ingest_bearer_token="DATA-INTEGRATION",
        allow_unsigned_ingestion=False,
    ))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://data",
        headers={"Authorization": "Bearer DATA-INTEGRATION"},
    ) as value:
        yield value


def _sdk() -> DrishtiClient:
    return DrishtiClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"result": "CREATED"})
    ))


def _public_pem(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")


@pytest.mark.anyio
async def test_real_source_metadata_reaches_signed_evidence_and_audit(client, monkeypatch, tmp_path):
    images = tmp_path / "fixture" / "images" / "train"
    labels = tmp_path / "fixture" / "labels" / "train"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    (tmp_path / "fixture" / "classes.txt").write_text("plane\n")
    (images / "one.jpg").touch()
    (images / "two.jpg").touch()
    monkeypatch.setattr("modules.data_integrity.analyzer.load_yolo", lambda *args, **kwargs: [{
        "sample_id": "one.jpg", "labels": ["plane"],
        "contributor_id": "source-a", "batch_id": "batch-1",
    }])
    monkeypatch.setattr("modules.data_integrity.analyzer.find_exact_duplicates",
                        lambda *args, **kwargs: {"a" * 64: ["one.jpg", "two.jpg"]})
    monkeypatch.setattr("modules.data_integrity.analyzer.find_label_anomalies", lambda *args: [])
    result = analyze_dataset(str(tmp_path / "fixture"), run_phash=False, run_ood=False)
    builder = DatasetIntegrityRunBuilder(_sdk())
    findings = builder.map_findings(result["findings"])
    assert len(findings) == 1
    assert list(findings[0].model_dump(mode="json")) == FROZEN_FIELDS == list(Finding.model_fields)
    assert "contributor_id=source-a" in findings[0].evidence
    assert "batch_id=batch-1" in findings[0].evidence
    assert "/home/" not in str(findings[0].model_dump(mode="json"))

    key = Ed25519PrivateKey.generate()
    await client.post("/api/producers", json={"producer_id": "data-final",
        "display_name": "Dataset Integrity", "module": "dataset_integrity", "metadata": {}})
    await client.post("/api/producers/data-final/keys", json={
        "key_id": "data-key", "public_key_pem": _public_pem(key)})
    await client.post("/api/assessments", json={
        "assessment_id": "DATA-SOURCE", "name": "Source risk", "metadata": {}})
    await client.post("/api/assessments/DATA-SOURCE/activate")
    run = builder.build_run(assessment_id="DATA-SOURCE", producer="data-final",
        producer_version="1", findings=findings, run_id="DATA-SOURCE-RUN")
    envelope = {"run": run.model_dump(mode="json"), "key_id": "data-key",
                "signature": builder.sign_run(run, private_key=key)}
    submitted = await client.post("/api/integration/signed-runs", json=envelope)
    assert submitted.status_code == 200 and submitted.json()["result"] == "CREATED"
    evidence = (await client.get(f"/api/evidence/{findings[0].finding_id}")).json()
    assert evidence == findings[0].model_dump(mode="json")
    summary = (await client.get(
        "/api/summary?assessment_id=DATA-SOURCE&trust_scope=authenticated")).json()
    assert summary["modules"]["dataset_integrity"]["finding_count"] == 1
    assert (await client.get("/api/audit/verify")).json()["status"] == "VALID"
