"""D5 reports-only categorical interpretation and adversarial validation."""
from dataclasses import replace
import json
import pytest
from modules.distribution_shift import *

HEX="a"*64
def d2(status=ComparisonStatus.COMPLETE,shift=False,ident="a"):
    state=FeatureDriftState.SHIFTED if shift else FeatureDriftState.STABLE
    f=FeatureDriftComparison("BRIGHTNESS",state,ChangeDirection.SAME,20,20,None,None,None,None,None,None,None,None,None,None,False,(),(),"fixture",())
    groups={state:("BRIGHTNESS",)}
    return DistributionShiftComparisonReport(1,f"drift-comparison:sha256:{ident*64}","r","c",status,20,20,DistributionComparisonPolicy().policy_id,DistributionComparisonPolicy(),(f,),
        groups.get(FeatureDriftState.SHIFTED,()),groups.get(FeatureDriftState.STABLE,()),groups.get(FeatureDriftState.PARTIAL,()),groups.get(FeatureDriftState.UNAVAILABLE,()),groups.get(FeatureDriftState.INCOMPARABLE,()),())
def d3(status=RepresentationReportStatus.COMPLETE,shift=False,ident="b"):
    state=RepresentationFeatureState.SHIFTED if shift else RepresentationFeatureState.STABLE
    f=RepresentationFeatureComparison("REPRESENTATION_MMD",state,20,20,(),(),"fixture",());g={state:(f.name,)}
    return RepresentationShiftReport(1,f"representation-comparison:sha256:{ident*64}",RepresentationComparisonPolicy().policy_id,"r","c","s",status,20,20,2,None,(f,),g.get(RepresentationFeatureState.SHIFTED,()),g.get(RepresentationFeatureState.STABLE,()),g.get(RepresentationFeatureState.PARTIAL,()),g.get(RepresentationFeatureState.UNAVAILABLE,()),g.get(RepresentationFeatureState.INCOMPARABLE,()),())
def d4(status=PredictionReportStatus.COMPLETE,shift=False,ident="c"):
    state=PredictionFeatureState.SHIFTED if shift else PredictionFeatureState.STABLE
    f=PredictionFeatureComparison("PREDICTION_ENTROPY",state,PredictionDirection.SAME,20,20,(),(),"fixture",());g={state:(f.name,)}
    return PredictionShiftReport(1,f"prediction-comparison:sha256:{ident*64}",PredictionComparisonPolicy().policy_id,"r","c","s",status,20,20,(f,),g.get(PredictionFeatureState.SHIFTED,()),g.get(PredictionFeatureState.STABLE,()),g.get(PredictionFeatureState.PARTIAL,()),g.get(PredictionFeatureState.UNAVAILABLE,()),g.get(PredictionFeatureState.INCOMPARABLE,()),())
def unavailable3():
    f=RepresentationFeatureComparison("REPRESENTATION_MMD",RepresentationFeatureState.UNAVAILABLE,0,0,(),(),"fixture",())
    return replace(d3(RepresentationReportStatus.UNAVAILABLE),feature_comparisons=(f,),stable_features=(),unavailable_features=(f.name,))
def partial3(shift):return replace(d3(RepresentationReportStatus.PARTIAL,shift),limitations=("partial",))

def test_types_ids_schema_and_fake_rejected():
    q=MultiSignalDriftInterpreter().interpret(d2(),d3(),d4()); assert q.status==InterpretationStatus.COMPLETE
    for args in ((object(),d3(),d4()),(d2(),object(),d4()),(d2(),d3(),object())):
        with pytest.raises(TypeError):MultiSignalDriftInterpreter().interpret(*args)
    with pytest.raises(ValueError):MultiSignalDriftInterpreter().interpret(replace(d2(),comparison_id="bad"),d3(),d4())
    with pytest.raises(ValueError):MultiSignalDriftInterpreter().interpret(d2(),replace(d3(),schema_version=2),d4())

def test_bundle_policy_and_interpretation_id_determinism():
    p=MultiSignalInterpretationPolicy(); assert p.policy_id==MultiSignalInterpretationPolicy().policy_id and p.policy_id.startswith("multisignal-policy:sha256:")
    a=MultiSignalDriftInterpreter().interpret(d2(),d3(),d4());b=MultiSignalDriftInterpreter().interpret(d2(),d3(),d4())
    assert a==b and a.bundle_id.startswith("multisignal-bundle:sha256:") and a.interpretation_id.startswith("multisignal-interpretation:sha256:")
    assert a.bundle_id!=MultiSignalDriftInterpreter().interpret(d2(ident="d"),d3(),d4()).bundle_id
    other=MultiSignalDriftInterpreter(replace(p,rule_version="D5_RULES_V2")).interpret(d2(),d3(),d4());assert other.policy_id!=a.policy_id and other.interpretation_id!=a.interpretation_id

