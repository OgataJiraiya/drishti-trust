"""D3 model-agnostic representation drift validation."""
from dataclasses import replace
import json
import numpy as np
import pytest
from modules.distribution_shift import *
from modules.distribution_shift.representation_statistics import (cosine_distance, mmd_squared,
    resolve_bandwidth, row_digest)

def cloud(n=40,d=4):
    rng=np.random.default_rng(271828); return rng.normal(size=(n,d))
def descriptor(d=4,norm=NormalizationMode.NONE,name="caller-local"):
    return RepresentationSpaceDescriptor(name,"1",d,norm)
def build(x,desc=None,limits=None,ids=None):
    p=RepresentationProfiler(limits); d=desc or descriptor(x.shape[1])
    return p.build_reference(x,d,ids),p.build_current(x.copy(),d,ids)

def test_descriptor_and_policy_id_determinism_and_semantic_changes():
    a=descriptor(); assert a.space_id==descriptor().space_id
    assert a.space_id.startswith("representation-space:sha256:") and len(a.space_id)==92
    assert a.space_id!=descriptor(name="other").space_id
    p=RepresentationComparisonPolicy(); assert p.policy_id==RepresentationComparisonPolicy().policy_id
    assert p.policy_id!=replace(p,mmd_threshold=.06).policy_id

@pytest.mark.parametrize("bad",[np.ones(3),np.ones((2,2,2)),np.empty((0,2)),np.empty((2,0))])
def test_shape_rejected(bad):
    with pytest.raises(ValueError): RepresentationProfiler().build_reference(bad,descriptor(bad.shape[-1] if bad.ndim else 1))

@pytest.mark.parametrize("bad,error",[(np.array([[object()]],dtype=object),TypeError),
    (np.array([["x"]]),TypeError),(np.array([[np.nan]]),ValueError),(np.array([[np.inf]]),ValueError),
    (np.array([[-np.inf]]),ValueError)])
def test_malformed_rejected(bad,error):
    with pytest.raises(error): RepresentationProfiler().build_reference(bad,descriptor(1))

def test_limits_enforced_and_truncation_deterministic_partial():
    x=cloud(12,3)
    with pytest.raises(ValueError): RepresentationProfiler(RepresentationLimits(max_dimensions=2)).build_reference(x,descriptor(3))
    with pytest.raises(ValueError): RepresentationProfiler(RepresentationLimits(max_total_values=20)).build_reference(x,descriptor(3))
    lim=RepresentationLimits(max_samples=5,max_total_values=100)
    a=RepresentationProfiler(lim).build_reference(x,descriptor(3)); b=RepresentationProfiler(lim).build_reference(x,descriptor(3))
    assert a.profile.status==RepresentationProfileStatus.PARTIAL and a.profile.profile_id==b.profile.profile_id
    assert len(a.selected_embeddings)==5 and "SAMPLE_SELECTION_TRUNCATED" in a.profile.limitations

def test_immutability_duplicates_layout_row_digest_and_privacy():
    x=np.asfortranarray(np.array([[1.,2.],[1.,2.],[3.,4.]])); original=x.copy()
    r,_=build(x,descriptor(2)); assert np.array_equal(x,original)
    with pytest.raises(ValueError): r.selected_embeddings[0,0]=99
    assert r.profile.sample_count==3
    assert row_digest(x[0])==row_digest(np.ascontiguousarray(x)[0])
    data=r.profile.to_dict(); assert "selected_embeddings" not in data and "raw_embeddings" not in data
    assert json.dumps(data,allow_nan=False)

def test_order_invariance_with_and_without_ids_and_role_identity():
    x=cloud(30,3); order=np.arange(30)[::-1]; p=RepresentationProfiler(); d=descriptor(3)
    a=p.build_reference(x,d); b=p.build_reference(x[order],d)
    assert a.profile.profile_id==b.profile.profile_id
    ids=tuple(f"s{i}" for i in range(30)); c=p.build_reference(x,d,ids); e=p.build_reference(x[order],d,tuple(ids[i] for i in order))
    assert c.profile.profile_id==e.profile.profile_id
    assert p.build_current(x,d).profile.profile_id==a.profile.profile_id

def test_ids_validated():
    x=cloud(2,2); p=RepresentationProfiler()
    with pytest.raises(ValueError): p.build_reference(x,descriptor(2),["a","a"])
    with pytest.raises(ValueError): p.build_reference(x,descriptor(2),["a"])

def test_profile_statistics_and_covariance_bound():
    x=np.array([[0.,0.],[2.,0.]]) ; r,_=build(x,descriptor(2))
    assert r.profile.centroid==(1.,0.) and r.profile.centroid_norm==1
    assert r.profile.norm_summary.mean==1 and r.profile.radial_summary.mean==1
    assert r.profile.variance_diagonal==(1.,0.) and r.profile.variance_summary.total==1
    assert r.profile.covariance_commitment is not None
    lim=RepresentationLimits(max_covariance_dimensions=1)
    q=RepresentationProfiler(lim).build_reference(x,descriptor(2))
    assert q.profile.status==RepresentationProfileStatus.PARTIAL
    assert q.profile.covariance_commitment is None and "COVARIANCE_UNAVAILABLE_DIMENSION_BOUND" in q.profile.limitations

def test_normalization_and_zero_vector():
    d=descriptor(2,NormalizationMode.L2_PER_SAMPLE); p=RepresentationProfiler()
    a=p.build_reference(np.array([[1.,0.],[0.,2.]]),d); b=p.build_current(np.array([[5.,0.],[0.,9.]]),d)
    assert a.profile.profile_id==b.profile.profile_id
    with pytest.raises(ValueError): p.build_reference(np.array([[0.,0.]]),d)

