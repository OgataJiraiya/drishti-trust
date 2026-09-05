"""Immutable public models for D4 caller-supplied output drift evidence."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Any
from backend.core.canonical import canonical_json_bytes

PREDICTION_SCHEMA_VERSION = 1

class PredictionRole(StrEnum): REFERENCE="REFERENCE"; CURRENT="CURRENT"
class PredictionEvidenceTier(StrEnum): LABEL_ONLY="LABEL_ONLY"; TOP1_CONFIDENCE="TOP1_CONFIDENCE"; FULL_PROBABILITIES="FULL_PROBABILITIES"
class PredictionOutputFamily(StrEnum): CLASSIFICATION="CLASSIFICATION"
class PredictionIdentityBasis(StrEnum): CALLER_DECLARED="CALLER_DECLARED"; DIGEST_DECLARED="DIGEST_DECLARED"
class PredictionProfileStatus(StrEnum): COMPLETE="COMPLETE"; PARTIAL="PARTIAL"; UNAVAILABLE="UNAVAILABLE"
class PredictionFeatureState(StrEnum): STABLE="STABLE"; SHIFTED="SHIFTED"; PARTIAL="PARTIAL"; UNAVAILABLE="UNAVAILABLE"; INCOMPARABLE="INCOMPARABLE"
class PredictionReportStatus(StrEnum): COMPLETE="COMPLETE"; PARTIAL="PARTIAL"; UNAVAILABLE="UNAVAILABLE"; INCOMPARABLE="INCOMPARABLE"
class PredictionDirection(StrEnum): INCREASED="INCREASED"; DECREASED="DECREASED"; SAME="SAME"; MIXED="MIXED"; UNKNOWN="UNKNOWN"

@dataclass(frozen=True)
class PredictionOutputSpaceDescriptor:
    evidence_tier: PredictionEvidenceTier
    class_labels: tuple[str,...]
    output_family: PredictionOutputFamily=PredictionOutputFamily.CLASSIFICATION
    probability_semantics: str="CLASS_PROBABILITIES"
    model_identity_digest: str|None=None
    output_head_name: str|None=None
    abstention_semantics: str|None=None
    unknown_semantics: str|None=None
    schema_version: int=PREDICTION_SCHEMA_VERSION
    def __post_init__(self):
        if self.schema_version!=PREDICTION_SCHEMA_VERSION: raise ValueError("unsupported descriptor schema")
        if not isinstance(self.evidence_tier,PredictionEvidenceTier): raise ValueError("invalid evidence tier")
        if not isinstance(self.output_family,PredictionOutputFamily): raise ValueError("invalid output family")
        if not self.class_labels or any(not isinstance(x,str) or not x or len(x)>256 for x in self.class_labels) or len(set(self.class_labels))!=len(self.class_labels): raise ValueError("class_labels must be unique bounded strings")
        for name in ("probability_semantics","output_head_name","abstention_semantics","unknown_semantics"):
            value=getattr(self,name)
            if value is not None and (not isinstance(value,str) or not value or len(value)>256): raise ValueError(f"{name} must be a bounded string")
        if self.model_identity_digest is not None:
            v=self.model_identity_digest
            if not (isinstance(v,str) and v.startswith("sha256:") and len(v)==71 and all(c in "0123456789abcdef" for c in v[7:])): raise ValueError("model_identity_digest must be sha256:<64 lowercase hex>")
    @property
    def identity_basis(self): return PredictionIdentityBasis.DIGEST_DECLARED if self.model_identity_digest else PredictionIdentityBasis.CALLER_DECLARED
    def semantics_dict(self):
        d=asdict(self); d["evidence_tier"]=self.evidence_tier.value; d["output_family"]=self.output_family.value; d["identity_basis"]=self.identity_basis.value; return d
    @property
    def space_id(self): return "prediction-space:sha256:"+sha256(canonical_json_bytes(self.semantics_dict())).hexdigest()
    def to_dict(self): return self.semantics_dict()

@dataclass(frozen=True)
class PredictionRecord:
    predicted_label: str
    sample_id: str|None=None
    confidence: float|None=None
    probabilities: tuple[float,...]|None=None
    abstained: bool|None=None
    unknown_or_ood: bool|None=None

@dataclass(frozen=True)
class PredictionProfileLimits:
    max_samples:int=100_000; max_classes:int=10_000; max_total_probability_values:int=10_000_000
    max_sample_id_length:int=256; histogram_bins:int=50; max_failure_examples:int=20
    probability_sum_tolerance:float=1e-6
    def __post_init__(self):
        for name in ("max_samples","max_classes","max_total_probability_values","max_sample_id_length","histogram_bins","max_failure_examples"):
            if type(getattr(self,name)) is not int or getattr(self,name)<=0: raise ValueError(f"{name} must be positive")
        if not isinstance(self.probability_sum_tolerance,(int,float)) or isinstance(self.probability_sum_tolerance,bool) or not isfinite(self.probability_sum_tolerance) or self.probability_sum_tolerance<=0: raise ValueError("probability_sum_tolerance must be finite and positive")
    def to_dict(self): return asdict(self)

@dataclass(frozen=True)
class PredictionSummary:
    count:int; minimum:float; maximum:float; mean:float; population_std:float; median:float; q05:float; q25:float; q75:float; q95:float

@dataclass(frozen=True)
class PredictionFailure:
    code:str; sample_id:str|None

@dataclass(frozen=True)
class PredictionProfile:
    schema_version:int; profile_id:str; role:PredictionRole; output_space_id:str; descriptor:PredictionOutputSpaceDescriptor
    status:PredictionProfileStatus; sample_count_supplied:int; sample_count_selected:int; sample_count:int; failure_count:int
    failure_counts:tuple[tuple[str,int],...]; failures:tuple[PredictionFailure,...]
    label_counts:tuple[tuple[str,int],...]; confidence_summary:PredictionSummary|None; confidence_histogram:tuple[int,...]|None
    entropy_summary:PredictionSummary|None; entropy_histogram:tuple[int,...]|None; margin_summary:PredictionSummary|None; margin_histogram:tuple[int,...]|None
    abstention_count:int|None; abstention_rate:float|None; unknown_count:int|None; unknown_rate:float|None
    sample_set_commitment:str; limitations:tuple[str,...]
    def to_dict(self): return asdict(self)

@dataclass(frozen=True)
class PredictionMetric:
    name:str; value:float|None; threshold:float|None; exceeded:bool|None
@dataclass(frozen=True)
class PredictionFeatureComparison:
    name:str; state:PredictionFeatureState; direction:PredictionDirection; reference_count:int; current_count:int
    metrics:tuple[PredictionMetric,...]; exceeded_metrics:tuple[str,...]; reason:str; limitations:tuple[str,...]

@dataclass(frozen=True)
class PredictionComparisonPolicy:
    min_reference_samples:int=20; min_current_samples:int=20; label_js_threshold:float=.10; label_tv_threshold:float=.25
    continuous_smd_threshold:float=.8; continuous_quantile_threshold:float=.5; continuous_median_change_threshold:float=.25
    histogram_js_threshold:float=.10; histogram_tv_threshold:float=.25; histogram_wasserstein_threshold:float=.10
    abstention_rate_change_threshold:float=.10; unknown_rate_change_threshold:float=.10; epsilon:float=1e-12
    def __post_init__(self):
        if type(self.min_reference_samples) is not int or self.min_reference_samples<=0 or type(self.min_current_samples) is not int or self.min_current_samples<=0: raise ValueError("minimum supports must be positive integers")
        for name,value in asdict(self).items():
            if name.startswith("min_"): continue
            if not isinstance(value,(int,float)) or isinstance(value,bool) or not isfinite(value) or value<=0: raise ValueError(f"{name} must be finite and positive")
        for name in ("label_js_threshold","label_tv_threshold","histogram_js_threshold","histogram_tv_threshold","histogram_wasserstein_threshold","abstention_rate_change_threshold","unknown_rate_change_threshold"):
            if getattr(self,name)>1: raise ValueError(f"{name} must be at most 1")
    def to_dict(self): return asdict(self)
    @property
    def policy_id(self): return "prediction-policy:sha256:"+sha256(canonical_json_bytes({"schema_version":1,**self.to_dict()})).hexdigest()

@dataclass(frozen=True)
class PredictionShiftReport:
    schema_version:int; comparison_id:str; policy_id:str; reference_profile_id:str; current_profile_id:str
    output_space_id:str|None; status:PredictionReportStatus; reference_sample_count:int; current_sample_count:int
    feature_comparisons:tuple[PredictionFeatureComparison,...]; shifted_features:tuple[str,...]; stable_features:tuple[str,...]
    partial_features:tuple[str,...]; unavailable_features:tuple[str,...]; incomparable_features:tuple[str,...]; limitations:tuple[str,...]
    def to_dict(self): return asdict(self)
