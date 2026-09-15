"""What did someone actually upload, and can anything serve it?

An upload is bytes. Whether those bytes are servable is not a matter of file
extension: a `.pt` can be a TorchScript archive (servable), an Ultralytics
checkpoint (convertible), or a bare `state_dict` (weights with no graph, which
*nothing* can execute without the code that defined the model). Telling the
difference at upload time is the difference between a useful message and
"validation failed".

This runs in the API, which has no torch, so the torch zip's pickle is scanned
for marker strings rather than unpickled. That is enough to name the family with
confidence, and it cannot execute anything the uploader put in the file.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass, field, asdict

# How the artifact can be used, in decreasing order of usefulness.
SERVABLE = "servable"          # a runtime can load and execute it as-is
CONVERTIBLE = "convertible"    # not servable, but Aperture can export it
WEIGHTS_ONLY = "weights_only"  # no graph: only the training code can export it
UNKNOWN = "unknown"

MAX_SCAN_BYTES = 4 * 1024 * 1024  # the pickle header carries every key name well within this


@dataclass
class ArtifactInfo:
    kind: str = "unknown"           # onnx | torchscript | ultralytics | state_dict | ...
    usability: str = UNKNOWN
    family: str | None = None       # rf-detr | yolo | detr | resnet | ...
    suggested_preset: str | None = None
    detail: str = ""                # what it is, in one sentence
    next_step: str = ""             # what to do about it
    export_snippet: str | None = None
    evidence: list[str] = field(default_factory=list)

    @property
    def servable(self) -> bool:
        return self.usability == SERVABLE

    def as_dict(self) -> dict:
        data = asdict(self)
        data["servable"] = self.servable
        return data


# --------------------------------------------------------------- family guesses
# Each entry: marker in the pickle, family, preset, and how to export it.
_FAMILIES: list[tuple[bytes, str, str | None, str]] = [
    (b"transformer.decoder", "rf-detr", "rf-detr",
     "from rfdetr import RFDETRBase\n"
     "model = RFDETRBase(pretrain_weights='weights.pt')\n"
     "model.export()          # writes inference_model.onnx"),
    (b"rfdetr", "rf-detr", "rf-detr",
     "from rfdetr import RFDETRBase\n"
     "model = RFDETRBase(pretrain_weights='weights.pt')\n"
     "model.export()"),
    (b"model.model.22", "yolo", "yolov8",
     "from ultralytics import YOLO\n"
     "YOLO('weights.pt').export(format='onnx', dynamic=True, opset=17)"),
    (b"model.0.conv.weight", "yolo", "yolov8",
     "from ultralytics import YOLO\n"
     "YOLO('weights.pt').export(format='onnx', dynamic=True, opset=17)"),
    (b"backbone.conv1.weight", "resnet", "resnet-imagenet",
     "import torch\n"
     "torch.onnx.export(model, torch.zeros(1, 3, 224, 224), 'model.onnx', opset_version=17)"),
    (b"features.0.0.weight", "efficientnet", "efficientnet",
     "import torch\n"
     "torch.onnx.export(model, torch.zeros(1, 3, 224, 224), 'model.onnx', opset_version=17)"),
    (b"visual.transformer", "clip", "clip-image",
     "import torch\n"
     "torch.onnx.export(model.visual, torch.zeros(1, 3, 224, 224), 'clip.onnx', opset_version=17)"),
    (b"image_encoder.", "sam", "sam",
     "Export the image encoder and the mask decoder separately; Aperture serves the\n"
     "mask decoder with the binary_mask decoder."),
]

_GENERIC_EXPORT = (
    "# in the code that trained this model:\n"
    "import torch\n"
    "torch.onnx.export(model, torch.zeros(1, 3, H, W), 'model.onnx',\n"
    "                  input_names=['images'], output_names=['output0'],\n"
    "                  dynamic_axes={'images': {0: 'batch'}}, opset_version=17)"
)


def inspect_artifact(path: str, *, filename: str | None = None) -> ArtifactInfo:
    name = (filename or path).lower()
    with open(path, "rb") as fh:
        head = fh.read(64)

    if zipfile.is_zipfile(path):
        return _inspect_zip(path, name)
    if _looks_onnx(path, head):
        return ArtifactInfo(
            kind="onnx", usability=SERVABLE, detail="An ONNX graph.",
            next_step="Validate it against a reference image, then publish.",
            evidence=["onnx protobuf header"],
        )
    if _looks_safetensors(path, head):
        return ArtifactInfo(
            kind="safetensors", usability=WEIGHTS_ONLY,
            detail="A safetensors file: named tensors with no model graph.",
            next_step="safetensors stores weights only, so nothing can execute it on its own. "
                      "Export ONNX (or TorchScript) from the code that defines the model and "
                      "upload that instead.",
            export_snippet=_GENERIC_EXPORT,
            evidence=["safetensors json header"],
        )
    if name.endswith((".engine", ".plan", ".trt")):
        return ArtifactInfo(
            kind="tensorrt", usability=SERVABLE, detail="A TensorRT engine.",
            next_step="Deploy it on a runtime with the same GPU, driver and TensorRT version "
                      "it was built with — an engine is not portable across those.",
            evidence=[f"{name.rsplit('.', 1)[-1]} extension"],
        )
    return ArtifactInfo(
        kind="unknown", usability=UNKNOWN,
        detail="Aperture could not tell what this file is.",
        next_step="Upload an ONNX graph, a TorchScript archive or a TensorRT engine.",
    )


def _inspect_zip(path: str, name: str) -> ArtifactInfo:
    with zipfile.ZipFile(path) as archive:
        entries = archive.namelist()

        if any(entry.endswith("constants.pkl") for entry in entries) and \
                any("/code/" in entry for entry in entries):
            return ArtifactInfo(
                kind="torchscript", usability=SERVABLE,
                detail="A TorchScript archive: weights and the traced graph together.",
                next_step="Validate it against a reference image, then publish.",
                evidence=["constants.pkl", "code/ in the archive"],
            )

        pickles = [entry for entry in entries if entry.endswith("data.pkl")]
        if not pickles:
            return ArtifactInfo(
                kind="zip", usability=UNKNOWN,
                detail="A zip archive that is not a recognised model format.",
                next_step="Upload an ONNX graph, a TorchScript archive or a TensorRT engine.",
                evidence=entries[:4],
            )

        with archive.open(pickles[0]) as handle:
            blob = handle.read(MAX_SCAN_BYTES)

    return _classify_torch_pickle(blob)


def _classify_torch_pickle(blob: bytes) -> ArtifactInfo:
    family, preset, snippet, evidence = None, None, None, []
    for marker, marker_family, marker_preset, marker_snippet in _FAMILIES:
        if marker in blob:
            family, preset, snippet = marker_family, marker_preset, marker_snippet
            evidence.append(marker.decode("ascii", "replace"))
            break

    if b"ultralytics" in blob:
        return ArtifactInfo(
            kind="ultralytics", usability=CONVERTIBLE, family=family or "yolo",
            suggested_preset=preset or _ultralytics_preset(blob),
            detail="An Ultralytics checkpoint: it carries the model object, the training "
                   "arguments and the class names.",
            next_step="Serve it as it is on a torch runtime (docker compose --profile torch "
                      "up -d runtime-torch) — placement sends .pt versions there. Converting to "
                      "ONNX is optional: it drops the torch dependency and usually runs faster, "
                      "and Aperture can do it for you.",
            export_snippet="from ultralytics import YOLO\n"
                           "YOLO('weights.pt').export(format='onnx', dynamic=True, opset=17)",
            evidence=evidence + ["ultralytics in the pickle"],
        )

    if family == "rf-detr":
        # A state_dict has no graph, but RF-DETR's own package can rebuild the
        # architecture from the layer shapes — so this one is served as it is on
        # a runtime that has `rfdetr`, and converted for anywhere else. Both
        # paths go through the same rebuild, so the ONNX and the .pt are the same
        # model; converting is for dropping the torch dependency, not for making
        # the weights work.
        segmentation = b"segmentation_head" in blob
        return ArtifactInfo(
            kind="state_dict", usability=SERVABLE, family=family,
            suggested_preset="rf-detr-seg" if segmentation else "rf-detr",
            detail=("An RF-DETR segmentation state_dict: tensors only, but the layer shapes "
                    "carry the architecture." if segmentation else
                    "An RF-DETR state_dict: tensors only, but the layer shapes carry the "
                    "architecture."),
            next_step="Deploy it on a runtime with the rfdetr package "
                      "(docker compose --profile torch up -d runtime-torch), which rebuilds the "
                      "model from the weights and serves it. Converting to ONNX is the other "
                      "option: same rebuild, no torch dependency, and usually faster.",
            export_snippet=snippet,
            evidence=evidence + (["segmentation_head"] if segmentation else []),
        )

    if family:
        return ArtifactInfo(
            kind="state_dict", usability=WEIGHTS_ONLY, family=family,
            suggested_preset=preset,
            detail=f"A {family} state_dict: tensors only, with no model graph. "
                   "The layer names identify the architecture, but the code that builds it "
                   "is not in the file.",
            next_step=f"Nothing can execute a state_dict on its own — not even a PyTorch "
                      f"runtime. Export ONNX from the {family} code that produced it and "
                      f"upload that. Once it is ONNX, start from the '{preset}' preset.",
            export_snippet=snippet,
            evidence=evidence,
        )

    if b"OrderedDict" in blob or b"state_dict" in blob:
        return ArtifactInfo(
            kind="state_dict", usability=WEIGHTS_ONLY,
            detail="A state_dict: tensors only, with no model graph.",
            next_step="Nothing can execute a state_dict on its own. Export ONNX or TorchScript "
                      "from the code that defines the model and upload that instead.",
            export_snippet=_GENERIC_EXPORT,
            evidence=["OrderedDict in the pickle"],
        )

    return ArtifactInfo(
        kind="torch_pickle", usability=CONVERTIBLE,
        detail="A pickled PyTorch object. It may carry the model, but loading it needs the "
               "classes it was defined with.",
        next_step="Convert it to ONNX, or export TorchScript from your training code — "
                  "TorchScript carries the graph and needs no extra packages to serve.",
        export_snippet="import torch\n"
                       "torch.jit.script(model).save('model.torchscript')",
    )


def _ultralytics_preset(blob: bytes) -> str:
    """Ultralytics ships one checkpoint shape per task; the head names give it away."""
    if b"SegmentationModel" in blob:
        return "yolov8-seg"
    if b"PoseModel" in blob:
        return "yolov8-pose"
    if b"ClassificationModel" in blob:
        return "resnet-imagenet"
    match = re.search(rb"yolo(?:v)?(\d+)", blob)
    if match and match.group(1) in (b"11", b"12"):
        return "yolov11"
    return "yolov8"


def _looks_onnx(path: str, head: bytes) -> bool:
    """Read the first two protobuf fields of a ModelProto.

    Searching for the string "onnx" does not work: a model exported with the
    default opset domain (the empty string) contains the word nowhere, which is
    true of both a torch export and an Ultralytics one. The wire format is a
    reliable signal instead - ir_version, then one of the known ModelProto
    fields.
    """
    if not head or head[0] != 0x08:  # field 1, varint: ir_version
        return False

    offset, shift, ir_version = 1, 0, 0
    while offset < len(head):
        byte = head[offset]
        ir_version |= (byte & 0x7F) << shift
        offset += 1
        if not byte & 0x80:
            break
        shift += 7
        if shift > 28:
            return False
    if not 1 <= ir_version <= 30 or offset >= len(head):
        return False

    # producer_name, producer_version, domain, model_version, doc_string,
    # graph, opset_import - whichever the producer wrote first
    return head[offset] in (0x12, 0x1A, 0x22, 0x28, 0x32, 0x3A, 0x42)


def _looks_safetensors(path: str, head: bytes) -> bool:
    if len(head) < 8:
        return False
    length = int.from_bytes(head[:8], "little")
    if not 2 <= length <= 100_000_000:
        return False
    with open(path, "rb") as fh:
        fh.seek(8)
        try:
            json.loads(fh.read(min(length, 1_000_000)).decode("utf-8"))
        except Exception:
            return False
    return True
