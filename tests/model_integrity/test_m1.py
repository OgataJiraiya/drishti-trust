from __future__ import annotations
import json
from pathlib import Path

import onnx
import pytest
from onnx import TensorProto, helper

from backend.core.canonical import canonical_json_text
from backend.schemas.evidence import Finding
from modules.model_integrity.artifacts import inspect_artifact, sha256_file
from modules.model_integrity.errors import (
    InvalidArtifactError, OnnxInspectionError, ResourceLimitError, UnsupportedFormatError,
)
from modules.model_integrity.models import InspectionLevel
from modules.model_integrity.service import ModelIntegrityService
from .conftest import make_model


def test_sha256_deterministic_and_different_bytes(tmp_path):
    one, two = tmp_path / "one.pt", tmp_path / "two.pt"
    one.write_bytes(b"model-one"); two.write_bytes(b"model-two")
    assert sha256_file(one) == sha256_file(one)
    assert sha256_file(one) != sha256_file(two)


def test_streamed_large_file(tmp_path):
    path = tmp_path / "large.pth"; path.write_bytes(b"abcd" * 1_250_000)
    assert len(sha256_file(path, chunk_size=8192)) == 64


@pytest.mark.parametrize("kind", ["missing", "directory", "empty", "symlink"])
def test_invalid_artifacts_rejected(tmp_path, kind):
    path = tmp_path / "model.onnx"
    if kind == "directory": path.mkdir()
    elif kind == "empty": path.write_bytes(b"")
    elif kind == "symlink":
        target = tmp_path / "target"; target.write_bytes(b"x"); path.symlink_to(target)
    with pytest.raises(InvalidArtifactError): sha256_file(path)


@pytest.mark.parametrize(("suffix", "claimed", "level"), [
    (".onnx", "onnx", InspectionLevel.STRUCTURAL), (".pt", "pytorch", InspectionLevel.ARTIFACT_ONLY),
    (".pth", "pytorch", InspectionLevel.ARTIFACT_ONLY), (".ckpt", "checkpoint", InspectionLevel.ARTIFACT_ONLY),
    (".h5", "hdf5", InspectionLevel.ARTIFACT_ONLY), (".keras", "keras", InspectionLevel.ARTIFACT_ONLY),
    (".tflite", "tflite", InspectionLevel.ARTIFACT_ONLY), (".pb", "protobuf", InspectionLevel.ARTIFACT_ONLY),
    (".bin", "unknown", InspectionLevel.UNSUPPORTED),
])
def test_format_recognition(tmp_path, suffix, claimed, level):
    path = tmp_path / f"model{suffix}"; path.write_bytes(b"opaque")
    report = inspect_artifact(path)
    assert (report.claimed_format, report.inspection_level) == (claimed, level)


def test_unsafe_format_is_artifact_only_and_strict_rejects(tmp_path):
    path = tmp_path / "hostile.pt"; path.write_bytes(b"pickle-like-but-never-loaded")
    manifest = ModelIntegrityService().inspect(path)
    assert manifest.structure is None and "STRUCTURE_UNAVAILABLE" in manifest.limitations[0]
    with pytest.raises(UnsupportedFormatError): ModelIntegrityService().inspect(path, strict=True)


def test_valid_onnx_structure_profile_io_and_parameters(tiny_onnx):
    manifest = ModelIntegrityService().inspect(tiny_onnx)
    structure = manifest.structure
    assert structure is not None
    assert structure.node_count == 2
    assert structure.operator_profile == {"Add": 1, "Identity": 1}
    assert structure.unique_operator_count == 2
    assert structure.inputs[0].name == "input" and structure.inputs[0].shape == [1, 2]
    assert structure.outputs[0].name == "output"
    assert structure.initializer_count == 2
    assert {item.name: item.element_count for item in structure.initializers} == {"bias": 2, "weight": 4}
    assert structure.total_parameter_count == 6


def test_invalid_onnx_rejected_cleanly(tmp_path):
    path = tmp_path / "bad.onnx"; path.write_bytes(b"not protobuf")
    with pytest.raises(OnnxInspectionError, match="protobuf parsing failed"):
        ModelIntegrityService().inspect(path)


def test_fingerprints_deterministic_and_graph_change(tmp_path):
    a, same, changed = tmp_path / "a.onnx", tmp_path / "same.onnx", tmp_path / "changed.onnx"
    make_model(a); make_model(same); make_model(changed, second_relu=True)
    service = ModelIntegrityService()
    ma, ms, mc = service.inspect(a), service.inspect(same), service.inspect(changed)
    assert ma.fingerprints.structural_sha256 == ms.fingerprints.structural_sha256
    assert ma.fingerprints.parameter_metadata_sha256 == ms.fingerprints.parameter_metadata_sha256
    assert ma.fingerprints.structural_sha256 != mc.fingerprints.structural_sha256


