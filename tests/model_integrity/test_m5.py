from __future__ import annotations

from dataclasses import asdict, replace
import base64
import copy
import json
import math

import httpx
import numpy as np
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.core.assurance_policy import SEVERITY_PENALTIES
from backend.core.config import Settings
from backend.main import create_app
from backend.schemas.evidence import Finding
from drishti_sdk import DrishtiClient, ModelIntegrityAdapter
from modules.model_integrity.baseline import BaselineComparisonService
from modules.model_integrity.baseline_models import ComparisonStatus
from modules.model_integrity.behavioral_models import (
    BehavioralAnalysisReport, BehavioralAnalysisStatus, BehavioralCoverage, BehavioralIssue,
)
from modules.model_integrity.findings import (
    FindingMappingContext, FindingMappingPolicy, ModelIntegrityEvidenceBundle,
    ModelIntegrityFindingMapper, mapping_policy_inventory,
)
from modules.model_integrity.integration import ModelIntegrityRunBuilder
from modules.model_integrity.models import ParameterIssue, StructuralIssue
from modules.model_integrity.service import ModelIntegrityService
from tests.model_integrity.test_m4 import make_static


FROZEN_FIELDS = ["finding_id", "module", "asset_type", "asset_id", "category", "severity",
    "confidence", "reason", "evidence", "recommendation", "limitations"]


@pytest.fixture
def anyio_backend(): return "asyncio"


@pytest.fixture
async def integration_client(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", key_dir=tmp_path / "keys",
        admin_bearer_token="M5-TEST-BEARER", internal_ingest_bearer_token="M5-TEST-BEARER",
        allow_unsigned_ingestion=False)
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://m5",
        headers={"Authorization": "Bearer M5-TEST-BEARER"}) as value:
        yield value


def sdk():
    return DrishtiClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))


def mapper(policy=None):
    return ModelIntegrityFindingMapper(ModelIntegrityAdapter(sdk()), policy=policy)


def manifest_with_issues(tmp_path, *, structural=(), parameter=()):
    manifest = ModelIntegrityService().inspect(make_static(tmp_path / "model.onnx"))
    structural_issues = [StructuralIssue(code, "MEDIUM", ["node=x"], "internal") for code in structural]
    parameter_issues = [ParameterIssue(code, "MEDIUM", "weight", 1,
        ["tensor=weight", "channel=1", "robust_z=18.7"], "internal") for code in parameter]
    analysis = replace(manifest.parameter_analysis, issues=parameter_issues)
    return replace(manifest, issues=structural_issues, parameter_analysis=analysis)


def behavioral_report(*codes):
    coverage = BehavioralCoverage(8, 8, 8, 8, 0, 0, 1, 1, 1, 1, 1.0, 1.0)
    issues = [BehavioralIssue(code, "REVIEW",
        ["trigger=solid_patch_bottom_right", "flip_rate=1", "secret_token=never-export"], "internal")
        for code in codes]
    return BehavioralAnalysisReport(BehavioralAnalysisStatus.COMPLETE, "runtime", coverage,
                                    True, [], [], issues, [])


def test_frozen_finding_schema_and_model_identity(tmp_path):
    manifest = manifest_with_issues(tmp_path, parameter=["PARAMETER_NAN"])
    finding = mapper().map(ModelIntegrityEvidenceBundle(manifest=manifest))[0]
    data = finding.model_dump(mode="json")
    assert list(data) == FROZEN_FIELDS == list(Finding.model_fields)
    assert finding.module.value == "model_integrity" and finding.asset_type == "model"
    assert finding.asset_id == manifest.artifact.artifact_id
    assert manifest.artifact.filename not in finding.asset_id
    assert isinstance(finding.evidence, list) and all(isinstance(item, str) for item in finding.evidence)
    assert isinstance(finding.limitations, list) and all(isinstance(item, str) for item in finding.limitations)
    assert not {"timestamp", "attack_class", "metadata", "run_id"} & set(data)


