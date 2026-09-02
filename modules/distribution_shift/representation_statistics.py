"""Bounded deterministic NumPy statistics for D3."""
from __future__ import annotations
from hashlib import sha256
from math import isfinite, sqrt
import numpy as np
from .representation_models import RepresentationSummary

def public(value: float) -> float:
    if not isfinite(float(value)): raise ValueError("metric must be finite")
    result = round(float(value), 12)
    return 0.0 if result == 0 else result

def canonical_matrix(value: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(value, dtype="<f8")

def row_digest(row: np.ndarray) -> str:
    value = canonical_matrix(np.asarray(row).reshape(1, -1))
    return "sha256:" + sha256(value.tobytes(order="C")).hexdigest()

def summary(values: np.ndarray) -> RepresentationSummary:
    x = np.asarray(values, dtype=np.float64)
    q = np.quantile(x, (.05, .25, .5, .75, .95), method="linear")
    return RepresentationSummary(len(x), public(x.min()), public(x.max()), public(x.mean()),
        public(x.std(ddof=0)), public(q[2]), public(q[0]), public(q[1]), public(q[3]), public(q[4]))

def squared_distances(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    result = np.sum(left*left, axis=1)[:, None] + np.sum(right*right, axis=1)[None, :] - 2*left@right.T
    minimum = float(result.min(initial=0))
    if minimum < -1e-7: raise ArithmeticError("materially negative squared distance")
    return np.maximum(result, 0.0)

def cosine_distance(left: np.ndarray, right: np.ndarray, epsilon: float) -> tuple[float | None, bool]:
    ln, rn = float(np.linalg.norm(left)), float(np.linalg.norm(right))
    if ln <= epsilon and rn <= epsilon: return 0.0, False
    if ln <= epsilon or rn <= epsilon: return None, True
    return public(min(2.0, max(0.0, 1-float(np.dot(left, right)/(ln*rn))))), False

def normalized_centroid_shift(left, right, left_rms, right_rms, epsilon):
    delta = float(np.linalg.norm(right-left)); scale = sqrt((left_rms**2+right_rms**2)/2)
    if scale <= epsilon: return ((0.0, False) if delta <= epsilon else (None, True))
    return public(delta/scale), False

def relative_l2(left, right, epsilon):
    delta=float(np.linalg.norm(right-left)); scale=max(float(np.linalg.norm(left)), float(np.linalg.norm(right)))
    if scale <= epsilon: return (0.0, False) if delta <= epsilon else (None, True)
    return public(delta/scale), False

def resolve_bandwidth(left, right, strategy, fixed, epsilon):
    if strategy == "FIXED": return public(fixed), False
    union=np.vstack((left,right)); d=squared_distances(union,union); values=d[np.triu_indices(len(union),1)]
    median=float(np.median(values)) if len(values) else 0.0
    return (public(median), False) if median > epsilon else (public(fixed), True)

def mmd_squared(left, right, bandwidth):
    gamma=1.0/(2.0*bandwidth)
    value=float(np.exp(-gamma*squared_distances(left,left)).mean()+np.exp(-gamma*squared_distances(right,right)).mean()-2*np.exp(-gamma*squared_distances(left,right)).mean())
    if value < -1e-9: raise ArithmeticError("materially negative MMD squared")
    return public(max(0.0,value))
