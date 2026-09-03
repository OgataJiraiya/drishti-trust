"""D6 frozen Finding mapping, SDK delegation, and final real-chain scenarios."""
from dataclasses import replace
import json
import httpx,pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from backend.schemas.evidence import Finding
from backend.integrations.signing import module_run_signing_bytes
from backend.core.config import Settings
from backend.main import create_app
from drishti_sdk import DistributionShiftAdapter,DrishtiClient
from modules.distribution_shift import *
from modules.distribution_shift.demo import build_interpretation_for_scenario

def sdk():return DrishtiClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={})))
@pytest.fixture
def anyio_backend():return "asyncio"
@pytest.fixture
async def client(tmp_path):
    app=create_app(Settings(data_dir=tmp_path/"data",key_dir=tmp_path/"keys",admin_bearer_token="D6-TEST",internal_ingest_bearer_token="D6-TEST",allow_unsigned_ingestion=True))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test",headers={"Authorization":"Bearer D6-TEST"}) as value:yield value
def mapper(client=None):
    c=client or sdk();return DistributionShiftFindingMapper(DistributionShiftAdapter(c))
@pytest.fixture(scope="module")
def reports():return {name:build_interpretation_for_scenario(name)[3] for name in ("clean","image-shift","representation-shift","output-shift","broad-shift","incomplete")}

def test_frozen_finding_shape_and_clean_zero(reports):
    assert list(Finding.model_fields)==["finding_id","module","asset_type","asset_id","category","severity","confidence","reason","evidence","recommendation","limitations"]
    assert mapper().map(reports["clean"])==[]

@pytest.mark.parametrize("scenario,category,severity",[("image-shift","IMAGE_STATISTICAL_SHIFT_ONLY","MEDIUM"),("representation-shift","REPRESENTATION_SHIFT_ONLY","MEDIUM"),("output-shift","PREDICTION_OUTPUT_SHIFT_ONLY","MEDIUM"),("broad-shift","BROAD_MULTILAYER_SHIFT","HIGH"),("incomplete","DISTRIBUTION_SHIFT_EVIDENCE_INCOMPLETE","LOW")])
def test_primary_scenario_mapping(reports,scenario,category,severity):
    f=mapper().map(reports[scenario]);assert len(f)==1;f=f[0]
    assert f.module.value=="distribution_shift" and f.category==category and f.severity.value==severity and f.recommendation.value=="REVIEW"
    assert f.confidence==(.9 if scenario!="incomplete" else 1.0) and f.asset_type=="distribution_context" and f.asset_id==reports[scenario].bundle_id
    assert all(isinstance(x,str) and len(x)<=1024 for x in f.evidence+f.limitations) and len(f.evidence)<=20
    json.dumps(f.model_dump(mode="json"),allow_nan=False)

def combined(a,b,c):return MultiSignalDriftInterpreter().interpret(build_interpretation_for_scenario(a)[0],build_interpretation_for_scenario(b)[1],build_interpretation_for_scenario(c)[2])
@pytest.mark.parametrize("args,category",[(('image-shift','representation-shift','clean'),"IMAGE_STATISTICAL_AND_REPRESENTATION_SHIFT"),(('image-shift','clean','output-shift'),"IMAGE_STATISTICAL_AND_OUTPUT_SHIFT"),(('clean','representation-shift','output-shift'),"REPRESENTATION_AND_OUTPUT_SHIFT")])
def test_two_layer_maps_once_high(args,category):
    f=mapper().map(combined(*args));assert len(f)==1 and f[0].category==category and f[0].severity.value=="HIGH"

