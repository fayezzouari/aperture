"""Validating a pre- or postprocess hook."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HookValidateIn(BaseModel):
    source: str
    kind: Literal["preprocess", "postprocess"] = "postprocess"


class HookValidateOut(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)
