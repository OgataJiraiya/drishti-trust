"""Bounded, non-executing analysis of embedded ONNX initializer values."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite, sqrt
from typing import Any, Iterable

import numpy as np

from .fingerprint import fingerprint
from .models import (
    AnalysisMode, AnalysisStatus, ParameterAnalysisReport, ParameterCoverage,
    ParameterIssue, TensorStatistics,
)

DEFAULT_MAX_TOTAL_PARAMETER_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_SINGLE_TENSOR_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_SAMPLE_VALUES = 65_536
DEFAULT_MAX_CHANNELS_PER_TENSOR = 4_096
DEFAULT_MAX_ISSUES_PER_TENSOR = 10
DEFAULT_MAX_TOTAL_PARAMETER_ISSUES = 200
DEFAULT_MAX_REPORTED_TENSORS = 2_000


@dataclass(frozen=True)
class ParameterAnalysisLimits:
    max_total_parameter_bytes: int = DEFAULT_MAX_TOTAL_PARAMETER_BYTES
    max_single_tensor_bytes: int = DEFAULT_MAX_SINGLE_TENSOR_BYTES
    max_sample_values: int = DEFAULT_MAX_SAMPLE_VALUES
    max_channels_per_tensor: int = DEFAULT_MAX_CHANNELS_PER_TENSOR
    max_issues_per_tensor: int = DEFAULT_MAX_ISSUES_PER_TENSOR
    max_total_parameter_issues: int = DEFAULT_MAX_TOTAL_PARAMETER_ISSUES
    max_reported_tensors: int = DEFAULT_MAX_REPORTED_TENSORS
    near_zero_threshold: float = 1e-8
    extreme_zero_fraction: float = 0.99
    tensor_outlier_robust_z: float = 8.0
    channel_outlier_robust_z: float = 8.0
    extreme_channel_ratio: float = 20.0
    energy_concentration_threshold: float = 0.95
    energy_top_k: int = 3

    def __post_init__(self) -> None:
        numeric = (self.max_total_parameter_bytes, self.max_single_tensor_bytes,
                   self.max_sample_values, self.max_channels_per_tensor,
                   self.max_issues_per_tensor, self.max_total_parameter_issues,
                   self.max_reported_tensors, self.energy_top_k)
        if any(value <= 0 for value in numeric):
            raise ValueError("parameter analysis limits must be positive")


# dtype -> (numpy dtype, typed protobuf field). String, complex and emerging packed
# formats are intentionally unsupported rather than guessed.
_DTYPES: dict[int, tuple[str, str]] = {
    1: ("<f4", "float_data"), 2: ("u1", "int32_data"),
    3: ("i1", "int32_data"), 4: ("<u2", "int32_data"),
    5: ("<i2", "int32_data"), 6: ("<i4", "int32_data"),
    7: ("<i8", "int64_data"), 9: ("?", "int32_data"),
    10: ("<f2", "int32_data"), 11: ("<f8", "double_data"),
    12: ("<u4", "uint64_data"), 13: ("<u8", "uint64_data"),
}


def _safe_count(dims: Iterable[int]) -> int | None:
    count = 1
    for raw in dims:
        dim = int(raw)
        if dim < 0 or (dim and count > (2**63 - 1) // dim):
            return None
        count *= dim
    return count


def _indices(count: int, maximum: int) -> np.ndarray:
    if count <= maximum:
        return np.arange(count, dtype=np.int64)
    # Integer arithmetic gives stable, unique, endpoint-inclusive positions.
    return (np.arange(maximum, dtype=np.int64) * (count - 1)) // (maximum - 1) if maximum > 1 else np.array([0])


def _decode(tensor: Any, limits: ParameterAnalysisLimits, remaining: int
            ) -> tuple[np.ndarray | None, int, int, AnalysisMode | None, str | None, bytes | None]:
    """Return bounded values, total count/bytes, mode, limitation and canonical bytes."""
    count = _safe_count(tensor.dims)
    spec = _DTYPES.get(int(tensor.data_type))
    if count is None:
        return None, 0, 0, None, "INVALID_OR_ABSURD_TENSOR_DIMENSIONS", None
    if spec is None:
        return None, count, 0, None, "UNSUPPORTED_PARAMETER_DTYPE", None
    dtype, field = spec
    np_dtype = np.dtype(dtype)
    expected = count * np_dtype.itemsize
    if expected > 2**63 - 1:
        return None, count, expected, None, "INVALID_OR_ABSURD_TENSOR_SIZE", None
    if tensor.external_data or int(tensor.data_location) == 1:
        return None, count, expected, None, "EXTERNAL_PARAMETER_DATA_UNAVAILABLE", None
    raw = bytes(tensor.raw_data)
    canonical: bytes | None = None
    if raw:
        if len(raw) != expected:
            return None, count, expected, None, "PARAMETER_DATA_LENGTH_MISMATCH", None
        base = np.frombuffer(raw, dtype=np_dtype, count=count)
        canonical = raw if np_dtype.byteorder in ("<", "|", "=") else base.astype(np_dtype.newbyteorder("<")).tobytes()
    else:
        source = getattr(tensor, field)
        if len(source) != count:
            return None, count, expected, None, "PARAMETER_DATA_LENGTH_MISMATCH", None
        # Allocation only occurs after both declared size and configured bounds pass.
        if expected <= limits.max_single_tensor_bytes and expected <= remaining:
            if int(tensor.data_type) == 10:
                base = np.asarray(source, dtype=np.uint16).view(np.float16)
            else:
                base = np.asarray(source, dtype=np_dtype)
            canonical = base.astype(np_dtype, copy=False).tobytes()
        else:
            idx = _indices(count, min(count, limits.max_sample_values))
            selected = [source[int(i)] for i in idx]
            if int(tensor.data_type) == 10:
                base = np.asarray(selected, dtype=np.uint16).view(np.float16)
            else:
                base = np.asarray(selected, dtype=np_dtype)
            return base, count, expected, AnalysisMode.SAMPLED, "PARAMETER_RESOURCE_LIMIT_SAMPLED", None
    full = expected <= limits.max_single_tensor_bytes and expected <= remaining
    if full:
        return base, count, expected, AnalysisMode.FULL, None, canonical
    idx = _indices(count, min(count, limits.max_sample_values))
    return base[idx].copy(), count, expected, AnalysisMode.SAMPLED, "PARAMETER_RESOURCE_LIMIT_SAMPLED", None


def _stats(name: str, dtype: str, shape: list[int], values: np.ndarray,
           total: int, mode: AnalysisMode, near_zero: float) -> TensorStatistics:
    array = values.astype(np.float64, copy=False)
    if np.issubdtype(values.dtype, np.floating):
        nan = np.isnan(array); pos = np.isposinf(array); neg = np.isneginf(array)
    else:
        nan = pos = neg = np.zeros(array.shape, dtype=bool)
    finite_values = array[np.isfinite(array)]
    finite_count = int(finite_values.size)
    if finite_count:
        absolute = np.abs(finite_values)
        if np.issubdtype(values.dtype, np.integer) or np.issubdtype(values.dtype, np.bool_):
            minimum, maximum = int(np.min(values)), int(np.max(values))
            absmax = max(abs(minimum), abs(maximum))
        else:
            minimum, maximum = float(np.min(finite_values)), float(np.max(finite_values))
            absmax = float(np.max(absolute))
        scale = float(np.max(absolute))
        if scale == 0.0:
            mean = std = rms = l1 = l2 = median = mad = 0.0
        else:
            normalized = finite_values / scale
            mean = _finite_or_none(scale * float(np.mean(normalized)))
            std = _finite_or_none(scale * float(np.std(normalized)))
            normalized_l1 = float(np.sum(np.abs(normalized), dtype=np.float64))
            normalized_squares = float(np.sum(normalized * normalized, dtype=np.float64))
            l1 = _finite_or_none(scale * normalized_l1)
            rms = _finite_or_none(scale * sqrt(normalized_squares / finite_count))
            l2 = _finite_or_none(scale * sqrt(normalized_squares))
            normalized_median = float(np.median(normalized))
            median = _finite_or_none(scale * normalized_median)
            mad = _finite_or_none(scale * float(np.median(np.abs(normalized - normalized_median))))
        zero = float(np.count_nonzero(finite_values == 0) / finite_count)
        near = float(np.count_nonzero(absolute <= near_zero) / finite_count)
    else:
        minimum = maximum = mean = std = rms = absmax = l1 = l2 = zero = near = median = mad = None
    return TensorStatistics(name, dtype, shape, mode, int(values.size), total, finite_count,
        int(np.count_nonzero(nan)), int(np.count_nonzero(pos)), int(np.count_nonzero(neg)),
        minimum, maximum, mean, std, rms, absmax, l1, l2, zero, near, median, mad)


def _finite_or_none(value: float) -> float | None:
    """Keep manifest numbers RFC-8259-compatible when a derived result is unrepresentable."""
    return value if isfinite(value) else None


def _issue(code: str, severity: str, name: str, evidence: list[str], explanation: str,
           channel: int | None = None) -> ParameterIssue:
    return ParameterIssue(code, severity, name, channel, evidence, explanation)


def _bound_issues(issues: list[ParameterIssue], limits: ParameterAnalysisLimits
                  ) -> tuple[list[ParameterIssue], list[str]]:
    severity_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    ordered = sorted(issues, key=lambda item: (severity_rank.get(item.severity_hint, 3),
        item.code, item.channel_index if item.channel_index is not None else -1,
        tuple(item.evidence)))
    retained: list[ParameterIssue] = []; counts: dict[str | None, int] = {}; truncated_tensors: set[str] = set()
    for item in ordered:
        count = counts.get(item.tensor_name, 0)
        if item.tensor_name is not None and count >= limits.max_issues_per_tensor:
            truncated_tensors.add(item.tensor_name)
            continue
        retained.append(item); counts[item.tensor_name] = count + 1
    limitations = [f"PARAMETER_ISSUES_TRUNCATED_PER_TENSOR: tensor={name}"
                   for name in sorted(truncated_tensors)]
    if len(retained) > limits.max_total_parameter_issues:
        retained = retained[:limits.max_total_parameter_issues]
        limitations.append("PARAMETER_ISSUES_TRUNCATED_TOTAL")
    retained.sort(key=lambda item: (item.tensor_name or "",
        item.channel_index if item.channel_index is not None else -1, item.code))
    return retained, limitations


def _channel_issues(name: str, values: np.ndarray, shape: list[int], limits: ParameterAnalysisLimits
                    ) -> tuple[list[ParameterIssue], str | None]:
    # ONNX matrix weights and common 1D/2D/3D convolution kernels have ranks 2..5.
    # Scalars, vectors, and higher-rank arbitrary tensors have no assumed channel meaning.
    if not 2 <= len(shape) <= 5 or shape[0] < 2 or values.size != _safe_count(shape):
        return [], None
    if shape[0] > limits.max_channels_per_tensor:
        return [], f"CHANNEL_ANALYSIS_SKIPPED_CHANNEL_LIMIT: tensor={name}"
    matrix = values.astype(np.float64, copy=False).reshape(shape[0], -1)
    if not np.all(np.isfinite(matrix)):
        return [], None
    scales = np.max(np.abs(matrix), axis=1)
    normalized = np.divide(matrix, scales[:, None], out=np.zeros_like(matrix), where=scales[:, None] != 0)
    relative_energies = np.sum(normalized * normalized, axis=1, dtype=np.float64)
    norms = scales * np.sqrt(relative_energies)
    if not np.all(np.isfinite(norms)):
        return [], f"CHANNEL_ANALYSIS_NUMERIC_RANGE_UNAVAILABLE: tensor={name}"
    median = float(np.median(norms)); mad = float(np.median(np.abs(norms - median)))
    candidates: list[tuple[float, ParameterIssue]] = []
    for index, norm in enumerate(norms):
        ratio = float(norm / median) if median > 0 else (0.0 if norm == 0 else float("inf"))
        robust = float(0.6745 * (norm - median) / mad) if mad > 0 else 0.0
        evidence = [f"tensor={name}", f"channel_index={index}", f"l2_norm={norm:.12g}",
                    f"peer_median={median:.12g}", f"robust_z={robust:.12g}", f"ratio={ratio:.12g}"]
        if norm == 0:
            candidates.append((float("inf"), _issue("DEAD_CHANNEL", "MEDIUM", name, evidence,
                "An output channel is entirely zero; pruning or architecture design may be legitimate.", index)))
        elif abs(robust) >= limits.channel_outlier_robust_z:
            candidates.append((abs(robust), _issue("CHANNEL_NORM_OUTLIER", "MEDIUM", name, evidence,
                "An output-channel norm is a robust within-tensor outlier; review is recommended.", index)))
        if ratio >= limits.extreme_channel_ratio:
            candidates.append((ratio, _issue("EXTREME_CHANNEL_SCALE", "HIGH", name, evidence,
                "One output channel has extreme scale relative to peers; this is not proof of a backdoor.", index)))
    global_scale = float(np.max(scales))
    energies = relative_energies * np.square(scales / global_scale) if global_scale else relative_energies
    total_energy = float(np.sum(energies, dtype=np.float64))
    if total_energy > 0:
        ordered = np.sort(energies)[::-1]
        top1 = float(ordered[0] / total_energy)
        topk = float(np.sum(ordered[:limits.energy_top_k]) / total_energy)
        if top1 >= limits.energy_concentration_threshold and shape[0] >= 4:
            candidates.append((top1 * 100, _issue("PARAMETER_ENERGY_CONCENTRATION", "MEDIUM", name,
                [f"tensor={name}", f"top_1_energy_fraction={top1:.12g}",
                 f"top_{limits.energy_top_k}_energy_fraction={topk:.12g}"],
                "Parameter energy is strongly concentrated in few channels; legitimate specialization is possible.")))
    candidates.sort(key=lambda item: (-item[0], item[1].code, item[1].channel_index or -1))
    return [item[1] for item in candidates], None


def analyze_parameters(model: Any, limits: ParameterAnalysisLimits | None = None
                       ) -> tuple[ParameterAnalysisReport, str | None, AnalysisStatus]:
    limits = limits or ParameterAnalysisLimits()
    tensors = sorted(model.graph.initializer, key=lambda item: item.name)
    stats: list[TensorStatistics] = []; issues: list[ParameterIssue] = []; limitations: list[str] = []
    commitments: list[dict[str, Any]] = []; used = analyzed_bytes = analyzed_elements = analyzable = 0
    analyzed_tensor_count = 0
    total_elements = total_bytes = 0
    scale_groups: dict[tuple[int, str], list[TensorStatistics]] = {}
    for tensor in tensors:
        decoded, count, byte_count, mode, limitation, canonical = _decode(tensor, limits, limits.max_total_parameter_bytes - used)
        total_elements += count; total_bytes += byte_count
        if int(tensor.data_type) in _DTYPES and not tensor.external_data and int(tensor.data_location) != 1:
            analyzable += 1
        if limitation:
            limitations.append(f"{limitation}: tensor={tensor.name}")
        if decoded is None or mode is None:
            continue
        item = _stats(tensor.name, str(int(tensor.data_type)), [int(x) for x in tensor.dims],
                      decoded, count, mode, limits.near_zero_threshold)
        analyzed_tensor_count += 1
        analyzed_elements += item.analyzed_element_count
        analyzed_bytes += item.analyzed_element_count * decoded.dtype.itemsize
        if mode == AnalysisMode.FULL:
            used += byte_count
        if len(stats) < limits.max_reported_tensors:
            stats.append(item)
        fraction_denominator = max(1, item.analyzed_element_count)
        for code, amount, severity in (("PARAMETER_NAN", item.nan_count, "HIGH"),
                ("PARAMETER_POSITIVE_INFINITY", item.positive_infinity_count, "HIGH"),
                ("PARAMETER_NEGATIVE_INFINITY", item.negative_infinity_count, "HIGH")):
            if amount:
                issues.append(_issue(code, severity, tensor.name,
                    [f"tensor={tensor.name}", f"count={amount}", f"fraction={amount/fraction_denominator:.12g}",
                     f"analysis_mode={mode}"], "Non-finite parameter values are a strong integrity anomaly, not proof of malicious modification."))
        if item.finite_count and item.zero_fraction == 1.0:
            issues.append(_issue("ALL_ZERO_TENSOR", "MEDIUM", tensor.name, [f"tensor={tensor.name}"],
                "All analyzed values are zero; pruning or architecture design may be legitimate."))
        elif item.finite_count and item.minimum == item.maximum:
            issues.append(_issue("CONSTANT_TENSOR", "LOW", tensor.name, [f"tensor={tensor.name}", f"value={item.minimum}"],
                "All analyzed finite values are constant; this can be legitimate."))
        elif item.zero_fraction is not None and item.zero_fraction >= limits.extreme_zero_fraction:
            issues.append(_issue("EXTREME_ZERO_FRACTION", "LOW", tensor.name,
                [f"tensor={tensor.name}", f"zero_fraction={item.zero_fraction:.12g}"],
                "The tensor is extremely sparse; pruning can legitimately produce this pattern."))
        if mode == AnalysisMode.FULL:
            channel_issues, channel_limitation = _channel_issues(
                tensor.name, decoded, [int(x) for x in tensor.dims], limits)
            issues.extend(channel_issues)
            if channel_limitation:
                limitations.append(channel_limitation)
        if item.rms is not None and len(tensor.dims) >= 2 and count >= 16:
            scale_groups.setdefault((len(tensor.dims), str(int(tensor.data_type))), []).append(item)
        if canonical is not None:
            commitments.append({"name": tensor.name, "dtype": int(tensor.data_type),
                "shape": [int(x) for x in tensor.dims], "value_sha256": sha256(canonical).hexdigest()})
    for peers in scale_groups.values():
        if len(peers) < 3: continue
        values = np.asarray([item.rms for item in peers], dtype=np.float64)
        median = float(np.median(values)); mad = float(np.median(np.abs(values - median)))
        if mad == 0: continue
        for item, value in zip(peers, values):
            robust = float(0.6745 * (value - median) / mad)
            if abs(robust) >= limits.tensor_outlier_robust_z:
                issues.append(_issue("TENSOR_SCALE_OUTLIER", "MEDIUM", item.tensor_name,
                    [f"tensor={item.tensor_name}", f"rms={value:.12g}", f"peer_median={median:.12g}",
                     f"robust_z={robust:.12g}"], "Tensor scale is a model-relative robust outlier among comparable tensors."))
    complete_values = len(commitments) == len(tensors)
    analyzed_tensors = analyzed_tensor_count
    if analyzed_tensors > len(stats):
        limitations.append("TENSOR_STATISTICS_TRUNCATED")
    status = (AnalysisStatus.UNAVAILABLE if not tensors or analyzed_tensors == 0 else
              AnalysisStatus.COMPLETE if complete_values and analyzed_elements == total_elements
              and not limitations else AnalysisStatus.PARTIAL)
    if status != AnalysisStatus.COMPLETE:
        code = "PARAMETER_ANALYSIS_UNAVAILABLE" if status == AnalysisStatus.UNAVAILABLE else "PARAMETER_ANALYSIS_PARTIAL"
        issues.append(_issue(code, "MEDIUM", None, [], "Parameter evidence is unavailable or incomplete; zero findings must not be interpreted as safety."))
    issues, issue_limitations = _bound_issues(issues, limits)
    limitations.extend(issue_limitations)
    if issue_limitations and status == AnalysisStatus.COMPLETE:
        status = AnalysisStatus.PARTIAL
        issues.append(_issue("PARAMETER_ANALYSIS_PARTIAL", "MEDIUM", None, [],
            "Parameter evidence is unavailable or incomplete; zero findings must not be interpreted as safety."))
        issues, repeated_limitations = _bound_issues(issues, limits)
        limitations.extend(repeated_limitations)
    coverage = ParameterCoverage(len(tensors), analyzable, analyzed_tensors, total_elements,
        analyzed_elements, total_bytes, analyzed_bytes,
        analyzed_tensors / len(tensors) if tensors else 0.0,
        analyzed_elements / total_elements if total_elements else 0.0)
    report = ParameterAnalysisReport(status, coverage,
        {"issue_count": len(issues), "reported_tensor_statistics": len(stats)}, stats, issues,
        sorted(set(limitations)))
    fp_status = AnalysisStatus.COMPLETE if complete_values and tensors else (AnalysisStatus.PARTIAL if commitments else AnalysisStatus.UNAVAILABLE)
    return report, fingerprint(commitments) if commitments else None, fp_status


def tensor_value_commitments(model: Any, limits: ParameterAnalysisLimits | None = None
                             ) -> list[dict[str, Any]]:
    """Return bounded per-tensor identities using the exact frozen M2 canonical bytes."""
    limits = limits or ParameterAnalysisLimits()
    result: list[dict[str, Any]] = []; used = 0
    for tensor in sorted(model.graph.initializer, key=lambda item: item.name):
        _values, _count, byte_count, mode, _limitation, canonical = _decode(
            tensor, limits, limits.max_total_parameter_bytes - used)
        if mode == AnalysisMode.FULL: used += byte_count
        if canonical is not None:
            result.append({"name": tensor.name, "dtype": int(tensor.data_type),
                "shape": [int(item) for item in tensor.dims],
                "value_sha256": sha256(canonical).hexdigest()})
    return result


def unavailable_parameter_report(limitation: str) -> ParameterAnalysisReport:
    """Represent absent parameter evidence explicitly instead of as an empty success."""
    coverage = ParameterCoverage(0, 0, 0, 0, 0, 0, 0, 0.0, 0.0)
    issue = _issue("PARAMETER_ANALYSIS_UNAVAILABLE", "MEDIUM", None, [],
        "Parameter evidence is unavailable; zero findings must not be interpreted as safety.")
    return ParameterAnalysisReport(AnalysisStatus.UNAVAILABLE, coverage,
        {"issue_count": 1, "reported_tensor_statistics": 0}, [], [issue], [limitation])
