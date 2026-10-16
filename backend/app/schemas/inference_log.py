"""Inference log rows and flagging them for review."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class InferenceLogOut(ORMModel):
    id: str
    request_id: str
    trace_id: str | None
    model_id: str | None
    model_slug: str
    version_id: str | None
    version_number: int | None
    tag: str | None
    route: str
    caller_type: str
    caller_label: str
    status: str
    http_status: int
    error_code: str | None
    error_message: str | None
    batch_size: int
    queue_ms: float
    preprocess_ms: float
    inference_ms: float
    postprocess_ms: float
    gateway_ms: float
    total_ms: float
    runtime_id: str | None
    detection_count: int
    confidence: dict
    input_stats: dict
    input_ref: dict
    overrides: dict
    flagged: bool
    flag_label: str | None
    created_at: datetime
    has_capture: bool = False


class InferenceLogDetail(InferenceLogOut):
    output: dict
    capture: dict
    image_urls: list[str] = Field(default_factory=list)
    overlay_urls: list[str] = Field(default_factory=list)


class FlagIn(BaseModel):
    flagged: bool = True
    label: str | None = Field(None, description="e.g. false_positive, missed_detection, wrong_class")
    note: str | None = None
