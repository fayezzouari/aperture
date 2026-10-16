"""Alert rules."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class AlertRuleIn(BaseModel):
    name: str
    metric: Literal["error_rate", "p95_latency_ms", "p99_latency_ms", "no_traffic",
                    "replica_down", "confidence_drop"]
    model_id: str | None = None
    version_id: str | None = None
    comparator: Literal[">", "<"] = ">"
    threshold: float = 0.0
    window_minutes: int = Field(5, ge=1, le=1440)
    min_samples: int = Field(20, ge=1, le=100000)
    channel: Literal["slack", "email", "webhook"] = "webhook"
    destination: str = ""
    enabled: bool = True


class AlertRuleOut(ORMModel):
    id: str
    name: str
    metric: str
    model_id: str | None
    version_id: str | None
    comparator: str
    threshold: float
    window_minutes: int
    min_samples: int
    channel: str
    destination: str
    enabled: bool
    state: str
    last_value: float | None
    last_fired_at: datetime | None
    last_checked_at: datetime | None
