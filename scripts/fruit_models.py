"""Export the three real models the `fruit` workspace serves, into one directory.

- yolov8n-seg.onnx and yolov8s-seg.onnx: Ultralytics YOLOv8 segmentation, COCO-80,
  the two versions of "Fruit bowl segmentation".
- resnet18-imagenet.onnx: torchvision ResNet-18, ImageNet-1k, raw logits, the
  "Fruit variety" stage of the workflow (ImageNet knows Granny Smith, orange,
  lemon, banana, fig, pomegranate).
- coco-classes.json and imagenet-classes.json: their class lists, in output order.

    uv run --with ultralytics --with onnx --with onnxslim --with onnxruntime \\
        python scripts/fruit_models.py /tmp/aperture-fruit

Downloads the published weights on first run (about 75 MB in all). Then run
scripts/seed_fruit.py against the same directory.
"""

from __future__ import annotations

import json
import os
import sys

import torch
import torchvision
from ultralytics import YOLO

out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "aperture-fruit")
os.makedirs(out, exist_ok=True)
os.chdir(out)

for name in ("yolov8n-seg", "yolov8s-seg"):
    model = YOLO(f"{name}.pt")
    model.export(format="onnx", imgsz=640, opset=12, simplify=True)
json.dump([model.names[i] for i in range(len(model.names))], open("coco-classes.json", "w"))

weights = torchvision.models.ResNet18_Weights.IMAGENET1K_V1
resnet = torchvision.models.resnet18(weights=weights).eval()
torch.onnx.export(resnet, torch.zeros(1, 3, 224, 224), "resnet18-imagenet.onnx",
                  input_names=["input"], output_names=["logits"],
                  dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
                  opset_version=13, dynamo=False)
json.dump(weights.meta["categories"], open("imagenet-classes.json", "w"))
print("models in", out)
