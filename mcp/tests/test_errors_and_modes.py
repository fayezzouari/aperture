from __future__ import annotations

import httpx
import pytest

from aperture_mcp.client import ApertureClient, ApertureError
from aperture_mcp.errors import to_tool_error
from aperture_mcp.server import build_server
from aperture_mcp.settings import ConfigError, Settings, load_settings

from . import fixtures as fx
from .conftest import call_error


async def test_forbidden_carries_code_request_id_and_hint(server, api):
    api.routes[("GET", "/v1/models")] = httpx.Response(403, json=fx.ERROR_403)
    text = await call_error(server, "list_models")
    assert "insufficient_role: this action needs the 'developer' role" in text
    assert "(request req-403)" in text and "Aperture admin" in text


async def test_rejected_token_points_at_the_tokens_page(settings):
    error = ApertureError(401, "token_revoked", "this personal access token was revoked", "r1")
    text = str(to_tool_error(error, settings))
    assert "http://api.test/account/tokens" in text
    assert settings.token not in text


async def test_unreachable_api(settings):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = ApertureClient(settings, transport=httpx.MockTransport(down))
    server = build_server(settings, client=client)
    text = await call_error(server, "list_models")
    assert "unreachable: cannot reach APERTURE_URL=http://api.test" in text


async def test_non_json_error_bodies_do_not_crash(settings):
    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway</html>")

    client = ApertureClient(settings, transport=httpx.MockTransport(html))
    with pytest.raises(ApertureError) as caught:
        await client.get("/v1/models")
    assert caught.value.code == "http_502"


async def test_api_key_mode_offers_only_inference_tools():
    server = build_server(Settings(url="http://api.test", token="ap_1234abcd.secret"))
    assert sorted(t.name for t in await server.list_tools()) == ["predict", "run_workflow"]
    assert "API key" in (server.instructions or "")


async def test_pat_mode_offers_everything(server):
    names = {t.name for t in await server.list_tools()}
    assert {"list_models", "get_overview", "search_inferences", "predict",
            "run_workflow", "search_audit"} <= names
    for tool in await server.list_tools():
        read_only = tool.annotations.read_only_hint
        assert read_only is (tool.name not in {"predict", "run_workflow"}), tool.name


def test_settings_from_env():
    settings = load_settings({"APERTURE_URL": "http://localhost:8080/",
                              "APERTURE_TOKEN": "ap_pat_x.y"})
    assert settings.url == "http://localhost:8080" and settings.mode == "pat"
    assert load_settings({"APERTURE_URL": "http://x", "APERTURE_TOKEN": "eyJ..."}).mode == "session"
    with pytest.raises(ConfigError, match="APERTURE_TOKEN"):
        load_settings({"APERTURE_URL": "http://x"})
    with pytest.raises(ConfigError, match="APERTURE_URL"):
        load_settings({"APERTURE_TOKEN": "ap_pat_x.y"})
