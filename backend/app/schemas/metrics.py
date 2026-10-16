"""Latency and throughput time series, summaries and usage rows."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TimeseriesPoint(BaseModel):
    ts: datetime
    requests: int = 0
    errors: int = 0
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    images: int = 0


class MetricsSummary(BaseModel):
    window_minutes: int
    requests: int
    errors: int
    error_rate: float
    images: int
    rps: float
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    stages: dict[str, float]
    by_status: dict[str, int]
    series: list[TimeseriesPoint]


class UsageRow(BaseModel):
    key: str
    label: str
    requests: int
    images: int
    errors: int
    error_rate: float
    p95_ms: float | None
    avg_ms: float | None
