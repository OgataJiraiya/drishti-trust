from __future__ import annotations

import json
import multiprocessing
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper

from backend.schemas.evidence import Finding
from modules.model_integrity.behavioral import (
    BehavioralIntegrityService, BehavioralLimits, ReferenceOnnxRuntime, RuntimeStatus, apply_trigger,
    _is_strong_behavior, default_triggers, output_delta, stable_softmax, summarize_output,
)
from modules.model_integrity.behavioral_models import (
    BehavioralAnalysisStatus, ClassificationKind, InputContract, InputLayout,
    OutputContract, TriggerBehavior, TriggerKind, TriggerLocation, TriggerSpec,
)
from modules.model_integrity.service import ModelIntegrityService


def make_classifier(path: Path, *, trigger_sensitive: bool = False, domain: str = "") -> Path:
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 1, 8, 8])
    y = helper.make_tensor_value_info("scores", TensorProto.FLOAT, [1, 2])
    mean = helper.make_node("ReduceMean", ["input"], ["global"], axes=[2, 3], keepdims=0, domain=domain)
    initializers = []
    if not trigger_sensitive:
        nodes = [mean, helper.make_node("Neg", ["global"], ["negative"]),
                 helper.make_node("Concat", ["negative", "global"], ["scores"], axis=1)]
    else:
        starts = numpy_helper.from_array(np.asarray([0, 0, 7, 7], dtype=np.int64), "starts")
        ends = numpy_helper.from_array(np.asarray([1, 1, 8, 8], dtype=np.int64), "ends")
        axes = numpy_helper.from_array(np.asarray([0, 1, 2, 3], dtype=np.int64), "axes")
        steps = numpy_helper.from_array(np.ones(4, dtype=np.int64), "steps")
        scale = numpy_helper.from_array(np.asarray([10], dtype=np.float32), "scale")
        offset = numpy_helper.from_array(np.asarray([-5], dtype=np.float32), "offset")
        initializers = [starts, ends, axes, steps, scale, offset]
        nodes = [mean, helper.make_node("Slice", ["input", "starts", "ends", "axes", "steps"], ["patch"]),
            helper.make_node("ReduceMean", ["patch"], ["patch_mean"], axes=[2, 3], keepdims=0),
            helper.make_node("Mul", ["patch_mean", "scale"], ["scaled"]),
            helper.make_node("Add", ["scaled", "offset"], ["target"]),
            helper.make_node("Concat", ["global", "target"], ["scores"], axis=1)]
    graph = helper.make_graph(nodes, "classifier", [x], [y], initializer=initializers)
    path.write_bytes(helper.make_model(graph, opset_imports=[helper.make_opsetid(domain, 17)]).SerializeToString())
    return path


@pytest.fixture
def contracts():
    return (InputContract("input", "float32", [1, 1, 8, 8], InputLayout.NCHW, 1, 0.0, 1.0),
            OutputContract("scores", ClassificationKind.LOGITS, -1))


@pytest.fixture
def corpus():
    return [np.full((1, 1, 8, 8), 0.2 + index * .01, dtype=np.float32) for index in range(8)]


class FakeRuntime:
    name = "fake-test-runtime"
    def __init__(self, function): self.function = function; self.calls = 0
    def run(self, model_bytes, feeds, timeout):
        self.calls += 1
        return self.function(self.calls, next(iter(feeds.values())))


def good_output(sample):
    value = float(np.mean(sample))
    return {"scores": np.asarray([[-value, value]], dtype=np.float32)}


def test_inspect_remains_nonexecuting(tmp_path, monkeypatch):
    path = make_classifier(tmp_path / "m.onnx")
    monkeypatch.setattr("modules.model_integrity.behavioral.ReferenceOnnxRuntime.run",
                        lambda *args: (_ for _ in ()).throw(AssertionError("inference called")))
    assert ModelIntegrityService().inspect(path).structure is not None