def test_no_usable_and_incomplete_shift_mapping(reports):
    unavailable=MultiSignalDriftInterpreter().interpret();f=mapper().map(unavailable)[0];assert f.category=="DISTRIBUTION_SHIFT_EVIDENCE_UNAVAILABLE" and f.severity.value=="MEDIUM" and f.confidence==1
    partial=replace(reports["broad-shift"],status=InterpretationStatus.PARTIAL,pattern_code=InterpretationPattern.SHIFT_WITH_INCOMPLETE_COVERAGE,changed_layers=("IMAGE_STATISTICAL",),limitations=reports["broad-shift"].limitations+("incomplete",))
    f=mapper().map(partial)[0];assert f.category=="DISTRIBUTION_SHIFT_WITH_INCOMPLETE_COVERAGE" and f.severity.value=="MEDIUM" and any("incomplete" in x.lower() for x in f.limitations)
    broad_partial=replace(reports["broad-shift"],status=InterpretationStatus.PARTIAL);f=mapper().map(broad_partial)[0];assert f.category=="BROAD_MULTILAYER_SHIFT" and f.severity.value=="HIGH"

def test_determinism_policy_identity_privacy_and_immutability(reports):
    report=reports["broad-shift"];before=json.dumps(report.to_dict(),sort_keys=True);a=mapper().map(report)[0];b=mapper().map(report)[0]
    assert a.model_dump(mode="json")==b.model_dump(mode="json") and before==json.dumps(report.to_dict(),sort_keys=True)
    c=sdk();changed=DistributionShiftFindingMapper(DistributionShiftAdapter(c),DistributionShiftFindingMappingPolicy(policy_version="DISTRIBUTION_SHIFT_MAPPING_POLICY_V2")).map(report)[0];assert changed.finding_id!=a.finding_id
    text=json.dumps(a.model_dump(mode="json")).lower()
    for forbidden in ("timestamp","signature","private_key","probabilities","histogram"):assert forbidden not in text
    assert report.interpretation_id in text and report.bundle_id in text and all(layer.source_comparison_id in text for layer in report.layers)

def test_invalid_input_schema_identity_and_unknown_pattern(reports):
    for bad in ({},object()):
        with pytest.raises(TypeError):mapper().map(bad)
    with pytest.raises(ValueError):mapper().map(replace(reports["broad-shift"],schema_version=2))
    with pytest.raises(ValueError):mapper().map(replace(reports["broad-shift"],interpretation_id="bad"))
    with pytest.raises(ValueError):mapper().map(replace(reports["broad-shift"],pattern_code="FUTURE_PATTERN"))

def test_inventory_and_recommendation_boundaries():
    rows=mapping_policy_inventory();assert rows and all(row["recommendation"] in {None,"REVIEW"} for row in rows)
    text=json.dumps(rows);assert '"CRITICAL"' not in text and '"QUARANTINE"' not in text and '"REJECT"' not in text and '"ACCEPT"' not in text

def test_run_builder_empty_module_asset_and_signing(reports):
    c=sdk();builder=DistributionShiftRunBuilder(c);findings=builder.map_findings(reports["broad-shift"])
    assert isinstance(builder.adapter,DistributionShiftAdapter)
    empty=builder.build_run(assessment_id="A",producer="p",producer_version="1",findings=[])
    assert empty.findings==[] and empty.module.value=="distribution_shift"
    wrong=findings[0].model_copy(update={"module":"model_integrity"})
    with pytest.raises(ValueError):builder.build_run(assessment_id="A",producer="p",producer_version="1",findings=[wrong])
    run=builder.build_run(assessment_id="A",producer="p",producer_version="1",findings=findings);assert run.module.value=="distribution_shift" and run.findings[0].finding_id==findings[0].finding_id
    key=Ed25519PrivateKey.generate();sig=builder.sign_run(run,private_key=key);key.public_key().verify(__import__("base64").b64decode(sig),module_run_signing_bytes(run))
    with pytest.raises(Exception):key.public_key().verify(__import__("base64").b64decode(sig),module_run_signing_bytes(run.model_copy(update={"assessment_id":"B"})))
    with pytest.raises(ValueError):builder.submit_signed_run(run=run,key_id="k",bundle_id="wrong",private_key=key)

