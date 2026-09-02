"""Pure feature-level comparison of aggregate D4 prediction profiles."""
from __future__ import annotations
from dataclasses import asdict
from hashlib import sha256
import json
from backend.core.canonical import canonical_json_bytes
from .prediction_models import *
from .statistics import (histogram_wasserstein,jensen_shannon,robust_quantile_shift,
    standardized_mean_difference,symmetric_median_change,total_variation)

FEATURE_ORDER=("PREDICTED_LABEL_DISTRIBUTION","TOP1_CONFIDENCE","PREDICTION_ENTROPY","TOP1_MARGIN","ABSTENTION_RATE","DECLARED_UNKNOWN_RATE")
def metric(name,value,threshold):return PredictionMetric(name,value,threshold,None if value is None else value>=threshold)
def direction(a,b,e):return PredictionDirection.SAME if abs(a-b)<=e else PredictionDirection.INCREASED if b>a else PredictionDirection.DECREASED
def empty(name,state,r,c,reason,lim):return PredictionFeatureComparison(name,state,PredictionDirection.UNKNOWN,r,c,(),(),reason,(lim,))

class PredictionShiftComparator:
    def __init__(self,policy:PredictionComparisonPolicy|None=None):self.policy=policy or PredictionComparisonPolicy()
    def compare(self,reference:PredictionProfile,current:PredictionProfile):
        if reference.role!=PredictionRole.REFERENCE:raise ValueError("reference must have REFERENCE role")
        if current.role!=PredictionRole.CURRENT:raise ValueError("current must have CURRENT role")
        if reference.schema_version!=PREDICTION_SCHEMA_VERSION or current.schema_version!=PREDICTION_SCHEMA_VERSION:return self._terminal(reference,current,PredictionReportStatus.INCOMPARABLE,"PROFILE_SCHEMA_INCOMPATIBLE")
        if reference.status==PredictionProfileStatus.UNAVAILABLE or current.status==PredictionProfileStatus.UNAVAILABLE:return self._terminal(reference,current,PredictionReportStatus.UNAVAILABLE,"INPUT_PROFILE_UNAVAILABLE")
        if reference.output_space_id!=current.output_space_id:return self._terminal(reference,current,PredictionReportStatus.INCOMPARABLE,"OUTPUT_SPACE_MISMATCH")
        support=reference.sample_count>=self.policy.min_reference_samples and current.sample_count>=self.policy.min_current_samples
        limitations=[]
        if not support:limitations.append("INSUFFICIENT_SAMPLE_SUPPORT")
        if reference.status==PredictionProfileStatus.PARTIAL or current.status==PredictionProfileStatus.PARTIAL:limitations.append("INPUT_PREDICTION_PROFILE_PARTIAL")
        if not support:
            fs=tuple(empty(n,PredictionFeatureState.PARTIAL,reference.sample_count,current.sample_count,"Insufficient support for a stable or shifted conclusion.","INSUFFICIENT_SAMPLE_SUPPORT") for n in FEATURE_ORDER)
            return self._report(reference,current,PredictionReportStatus.PARTIAL,fs,limitations)
        fs=[self._labels(reference,current)]
        fs.extend(self._continuous(name,field,hist,reference,current) for name,field,hist in (
            ("TOP1_CONFIDENCE","confidence_summary","confidence_histogram"),("PREDICTION_ENTROPY","entropy_summary","entropy_histogram"),("TOP1_MARGIN","margin_summary","margin_histogram")))
        fs.append(self._rate("ABSTENTION_RATE","abstention_rate",self.policy.abstention_rate_change_threshold,reference,current))
        fs.append(self._rate("DECLARED_UNKNOWN_RATE","unknown_rate",self.policy.unknown_rate_change_threshold,reference,current))
        degraded=limitations or any(f.state in {PredictionFeatureState.PARTIAL,PredictionFeatureState.UNAVAILABLE,PredictionFeatureState.INCOMPARABLE} for f in fs)
        return self._report(reference,current,PredictionReportStatus.PARTIAL if degraded else PredictionReportStatus.COMPLETE,tuple(fs),limitations)
    def _labels(self,r,c):
        left=tuple(x[1] for x in r.label_counts);right=tuple(x[1] for x in c.label_counts)
        ms=(metric("JENSEN_SHANNON",jensen_shannon(left,right),self.policy.label_js_threshold),metric("TOTAL_VARIATION",total_variation(left,right),self.policy.label_tv_threshold))
        shifted=all(m.exceeded for m in ms);return self._feature("PREDICTED_LABEL_DISTRIBUTION",shifted,PredictionDirection.MIXED if shifted else PredictionDirection.SAME,r,c,ms,"Predicted-label frequency comparison completed.")
    def _continuous(self,name,field,histfield,r,c):
        a,b=getattr(r,field),getattr(c,field); ah,bh=getattr(r,histfield),getattr(c,histfield)
        if a is None or b is None or ah is None or bh is None:return empty(name,PredictionFeatureState.UNAVAILABLE,r.sample_count,c.sample_count,"This evidence tier does not supply the feature.","FEATURE_NOT_SUPPLIED")
        smd,constant1=standardized_mean_difference(a,b,self.policy.epsilon);quant,constant2=robust_quantile_shift(a,b,self.policy.epsilon)
        ms=(metric("STANDARDIZED_MEAN_DIFFERENCE",smd,self.policy.continuous_smd_threshold),metric("ROBUST_QUANTILE_SHIFT",quant,self.policy.continuous_quantile_threshold),
            metric("SYMMETRIC_MEDIAN_CHANGE",symmetric_median_change(a,b,self.policy.epsilon),self.policy.continuous_median_change_threshold),
            metric("JENSEN_SHANNON",jensen_shannon(ah,bh),self.policy.histogram_js_threshold),metric("TOTAL_VARIATION",total_variation(ah,bh),self.policy.histogram_tv_threshold),
            metric("HISTOGRAM_WASSERSTEIN",histogram_wasserstein(ah,bh),self.policy.histogram_wasserstein_threshold))
        return self._feature(name,constant1 or constant2 or sum(m.exceeded is True for m in ms)>=2,direction(a.median,b.median,self.policy.epsilon),r,c,ms,"Observable output distribution comparison completed.")
    def _rate(self,name,field,threshold,r,c):
        a,b=getattr(r,field),getattr(c,field)
        if a is None or b is None:return empty(name,PredictionFeatureState.UNAVAILABLE,r.sample_count,c.sample_count,"Caller did not declare and supply this flag.","CALLER_DECLARED_FLAG_UNAVAILABLE")
        ms=(metric("ABSOLUTE_RATE_CHANGE",abs(b-a),threshold),)
        reason="Caller-declared unknown/OOD output-rate comparison completed." if name=="DECLARED_UNKNOWN_RATE" else "Caller-declared abstention-rate comparison completed."
        return self._feature(name,ms[0].exceeded is True,direction(a,b,self.policy.epsilon),r,c,ms,reason)
    def _feature(self,name,shifted,direction_value,r,c,ms,reason):
        return PredictionFeatureComparison(name,PredictionFeatureState.SHIFTED if shifted else PredictionFeatureState.STABLE,direction_value,r.sample_count,c.sample_count,ms,tuple(sorted(m.name for m in ms if m.exceeded)),reason,())
    def _terminal(self,r,c,status,lim):
        state=PredictionFeatureState.INCOMPARABLE if status==PredictionReportStatus.INCOMPARABLE else PredictionFeatureState.UNAVAILABLE
        return self._report(r,c,status,tuple(empty(n,state,r.sample_count,c.sample_count,"Prediction evidence cannot be compared.",lim) for n in FEATURE_ORDER),[lim])
    def _report(self,r,c,status,fs,limitations):
        groups={s:tuple(f.name for f in fs if f.state==s) for s in PredictionFeatureState}
        core={"schema_version":1,"policy_id":self.policy.policy_id,"policy":self.policy.to_dict(),"reference_profile_id":r.profile_id,"current_profile_id":c.profile_id,
            "output_space_id":r.output_space_id if r.output_space_id==c.output_space_id else None,"status":status.value,"reference_sample_count":r.sample_count,"current_sample_count":c.sample_count,
            "feature_comparisons":[asdict(f) for f in fs],"shifted_features":groups[PredictionFeatureState.SHIFTED],"stable_features":groups[PredictionFeatureState.STABLE],
            "partial_features":groups[PredictionFeatureState.PARTIAL],"unavailable_features":groups[PredictionFeatureState.UNAVAILABLE],"incomparable_features":groups[PredictionFeatureState.INCOMPARABLE],"limitations":tuple(sorted(set(limitations)))}
        cid="prediction-comparison:sha256:"+sha256(canonical_json_bytes(core)).hexdigest()
        report=PredictionShiftReport(1,cid,self.policy.policy_id,r.profile_id,c.profile_id,core["output_space_id"],status,r.sample_count,c.sample_count,fs,core["shifted_features"],core["stable_features"],core["partial_features"],core["unavailable_features"],core["incomparable_features"],core["limitations"])
        json.dumps(report.to_dict(),allow_nan=False);return report