def test_inspect_cli_and_service_never_construct_runtime(tmp_path, monkeypatch):
    path = make_classifier(tmp_path / "m.onnx")
    monkeypatch.setattr("modules.model_integrity.behavioral.ReferenceOnnxRuntime.__init__",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runtime created")))
    service = ModelIntegrityService()
    assert service.inspect(path).structure is not None
    from modules.model_integrity import cli
    monkeypatch.setattr("sys.argv", ["model-integrity", "inspect", str(path), "--json"])
    assert cli.main() == 0


def test_behavior_requires_explicit_call(tmp_path, contracts):
    runtime = FakeRuntime(lambda count, sample: (RuntimeStatus.SUCCESS, good_output(sample), None))
    path = make_classifier(tmp_path / "m.onnx")
    ModelIntegrityService().inspect(path)
    assert runtime.calls == 0
    BehavioralIntegrityService(runtime=runtime).analyze(path, [np.zeros((1,1,8,8), np.float32)], *contracts, triggers=[])
    assert runtime.calls == 2


@pytest.mark.parametrize("suffix", [".pt", ".bin"])
def test_unsupported_model_cannot_execute(tmp_path, contracts, suffix):
    path = tmp_path / f"m{suffix}"; path.write_bytes(b"opaque")
    runtime = FakeRuntime(None)
    report = BehavioralIntegrityService(runtime=runtime).analyze(path, [], *contracts)
    assert report.status == BehavioralAnalysisStatus.UNAVAILABLE and runtime.calls == 0
    assert report.issues[0].code == "BEHAVIOR_ANALYSIS_UNAVAILABLE"


def test_custom_domain_rejected(tmp_path, contracts):
    runtime = FakeRuntime(None); path = make_classifier(tmp_path / "m.onnx", domain="vendor.custom")
    report = BehavioralIntegrityService(runtime=runtime).analyze(path, [], *contracts)
    assert report.status == BehavioralAnalysisStatus.UNAVAILABLE and runtime.calls == 0
    assert "UNSUPPORTED_OPERATOR_DOMAIN" in report.limitations


def test_external_data_rejected(tmp_path, contracts):
    path = make_classifier(tmp_path / "m.onnx")
    model = onnx.load(path); tensor = numpy_helper.from_array(np.asarray([1], np.float32), "external")
    tensor.ClearField("raw_data"); tensor.data_location = TensorProto.EXTERNAL
    entry = tensor.external_data.add(); entry.key = "location"; entry.value = "never.bin"
    model.graph.initializer.append(tensor); path.write_bytes(model.SerializeToString())
    runtime = FakeRuntime(None); report = BehavioralIntegrityService(runtime=runtime).analyze(path, [], *contracts)
    assert report.status == BehavioralAnalysisStatus.UNAVAILABLE and runtime.calls == 0


@pytest.mark.parametrize(("status", "code"), [(RuntimeStatus.TIMEOUT, "RUNTIME_TIMEOUT"),
    (RuntimeStatus.FAILURE, "RUNTIME_FAILURE")])
def test_runtime_timeout_and_failure_bounded(tmp_path, contracts, status, code):
    path = make_classifier(tmp_path / "m.onnx")
    runtime = FakeRuntime(lambda count, sample: (status, None, "controlled"))
    report = BehavioralIntegrityService(runtime=runtime).analyze(path, [np.zeros((1,1,8,8), np.float32)], *contracts, triggers=[])
    assert report.status == BehavioralAnalysisStatus.UNAVAILABLE
    assert code in {item.code for item in report.issues}


def test_clean_baseline_and_repeatability_stable(tmp_path, contracts):
    path = make_classifier(tmp_path / "m.onnx")
    runtime = FakeRuntime(lambda count, sample: (RuntimeStatus.SUCCESS, good_output(sample), None))
    report = BehavioralIntegrityService(runtime=runtime).analyze(path, [np.zeros((1,1,8,8), np.float32)], *contracts, triggers=[])
    assert report.status == BehavioralAnalysisStatus.COMPLETE
    assert report.repeatability_stable and report.coverage.clean_sample_coverage == 1


def test_repeatability_mismatch_detected(tmp_path, contracts):
    path = make_classifier(tmp_path / "m.onnx")
    runtime = FakeRuntime(lambda count, sample: (RuntimeStatus.SUCCESS,
        {"scores": np.asarray([[0, count]], dtype=np.float32)}, None))
    report = BehavioralIntegrityService(runtime=runtime).analyze(path, [np.zeros((1,1,8,8), np.float32)], *contracts, triggers=[])
    assert "NONDETERMINISTIC_OUTPUT" in {item.code for item in report.issues}
    assert report.status == BehavioralAnalysisStatus.UNAVAILABLE


@pytest.mark.parametrize(("output", "code"), [
    ({"scores": np.asarray([[np.nan, np.inf]], np.float32)}, "OUTPUT_NONFINITE"),
    ({"scores": np.ones((1, 3), np.float32)}, "OUTPUT_CONTRACT_CHANGED"),
    ({"scores": np.ones((1, 2), np.float64)}, "OUTPUT_CONTRACT_CHANGED"),
    ({"unexpected": np.ones((1, 2), np.float32)}, "OUTPUT_CONTRACT_CHANGED"),
])
def test_hostile_outputs_bounded(tmp_path, contracts, output, code):
    path = make_classifier(tmp_path / "m.onnx")
    runtime = FakeRuntime(lambda count, sample: (RuntimeStatus.SUCCESS, output, None))
    report = BehavioralIntegrityService(runtime=runtime).analyze(path, [np.zeros((1,1,8,8), np.float32)], *contracts, triggers=[])
    assert code in {item.code for item in report.issues}


def test_output_fingerprint_and_summary_nonfinite_json():
    one = summarize_output("x", np.asarray([1, np.nan, np.inf, -np.inf], np.float32))
    same = summarize_output("x", np.asarray([1, np.nan, np.inf, -np.inf], np.float32))
    changed = summarize_output("x", np.asarray([2, np.nan, np.inf, -np.inf], np.float32))
    assert one.fingerprint == same.fingerprint != changed.fingerprint
    assert (one.nan_count, one.positive_infinity_count, one.negative_infinity_count) == (1,1,1)
    json.dumps(one.__dict__, allow_nan=False)


def test_generic_output_deltas():
    delta = output_delta(np.asarray([1., 2.]), np.asarray([2., 4.]))
    assert delta.maximum_absolute_difference == pytest.approx(2)
    assert delta.mean_absolute_difference == pytest.approx(1.5)
    assert delta.normalized_l2_delta == pytest.approx(1)
    assert delta.cosine_similarity == pytest.approx(1)
    zero = output_delta(np.zeros(2), np.ones(2))
    assert np.isfinite(zero.normalized_l2_delta) and zero.cosine_similarity is None
    assert output_delta(np.asarray([]), np.asarray([])).maximum_absolute_difference is None


def test_stable_softmax_large_logits_and_classification(tmp_path, contracts):
    scores = stable_softmax(np.asarray([10000., 10001.]))
    assert np.all(np.isfinite(scores)) and scores.sum() == pytest.approx(1)
    assert int(np.argmax(scores)) == 1


@pytest.mark.parametrize("layout", [InputLayout.NCHW, InputLayout.NHWC])
@pytest.mark.parametrize("kind", list(TriggerKind))
def test_trigger_placement_range_dtype_and_determinism(layout, kind):
    shape = [1, 1, 8, 8] if layout == InputLayout.NCHW else [1, 8, 8, 1]
    contract = InputContract("input", "float32", shape, layout, 1, -1, 2)
    sample = np.full(shape, .5, np.float32)
    trigger = TriggerSpec("probe", kind, TriggerLocation.BOTTOM_RIGHT, 2, 2)
    first = apply_trigger(sample, contract, trigger); second = apply_trigger(sample, contract, trigger)
    assert np.array_equal(first, second) and first.dtype == sample.dtype
    assert first.min() >= -1 and first.max() <= 2 and np.array_equal(sample, np.full(shape, .5, np.float32))
    region = first[:, :, -2:, -2:] if layout == InputLayout.NCHW else first[:, -2:, -2:, :]
    assert not np.all(region == .5)


def test_ambiguous_layout_and_wrong_inputs_unavailable(tmp_path):
    path = make_classifier(tmp_path / "m.onnx")
    output = OutputContract("scores", ClassificationKind.LOGITS)
    ambiguous = InputContract("input", "float32", [1, 1, 8, 8], InputLayout.NHWC, 1, 0, 1)
    report = BehavioralIntegrityService(runtime=FakeRuntime(None)).analyze(path, [], ambiguous, output)
    assert report.status == BehavioralAnalysisStatus.UNAVAILABLE
    valid = InputContract("input", "float32", [1, 1, 8, 8], InputLayout.NCHW, 1, 0, 1)
    wrong = np.zeros((1,1,7,8), np.float32)
    assert BehavioralIntegrityService(runtime=FakeRuntime(None)).analyze(path, [wrong], valid, output).status == BehavioralAnalysisStatus.UNAVAILABLE


def test_sample_trigger_run_and_input_bounds(tmp_path, contracts, corpus):
    path = make_classifier(tmp_path / "m.onnx")
    runtime = FakeRuntime(lambda count, sample: (RuntimeStatus.SUCCESS, good_output(sample), None))
    limits = BehavioralLimits(max_samples=2, max_triggers=1, max_total_runs=10)
    report = BehavioralIntegrityService(limits=limits, runtime=runtime).analyze(path, corpus, *contracts)
    assert report.coverage.requested_samples == 2 and report.coverage.triggers_requested == 1
    assert {"SAMPLES_TRUNCATED", "TRIGGERS_TRUNCATED"} <= set(report.limitations)
    tight = BehavioralLimits(max_total_runs=2)
    assert BehavioralIntegrityService(limits=tight, runtime=runtime).analyze(path, corpus[:1], *contracts).status == BehavioralAnalysisStatus.UNAVAILABLE
    byte_tight = BehavioralLimits(max_input_bytes_per_sample=1)
    assert BehavioralIntegrityService(limits=byte_tight, runtime=runtime).analyze(path, corpus[:1], *contracts).status == BehavioralAnalysisStatus.UNAVAILABLE


@pytest.mark.parametrize(("limit_name", "value", "reason"), [
    ("max_output_tensors", 0, "positive"),
    ("max_output_elements_per_run", 1, "OUTPUT_ELEMENT_LIMIT_EXCEEDED"),
    ("max_output_bytes_per_run", 1, "OUTPUT_RESOURCE_LIMIT_EXCEEDED"),
])
def test_output_resource_contract_limits(tmp_path, contracts, limit_name, value, reason):
    if value == 0:
        with pytest.raises(ValueError): BehavioralLimits(**{limit_name: value})
        return
    path = make_classifier(tmp_path / "m.onnx")
    report = BehavioralIntegrityService(limits=BehavioralLimits(**{limit_name: value}), runtime=FakeRuntime(None)).analyze(path, [], *contracts)
    assert report.status == BehavioralAnalysisStatus.UNAVAILABLE and reason in report.limitations


def _solid_controls(contract):
    return [item for item in default_triggers(contract) if item.kind == TriggerKind.SOLID_PATCH]


def test_clean_and_trigger_sensitive_real_models(tmp_path, contracts, corpus):
    clean_path = make_classifier(tmp_path / "clean.onnx")
    trigger_path = make_classifier(tmp_path / "trigger.onnx", trigger_sensitive=True)
    triggers = _solid_controls(contracts[0])
    clean = BehavioralIntegrityService().analyze(clean_path, corpus, *contracts, triggers=triggers)
    sensitive = BehavioralIntegrityService().analyze(trigger_path, corpus, *contracts, triggers=triggers)
    assert clean.status == BehavioralAnalysisStatus.COMPLETE and clean.repeatability_stable
    assert "TRIGGER_SENSITIVITY" not in {item.code for item in clean.issues}
    assert sensitive.status == BehavioralAnalysisStatus.COMPLETE and sensitive.repeatability_stable
    assert "TRIGGER_SENSITIVITY" in {item.code for item in sensitive.issues}
    bottom = next(item for item in sensitive.triggers if item.trigger.location == TriggerLocation.BOTTOM_RIGHT)
    assert bottom.prediction_flip_rate == 1 and bottom.dominant_class_rate == 1
    assert bottom.control_relative_flip_rate == 1
    json.dumps(sensitive.to_dict(), allow_nan=False)


def test_single_flip_and_insufficient_support_not_strong(tmp_path, contracts):
    path = make_classifier(tmp_path / "m.onnx")
    runtime = FakeRuntime(lambda count, sample: (RuntimeStatus.SUCCESS, good_output(sample), None))
    sample = np.zeros((1,1,8,8), np.float32)
    report = BehavioralIntegrityService(runtime=runtime).analyze(path, [sample], *contracts,
        triggers=_solid_controls(contracts[0])[:1])
    assert "TRIGGER_SENSITIVITY" not in {item.code for item in report.issues}


def test_issue_bounding_and_partial_coverage(tmp_path, contracts):
    path = make_classifier(tmp_path / "m.onnx")
    runtime = FakeRuntime(lambda count, sample: (RuntimeStatus.FAILURE, None, "x"))
    report = BehavioralIntegrityService(limits=BehavioralLimits(max_behavior_issues=1), runtime=runtime).analyze(
        path, [np.zeros((1,1,8,8), np.float32) for _ in range(3)], *contracts, triggers=[])
    assert len(report.issues) == 1 and "BEHAVIOR_ISSUES_TRUNCATED" in report.limitations
    assert report.status == BehavioralAnalysisStatus.UNAVAILABLE


def test_partial_coverage_when_one_clean_sample_fails(tmp_path, contracts):
    path = make_classifier(tmp_path / "m.onnx")
    # Two repeatability calls succeed for sample one; sample two then fails.
    runtime = FakeRuntime(lambda count, sample: (RuntimeStatus.SUCCESS, good_output(sample), None)
                          if count <= 2 else (RuntimeStatus.FAILURE, None, "controlled"))
    samples = [np.zeros((1,1,8,8), np.float32), np.ones((1,1,8,8), np.float32)]
    report = BehavioralIntegrityService(runtime=runtime).analyze(path, samples, *contracts, triggers=[])
    assert report.status == BehavioralAnalysisStatus.PARTIAL
    assert report.coverage.clean_sample_coverage == .5


def test_invalid_probability_contract_is_unusable(tmp_path, contracts):
    path = make_classifier(tmp_path / "m.onnx")
    output = OutputContract("scores", ClassificationKind.PROBABILITIES, -1)
    runtime = FakeRuntime(lambda count, sample: (RuntimeStatus.SUCCESS,
        {"scores": np.asarray([[2, -1]], np.float32)}, None))
    report = BehavioralIntegrityService(runtime=runtime).analyze(path, [np.zeros((1,1,8,8), np.float32)], contracts[0], output, triggers=[])
    assert report.status == BehavioralAnalysisStatus.UNAVAILABLE
    assert "OUTPUT_CONTRACT_CHANGED" in {item.code for item in report.issues}


def test_huge_static_input_declaration_rejected_before_sample_allocation(tmp_path):
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 1, 2**30, 2**30])
    y = helper.make_tensor_value_info("scores", TensorProto.FLOAT, [1, 2])
    graph = helper.make_graph([helper.make_node("Identity", ["input"], ["scores"])], "huge", [x], [y])
    path = tmp_path / "huge.onnx"; path.write_bytes(helper.make_model(graph).SerializeToString())
    contract = InputContract("input", "float32", [1,1,2**30,2**30], InputLayout.NCHW, 1, 0, 1)
    report = BehavioralIntegrityService(runtime=FakeRuntime(None)).analyze(
        path, [], contract, OutputContract("scores"), triggers=[])
    assert report.status == BehavioralAnalysisStatus.UNAVAILABLE
    assert "INPUT_DECLARATION_RESOURCE_LIMIT_EXCEEDED" in report.limitations


