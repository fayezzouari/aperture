"""Model versions: creation, edits, validation results and comparisons."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from aperture_core.configspec import ModelConfig
from pydantic import BaseModel, Field

from app.schemas.common import ORMModel
from app.schemas.deployment import DeploymentOut


class VersionCreate(BaseModel):
    """Metadata for a new version. Weights arrive through the upload endpoints."""

    preset: str | None = Field(None, description="config preset to start from (R1.5)")
    inherit_from_version: int | None = Field(
        None, description="copy this version's config instead of a preset (R1.6)"
    )
    config: ModelConfig | None = None
    artifact_format: Literal["onnx", "pt", "torchscript", "safetensors", "tensorrt", "other"] = "onnx"
    training_run_id: str | None = None
    dataset_version: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    git_commit: str | None = None
    changelog: str = ""


class VersionPatch(BaseModel):
    config: ModelConfig | None = None
    training_run_id: str | None = None
    dataset_version: str | None = None
    metrics: dict[str, float] | None = None
    git_commit: str | None = None
    changelog: str | None = None


class ValidationOut(BaseModel):
    status: str = "pending"  # pending | running | passed | failed
    checked_at: datetime | None = None
    overlay_url: str | None = None
    sample_name: str | None = None
    timings: dict[str, float] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class VersionOut(ORMModel):
    id: str
    model_id: str
    version: int
    status: str
    preset: str | None
    artifact_format: str
    weights_bytes: int
    weights_sha256: str | None
    artifact_info: dict = Field(default_factory=dict)
    config: dict
    training_run_id: str | None
    dataset_version: str | None
    metrics: dict
    git_commit: str | None
    changelog: str
    source: str
    parent_version_id: str | None
    validation: dict
    created_by: str | None
    created_at: datetime
    published_at: datetime | None
    deprecated_at: datetime | None
    last_served_at: datetime | None
    tags: list[str] = Field(default_factory=list)
    deployment: DeploymentOut | None = None


class VersionCompare(BaseModel):
    """R2.5 - what the side-by-side view renders."""

    left: VersionOut
    right: VersionOut
    config_diff: list[dict[str, Any]]
    metric_diff: dict[str, dict[str, float | None]]
    traffic: dict[str, dict[str, float | None]]
