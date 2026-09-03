from __future__ import annotations
import pytest
import onnx
from onnx import TensorProto, helper


def make_model(path, *, second_relu=False, weight_shape=(2, 2), dynamic=False,
               custom_domain="", initializer_order=False, doc=""):
    dims = [None if dynamic else 1, 2]
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, dims)
    y = helper.make_tensor_value_info("output", TensorProto.FLOAT, dims)
    w = helper.make_tensor("weight", TensorProto.FLOAT, weight_shape,
                           [float(i) for i in range(max(1, weight_shape[0] * weight_shape[1]))])
    b = helper.make_tensor("bias", TensorProto.FLOAT, [2], [0.0, 0.0])
    nodes = [helper.make_node("Add", ["input", "bias"], ["hidden"], name="add")]
    output_name = "hidden"
    if second_relu:
        nodes.append(helper.make_node("Relu", ["hidden"], ["output"], name="relu", domain=custom_domain))
        output_name = "output"
    else:
        nodes.append(helper.make_node("Identity", ["hidden"], ["output"], name="identity", domain=custom_domain))
        output_name = "output"
    initializers = [w, b]
    if initializer_order: initializers.reverse()
    graph = helper.make_graph(nodes, "tiny_graph", [x], [y], initializer=initializers)
    model = helper.make_model(graph, producer_name="drishti-test", opset_imports=[helper.make_opsetid("", 18)])
    model.doc_string = doc
    onnx.save_model(model, path)
    return model


@pytest.fixture
def tiny_onnx(tmp_path):
    path = tmp_path / "tiny.onnx"
    make_model(path)
    return path