def test_frozen_m1_m2_fingerprints_unchanged(tmp_path):
    path = make_classifier(tmp_path / "m.onnx", trigger_sensitive=True)
    before = ModelIntegrityService().inspect(path)
    after = ModelIntegrityService().inspect(path)
    assert before.artifact.sha256 == after.artifact.sha256
    assert before.fingerprints.structural_sha256 == after.fingerprints.structural_sha256
    assert before.fingerprints.parameter_metadata_sha256 == after.fingerprints.parameter_metadata_sha256
    assert before.fingerprints.parameter_value_sha256 == after.fingerprints.parameter_value_sha256


def test_frozen_finding_schema_still_unchanged():
    assert list(Finding.model_fields) == ["finding_id", "module", "asset_type", "asset_id",
        "category", "severity", "confidence", "reason", "evidence", "recommendation", "limitations"]


def _hanging_worker(*args):
    time.sleep(10)


def _empty_worker(connection, *args):
    connection.close()


def _crashing_worker(*args):
    raise SystemExit(9)


@pytest.mark.parametrize("worker", [_empty_worker, _crashing_worker])
def test_worker_exit_without_payload_is_bounded(monkeypatch, worker):
    monkeypatch.setattr("modules.model_integrity.behavioral._reference_worker", worker)
    status, outputs, error = ReferenceOnnxRuntime().run(b"x", {}, .2)
    assert status == RuntimeStatus.FAILURE and outputs is None and error


