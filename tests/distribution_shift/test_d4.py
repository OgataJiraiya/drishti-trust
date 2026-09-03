"""D4 deterministic caller-supplied prediction/output drift tests."""
from dataclasses import replace
import json
import numpy as np
import pytest
from modules.distribution_shift import *
from modules.distribution_shift.prediction_statistics import histogram,margin,normalized_entropy

FEATURES=("PREDICTED_LABEL_DISTRIBUTION","TOP1_CONFIDENCE","PREDICTION_ENTROPY","TOP1_MARGIN","ABSTENTION_RATE","DECLARED_UNKNOWN_RATE")
def desc(tier=PredictionEvidenceTier.FULL_PROBABILITIES,labels=("A","B"),flags=False,digest=None):
    return PredictionOutputSpaceDescriptor(tier,labels,model_identity_digest=digest,
        abstention_semantics="caller rejection flag" if flags else None,unknown_semantics="caller unknown/OOD flag" if flags else None)
def full(n=40,p=.9,flags=False):
    return [PredictionRecord("A",f"s{i}",p,(p,1-p),False,False) if flags else PredictionRecord("A",f"s{i}",p,(p,1-p)) for i in range(n)]
def profiles(records,d=None,limits=None):
    d=d or desc(); p=PredictionProfiler(limits); return p.build_reference(records,d),p.build_current(list(records),d)
def feature(report,name):return next(x for x in report.feature_comparisons if x.name==name)

def test_descriptor_identity_semantics_and_format():
    d=desc(); assert d.space_id==desc().space_id and d.space_id.startswith("prediction-space:sha256:") and len(d.space_id)==88
    assert d.identity_basis==PredictionIdentityBasis.CALLER_DECLARED
    assert d.space_id!=desc(labels=("B","A")).space_id!=desc(labels=("A","B","C")).space_id
    assert d.space_id!=desc(PredictionEvidenceTier.LABEL_ONLY).space_id
    digest="sha256:"+"a"*64; assert desc(digest=digest).identity_basis==PredictionIdentityBasis.DIGEST_DECLARED
    assert desc(digest=digest).space_id!=desc(digest="sha256:"+"b"*64).space_id

@pytest.mark.parametrize("labels",[(),("A","A"),("",)])
def test_bad_class_vocabulary(labels):
    with pytest.raises(ValueError):desc(labels=labels)

def test_all_tiers_and_graceful_degradation():
    label=[PredictionRecord("A") for _ in range(30)]; d=desc(PredictionEvidenceTier.LABEL_ONLY); r,c=profiles(label,d)
    q=PredictionShiftComparator().compare(r,c); assert q.stable_features==("PREDICTED_LABEL_DISTRIBUTION",) and set(q.unavailable_features)==set(FEATURES[1:])
    top=[PredictionRecord("A",confidence=.8) for _ in range(30)]; d2=desc(PredictionEvidenceTier.TOP1_CONFIDENCE); r,c=profiles(top,d2); q=PredictionShiftComparator().compare(r,c)
    assert set(q.stable_features)==set(FEATURES[:2]) and set(q.unavailable_features)==set(FEATURES[2:])
    r,c=profiles(full()); q=PredictionShiftComparator().compare(r,c); assert set(q.stable_features)==set(FEATURES[:4])

@pytest.mark.parametrize("record,code",[(PredictionRecord("Z",probabilities=(.9,.1)),"INVALID_LABEL"),
    (PredictionRecord("A",confidence=-.1,probabilities=(.9,.1)),"INVALID_CONFIDENCE"),(PredictionRecord("A",confidence=1.1,probabilities=(.9,.1)),"INVALID_CONFIDENCE"),
    (PredictionRecord("A",confidence=float("nan"),probabilities=(.9,.1)),"INVALID_CONFIDENCE"),(PredictionRecord("A",confidence=float("inf"),probabilities=(.9,.1)),"INVALID_CONFIDENCE"),
    (PredictionRecord("A",probabilities=((.9,.1),)),"INVALID_PROBABILITY_VECTOR"),(PredictionRecord("A",probabilities=(1.,)),"INVALID_PROBABILITY_VECTOR"),
    (PredictionRecord("A",probabilities=(-.1,1.1)),"INVALID_PROBABILITY_VECTOR"),(PredictionRecord("A",probabilities=(float("nan"),.1)),"INVALID_PROBABILITY_VECTOR"),
    (PredictionRecord("A",probabilities=(float("inf"),0)),"INVALID_PROBABILITY_VECTOR"),(PredictionRecord("A",probabilities=(.6,.3)),"PROBABILITY_SUM_MISMATCH"),
    (PredictionRecord("A",probabilities=(.1,.9)),"LABEL_ARGMAX_MISMATCH"),(PredictionRecord("A",confidence=.8,probabilities=(.9,.1)),"CONFIDENCE_PROBABILITY_MISMATCH")])
