"""Pure bounded profiling of caller-supplied prediction observations."""
from __future__ import annotations
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
import numpy as np
from backend.core.canonical import canonical_json_bytes
from .prediction_models import *
from .prediction_statistics import histogram,margin,normalized_entropy,public,summary

def _select(records,k):
    if len(records)<=k:return list(range(len(records)))
    ids=[r.sample_id if isinstance(r,PredictionRecord) else None for r in records]
    if all(isinstance(x,str) and x for x in ids) and len(set(ids))==len(ids):
        ranked=sorted(range(len(records)),key=lambda i:(sha256(ids[i].encode()).digest(),ids[i])); return sorted(ranked[:k])
    return list(np.linspace(0,len(records)-1,k,dtype=np.int64))

class PredictionProfiler:
    def __init__(self,limits:PredictionProfileLimits|None=None): self.limits=limits or PredictionProfileLimits()
    def build_reference(self,records,descriptor): return self.build(records,descriptor,PredictionRole.REFERENCE)
    def build_current(self,records,descriptor): return self.build(records,descriptor,PredictionRole.CURRENT)
    def build(self,records,descriptor,role):
        if not isinstance(descriptor,PredictionOutputSpaceDescriptor): raise TypeError("descriptor required")
        if not isinstance(role,PredictionRole): raise ValueError("role must be PredictionRole")
        if len(descriptor.class_labels)>self.limits.max_classes: raise ValueError("class vocabulary exceeds max_classes")
        supplied=tuple(records)
        if descriptor.evidence_tier==PredictionEvidenceTier.FULL_PROBABILITIES and len(supplied)*len(descriptor.class_labels)>self.limits.max_total_probability_values: raise ValueError("probability values exceed resource limit")
        indices=_select(supplied,min(len(supplied),self.limits.max_samples)); selected=[supplied[i] for i in indices]
        valid=[]; failures=[]
        for record in selected:
            code=self._validate(record,descriptor)
            if code: failures.append(PredictionFailure(code,record.sample_id if isinstance(record,PredictionRecord) and isinstance(record.sample_id,str) and len(record.sample_id)<=self.limits.max_sample_id_length else None))
            else: valid.append(record)
        # Canonical observation ordering makes complete profiles invariant to caller ordering.
        entries=[]
        for r in valid:
            observation={"sample_id":r.sample_id,"predicted_label":r.predicted_label,
                "confidence":public(r.confidence) if r.confidence is not None else None,
                "probabilities":tuple(public(x) for x in r.probabilities) if r.probabilities is not None else None,
                "abstained":r.abstained,"unknown_or_ood":r.unknown_or_ood}
            digest=sha256(canonical_json_bytes(observation)).hexdigest(); entries.append((r.sample_id or "",digest,observation,r))
        entries.sort(key=lambda x:(x[0],x[1])); valid=[x[3] for x in entries]
        commitment="sha256:"+sha256(canonical_json_bytes([x[2] for x in entries])).hexdigest()
        counts=Counter(r.predicted_label for r in valid); label_counts=tuple((label,counts[label]) for label in descriptor.class_labels)
        confidences=[]; entropies=[]; margins=[]
        for r in valid:
            if descriptor.evidence_tier==PredictionEvidenceTier.TOP1_CONFIDENCE: confidences.append(float(r.confidence))
            elif descriptor.evidence_tier==PredictionEvidenceTier.FULL_PROBABILITIES:
                p=tuple(float(x) for x in r.probabilities); confidences.append(max(p)); entropies.append(normalized_entropy(p)); m=margin(p)
                if m is not None:margins.append(m)
        abstention_count=sum(r.abstained for r in valid) if descriptor.abstention_semantics is not None and valid else None
        unknown_count=sum(r.unknown_or_ood for r in valid) if descriptor.unknown_semantics is not None and valid else None
        limitation=[]
        if len(supplied)>len(selected):limitation.append("SAMPLE_SELECTION_TRUNCATED")
        if failures:limitation.append("MALFORMED_OBSERVATIONS_EXCLUDED")
        if not valid: status=PredictionProfileStatus.UNAVAILABLE; limitation.append("NO_USABLE_OBSERVATIONS")
        elif limitation: status=PredictionProfileStatus.PARTIAL
        else: status=PredictionProfileStatus.COMPLETE
        failure_counts=tuple(sorted(Counter(f.code for f in failures).items()))
        failures_tuple=tuple(sorted(failures,key=lambda f:(f.code,f.sample_id or ""))[:self.limits.max_failure_examples])
        core={"schema_version":PREDICTION_SCHEMA_VERSION,"output_space_id":descriptor.space_id,"descriptor":descriptor.semantics_dict(),
            "status":status.value,"sample_count_supplied":len(supplied),"sample_count_selected":len(selected),"sample_count":len(valid),"failure_count":len(failures),
            "failure_counts":failure_counts,"failures":[asdict(f) for f in failures_tuple],"label_counts":label_counts,
            "confidence_summary":asdict(summary(confidences)) if confidences else None,"confidence_histogram":histogram(confidences,self.limits.histogram_bins) if confidences else None,
            "entropy_summary":asdict(summary(entropies)) if entropies else None,"entropy_histogram":histogram(entropies,self.limits.histogram_bins) if entropies else None,
            "margin_summary":asdict(summary(margins)) if margins else None,"margin_histogram":histogram(margins,self.limits.histogram_bins) if margins else None,
            "abstention_count":abstention_count,"abstention_rate":public(abstention_count/len(valid)) if abstention_count is not None else None,
            "unknown_count":unknown_count,"unknown_rate":public(unknown_count/len(valid)) if unknown_count is not None else None,
            "sample_set_commitment":commitment,"limits":self.limits.to_dict(),"limitations":tuple(sorted(set(limitation)))}
        pid="prediction-profile:sha256:"+sha256(canonical_json_bytes(core)).hexdigest()
        profile=PredictionProfile(PREDICTION_SCHEMA_VERSION,pid,role,descriptor.space_id,descriptor,status,len(supplied),len(selected),len(valid),len(failures),failure_counts,failures_tuple,label_counts,
            summary(confidences) if confidences else None,core["confidence_histogram"],summary(entropies) if entropies else None,core["entropy_histogram"],summary(margins) if margins else None,core["margin_histogram"],
            abstention_count,core["abstention_rate"],unknown_count,core["unknown_rate"],commitment,core["limitations"])
        json.dumps(profile.to_dict(),allow_nan=False); return profile

    def _validate(self,r,d):
        if not isinstance(r,PredictionRecord):return "INVALID_RECORD"
        if r.sample_id is not None and (not isinstance(r.sample_id,str) or not r.sample_id or len(r.sample_id)>self.limits.max_sample_id_length):return "INVALID_SAMPLE_ID"
        if r.predicted_label not in d.class_labels:return "INVALID_LABEL"
        if r.confidence is not None and (not isinstance(r.confidence,(int,float)) or isinstance(r.confidence,bool) or not np.isfinite(r.confidence) or not 0<=r.confidence<=1):return "INVALID_CONFIDENCE"
        if (d.abstention_semantics is None) != (r.abstained is None) or r.abstained is not None and type(r.abstained) is not bool:return "INVALID_ABSTENTION_FLAG"
        if (d.unknown_semantics is None) != (r.unknown_or_ood is None) or r.unknown_or_ood is not None and type(r.unknown_or_ood) is not bool:return "INVALID_UNKNOWN_FLAG"
        if d.evidence_tier==PredictionEvidenceTier.LABEL_ONLY:
            return None if r.confidence is None and r.probabilities is None else "EVIDENCE_TIER_MISMATCH"
        if d.evidence_tier==PredictionEvidenceTier.TOP1_CONFIDENCE:
            return None if r.confidence is not None and r.probabilities is None else "EVIDENCE_TIER_MISMATCH"
        p=r.probabilities
        if not isinstance(p,(tuple,list,np.ndarray)) or np.asarray(p).ndim!=1:return "INVALID_PROBABILITY_VECTOR"
        a=np.asarray(p)
        if a.dtype.kind not in "iuf" or len(a)!=len(d.class_labels) or not np.isfinite(a).all() or np.any(a<0) or np.any(a>1):return "INVALID_PROBABILITY_VECTOR"
        if abs(float(a.sum())-1)>self.limits.probability_sum_tolerance:return "PROBABILITY_SUM_MISMATCH"
        if r.predicted_label!=d.class_labels[int(np.argmax(a))]:return "LABEL_ARGMAX_MISMATCH"
        if r.confidence is not None and abs(float(r.confidence)-float(a.max()))>self.limits.probability_sum_tolerance:return "CONFIDENCE_PROBABILITY_MISMATCH"
        return None