def test_twenty_timeouts_are_terminated_reaped_and_parent_remains_healthy(monkeypatch):
    monkeypatch.setattr("modules.model_integrity.behavioral._reference_worker", _hanging_worker)
    runtime = ReferenceOnnxRuntime()
    before = {child.pid for child in multiprocessing.active_children()}
    for _ in range(20):
        status, outputs, error = runtime.run(b"x", {}, .01)
        assert (status, outputs) == (RuntimeStatus.TIMEOUT, None) and error
    assert {child.pid for child in multiprocessing.active_children()} == before


@pytest.mark.parametrize("payload", [None, (), ("SUCCESS", {}, None, "extra"),
    ("SUCCESS", [], None), ("FAILURE", {}, "bad"), ("SUCCESS", {1: np.zeros(1)}, None)])
def test_malformed_ipc_payload_rejected(payload):
    with pytest.raises((ValueError, TypeError)):
        ReferenceOnnxRuntime()._validated_payload(payload)


def test_parent_ipc_output_limits_revalidated():
    runtime = ReferenceOnnxRuntime(max_output_tensors=1, max_output_elements=1, max_output_bytes=4)
    with pytest.raises(ValueError):
        runtime._validated_payload(("SUCCESS", {"x": np.zeros(2, np.float32)}, None))


