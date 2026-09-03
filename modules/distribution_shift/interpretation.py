"""D5 deterministic interpretation of frozen D2/D3/D4 reports only."""
from __future__ import annotations
from dataclasses import asdict
from hashlib import sha256
import json,re
from backend.core.canonical import canonical_json_bytes
from .comparison_models import COMPARISON_SCHEMA_VERSION,ComparisonStatus,DistributionShiftComparisonReport,FeatureDriftState
from .representation_models import REPRESENTATION_SCHEMA_VERSION,RepresentationFeatureState,RepresentationReportStatus,RepresentationShiftReport
from .prediction_models import PREDICTION_SCHEMA_VERSION,PredictionFeatureState,PredictionReportStatus,PredictionShiftReport
from .interpretation_models import *

LAYER_ORDER=(SignalLayer.IMAGE_STATISTICAL,SignalLayer.REPRESENTATION,SignalLayer.PREDICTION_OUTPUT)
UNSUPPORTED=("CAUSE_NOT_ESTABLISHED","MALICIOUS_INTENT_NOT_ESTABLISHED","MODEL_PERFORMANCE_NOT_ESTABLISHED","CALIBRATION_NOT_ESTABLISHED","DEPLOYMENT_SAFETY_NOT_ESTABLISHED","REFERENCE_AUTHENTICITY_NOT_ESTABLISHED","COMMON_CAUSE_NOT_ESTABLISHED")
ID_RULES={SignalLayer.IMAGE_STATISTICAL:(DistributionShiftComparisonReport,COMPARISON_SCHEMA_VERSION,"drift-comparison",ComparisonStatus,FeatureDriftState),SignalLayer.REPRESENTATION:(RepresentationShiftReport,REPRESENTATION_SCHEMA_VERSION,"representation-comparison",RepresentationReportStatus,RepresentationFeatureState),SignalLayer.PREDICTION_OUTPUT:(PredictionShiftReport,PREDICTION_SCHEMA_VERSION,"prediction-comparison",PredictionReportStatus,PredictionFeatureState)}
PARTITIONS=("shifted_features","stable_features","partial_features","unavailable_features","incomparable_features")

def _valid_id(value,prefix):return isinstance(value,str) and re.fullmatch(re.escape(prefix)+r":sha256:[0-9a-f]{64}",value) is not None
def _feature_name(item):return getattr(item,"feature",getattr(item,"name",None))

