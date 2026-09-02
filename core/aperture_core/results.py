"""R3.4 - the typed response contract, identical for every task.

Products integrate against these shapes, not against whatever a given
architecture happens to emit, which is what makes swapping YOLO11 for RF-DETR a
retag rather than a client release.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Mask(BaseModel):
    format: Literal["rle", "polygon"]
    size: list[int] = Field(description="[height, width] of the mask frame, in original image pixels")
    counts: str | None = Field(None, description="COCO-style column-major RLE, ascii encoded")
    polygons: list[list[float]] | None = Field(
        None, description="flat [x1,y1,x2,y2,...] rings in original image pixels"
    )
    area: float | None = None


class Keypoint(BaseModel):
    name: str | None = None
    x: float
    y: float
    score: float


class Detection(BaseModel):
    box: list[float] = Field(description="[x1, y1, x2, y2] in original image pixels")
    score: float
    class_id: int
    class_name: str
    mask: Mask | None = None
    keypoints: list[Keypoint] | None = None


class Classification(BaseModel):
    class_id: int
    class_name: str
    score: float


class TextRegion(BaseModel):
    text: str
    score: float
    box: list[float] | None = None


class ImageResult(BaseModel):
    """One entry per input image, in request order."""

    index: int
    width: int
    height: int
    detections: list[Detection] | None = None
    classifications: list[Classification] | None = None
    segments: list[Detection] | None = None
    texts: list[TextRegion] | None = None
    embedding: list[float] | None = None
    embedding_dim: int | None = None
    raw: dict | None = Field(
        None, description="passthrough decoder only: output tensor names, shapes and values"
    )
    error: str | None = Field(
        None, description="set when this one image failed inside an otherwise successful batch"
    )


class LatencyBreakdown(BaseModel):
    """R5.2 - every stage, every response. The UI renders this as a waterfall."""

    queue_ms: float = 0.0
    preprocess_ms: float = 0.0
    inference_ms: float = 0.0
    postprocess_ms: float = 0.0
    runtime_ms: float = 0.0
    gateway_ms: float = 0.0
    total_ms: float = 0.0


class PredictResponse(BaseModel):
    request_id: str
    model: str
    model_version: int = Field(description="R3.5 - the version actually served, even via a tag")
    version_id: str
    tag: str | None = None
    task: str
    batch_size: int
    results: list[ImageResult]
    latency: LatencyBreakdown
    runtime_id: str | None = None
    trace_id: str | None = None
    overrides_applied: dict[str, float | int | bool] = Field(default_factory=dict)
    overlays: list[str] | None = Field(
        None,
        description="base64 PNG overlays, one per image; only when include_overlay was requested",
    )


class ErrorBody(BaseModel):
    """R3.7 - one error shape for the whole API."""

    code: str = Field(description="stable machine-readable code, e.g. model_not_found")
    message: str
    request_id: str | None = None
    details: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