def test_nested_custom_domain_is_ineligible(tmp_path, contracts):
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 1, 8, 8])
    y = helper.make_tensor_value_info("scores", TensorProto.FLOAT, [1, 2])
    branch_out = helper.make_tensor_value_info("branch_out", TensorProto.FLOAT, [1, 2])
    custom = helper.make_graph([helper.make_node("Hidden", ["input"], ["branch_out"], domain="evil")],
                               "custom", [], [branch_out])
    safe = helper.make_graph([helper.make_node("Identity", ["input"], ["branch_out"])],
                             "safe", [], [branch_out])
    condition = numpy_helper.from_array(np.asarray(True), "condition")
    graph = helper.make_graph([helper.make_node("If", ["condition"], ["scores"],
                                                then_branch=custom, else_branch=safe)],
                              "nested", [x], [y], [condition])
    path = tmp_path / "nested.onnx"
    path.write_bytes(helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17),
                     helper.make_opsetid("evil", 1)]).SerializeToString())
    report = BehavioralIntegrityService(runtime=FakeRuntime(None)).analyze(path, [], *contracts)
    assert report.status == BehavioralAnalysisStatus.UNAVAILABLE
    assert "UNSUPPORTED_OPERATOR_DOMAIN" in report.limitations


def test_multiple_required_inputs_and_static_batch_rejected(tmp_path, contracts):
    path = make_classifier(tmp_path / "multi.onnx")
    model = onnx.load(path)
    model.graph.input.append(helper.make_tensor_value_info("aux", TensorProto.FLOAT, [1]))
    path.write_bytes(model.SerializeToString())
    report = BehavioralIntegrityService(runtime=FakeRuntime(None)).analyze(path, [], *contracts)
    assert "UNSUPPORTED_IO_CONTRACT" in report.limitations
    batch = InputContract("input", "float32", [8, 1, 8, 8], InputLayout.NCHW, 1, 0, 1)
    path = make_classifier(tmp_path / "batch.onnx")
    model = onnx.load(path); model.graph.input[0].type.tensor_type.shape.dim[0].dim_value = 8
    path.write_bytes(model.SerializeToString())
    report = BehavioralIntegrityService(runtime=FakeRuntime(None)).analyze(path, [], batch, contracts[1])
    assert "INPUT_LAYOUT_CONTRACT_MISMATCH" in report.limitations


