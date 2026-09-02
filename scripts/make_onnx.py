"""Build small, self-contained ONNX detectors for demos and end-to-end tests.

These are not trained models: the graph reduces the image to a scalar, multiplies
it by zero and adds a constant head, so the output is a fixed set of boxes with a
real, dynamic batch axis. That is exactly what is needed to exercise
preprocessing, batching, decoding and overlay rendering without shipping weights.
"""

from __future__ import annotations

import argparse

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper


def build_detector(path: str, *, classes: int = 4, anchors: int = 60, size: int = 640,
                   boxes: list[tuple[float, float, float, float, int, float]] | None = None) -> str:
    """A YOLOv8-shaped head: (N, 4 + classes, anchors), cxcywh in model space."""
    head = np.full((1, 4 + classes, anchors), 0.01, np.float32)
    boxes = boxes or [
        (200.0, 240.0, 180.0, 200.0, 0, 0.93),
        (430.0, 300.0, 150.0, 160.0, 1, 0.81),
        (330.0, 130.0, 120.0, 90.0, 2, 0.64),
    ]
    for index, (cx, cy, width, height, class_id, score) in enumerate(boxes):
        head[0, 0, index] = cx
        head[0, 1, index] = cy
        head[0, 2, index] = width
        head[0, 3, index] = height
        head[0, 4 + class_id, index] = score

    nodes = [
        helper.make_node("ReduceMean", ["images", "axes"], ["pooled"], keepdims=1),
        helper.make_node("Reshape", ["pooled", "shape"], ["scalar"]),
        helper.make_node("Mul", ["scalar", "zero"], ["zeroed"]),
        helper.make_node("Add", ["zeroed", "head"], ["output0"]),
    ]
    graph = helper.make_graph(
        nodes,
        "aperture-synthetic-detector",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, ["batch", 3, size, size])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT,
                                       ["batch", 4 + classes, anchors])],
        [
            numpy_helper.from_array(np.array([1, 2, 3], np.int64), "axes"),
            numpy_helper.from_array(np.array([-1, 1, 1], np.int64), "shape"),
            numpy_helper.from_array(np.zeros((1, 1, 1), np.float32), "zero"),
            numpy_helper.from_array(head, "head"),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)],
                              producer_name="aperture")
    model.ir_version = 9
    onnx.checker.check_model(model)
    onnx.save(model, path)
    return path


def build_classifier(path: str, *, classes: int = 5, size: int = 224, winner: int = 2) -> str:
    logits = np.full((1, classes), -2.0, np.float32)
    logits[0, winner] = 6.0
    nodes = [
        helper.make_node("ReduceMean", ["images", "axes"], ["pooled"], keepdims=1),
        helper.make_node("Reshape", ["pooled", "shape"], ["scalar"]),
        helper.make_node("Mul", ["scalar", "zero"], ["zeroed"]),
        helper.make_node("Add", ["zeroed", "logits"], ["output0"]),
    ]
    graph = helper.make_graph(
        nodes,
        "aperture-synthetic-classifier",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, ["batch", 3, size, size])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, ["batch", classes])],
        [
            numpy_helper.from_array(np.array([1, 2, 3], np.int64), "axes"),
            numpy_helper.from_array(np.array([-1, 1], np.int64), "shape"),
            numpy_helper.from_array(np.zeros((1, 1), np.float32), "zero"),
            numpy_helper.from_array(logits, "logits"),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)],
                              producer_name="aperture")
    model.ir_version = 9
    onnx.checker.check_model(model)
    onnx.save(model, path)
    return path


def sample_image(path: str, width: int = 1280, height: int = 720) -> str:
    from PIL import Image, ImageDraw

    gradient = np.linspace(24, 90, height, dtype=np.uint8)[:, None]
    canvas = np.repeat(np.repeat(gradient, width, axis=1)[..., None], 3, axis=2)
    image = Image.fromarray(canvas)
    draw = ImageDraw.Draw(image)
    draw.rectangle([150, 190, 560, 520], outline=(235, 235, 240), width=6)
    draw.ellipse([700, 200, 1050, 520], outline=(255, 176, 32), width=6)
    draw.line([0, height - 70, width, height - 150], fill=(88, 200, 255), width=8)
    image.save(path, quality=92)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["detector", "classifier", "image"])
    parser.add_argument("path")
    parser.add_argument("--classes", type=int, default=4)
    args = parser.parse_args()
    if args.kind == "detector":
        print(build_detector(args.path, classes=args.classes))
    elif args.kind == "classifier":
        print(build_classifier(args.path, classes=args.classes))
    else:
        print(sample_image(args.path))
