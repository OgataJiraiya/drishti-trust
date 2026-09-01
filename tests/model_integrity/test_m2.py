from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper

from backend.core.canonical import canonical_json_text
from modules.model_integrity.models import AnalysisMode, AnalysisStatus
from modules.model_integrity.parameter_analysis import ParameterAnalysisLimits
from modules.model_integrity.service import ModelIntegrityService


def _model(path: Path, tensors, *, doc=""):
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1])
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [1])
    graph = helper.make_graph([helper.make_node("Identity", ["x"], ["y"])], "m", [x], [y], initializer=tensors)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    model.doc_string = doc
    path.write_bytes(model.SerializeToString())
    return path


def _tensor(name, values, dtype=np.float32):
    return numpy_helper.from_array(np.asarray(values, dtype=dtype), name)


def _inspect(tmp_path, *tensors, limits=None):
    return ModelIntegrityService(parameter_limits=limits).inspect(_model(tmp_path / "m.onnx", tensors))


def _stat(manifest, name="w"):
    return next(item for item in manifest.parameter_analysis.tensor_statistics if item.tensor_name == name)


def _codes(manifest):
    return [item.code for item in manifest.parameter_analysis.issues]


def test_exact_statistics_norms_and_zero_fractions(tmp_path):
    stat = _stat(_inspect(tmp_path, _tensor("w", [0, 1, 2, 3])))
    assert stat.mean == pytest.approx(1.5)
    assert stat.standard_deviation == pytest.approx(np.std([0, 1, 2, 3]))
    assert stat.rms == pytest.approx(np.sqrt(3.5))
    assert stat.l1_norm == pytest.approx(6)
    assert stat.l2_norm == pytest.approx(np.sqrt(14))
    assert stat.zero_fraction == .25 and stat.near_zero_fraction == .25
    assert stat.median == 1.5 and stat.mad == 1.0


@pytest.mark.parametrize(("value", "code"), [
    (np.nan, "PARAMETER_NAN"), (np.inf, "PARAMETER_POSITIVE_INFINITY"),
    (-np.inf, "PARAMETER_NEGATIVE_INFINITY"),
])
def test_nonfinite_detection(tmp_path, value, code):
    manifest = _inspect(tmp_path, _tensor("w", [1, value]))
    assert code in _codes(manifest)
    issue = next(item for item in manifest.parameter_analysis.issues if item.code == code)
    assert "count=1" in issue.evidence and "fraction=0.5" in issue.evidence


def test_zero_constant_and_near_zero(tmp_path):
    manifest = _inspect(tmp_path, _tensor("zero", np.zeros(4)),
                        _tensor("constant", np.ones(4)), _tensor("near", [1e-10, 1]))
    assert {"ALL_ZERO_TENSOR", "CONSTANT_TENSOR"} <= set(_codes(manifest))
    assert _stat(manifest, "near").near_zero_fraction == .5


def test_deterministic_sampling_and_partial_status(tmp_path):
    limits = ParameterAnalysisLimits(max_single_tensor_bytes=16, max_total_parameter_bytes=16, max_sample_values=5)
    tensor = _tensor("w", np.arange(100, dtype=np.float32))
    first = _inspect(tmp_path, tensor, limits=limits)
    second = ModelIntegrityService(parameter_limits=limits).inspect(tmp_path / "m.onnx")
    assert _stat(first) == _stat(second)
    assert _stat(first).analysis_mode == AnalysisMode.SAMPLED
    assert _stat(first).analyzed_element_count == 5
    assert first.parameter_analysis.status == AnalysisStatus.PARTIAL
    assert first.fingerprints.parameter_value_status == AnalysisStatus.UNAVAILABLE
    assert first.fingerprints.parameter_value_sha256 is None


