from __future__ import annotations
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.core.config import Settings
from backend.main import create_app
from drishti_sdk.client import DrishtiClient
from modules.model_integrity.integration import ModelIntegrityRunBuilder
from modules.model_integrity.demo import (ModelIntegrityFinalOrchestrator, SCENARIOS,
    ScenarioKind, create_behavioral_specimen, create_static_specimen)
from modules.model_integrity.findings import FindingMappingContext, ModelIntegrityEvidenceBundle


@pytest.fixture
def anyio_backend(): return "asyncio"


def specimens(tmp_path: Path, kind: ScenarioKind):
    reference = create_static_specimen(tmp_path / "reference.onnx", ScenarioKind.CLEAN)
    if kind == ScenarioKind.CLEAN:
        candidate = tmp_path / "candidate.onnx"; candidate.write_bytes(reference.read_bytes())
    else: candidate = create_static_specimen(tmp_path / "candidate.onnx", kind)
    return reference, candidate


def run(tmp_path: Path, kind: ScenarioKind):
    reference, candidate = specimens(tmp_path, kind)
    service = ModelIntegrityFinalOrchestrator()
    return service, reference, candidate, service.run_static_scenario(SCENARIOS[kind], reference, candidate)


def test_branch_and_frozen_base_assumptions_documented():
    text = Path("docs/MODEL_INTEGRITY_FINAL_DEMO.md").read_text()
    assert "feat/model-final-integration" in text and "8c3229a" in text


def test_clean_exact_equality_no_fake_finding_and_limitation(tmp_path):
    _, _, _, result = run(tmp_path, ScenarioKind.CLEAN)
    assert (result.artifact_state, result.structural_state, result.parameter_metadata_state,
            result.parameter_value_state) == ("SAME", "SAME", "SAME", "SAME")
    assert result.finding_count == 0 and "MODEL_SAFE" not in result.finding_categories
    assert result.backend_submission_status == "OFFLINE_NOT_SUBMITTED"
    assert any("remains UNKNOWN" in item for item in result.limitations)


def test_weight_change_preserves_structure_and_names_tensor(tmp_path):
    _, _, _, result = run(tmp_path, ScenarioKind.WEIGHT_CHANGE)
    assert result.artifact_state == "CHANGED" and result.structural_state == "SAME"
    assert result.parameter_metadata_state == "SAME" and result.parameter_value_state == "CHANGED"
    assert result.changed_tensors == ("weight",) and "PARAMETER_VALUE_CHANGED" in result.finding_categories


def test_structure_change_is_explainable_and_mapped(tmp_path):
    service, reference, candidate, result = run(tmp_path, ScenarioKind.STRUCTURE_CHANGE)
    comparison = service.comparison_service.compare(reference, candidate)
    assert result.structural_state == "CHANGED" and "STRUCTURAL_FINGERPRINT_CHANGED" in result.finding_categories
    assert comparison.structure.operator_changes or comparison.structure.initializer_changes


def test_nan_is_exact_strong_observation(tmp_path):
    service, _, candidate, result = run(tmp_path, ScenarioKind.PARAMETER_NAN)
    manifest = service.inspection_service.inspect(candidate)
    issues = [item for item in manifest.parameter_analysis.issues if item.code == "PARAMETER_NAN"]
    assert len(issues) == 1 and issues[0].tensor_name == "weight"
    assert "PARAMETER_NAN" in result.finding_categories and "QUARANTINE" in result.recommendations


