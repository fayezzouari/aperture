"""The versioned pre/post-processing contract that travels with a model version.

R1.4: this object is stored *on the model version row*, never globally, so a new
version can change its own preprocessing without touching what production
serves. R1.6 copies it forward as the starting point for the next version.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_SCHEMA_VERSION = 1

# R3.6: only these may be overridden per request. Anything structural (resize
# mode, normalisation, layout) would change the meaning of the weights and is
# therefore deploy-time only.
OVERRIDABLE_THRESHOLDS = (
    "score_threshold",
    "iou_threshold",
    "top_k",
    "max_detections",
    "mask_threshold",
    "class_agnostic_nms",
)


class Task(str, Enum):
    detection = "detection"
    classification = "classification"
    segmentation = "segmentation"
    keypoints = "keypoints"
    ocr = "ocr"
    embedding = "embedding"


class Backend(str, Enum):
    onnx = "onnx"
    torchscript = "torchscript"
    torch = "torch"
    tensorrt = "tensorrt"


class Decoder(str, Enum):
    """How raw output tensors become the typed response of R3.4."""

    yolo_v5 = "yolo_v5"
    yolo_v8 = "yolo_v8"  # also v9/v10/v11 - same anchor-free head
    yolo_seg = "yolo_seg"
    yolo_pose = "yolo_pose"
    detr = "detr"  # DETR / RF-DETR / Deformable-DETR
    detr_seg = "detr_seg"  # the same, plus a per-query mask head (RF-DETR Seg)
    classifier_softmax = "classifier_softmax"
    classifier_sigmoid = "classifier_sigmoid"
    classifier_probs = "classifier_probs"  # the head already applied softmax (YOLO-cls)
    embedding = "embedding"
    ctc_greedy = "ctc_greedy"  # OCR recognition heads (PaddleOCR rec, CRNN)
    binary_mask = "binary_mask"  # SAM / U-Net style single-mask output
    passthrough = "passthrough"


class ResizeMode(str, Enum):
    letterbox = "letterbox"  # aspect preserved, padded - YOLO default
    stretch = "stretch"
    shortest_side = "shortest_side"
    center_crop = "center_crop"  # resize shortest side then crop - ImageNet default
    none = "none"


class InputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(640, ge=1, le=8192)
    height: int = Field(640, ge=1, le=8192)
    channels: int = Field(3, ge=1, le=4)
    layout: Literal["NCHW", "NHWC"] = "NCHW"
    dtype: Literal["float32", "float16", "uint8", "int8"] = "float32"
    color: Literal["RGB", "BGR", "GRAY"] = "RGB"
    dynamic_shape: bool = False
    input_names: list[str] = Field(default_factory=list)
    output_names: list[str] = Field(default_factory=list)


class ResizeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: ResizeMode = ResizeMode.letterbox
    width: int = Field(640, ge=1, le=8192)
    height: int = Field(640, ge=1, le=8192)
    pad_value: int = Field(114, ge=0, le=255)
    interpolation: Literal["bilinear", "bicubic", "nearest", "area"] = "bilinear"
    resize_side: int | None = Field(None, ge=1, le=8192, description="shortest_side/center_crop only")


class NormalizeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scale: float = Field(1 / 255.0, description="applied before mean/std")
    mean: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    std: list[float] = Field(default_factory=lambda: [1.0, 1.0, 1.0])


class PreprocessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resize: ResizeConfig = Field(default_factory=ResizeConfig)
    normalize: NormalizeConfig = Field(default_factory=NormalizeConfig)
    grayscale: bool = False
    auto_orient: bool = True  # honour EXIF rotation - a very common source of skew


class PostprocessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decoder: Decoder = Decoder.yolo_v8
    score_threshold: float = Field(0.25, ge=0.0, le=1.0)
    iou_threshold: float = Field(0.45, ge=0.0, le=1.0)
    top_k: int = Field(300, ge=1, le=10000)
    max_detections: int = Field(300, ge=1, le=10000)
    class_agnostic_nms: bool = False
    nms: bool = True
    normalized_boxes: bool = False
    box_format: Literal["xyxy", "xywh", "cxcywh"] = "cxcywh"
    sigmoid_logits: bool = False
    class_offset: int = Field(
        0, ge=0, le=8,
        description="Leading logit columns that are not classes. RF-DETR's ONNX "
                    "exporter prepends a dead background slot (zero weights, bias "
                    "-20), so its graphs need 1 - with 0 every class id is one too high.",
    )
    mask_format: Literal["rle", "polygon", "none"] = "rle"
    mask_threshold: float = Field(0.5, ge=0.0, le=1.0)
    normalize_embedding: bool = True
    charset: list[str] = Field(default_factory=list, description="ctc_greedy only")
    keypoint_names: list[str] = Field(default_factory=list)
    output_index: dict[str, int] = Field(
        default_factory=dict,
        description="maps a decoder role ('boxes', 'logits', 'proto') to an output tensor index",
    )


class HookConfig(BaseModel):
    """R1.9 - user supplied Python for models the built-in decoders can't express."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    preprocess_source: str | None = None
    postprocess_source: str | None = None
    timeout_ms: int = Field(2000, ge=100, le=60000)
    memory_mb: int = Field(512, ge=64, le=8192)


class WarmupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iterations: int = Field(3, ge=0, le=50)
    batch_sizes: list[int] = Field(default_factory=lambda: [1])


class ModelConfig(BaseModel):
    """The full, self-contained recipe for turning bytes into predictions."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = CONFIG_SCHEMA_VERSION
    task: Task = Task.detection
    backend: Backend = Backend.onnx
    input: InputSpec = Field(default_factory=InputSpec)
    preprocess: PreprocessConfig = Field(default_factory=PreprocessConfig)
    postprocess: PostprocessConfig = Field(default_factory=PostprocessConfig)
    classes: list[str] = Field(default_factory=list)
    hook: HookConfig = Field(default_factory=HookConfig)
    warmup: WarmupConfig = Field(default_factory=WarmupConfig)
    notes: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _coherent(self) -> "ModelConfig":
        n = len(self.preprocess.normalize.mean)
        if n != len(self.preprocess.normalize.std):
            raise ValueError("normalize.mean and normalize.std must have the same length")
        if not self.preprocess.grayscale and self.input.channels == 3 and n not in (1, 3):
            raise ValueError("normalize.mean must have 1 or 3 entries for a 3-channel input")
        if self.postprocess.decoder == Decoder.ctc_greedy and not self.postprocess.charset:
            raise ValueError("ctc_greedy decoder requires postprocess.charset")
        if self.task in (Task.detection, Task.segmentation) and not self.classes:
            # not fatal: class ids still come back, names are just absent
            pass
        return self

    def class_name(self, class_id: int) -> str:
        if 0 <= class_id < len(self.classes):
            return self.classes[class_id]
        return f"class_{class_id}"

    def apply_overrides(self, overrides: dict[str, Any] | None) -> "ModelConfig":
        """R3.6 - per-request threshold tuning, never mutating the deployed config."""
        if not overrides:
            return self
        clean = {k: v for k, v in overrides.items() if k in OVERRIDABLE_THRESHOLDS and v is not None}
        if not clean:
            return self
        return self.model_copy(
            update={"postprocess": self.postprocess.model_copy(update=clean)}, deep=True
        )
