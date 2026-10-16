"""Instance-wide settings: capture sampling and retention."""

from __future__ import annotations

from pydantic import BaseModel, Field


class InstanceSettingsPatch(BaseModel):
    # null clears an override, which falls back to the AP_CAPTURE_* default
    capture_sample_rate: float | None = Field(None, ge=0.0, le=1.0)
    capture_retention_days: int | None = Field(None, ge=1, le=3650)


class InstanceSettingsOut(BaseModel):
    capture_sample_rate: float | None
    capture_retention_days: int | None
    effective_sample_rate: float
    effective_retention_days: int