def test_mapping_deterministic_order_ids_json_and_no_safe_finding(tmp_path):
    manifest = manifest_with_issues(tmp_path, structural=["CUSTOM_OPERATOR_DOMAIN"],
        parameter=["CHANNEL_NORM_OUTLIER", "PARAMETER_NAN"])
    first = mapper().map(ModelIntegrityEvidenceBundle(manifest=manifest))
    second = mapper().map(ModelIntegrityEvidenceBundle(manifest=manifest))
    assert first == second
    assert [item.finding_id for item in first] == [item.finding_id for item in second]
    assert "MODEL_SAFE" not in {item.category for item in first}
    for finding in first:
        assert np.isfinite(finding.confidence) and 0 <= finding.confidence <= 1
        encoded = json.dumps(finding.model_dump(mode="json"), allow_nan=False)
        assert ": Infinity" not in encoded and ": NaN" not in encoded and ": -Infinity" not in encoded


@pytest.mark.parametrize("code", ["CUSTOM_OPERATOR_DOMAIN", "OPERATOR_NOT_APPROVED",
    "EXTERNAL_DATA_LOCATION_MISSING", "EXTERNAL_DATA_PATH_UNSAFE", "EXTERNAL_DATA_NOT_VERIFIED",
    "NO_GRAPH_INPUTS", "NO_GRAPH_OUTPUTS", "DYNAMIC_OR_UNKNOWN_SHAPE", "EMPTY_GRAPH",
    "INITIALIZER_NAME_MISSING"])
def test_m1_mapping_inventory(tmp_path, code):
    findings = mapper().map(ModelIntegrityEvidenceBundle(
        manifest=manifest_with_issues(tmp_path, structural=[code])))
    assert {item.category for item in findings} == {code}


@pytest.mark.parametrize("code", ["PARAMETER_NAN", "PARAMETER_POSITIVE_INFINITY",
    "PARAMETER_NEGATIVE_INFINITY", "ALL_ZERO_TENSOR", "CONSTANT_TENSOR",
    "EXTREME_ZERO_FRACTION", "TENSOR_SCALE_OUTLIER", "CHANNEL_NORM_OUTLIER", "DEAD_CHANNEL",
    "EXTREME_CHANNEL_SCALE", "PARAMETER_ENERGY_CONCENTRATION", "PARAMETER_ANALYSIS_PARTIAL",
    "PARAMETER_ANALYSIS_UNAVAILABLE"])
def test_m2_mapping_policy_and_statistical_limitations(tmp_path, code):
    findings = mapper().map(ModelIntegrityEvidenceBundle(
        manifest=manifest_with_issues(tmp_path, parameter=[code])))
    assert findings[0].category == code
    assert findings[0].recommendation.value in {"REVIEW", "QUARANTINE"}
    if code in {"CHANNEL_NORM_OUTLIER", "PARAMETER_ENERGY_CONCENTRATION"}:
        assert "legitimate" in findings[0].limitations[0].lower()


@pytest.mark.parametrize("code", ["BEHAVIOR_ANALYSIS_UNAVAILABLE", "BEHAVIOR_ANALYSIS_PARTIAL",
    "RUNTIME_TIMEOUT", "RUNTIME_FAILURE", "OUTPUT_NONFINITE", "OUTPUT_CONTRACT_CHANGED",
    "NONDETERMINISTIC_OUTPUT", "TRIGGER_OUTPUT_DIVERGENCE", "TRIGGER_PREDICTION_FLIP",
    "TRIGGER_TARGET_CONCENTRATION", "TRIGGER_SENSITIVITY"])
def test_m3_mapping_is_observational_and_redacts_secrets(tmp_path, code):
    manifest = manifest_with_issues(tmp_path)
    findings = mapper().map(ModelIntegrityEvidenceBundle(manifest=manifest,
        behavioral=behavioral_report(code)))
    finding = next(item for item in findings if item.category == code)
    text = json.dumps(finding.model_dump(mode="json"), allow_nan=False).lower()
    assert "backdoor detected" not in text and "confirmed backdoor" not in text
    assert "never-export" not in text and "<redacted>" in text


