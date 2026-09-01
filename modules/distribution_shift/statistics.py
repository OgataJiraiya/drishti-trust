"""Small deterministic D2 effect-size and distribution-distance functions."""
from __future__ import annotations

from math import isfinite, log2, sqrt
from typing import Iterable

from .models import FeatureSummary


def _public(value: float) -> float:
    if not isfinite(value): raise ValueError("metric must be finite")
    result = round(float(value), 12)
    return 0.0 if result == 0 else result


def normalize_histogram(values: Iterable[int]) -> tuple[float, ...] | None:
    counts = tuple(values)
    if not counts or any(type(item) is not int or item < 0 for item in counts): return None
    total = sum(counts)
    if total <= 0: return None
    return tuple(item / total for item in counts)


def jensen_shannon(reference: Iterable[int], current: Iterable[int]) -> float | None:
    p, q = normalize_histogram(reference), normalize_histogram(current)
    if p is None or q is None or len(p) != len(q): return None
    midpoint = tuple((left + right) / 2 for left, right in zip(p, q, strict=True))
    def divergence(values):
        return sum(value * log2(value / middle) for value, middle in zip(values, midpoint, strict=True)
                   if value > 0)
    return _public(min(1.0, max(0.0, .5 * divergence(p) + .5 * divergence(q))))


def total_variation(reference: Iterable[int], current: Iterable[int]) -> float | None:
    p, q = normalize_histogram(reference), normalize_histogram(current)
    if p is None or q is None or len(p) != len(q): return None
    return _public(min(1.0, max(0.0, .5 * sum(abs(left-right) for left, right in zip(p, q, strict=True)))))


def histogram_wasserstein(reference: Iterable[int], current: Iterable[int]) -> float | None:
    p, q = normalize_histogram(reference), normalize_histogram(current)
    if p is None or q is None or len(p) != len(q): return None
    cumulative_p = cumulative_q = distance = 0.0
    width = 1.0 / len(p)
    for left, right in zip(p, q, strict=True):
        cumulative_p += left; cumulative_q += right
        distance += abs(cumulative_p - cumulative_q) * width
    return _public(min(1.0, max(0.0, distance)))


def standardized_mean_difference(reference: FeatureSummary, current: FeatureSummary,
                                 epsilon: float) -> tuple[float | None, bool]:
    scale = sqrt((reference.population_std ** 2 + current.population_std ** 2) / 2)
    delta = abs(current.mean-reference.mean)
    if scale <= epsilon:
        return (0.0, False) if delta <= epsilon else (None, True)
    return _public(delta / scale), False


def robust_quantile_shift(reference: FeatureSummary, current: FeatureSummary,
                          epsilon: float) -> tuple[float | None, bool]:
    reference_range = reference.q95-reference.q05; current_range = current.q95-current.q05
    scale = max(reference_range, current_range)
    delta = max(abs(getattr(current, name)-getattr(reference, name))
                for name in ("q05", "q25", "median", "q75", "q95"))
    if scale <= epsilon:
        return (0.0, False) if delta <= epsilon else (None, True)
    return _public(delta / scale), False


def symmetric_median_change(reference: FeatureSummary, current: FeatureSummary,
                            epsilon: float) -> float:
    numerator = 2 * abs(current.median-reference.median)
    denominator = abs(current.median) + abs(reference.median) + epsilon
    return _public(min(2.0, max(0.0, numerator / denominator)))

