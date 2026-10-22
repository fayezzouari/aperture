"""The shared HTTP server, driven over real streamable-HTTP requests in-process."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from aperture_mcp.client import ApertureClient
from aperture_mcp.remote import TokenCheck, build_app
from aperture_mcp.settings import ConfigError, Settings, load_remote_settings

from . import fixtures as fx
from .conftest import TOKEN

API_KEY = "ap_1234abcd.secret"
ACCEPT = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.fixture
def remote_settings() -> Settings:
    return Settings(url="http://api.test", token="", remote=True,
                    public_url="https://aperture.example")


@asynccontextmanager
async def running(api, settings: Settings):
    transport = httpx.MockTransport(api.handler)
    client = ApertureClient(settings, transport=transport)
    check = TokenCheck(settings, transport=transport)
    gate = build_app(settings, client=client, check=check)
    inner = gate.app
    async with (inner.router.lifespan_context(inner),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=gate),
                                  base_url="http://mcp.test") as http):
        yield http
    await client.aclose()
    await check.aclose()


async def rpc(http: httpx.AsyncClient, method: str, params: dict | None = None, *,
              token: str | None = TOKEN, workspace: str | None = None) -> httpx.Response:
    headers = dict(ACCEPT)
    if token:
        headers["authorization"] = f"Bearer {token}"
    if workspace:
        headers["x-aperture-workspace"] = workspace
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    if method != "initialize":
        headers["mcp-protocol-version"] = "2025-06-18"
    return await http.post("/mcp", json=body, headers=headers)


INIT = {"protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"}}


async def test_no_token_is_refused_before_mcp(api, remote_settings):
    async with running(api, remote_settings) as http:
        response = await rpc(http, "initialize", INIT, token=None)
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer")
    assert ('resource_metadata="https://aperture.example/.well-known/'
            'oauth-protected-resource/mcp"') in challenge
    assert "https://aperture.example/account/tokens" in response.json()["error"]["message"]
    assert api.calls == []


@pytest.mark.parametrize("path", ["/.well-known/oauth-protected-resource",
                                  "/.well-known/oauth-protected-resource/mcp"])
async def test_resource_metadata_names_aperture_as_the_authorization_server(
        api, remote_settings, path):
    async with running(api, remote_settings) as http:
        response = await http.get(path)
    assert response.status_code == 200
    meta = response.json()
    assert meta["resource"] == "https://aperture.example/mcp"
    assert meta["authorization_servers"] == ["https://aperture.example"]


async def test_revoked_token_fails_at_connect(api, remote_settings):
    api.routes[("GET", "/v1/auth/me")] = httpx.Response(401, json={
        "error": {"code": "token_revoked", "message": "revoked", "request_id": "r1"}})
    async with running(api, remote_settings) as http:
        response = await rpc(http, "initialize", INIT)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_revoked"
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


async def test_health_needs_no_token(api, remote_settings):
    async with running(api, remote_settings) as http:
        response = await http.get("/healthz")
    assert response.status_code == 200


async def test_initialize_and_full_tool_list_for_a_person(api, remote_settings):
    async with running(api, remote_settings) as http:
        init = await rpc(http, "initialize", INIT)
        assert init.status_code == 200, init.text
        assert init.json()["result"]["serverInfo"]["name"] == "aperture"
        listed = await rpc(http, "tools/list")
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert {"list_models", "get_inference", "predict", "run_workflow"} <= names


async def test_api_key_sees_only_inference_tools(api, remote_settings):
    async with running(api, remote_settings) as http:
        listed = await rpc(http, "tools/list", token=API_KEY)
    names = sorted(tool["name"] for tool in listed.json()["result"]["tools"])
    assert names == ["predict", "run_workflow"]


async def test_each_call_carries_its_callers_token_and_workspace(api, remote_settings):
    api.auth_expected = None
    async with running(api, remote_settings) as http:
        first = await rpc(http, "tools/call", {"name": "list_models", "arguments": {}},
                          workspace="cloud")
        sent_first = api.last()
        second = await rpc(http, "tools/call", {"name": "list_models", "arguments": {}},
                           token="ap_pat_other.secret")
        sent_second = api.last()
    assert first.json()["result"]["isError"] is False
    assert second.json()["result"]["isError"] is False
    assert sent_first.headers["authorization"] == f"Bearer {TOKEN}"
    assert sent_first.headers["x-aperture-workspace"] == "cloud"
    assert sent_second.headers["authorization"] == "Bearer ap_pat_other.secret"
    assert "x-aperture-workspace" not in sent_second.headers


async def test_predict_refuses_server_side_paths(api, remote_settings):
    async with running(api, remote_settings) as http:
        response = await rpc(http, "tools/call", {"name": "predict", "arguments": {
            "model": "part-detection", "images": [__file__]}})
    result = response.json()["result"]
    assert result["isError"] is True
    assert "cannot read files on your machine" in result["content"][0]["text"]
    assert not any(call.url.path.endswith("/predict") for call in api.calls)


async def test_predict_by_url_still_works(api, remote_settings):
    async with running(api, remote_settings) as http:
        response = await rpc(http, "tools/call", {"name": "predict", "arguments": {
            "model": "part-detection", "images": ["https://example.com/part.jpg"]}})
    result = response.json()["result"]
    assert result["isError"] is False, result
    assert api.last_json()["image_urls"] == ["https://example.com/part.jpg"]
    assert any(block["type"] == "image" for block in result["content"])


async def test_rejected_token_hint_names_the_header(api, remote_settings):
    api.routes[("GET", "/v1/models")] = httpx.Response(401, json={
        "error": {"code": "token_expired", "message": "expired", "request_id": "r2"}})
    async with running(api, remote_settings) as http:
        response = await rpc(http, "tools/call", {"name": "list_models", "arguments": {}})
    text = response.json()["result"]["content"][0]["text"]
    assert "Authorization header" in text and "https://aperture.example/account/tokens" in text
    assert TOKEN not in text


async def test_presigned_capture_goes_to_the_internal_endpoint():
    seen: list[httpx.Request] = []

    def store(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=fx.SMALL_PNG)

    settings = Settings(url="http://api.test", token="", remote=True,
                        s3_public_endpoint="http://localhost:9000",
                        s3_endpoint="http://minio:9000")
    client = ApertureClient(settings, transport=httpx.MockTransport(store))
    await client.fetch_bytes("http://localhost:9000/captures/a.png?X-Amz-Signature=abc")
    await client.fetch_bytes("https://cdn.example/b.png")
    await client.aclose()
    assert str(seen[0].url) == "http://minio:9000/captures/a.png?X-Amz-Signature=abc"
    assert seen[0].headers["host"] == "localhost:9000"
    assert seen[1].url.host == "cdn.example"


def test_remote_settings_from_env():
    settings = load_remote_settings({"APERTURE_URL": "http://api:8000/",
                                     "APERTURE_PUBLIC_URL": "https://aperture.example",
                                     "APERTURE_MCP_PORT": "9001"})
    assert settings.remote and settings.token == "" and settings.port == 9001
    assert settings.url == "http://api:8000" and settings.console_url == "https://aperture.example"
    with pytest.raises(ConfigError, match="APERTURE_URL"):
        load_remote_settings({})
    with pytest.raises(ConfigError, match="both"):
        load_remote_settings({"APERTURE_URL": "http://api:8000",
                              "APERTURE_S3_ENDPOINT": "http://minio:9000"})