def test_m4_static_mapping_designated_and_verified_wording(tmp_path):
    reference = make_static(tmp_path / "reference.onnx")
    candidate = make_static(tmp_path / "candidate.onnx", weight=2)
    designated = BaselineComparisonService().compare(reference, candidate)
    findings = mapper().map(ModelIntegrityEvidenceBundle(comparison=designated))
    categories = {item.category for item in findings}
    assert {"REFERENCE_ARTIFACT_MISMATCH", "PARAMETER_VALUE_CHANGED"} <= categories
    assert all("approved" not in item.reason.lower() and "malicious" not in item.reason.lower()
               for item in findings)
    digest = ModelIntegrityService().inspect(reference).artifact.sha256
    verified = BaselineComparisonService().compare(reference, candidate,
        expected_reference_sha256=digest, reference_registry_state="APPROVED")
    mapped = mapper().map(ModelIntegrityEvidenceBundle(comparison=verified))
    assert any("reference_status=VERIFIED_REFERENCE" in item.evidence for item in mapped)


def test_m4_new_anomaly_and_behavior_comparison_mapping(tmp_path):
    reference = make_static(tmp_path / "r.onnx", weight=1)
    candidate = make_static(tmp_path / "c.onnx", weight=0)
    comparison = BaselineComparisonService().compare(reference, candidate)
    findings = mapper().map(ModelIntegrityEvidenceBundle(comparison=comparison))
    assert "NEW_PARAMETER_ANOMALY" in {item.category for item in findings}
    from tests.model_integrity.test_m4 import _behavior_reports
    ref, cand, protocol, *_ = _behavior_reports(tmp_path)
    from modules.model_integrity.baseline import compare_behavior_reports
    behavior = compare_behavior_reports(ref, cand, protocol, protocol)
    mapped = mapper().map(ModelIntegrityEvidenceBundle(comparison=comparison,
        behavioral_comparison=behavior))
    assert "NEW_TRIGGER_SENSITIVITY" in {item.category for item in mapped}
    incomparable = replace(behavior, status=ComparisonStatus.INCOMPARABLE,
        codes=["NEW_TRIGGER_SENSITIVITY"], limitations=["protocol mismatch"])
    mapped = mapper().map(ModelIntegrityEvidenceBundle(comparison=comparison,
        behavioral_comparison=incomparable))
    assert "NEW_TRIGGER_SENSITIVITY" not in {item.category for item in mapped}
    assert "BEHAVIOR_PROTOCOL_INCOMPARABLE" in {item.category for item in mapped}
    partial = replace(behavior, status=ComparisonStatus.PARTIAL,
        codes=["NEW_TRIGGER_SENSITIVITY"], limitations=["partial coverage"])
    mapped = mapper().map(ModelIntegrityEvidenceBundle(comparison=comparison,
        behavioral_comparison=partial))
    assert "NEW_TRIGGER_SENSITIVITY" not in {item.category for item in mapped}
    assert "BEHAVIOR_COMPARISON_PARTIAL" in {item.category for item in mapped}
    unavailable = replace(behavior, status=ComparisonStatus.UNAVAILABLE,
        codes=["NEW_TRIGGER_SENSITIVITY"], limitations=["no coverage"])
    mapped = mapper().map(ModelIntegrityEvidenceBundle(comparison=comparison,
        behavioral_comparison=unavailable))
    assert "NEW_TRIGGER_SENSITIVITY" not in {item.category for item in mapped}
    assert "BEHAVIOR_COMPARISON_UNAVAILABLE" in {item.category for item in mapped}


def test_identity_context_required_and_must_agree(tmp_path):
    behavior = behavioral_report("RUNTIME_FAILURE")
    with pytest.raises(ValueError): mapper().map(ModelIntegrityEvidenceBundle(behavioral=behavior))
    manifest = manifest_with_issues(tmp_path, parameter=["PARAMETER_NAN"])
    with pytest.raises(ValueError): mapper().map(ModelIntegrityEvidenceBundle(manifest=manifest),
        FindingMappingContext("model:sha256:" + "0"*64))


def test_deduplication_and_finding_bound(tmp_path):
    manifest = manifest_with_issues(tmp_path, parameter=["PARAMETER_NAN"] * 8)
    assert len(mapper().map(ModelIntegrityEvidenceBundle(manifest=manifest))) == 1
    codes = ["PARAMETER_NAN", "PARAMETER_POSITIVE_INFINITY", "PARAMETER_NEGATIVE_INFINITY",
             "CHANNEL_NORM_OUTLIER", "PARAMETER_ENERGY_CONCENTRATION"]
    manifest = manifest_with_issues(tmp_path, parameter=codes)
    findings = mapper(FindingMappingPolicy(max_findings=3)).map(ModelIntegrityEvidenceBundle(manifest=manifest))
    assert len(findings) == 3 and findings[-1].category == "FINDING_MAPPING_TRUNCATED"
    json.dumps([item.model_dump(mode="json") for item in findings], allow_nan=False)


