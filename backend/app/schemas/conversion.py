"""Converting uploaded weights into a servable artifact."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ConversionIn(BaseModel):
    """R1.10 - queue an export/optimisation and land the result as a new version."""

    target: Literal["onnx", "onnx_fp16", "onnx_int8", "tensorrt_fp16", "tensorrt_int8"]
    opset: int = Field(17, ge=11, le=21)
    calibration_keys: list[str] = Field(default_factory=list)
    workspace_mb: int = Field(4096, ge=256, le=65536)
    changelog: str = ""