def pem(key):return key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
@pytest.mark.anyio
async def test_backend_signed_lifecycle_replay_auth_audit_snapshot_checkpoint(client,reports):
    c=sdk();builder=DistributionShiftRunBuilder(c);finding=builder.map_findings(reports["broad-shift"])[0];key=Ed25519PrivateKey.generate();wrong=Ed25519PrivateKey.generate()
    assert (await client.post("/api/producers",json={"producer_id":"d6-producer","display_name":"D6","module":"distribution_shift","metadata":{}})).status_code==200
    assert (await client.post("/api/producers/d6-producer/keys",json={"key_id":"d6-key","public_key_pem":pem(key)})).status_code==200
    await client.post("/api/assessments",json={"assessment_id":"D6-A","name":"D6","metadata":{}})
    run=builder.build_run(assessment_id="D6-A",producer="d6-producer",producer_version="1",findings=[finding],run_id="D6-RUN")
    envelope={"run":run.model_dump(mode="json"),"key_id":"d6-key","signature":builder.sign_run(run,private_key=key)}
    assert (await client.post("/api/integration/signed-runs",json=envelope)).status_code==409
    await client.post("/api/assessments/D6-A/activate");first=await client.post("/api/integration/signed-runs",json=envelope);assert first.json()["result"]=="CREATED"
    assert (await client.post("/api/integration/signed-runs",json=envelope)).json()["result"]=="EXISTS"
    bad={**envelope,"signature":builder.sign_run(run,private_key=wrong)};assert (await client.post("/api/integration/signed-runs",json=bad)).status_code==401
    stored=(await client.get("/api/integration/runs/D6-RUN")).json();assert stored["module"]=="distribution_shift" and stored["finding_ids"]==[finding.finding_id]
    assert stored["authentication"]["authenticated"] and stored["authentication"]["mode"]=="ED25519" and stored["authentication"]["producer_id"]=="d6-producer" and stored["authentication"]["key_id"]=="d6-key"
    summary=(await client.get("/api/summary?assessment_id=D6-A&trust_scope=authenticated")).json();assert "distribution_shift" in summary["modules"] and summary["overall"]["assessment_coverage"]<1
    assert (await client.get("/api/audit/verify")).json()["status"]=="VALID"
    await client.post("/api/assessments/D6-A/seal");assert (await client.post("/api/integration/signed-runs",json=envelope)).status_code==409
    assert (await client.get("/api/assessments/D6-A/snapshot/verify")).json()["status"]=="VALID"
    await client.post("/api/audit/outbox/drain");checkpoint=(await client.post("/api/audit/checkpoints")).json()["bundle"];assert (await client.post("/api/audit/checkpoints/verify",json=checkpoint)).json()["status"]=="VALID"
    outbox=(await client.get("/api/audit/outbox/status")).json();assert outbox["healthy"] and outbox["pending_count"]==0

@pytest.mark.parametrize("scenario,pattern,count,severity",[("clean","NO_OBSERVED_SHIFT_ALL_LAYERS",0,None),("image-shift","IMAGE_STATISTICAL_SHIFT_ONLY",1,"MEDIUM"),("representation-shift","REPRESENTATION_SHIFT_ONLY",1,"MEDIUM"),("output-shift","PREDICTION_OUTPUT_SHIFT_ONLY",1,"MEDIUM"),("broad-shift","BROAD_MULTILAYER_SHIFT",1,"HIGH"),("incomplete","NO_SHIFT_OBSERVED_IN_ASSESSED_EVIDENCE",1,"LOW")])
def test_real_d1_d6_offline_scenarios(scenario,pattern,count,severity):
    a=DistributionShiftFinalOrchestrator().run_offline(scenario);b=DistributionShiftFinalOrchestrator().run_offline(scenario)
    assert a.d5_pattern==pattern and a.finding_count==count and a.report_id==b.report_id and a.to_dict()==b.to_dict()
    if severity:assert a.finding_severities==(severity,) and a.finding_recommendations==("REVIEW",)
    json.dumps(a.to_dict(),allow_nan=False)
