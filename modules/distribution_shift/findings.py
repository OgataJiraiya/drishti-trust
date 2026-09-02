"""Conservative D6 mapping from exact D5 interpretation to frozen Finding v1."""
from __future__ import annotations
from dataclasses import asdict,dataclass
import json,re
from backend.schemas.evidence import Finding
from drishti_sdk.adapters import DistributionShiftAdapter
from .interpretation_models import (INTERPRETATION_SCHEMA_VERSION,InterpretationPattern,
    InterpretationStatus,MultiSignalInterpretationReport)

DISTRIBUTION_SHIFT_MAPPING_POLICY_V1="DISTRIBUTION_SHIFT_MAPPING_POLICY_V1"
@dataclass(frozen=True)
class DistributionShiftFindingMappingPolicy:
    policy_version:str=DISTRIBUTION_SHIFT_MAPPING_POLICY_V1
    max_findings:int=1;max_evidence:int=20;max_limitations:int=10;max_text_length:int=1024
    def __post_init__(self):
        if not isinstance(self.policy_version,str) or not self.policy_version or len(self.policy_version)>128:raise ValueError("policy_version must be bounded")
        for name in ("max_findings","max_evidence","max_limitations","max_text_length"):
            if type(getattr(self,name)) is not int or getattr(self,name)<=0:raise ValueError(f"{name} must be positive")
        if self.max_findings!=1:raise ValueError("D6 v1 max_findings must remain one")

@dataclass(frozen=True)
class _Mapping:
    category:str;severity:str;confidence:float;reason:str;rationale:str

_M={
InterpretationPattern.IMAGE_STATISTICAL_SHIFT_ONLY:_Mapping("IMAGE_STATISTICAL_SHIFT_ONLY","MEDIUM",.90,"Image/statistical distribution-shift evidence was observed while fully assessed representation and prediction-output layers showed no shift.","One observable layer changed."),
InterpretationPattern.REPRESENTATION_SHIFT_ONLY:_Mapping("REPRESENTATION_SHIFT_ONLY","MEDIUM",.90,"Representation-space distribution-shift evidence was observed while fully assessed image/statistical and prediction-output layers showed no shift.","One observable layer changed."),
InterpretationPattern.PREDICTION_OUTPUT_SHIFT_ONLY:_Mapping("PREDICTION_OUTPUT_SHIFT_ONLY","MEDIUM",.90,"Prediction-output distribution-shift evidence was observed while fully assessed image/statistical and representation layers showed no shift.","One observable layer changed."),
InterpretationPattern.IMAGE_STATISTICAL_AND_REPRESENTATION_SHIFT:_Mapping("IMAGE_STATISTICAL_AND_REPRESENTATION_SHIFT","HIGH",.90,"Distribution-shift evidence was observed in image/statistical and representation layers while measured prediction-output evidence showed no shift.","Two observable layers changed."),
InterpretationPattern.IMAGE_STATISTICAL_AND_OUTPUT_SHIFT:_Mapping("IMAGE_STATISTICAL_AND_OUTPUT_SHIFT","HIGH",.90,"Distribution-shift evidence was observed in image/statistical and prediction-output layers while measured representation evidence showed no shift.","Two observable layers changed."),
InterpretationPattern.REPRESENTATION_AND_OUTPUT_SHIFT:_Mapping("REPRESENTATION_AND_OUTPUT_SHIFT","HIGH",.90,"Distribution-shift evidence was observed in representation and prediction-output layers while measured image/statistical evidence showed no shift.","Two observable layers changed."),
InterpretationPattern.BROAD_MULTILAYER_SHIFT:_Mapping("BROAD_MULTILAYER_SHIFT","HIGH",.90,"Distribution-shift evidence was observed across image/statistical, representation, and prediction-output layers.","Three observable layers changed."),
InterpretationPattern.NO_SHIFT_OBSERVED_IN_ASSESSED_EVIDENCE:_Mapping("DISTRIBUTION_SHIFT_EVIDENCE_INCOMPLETE","LOW",1.0,"No shift was observed in assessed evidence, but the distribution-shift assessment had incomplete cross-layer coverage.","The structural coverage gap is directly represented."),
InterpretationPattern.NO_USABLE_EVIDENCE:_Mapping("DISTRIBUTION_SHIFT_EVIDENCE_UNAVAILABLE","MEDIUM",1.0,"The supplied distribution-shift evidence did not support a usable cross-layer assessment.","The structural assessability gap is directly represented."),
}
def mapping_policy_inventory():
    rows=[]
    for pattern,m in _M.items():rows.append({"source_pattern":pattern.value,"category":m.category,"severity":m.severity,"confidence":m.confidence,"recommendation":"REVIEW","rationale":m.rationale,"limitation":"Observational distribution evidence does not establish cause or model performance."})
    rows.append({"source_pattern":InterpretationPattern.SHIFT_WITH_INCOMPLETE_COVERAGE.value,"category":"DISTRIBUTION_SHIFT_WITH_INCOMPLETE_COVERAGE","severity":"MEDIUM_OR_HIGH_BY_CHANGED_LAYER_COUNT","confidence":.90,"recommendation":"REVIEW","rationale":"Observed shift is retained while incomplete coverage is explicit.","limitation":"Unassessed layers prevent a full stability conclusion."})
    rows.append({"source_pattern":InterpretationPattern.NO_OBSERVED_SHIFT_ALL_LAYERS.value,"category":None,"severity":None,"confidence":None,"recommendation":None,"rationale":"Clean complete evidence maps to zero Findings.","limitation":"Frozen ModuleRun v1 cannot represent zero-Finding completion."})
    return tuple(rows)

