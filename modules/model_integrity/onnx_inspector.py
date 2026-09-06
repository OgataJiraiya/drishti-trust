"""ONNX protobuf metadata inspection without inference or external tensor loading."""
from __future__ import annotations
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from .errors import OnnxInspectionError
from .fingerprint import fingerprint
from .models import OnnxStructure, StructuralIssue, TensorMetadata, ValueMetadata

MAX_NODES = 100_000
MAX_INITIALIZERS = 100_000


def _safe_element_count(shape: list[int]) -> int | None:
    count = 1
    for dimension in shape:
        if dimension < 0 or (dimension and count > (2**63 - 1) // dimension):
            return None
        count *= dimension
    return count


def _onnx():
    try: import onnx
    except ImportError as exc: raise OnnxInspectionError("ONNX structural support is not installed") from exc
    return onnx


def _dtype(value: int) -> str:
    onnx = _onnx()
    try: return onnx.TensorProto.DataType.Name(value)
    except ValueError: return f"UNKNOWN({value})"


def _shape(tensor_type: Any) -> list[int | str | None]:
    if not tensor_type.HasField("shape"): return []
    result: list[int | str | None] = []
    for dimension in tensor_type.shape.dim:
        if dimension.HasField("dim_value"): result.append(int(dimension.dim_value))
        elif dimension.HasField("dim_param"): result.append(dimension.dim_param or None)
        else: result.append(None)
    return result


def _value(value: Any) -> ValueMetadata:
    tensor = value.type.tensor_type
    return ValueMetadata(name=value.name, dtype=_dtype(tensor.elem_type), shape=_shape(tensor))


def _safe_external_locations(tensor: Any, model_dir: Path) -> tuple[list[str], list[StructuralIssue]]:
    onnx = _onnx()
    if tensor.data_location != onnx.TensorProto.EXTERNAL and not tensor.external_data: return [], []
    values = {entry.key: entry.value for entry in tensor.external_data}
    location = values.get("location", "")
    issues: list[StructuralIssue] = []
    if not location:
        issues.append(StructuralIssue("EXTERNAL_DATA_LOCATION_MISSING", "HIGH",
            [f"initializer={tensor.name}"], "External tensor data has no bounded location."))
        return [], issues
    normalized = unquote(location).replace("\\", "/")
    pure = PurePosixPath(normalized)
    unsafe = (pure.is_absolute() or ".." in pure.parts or "\x00" in normalized
              or "://" in normalized or (pure.parts and pure.parts[0].endswith(":")))
    try:
        resolved = (model_dir / normalized).resolve()
        resolved.relative_to(model_dir.resolve())
    except (OSError, ValueError): unsafe = True
    if unsafe:
        issues.append(StructuralIssue("EXTERNAL_DATA_PATH_UNSAFE", "HIGH",
            [f"initializer={tensor.name}", f"location={location[:256]}"],
            "External tensor location escapes or may escape the model directory."))
    else:
        issues.append(StructuralIssue("EXTERNAL_DATA_NOT_VERIFIED", "MEDIUM",
            [f"initializer={tensor.name}", f"location={location[:256]}"],
            "External tensor data was detected but deliberately not loaded in M1."))
    return [location[:256]], issues


def inspect_onnx(path: Path, approved_operators: set[str] | None = None) -> tuple[OnnxStructure, str, str, list[StructuralIssue], list[str], Any]:
    onnx = _onnx()
    try:
        model = onnx.load_model(path, load_external_data=False)
    except Exception as exc:
        raise OnnxInspectionError("ONNX protobuf parsing failed") from exc
    graph = model.graph
    if len(graph.node) > MAX_NODES or len(graph.initializer) > MAX_INITIALIZERS:
        raise OnnxInspectionError("ONNX graph metadata exceeds bounded M1 inspection limits")
    issues: list[StructuralIssue] = []
    limitations: list[str] = []
    inputs = [_value(value) for value in graph.input]
    outputs = [_value(value) for value in graph.output]
    if not inputs: issues.append(StructuralIssue("NO_GRAPH_INPUTS", "HIGH", [], "The graph declares no inputs."))
    if not outputs: issues.append(StructuralIssue("NO_GRAPH_OUTPUTS", "HIGH", [], "The graph declares no outputs."))
    if not graph.node: issues.append(StructuralIssue("EMPTY_GRAPH", "HIGH", [], "The graph contains no operator nodes."))
    for value in inputs + outputs:
        if any(not isinstance(item, int) for item in value.shape):
            issues.append(StructuralIssue("DYNAMIC_OR_UNKNOWN_SHAPE", "LOW",
                [f"tensor={value.name}", f"shape={value.shape}"],
                "A graph boundary contains symbolic or unknown dimensions."))

    nodes: list[dict[str, Any]] = []
    profile: Counter[str] = Counter()
    for index, node in enumerate(graph.node):
        domain = node.domain or ""
        profile[node.op_type] += 1
        nodes.append({"index": index, "name": node.name, "op_type": node.op_type,
            "domain": domain, "inputs": list(node.input), "outputs": list(node.output),
            "attributes": sorted(
                [{"name": attr.name, "type": int(attr.type)} for attr in node.attribute],
                key=lambda item: (item["name"], item["type"]),
            )})
        if domain not in {"", "ai.onnx", "ai.onnx.ml"}:
            issues.append(StructuralIssue("CUSTOM_OPERATOR_DOMAIN", "MEDIUM",
                [f"node={node.name or index}", f"domain={domain}", f"op_type={node.op_type}"],
                "A non-standard operator domain requires separate implementation review."))
        if approved_operators is not None and node.op_type not in approved_operators:
            issues.append(StructuralIssue("OPERATOR_NOT_APPROVED", "MEDIUM",
                [f"node={node.name or index}", f"op_type={node.op_type}"],
                "Operator is outside the caller-provided approved set."))

    initializers: list[TensorMetadata] = []
    external_seen = False
    for tensor in graph.initializer:
        shape = [int(item) for item in tensor.dims]
        count = _safe_element_count(shape)
        locations, external_issues = _safe_external_locations(tensor, path.parent)
        external_seen |= bool(locations or external_issues)
        issues.extend(external_issues)
        initializers.append(TensorMetadata(name=tensor.name, dtype=_dtype(tensor.data_type),
            shape=shape, element_count=count, raw_data_byte_length=len(tensor.raw_data),
            external_data=locations))
    initializers.sort(key=lambda item: item.name)
    if external_seen: limitations.append("EXTERNAL_DATA_UNVERIFIED")
    if any(not item.name for item in initializers):
        issues.append(StructuralIssue("INITIALIZER_NAME_MISSING", "MEDIUM", [],
            "An initializer lacks a stable name for later comparison."))

    opsets = sorted(({"domain": item.domain or "", "version": int(item.version)}
                     for item in model.opset_import), key=lambda item: (item["domain"], item["version"]))
    operator_profile = dict(sorted(profile.items()))
    structure = OnnxStructure(ir_version=int(model.ir_version), producer_name=model.producer_name,
        producer_version=model.producer_version, domain=model.domain,
        model_version=int(model.model_version), opsets=opsets, graph_name=graph.name,
        inputs=inputs, outputs=outputs, nodes=nodes, node_count=len(nodes),
        operator_profile=operator_profile, unique_operator_count=len(operator_profile),
        initializers=initializers, initializer_count=len(initializers),
        total_parameter_count=(None if any(item.element_count is None for item in initializers)
                               else sum(item.element_count or 0 for item in initializers)))
    structure_payload = {
        "format": "onnx", "ir_version": structure.ir_version, "opsets": opsets,
        "inputs": [item.__dict__ for item in inputs], "outputs": [item.__dict__ for item in outputs],
        "nodes": nodes,
        "initializers": [
            {"name": item.name, "dtype": item.dtype, "shape": item.shape,
             "element_count": item.element_count}
            for item in initializers
        ],
    }
    parameter_payload = [item.__dict__ for item in initializers]
    return structure, fingerprint(structure_payload), fingerprint(parameter_payload), issues, limitations, model