@pytest.mark.parametrize("bits,pattern",[((0,0,0),InterpretationPattern.NO_OBSERVED_SHIFT_ALL_LAYERS),((1,0,0),InterpretationPattern.IMAGE_STATISTICAL_SHIFT_ONLY),
    ((0,1,0),InterpretationPattern.REPRESENTATION_SHIFT_ONLY),((0,0,1),InterpretationPattern.PREDICTION_OUTPUT_SHIFT_ONLY),
    ((1,1,0),InterpretationPattern.IMAGE_STATISTICAL_AND_REPRESENTATION_SHIFT),((1,0,1),InterpretationPattern.IMAGE_STATISTICAL_AND_OUTPUT_SHIFT),
    ((0,1,1),InterpretationPattern.REPRESENTATION_AND_OUTPUT_SHIFT),((1,1,1),InterpretationPattern.BROAD_MULTILAYER_SHIFT)])
def test_complete_pattern_matrix(bits,pattern):
    q=MultiSignalDriftInterpreter().interpret(d2(shift=bool(bits[0])),d3(shift=bool(bits[1])),d4(shift=bool(bits[2])))
    assert q.pattern_code==pattern and q.status==InterpretationStatus.COMPLETE

def test_layer_order_changed_stable_and_observation_axes():
    q=MultiSignalDriftInterpreter().interpret(d2(shift=True),partial3(True),None)
    assert tuple(x.layer for x in q.layers)==(SignalLayer.IMAGE_STATISTICAL,SignalLayer.REPRESENTATION,SignalLayer.PREDICTION_OUTPUT)
    assert q.changed_layers==(SignalLayer.IMAGE_STATISTICAL.value,SignalLayer.REPRESENTATION.value)
    assert q.layers[1].coverage==LayerCoverage.PARTIAL and q.layers[1].observation==LayerObservation.SHIFT_EVIDENCE_PRESENT
    assert q.layers[2].coverage==LayerCoverage.NOT_PROVIDED and q.layers[2].observation==LayerObservation.NOT_ASSESSABLE
    assert SignalLayer.REPRESENTATION.value in q.incomplete_layers and SignalLayer.PREDICTION_OUTPUT.value in q.not_assessable_layers

def test_partial_stable_not_whole_layer_stable():
    q=MultiSignalDriftInterpreter().interpret(d2(),partial3(False),d4())
    assert q.layers[1].observation==LayerObservation.NO_SHIFT_OBSERVED and SignalLayer.REPRESENTATION.value not in q.stable_layers
    assert q.pattern_code==InterpretationPattern.NO_SHIFT_OBSERVED_IN_ASSESSED_EVIDENCE and q.status==InterpretationStatus.PARTIAL

def test_false_all_clear_missing_and_unavailable():
    q=MultiSignalDriftInterpreter().interpret(d2(),d3(),None)
    assert q.pattern_code==InterpretationPattern.NO_SHIFT_OBSERVED_IN_ASSESSED_EVIDENCE and q.status==InterpretationStatus.PARTIAL
    q2=MultiSignalDriftInterpreter().interpret(None,unavailable3(),None)
    assert q2.pattern_code==InterpretationPattern.NO_USABLE_EVIDENCE and q2.status==InterpretationStatus.UNAVAILABLE and not q2.stable_layers
    assert MultiSignalDriftInterpreter().interpret().status==InterpretationStatus.UNAVAILABLE

def test_partial_shift_and_incomparable_prevent_only_pattern():
    q=MultiSignalDriftInterpreter().interpret(d2(),partial3(True),d4())
    assert q.pattern_code==InterpretationPattern.SHIFT_WITH_INCOMPLETE_COVERAGE and q.status==InterpretationStatus.PARTIAL
    badf=PredictionFeatureComparison("PREDICTION_ENTROPY",PredictionFeatureState.INCOMPARABLE,PredictionDirection.UNKNOWN,20,20,(),(),"fixture",())
    inc=replace(d4(PredictionReportStatus.INCOMPARABLE),feature_comparisons=(badf,),stable_features=(),incomparable_features=(badf.name,))
    q2=MultiSignalDriftInterpreter().interpret(d2(shift=True),d3(),inc)
    assert q2.pattern_code==InterpretationPattern.SHIFT_WITH_INCOMPLETE_COVERAGE and q2.layers[2].observation==LayerObservation.NOT_ASSESSABLE

