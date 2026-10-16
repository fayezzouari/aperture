"""Model presets offered at registration."""

from __future__ import annotations

from pydantic import BaseModel


class PresetOut(BaseModel):
    name: str
    label: str
    family: str
    task: str
    description: str