def _behavior_with_distinct_issues(count, code="RUNTIME_FAILURE"):
    report = behavioral_report()
    return replace(report, issues=[BehavioralIssue(code, "REVIEW", [f"trigger=t-{index}"], "internal")
                                   for index in range(count)])


@pytest.mark.parametrize("count,expected,truncated", [
    (99, 99, False), (100, 100, False), (101, 100, True),
    (500, 100, True), (10_000, 100, True),
])
def test_exact_finding_boundaries_are_sorted_before_truncation(count, expected, truncated):
    identity = "model:sha256:" + "a" * 64
    forward = _behavior_with_distinct_issues(count)
    reverse = replace(forward, issues=list(reversed(forward.issues)))
    first = mapper().map(ModelIntegrityEvidenceBundle(behavioral=forward),
                         FindingMappingContext(identity))
    second = mapper().map(ModelIntegrityEvidenceBundle(behavioral=reverse),
                          FindingMappingContext(identity))
    assert len(first) == expected and first == second
    assert (first[-1].category == "FINDING_MAPPING_TRUNCATED") is truncated
    if truncated:
        assert sum(item.category == "FINDING_MAPPING_TRUNCATED" for item in first) == 1
        assert first[-1].severity.value == "LOW" and first[-1].recommendation.value == "REVIEW"


def test_truncation_retains_high_before_low_findings():
    identity = "model:sha256:" + "b" * 64
    low = _behavior_with_distinct_issues(500, "BEHAVIOR_ANALYSIS_PARTIAL")
    high = BehavioralIssue("TRIGGER_SENSITIVITY", "REVIEW", ["trigger=must-survive"], "internal")
    report = replace(low, issues=low.issues + [high])
    findings = mapper().map(ModelIntegrityEvidenceBundle(behavioral=report),
                            FindingMappingContext(identity))
    assert "TRIGGER_SENSITIVITY" in {item.category for item in findings}
    assert len(findings) == 100 and findings[-1].category == "FINDING_MAPPING_TRUNCATED"


def test_finding_id_collision_resistance_and_source_dedup(tmp_path):
    manifest = manifest_with_issues(tmp_path)
    issues = [ParameterIssue("CHANNEL_NORM_OUTLIER", "MEDIUM", "weight", channel,
              ["tensor=weight", f"channel={channel}"], "internal") for channel in (1, 2)]
    manifest = replace(manifest, parameter_analysis=replace(manifest.parameter_analysis, issues=issues))
    findings = mapper().map(ModelIntegrityEvidenceBundle(manifest=manifest))
    assert len({item.finding_id for item in findings}) == 2

    behavior = replace(behavioral_report(), issues=[
        BehavioralIssue("TRIGGER_SENSITIVITY", "REVIEW", [f"trigger={trigger}"], "internal")
        for trigger in ("a", "b", "a")])
    mapped = mapper().map(ModelIntegrityEvidenceBundle(manifest=manifest, behavioral=behavior))
    triggers = [item for item in mapped if item.category == "TRIGGER_SENSITIVITY"]
    assert len(triggers) == 2 and len({item.finding_id for item in triggers}) == 2

    other = replace(manifest, artifact=replace(manifest.artifact,
        sha256="c" * 64, artifact_id="model:sha256:" + "c" * 64))
    other_id = mapper().map(ModelIntegrityEvidenceBundle(manifest=other))[0].finding_id
    assert other_id not in {item.finding_id for item in findings}

    comparison = BaselineComparisonService().compare(
        make_static(tmp_path / "collision-reference.onnx"),
        make_static(tmp_path / "collision-candidate.onnx", weight=2))
    changed_context = replace(comparison, comparison_id="comparison:sha256:" + "e" * 64)
    original_ids = {item.finding_id for item in mapper().map(
        ModelIntegrityEvidenceBundle(comparison=comparison))}
    changed_ids = {item.finding_id for item in mapper().map(
        ModelIntegrityEvidenceBundle(comparison=changed_context))}
    assert original_ids.isdisjoint(changed_ids)