def test_parameter_shape_changes_metadata_fingerprint(tmp_path):
    a, b = tmp_path / "a.onnx", tmp_path / "b.onnx"
    make_model(a, weight_shape=(2, 2)); make_model(b, weight_shape=(2, 3))
    service = ModelIntegrityService()
    assert service.inspect(a).fingerprints.parameter_metadata_sha256 != service.inspect(b).fingerprints.parameter_metadata_sha256


def test_initializer_canonical_order_is_stable(tmp_path):
    a, b = tmp_path / "a.onnx", tmp_path / "b.onnx"
    make_model(a); make_model(b, initializer_order=True)
    service = ModelIntegrityService()
    assert service.inspect(a).fingerprints.structural_sha256 == service.inspect(b).fingerprints.structural_sha256


def test_artifact_bytes_can_change_without_structure_change(tmp_path):
    a, b = tmp_path / "a.onnx", tmp_path / "b.onnx"
    make_model(a, doc="first serialization metadata"); make_model(b, doc="changed non-fingerprinted documentation")
    service = ModelIntegrityService(); ma, mb = service.inspect(a), service.inspect(b)
    assert ma.artifact.sha256 != mb.artifact.sha256
    assert ma.fingerprints.structural_sha256 == mb.fingerprints.structural_sha256


def test_custom_domain_and_dynamic_shape_issues(tmp_path):
    path = tmp_path / "custom.onnx"; make_model(path, custom_domain="vendor.secret", dynamic=True)
    codes = {item.code for item in ModelIntegrityService().inspect(path).issues}
    assert "CUSTOM_OPERATOR_DOMAIN" in codes
    assert "DYNAMIC_OR_UNKNOWN_SHAPE" in codes


def _external_model(path, location):
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1])
    y = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1])
    tensor = helper.make_tensor("external_weight", TensorProto.FLOAT, [1], [0.0])
    tensor.ClearField("float_data"); tensor.data_location = TensorProto.EXTERNAL
    entry = tensor.external_data.add(); entry.key = "location"; entry.value = location
    graph = helper.make_graph([helper.make_node("Add", ["input", "external_weight"], ["output"])],
                              "external", [x], [y], [tensor])
    onnx.save_model(helper.make_model(graph), path)


@pytest.mark.parametrize(("location", "code"), [
    ("weights.bin", "EXTERNAL_DATA_NOT_VERIFIED"),
    ("../../../secret", "EXTERNAL_DATA_PATH_UNSAFE"),
    ("..%2F..%2Fsecret", "EXTERNAL_DATA_PATH_UNSAFE"),
    ("/etc/passwd", "EXTERNAL_DATA_PATH_UNSAFE"),
    ("file:///etc/passwd", "EXTERNAL_DATA_PATH_UNSAFE"),
])
def test_external_data_detected_without_reading(tmp_path, location, code):
    path = tmp_path / "external.onnx"; _external_model(path, location)
    manifest = ModelIntegrityService().inspect(path)
    assert code in {item.code for item in manifest.issues}
    assert "EXTERNAL_DATA_UNVERIFIED" in manifest.limitations


def test_manifest_json_is_deterministic_and_contains_no_weights(tiny_onnx):
    manifest = ModelIntegrityService().inspect(tiny_onnx)
    first = canonical_json_text(manifest.to_dict())
    second = canonical_json_text(ModelIntegrityService().inspect(tiny_onnx).to_dict())
    assert first == second
    assert '"raw_data":' not in first and '"float_data":' not in first
    assert json.loads(first)["schema_version"] == "1"


def test_frozen_finding_schema_unchanged():
    assert list(Finding.model_fields) == ["finding_id", "module", "asset_type", "asset_id",
        "category", "severity", "confidence", "reason", "evidence", "recommendation", "limitations"]


def test_small_onnx_remains_structural_under_configured_limit(tiny_onnx):
    size = tiny_onnx.stat().st_size
    manifest = ModelIntegrityService(max_onnx_parse_bytes=size).inspect(tiny_onnx)
    assert manifest.artifact.inspection_level == InspectionLevel.STRUCTURAL
    assert manifest.fingerprints.structural_sha256 is not None


def test_oversized_onnx_degrades_without_calling_parser(tiny_onnx, monkeypatch):
    parser_called = False
    def forbidden_parser(*args, **kwargs):
        nonlocal parser_called
        parser_called = True
        raise AssertionError("oversized ONNX reached parser")
    monkeypatch.setattr("modules.model_integrity.service.inspect_onnx", forbidden_parser)
    manifest = ModelIntegrityService(max_onnx_parse_bytes=1).inspect(tiny_onnx)
    assert parser_called is False
    assert len(manifest.artifact.sha256) == 64
    assert manifest.artifact.inspection_level == InspectionLevel.ARTIFACT_ONLY
    assert manifest.structure is None
    assert manifest.fingerprints.structural_sha256 is None
    assert manifest.fingerprints.parameter_metadata_sha256 is None
    assert manifest.limitations == ["STRUCTURE_UNAVAILABLE_RESOURCE_LIMIT"]