@pytest.mark.parametrize("transform", [lambda x: x[..., ::-1], lambda x: x.transpose(0, 1, 3, 2)])
def test_noncontiguous_and_negative_stride_inputs_are_copied(tmp_path, contracts, transform):
    path = make_classifier(tmp_path / "m.onnx")
    base = np.arange(64, dtype=np.float32).reshape(1, 1, 8, 8) / 64
    sample = transform(base); sample.setflags(write=False)
    seen = []
    def capture(count, value):
        seen.append(value)
        assert value.flags.c_contiguous and value.flags.writeable and not np.shares_memory(value, sample)
        return RuntimeStatus.SUCCESS, good_output(value), None
    report = BehavioralIntegrityService(runtime=FakeRuntime(capture)).analyze(path, [sample], *contracts, triggers=[])
    assert report.status == BehavioralAnalysisStatus.COMPLETE and np.array_equal(base, np.arange(64, dtype=np.float32).reshape(1,1,8,8)/64)


@pytest.mark.parametrize("dtype", [np.float64, np.int32, np.uint8])
def test_input_dtype_mismatch_is_not_silently_converted(tmp_path, contracts, dtype):
    path = make_classifier(tmp_path / "m.onnx")
    report = BehavioralIntegrityService(runtime=FakeRuntime(None)).analyze(
        path, [np.zeros((1,1,8,8), dtype=dtype)], *contracts, triggers=[])
    assert report.status == BehavioralAnalysisStatus.UNAVAILABLE
    assert "INPUT_CONTRACT_MISMATCH" in report.limitations


def test_bool_contract_is_explicitly_unavailable(tmp_path):
    x = helper.make_tensor_value_info("input", TensorProto.BOOL, [1, 1, 2, 2])
    y = helper.make_tensor_value_info("scores", TensorProto.BOOL, [1, 1, 2, 2])
    path = tmp_path / "bool.onnx"
    path.write_bytes(helper.make_model(helper.make_graph([helper.make_node("Identity", ["input"], ["scores"])],
                     "bool", [x], [y])).SerializeToString())
    contract = InputContract("input", "bool", [1,1,2,2], InputLayout.NCHW, 1, 0, 1)
    report = BehavioralIntegrityService(runtime=FakeRuntime(None)).analyze(path, [], contract, OutputContract("scores"))
    assert "UNSUPPORTED_BOOLEAN_TRIGGER_INPUT" in report.limitations


@pytest.mark.parametrize("height,width,patch_h,patch_w", [(1,1,1,1), (3,5,1,2), (3,5,3,5)])
def test_trigger_spatial_edges(height, width, patch_h, patch_w):
    contract = InputContract("x", "uint8", [1, 3, height, width], InputLayout.NCHW, 3, 1, 9)
    original = np.full(contract.shape, 4, np.uint8)
    outputs = [apply_trigger(original, contract, TriggerSpec(str(location), TriggerKind.SOLID_PATCH,
               location, patch_h, patch_w)) for location in TriggerLocation]
    assert np.all(original == 4)
    assert all(np.count_nonzero(item != original) == 3 * patch_h * patch_w for item in outputs)
    assert all(not np.shares_memory(item, original) for item in outputs)


def test_invalid_patch_dimensions_and_zero_range_rejected():
    contract = InputContract("x", "uint8", [1,1,2,2], InputLayout.NCHW, 1, 1, 4)
    sample = np.ones(contract.shape, np.uint8)
    with pytest.raises(ValueError): apply_trigger(sample, contract,
        TriggerSpec("bad", TriggerKind.SOLID_PATCH, TriggerLocation.CENTER, 3, 1))
    with pytest.raises(ValueError): apply_trigger(sample, contract,
        TriggerSpec("zero", TriggerKind.ZERO_PATCH, TriggerLocation.CENTER, 1, 1))


def test_fingerprint_preserves_shape_dtype_signed_zero_and_nan_payload():
    positive = summarize_output("x", np.asarray([0.0], np.float32)).fingerprint
    negative = summarize_output("x", np.asarray([-0.0], np.float32)).fingerprint
    shaped = summarize_output("x", np.asarray([[0.0]], np.float32)).fingerprint
    typed = summarize_output("x", np.asarray([0.0], np.float64)).fingerprint
    nan1 = np.asarray([0x7fc00001], np.uint32).view(np.float32)
    nan2 = np.asarray([0x7fc00002], np.uint32).view(np.float32)
    assert len({positive, negative, shaped, typed}) == 4
    assert summarize_output("x", nan1).fingerprint != summarize_output("x", nan2).fingerprint