def test_broad_shift_allowed_with_incomplete_coverage_noncausal():
    q=MultiSignalDriftInterpreter().interpret(d2(shift=True),partial3(True),d4(shift=True))
    assert q.pattern_code==InterpretationPattern.BROAD_MULTILAYER_SHIFT and q.status==InterpretationStatus.PARTIAL
    low=q.summary.lower(); assert "common cause" in low and "does not establish" in low
    for affirmative in ("attack confirmed","model failed","accuracy degraded","poisoning detected","compromised"):
        assert affirmative not in low

def test_partition_overlap_duplicates_and_future_size_rejected():
    with pytest.raises(ValueError):MultiSignalDriftInterpreter().interpret(None,replace(d3(),shifted_features=("REPRESENTATION_MMD",)),None)
    with pytest.raises(ValueError):MultiSignalDriftInterpreter().interpret(None,replace(d3(),stable_features=("REPRESENTATION_MMD","REPRESENTATION_MMD")),None)
    policy=MultiSignalInterpretationPolicy(max_feature_names_per_layer=1)
    extra=RepresentationFeatureComparison("X",RepresentationFeatureState.STABLE,20,20,(),(),"fixture",())
    huge=replace(d3(),feature_comparisons=d3().feature_comparisons+(extra,),stable_features=("REPRESENTATION_MMD","X"))
    with pytest.raises(ValueError):MultiSignalDriftInterpreter(policy).interpret(None,huge,None)
    with pytest.raises(ValueError):MultiSignalDriftInterpreter().interpret(None,replace(d3(),status=RepresentationReportStatus.UNAVAILABLE),None)
    empty=replace(d3(),feature_comparisons=(),stable_features=())
    with pytest.raises(ValueError):MultiSignalDriftInterpreter().interpret(None,empty,None)

def test_input_immutability_strict_json_privacy_and_source_ids():
    reports=(d2(shift=True),d3(shift=True),d4(shift=True)); before=tuple(json.dumps(x.to_dict(),sort_keys=True) for x in reports)
    q=MultiSignalDriftInterpreter().interpret(*reports);after=tuple(json.dumps(x.to_dict(),sort_keys=True) for x in reports);assert before==after
    text=json.dumps(q.to_dict(),allow_nan=False);assert all(x.comparison_id in text for x in reports)
    for forbidden in ("centroid","histogram","probabilities","raw_embeddings","timestamp","generated_at","/home/"):assert forbidden not in text

def test_fixed_unsupported_boundaries_and_checks_bounded():
    q=MultiSignalDriftInterpreter(MultiSignalInterpretationPolicy(max_analyst_checks=2)).interpret(d2(shift=True),d3(shift=True),d4(shift=True))
    assert len(q.analyst_checks)==2
    assert {"CAUSE_NOT_ESTABLISHED","MALICIOUS_INTENT_NOT_ESTABLISHED","MODEL_PERFORMANCE_NOT_ESTABLISHED","DEPLOYMENT_SAFETY_NOT_ESTABLISHED","REFERENCE_AUTHENTICITY_NOT_ESTABLISHED"}<=set(q.unsupported_conclusions)

def test_no_prohibited_fields_or_disposition_language():
    text=json.dumps(MultiSignalDriftInterpreter().interpret(d2(shift=True),d3(shift=True),d4(shift=True)).to_dict()).lower()
    for forbidden in ("drift_score","risk_score","trust_score","confidence_score","layer_score","severity","recommendation","disposition","finding_id","modulerun","quarantine"):
        assert forbidden not in text

def test_identity_changes_with_source_id_and_text_determinism():
    i=MultiSignalDriftInterpreter();a=i.interpret(d2(),d3(),d4());b=i.interpret(d2(),d3(),d4(ident="e"))
    assert a.bundle_id!=b.bundle_id and a.interpretation_id!=b.interpretation_id
    assert (a.summary,a.key_observations,a.analyst_checks,a.unsupported_conclusions,a.limitations)==(i.interpret(d2(),d3(),d4()).summary,i.interpret(d2(),d3(),d4()).key_observations,i.interpret(d2(),d3(),d4()).analyst_checks,i.interpret(d2(),d3(),d4()).unsupported_conclusions,i.interpret(d2(),d3(),d4()).limitations)
