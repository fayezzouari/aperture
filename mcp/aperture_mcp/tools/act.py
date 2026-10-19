"""Tools that run something: a model, a workflow.

These do not mutate the registry, but they are not free either - a prediction
is traffic, is logged, may be captured, and with `ondemand` may start a
replica. The annotations say so.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.types import ToolAnnotations

from .. import trim
from ..errors import guarded
from ..images import ImageInput, decode_overlays, load_input
from ..state import AppState

ACT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                      open_world_hint=False)


def register_act_tools(mcp: MCPServer, state: AppState) -> None:
    client = state.client
    settings = state.settings
    guard = guarded(settings)

    @mcp.tool(annotations=ACT, structured_output=False)
    @guard
    async def predict(model: str, images: list[str], ref: str = "production",
                      overrides: dict[str, Any] | None = None,
                      inputs: dict[str, Any] | None = None, ondemand: bool = False,
                      include_overlay: bool = True, format: str | None = None,
                      workspace: str | None = None) -> list[str | Image]:
        """Run images through a model and see what it returns, drawn on the image.

        `model` is the slug; `ref` a tag (production, staging) or a version number.
        `images` are file paths on this machine, http(s) URLs, data: URLs or base64.
        `overrides` adjusts thresholds for this call only (e.g. {"score_threshold": 0.4});
        `inputs` feeds a model's extra non-image inputs by name. `ondemand=true` starts one
        replica for an undeployed version and waits for it (developer role); leave it off
        to be told the model is not deployed instead. `format="roboflow"` returns Roboflow's
        response shape instead of Aperture's. The overlay comes back as an image block."""
        loaded = _load_images(images, settings.max_images, settings.max_upload_bytes)
        body: dict[str, Any] = {"include_overlay": include_overlay}
        if loaded["base64"]:
            body["images"] = loaded["base64"]
        if loaded["urls"]:
            body["image_urls"] = loaded["urls"]
        if overrides:
            body["overrides"] = overrides
        if inputs:
            body["inputs"] = inputs
        params = {"ondemand": ondemand or None, "format": format}
        response = await client.post_json(f"/v1/models/{model}/{ref}/predict", body,
                                          params=params, workspace=workspace,
                                          timeout=settings.predict_timeout_s)
        if format == "roboflow":
            return [json.dumps(response, indent=1, default=str)]
        parts: list[str | Image] = [json.dumps(trim.prediction_summary(response), indent=1)]
        parts.extend(decode_overlays(response.get("overlays"), settings.max_image_px))
        return parts

    @mcp.tool(annotations=ACT, structured_output=False)
    @guard
    async def run_workflow(slug: str, images: list[str], params: dict[str, Any] | None = None,
                           draft: bool = False, format: str | None = None,
                           workspace: str | None = None) -> list[str | Image]:
        """Run images through a workflow (several models as one graph). `params` are the
        request fields gate nodes route on. `draft=true` runs the unpublished graph. Node
        previews, when the workflow produces them, come back as image blocks."""
        loaded = _load_images(images, settings.max_images, settings.max_upload_bytes)
        body: dict[str, Any] = {}
        if loaded["base64"]:
            body["images"] = loaded["base64"]
        if loaded["urls"]:
            body["image_urls"] = loaded["urls"]
        if params:
            body["params"] = params
        if draft:
            body["draft"] = True
        response = await client.post_json(f"/v1/workflows/{slug}/run", body,
                                          params={"format": format}, workspace=workspace,
                                          timeout=settings.predict_timeout_s)
        parts: list[str | Image] = [json.dumps(trim.workflow_run_summary(response), indent=1,
                                               default=str)]
        for previews in (response.get("previews") or {}).values():
            parts.extend(decode_overlays(previews, settings.max_image_px))
        return parts


def _load_images(sources: list[str], max_images: int, max_bytes: int) -> dict[str, list[str]]:
    if not sources:
        raise ValueError("pass at least one image (path, URL, data: URL or base64)")
    if len(sources) > max_images:
        raise ValueError(f"at most {max_images} images per call, got {len(sources)}")
    loaded: list[ImageInput] = [load_input(source) for source in sources]
    total = sum(item.size for item in loaded)
    if total > max_bytes:
        raise ValueError(f"images total {total / 1e6:.1f} MB, the limit is {max_bytes / 1e6:.0f} MB")
    return {
        "base64": [item.value for item in loaded if item.kind == "base64"],
        "urls": [item.value for item in loaded if item.kind == "url"],
    }