@pytest.mark.parametrize("values", [[-10000., -10001.], [2., 2.], [1.], [np.nan, 1.], [np.inf, 1.]])
def test_softmax_edge_contract(values):
    if np.all(np.isfinite(values)):
        result = stable_softmax(np.asarray(values)); assert np.all(np.isfinite(result)) and result.sum() == pytest.approx(1)
    else:
        with pytest.raises(ValueError): stable_softmax(np.asarray(values))


@pytest.mark.parametrize("count", [1, 2, 4])
def test_small_perfect_samples_never_strong(tmp_path, contracts, count):
    path = make_classifier(tmp_path / f"small-{count}.onnx", trigger_sensitive=True)
    samples = [np.zeros((1,1,8,8), np.float32) for _ in range(count)]
    report = BehavioralIntegrityService().analyze(path, samples, *contracts, triggers=_solid_controls(contracts[0]))
    assert "TRIGGER_SENSITIVITY" not in {item.code for item in report.issues}


def test_strong_rule_threshold_boundaries():
    trigger = TriggerSpec("x", TriggerKind.SOLID_PATCH, TriggerLocation.CENTER, 1, 1)
    at = TriggerBehavior(trigger, 8, 8, 1, 1, 6, .75, 1, .75, .25, .50, 0, .25, .50)
    limits = BehavioralLimits()
    assert _is_strong_behavior(at, limits)
    for field, below in [("samples_successful", 7), ("prediction_flip_rate", .749999),
                         ("dominant_class_rate", .749999), ("target_concentration_lift", .499999),
                         ("control_relative_flip_rate", .499999)]:
        assert not _is_strong_behavior(replace(at, **{field: below}), limits)


def test_matched_controls_exclude_self_location_and_mismatched_size():
    service = BehavioralIntegrityService(runtime=FakeRuntime(None))
    base = TriggerBehavior(TriggerSpec("a", TriggerKind.SOLID_PATCH, TriggerLocation.TOP_LEFT, 1, 1),
                           8, 8, 0, 0, 8, 1, 1, 1, 0, 1, 0, None, None)
    same_location = replace(base, trigger=replace(base.trigger, trigger_id="alias"), prediction_flip_rate=0)
    other_size = replace(base, trigger=TriggerSpec("size", TriggerKind.SOLID_PATCH,
                         TriggerLocation.BOTTOM_RIGHT, 2, 2), prediction_flip_rate=0)
    assert service._controls([base, same_location, other_size])[0].control_median_flip_rate is None
    peer = replace(base, trigger=TriggerSpec("peer", TriggerKind.SOLID_PATCH,
                   TriggerLocation.BOTTOM_RIGHT, 1, 1), prediction_flip_rate=.25)
    assert service._controls([base, peer])[0].control_relative_flip_rate == .75


def test_extreme_numeric_deltas_are_finite_or_explicitly_unavailable():
    maximum = np.finfo(np.float64).max
    for left, right in [(np.asarray([maximum]), np.asarray([-maximum])),
                        (np.asarray([np.iinfo(np.int64).min]), np.asarray([np.iinfo(np.int64).max])),
                        (np.asarray([1e-300]), np.asarray([-1e-300])),
                        (np.asarray([1.]), np.asarray([-1.]))]:
        values = output_delta(left, right)
        assert all(item is None or np.isfinite(item) for item in values.__dict__.values())


def test_all_trigger_failures_are_partial_not_clean(tmp_path, contracts):
    path = make_classifier(tmp_path / "m.onnx")
    def baseline_then_fail(count, sample):
        return ((RuntimeStatus.SUCCESS, good_output(sample), None) if count <= 2
                else (RuntimeStatus.FAILURE, None, "controlled"))
    report = BehavioralIntegrityService(runtime=FakeRuntime(baseline_then_fail)).analyze(
        path, [np.zeros((1,1,8,8), np.float32)], *contracts, triggers=_solid_controls(contracts[0])[:1])
    assert report.status == BehavioralAnalysisStatus.PARTIAL
    assert report.coverage.successful_trigger_runs == 0
    assert "BEHAVIOR_ANALYSIS_PARTIAL" in {item.code for item in report.issues}


def test_low_worker_memory_ceiling_fails_bounded_without_restricting_parent(tmp_path):
    path = make_classifier(tmp_path / "m.onnx")
    model_bytes = path.read_bytes()
    runtime = ReferenceOnnxRuntime(max_address_space_bytes=16 * 1024 * 1024)
    status, outputs, error = runtime.run(model_bytes, {"input": np.zeros((1,1,8,8), np.float32)}, 2)
    assert status == RuntimeStatus.FAILURE and outputs is None and error
    assert np.zeros(1024, np.float64).nbytes == 8192