def test_full_mode_and_resource_ceiling(tmp_path):
    full = _inspect(tmp_path, _tensor("w", np.arange(4)))
    assert _stat(full).analysis_mode == AnalysisMode.FULL
    assert full.parameter_analysis.status == AnalysisStatus.COMPLETE
    limited = _inspect(tmp_path, _tensor("w", np.arange(100)), limits=ParameterAnalysisLimits(max_total_parameter_bytes=8, max_sample_values=3))
    assert _stat(limited).analyzed_element_count == 3
    assert limited.parameter_analysis.coverage.element_coverage == .03


def test_absurd_dimensions_and_malformed_length_degrade_without_allocation(tmp_path):
    huge = TensorProto(name="huge", data_type=TensorProto.FLOAT, dims=[2**62, 8], raw_data=b"x")
    malformed = TensorProto(name="bad", data_type=TensorProto.FLOAT, dims=[2], raw_data=b"1234")
    manifest = _inspect(tmp_path, huge, malformed)
    assert manifest.parameter_analysis.status == AnalysisStatus.UNAVAILABLE
    assert any("INVALID_OR_ABSURD" in item for item in manifest.parameter_analysis.limitations)
    assert any("DATA_LENGTH_MISMATCH" in item for item in manifest.parameter_analysis.limitations)


def test_raw_data_too_long_and_more_hostile_shape_products(tmp_path):
    too_long = TensorProto(name="long", data_type=TensorProto.FLOAT, dims=[1], raw_data=b"12345678")
    huge = TensorProto(name="triple", data_type=TensorProto.FLOAT,
                       dims=[2**31 - 1, 2**31 - 1, 2**31 - 1], raw_data=b"x")
    manifest = _inspect(tmp_path, too_long, huge)
    assert manifest.structure.total_parameter_count is None
    assert sum("PARAMETER_DATA_LENGTH_MISMATCH" in item for item in manifest.parameter_analysis.limitations) == 1
    assert sum("INVALID_OR_ABSURD_TENSOR_DIMENSIONS" in item for item in manifest.parameter_analysis.limitations) == 1


def test_external_tensor_never_opened_and_coverage_explicit(tmp_path, monkeypatch):
    tensor = TensorProto(name="external", data_type=TensorProto.FLOAT, dims=[1], data_location=TensorProto.EXTERNAL)
    entry = tensor.external_data.add(); entry.key = "location"; entry.value = "weights.bin"
    (tmp_path / "weights.bin").write_bytes(np.asarray([99], dtype="<f4").tobytes())
    manifest = _inspect(tmp_path, tensor)
    assert manifest.parameter_analysis.status == AnalysisStatus.UNAVAILABLE
    assert manifest.parameter_analysis.coverage.tensor_coverage == 0
    assert "EXTERNAL_PARAMETER_DATA_UNAVAILABLE" in manifest.parameter_analysis.limitations[0]


@pytest.mark.parametrize("dtype", [np.float16, np.float32, np.float64, np.int8, np.int16,
    np.int32, np.int64, np.uint8, np.uint16, np.uint32, np.uint64, np.bool_])
def test_supported_numeric_dtypes(tmp_path, dtype):
    manifest = _inspect(tmp_path, _tensor("w", [0, 1], dtype))
    assert manifest.parameter_analysis.status == AnalysisStatus.COMPLETE
    assert _stat(manifest).finite_count == 2


def test_unsupported_dtype_and_empty_tensor_safe(tmp_path):
    string = helper.make_tensor("text", TensorProto.STRING, [1], [b"x"])
    empty = _tensor("empty", np.asarray([], dtype=np.float32))
    manifest = _inspect(tmp_path, string, empty)
    assert any("UNSUPPORTED_PARAMETER_DTYPE" in item for item in manifest.parameter_analysis.limitations)
    assert _stat(manifest, "empty").analyzed_element_count == 0


