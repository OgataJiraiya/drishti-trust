"""Deterministic finite D4 aggregation helpers."""
from math import isfinite,log2
import numpy as np
from .prediction_models import PredictionSummary

def public(v):
    if not isfinite(float(v)): raise ValueError("value must be finite")
    x=round(float(v),12); return 0.0 if x==0 else x
def summary(values):
    x=np.asarray(values,dtype=np.float64); q=np.quantile(x,(.05,.25,.5,.75,.95),method="linear")
    return PredictionSummary(len(x),public(x.min()),public(x.max()),public(x.mean()),public(x.std(ddof=0)),public(q[2]),public(q[0]),public(q[1]),public(q[3]),public(q[4]))
def histogram(values,bins): return tuple(int(v) for v in np.histogram(np.asarray(values),bins=bins,range=(0.,1.))[0])
def normalized_entropy(probabilities):
    p=np.asarray(probabilities,dtype=np.float64); h=-sum(float(x)*log2(float(x)) for x in p if x>0)
    return 0.0 if len(p)==1 else public(min(1,max(0,h/log2(len(p)))))
def margin(probabilities):
    if len(probabilities)<2:return None
    ordered=np.sort(np.asarray(probabilities,dtype=np.float64)); return public(ordered[-1]-ordered[-2])