def test_preloaded_parent_cannot_make_tiny_worker_ceiling_succeed(tmp_path):
    from onnx.reference import ReferenceEvaluator
    path = make_classifier(tmp_path / "preloaded.onnx")
    parent_evaluator = ReferenceEvaluator(onnx.load(path))
    parent_output = parent_evaluator.run(None, {"input": np.zeros((1,1,8,8), np.float32)})
    runtime = ReferenceOnnxRuntime(max_address_space_bytes=16 * 1024 * 1024)
    status, outputs, error = runtime.run(path.read_bytes(),
        {"input": np.zeros((1,1,8,8), np.float32)}, 2)
    assert status == RuntimeStatus.FAILURE and outputs is None
    assert error and "inherited address space exceeds configured ceiling" in error
    assert len(parent_output) == 1 and np.asarray(parent_output[0]).shape == (1, 2)


def test_repeated_clean_and_sensitive_reports_are_stable(tmp_path, contracts, corpus):
    clean_path = make_classifier(tmp_path / "clean-repeat.onnx")
    sensitive_path = make_classifier(tmp_path / "sensitive-repeat.onnx", trigger_sensitive=True)
    triggers = _solid_controls(contracts[0])
    clean_corpora = [corpus,
        [np.full((1,1,8,8), .05 + index * .015, np.float32) for index in range(8)]]
    clean_reports = [BehavioralIntegrityService().analyze(clean_path, values, *contracts, triggers=triggers)
                     for values in clean_corpora]
    sensitive_reports = [BehavioralIntegrityService().analyze(sensitive_path, corpus, *contracts, triggers=triggers)
                         for _ in range(2)]
    assert all("TRIGGER_SENSITIVITY" not in {issue.code for issue in report.issues}
               for report in clean_reports)
    assert sensitive_reports[0].to_dict() == sensitive_reports[1].to_dict()
    strong = [[issue.evidence for issue in report.issues if issue.code == "TRIGGER_SENSITIVITY"]
              for report in sensitive_reports]
    assert strong[0] == strong[1] and strong[0]


def test_strict_json_for_clean_nonfinite_partial_and_unavailable_reports(tmp_path, contracts):
    path = make_classifier(tmp_path / "json.onnx")
    sample = np.zeros((1,1,8,8), np.float32)
    clean = BehavioralIntegrityService(runtime=FakeRuntime(
        lambda count, value: (RuntimeStatus.SUCCESS, good_output(value), None))).analyze(
            path, [sample], *contracts, triggers=[])
    nan_report = BehavioralIntegrityService(runtime=FakeRuntime(
        lambda count, value: (RuntimeStatus.SUCCESS,
            {"scores": np.asarray([[np.nan, 0]], np.float32)}, None))).analyze(
                path, [sample], *contracts, triggers=[])
    inf_report = BehavioralIntegrityService(runtime=FakeRuntime(
        lambda count, value: (RuntimeStatus.SUCCESS,
            {"scores": np.asarray([[np.inf, -np.inf]], np.float32)}, None))).analyze(
                path, [sample], *contracts, triggers=[])
    partial = BehavioralIntegrityService(runtime=FakeRuntime(
        lambda count, value: ((RuntimeStatus.SUCCESS, good_output(value), None) if count <= 2
                              else (RuntimeStatus.FAILURE, None, "controlled")))).analyze(
            path, [sample], *contracts, triggers=_solid_controls(contracts[0])[:1])
    unavailable = BehavioralIntegrityService(runtime=FakeRuntime(None)).analyze(
        tmp_path / "missing.onnx", [], *contracts)
    for report in [clean, nan_report, inf_report, partial, unavailable]:
        json.dumps(report.to_dict(), allow_nan=False)


def test_npy_absurd_header_rejected_before_array_load(tmp_path, monkeypatch):
    from modules.model_integrity.cli import _load_bounded_npy
    path = tmp_path / "huge.npy"
    with path.open("wb") as stream:
        np.lib.format.write_array_header_1_0(stream,
            {"descr": np.dtype("float32").str, "fortran_order": False,
             "shape": (2**30, 1, 1, 1, 1)})
    monkeypatch.setattr(np, "load", lambda *args, **kwargs:
        (_ for _ in ()).throw(AssertionError("np.load reached")))
    with pytest.raises(ValueError, match="declaration exceeds"):
        _load_bounded_npy(path)


def test_behavioral_cli_invalid_input_is_bounded_without_traceback(tmp_path, monkeypatch, capsys):
    from modules.model_integrity import cli
    monkeypatch.setattr("sys.argv", ["model-integrity", "behavioral", str(tmp_path / "m.onnx"),
        "--input-npy", str(tmp_path / "missing.npy"), "--layout", "NCHW",
        "--value-min", "0", "--value-max", "1", "--output", "scores"])
    assert cli.main() == 2
    output = capsys.readouterr().out
    assert "BEHAVIORAL ANALYSIS FAILED" in output and "Traceback" not in output
