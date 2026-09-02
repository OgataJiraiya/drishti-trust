"""Immutable categorical models for D5 multi-signal interpretation."""
from __future__ import annotations
from dataclasses import asdict,dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any
from backend.core.canonical import canonical_json_bytes

INTERPRETATION_SCHEMA_VERSION=1
class SignalLayer(StrEnum): IMAGE_STATISTICAL="IMAGE_STATISTICAL"; REPRESENTATION="REPRESENTATION"; PREDICTION_OUTPUT="PREDICTION_OUTPUT"
class SourcePresence(StrEnum): PROVIDED="PROVIDED"; NOT_PROVIDED="NOT_PROVIDED"
class LayerCoverage(StrEnum): COMPLETE="COMPLETE"; PARTIAL="PARTIAL"; UNAVAILABLE="UNAVAILABLE"; INCOMPARABLE="INCOMPARABLE"; NOT_PROVIDED="NOT_PROVIDED"
class LayerObservation(StrEnum): SHIFT_EVIDENCE_PRESENT="SHIFT_EVIDENCE_PRESENT"; NO_SHIFT_OBSERVED="NO_SHIFT_OBSERVED"; NOT_ASSESSABLE="NOT_ASSESSABLE"
class InterpretationStatus(StrEnum): COMPLETE="COMPLETE"; PARTIAL="PARTIAL"; UNAVAILABLE="UNAVAILABLE"
class InterpretationPattern(StrEnum):
    NO_OBSERVED_SHIFT_ALL_LAYERS="NO_OBSERVED_SHIFT_ALL_LAYERS"
    IMAGE_STATISTICAL_SHIFT_ONLY="IMAGE_STATISTICAL_SHIFT_ONLY"
    REPRESENTATION_SHIFT_ONLY="REPRESENTATION_SHIFT_ONLY"
    PREDICTION_OUTPUT_SHIFT_ONLY="PREDICTION_OUTPUT_SHIFT_ONLY"
    IMAGE_STATISTICAL_AND_REPRESENTATION_SHIFT="IMAGE_STATISTICAL_AND_REPRESENTATION_SHIFT"
    IMAGE_STATISTICAL_AND_OUTPUT_SHIFT="IMAGE_STATISTICAL_AND_OUTPUT_SHIFT"
    REPRESENTATION_AND_OUTPUT_SHIFT="REPRESENTATION_AND_OUTPUT_SHIFT"
    BROAD_MULTILAYER_SHIFT="BROAD_MULTILAYER_SHIFT"
    SHIFT_WITH_INCOMPLETE_COVERAGE="SHIFT_WITH_INCOMPLETE_COVERAGE"
    NO_SHIFT_OBSERVED_IN_ASSESSED_EVIDENCE="NO_SHIFT_OBSERVED_IN_ASSESSED_EVIDENCE"
    NO_USABLE_EVIDENCE="NO_USABLE_EVIDENCE"

@dataclass(frozen=True)
class MultiSignalInterpretationPolicy:
    rule_version:str="D5_RULES_V1"; max_feature_names_per_layer:int=128; max_analyst_checks:int=16; max_unsupported_conclusions:int=16
    def __post_init__(self):
        if not isinstance(self.rule_version,str) or not self.rule_version or len(self.rule_version)>64:raise ValueError("rule_version must be bounded")
        for name in ("max_feature_names_per_layer","max_analyst_checks","max_unsupported_conclusions"):
            if type(getattr(self,name)) is not int or getattr(self,name)<=0:raise ValueError(f"{name} must be positive")
    def to_dict(self):return asdict(self)
    @property
    def policy_id(self):return "multisignal-policy:sha256:"+sha256(canonical_json_bytes({"schema_version":1,**self.to_dict()})).hexdigest()

@dataclass(frozen=True)
class MultiSignalBundle:
    image_statistical_comparison_id:str|None; representation_comparison_id:str|None; prediction_comparison_id:str|None
    @property
    def bundle_id(self):return "multisignal-bundle:sha256:"+sha256(canonical_json_bytes(asdict(self))).hexdigest()
    def to_dict(self):return {**asdict(self),"bundle_id":self.bundle_id}

@dataclass(frozen=True)
class LayerInterpretation:
    layer:SignalLayer; source_presence:SourcePresence; source_comparison_id:str|None; source_status:str|None
    coverage:LayerCoverage; observation:LayerObservation; shifted_features:tuple[str,...]; stable_features:tuple[str,...]
    partial_features:tuple[str,...]; unavailable_features:tuple[str,...]; incomparable_features:tuple[str,...]
    summary_code:str; limitations:tuple[str,...]

@dataclass(frozen=True)
class MultiSignalInterpretationReport:
    schema_version:int; interpretation_id:str; bundle_id:str; policy_id:str; status:InterpretationStatus; pattern_code:InterpretationPattern
    layers:tuple[LayerInterpretation,...]; changed_layers:tuple[str,...]; stable_layers:tuple[str,...]; incomplete_layers:tuple[str,...]
    not_assessable_layers:tuple[str,...]; summary:str; key_observations:tuple[str,...]; analyst_checks:tuple[str,...]
    unsupported_conclusions:tuple[str,...]; limitations:tuple[str,...]
    def to_dict(self)->dict[str,Any]:return asdict(self)