def test_invalid_records_degrade(record,code):
    p=PredictionProfiler(); q=p.build_reference([record],desc()); assert q.status==PredictionProfileStatus.UNAVAILABLE and q.failure_counts==((code,1),)

def test_probability_tolerance_ties_entropy_margin_edges():
    lim=PredictionProfileLimits(probability_sum_tolerance=1e-5); r=PredictionProfiler(lim).build_reference([PredictionRecord("A",probabilities=(.500001,.5))],desc())
    assert r.status==PredictionProfileStatus.COMPLETE
    tied=PredictionProfiler().build_reference([PredictionRecord("A",probabilities=(.5,.5))],desc()); assert tied.sample_count==1
    assert normalized_entropy((1.,0.))==0 and normalized_entropy((.5,.5))==1 and normalized_entropy((1.,))==0
    assert margin((.9,.1))==.8 and margin((1.,)) is None
    h=histogram([0.,1.],10); assert h[0]==1 and h[-1]==1

def test_one_class_margin_unavailable():
    d=desc(labels=("A",)); rec=[PredictionRecord("A",probabilities=(1.,)) for _ in range(30)]; r,c=profiles(rec,d); q=PredictionShiftComparator().compare(r,c)
    assert feature(q,"PREDICTION_ENTROPY").state==PredictionFeatureState.STABLE and feature(q,"TOP1_MARGIN").state==PredictionFeatureState.UNAVAILABLE

def test_flags_declared_only_and_rates():
    bad=PredictionProfiler().build_reference([PredictionRecord("A",probabilities=(.9,.1),abstained=False)],desc()); assert bad.failure_counts==(("INVALID_ABSTENTION_FLAG",1),)
    records=full(40,flags=True); records[0]=replace(records[0],abstained=True,unknown_or_ood=True); r,_=profiles(records,desc(flags=True))
    assert r.abstention_count==1 and r.abstention_rate==.025 and r.unknown_rate==.025

def test_limits_truncation_failures_and_total_values():
    with pytest.raises(ValueError):profiles(full(10),desc(),PredictionProfileLimits(max_classes=1))
    with pytest.raises(ValueError):profiles(full(10),desc(),PredictionProfileLimits(max_total_probability_values=10))
    lim=PredictionProfileLimits(max_samples=5); a=PredictionProfiler(lim).build_reference(full(20),desc()); b=PredictionProfiler(lim).build_reference(full(20),desc())
    assert a.status==PredictionProfileStatus.PARTIAL and a.sample_count==5 and a.profile_id==b.profile_id

def test_order_invariance_duplicates_role_and_privacy():
    records=full(30); p=PredictionProfiler(); d=desc(); a=p.build_reference(records,d); b=p.build_reference(records[::-1],d); c=p.build_current(records,d)
    assert a.profile_id==b.profile_id==c.profile_id and a.sample_count==30
    dup=[records[0],records[0]]; assert p.build_reference(dup,d).sample_count==2
    text=json.dumps(a.to_dict(),allow_nan=False); assert "probabilities" not in text and "/home/" not in text and "timestamp" not in text

def test_profile_aggregates_and_strict_json():
    records=[PredictionRecord("A",probabilities=(1.,0.)),PredictionRecord("B",probabilities=(0.,1.))]
    r,_=profiles(records); assert r.label_counts==(("A",1),("B",1)); assert r.confidence_summary.mean==1 and r.entropy_summary.mean==0 and r.margin_summary.mean==1
    assert sum(r.confidence_histogram)==2 and json.dumps(r.to_dict(),allow_nan=False)
    assert r.profile_id.startswith("prediction-profile:sha256:") and len(r.profile_id)==90

def test_identical_full_output_with_flags_all_stable():
    r,c=profiles(full(40,flags=True),desc(flags=True)); q=PredictionShiftComparator().compare(r,c)
    assert q.status==PredictionReportStatus.COMPLETE and set(q.stable_features)==set(FEATURES) and not q.shifted_features
    assert q.comparison_id.startswith("prediction-comparison:sha256:") and len(q.comparison_id)==93
    assert json.dumps(q.to_dict(),allow_nan=False)

def test_predicted_label_frequency_shift():
    d=desc(PredictionEvidenceTier.LABEL_ONLY); ref=[PredictionRecord("A") for _ in range(80)]+[PredictionRecord("B") for _ in range(20)]
    cur=[PredictionRecord("A") for _ in range(20)]+[PredictionRecord("B") for _ in range(80)]; p=PredictionProfiler(); q=PredictionShiftComparator().compare(p.build_reference(ref,d),p.build_current(cur,d))
    f=feature(q,"PREDICTED_LABEL_DISTRIBUTION"); assert f.state==PredictionFeatureState.SHIFTED and all(m.value>0 for m in f.metrics)