def test_value_fingerprint_semantics_and_order(tmp_path):
    a = _model(tmp_path / "a.onnx", [_tensor("a", [1, 2]), _tensor("b", [3])], doc="a")
    b = _model(tmp_path / "b.onnx", [_tensor("b", [3]), _tensor("a", [1, 2])], doc="b")
    c = _model(tmp_path / "c.onnx", [_tensor("a", [1, 9]), _tensor("b", [3])])
    service = ModelIntegrityService(); ma, mb, mc = service.inspect(a), service.inspect(b), service.inspect(c)
    assert ma.fingerprints.parameter_value_sha256 == mb.fingerprints.parameter_value_sha256
    assert ma.fingerprints.parameter_value_sha256 != mc.fingerprints.parameter_value_sha256
    assert ma.artifact.sha256 != mb.artifact.sha256


def test_typed_and_raw_fields_have_same_canonical_value_fingerprint(tmp_path):
    typed = helper.make_tensor("w", TensorProto.FLOAT, [2], [1.0, -2.0], raw=False)
    raw = _tensor("w", [1.0, -2.0], np.float32)
    service = ModelIntegrityService()
    a = service.inspect(_model(tmp_path / "typed.onnx", [typed]))
    b = service.inspect(_model(tmp_path / "raw.onnx", [raw]))
    assert a.fingerprints.parameter_value_sha256 == b.fingerprints.parameter_value_sha256


@pytest.mark.parametrize(("onnx_dtype", "numpy_dtype", "values"), [
    (TensorProto.FLOAT16, np.float16, [0, -2]), (TensorProto.DOUBLE, np.float64, [0, -2]),
    (TensorProto.INT8, np.int8, [-1, 2]), (TensorProto.INT16, np.int16, [-1, 2]),
    (TensorProto.INT32, np.int32, [-1, 2]), (TensorProto.INT64, np.int64, [-1, 2]),
    (TensorProto.UINT8, np.uint8, [0, 2]), (TensorProto.UINT16, np.uint16, [0, 2]),
    (TensorProto.UINT32, np.uint32, [0, 2]), (TensorProto.UINT64, np.uint64, [0, 2]),
    (TensorProto.BOOL, np.bool_, [False, True]),
])
def test_typed_and_raw_canonical_bytes_across_dtypes(tmp_path, onnx_dtype, numpy_dtype, values):
    typed = helper.make_tensor("w", onnx_dtype, [2], values, raw=False)
    raw = _tensor("w", values, numpy_dtype)
    service = ModelIntegrityService()
    a = service.inspect(_model(tmp_path / "typed.onnx", [typed]))
    b = service.inspect(_model(tmp_path / "raw.onnx", [raw]))
    assert a.fingerprints.parameter_value_sha256 == b.fingerprints.parameter_value_sha256


def test_dtype_mutation_and_nan_payload_change_fingerprint(tmp_path):
    service = ModelIntegrityService()
    f32 = service.inspect(_model(tmp_path / "f32.onnx", [_tensor("w", [1], np.float32)]))
    i32 = service.inspect(_model(tmp_path / "i32.onnx", [_tensor("w", [1], np.int32)]))
    nan_a = np.asarray([0x7FC00001], dtype="<u4").view("<f4")
    nan_b = np.asarray([0x7FC00002], dtype="<u4").view("<f4")
    a = service.inspect(_model(tmp_path / "nan-a.onnx", [_tensor("w", nan_a)]))
    b = service.inspect(_model(tmp_path / "nan-b.onnx", [_tensor("w", nan_b)]))
    assert f32.fingerprints.parameter_value_sha256 != i32.fingerprints.parameter_value_sha256
    assert a.fingerprints.parameter_value_sha256 != b.fingerprints.parameter_value_sha256


def test_shape_change_changes_value_fingerprint(tmp_path):
    a = _model(tmp_path / "a.onnx", [_tensor("w", np.asarray([1, 2]).reshape(2, 1))])
    b = _model(tmp_path / "b.onnx", [_tensor("w", np.asarray([1, 2]).reshape(1, 2))])
    service = ModelIntegrityService()
    assert service.inspect(a).fingerprints.parameter_value_sha256 != service.inspect(b).fingerprints.parameter_value_sha256


