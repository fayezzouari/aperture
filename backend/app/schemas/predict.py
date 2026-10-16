"""The JSON body of a predict request."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PredictJSON(BaseModel):
    """JSON body form of a prediction request (R3.2)."""

    images: list[str] | None = Field(
        None, description="base64 payloads or data URLs, one per image"
    )
    image_urls: list[str] | None = Field(None, description="remote URLs the server fetches")
    image: str | None = Field(None, description="convenience single-image alias")
    image_url: str | None = None
    overrides: dict[str, Any] | None = Field(
        None, description="per-request threshold overrides (R3.6)"
    )
    inputs: dict[str, Any] | None = Field(
        None,
        description="named non-image inputs for a multi-input model, one row per image "
                    '(or a single row applied to all of them), e.g. {"aux": [0.5, 0.5, 0.2, 0.3]}',
    )
    extra: dict[str, Any] | None = Field(None, description="passed through to a hook")
    include_overlay: bool = False
    capture: bool | None = Field(None, description="force payload capture on or off")
    format: str | None = Field(
        None, description="'aperture' (default) or 'roboflow' for a Roboflow-shaped response"
    )
