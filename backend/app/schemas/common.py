"""Shared pieces: the role literal and the ORM-backed base model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Role = Literal["viewer", "developer", "admin"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