def test_tensor_scale_channel_dead_and_energy_anomalies(tmp_path):
    tensors = [_tensor("scale_a", np.ones((4, 4))), _tensor("scale_b", np.full((4, 4), 1.1)),
               _tensor("scale_x", np.full((4, 4), 1000))]
    channels = np.vstack([np.ones(8), np.full(8, 2), np.full(8, 3), np.full(8, 100)]).astype(np.float32)
    dead = np.vstack([np.ones(8), np.ones(8), np.ones(8), np.zeros(8)]).astype(np.float32)
    manifest = _inspect(tmp_path, *tensors, _tensor("channels", channels), _tensor("dead", dead))
    codes = set(_codes(manifest))
    assert "TENSOR_SCALE_OUTLIER" in codes
    assert "CHANNEL_NORM_OUTLIER" in codes
    assert "EXTREME_CHANNEL_SCALE" in codes
    assert "DEAD_CHANNEL" in codes
    assert "PARAMETER_ENERGY_CONCENTRATION" in codes


def test_mad_zero_is_safe_and_top_k_bounded(tmp_path):
    values = np.ones((50, 4), dtype=np.float32)
    values[:20] *= np.arange(1, 21)[:, None] * 100
    limits = ParameterAnalysisLimits(max_issues_per_tensor=3, max_total_parameter_issues=4)
    manifest = _inspect(tmp_path, _tensor("many", values), limits=limits)
    per_tensor = [item for item in manifest.parameter_analysis.issues if item.tensor_name == "many"]
    assert len(per_tensor) <= 3
    assert len(manifest.parameter_analysis.issues) <= 4
    assert any("PARAMETER_ISSUES_TRUNCATED_PER_TENSOR" in item
               for item in manifest.parameter_analysis.limitations)


@pytest.mark.parametrize(("dtype", "values"), [
    (np.int8, [-128, 127]), (np.int16, [-32768, 32767]),
    (np.int32, [-(2**31), 2**31 - 1]), (np.int64, [-(2**63), 2**63 - 1]),
    (np.uint64, [0, 2**64 - 1]),
])
def test_integer_limit_statistics_do_not_wrap(tmp_path, dtype, values):
    stat = _stat(_inspect(tmp_path, _tensor("w", values, dtype)))
    expected_l1 = float(sum(abs(value) for value in values))
    expected_l2 = math.sqrt(sum(value * value for value in values))
    assert stat.l1_norm == pytest.approx(expected_l1)
    assert stat.l2_norm == pytest.approx(expected_l2)
    assert stat.rms == pytest.approx(expected_l2 / math.sqrt(len(values)))
    assert stat.l1_norm >= 0 and stat.l2_norm >= 0


def test_float16_accumulates_in_float64_and_extreme_float_stays_json_safe(tmp_path):
    half = _stat(_inspect(tmp_path, _tensor("w", [65504, 65504], np.float16)))
    assert half.l2_norm == pytest.approx(65504 * math.sqrt(2))
    maximum = np.finfo(np.float64).max
    manifest = _inspect(tmp_path, _tensor("w", [maximum, maximum], np.float64))
    stat = _stat(manifest)
    assert stat.rms == maximum and stat.l1_norm is None and stat.l2_norm is None
    json.dumps(manifest.to_dict(), allow_nan=False)


def test_nonfinite_manifest_is_strict_json(tmp_path):
    manifest = _inspect(tmp_path, _tensor("w", [np.nan, np.inf, -np.inf, 2], np.float64))
    encoded = json.dumps(manifest.to_dict(), sort_keys=True, allow_nan=False)
    assert ": NaN" not in encoded and ": Infinity" not in encoded and ": -Infinity" not in encoded
    stat = _stat(manifest)
    assert stat.mean == 2.0
    assert (stat.nan_count, stat.positive_infinity_count, stat.negative_infinity_count) == (1, 1, 1)