def test_identical_complete_stable_strict_json_and_deterministic():
    r,c=build(cloud()); comp=RepresentationShiftComparator(); a=comp.compare(r,c); b=comp.compare(r,c)
    assert a.status==RepresentationReportStatus.COMPLETE and set(a.stable_features)==set(FEATURES)
    assert not a.shifted_features and a.comparison_id==b.comparison_id and a.to_dict()==b.to_dict()
    assert a.comparison_id.startswith("representation-comparison:sha256:") and len(a.comparison_id)==97
    json.dumps(a.to_dict(),allow_nan=False)

FEATURES=("REPRESENTATION_CENTROID","REPRESENTATION_DISPERSION","REPRESENTATION_VARIANCE","REPRESENTATION_MMD","REPRESENTATION_SUPPORT_DISTANCE")

def test_translation_scenario():
    x=cloud(80); p=RepresentationProfiler(); d=descriptor(); r=p.build_reference(x,d); c=p.build_current(x+4,d)
    q=RepresentationShiftComparator().compare(r,c)
    assert {"REPRESENTATION_CENTROID","REPRESENTATION_MMD","REPRESENTATION_SUPPORT_DISTANCE"}<=set(q.shifted_features)
    assert "REPRESENTATION_VARIANCE" in q.stable_features and "REPRESENTATION_DISPERSION" in q.stable_features

def test_dispersion_expansion_scenario():
    x=cloud(80); center=x.mean(0); p=RepresentationProfiler(); d=descriptor(); r=p.build_reference(x,d); c=p.build_current(center+4*(x-center),d)
    q=RepresentationShiftComparator().compare(r,c)
    assert {"REPRESENTATION_DISPERSION","REPRESENTATION_VARIANCE","REPRESENTATION_MMD"}<=set(q.shifted_features)
    assert "REPRESENTATION_CENTROID" in q.stable_features

def test_mixture_and_distant_support_scenarios():
    rng=np.random.default_rng(7); left=rng.normal(-3,.2,(40,2)); right=rng.normal(3,.2,(40,2)); x=np.vstack((left,right)); y=np.vstack((left[:8],right))
    p=RepresentationProfiler(); d=descriptor(2); r=p.build_reference(x,d)
    mixture=RepresentationShiftComparator().compare(r,p.build_current(y,d)); assert mixture.feature_comparisons[3].metrics[0].value>0
    distant=RepresentationShiftComparator().compare(r,p.build_current(x+20,d))
    assert {"REPRESENTATION_CENTROID","REPRESENTATION_MMD","REPRESENTATION_SUPPORT_DISTANCE"}<=set(distant.shifted_features)

def test_insufficient_support_has_no_stable_or_shifted():
    r,c=build(cloud(3)); q=RepresentationShiftComparator().compare(r,c)
    assert q.status==RepresentationReportStatus.PARTIAL and not q.stable_features and not q.shifted_features
    assert set(q.partial_features)==set(FEATURES)

def test_dimension_space_and_role_mismatch():
    p=RepresentationProfiler(); r=p.build_reference(cloud(30,2),descriptor(2)); c=p.build_current(cloud(30,3),descriptor(3))
    q=RepresentationShiftComparator().compare(r,c); assert q.status==RepresentationReportStatus.INCOMPARABLE
    c2=p.build_current(cloud(30,2),descriptor(2,name="different")); assert RepresentationShiftComparator().compare(r,c2).status==RepresentationReportStatus.INCOMPARABLE
    with pytest.raises(ValueError): RepresentationShiftComparator().compare(c2,r)

def test_pairwise_partial_and_support_unavailable():
    x=cloud(30); r,c=build(x); lim=RepresentationLimits(max_pairwise_samples=5,max_nearest_reference_points=5)
    q=RepresentationShiftComparator(limits=lim).compare(r,c)
    assert q.status==RepresentationReportStatus.PARTIAL and "PAIRWISE_EVIDENCE_DETERMINISTICALLY_SUBSAMPLED" in q.limitations
    r1,c1=build(cloud(1)); policy=RepresentationComparisonPolicy(min_reference_samples=1,min_current_samples=1)
    z=RepresentationShiftComparator(policy).compare(r1,c1); assert "REPRESENTATION_SUPPORT_DISTANCE" in z.unavailable_features

def test_numeric_edge_cases_mmd_symmetry_bandwidth_and_cosine():
    z=np.zeros(2); o=np.ones(2); assert cosine_distance(z,z,1e-12)==(0.,False)
    assert cosine_distance(z,o,1e-12)==(None,True)
    x=cloud(10,2); y=x+1
    bw,fallback=resolve_bandwidth(x,y,"MEDIAN_SQUARED_DISTANCE",1,1e-12); assert bw>0 and not fallback
    assert mmd_squared(x,y,bw)==mmd_squared(y,x,bw) and mmd_squared(x,x,bw)==0
    assert resolve_bandwidth(np.zeros((2,2)),np.zeros((2,2)),"MEDIAN_SQUARED_DISTANCE",1,1e-12)==(1.,True)

@pytest.mark.parametrize("kwargs",[{"mmd_threshold":float("nan")},{"mmd_threshold":float("inf")},{"mmd_threshold":-1},{"min_reference_samples":0}])
def test_policy_rejects_invalid(kwargs):
    with pytest.raises(ValueError): RepresentationComparisonPolicy(**kwargs)

def test_public_contract_has_no_scores_findings_or_disposition():
    r,c=build(cloud()); text=json.dumps(RepresentationShiftComparator().compare(r,c).to_dict()).lower()
    for forbidden in ("drift_score","risk_score","trust_score","severity","disposition","finding_id","modulerun","recommendation"):
        assert forbidden not in text