@pytest.mark.parametrize("refv,curv,expected",[(.9,.55,PredictionDirection.DECREASED),(.55,.9,PredictionDirection.INCREASED)])
def test_confidence_shift_directions(refv,curv,expected):
    d=desc(PredictionEvidenceTier.TOP1_CONFIDENCE); p=PredictionProfiler(); a=[PredictionRecord("A",confidence=refv) for _ in range(40)];b=[PredictionRecord("A",confidence=curv) for _ in range(40)]
    f=feature(PredictionShiftComparator().compare(p.build_reference(a,d),p.build_current(b,d)),"TOP1_CONFIDENCE"); assert f.state==PredictionFeatureState.SHIFTED and f.direction==expected

def test_entropy_increase_and_margin_decrease():
    p=PredictionProfiler(); d=desc(); ref=full(40,.9); cur=full(40,.55); q=PredictionShiftComparator().compare(p.build_reference(ref,d),p.build_current(cur,d))
    assert feature(q,"PREDICTION_ENTROPY").state==PredictionFeatureState.SHIFTED and feature(q,"PREDICTION_ENTROPY").direction==PredictionDirection.INCREASED
    assert feature(q,"TOP1_MARGIN").state==PredictionFeatureState.SHIFTED and feature(q,"TOP1_MARGIN").direction==PredictionDirection.DECREASED

def test_abstention_and_unknown_shift():
    p=PredictionProfiler(); d=desc(flags=True); ref=full(40,flags=True); cur=full(40,flags=True)
    cur=[replace(x,abstained=i<12,unknown_or_ood=i<14) for i,x in enumerate(cur)]; q=PredictionShiftComparator().compare(p.build_reference(ref,d),p.build_current(cur,d))
    assert feature(q,"ABSTENTION_RATE").state==PredictionFeatureState.SHIFTED
    u=feature(q,"DECLARED_UNKNOWN_RATE"); assert u.state==PredictionFeatureState.SHIFTED and "Caller-declared" in u.reason

def test_insufficient_partial_unavailable_and_mismatch():
    r,c=profiles(full(3)); q=PredictionShiftComparator().compare(r,c); assert not q.stable_features and not q.shifted_features and set(q.partial_features)==set(FEATURES)
    unavailable=PredictionProfiler().build_reference([],desc()); assert PredictionShiftComparator().compare(unavailable,c).status==PredictionReportStatus.UNAVAILABLE
    partial=PredictionProfiler(PredictionProfileLimits(max_samples=20)).build_reference(full(30),desc()); current=PredictionProfiler(PredictionProfileLimits(max_samples=20)).build_current(full(30),desc())
    assert PredictionShiftComparator().compare(partial,current).status==PredictionReportStatus.PARTIAL
    other_records=[PredictionRecord("B",f"o{i}",.9,(.9,.1)) for i in range(40)]
    other=PredictionProfiler().build_current(other_records,desc(labels=("B","A")))
    assert PredictionShiftComparator().compare(profiles(full())[0],other).status==PredictionReportStatus.INCOMPARABLE

def test_roles_policy_ids_determinism_and_swap():
    policy=PredictionComparisonPolicy(); assert policy.policy_id==PredictionComparisonPolicy().policy_id and policy.policy_id!=replace(policy,label_js_threshold=.2).policy_id
    p=PredictionProfiler();d=desc(PredictionEvidenceTier.TOP1_CONFIDENCE); a=[PredictionRecord("A",confidence=.9) for _ in range(40)];b=[PredictionRecord("A",confidence=.5) for _ in range(40)]
    ar=p.build_reference(a,d);bc=p.build_current(b,d); q=PredictionShiftComparator().compare(ar,bc); q2=PredictionShiftComparator().compare(p.build_reference(b,d),p.build_current(a,d))
    assert q.comparison_id!=q2.comparison_id and feature(q,"TOP1_CONFIDENCE").metrics==feature(q2,"TOP1_CONFIDENCE").metrics
    assert feature(q,"TOP1_CONFIDENCE").direction==PredictionDirection.DECREASED and feature(q2,"TOP1_CONFIDENCE").direction==PredictionDirection.INCREASED
    with pytest.raises(ValueError):PredictionShiftComparator().compare(bc,ar)

@pytest.mark.parametrize("kwargs",[{"label_js_threshold":float("nan")},{"label_js_threshold":float("inf")},{"label_js_threshold":-1},{"min_reference_samples":0}])
def test_policy_validation(kwargs):
    with pytest.raises(ValueError):PredictionComparisonPolicy(**kwargs)

def test_input_not_mutated_and_no_prohibited_public_claim_fields():
    probabilities=np.array([.9,.1]); record=PredictionRecord("A",probabilities=probabilities); before=probabilities.copy(); r=PredictionProfiler().build_reference([record],desc()); assert np.array_equal(probabilities,before)
    text=json.dumps(r.to_dict()).lower()
    for forbidden in ("drift_score","risk_score","trust_score","severity","recommendation","disposition","finding_id","modulerun"):
        assert forbidden not in text