def test_negative_zero_single_channel_and_identical_channels_safe(tmp_path):
    negative_zero = _tensor("negative-zero", np.asarray([-0.0], dtype=np.float32))
    single = _tensor("single", np.ones((1, 4), dtype=np.float32))
    identical = _tensor("identical", np.ones((4, 4), dtype=np.float32))
    manifest = _inspect(tmp_path, negative_zero, single, identical)
    assert _stat(manifest, "negative-zero").zero_fraction == 1.0
    assert not {"CHANNEL_NORM_OUTLIER", "EXTREME_CHANNEL_SCALE"} & set(_codes(manifest))


def test_channel_and_tensor_record_ceiling_limitations(tmp_path):
    limits = ParameterAnalysisLimits(max_channels_per_tensor=2, max_reported_tensors=1)
    manifest = _inspect(tmp_path, _tensor("a", np.ones((3, 4))), _tensor("b", np.ones(4)), limits=limits)
    assert len(manifest.parameter_analysis.tensor_statistics) == 1
    assert "TENSOR_STATISTICS_TRUNCATED" in manifest.parameter_analysis.limitations
    assert any("CHANNEL_ANALYSIS_SKIPPED_CHANNEL_LIMIT" in item
               for item in manifest.parameter_analysis.limitations)
    assert manifest.parameter_analysis.status == AnalysisStatus.PARTIAL


def test_total_issue_truncation_is_explicit_and_partial(tmp_path):
    limits = ParameterAnalysisLimits(max_total_parameter_issues=2)
    manifest = _inspect(tmp_path, *[_tensor(f"w{i}", [np.nan, np.inf, -np.inf]) for i in range(3)], limits=limits)
    assert len(manifest.parameter_analysis.issues) == 2
    assert "PARAMETER_ISSUES_TRUNCATED_TOTAL" in manifest.parameter_analysis.limitations
    assert manifest.parameter_analysis.status == AnalysisStatus.PARTIAL


def test_mixed_embedded_and_external_fingerprint_is_explicitly_partial(tmp_path):
    external = TensorProto(name="external", data_type=TensorProto.FLOAT, dims=[1], data_location=TensorProto.EXTERNAL)
    entry = external.external_data.add(); entry.key = "location"; entry.value = "never-open.bin"
    manifest = _inspect(tmp_path, _tensor("embedded", [1]), external)
    assert manifest.fingerprints.parameter_value_sha256 is not None
    assert manifest.fingerprints.parameter_value_status == AnalysisStatus.PARTIAL
    assert manifest.parameter_analysis.status == AnalysisStatus.PARTIAL
    assert manifest.parameter_analysis.coverage.tensor_coverage == .5


def test_manifest_is_deterministic_bounded_and_has_no_arrays(tmp_path):
    path = _model(tmp_path / "m.onnx", [_tensor("w", np.arange(32).reshape(4, 8))])
    service = ModelIntegrityService()
    one = canonical_json_text(service.inspect(path).to_dict()); two = canonical_json_text(service.inspect(path).to_dict())
    assert one == two
    assert '"raw_data":' not in one and '"float_data":' not in one
    data = json.loads(one)
    assert "parameter_analysis" in data and "parameter_value_sha256" in data["fingerprints"]


def test_m1_fingerprints_unchanged_by_analysis(tmp_path):
    path = _model(tmp_path / "m.onnx", [_tensor("w", [1, 2])])
    first = ModelIntegrityService().inspect(path)
    second = ModelIntegrityService(parameter_limits=ParameterAnalysisLimits(max_sample_values=1, max_single_tensor_bytes=1)).inspect(path)
    assert first.fingerprints.structural_sha256 == second.fingerprints.structural_sha256
    assert first.fingerprints.parameter_metadata_sha256 == second.fingerprints.parameter_metadata_sha256


def test_unsupported_format_has_explicit_unavailable_analysis(tmp_path):
    path = tmp_path / "model.pt"; path.write_bytes(b"opaque")
    manifest = ModelIntegrityService().inspect(path)
    assert manifest.parameter_analysis.status == AnalysisStatus.UNAVAILABLE
    assert manifest.parameter_analysis.coverage.tensor_coverage == 0
    assert _codes(manifest) == ["PARAMETER_ANALYSIS_UNAVAILABLE"]
