"""Pure bounded comparison of D3 representation evidence bundles."""
from __future__ import annotations
from dataclasses import asdict
from hashlib import sha256
import json
import numpy as np
from backend.core.canonical import canonical_json_bytes
from .representation_models import *
from .representation_profile import RepresentationWindowEvidence
from .representation_statistics import (cosine_distance, mmd_squared, normalized_centroid_shift,
    public, relative_l2, resolve_bandwidth, squared_distances)

FEATURE_ORDER=("REPRESENTATION_CENTROID","REPRESENTATION_DISPERSION","REPRESENTATION_VARIANCE",
               "REPRESENTATION_MMD","REPRESENTATION_SUPPORT_DISTANCE")

def _metric(name,value,threshold):
    return RepresentationMetric(name,value,threshold,None if value is None else value>=threshold)

def _empty(name,state,ref,cur,reason,limitation):
    return RepresentationFeatureComparison(name,state,ref,cur,(),(),reason,(limitation,))

def _cap(matrix: np.ndarray, count: int) -> tuple[np.ndarray,bool]:
    if len(matrix)<=count:return matrix,False
    return matrix[np.linspace(0,len(matrix)-1,count,dtype=np.int64)],True

class RepresentationShiftComparator:
    def __init__(self, policy: RepresentationComparisonPolicy|None=None,
                 limits: RepresentationLimits|None=None):
        self.policy=policy or RepresentationComparisonPolicy(); self.limits=limits or RepresentationLimits()

    def compare(self, reference: RepresentationWindowEvidence,
                current: RepresentationWindowEvidence) -> RepresentationShiftReport:
        rp,cp=reference.profile,current.profile
        if rp.role != RepresentationRole.REFERENCE: raise ValueError("reference must have REFERENCE role")
        if cp.role != RepresentationRole.CURRENT: raise ValueError("current must have CURRENT role")
        if rp.schema_version!=REPRESENTATION_SCHEMA_VERSION or cp.schema_version!=REPRESENTATION_SCHEMA_VERSION:
            return self._terminal(rp,cp,RepresentationReportStatus.INCOMPARABLE,"PROFILE_SCHEMA_INCOMPATIBLE")
        if rp.status==RepresentationProfileStatus.UNAVAILABLE or cp.status==RepresentationProfileStatus.UNAVAILABLE:
            return self._terminal(rp,cp,RepresentationReportStatus.UNAVAILABLE,"INPUT_PROFILE_UNAVAILABLE")
        if rp.dimension!=cp.dimension:
            return self._terminal(rp,cp,RepresentationReportStatus.INCOMPARABLE,"REPRESENTATION_DIMENSION_MISMATCH")
        if rp.space_id!=cp.space_id:
            return self._terminal(rp,cp,RepresentationReportStatus.INCOMPARABLE,"REPRESENTATION_SPACE_MISMATCH")
        support=rp.sample_count>=self.policy.min_reference_samples and cp.sample_count>=self.policy.min_current_samples
        limitations=[]
        if not support: limitations.append("INSUFFICIENT_SAMPLE_SUPPORT")
        if rp.status==RepresentationProfileStatus.PARTIAL or cp.status==RepresentationProfileStatus.PARTIAL:
            limitations.append("INPUT_REPRESENTATION_PROFILE_PARTIAL")
        x,y=reference.selected_embeddings,current.selected_embeddings
        if not support:
            features=tuple(_empty(n,RepresentationFeatureState.PARTIAL,len(x),len(y),
                "Insufficient sample support for a stable or shifted conclusion.","INSUFFICIENT_SAMPLE_SUPPORT") for n in FEATURE_ORDER)
            return self._report(rp,cp,RepresentationReportStatus.PARTIAL,features,None,limitations)
        features=[]
        # Centroid: both complementary metrics must exceed, or exact degeneracy changes.
        lc,rc=np.asarray(rp.centroid),np.asarray(cp.centroid)
        cosine,degenerate=cosine_distance(lc,rc,self.policy.epsilon)
        movement,collapse=normalized_centroid_shift(lc,rc,
            float(np.sqrt(np.mean(np.sum((x-lc)**2,axis=1)))),float(np.sqrt(np.mean(np.sum((y-rc)**2,axis=1)))),self.policy.epsilon)
        cm=(_metric("CENTROID_COSINE_DISTANCE",cosine,self.policy.centroid_cosine_distance_threshold),
            _metric("NORMALIZED_CENTROID_SHIFT",movement,self.policy.normalized_centroid_shift_threshold))
        shifted=degenerate or collapse or all(m.exceeded is True for m in cm)
        features.append(self._feature(FEATURE_ORDER[0],shifted,rp,cp,cm,(),
            "Embedding centroid moved under the configured policy." if shifted else "Centroid evidence remains within configured thresholds."))
        # Dispersion summary effects.
        a,b=rp.radial_summary,cp.radial_summary
        scale=(a.population_std**2+b.population_std**2)**.5/(2**.5); delta=abs(b.mean-a.mean)
        constant=scale<=self.policy.epsilon and delta>self.policy.epsilon
        smd=None if constant else (0.0 if scale<=self.policy.epsilon else public(delta/scale))
        qscale=max(a.q95-a.q05,b.q95-b.q05); qdelta=max(abs(getattr(a,n)-getattr(b,n)) for n in ("q05","q25","median","q75","q95"))
        qconstant=qscale<=self.policy.epsilon and qdelta>self.policy.epsilon
        quant=None if qconstant else (0.0 if qscale<=self.policy.epsilon else public(qdelta/qscale))
        symmetric=public(2*abs(b.median-a.median)/(abs(a.median)+abs(b.median)+self.policy.epsilon))
        dm=tuple(_metric(n,v,self.policy.dispersion_shift_threshold) for n,v in
            (("STANDARDIZED_MEAN_DIFFERENCE",smd),("ROBUST_QUANTILE_SHIFT",quant),("SYMMETRIC_MEDIAN_CHANGE",symmetric)))
        features.append(self._feature(FEATURE_ORDER[1],constant or qconstant or sum(m.exceeded is True for m in dm)>=2,rp,cp,dm,(),"Radial feature-space dispersion comparison completed."))
        # Diagonal variance.
        lv,rv=np.asarray(rp.variance_diagonal),np.asarray(cp.variance_diagonal)
        rel,collapsed=relative_l2(lv,rv,self.policy.epsilon); vcos,vdeg=cosine_distance(lv,rv,self.policy.epsilon)
        ltot,rtot=float(lv.sum()),float(rv.sum()); total=None if min(ltot,rtot)<=self.policy.epsilon and abs(ltot-rtot)>self.policy.epsilon else public(abs(rtot-ltot)/max(ltot,rtot,self.policy.epsilon))
        vm=tuple(_metric(n,v,self.policy.variance_shift_threshold) for n,v in (("RELATIVE_L2_VARIANCE",rel),("VARIANCE_COSINE_DISTANCE",vcos),("RELATIVE_TOTAL_VARIANCE_CHANGE",total)))
        features.append(self._feature(FEATURE_ORDER[2],collapsed or vdeg or sum(m.exceeded is True for m in vm)>=2,rp,cp,vm,(),"Per-dimension variance comparison completed."))
        # Bounded MMD.
        px,tx=_cap(x,self.limits.max_pairwise_samples); py,ty=_cap(y,self.limits.max_pairwise_samples)
        bandwidth,fallback=resolve_bandwidth(px,py,self.policy.bandwidth_strategy,self.policy.fixed_bandwidth,self.policy.epsilon)
        mv=mmd_squared(px,py,bandwidth); ml=[]
        if tx or ty: ml.append("PAIRWISE_EVIDENCE_DETERMINISTICALLY_SUBSAMPLED")
        if fallback: ml.append("RBF_BANDWIDTH_FALLBACK_USED")
        mm=(_metric("BIASED_MMD_SQUARED",mv,self.policy.mmd_threshold),)
        features.append(self._feature(FEATURE_ORDER[3],mm[0].exceeded is True,rp,cp,mm,tuple(ml),"Bounded RBF-kernel MMD comparison completed."))
        limitations.extend(ml)
        # Nearest-reference support.
        sx,tsx=_cap(x,self.limits.max_nearest_reference_points); sy,tsy=_cap(y,self.limits.max_nearest_reference_points)
        if len(sx)<2:
            features.append(_empty(FEATURE_ORDER[4],RepresentationFeatureState.UNAVAILABLE,len(x),len(y),"Reference self-support requires at least two points.","REFERENCE_SELF_SUPPORT_UNAVAILABLE"))
        else:
            selfd=np.sqrt(squared_distances(sx,sx)); np.fill_diagonal(selfd,np.inf)
            base=float(np.median(selfd.min(axis=1))); current_nn=np.sqrt(squared_distances(sy,sx)).min(axis=1); observed=float(np.median(current_nn))
            separated=base<=self.policy.epsilon and observed>self.policy.epsilon
            ratio=None if separated else (1.0 if base<=self.policy.epsilon else public(observed/base))
            sm=(_metric("MEDIAN_NEAREST_REFERENCE_RATIO",ratio,self.policy.nearest_support_shift_threshold),
                RepresentationMetric("CURRENT_MEAN_NEAREST_REFERENCE_DISTANCE",public(current_nn.mean()),None,None),
                RepresentationMetric("CURRENT_Q95_NEAREST_REFERENCE_DISTANCE",public(np.quantile(current_nn,.95)),None,None))
            sl=("SUPPORT_EVIDENCE_DETERMINISTICALLY_SUBSAMPLED",) if tsx or tsy else ()
            features.append(self._feature(FEATURE_ORDER[4],separated or sm[0].exceeded is True,rp,cp,sm,sl,"Nearest-reference support comparison completed.")); limitations.extend(sl)
        degraded=limitations or any(f.state in {RepresentationFeatureState.PARTIAL,RepresentationFeatureState.UNAVAILABLE} for f in features)
        return self._report(rp,cp,RepresentationReportStatus.PARTIAL if degraded else RepresentationReportStatus.COMPLETE,tuple(features),bandwidth,limitations)

    def _feature(self,name,shifted,rp,cp,metrics,limitations,reason):
        return RepresentationFeatureComparison(name,RepresentationFeatureState.SHIFTED if shifted else RepresentationFeatureState.STABLE,
            rp.sample_count,cp.sample_count,metrics,tuple(sorted(m.name for m in metrics if m.exceeded)),reason,tuple(sorted(limitations)))

    def _terminal(self,rp,cp,status,limitation):
        state=RepresentationFeatureState.INCOMPARABLE if status==RepresentationReportStatus.INCOMPARABLE else RepresentationFeatureState.UNAVAILABLE
        features=tuple(_empty(n,state,rp.sample_count,cp.sample_count,"Representation evidence cannot be compared.",limitation) for n in FEATURE_ORDER)
        return self._report(rp,cp,status,features,None,[limitation])

    def _report(self,rp,cp,status,features,bandwidth,limitations):
        groups={s:tuple(f.name for f in features if f.state==s) for s in RepresentationFeatureState}
        core={"schema_version":1,"policy_id":self.policy.policy_id,"policy":self.policy.to_dict(),
            "reference_profile_id":rp.profile_id,"current_profile_id":cp.profile_id,
            "representation_space_id":rp.space_id if rp.space_id==cp.space_id else None,"status":status.value,
            "reference_sample_count":rp.sample_count,"current_sample_count":cp.sample_count,
            "dimension":rp.dimension if rp.dimension==cp.dimension else None,"resolved_bandwidth":bandwidth,
            "feature_comparisons":[asdict(f) for f in features],"shifted_features":groups[RepresentationFeatureState.SHIFTED],
            "stable_features":groups[RepresentationFeatureState.STABLE],"partial_features":groups[RepresentationFeatureState.PARTIAL],
            "unavailable_features":groups[RepresentationFeatureState.UNAVAILABLE],"incomparable_features":groups[RepresentationFeatureState.INCOMPARABLE],
            "limitations":tuple(sorted(set(limitations)))}
        cid="representation-comparison:sha256:"+sha256(canonical_json_bytes(core)).hexdigest()
        report=RepresentationShiftReport(1,cid,self.policy.policy_id,rp.profile_id,cp.profile_id,core["representation_space_id"],status,
            rp.sample_count,cp.sample_count,core["dimension"],bandwidth,features,core["shifted_features"],core["stable_features"],
            core["partial_features"],core["unavailable_features"],core["incomparable_features"],core["limitations"])
        json.dumps(report.to_dict(),allow_nan=False); return report