def test_secret_key_boundaries_and_benign_secret_tensor_value(tmp_path):
    manifest = manifest_with_issues(tmp_path)
    evidence = ["API_KEY=one", "Api-Key=two", "authorization_token=three",
                "BearerToken=four", "tensor=secret_head.weight"]
    issue = ParameterIssue("PARAMETER_NAN", "HIGH", "secret_head.weight", None, evidence, "internal")
    manifest = replace(manifest, parameter_analysis=replace(manifest.parameter_analysis, issues=[issue]))
    finding = mapper().map(ModelIntegrityEvidenceBundle(manifest=manifest))[0]
    text = " ".join(finding.evidence)
    assert not any(secret in text for secret in ("one", "two", "three", "four"))
    assert "tensor=secret_head.weight" in finding.evidence
    assert sum("<redacted>" in item for item in finding.evidence) == 4


def test_mapping_does_not_mutate_inputs(tmp_path):
    manifest = manifest_with_issues(tmp_path, parameter=["PARAMETER_NAN"])
    behavior = behavioral_report("TRIGGER_SENSITIVITY")
    before_manifest = copy.deepcopy(manifest)
    before_behavior = copy.deepcopy(behavior)
    bundle = ModelIntegrityEvidenceBundle(manifest=manifest, behavioral=behavior)
    first = mapper().map(bundle); second = mapper().map(bundle)
    assert manifest == before_manifest and behavior == before_behavior and first == second


def test_policy_has_only_frozen_conservative_values_and_finite_confidence():
    inventory = mapping_policy_inventory()
    assert all(item["severity"] in {"INFO", "LOW", "MEDIUM", "HIGH"}
               and item["recommendation"] in {"REVIEW", "QUARANTINE"}
               and math.isfinite(item["confidence"]) and 0 <= item["confidence"] <= 1
               for item in inventory.values())


def test_mapper_never_executes_behavior(tmp_path, monkeypatch):
    manifest = manifest_with_issues(tmp_path, parameter=["PARAMETER_NAN"])
    fail = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("execution called"))
    monkeypatch.setattr("modules.model_integrity.behavioral.BehavioralIntegrityService.__init__", fail)
    monkeypatch.setattr("modules.model_integrity.behavioral.ReferenceOnnxRuntime.run", fail)
    monkeypatch.setattr("onnx.reference.ReferenceEvaluator", fail)
    assert mapper().map(ModelIntegrityEvidenceBundle(manifest=manifest))


@pytest.mark.parametrize("value,expected", [
    (float("nan"), "unavailable"), (float("inf"), "unavailable"),
    (float("-inf"), "unavailable"), (1.234567890123456, "1.23456789012"),
    (-1e300, "-1e+300"), (None, "unavailable"),
])
def test_numeric_evidence_format_is_finite_bounded_and_deterministic(value, expected):
    assert ModelIntegrityFindingMapper._number(value) == expected


def test_existing_adapter_sdk_canonical_signing_and_assessment_binding(tmp_path):
    client = sdk(); builder = ModelIntegrityRunBuilder(client)
    manifest = manifest_with_issues(tmp_path, parameter=["PARAMETER_NAN"])
    findings = builder.map_findings(ModelIntegrityEvidenceBundle(manifest=manifest))
    run = builder.build_run(assessment_id="A", producer="person2", producer_version="M5", findings=findings)
    key = Ed25519PrivateKey.generate(); signature = builder.sign_run(run, private_key=key)
    assert signature == client.sign_run(run, private_key=key)
    changed = run.model_copy(update={"assessment_id": "B"})
    from backend.integrations.signing import module_run_signing_bytes
    with pytest.raises(Exception): key.public_key().verify(base64.b64decode(signature),
                                                            module_run_signing_bytes(changed))
    assert run.run_id == builder.build_run(assessment_id="A", producer="person2",
        producer_version="M5", findings=findings).run_id
    with pytest.raises(ValueError): builder.build_run(assessment_id="A", producer="person2",
                                                       producer_version="M5", findings=[])


