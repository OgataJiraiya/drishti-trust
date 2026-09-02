"""Runtime-generated, offline-by-default D1-D6 final product scenarios."""
from __future__ import annotations
from dataclasses import asdict,dataclass
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from PIL import Image
from backend.core.canonical import canonical_json_bytes
from drishti_sdk import DrishtiClient
from .comparison import DistributionShiftComparator
from .findings import DistributionShiftFindingMapper
from .interpretation import MultiSignalDriftInterpreter
from .interpretation_models import InterpretationPattern
from .prediction_comparison import PredictionShiftComparator
from .prediction_models import PredictionEvidenceTier,PredictionOutputSpaceDescriptor,PredictionRecord
from .prediction_profile import PredictionProfiler
from .profiling import ImageWindowProfiler
from .representation_comparison import RepresentationShiftComparator
from .representation_models import RepresentationSpaceDescriptor
from .representation_profile import RepresentationProfiler
from drishti_sdk.adapters import DistributionShiftAdapter

SCENARIOS=("clean","image-shift","representation-shift","output-shift","broad-shift","incomplete")
def build_interpretation_for_scenario(scenario):
    if scenario not in SCENARIOS:raise ValueError("unsupported final demo scenario")
    with TemporaryDirectory(prefix="drishti-d6-") as root:
        base=Path(root);refdir=base/"reference";curdir=base/"current";refdir.mkdir();curdir.mkdir();image_shift=scenario in {"image-shift","broad-shift"}
        for i in range(20):
            Image.new("RGB",(8,8),(0,0,0)).save(refdir/f"{i:02d}.png");Image.new("RGB",(8,8),(255,255,255) if image_shift else (0,0,0)).save(curdir/f"{i:02d}.png")
        ip=ImageWindowProfiler();d2=DistributionShiftComparator().compare(ip.profile_reference(refdir),ip.profile_current(curdir))
    rng=np.random.default_rng(20260902);x=rng.normal(size=(40,4));y=x+5 if scenario in {"representation-shift","broad-shift"} else x.copy();rd=RepresentationSpaceDescriptor("d6-runtime-synthetic","1",4);rp=RepresentationProfiler();d3=RepresentationShiftComparator().compare(rp.build_reference(x,rd),rp.build_current(y,rd))
    if scenario=="incomplete":pd=PredictionOutputSpaceDescriptor(PredictionEvidenceTier.LABEL_ONLY,("A","B"));refrec=[PredictionRecord("A",f"s{i}") for i in range(40)];currec=list(refrec)
    else:
        pd=PredictionOutputSpaceDescriptor(PredictionEvidenceTier.FULL_PROBABILITIES,("A","B"),abstention_semantics="caller abstention",unknown_semantics="caller unknown/OOD");refrec=[PredictionRecord("A",f"s{i}",.9,(.9,.1),False,False) for i in range(40)];shift=scenario in {"output-shift","broad-shift"};currec=[PredictionRecord("B" if shift else "A",f"s{i}",.9,(.1,.9) if shift else (.9,.1),False,False) for i in range(40)]
    pp=PredictionProfiler();d4=PredictionShiftComparator().compare(pp.build_reference(refrec,pd),pp.build_current(currec,pd));return d2,d3,d4,MultiSignalDriftInterpreter().interpret(d2,d3,d4)
@dataclass(frozen=True)
class DistributionShiftFinalResult:
    schema_version:int;report_id:str;scenario_id:str;scenario_name:str
    d2_status:str;d2_shifted_features:tuple[str,...];d3_status:str;d3_shifted_features:tuple[str,...]
    d4_status:str;d4_shifted_features:tuple[str,...];d5_status:str;d5_pattern:str;changed_layers:tuple[str,...]
    interpretation_id:str;bundle_id:str;finding_count:int;finding_ids:tuple[str,...];finding_categories:tuple[str,...]
    finding_severities:tuple[str,...];finding_recommendations:tuple[str,...];backend_submission_status:str
    limitations:tuple[str,...]
    def to_dict(self):return asdict(self)

def render_final_result(result:DistributionShiftFinalResult)->str:
    finding=("none" if not result.finding_categories else
        f"{result.finding_categories[0]} — {result.finding_severities[0]} / {result.finding_recommendations[0]}")
    return "\n".join(("DRISHTI-TRUST — DISTRIBUTION SHIFT FINAL DEMO",f"Scenario: {result.scenario_name}",
        f"D2 Statistical/Image: {result.d2_status}; shifted={','.join(result.d2_shifted_features) or 'none'}",
        f"D3 Representation: {result.d3_status}; shifted={','.join(result.d3_shifted_features) or 'none'}",
        f"D4 Prediction: {result.d4_status}; shifted={','.join(result.d4_shifted_features) or 'none'}",
        f"D5 Interpretation: {result.d5_pattern}",f"D6 Frozen Findings: {result.finding_count}; {finding}",
        "Scientific boundary: observed distribution shift does not establish cause, malicious intent, model-performance degradation, calibration degradation, or deployment unsafety."))

class DistributionShiftFinalOrchestrator:
    def __init__(self,client:DrishtiClient|None=None):self.client=client
    def run_offline(self,scenario:str)->DistributionShiftFinalResult:
        d2,d3,d4,d5=build_interpretation_for_scenario(scenario)
        local_client=self.client or DrishtiClient();mapper=DistributionShiftFindingMapper(DistributionShiftAdapter(local_client));findings=mapper.map(d5)
        if self.client is None:local_client.close()
        limitations=("Frozen ModuleRunSubmission v1 cannot represent a clean zero-Finding completion.",) if not findings else ("Signed payload authentication does not prove physical-world observation truth.",)
        core={"schema_version":1,"scenario_id":scenario,"d2_comparison_id":d2.comparison_id,"d3_comparison_id":d3.comparison_id,"d4_comparison_id":d4.comparison_id,"interpretation_id":d5.interpretation_id,"bundle_id":d5.bundle_id,"finding_ids":tuple(f.finding_id for f in findings)}
        report_id="distribution-demo:sha256:"+sha256(canonical_json_bytes(core)).hexdigest()
        result=DistributionShiftFinalResult(1,report_id,scenario,scenario.replace("-"," ").title(),d2.status.value,d2.shifted_features,d3.status.value,d3.shifted_features,d4.status.value,d4.shifted_features,d5.status.value,d5.pattern_code.value,d5.changed_layers,d5.interpretation_id,d5.bundle_id,len(findings),tuple(f.finding_id for f in findings),tuple(f.category for f in findings),tuple(f.severity.value for f in findings),tuple(f.recommendation.value for f in findings),"NOT_REQUESTED",limitations)
        json.dumps(result.to_dict(),allow_nan=False);return result
