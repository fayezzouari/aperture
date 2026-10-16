"""A fake Aperture behind httpx.MockTransport, and the server built on top of it."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from aperture_mcp.client import ApertureClient
from aperture_mcp.server import build_server
from aperture_mcp.settings import Settings

from . import fixtures as fx

TOKEN = "ap_pat_0badf00d.secret"


@dataclass
class FakeApi:
    """Routes (method, path) to a canned body; records what the server sent."""

    calls: list[httpx.Request] = field(default_factory=list)
    routes: dict[tuple[str, str], object] = field(default_factory=dict)
    auth_expected: str | None = f"Bearer {TOKEN}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host == "minio.test":
            assert "authorization" not in request.headers, "presigned GETs must be anonymous"
            if request.url.path.endswith(".jpg"):
                return httpx.Response(200, content=fx.BIG_JPEG,
                                      headers={"content-type": "image/jpeg"})
            return httpx.Response(200, content=fx.SMALL_PNG,
                                  headers={"content-type": "image/png"})
        if self.auth_expected:
            assert request.headers.get("authorization") == self.auth_expected
        body = self.routes.get((request.method, request.url.path))
        if body is None:
            return httpx.Response(404, json={"error": {"code": "not_found",
                                                       "message": f"no route {request.url.path}",
                                                       "request_id": "req-404"}})
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)

    def last(self) -> httpx.Request:
        return self.calls[-1]

    def last_json(self) -> dict:
        return json.loads(self.last().content)


@pytest.fixture
def api() -> FakeApi:
    fake = FakeApi()
    fake.routes.update({
        ("GET", "/v1/auth/me"): fx.ME,
        ("GET", "/v1/models"): [fx.MODEL],
        ("GET", "/v1/models/part-detection"): fx.MODEL,
        ("GET", "/v1/models/part-detection/versions"): [fx.VERSION],
        ("GET", "/v1/models/part-detection/versions/3"): fx.VERSION,
        ("GET", "/v1/models/part-detection/deployments"): [fx.DEPLOYMENT],
        ("GET", "/v1/overview"): fx.OVERVIEW,
        ("GET", "/v1/inferences"): [fx.INFERENCE] * 3,
        ("GET", "/v1/inferences/log1"): fx.INFERENCE_DETAIL,
        ("GET", "/v1/meta/presets"): fx.PRESETS,
        ("GET", "/v1/meta/presets/yolov11"): fx.VERSION["config"],
        ("GET", "/api/openapi.json"): fx.OPENAPI,
        ("POST", "/v1/models/part-detection/production/predict"): fx.PREDICT_RESPONSE,
        ("POST", "/v1/models/part-detection/deployments"): httpx.Response(403, json=fx.ERROR_403),
    })
    return fake


@pytest.fixture
def settings() -> Settings:
    return Settings(url="http://api.test", token=TOKEN)


@pytest.fixture
async def server(api: FakeApi, settings: Settings):
    client = ApertureClient(settings, transport=httpx.MockTransport(api.handler))
    yield build_server(settings, client=client)
    await client.aclose()


async def call(server, tool: str, /, **arguments):
    return await server.call_tool(tool, arguments)


async def call_error(server, tool: str, /, **arguments) -> str:
    """In-process call_tool raises the ToolError the wire would carry as is_error."""
    with pytest.raises(ToolError) as caught:
        await server.call_tool(tool, arguments)
    return str(caught.value)