class DistributionShiftFindingMapper:
    def __init__(self,adapter:DistributionShiftAdapter,policy:DistributionShiftFindingMappingPolicy|None=None):
        if not isinstance(adapter,DistributionShiftAdapter):raise TypeError("adapter must be DistributionShiftAdapter")
        self.adapter=adapter;self.policy=policy or DistributionShiftFindingMappingPolicy()
    def map(self,report:MultiSignalInterpretationReport)->list[Finding]:
        if type(report) is not MultiSignalInterpretationReport:raise TypeError("report must be MultiSignalInterpretationReport")
        if report.schema_version!=INTERPRETATION_SCHEMA_VERSION:raise ValueError("unsupported D5 schema")
        if not re.fullmatch(r"multisignal-interpretation:sha256:[0-9a-f]{64}",report.interpretation_id) or not re.fullmatch(r"multisignal-bundle:sha256:[0-9a-f]{64}",report.bundle_id):raise ValueError("malformed D5 identity")
        json.dumps(report.to_dict(),allow_nan=False)
        pattern=report.pattern_code
        if not isinstance(pattern,InterpretationPattern):raise ValueError("unsupported D5 pattern")
        if pattern==InterpretationPattern.NO_OBSERVED_SHIFT_ALL_LAYERS:
            if report.status!=InterpretationStatus.COMPLETE or report.changed_layers:raise ValueError("malformed clean D5 interpretation")
            return []
        if pattern==InterpretationPattern.SHIFT_WITH_INCOMPLETE_COVERAGE:
            if not report.changed_layers:raise ValueError("incomplete shift pattern requires changed layers")
            mapped=_Mapping("DISTRIBUTION_SHIFT_WITH_INCOMPLETE_COVERAGE","MEDIUM" if len(report.changed_layers)==1 else "HIGH",.90,"Distribution-shift evidence was observed while cross-layer evidence coverage was incomplete.","Observed changed-layer scope determines operational attention.")
        else:
            mapped=_M.get(pattern)
            if mapped is None:raise ValueError("unsupported D5 pattern")
        evidence=[f"mapping_policy={self.policy.policy_version}",f"interpretation_id={report.interpretation_id}",f"bundle_id={report.bundle_id}",f"pattern_code={pattern.value}",f"interpretation_status={report.status.value}",f"changed_layers={','.join(report.changed_layers) or 'none'}"]
        for layer in report.layers:
            evidence.append(f"layer={layer.layer.value};coverage={layer.coverage.value};observation={layer.observation.value}")
            if layer.shifted_features:evidence.append(f"shifted_features[{layer.layer.value}]={','.join(layer.shifted_features)}")
            if layer.source_comparison_id:evidence.append(f"source_comparison_id[{layer.layer.value}]={layer.source_comparison_id}")
        truncated=len(evidence)>self.policy.max_evidence
        evidence=evidence[:self.policy.max_evidence]
        if truncated:evidence[-1]="evidence_truncated=true"
        evidence=[x[:self.policy.max_text_length] for x in evidence]
        limitations=["Distribution shift is observational evidence and does not establish cause, malicious intent, model-performance degradation, calibration degradation, or deployment unsafety.","Deterministic source comparison IDs are content identities, not authenticated provenance.","The designated reference is not thereby authenticated, approved, or safe.","D5 source association is caller-designated; signed D6 submission authenticates the producer and submitted payload, not physical-world correspondence of source windows."]
        if report.status!=InterpretationStatus.COMPLETE:limitations.append("Cross-layer evidence coverage was incomplete; absence of additional shift evidence is not a full stability conclusion.")
        if truncated:limitations.append("Finding evidence was deterministically truncated to the configured bound.")
        finding=self.adapter.finding(asset_type="distribution_context",asset_id=report.bundle_id,category=mapped.category,severity=mapped.severity,confidence=mapped.confidence,reason=mapped.reason,evidence=evidence,recommendation="REVIEW",limitations=limitations[:self.policy.max_limitations],stable_evidence_key={"mapping_policy":self.policy.policy_version,"interpretation_id":report.interpretation_id,"category":mapped.category})
        json.dumps(finding.model_dump(mode="json"),allow_nan=False);return [finding]