class MultiSignalDriftInterpreter:
    def __init__(self,policy:MultiSignalInterpretationPolicy|None=None):self.policy=policy or MultiSignalInterpretationPolicy()
    def interpret(self,statistical_report=None,representation_report=None,prediction_report=None):
        reports=(statistical_report,representation_report,prediction_report)
        layers=tuple(self._layer(layer,report) for layer,report in zip(LAYER_ORDER,reports,strict=True))
        bundle=MultiSignalBundle(*(x.source_comparison_id for x in layers))
        changed=tuple(x.layer.value for x in layers if x.observation==LayerObservation.SHIFT_EVIDENCE_PRESENT)
        stable=tuple(x.layer.value for x in layers if x.coverage==LayerCoverage.COMPLETE and x.observation==LayerObservation.NO_SHIFT_OBSERVED)
        incomplete=tuple(x.layer.value for x in layers if x.coverage!=LayerCoverage.COMPLETE)
        not_assessable=tuple(x.layer.value for x in layers if x.observation==LayerObservation.NOT_ASSESSABLE)
        usable=any(x.observation!=LayerObservation.NOT_ASSESSABLE for x in layers)
        all_complete=all(x.coverage==LayerCoverage.COMPLETE for x in layers)
        status=InterpretationStatus.COMPLETE if all_complete else InterpretationStatus.PARTIAL if usable else InterpretationStatus.UNAVAILABLE
        pattern=self._pattern(layers,all_complete,usable)
        summary=self._summary(pattern,incomplete)
        observations=tuple(self._observation_text(x) for x in layers)
        checks=self._checks(layers)
        limitations=["SOURCE_ASSOCIATION_CALLER_DESIGNATED","DETERMINISTIC_SOURCE_IDENTITY_NOT_AUTHENTICATED"]
        if incomplete:limitations.append("ONE_OR_MORE_LAYERS_INCOMPLETELY_ASSESSED")
        core={"schema_version":INTERPRETATION_SCHEMA_VERSION,"bundle_id":bundle.bundle_id,"policy_id":self.policy.policy_id,"status":status.value,"pattern_code":pattern.value,
            "layers":[asdict(x) for x in layers],"changed_layers":changed,"stable_layers":stable,"incomplete_layers":incomplete,"not_assessable_layers":not_assessable,
            "summary":summary,"key_observations":observations,"analyst_checks":checks,"unsupported_conclusions":UNSUPPORTED[:self.policy.max_unsupported_conclusions],"limitations":tuple(sorted(limitations))}
        iid="multisignal-interpretation:sha256:"+sha256(canonical_json_bytes(core)).hexdigest()
        report=MultiSignalInterpretationReport(INTERPRETATION_SCHEMA_VERSION,iid,bundle.bundle_id,self.policy.policy_id,status,pattern,layers,changed,stable,incomplete,not_assessable,summary,observations,checks,core["unsupported_conclusions"],core["limitations"])
        json.dumps(report.to_dict(),allow_nan=False);return report
    def _layer(self,layer,report):
        if report is None:return LayerInterpretation(layer,SourcePresence.NOT_PROVIDED,None,None,LayerCoverage.NOT_PROVIDED,LayerObservation.NOT_ASSESSABLE,(),(),(),(),(),"SOURCE_NOT_PROVIDED",("SOURCE_NOT_PROVIDED",))
        report_type,schema,prefix,status_type,state_type=ID_RULES[layer]
        if type(report) is not report_type:raise TypeError(f"{layer.value} source must be the frozen concrete report type")
        if report.schema_version!=schema:raise ValueError("unsupported source schema")
        if not _valid_id(report.comparison_id,prefix):raise ValueError("malformed source comparison ID")
        if not isinstance(report.status,status_type):raise ValueError("malformed source status")
        try:json.dumps(report.to_dict(),allow_nan=False)
        except (TypeError,ValueError) as exc:raise ValueError("source report is not strict-JSON-safe") from exc
        values=[]
        for field in PARTITIONS:
            items=getattr(report,field)
            if not isinstance(items,tuple) or any(not isinstance(x,str) or not x or len(x)>128 for x in items):raise ValueError("malformed source feature partition")
            if len(items)>self.policy.max_feature_names_per_layer or len(set(items))!=len(items):raise ValueError("source feature partition exceeds bounds or contains duplicates")
            values.append(set(items))
        if any(values[i]&values[j] for i in range(5) for j in range(i+1,5)):raise ValueError("source feature partitions overlap")
        comparisons=report.feature_comparisons
        if not isinstance(comparisons,tuple) or len(comparisons)>self.policy.max_feature_names_per_layer:raise ValueError("source feature comparisons exceed bounds")
        names=[_feature_name(x) for x in comparisons]
        if any(not isinstance(x,str) or not x for x in names) or len(names)!=len(set(names)):raise ValueError("malformed source feature comparisons")
        expected={state.value:set() for state in state_type}
        for item,name in zip(comparisons,names,strict=True):
            if not isinstance(item.state,state_type):raise ValueError("malformed upstream feature state")
            expected[item.state.value].add(name)
        for field,actual in zip(PARTITIONS,values,strict=True):
            if actual!=expected[field.removesuffix("_features").upper()]:raise ValueError("source partitions disagree with feature states")
        coverage=LayerCoverage(report.status.value)
        shifted=tuple(sorted(report.shifted_features));stable=tuple(sorted(report.stable_features))
        if coverage==LayerCoverage.COMPLETE and (not (shifted or stable) or any(values[index] for index in (2,3,4))):
            raise ValueError("complete source has impossible feature coverage")
        if coverage in {LayerCoverage.UNAVAILABLE,LayerCoverage.INCOMPARABLE} and (shifted or stable):
            raise ValueError("non-assessable source contains a stable or shifted conclusion")
        if shifted:observation=LayerObservation.SHIFT_EVIDENCE_PRESENT
        elif stable:observation=LayerObservation.NO_SHIFT_OBSERVED
        else:observation=LayerObservation.NOT_ASSESSABLE
        code="SHIFT_PRESENT" if shifted else "NO_SHIFT_IN_ASSESSED_FEATURES" if stable else "LAYER_NOT_ASSESSABLE"
        limitation=() if coverage==LayerCoverage.COMPLETE else (f"SOURCE_COVERAGE_{coverage.value}",)
        return LayerInterpretation(layer,SourcePresence.PROVIDED,report.comparison_id,report.status.value,coverage,observation,shifted,stable,tuple(sorted(report.partial_features)),tuple(sorted(report.unavailable_features)),tuple(sorted(report.incomparable_features)),code,limitation)
    def _pattern(self,layers,complete,usable):
        changed=tuple(x.layer for x in layers if x.observation==LayerObservation.SHIFT_EVIDENCE_PRESENT)
        if not usable:return InterpretationPattern.NO_USABLE_EVIDENCE
        if not complete:
            return InterpretationPattern.BROAD_MULTILAYER_SHIFT if len(changed)==3 else InterpretationPattern.SHIFT_WITH_INCOMPLETE_COVERAGE if changed else InterpretationPattern.NO_SHIFT_OBSERVED_IN_ASSESSED_EVIDENCE
        mapping={():InterpretationPattern.NO_OBSERVED_SHIFT_ALL_LAYERS,(LAYER_ORDER[0],):InterpretationPattern.IMAGE_STATISTICAL_SHIFT_ONLY,(LAYER_ORDER[1],):InterpretationPattern.REPRESENTATION_SHIFT_ONLY,(LAYER_ORDER[2],):InterpretationPattern.PREDICTION_OUTPUT_SHIFT_ONLY,
            (LAYER_ORDER[0],LAYER_ORDER[1]):InterpretationPattern.IMAGE_STATISTICAL_AND_REPRESENTATION_SHIFT,(LAYER_ORDER[0],LAYER_ORDER[2]):InterpretationPattern.IMAGE_STATISTICAL_AND_OUTPUT_SHIFT,(LAYER_ORDER[1],LAYER_ORDER[2]):InterpretationPattern.REPRESENTATION_AND_OUTPUT_SHIFT,tuple(LAYER_ORDER):InterpretationPattern.BROAD_MULTILAYER_SHIFT}
        return mapping[changed]
    def _summary(self,p,incomplete):
        text={InterpretationPattern.NO_OBSERVED_SHIFT_ALL_LAYERS:"No shift was observed across the three fully assessed evidence layers. This does not establish model correctness, deployment safety, or future reliability.",
        InterpretationPattern.IMAGE_STATISTICAL_SHIFT_ONLY:"Image/statistical shift evidence is present. Representation and prediction-output evidence were fully assessed and showed no shift under their configured detectors.",
        InterpretationPattern.REPRESENTATION_SHIFT_ONLY:"Representation-space evidence changed while fully assessed image/statistical and prediction-output evidence showed no shift.",
        InterpretationPattern.PREDICTION_OUTPUT_SHIFT_ONLY:"Prediction-output behavior changed while fully assessed image/statistical and representation evidence showed no shift.",
        InterpretationPattern.IMAGE_STATISTICAL_AND_REPRESENTATION_SHIFT:"Image/statistical and representation evidence changed while measured prediction-output distribution evidence showed no shift.",
        InterpretationPattern.IMAGE_STATISTICAL_AND_OUTPUT_SHIFT:"Image/statistical and prediction-output evidence changed while the supplied representation-space evidence showed no shift; this is not a contradiction.",
        InterpretationPattern.REPRESENTATION_AND_OUTPUT_SHIFT:"Representation and prediction-output evidence changed without observed shift in fully assessed image/statistical features.",
        InterpretationPattern.BROAD_MULTILAYER_SHIFT:"Shift evidence is present across image/statistical, representation, and prediction-output layers. This pattern does not establish a common cause, malicious intent, or model-performance degradation.",
        InterpretationPattern.SHIFT_WITH_INCOMPLETE_COVERAGE:"Shift evidence is present in one or more observable layers. Coverage is incomplete, so no full cross-layer stability conclusion is supported.",
        InterpretationPattern.NO_SHIFT_OBSERVED_IN_ASSESSED_EVIDENCE:"No shift was observed in assessable evidence, but one or more layers were not fully assessed.",
        InterpretationPattern.NO_USABLE_EVIDENCE:"No usable stable or shifted feature conclusion was available from the supplied source reports."}[p]
        if incomplete and p==InterpretationPattern.BROAD_MULTILAYER_SHIFT:text+=" One or more layers have incomplete coverage."
        return text
    def _observation_text(self,x):
        label=x.layer.value.replace("_"," ").title()
        if x.observation==LayerObservation.SHIFT_EVIDENCE_PRESENT:return f"{label}: shifted features: {', '.join(x.shifted_features)}."
        if x.observation==LayerObservation.NO_SHIFT_OBSERVED:return f"{label}: no shift observed in assessed features."
        return f"{label}: not assessable ({x.coverage.value})."
    def _checks(self,layers):
        checks=["Confirm the source reports refer to the intended designated reference/current context."]
        mapping={SignalLayer.IMAGE_STATISTICAL:("Inspect the shifted image/statistical features.","Verify capture and preprocessing configuration."),SignalLayer.REPRESENTATION:("Verify representation-space identity.","Inspect shifted representation features and slice-specific support."),SignalLayer.PREDICTION_OUTPUT:("Verify prediction-output-space identity.","Inspect shifted output features and runtime/output-head configuration.")}
        for x in layers:
            if x.observation==LayerObservation.SHIFT_EVIDENCE_PRESENT:checks.extend(mapping[x.layer])
        if any(x.coverage!=LayerCoverage.COMPLETE for x in layers):checks.append("Acquire or validate incomplete source-layer evidence.")
        checks.append("Inspect slice-specific behavior not represented by aggregate evidence.")
        return tuple(checks[:self.policy.max_analyst_checks])
