"""Model-agnostic construction of bounded D3 representation evidence."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import numpy as np
from backend.core.canonical import canonical_json_bytes
from .representation_models import (REPRESENTATION_SCHEMA_VERSION, NormalizationMode,
    RepresentationLimits, RepresentationProfile, RepresentationProfileStatus,
    RepresentationRole, RepresentationSpaceDescriptor, VarianceSummary)
from .representation_statistics import canonical_matrix, public, row_digest, summary

@dataclass(frozen=True)
class RepresentationWindowEvidence:
    """Public aggregate profile plus non-serialized, immutable selected rows."""
    profile: RepresentationProfile
    _selected_embeddings: np.ndarray

    @property
    def selected_embeddings(self) -> np.ndarray:
        view=self._selected_embeddings.view(); view.flags.writeable=False; return view


def _indices(count: int, selected: int, sample_ids: tuple[str, ...] | None) -> np.ndarray:
    if count <= selected: return np.arange(count, dtype=np.int64)
    if sample_ids is None: return np.linspace(0, count-1, selected, dtype=np.int64)
    ranked=sorted(range(count), key=lambda i: (sha256(sample_ids[i].encode()).digest(), sample_ids[i]))
    return np.asarray(sorted(ranked[:selected]), dtype=np.int64)


class RepresentationProfiler:
    def __init__(self, limits: RepresentationLimits | None = None) -> None:
        self.limits=limits or RepresentationLimits()

    def build_reference(self, embeddings, descriptor, sample_ids=None):
        return self.build(embeddings, descriptor, RepresentationRole.REFERENCE, sample_ids)

    def build_current(self, embeddings, descriptor, sample_ids=None):
        return self.build(embeddings, descriptor, RepresentationRole.CURRENT, sample_ids)

    def build(self, embeddings: np.ndarray, descriptor: RepresentationSpaceDescriptor,
              role: RepresentationRole, sample_ids=None) -> RepresentationWindowEvidence:
        if not isinstance(descriptor, RepresentationSpaceDescriptor): raise TypeError("descriptor required")
        if not isinstance(role, RepresentationRole): raise ValueError("role must be a RepresentationRole")
        if not isinstance(embeddings, np.ndarray): raise TypeError("embeddings must be an ndarray")
        if embeddings.ndim != 2: raise ValueError("embeddings must have exactly two dimensions")
        n,d=embeddings.shape
        if n <= 0 or d <= 0: raise ValueError("embeddings must have positive sample and dimension axes")
        if d > self.limits.max_dimensions: raise ValueError("embedding dimension exceeds max_dimensions")
        if d != descriptor.output_dimension: raise ValueError("descriptor output_dimension mismatch")
        if n*d > self.limits.max_total_values: raise ValueError("embeddings exceed max_total_values")
        if embeddings.dtype.kind not in "iuf": raise TypeError("embeddings must have numeric non-object dtype")
        matrix=canonical_matrix(embeddings)
        if not np.isfinite(matrix).all(): raise ValueError("embeddings must contain finite values only")
        ids=None
        if sample_ids is not None:
            ids=tuple(sample_ids)
            if len(ids) != n: raise ValueError("sample_ids length mismatch")
            if any(not isinstance(v,str) or not v or len(v)>self.limits.max_sample_id_length for v in ids):
                raise ValueError("sample_ids must be bounded non-empty strings")
            if len(set(ids)) != len(ids): raise ValueError("sample_ids must be unique")
        chosen=_indices(n,min(n,self.limits.max_samples),ids)
        selected=canonical_matrix(matrix[chosen]).copy()
        selected_ids=tuple(ids[i] for i in chosen) if ids else None
        if descriptor.normalization == NormalizationMode.L2_PER_SAMPLE:
            norms=np.linalg.norm(selected,axis=1)
            if np.any(norms <= 0): raise ValueError("zero vector cannot be L2 normalized")
            selected=canonical_matrix(selected/norms[:,None])
        # Canonical row ordering makes aggregate evidence and pairwise selection order invariant.
        entries=[((selected_ids[i] if selected_ids else None), row_digest(selected[i]), selected[i]) for i in range(len(selected))]
        entries.sort(key=lambda item: ((item[0] or ""), item[1]))
        selected=canonical_matrix(np.vstack([item[2] for item in entries])).copy(); selected.flags.writeable=False
        commitment_entries=[{"sample_id":item[0],"row_digest":item[1]} for item in entries]
        commitment="sha256:"+sha256(canonical_json_bytes(commitment_entries)).hexdigest()
        centroid=selected.mean(axis=0,dtype=np.float64)
        norms=np.linalg.norm(selected,axis=1); radial=np.linalg.norm(selected-centroid,axis=1)
        variance=selected.var(axis=0,ddof=0,dtype=np.float64)
        covariance_commitment=None; limitations=[]
        if d <= self.limits.max_covariance_dimensions:
            covariance=np.zeros((d,d),dtype=np.float64) if len(selected)==1 else np.asarray(np.cov(selected,rowvar=False,ddof=0),dtype=np.float64).reshape(d,d)
            covariance_commitment="sha256:"+sha256(canonical_matrix(covariance).tobytes()).hexdigest()
        else: limitations.append("COVARIANCE_UNAVAILABLE_DIMENSION_BOUND")
        status=(RepresentationProfileStatus.PARTIAL if limitations
                else RepresentationProfileStatus.COMPLETE)
        if n>len(selected): status=RepresentationProfileStatus.PARTIAL; limitations.append("SAMPLE_SELECTION_TRUNCATED")
        core={"schema_version":REPRESENTATION_SCHEMA_VERSION,"space_id":descriptor.space_id,
            "descriptor":descriptor.semantics_dict(),"status":status.value,"sample_count_supplied":n,
            "sample_count":len(selected),"dimension":d,"sample_set_commitment":commitment,
            "centroid":tuple(public(v) for v in centroid),"centroid_norm":public(np.linalg.norm(centroid)),
            "norm_summary":asdict(summary(norms)),"radial_summary":asdict(summary(radial)),
            "variance_diagonal":tuple(public(v) for v in variance),
            "variance_summary":asdict(VarianceSummary(public(variance.mean()),public(np.median(variance)),public(variance.max()),public(variance.sum()))),
            "covariance_commitment":covariance_commitment,"limits":self.limits.to_dict(),
            "limitations":tuple(sorted(limitations))}
        profile_id="representation-profile:sha256:"+sha256(canonical_json_bytes(core)).hexdigest()
        profile=RepresentationProfile(REPRESENTATION_SCHEMA_VERSION,profile_id,role,descriptor.space_id,
            descriptor,status,n,len(selected),d,commitment,core["centroid"],core["centroid_norm"],
            summary(norms),summary(radial),core["variance_diagonal"],VarianceSummary(**core["variance_summary"]),
            covariance_commitment,core["limitations"])
        json.dumps(profile.to_dict(),allow_nan=False)
        return RepresentationWindowEvidence(profile,selected)
