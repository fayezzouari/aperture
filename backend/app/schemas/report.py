"""One-off and scheduled reports."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReportRequest(BaseModel):
    kind: Literal["usage", "errors", "latency", "flagged"] = "usage"
    group_by: Literal["model", "version", "consumer", "day", "error_code"] = "model"
    since_hours: int = Field(168, ge=1, le=24 * 400)
    model_id: str | None = None
    fmt: Literal["json", "csv", "pdf"] = "json"


class ReportScheduleIn(BaseModel):
    name: str
    kind: Literal["usage", "errors", "latency", "flagged"] = "usage"
    params: dict = Field(default_factory=dict)
    fmt: Literal["csv", "pdf"] = "csv"
    cron: str | None = Field(None, description="m h dom mon dow, evaluated in UTC")
    channel: Literal["none", "slack", "email", "webhook"] = "none"
    destination: str = ""
    enabled: bool = True
