"""The overview page."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.alert import AlertRuleOut
from app.schemas.inference_log import InferenceLogOut
from app.schemas.metrics import MetricsSummary
from app.schemas.runtime import RuntimeOut


class OverviewOut(BaseModel):
    models: int
    versions: int
    deployments: int
    replicas_ready: int
    replicas_desired: int
    runtimes: list[RuntimeOut]
    metrics: MetricsSummary
    recent_errors: list[InferenceLogOut]
    firing_alerts: list[AlertRuleOut]
    log_stream_depth: int
