"""Input drift against a baseline window."""

from __future__ import annotations

from pydantic import BaseModel


class DriftOut(BaseModel):
    version_id: str
    baseline: dict | None
    current: dict | None
    windows: list[dict]
    signals: list[dict]
