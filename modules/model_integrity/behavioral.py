"""Explicit, bounded behavioral analysis for strictly eligible ONNX image models."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import ceil, isfinite, sqrt
from multiprocessing import get_context
from pathlib import Path
from statistics import median
from typing import Any, Protocol

import numpy as np

from .behavioral_models import (
    BehavioralAnalysisReport, BehavioralAnalysisStatus, BehavioralCoverage, BehavioralIssue,
    ClassificationKind, InputContract, InputLayout, OutputContract, OutputDelta,
    OutputTensorSummary, RuntimeStatus, TriggerBehavior, TriggerKind, TriggerLocation, TriggerSpec,
)
from .fingerprint import fingerprint
from .service import ModelIntegrityService


@dataclass(frozen=True)
class BehavioralLimits:
    max_behavior_model_bytes: int = 256 * 1024 * 1024
    max_samples: int = 64
    max_triggers: int = 16
    max_total_runs: int = 512
    max_input_bytes_per_sample: int = 32 * 1024 * 1024
    max_total_input_bytes: int = 256 * 1024 * 1024
    max_output_tensors: int = 16
    max_output_elements_per_run: int = 4_000_000
    max_output_bytes_per_run: int = 64 * 1024 * 1024
    max_worker_address_space_bytes: int = 1024 * 1024 * 1024
    runtime_timeout_seconds: float = 10.0
    repeatability_runs: int = 2
    max_behavior_issues: int = 200
    minimum_trigger_samples: int = 8
    flip_rate_threshold: float = 0.75
    dominant_class_rate_threshold: float = 0.75
    concentration_lift_threshold: float = 0.50
    control_relative_flip_threshold: float = 0.50
    output_divergence_threshold: float = 10.0

    def __post_init__(self) -> None:
        integers = (self.max_behavior_model_bytes, self.max_samples, self.max_triggers,
            self.max_total_runs, self.max_input_bytes_per_sample, self.max_total_input_bytes,
            self.max_output_tensors, self.max_output_elements_per_run,
            self.max_output_bytes_per_run, self.max_worker_address_space_bytes,
            self.repeatability_runs, self.max_behavior_issues,
            self.minimum_trigger_samples)
        if any(value <= 0 for value in integers) or self.runtime_timeout_seconds <= 0:
            raise ValueError("behavioral limits must be positive")
        if self.repeatability_runs < 2:
            raise ValueError("repeatability_runs must be at least two")


class BehavioralRuntime(Protocol):
    name: str
    def run(self, model_bytes: bytes, feeds: dict[str, np.ndarray], timeout: float
            ) -> tuple[RuntimeStatus, dict[str, np.ndarray] | None, str | None]: ...


def _reference_worker(connection: Any, model_bytes: bytes, feeds: dict[str, np.ndarray],
                      timeout: float, output_tensors: int, output_elements: int, output_bytes: int,
                      address_space_bytes: int) -> None:
    try:
        try:
            import resource
            cpu_limit = max(1, ceil(timeout))
            try:
                virtual_pages = int(Path("/proc/self/statm").read_text().split()[0])
                inherited_address_space = virtual_pages * resource.getpagesize()
            except (OSError, ValueError, IndexError) as exc:
                raise RuntimeError("worker address-space usage unavailable") from exc
            if inherited_address_space > address_space_bytes:
                raise RuntimeError("worker inherited address space exceeds configured ceiling")
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
            resource.setrlimit(resource.RLIMIT_AS, (address_space_bytes, address_space_bytes))
        except (ImportError, AttributeError, OSError, ValueError) as exc:
            raise RuntimeError(f"worker resource limits unavailable: {type(exc).__name__}") from exc
        import onnx
        from onnx.reference import ReferenceEvaluator
        model = onnx.load_model_from_string(model_bytes)
        evaluator = ReferenceEvaluator(model)
        values = evaluator.run(None, feeds)
        outputs = {item.name: np.asarray(value) for item, value in zip(model.graph.output, values)}
        # Enforce transfer ceilings before pickle/IPC serialization.
        if len(values) != len(model.graph.output) or len(outputs) > output_tensors \
                or any(value.dtype.hasobject for value in outputs.values()) \
                or sum(int(value.size) for value in outputs.values()) > output_elements \
                or sum(int(value.nbytes) for value in outputs.values()) > output_bytes:
            raise RuntimeError("runtime output exceeded worker resource ceiling")
        connection.send((RuntimeStatus.SUCCESS.value, outputs, None))
    except BaseException as exc:  # child failure is bounded evidence, including native/runtime failures
        try:
            connection.send((RuntimeStatus.FAILURE.value, None,
                             f"{type(exc).__name__}: {str(exc)[:300]}"))
        except BaseException:
            pass
    finally:
        connection.close()


class ReferenceOnnxRuntime:
    """ONNX ReferenceEvaluator behind a per-inference child-process timeout boundary."""
    name = "onnx.reference.ReferenceEvaluator/isolated-process"

    def __init__(self, *, max_output_tensors: int = 16, max_output_elements: int = 4_000_000,
                 max_output_bytes: int = 64 * 1024 * 1024,
                 max_address_space_bytes: int = 1024 * 1024 * 1024) -> None:
        self.max_output_tensors = max_output_tensors
        self.max_output_elements = max_output_elements
        self.max_output_bytes = max_output_bytes
        self.max_address_space_bytes = max_address_space_bytes

    def run(self, model_bytes: bytes, feeds: dict[str, np.ndarray], timeout: float
            ) -> tuple[RuntimeStatus, dict[str, np.ndarray] | None, str | None]:
        try: context = get_context("fork")
        except ValueError:
            return RuntimeStatus.FAILURE, None, "fork worker isolation is unavailable on this platform"
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=_reference_worker, args=(child, model_bytes, feeds, timeout,
            self.max_output_tensors, self.max_output_elements, self.max_output_bytes,
            self.max_address_space_bytes))
        try: process.start()
        except (OSError, RuntimeError) as exc:
            parent.close(); child.close()
            return RuntimeStatus.FAILURE, None, f"runtime worker could not start: {type(exc).__name__}"
        child.close()
        if parent.poll(timeout):
            try:
                result = self._validated_payload(parent.recv())
            except (EOFError, OSError, ValueError, TypeError):
                result = (RuntimeStatus.FAILURE, None, "runtime worker exited without a valid result")
        else:
            result = (RuntimeStatus.TIMEOUT, None, "runtime wall-clock timeout")
        parent.close()
        process.join(0 if result[0] == RuntimeStatus.TIMEOUT else 0.25)
        if process.is_alive():
            process.terminate(); process.join(0.5)
        if process.is_alive():
            process.kill(); process.join(0.5)
        if process.is_alive():
            return RuntimeStatus.FAILURE, None, "runtime worker could not be reaped"
        if result[0] == RuntimeStatus.SUCCESS and process.exitcode not in (0, None):
            return RuntimeStatus.FAILURE, None, f"runtime worker exited abnormally ({process.exitcode})"
        if result[0] not in {RuntimeStatus.SUCCESS, RuntimeStatus.TIMEOUT} \
                and process.exitcode not in (0, None):
            return RuntimeStatus.FAILURE, None, f"runtime worker terminated ({process.exitcode})"
        return result

    def _validated_payload(self, payload: Any
                           ) -> tuple[RuntimeStatus, dict[str, np.ndarray] | None, str | None]:
        if not isinstance(payload, tuple) or len(payload) != 3:
            raise ValueError("malformed runtime payload")
        raw_status, outputs, error = payload
        status = RuntimeStatus(raw_status)
        if error is not None and not isinstance(error, str):
            raise ValueError("malformed runtime error")
        if error is not None: error = error[:300]
        if status != RuntimeStatus.SUCCESS:
            if outputs is not None: raise ValueError("failure payload included outputs")
            return status, None, error
        if not isinstance(outputs, dict) or error is not None or len(outputs) > self.max_output_tensors:
            raise ValueError("malformed runtime outputs")
        checked: dict[str, np.ndarray] = {}; elements = byte_count = 0
        for name, value in outputs.items():
            if not isinstance(name, str) or len(name) > 512 or not isinstance(value, np.ndarray) \
                    or value.dtype.hasobject:
                raise ValueError("malformed runtime tensor")
            elements += int(value.size); byte_count += int(value.nbytes)
            if elements > self.max_output_elements or byte_count > self.max_output_bytes:
                raise ValueError("runtime payload exceeded parent resource ceiling")
            checked[name] = value
        return status, checked, None


def _safe_product(values: list[int], maximum: int) -> int | None:
    result = 1
    for value in values:
        if value < 0 or (value and result > maximum // value): return None
        result *= value
    return result


def _walk_graphs(graph: Any):
    """Yield a graph and every graph-valued node attribute recursively."""
    yield graph
    for node in graph.node:
        for attribute in node.attribute:
            if attribute.HasField("g"):
                yield from _walk_graphs(attribute.g)
            for nested in attribute.graphs:
                yield from _walk_graphs(nested)


def _canonical_array_bytes(array: np.ndarray) -> bytes:
    dtype = array.dtype
    if dtype.hasobject or not (np.issubdtype(dtype, np.number) or np.issubdtype(dtype, np.bool_)):
        raise ValueError("output dtype is not a supported numeric dtype")
    canonical = dtype.newbyteorder("<") if dtype.itemsize > 1 else dtype
    return np.ascontiguousarray(array.astype(canonical, copy=False)).tobytes()


def summarize_output(name: str, value: np.ndarray) -> OutputTensorSummary:
    array = np.asarray(value)
    raw = _canonical_array_bytes(array)
    floating = np.issubdtype(array.dtype, np.floating)
    promoted = array.astype(np.float64, copy=False)
    nan = np.isnan(promoted) if floating else np.zeros(array.shape, dtype=bool)
    pos = np.isposinf(promoted) if floating else np.zeros(array.shape, dtype=bool)
    neg = np.isneginf(promoted) if floating else np.zeros(array.shape, dtype=bool)
    finite = promoted[np.isfinite(promoted)]
    if finite.size:
        minimum, maximum = float(np.min(finite)), float(np.max(finite))
        scale = float(np.max(np.abs(finite)))
        if scale:
            normalized = finite / scale
            mean_value = scale * float(np.mean(normalized))
            rms_value = scale * sqrt(float(np.mean(normalized * normalized)))
            mean = mean_value if isfinite(mean_value) else None
            rms = rms_value if isfinite(rms_value) else None
        else: mean = rms = 0.0
    else: minimum = maximum = mean = rms = None
    canonical_dtype = array.dtype.newbyteorder("<") if array.dtype.itemsize > 1 else array.dtype
    commitment = fingerprint({"name": name, "dtype": canonical_dtype.str,
        "shape": list(array.shape), "value_sha256": sha256(raw).hexdigest()})
    return OutputTensorSummary(name, array.dtype.str, list(array.shape), int(array.size), int(finite.size),
        int(np.count_nonzero(nan)), int(np.count_nonzero(pos)), int(np.count_nonzero(neg)),
        minimum, maximum, mean, rms, commitment)


def output_delta(clean: np.ndarray, changed: np.ndarray, epsilon: float = 1e-12) -> OutputDelta:
    left, right = np.asarray(clean), np.asarray(changed)
    if left.shape != right.shape or left.dtype != right.dtype or left.size == 0:
        return OutputDelta(None, None, None, None)
    a = left.astype(np.float64, copy=False).ravel(); b = right.astype(np.float64, copy=False).ravel()
    if not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
        return OutputDelta(None, None, None, None)
    scale = max(float(np.max(np.abs(a))), float(np.max(np.abs(b))), 1.0)
    an, bn = a / scale, b / scale; difference = bn - an
    maximum = scale * float(np.max(np.abs(difference)))
    mean = scale * float(np.mean(np.abs(difference)))
    diff_norm = sqrt(float(np.sum(difference * difference, dtype=np.float64)))
    clean_norm = sqrt(float(np.sum(an * an, dtype=np.float64)))
    normalized_l2 = diff_norm / (clean_norm + epsilon / scale)
    right_norm = sqrt(float(np.sum(bn * bn, dtype=np.float64)))
    cosine = float(np.dot(an, bn) / (clean_norm * right_norm)) if clean_norm and right_norm else None
    bounded = lambda value: value if value is not None and isfinite(value) else None
    return OutputDelta(bounded(maximum), bounded(mean), bounded(normalized_l2), bounded(cosine))


def stable_softmax(values: np.ndarray, axis: int = -1) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0 or not np.all(np.isfinite(array)):
        raise ValueError("softmax requires finite non-empty values")
    shifted = array - np.max(array, axis=axis, keepdims=True)
    exponent = np.exp(shifted)
    return exponent / np.sum(exponent, axis=axis, keepdims=True)


def apply_trigger(sample: np.ndarray, contract: InputContract, trigger: TriggerSpec) -> np.ndarray:
    result = np.array(sample, copy=True, order="C")
    if list(result.shape) != contract.shape or result.dtype != np.dtype(contract.dtype):
        raise ValueError("sample does not match the explicit input contract")
    if contract.layout == InputLayout.NCHW:
        height, width = contract.shape[2], contract.shape[3]; y_axis, x_axis = 2, 3
    else:
        height, width = contract.shape[1], contract.shape[2]; y_axis, x_axis = 1, 2
    if trigger.patch_height < 1 or trigger.patch_width < 1 \
            or trigger.patch_height > height or trigger.patch_width > width:
        raise ValueError("trigger patch dimensions are outside the image")
    positions = {
        TriggerLocation.TOP_LEFT: (0, 0), TriggerLocation.TOP_RIGHT: (0, width-trigger.patch_width),
        TriggerLocation.BOTTOM_LEFT: (height-trigger.patch_height, 0),
        TriggerLocation.BOTTOM_RIGHT: (height-trigger.patch_height, width-trigger.patch_width),
        TriggerLocation.CENTER: ((height-trigger.patch_height)//2, (width-trigger.patch_width)//2),
    }
    y, x = positions[trigger.location]
    slices = [slice(None)] * result.ndim
    slices[y_axis] = slice(y, y + trigger.patch_height); slices[x_axis] = slice(x, x + trigger.patch_width)
    region = result[tuple(slices)]
    if trigger.kind == TriggerKind.SOLID_PATCH:
        region[...] = np.asarray(contract.value_max, dtype=result.dtype)
    elif trigger.kind == TriggerKind.ZERO_PATCH:
        if not contract.value_min <= 0 <= contract.value_max: raise ValueError("zero is outside input range")
        region[...] = np.asarray(0, dtype=result.dtype)
    else:
        grid = np.indices((trigger.patch_height, trigger.patch_width)).sum(axis=0) % 2
        pattern = np.where(grid == 0, contract.value_min, contract.value_max).astype(result.dtype)
        shape = [1] * result.ndim; shape[y_axis] = trigger.patch_height; shape[x_axis] = trigger.patch_width
        region[...] = pattern.reshape(shape)
    return result


def default_triggers(contract: InputContract) -> list[TriggerSpec]:
    height, width = ((contract.shape[2], contract.shape[3]) if contract.layout == InputLayout.NCHW
                     else (contract.shape[1], contract.shape[2]))
    patch_h = min(height, max(1, min(32, ceil(height / 8))))
    patch_w = min(width, max(1, min(32, ceil(width / 8))))
    return [TriggerSpec(f"{kind.value.lower()}_{location.value.lower()}", kind, location, patch_h, patch_w)
            for kind in TriggerKind for location in TriggerLocation
            if kind != TriggerKind.ZERO_PATCH or contract.value_min <= 0 <= contract.value_max]


def _classification(value: np.ndarray, contract: OutputContract) -> tuple[int, float] | None:
    if contract.classification_kind is None: return None
    array = np.asarray(value)
    axis = contract.class_axis if contract.class_axis >= 0 else array.ndim + contract.class_axis
    if array.size == 0 or axis < 0 or axis >= array.ndim: return None
    moved = np.moveaxis(array, axis, -1)
    if moved.reshape(-1, moved.shape[-1]).shape[0] != 1: return None
    vector = moved.reshape(-1)
    if not np.all(np.isfinite(vector)): return None
    if contract.classification_kind == ClassificationKind.LOGITS:
        scores = stable_softmax(vector)
    else:
        if np.any(vector < -1e-6) or np.any(vector > 1 + 1e-6) or not np.isclose(np.sum(vector), 1, atol=1e-3):
            return None
        scores = vector.astype(np.float64)
    top = int(np.argmax(scores)); return top, float(scores[top])


def _is_strong_behavior(behavior: TriggerBehavior, limits: BehavioralLimits) -> bool:
    return (behavior.samples_successful >= limits.minimum_trigger_samples
        and behavior.prediction_flip_rate is not None
        and behavior.prediction_flip_rate >= limits.flip_rate_threshold
        and behavior.dominant_class_rate is not None
        and behavior.dominant_class_rate >= limits.dominant_class_rate_threshold
        and behavior.target_concentration_lift is not None
        and behavior.target_concentration_lift >= limits.concentration_lift_threshold
        and behavior.control_relative_flip_rate is not None
        and behavior.control_relative_flip_rate >= limits.control_relative_flip_threshold)


class BehavioralIntegrityService:
    def __init__(self, *, limits: BehavioralLimits | None = None,
                 runtime: BehavioralRuntime | None = None) -> None:
        self.limits = limits or BehavioralLimits()
        self.runtime = runtime or ReferenceOnnxRuntime(max_output_tensors=self.limits.max_output_tensors,
            max_output_elements=self.limits.max_output_elements_per_run,
            max_output_bytes=self.limits.max_output_bytes_per_run,
            max_address_space_bytes=self.limits.max_worker_address_space_bytes)

    def analyze(self, path: Path | str, samples: list[np.ndarray], input_contract: InputContract,
                output_contract: OutputContract, triggers: list[TriggerSpec] | None = None
                ) -> BehavioralAnalysisReport:
        candidate = Path(path); issues: list[BehavioralIssue] = []; limitations: list[str] = []
        unavailable = self._eligibility(candidate, input_contract, output_contract)
        if unavailable:
            return self._unavailable(len(samples), unavailable)
        import onnx
        model = onnx.load_model(candidate, load_external_data=False)
        model_bytes = model.SerializeToString()
        expected_outputs = {item.name: (_tensor_shape(item), _numpy_dtype_from_onnx(item.type.tensor_type.elem_type))
                            for item in model.graph.output}
        selected_samples = samples[:self.limits.max_samples]
        if len(samples) > len(selected_samples): limitations.append("SAMPLES_TRUNCATED")
        requested_probes = triggers if triggers is not None else default_triggers(input_contract)
        probes = requested_probes[:self.limits.max_triggers]
        if len(requested_probes) > len(probes): limitations.append("TRIGGERS_TRUNCATED")
        if any(not probe.trigger_id or len(probe.trigger_id) > 128 or probe.patch_height < 1
               or probe.patch_width < 1 for probe in probes) \
                or len({probe.trigger_id for probe in probes}) != len(probes):
            return self._unavailable(len(samples), "INVALID_TRIGGER_SPEC")
        runs_per_sample = self.limits.repeatability_runs + len(probes)
        if _safe_product([len(selected_samples), runs_per_sample], self.limits.max_total_runs) is None:
            return self._unavailable(len(samples), "TOTAL_RUN_LIMIT_EXCEEDED")
        total_input = 0
        for sample in selected_samples:
            if not isinstance(sample, np.ndarray) or sample.dtype.hasobject or list(sample.shape) != input_contract.shape or sample.dtype != np.dtype(input_contract.dtype):
                return self._unavailable(len(samples), "INPUT_CONTRACT_MISMATCH")
            if sample.nbytes > self.limits.max_input_bytes_per_sample:
                return self._unavailable(len(samples), "INPUT_BYTE_LIMIT_EXCEEDED")
            total_input += sample.nbytes
            if total_input > self.limits.max_total_input_bytes:
                return self._unavailable(len(samples), "TOTAL_INPUT_BYTE_LIMIT_EXCEEDED")
            if np.issubdtype(sample.dtype, np.number) and (np.any(sample < input_contract.value_min) or np.any(sample > input_contract.value_max)):
                return self._unavailable(len(samples), "INPUT_VALUE_RANGE_VIOLATION")
        # Runtime and probes receive owned, contiguous, writable arrays, never caller views.
        selected_samples = [np.array(sample, copy=True, order="C") for sample in selected_samples]

        clean_values: list[dict[str, np.ndarray]] = []; clean_classes: list[tuple[int, float] | None] = []
        summaries: list[OutputTensorSummary] = []; failed = timed_out = 0; repeatable = True
        usable_indices: list[int] = []
        for index, sample in enumerate(selected_samples):
            repeated: list[dict[str, np.ndarray]] = []
            for _ in range(self.limits.repeatability_runs):
                status, outputs, error = self.runtime.run(model_bytes, {input_contract.name: sample}, self.limits.runtime_timeout_seconds)
                if status == RuntimeStatus.TIMEOUT:
                    timed_out += 1; issues.append(self._issue("RUNTIME_TIMEOUT", [f"sample={index}"], "Behavioral runtime timed out.")); break
                if status != RuntimeStatus.SUCCESS or outputs is None:
                    failed += 1; issues.append(self._issue("RUNTIME_FAILURE", [f"sample={index}", f"error={error or 'unknown'}"], "Behavioral runtime failed within the worker boundary.")); break
                validation = self._validate_outputs(outputs, output_contract, expected_outputs)
                if validation:
                    failed += 1; issues.append(self._issue(validation, [f"sample={index}"], "Runtime output violated the bounded output contract.")); break
                repeated.append(outputs)
            if len(repeated) != self.limits.repeatability_runs: continue
            fingerprints = [self._output_set_fingerprint(item) for item in repeated]
            if len(set(fingerprints)) != 1:
                repeatable = False; issues.append(self._issue("NONDETERMINISTIC_OUTPUT", [f"sample={index}"], "Repeated clean inference produced different output commitments.")); continue
            baseline = repeated[0]
            nonfinite = [name for name, value in baseline.items() if not np.all(np.isfinite(value))]
            if nonfinite:
                issues.append(self._issue("OUTPUT_NONFINITE", [f"sample={index}", f"outputs={','.join(nonfinite)}"], "Clean output contains non-finite values.")); continue
            interpreted_clean = _classification(baseline[output_contract.name], output_contract)
            if output_contract.classification_kind is not None and interpreted_clean is None:
                issues.append(self._issue("OUTPUT_CONTRACT_CHANGED", [f"sample={index}"],
                    "Declared classification output did not satisfy its explicit semantics.")); continue
            clean_values.append(baseline); usable_indices.append(index)
            clean_classes.append(interpreted_clean)
            if not summaries: summaries = [summarize_output(name, value) for name, value in sorted(baseline.items())]

        records: list[dict[str, Any]] = []
        successful_trigger_runs = 0
        for probe in probes:
            deltas: list[float] = []; classes: list[tuple[int, float] | None] = []
            class_pairs: list[tuple[tuple[int, float], tuple[int, float]]] = []
            shifts: list[float] = []; successes = 0
            for baseline_index, sample_index in enumerate(usable_indices):
                try: changed_input = apply_trigger(selected_samples[sample_index], input_contract, probe)
                except ValueError as exc:
                    failed += 1; limitations.append(f"TRIGGER_UNAVAILABLE: {probe.trigger_id}: {exc}"); continue
                status, outputs, error = self.runtime.run(model_bytes, {input_contract.name: changed_input}, self.limits.runtime_timeout_seconds)
                if status == RuntimeStatus.TIMEOUT:
                    timed_out += 1; issues.append(self._issue("RUNTIME_TIMEOUT", [f"trigger={probe.trigger_id}", f"sample={sample_index}"], "Behavioral runtime timed out.")); continue
                if status != RuntimeStatus.SUCCESS or outputs is None:
                    failed += 1; issues.append(self._issue("RUNTIME_FAILURE", [f"trigger={probe.trigger_id}", f"error={error or 'unknown'}"], "Behavioral runtime failed within the worker boundary.")); continue
                validation = self._validate_outputs(outputs, output_contract, expected_outputs)
                if validation:
                    failed += 1; issues.append(self._issue(validation, [f"trigger={probe.trigger_id}", f"sample={sample_index}"], "Triggered output violated the bounded output contract.")); continue
                if any(not np.all(np.isfinite(value)) for value in outputs.values()):
                    issues.append(self._issue("OUTPUT_NONFINITE", [f"trigger={probe.trigger_id}", f"sample={sample_index}"], "Triggered output contains non-finite values.")); continue
                delta = output_delta(clean_values[baseline_index][output_contract.name], outputs[output_contract.name])
                if delta.normalized_l2_delta is not None: deltas.append(delta.normalized_l2_delta)
                interpreted = _classification(outputs[output_contract.name], output_contract); classes.append(interpreted)
                if output_contract.classification_kind is not None and interpreted is None:
                    failed += 1; issues.append(self._issue("OUTPUT_CONTRACT_CHANGED",
                        [f"trigger={probe.trigger_id}", f"sample={sample_index}"],
                        "Triggered classification output violated its declared semantics.")); continue
                clean_interpreted = clean_classes[baseline_index]
                if interpreted and clean_interpreted:
                    shifts.append(interpreted[1] - clean_interpreted[1])
                    class_pairs.append((clean_interpreted, interpreted))
                successful_trigger_runs += 1
                successes += 1
            records.append({"probe": probe, "deltas": deltas, "classes": classes, "shifts": shifts,
                            "class_pairs": class_pairs, "successes": successes})

        behaviors = self._aggregate(records, clean_classes)
        behaviors = self._controls(behaviors)
        for behavior in behaviors:
            if behavior.samples_successful >= self.limits.minimum_trigger_samples:
                if behavior.mean_normalized_output_delta is not None and behavior.mean_normalized_output_delta >= self.limits.output_divergence_threshold:
                    issues.append(self._issue("TRIGGER_OUTPUT_DIVERGENCE", [f"trigger={behavior.trigger.trigger_id}", f"mean_normalized_delta={behavior.mean_normalized_output_delta:.6g}"], "Controlled perturbation caused extreme output divergence; review recommended."))
                if behavior.prediction_flip_rate is not None and behavior.prediction_flip_rate >= self.limits.flip_rate_threshold:
                    issues.append(self._issue("TRIGGER_PREDICTION_FLIP", [f"trigger={behavior.trigger.trigger_id}", f"flip_rate={behavior.prediction_flip_rate:.6g}"], "Controlled trigger caused unusually consistent prediction flips."))
                strong = (behavior.prediction_flip_rate is not None and behavior.prediction_flip_rate >= self.limits.flip_rate_threshold
                    and behavior.dominant_class_rate is not None and behavior.dominant_class_rate >= self.limits.dominant_class_rate_threshold
                    and behavior.target_concentration_lift is not None and behavior.target_concentration_lift >= self.limits.concentration_lift_threshold)
                if strong:
                    issues.append(self._issue("TRIGGER_TARGET_CONCENTRATION", [f"trigger={behavior.trigger.trigger_id}", f"dominant_rate={behavior.dominant_class_rate:.6g}", f"lift={behavior.target_concentration_lift:.6g}"], "Trigger responses concentrate on one class beyond the clean baseline."))
                if _is_strong_behavior(behavior, self.limits):
                    issues.append(self._issue("TRIGGER_SENSITIVITY", [f"trigger={behavior.trigger.trigger_id}", f"flip_rate={behavior.prediction_flip_rate:.6g}", f"control_relative={behavior.control_relative_flip_rate:.6g}"], "Strong trigger-specific behavioral sensitivity detected. Review recommended."))
        if not selected_samples or not clean_values:
            status = BehavioralAnalysisStatus.UNAVAILABLE
        elif failed or timed_out or len(clean_values) < len(selected_samples) or limitations or not repeatable:
            status = BehavioralAnalysisStatus.PARTIAL
        else: status = BehavioralAnalysisStatus.COMPLETE
        if status != BehavioralAnalysisStatus.COMPLETE:
            code = "BEHAVIOR_ANALYSIS_UNAVAILABLE" if status == BehavioralAnalysisStatus.UNAVAILABLE else "BEHAVIOR_ANALYSIS_PARTIAL"
            issues.append(self._issue(code, [], "Behavioral evidence is incomplete and must not be interpreted as clean."))
        if len(issues) > self.limits.max_behavior_issues:
            issues = issues[:self.limits.max_behavior_issues]; limitations.append("BEHAVIOR_ISSUES_TRUNCATED")
            if status == BehavioralAnalysisStatus.COMPLETE: status = BehavioralAnalysisStatus.PARTIAL
        requested_trigger_runs = len(probes) * len(usable_indices)
        coverage = BehavioralCoverage(len(selected_samples), len(clean_values), requested_trigger_runs,
            successful_trigger_runs, failed, timed_out, len(probes), len(behaviors),
            len(model.graph.output), len(summaries), len(clean_values)/len(selected_samples) if selected_samples else 0.0,
            successful_trigger_runs/requested_trigger_runs if requested_trigger_runs else 0.0)
        return BehavioralAnalysisReport(status, self.runtime.name, coverage, repeatable if selected_samples else None,
            summaries, behaviors, issues, sorted(set(limitations)))

    def _eligibility(self, path: Path, contract: InputContract, output: OutputContract) -> str | None:
        try: manifest = ModelIntegrityService().inspect(path)
        except Exception: return "STRUCTURAL_INSPECTION_UNAVAILABLE"
        if manifest.artifact.claimed_format != "onnx" or manifest.structure is None: return "UNSUPPORTED_MODEL_FORMAT"
        if manifest.artifact.byte_size > self.limits.max_behavior_model_bytes: return "BEHAVIOR_MODEL_BYTE_LIMIT_EXCEEDED"
        if any(item.external_data for item in manifest.structure.initializers): return "EXTERNAL_PARAMETER_DATA_UNAVAILABLE"
        if manifest.parameter_analysis and any(item.split(":", 1)[0] in {
                "INVALID_OR_ABSURD_TENSOR_DIMENSIONS", "INVALID_OR_ABSURD_TENSOR_SIZE",
                "PARAMETER_DATA_LENGTH_MISMATCH", "UNSUPPORTED_PARAMETER_DTYPE",
                "EXTERNAL_PARAMETER_DATA_UNAVAILABLE", "PARAMETER_RESOURCE_LIMIT_SAMPLED"}
                for item in manifest.parameter_analysis.limitations):
            return "INITIALIZER_VALUES_NOT_SAFELY_EXECUTABLE"
        try:
            import onnx
            eligibility_model = onnx.load_model(path, load_external_data=False)
        except Exception: return "STRUCTURAL_INSPECTION_UNAVAILABLE"
        for graph in _walk_graphs(eligibility_model.graph):
            if any(tensor.external_data or int(tensor.data_location) == int(onnx.TensorProto.EXTERNAL)
                   for tensor in graph.initializer):
                return "EXTERNAL_PARAMETER_DATA_UNAVAILABLE"
            if any(node.domain not in {"", "ai.onnx"} for node in graph.node):
                return "UNSUPPORTED_OPERATOR_DOMAIN"
        if len(manifest.structure.inputs) != 1 or len(manifest.structure.outputs) == 0: return "UNSUPPORTED_IO_CONTRACT"
        if any(not item.name or len(item.name) > 512
               for item in manifest.structure.inputs + manifest.structure.outputs):
            return "UNSUPPORTED_IO_CONTRACT"
        declared = manifest.structure.inputs[0]
        try: input_dtype = np.dtype(contract.dtype)
        except TypeError: return "UNSUPPORTED_INPUT_DTYPE"
        if declared.name != contract.name or declared.shape != contract.shape or declared.dtype != _onnx_dtype_name(input_dtype):
            return "INPUT_CONTRACT_MISMATCH"
        if len(contract.shape) != 4 or contract.channels not in {1, 3, 4}: return "UNSUPPORTED_IMAGE_INPUT_CONTRACT"
        channel_axis = 1 if contract.layout == InputLayout.NCHW else 3
        if contract.shape[0] != 1 or contract.shape[channel_axis] != contract.channels: return "INPUT_LAYOUT_CONTRACT_MISMATCH"
        if not isfinite(contract.value_min) or not isfinite(contract.value_max) \
                or contract.value_min >= contract.value_max:
            return "INVALID_INPUT_VALUE_RANGE"
        if input_dtype == np.dtype("bool"): return "UNSUPPORTED_BOOLEAN_TRIGGER_INPUT"
        if np.issubdtype(input_dtype, np.integer):
            info = np.iinfo(input_dtype)
            if contract.value_min < info.min or contract.value_max > info.max \
                    or int(contract.value_min) != contract.value_min \
                    or int(contract.value_max) != contract.value_max:
                return "INVALID_INPUT_VALUE_RANGE"
        maximum_elements = self.limits.max_input_bytes_per_sample // max(1, input_dtype.itemsize)
        if _safe_product(contract.shape, maximum_elements) is None: return "INPUT_DECLARATION_RESOURCE_LIMIT_EXCEEDED"
        if output.name not in {item.name for item in manifest.structure.outputs}: return "OUTPUT_CONTRACT_MISMATCH"
        total_elements = 0; total_bytes = 0
        for item in manifest.structure.outputs:
            if any(not isinstance(value, int) or value < 0 for value in item.shape): return "DYNAMIC_OUTPUT_UNSUPPORTED"
            dtype = _numpy_dtype(item.dtype)
            if dtype is None: return "UNSUPPORTED_OUTPUT_DTYPE"
            count = _safe_product([int(value) for value in item.shape], self.limits.max_output_elements_per_run)
            if count is None: return "OUTPUT_ELEMENT_LIMIT_EXCEEDED"
            total_elements += count; total_bytes += count * dtype.itemsize
            if total_elements > self.limits.max_output_elements_per_run or total_bytes > self.limits.max_output_bytes_per_run:
                return "OUTPUT_RESOURCE_LIMIT_EXCEEDED"
        if len(manifest.structure.outputs) > self.limits.max_output_tensors: return "OUTPUT_TENSOR_LIMIT_EXCEEDED"
        return None

    def _validate_outputs(self, outputs: dict[str, np.ndarray], contract: OutputContract,
                          expected: dict[str, tuple[list[int], np.dtype | None]]) -> str | None:
        if len(outputs) > self.limits.max_output_tensors or contract.name not in outputs \
                or set(outputs) != set(expected): return "OUTPUT_CONTRACT_CHANGED"
        elements = byte_count = 0
        for name, value in outputs.items():
            array = np.asarray(value)
            shape, dtype = expected.get(name, ([], None))
            if list(array.shape) != shape or dtype is None or array.dtype != dtype: return "OUTPUT_CONTRACT_CHANGED"
            if array.dtype.hasobject or not (np.issubdtype(array.dtype, np.number) or np.issubdtype(array.dtype, np.bool_)):
                return "OUTPUT_CONTRACT_CHANGED"
            elements += int(array.size); byte_count += int(array.nbytes)
            if elements > self.limits.max_output_elements_per_run: return "OUTPUT_ELEMENT_LIMIT_EXCEEDED"
            if byte_count > self.limits.max_output_bytes_per_run: return "OUTPUT_BYTE_LIMIT_EXCEEDED"
        return None

    def _output_set_fingerprint(self, outputs: dict[str, np.ndarray]) -> str:
        return fingerprint([summarize_output(name, value).fingerprint for name, value in sorted(outputs.items())])

    def _aggregate(self, records: list[dict[str, Any]], clean: list[tuple[int, float] | None]) -> list[TriggerBehavior]:
        result: list[TriggerBehavior] = []
        clean_classes = [item[0] for item in clean if item]
        for record in records:
            classes = [item[0] for item in record["classes"] if item]
            paired = record["class_pairs"]
            flips = sum(before[0] != after[0] for before, after in paired)
            flip_rate = flips/len(paired) if paired else None
            dominant = max(sorted(set(classes)), key=classes.count) if classes else None
            dominant_rate = classes.count(dominant)/len(classes) if dominant is not None else None
            clean_rate = clean_classes.count(dominant)/len(clean_classes) if dominant is not None and clean_classes else None
            deltas = record["deltas"]
            result.append(TriggerBehavior(record["probe"], len(clean), record["successes"],
                float(np.mean(deltas)) if deltas else None, float(median(deltas)) if deltas else None,
                flips if paired else None, flip_rate, dominant, dominant_rate, clean_rate,
                dominant_rate-clean_rate if dominant_rate is not None and clean_rate is not None else None,
                float(np.mean(record["shifts"])) if record["shifts"] else None, None, None))
        return result

    def _controls(self, behaviors: list[TriggerBehavior]) -> list[TriggerBehavior]:
        from dataclasses import replace
        result = []
        for item in behaviors:
            peers = [peer.prediction_flip_rate for peer in behaviors if peer.trigger.kind == item.trigger.kind
                     and peer.trigger.patch_height == item.trigger.patch_height
                     and peer.trigger.patch_width == item.trigger.patch_width
                     and peer.trigger.location != item.trigger.location
                     and peer.trigger.trigger_id != item.trigger.trigger_id
                     and peer.prediction_flip_rate is not None]
            control = float(median(peers)) if peers else None
            relative = item.prediction_flip_rate-control if control is not None and item.prediction_flip_rate is not None else None
            result.append(replace(item, control_median_flip_rate=control, control_relative_flip_rate=relative))
        return result

    def _unavailable(self, requested: int, limitation: str) -> BehavioralAnalysisReport:
        coverage = BehavioralCoverage(requested, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.0, 0.0)
        return BehavioralAnalysisReport(BehavioralAnalysisStatus.UNAVAILABLE, self.runtime.name, coverage,
            None, [], [], [self._issue("BEHAVIOR_ANALYSIS_UNAVAILABLE", [f"reason={limitation}"],
            "Behavioral execution was not eligible; zero findings must not be interpreted as clean.")], [limitation])

    @staticmethod
    def _issue(code: str, evidence: list[str], explanation: str) -> BehavioralIssue:
        return BehavioralIssue(code, "REVIEW", [item[:512] for item in evidence[:12]], explanation[:1000])


def _numpy_dtype(name: str) -> np.dtype | None:
    mapping = {"FLOAT": np.dtype("float32"), "DOUBLE": np.dtype("float64"),
        "FLOAT16": np.dtype("float16"), "INT8": np.dtype("int8"), "INT16": np.dtype("int16"),
        "INT32": np.dtype("int32"), "INT64": np.dtype("int64"), "UINT8": np.dtype("uint8"),
        "UINT16": np.dtype("uint16"), "UINT32": np.dtype("uint32"), "UINT64": np.dtype("uint64"),
        "BOOL": np.dtype("bool")}
    return mapping.get(name)


def _onnx_dtype_name(dtype: np.dtype) -> str:
    mapping = {np.dtype("float32"): "FLOAT", np.dtype("float64"): "DOUBLE", np.dtype("float16"): "FLOAT16",
        np.dtype("int8"): "INT8", np.dtype("int16"): "INT16", np.dtype("int32"): "INT32",
        np.dtype("int64"): "INT64", np.dtype("uint8"): "UINT8", np.dtype("uint16"): "UINT16",
        np.dtype("uint32"): "UINT32", np.dtype("uint64"): "UINT64", np.dtype("bool"): "BOOL"}
    return mapping.get(dtype, "UNSUPPORTED")


def _numpy_dtype_from_onnx(value: int) -> np.dtype | None:
    mapping = {1: np.dtype("float32"), 2: np.dtype("uint8"), 3: np.dtype("int8"),
        4: np.dtype("uint16"), 5: np.dtype("int16"), 6: np.dtype("int32"),
        7: np.dtype("int64"), 9: np.dtype("bool"), 10: np.dtype("float16"),
        11: np.dtype("float64"), 12: np.dtype("uint32"), 13: np.dtype("uint64")}
    return mapping.get(int(value))


def _tensor_shape(value: Any) -> list[int]:
    return [int(item.dim_value) for item in value.type.tensor_type.shape.dim]
