"""Importing a model version from Roboflow."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RoboflowImportIn(BaseModel):
    """R1.11 - pull a trained model out of a Roboflow project."""

    ref: str = Field(
        description="a Roboflow model reference: 'workspace/project/version', or the "
                    "app URL, or the model_id a workflow names",
    )
    slug: str | None = Field(
        None, pattern=r"^[a-z0-9][a-z0-9._-]{1,94}$",
        description="the Aperture model to land it under; defaults to the Roboflow project id. "
                    "An existing model gains a new version instead of being replaced.",
    )
    preset: str | None = Field(
        None, description="override the preset inferred from Roboflow's model type")
    api_key: str | None = Field(
        None, description="use this key instead of the instance's AP_ROBOFLOW_API_KEY")
    opset: int = Field(17, ge=11, le=21)
    changelog: str = ""