def test_static_path_never_constructs_behavior_service(tmp_path, monkeypatch):
    reference, candidate = specimens(tmp_path, ScenarioKind.WEIGHT_CHANGE)
    monkeypatch.setattr("modules.model_integrity.behavioral.BehavioralIntegrityService.__init__",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runtime constructed")))
    ModelIntegrityFinalOrchestrator().run_static_scenario(SCENARIOS[ScenarioKind.WEIGHT_CHANGE], reference, candidate)


def test_behavior_requires_explicit_path_and_detects_new_sensitivity(tmp_path):
    reference = create_behavioral_specimen(tmp_path / "clean.onnx", False)
    candidate = create_behavioral_specimen(tmp_path / "sensitive.onnx", True)
    service = ModelIntegrityFinalOrchestrator()
    with pytest.raises(ValueError): service.run_static_scenario(SCENARIOS[ScenarioKind.TRIGGER_SENSITIVE], reference, candidate)
    result = service.run_behavioral_scenario(SCENARIOS[ScenarioKind.TRIGGER_SENSITIVE], reference, candidate)
    assert result.behavioral_protocol_status == "COMPLETE"
    assert "NEW_TRIGGER_SENSITIVITY" in result.finding_categories
    assert "confirmed backdoor" not in json.dumps(result.to_dict()).lower()


def test_candidate_identity_conflict_fails_closed(tmp_path):
    service, reference, candidate, _ = run(tmp_path, ScenarioKind.WEIGHT_CHANGE)
    with pytest.raises(ValueError, match="identities disagree"):
        service.run_static_scenario(SCENARIOS[ScenarioKind.WEIGHT_CHANGE], reference, candidate,
            caller_candidate_artifact_id="model:sha256:" + "0" * 64)


def test_reference_verification_and_mismatch_semantics(tmp_path):
    service, reference, candidate, _ = run(tmp_path, ScenarioKind.WEIGHT_CHANGE)
    digest = service.inspection_service.inspect(reference).artifact.sha256
    verified = service.run_static_scenario(SCENARIOS[ScenarioKind.WEIGHT_CHANGE], reference, candidate,
        expected_reference_sha256=digest)
    assert verified.reference_status == "VERIFIED_REFERENCE"
    mismatch = service.run_static_scenario(SCENARIOS[ScenarioKind.WEIGHT_CHANGE], reference, candidate,
        expected_reference_sha256="0" * 64)
    assert mismatch.reference_status == "DESIGNATED_ONLY" and "EXPECTED_REFERENCE_SHA256_MISMATCH" in mismatch.limitations


def test_deterministic_report_order_json_and_no_leakage(tmp_path):
    results = [run(tmp_path / kind.value, kind)[3] for kind in
               (ScenarioKind.WEIGHT_CHANGE, ScenarioKind.CLEAN, ScenarioKind.PARAMETER_NAN)]
    first = ModelIntegrityFinalOrchestrator.report(results)
    second = ModelIntegrityFinalOrchestrator.report(reversed(results))
    assert first == second and first.report_id.startswith("model-demo:sha256:")
    encoded = first.to_json(indent=None); json.loads(encoded)
    forbidden = ("/home/", "PRIVATE KEY", "bearer", "token=", "api_key=", "raw_data", "sample_values")
    assert not any(item.lower() in encoded.lower() for item in forbidden)


def test_findings_order_and_ids_deterministic(tmp_path):
    first = run(tmp_path / "a", ScenarioKind.PARAMETER_NAN)[3]
    second = run(tmp_path / "b", ScenarioKind.PARAMETER_NAN)[3]
    assert first.finding_ids == second.finding_ids and first.finding_categories == second.finding_categories


def test_numpy_inputs_are_not_mutated_by_behavior(tmp_path):
    # The explicit orchestration creates its corpus locally; lower-level immutability is frozen M3 behavior.
    sample = np.full((1, 1, 8, 8), .2, np.float32); before = sample.copy()
    create_behavioral_specimen(tmp_path / "model.onnx", False)
    assert np.array_equal(sample, before)


def test_finding_contract_shape_unchanged(tmp_path):
    service, reference, candidate, _ = run(tmp_path, ScenarioKind.PARAMETER_NAN)
    manifest = service.inspection_service.inspect(candidate)
    comparison = service.comparison_service.compare(reference, candidate)
    findings = service._mapper().map(ModelIntegrityEvidenceBundle(manifest=manifest, comparison=comparison),
        FindingMappingContext(manifest.artifact.artifact_id))
    expected = {"finding_id", "module", "asset_type", "asset_id", "category", "severity",
                "confidence", "reason", "evidence", "recommendation", "limitations"}
    assert set(findings[0].model_dump()) == expected
    assert isinstance(findings[0].evidence, list) and isinstance(findings[0].limitations, list)


def test_synthetic_files_are_caller_scoped(tmp_path):
    target = tmp_path / "only-here.onnx"; create_static_specimen(target, ScenarioKind.CLEAN)
    assert target.is_file()


@pytest.mark.anyio
async def test_m6_genuine_signed_replay_lifecycle_assurance_and_audit(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", key_dir=tmp_path / "keys",
        admin_bearer_token="M6-TEST-ADMIN", internal_ingest_bearer_token="M6-TEST-ADMIN",
        allow_unsigned_ingestion=True)
    app = create_app(settings)
    headers = {"Authorization": "Bearer M6-TEST-ADMIN"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://m6", headers=headers) as backend:
        service, reference, candidate, result = run(tmp_path / "specimen", ScenarioKind.WEIGHT_CHANGE)
        manifest = service.inspection_service.inspect(candidate)
        comparison = service.comparison_service.compare(reference, candidate)
        local = DrishtiClient(); builder = ModelIntegrityRunBuilder(local)
        findings = builder.map_findings(ModelIntegrityEvidenceBundle(manifest=manifest, comparison=comparison),
            FindingMappingContext(result.candidate_artifact_id))
        assessment = "M6-SIGNED-WEIGHT"
        assert (await backend.post("/api/assessments", json={"assessment_id": assessment,
            "name": "M6 signed weight", "metadata": {}})).status_code == 201
        assert (await backend.post(f"/api/assessments/{assessment}/activate")).status_code == 200
        key = Ed25519PrivateKey.generate()
        pem = key.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        assert (await backend.post("/api/producers", json={"producer_id": "m6-model",
            "display_name": "M6 Model Integrity", "module": "model_integrity", "metadata": {}})).status_code == 200
        assert (await backend.post("/api/producers/m6-model/keys", json={"key_id": "m6-key",
            "public_key_pem": pem})).status_code == 200
        signed_run = builder.build_run(assessment_id=assessment, producer="m6-model",
            producer_version="M6", findings=findings)
        signature = builder.sign_run(signed_run, private_key=key)
        payload = {"run": signed_run.model_dump(mode="json"), "key_id": "m6-key", "signature": signature}
        first = await backend.post("/api/integration/signed-runs", json=payload)
        second = await backend.post("/api/integration/signed-runs", json=payload)
        assert first.status_code == second.status_code == 200
        assert first.json()["result"] == "CREATED" and second.json()["result"] == "EXISTS"
        persisted = (await backend.get(f"/api/integration/runs/{signed_run.run_id}")).json()
        assert persisted["module"] == "model_integrity" and persisted["authentication"]["authenticated"] is True
        stored_findings = [(await backend.get(f"/api/evidence/{item.finding_id}")).json() for item in findings]
        assert stored_findings == [item.model_dump(mode="json") for item in findings]
        summary = (await backend.get(f"/api/summary?assessment_id={assessment}&trust_scope=authenticated")).json()
        assert summary and "fully trusted" not in json.dumps(summary).lower()
        tampered = json.loads(json.dumps(payload)); tampered["run"]["findings"][0]["category"] += "_TAMPERED"
        assert (await backend.post("/api/integration/signed-runs", json=tampered)).status_code in {401, 403}
        sealed = await backend.post(f"/api/assessments/{assessment}/seal")
        assert sealed.status_code == 200
        assert (await backend.get(f"/api/assessments/{assessment}/snapshot/verify")).json()["status"] == "VALID"
        assert (await backend.post("/api/integration/signed-runs", json=payload)).status_code == 409
        assert (await backend.post("/api/audit/outbox/drain")).status_code == 200
        assert (await backend.get("/api/audit/verify")).json()["status"] == "VALID"
        checkpoint = (await backend.post("/api/audit/checkpoints")).json()["bundle"]
        assert (await backend.post("/api/audit/checkpoints/verify", json=checkpoint)).json()["status"] == "VALID"
        local.close()