def test_integration_wrapper_reuses_sdk_submission_and_result_is_json_safe(tmp_path):
    seen = []
    def handler(request):
        seen.append(request); return httpx.Response(200, json={"result": "CREATED"})
    client = DrishtiClient(transport=httpx.MockTransport(handler)); builder = ModelIntegrityRunBuilder(client)
    manifest = manifest_with_issues(tmp_path, parameter=["PARAMETER_NAN"])
    findings = builder.map_findings(ModelIntegrityEvidenceBundle(manifest=manifest))
    run = builder.build_run(assessment_id="A", producer="p", producer_version="M5", findings=findings)
    result = builder.submit_signed_run(run=run, key_id="k", candidate_artifact_id=manifest.artifact.artifact_id,
                                       private_key=Ed25519PrivateKey.generate())
    assert seen[0].url.path == "/api/integration/signed-runs"
    encoded = json.dumps(asdict(result), allow_nan=False)
    assert "private" not in encoded.lower() and result.backend_result == "CREATED"
    with pytest.raises(ValueError):
        builder.submit_signed_run(run=run, key_id="k", candidate_artifact_id="filename.onnx",
                                  private_key=Ed25519PrivateKey.generate())


def _public_pem(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()


@pytest.mark.anyio
async def test_signed_product_integration_tamper_replay_revocation_and_audit(integration_client, tmp_path):
    client = integration_client
    local = sdk(); builder = ModelIntegrityRunBuilder(local); key = Ed25519PrivateKey.generate()
    assert (await client.post("/api/producers", json={"producer_id": "person2-m5",
        "display_name": "Person 2 M5", "module": "model_integrity", "metadata": {}})).status_code == 200
    assert (await client.post("/api/producers/person2-m5/keys", json={"key_id": "m5-key",
        "public_key_pem": _public_pem(key)})).status_code == 200
    await client.post("/api/assessments", json={"assessment_id": "M5-A", "name": "M5", "metadata": {}})
    await client.post("/api/assessments/M5-A/activate")
    reference = make_static(tmp_path / "reference.onnx")
    candidate = make_static(tmp_path / "candidate.onnx", weight=2)
    comparison = BaselineComparisonService().compare(reference, candidate)
    findings = builder.map_findings(ModelIntegrityEvidenceBundle(comparison=comparison))
    run = builder.build_run(assessment_id="M5-A", producer="person2-m5",
        producer_version="M5", findings=findings, run_id="M5-SIGNED-RUN")
    envelope = {"run": run.model_dump(mode="json"), "key_id": "m5-key",
                "signature": builder.sign_run(run, private_key=key)}
    accepted = await client.post("/api/integration/signed-runs", json=envelope)
    assert accepted.status_code == 200 and accepted.json()["result"] == "CREATED"
    replay = await client.post("/api/integration/signed-runs", json=envelope)
    assert replay.status_code == 200 and replay.json()["result"] == "EXISTS"
    stored = (await client.get("/api/integration/runs/M5-SIGNED-RUN")).json()
    assert stored["module"] == "model_integrity" and stored["assessment_id"] == "M5-A"
    assert stored["authentication"]["authenticated"] is True
    mutations = [
        ("assessment_id", lambda body: body["run"].__setitem__("assessment_id", "M5-B")),
        ("run_id", lambda body: body["run"].__setitem__("run_id", "M5-TAMPERED-RUN")),
        ("category", lambda body: body["run"]["findings"][0].__setitem__("category", "TAMPERED")),
        ("confidence", lambda body: body["run"]["findings"][0].__setitem__("confidence", .1)),
        ("evidence", lambda body: body["run"]["findings"][0].__setitem__("evidence", ["tampered=true"])),
        ("recommendation", lambda body: body["run"]["findings"][0].__setitem__("recommendation", "ACCEPT")),
        ("asset_id", lambda body: body["run"]["findings"][0].__setitem__(
            "asset_id", "model:sha256:" + "d" * 64)),
    ]
    for _, mutate in mutations:
        tampered = copy.deepcopy(envelope); mutate(tampered)
        assert (await client.post("/api/integration/signed-runs", json=tampered)).status_code == 401
    unknown = {**envelope, "key_id": "unknown-key"}
    assert (await client.post("/api/integration/signed-runs", json=unknown)).status_code in {401, 403, 404}
    unknown_run = run.model_copy(update={"run_id": "M5-UNKNOWN-PRODUCER", "producer": "unknown-producer"})
    unknown_producer = {"run": unknown_run.model_dump(mode="json"), "key_id": "m5-key",
                        "signature": builder.sign_run(unknown_run, private_key=key)}
    assert (await client.post("/api/integration/signed-runs", json=unknown_producer)).status_code in {401, 403, 404}
    await client.post("/api/producers/person2-m5/keys/m5-key/revoke")
    new_run = run.model_copy(update={"run_id": "M5-REVOKED-RUN"})
    revoked = {"run": new_run.model_dump(mode="json"), "key_id": "m5-key",
               "signature": builder.sign_run(new_run, private_key=key)}
    assert (await client.post("/api/integration/signed-runs", json=revoked)).status_code == 403
    historical = (await client.get("/api/integration/runs/M5-SIGNED-RUN")).json()
    assert historical["authentication"]["authenticated"] is True
    summary = (await client.get("/api/summary?assessment_id=M5-A")).json()
    assert summary["modules"]["model_integrity"]["finding_count"] == len(findings)
    assert summary["modules"]["model_integrity"]["assurance_score"] < 100
    assert summary["modules"]["model_integrity"]["disposition"] in {"REVIEW", "QUARANTINE"}
    persisted = [(await client.get(f"/api/evidence/{finding.finding_id}")).json() for finding in findings]
    assert persisted == [finding.model_dump(mode="json") for finding in findings]
    assert SEVERITY_PENALTIES  # frozen backend policy is consumed, never copied into M5.
    await client.post("/api/audit/outbox/drain")
    audit = (await client.get("/api/audit/verify")).json()
    assert audit["valid"] is True


@pytest.mark.anyio
async def test_signed_m5_preserves_draft_active_and_sealed_lifecycle(integration_client, tmp_path):
    client = integration_client; key = Ed25519PrivateKey.generate(); builder = ModelIntegrityRunBuilder(sdk())
    await client.post("/api/producers", json={"producer_id": "m5-lifecycle",
        "display_name": "M5 lifecycle", "module": "model_integrity", "metadata": {}})
    await client.post("/api/producers/m5-lifecycle/keys", json={"key_id": "lifecycle-key",
        "public_key_pem": _public_pem(key)})
    await client.post("/api/assessments", json={"assessment_id": "M5-LIFE", "name": "M5", "metadata": {}})
    manifest = manifest_with_issues(tmp_path, parameter=["PARAMETER_NAN"])
    findings = builder.map_findings(ModelIntegrityEvidenceBundle(manifest=manifest))
    run = builder.build_run(assessment_id="M5-LIFE", producer="m5-lifecycle",
        producer_version="M5", findings=findings, run_id="M5-LIFECYCLE-RUN")
    envelope = {"run": run.model_dump(mode="json"), "key_id": "lifecycle-key",
                "signature": builder.sign_run(run, private_key=key)}
    assert (await client.post("/api/integration/signed-runs", json=envelope)).status_code == 409
    await client.post("/api/assessments/M5-LIFE/activate")
    assert (await client.post("/api/integration/signed-runs", json=envelope)).json()["result"] == "CREATED"
    await client.post("/api/assessments/M5-LIFE/seal")
    assert (await client.post("/api/integration/signed-runs", json=envelope)).status_code == 409


@pytest.mark.anyio
async def test_actual_backend_rejects_zero_finding_run_contract(integration_client):
    response = await integration_client.post("/api/integration/signed-runs", json={
        "run": {"run_id": "EMPTY", "assessment_id": "A", "module": "model_integrity",
                "producer": "p", "producer_version": "M5", "findings": []},
        "key_id": "k", "signature": "AAAA"})
    assert response.status_code == 422


def test_zero_finding_backend_contract_is_preserved():
    builder = ModelIntegrityRunBuilder(sdk())
    with pytest.raises(ValueError, match="at least one Finding"):
        builder.build_run(assessment_id="A", producer="p", producer_version="M5", findings=[])


def test_policy_inventory_and_frozen_backend_policy_untouched():
    inventory = mapping_policy_inventory()
    assert inventory["TRIGGER_SENSITIVITY"]["category"] == "TRIGGER_SENSITIVITY"
    assert "BACKDOOR_CONFIRMED" not in inventory
    assert list(Finding.model_fields) == FROZEN_FIELDS
