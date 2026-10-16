"""Resources: reference material the client can attach without a tool call."""

from __future__ import annotations

import json
from typing import Any

from mcp.server import MCPServer

from . import trim
from .state import AppState

JSON = "application/json"


def register_resources(mcp: MCPServer, state: AppState) -> None:
    client = state.client

    @mcp.resource("aperture://me", mime_type=JSON,
                  description="Who this token signs in as and the instance role it carries")
    async def me() -> str:
        return _dump(trim.user_row(await client.get("/v1/auth/me")))

    @mcp.resource("aperture://models/{ref}", mime_type=JSON,
                  description="A model card: tags, versions and live deployments")
    async def model_card(ref: str) -> str:
        model = await client.get(f"/v1/models/{ref}")
        versions = await client.get(f"/v1/models/{ref}/versions")
        deployments = await client.get(f"/v1/models/{ref}/deployments")
        return _dump({
            "model": trim.model_row(model),
            "versions": [trim.version_row(v) for v in versions],
            "deployments": [trim.deployment_row(d) for d in deployments],
        })

    @mcp.resource("aperture://models/{ref}/versions/{version}/config", mime_type=JSON,
                  description="The inference config stored on one model version")
    async def version_config(ref: str, version: str) -> str:
        row = await client.get(f"/v1/models/{ref}/versions/{version}")
        return _dump(row.get("config") or {})

    @mcp.resource("aperture://presets/{name}", mime_type=JSON,
                  description="The default config of an architecture preset")
    async def preset(name: str) -> str:
        return _dump(await client.get(f"/v1/meta/presets/{name}"))

    @mcp.resource("aperture://schema/config", mime_type=JSON,
                  description="JSON schema of a version's config (ModelConfig and friends)")
    async def config_schema() -> str:
        document = await client.openapi()
        return _dump(extract_config_schema(document))

    @mcp.resource("aperture://openapi", mime_type=JSON,
                  description="Aperture's OpenAPI document")
    async def openapi() -> str:
        return _dump(await client.openapi())


def extract_config_schema(document: dict[str, Any]) -> dict[str, Any]:
    """ModelConfig plus every schema it references, resolved transitively."""
    schemas = (document.get("components") or {}).get("schemas") or {}
    if "ModelConfig" not in schemas:
        return {"error": "ModelConfig is not in this OpenAPI document"}
    wanted: dict[str, Any] = {}
    queue = ["ModelConfig"]
    while queue:
        name = queue.pop()
        if name in wanted or name not in schemas:
            continue
        wanted[name] = schemas[name]
        queue.extend(_refs(schemas[name]))
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": "#/$defs/ModelConfig", "$defs": wanted}


def _refs(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.append(ref.rsplit("/", 1)[-1])
        for value in node.values():
            found.extend(_refs(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_refs(value))
    return found


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=1, default=str)