def test_strict_oversized_onnx_fails_with_bounded_error(tiny_onnx):
    with pytest.raises(ResourceLimitError, match="parse-size limit"):
        ModelIntegrityService(max_onnx_parse_bytes=1).inspect(tiny_onnx, strict=True)


def test_streaming_sha256_is_not_limited_by_onnx_parse_ceiling(tmp_path):
    path = tmp_path / "large.onnx"
    path.write_bytes(b"x" * 4096)
    assert len(sha256_file(path)) == 64
    manifest = ModelIntegrityService(max_onnx_parse_bytes=128).inspect(path)
    assert manifest.artifact.sha256 == sha256_file(path)
    assert manifest.artifact.inspection_level == InspectionLevel.ARTIFACT_ONLY


def test_storage_representation_is_metadata_not_structure(tmp_path):
    from onnx import numpy_helper
    a, b = tmp_path / 'typed.onnx', tmp_path / 'raw.onnx'
    model = make_model(a)
    tensor = model.graph.initializer[0]
    tensor.CopyFrom(numpy_helper.from_array(numpy_helper.to_array(tensor).copy(), tensor.name))
    onnx.save_model(model, b)
    ma, mb = (ModelIntegrityService().inspect(p) for p in (a, b))
    assert ma.fingerprints.structural_sha256 == mb.fingerprints.structural_sha256
    assert ma.fingerprints.parameter_metadata_sha256 != mb.fingerprints.parameter_metadata_sha256
    assert ma.fingerprints.parameter_value_sha256 == mb.fingerprints.parameter_value_sha256


@pytest.mark.parametrize('value', [0.001, float('nan')])
def test_value_only_mutation_preserves_metadata_and_structure(tmp_path, value):
    from modules.model_integrity.baseline import BaselineComparisonService
    a, b = tmp_path / 'reference.onnx', tmp_path / 'candidate.onnx'
    model = make_model(a)
    model.graph.initializer[0].float_data[0] = value
    onnx.save_model(model, b)
    ma, mb = (ModelIntegrityService().inspect(p) for p in (a, b))
    assert ma.artifact.sha256 != mb.artifact.sha256
    assert ma.structure.initializers == mb.structure.initializers
    assert ma.fingerprints.structural_sha256 == mb.fingerprints.structural_sha256
    assert ma.fingerprints.parameter_metadata_sha256 == mb.fingerprints.parameter_metadata_sha256
    assert ma.fingerprints.parameter_value_sha256 != mb.fingerprints.parameter_value_sha256
    result = BaselineComparisonService().compare(a, b)
    assert result.structure.state == 'SAME'
    assert 'STRUCTURAL_FINGERPRINT_CHANGED' not in result.structure.codes
    assert mb.parameter_analysis.status == 'COMPLETE'
    assert mb.parameter_analysis.coverage.element_coverage == 1
    if value != value:
        issue = next(i for i in mb.parameter_analysis.issues if i.code == 'PARAMETER_NAN')
        assert issue.tensor_name == 'weight' and issue.severity_hint == 'HIGH'


@pytest.mark.parametrize('mutation', ['operator', 'opset', 'input', 'output', 'added', 'removed', 'name', 'dtype', 'shape'])
def test_genuine_structure_changes_remain_visible(tmp_path, mutation):
    from modules.model_integrity.baseline import BaselineComparisonService
    a, b = tmp_path / 'reference.onnx', tmp_path / 'candidate.onnx'
    model = make_model(a)
    if mutation == 'operator': model.graph.node.pop()
    elif mutation == 'opset': model.opset_import[0].version += 1
    elif mutation == 'input': model.graph.input[0].type.tensor_type.shape.dim[0].dim_value = 2
    elif mutation == 'output': model.graph.output[0].type.tensor_type.shape.dim[0].dim_value = 2
    elif mutation == 'added': model.graph.initializer.append(helper.make_tensor('extra', TensorProto.FLOAT, [1], [1]))
    elif mutation == 'removed': model.graph.initializer.pop()
    elif mutation == 'name': model.graph.initializer[0].name = 'renamed'
    elif mutation == 'dtype': model.graph.initializer[0].data_type = TensorProto.INT32
    elif mutation == 'shape': model.graph.initializer[0].dims[:] = [4, 1]
    onnx.save_model(model, b)
    result = BaselineComparisonService().compare(a, b)
    assert result.structure.state == 'CHANGED'
    assert 'STRUCTURAL_FINGERPRINT_CHANGED' in result.structure.codes


def test_zero_element_initializer_has_no_channel_reduction_failure(tmp_path):
    path = tmp_path / 'empty-tensor.onnx'
    model = make_model(path)
    model.graph.initializer[0].CopyFrom(helper.make_tensor('weight', TensorProto.FLOAT, [2, 0], []))
    onnx.save_model(model, path)
    result = ModelIntegrityService().inspect(path)
    assert result.parameter_analysis.status == 'COMPLETE'
    assert result.parameter_analysis.coverage.analyzed_parameter_tensors == 2
