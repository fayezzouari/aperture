from __future__ import annotations

from .conftest import call


async def test_list_models_is_trimmed_and_paged(server, api):
    result = await call(server, "list_models", limit=1)
    page = result.structured_content
    assert page["count"] == 1 and page["next_offset"] == 1
    row = page["items"][0]
    assert row["slug"] == "part-detection"
    assert row["tags"] == {"production": 3}
    assert row["p95_ms_24h"] == 71.5
    assert "workspace_id" not in row and "created_at" not in row
    assert api.last().url.params["limit"] == "1"
    assert api.last().headers["x-aperture-workspace"] == "cloud"


async def test_workspace_override_travels_as_a_header(server, api):
    await call(server, "list_models", workspace="edge")
    assert api.last().headers["x-aperture-workspace"] == "edge"


async def test_list_workspaces_names_the_default(server, api):
    out = (await call(server, "list_workspaces")).structured_content
    assert out["default"] == "cloud"
    assert out["items"] == [{"slug": "cloud", "name": "Cloud", "description": "",
                             "model_count": 2, "member_count": 3, "role": "developer"}]


async def test_unset_filters_are_not_sent(server, api):
    await call(server, "list_models", deployed=True)
    params = api.last().url.params
    assert params["deployed"] == "true"
    assert "q" not in params and "tag" not in params


async def test_get_model_is_a_card(server):
    card = (await call(server, "get_model", ref="part-detection")).structured_content
    assert card["model"]["slug"] == "part-detection"
    assert card["versions"][0]["status"] == "published"
    assert "config" not in card["versions"][0]  # the list view stays light
    assert card["deployments"][0]["replicas"] == {"desired": 2, "ready": 2,
                                                  "by_state": {"ready": 2}}


async def test_get_version_carries_config_and_a_short_validation(server):
    detail = (await call(server, "get_version", ref="part-detection", version=3)).structured_content
    assert detail["config"]["classes"] == ["bumper", "door"]
    assert detail["validation"] == {"status": "ready", "errors": [], "warnings": []}
    detail = (await call(server, "get_version", ref="part-detection", version=3,
                         include_config=False)).structured_content
    assert "config" not in detail


async def test_overview_drops_runtime_metrics_and_series(server):
    view = (await call(server, "get_overview")).structured_content
    assert view["models"] == 2
    assert view["runtimes"][0]["loaded"] == [{"model": "part-detection", "version": 3,
                                              "state": "ready"}]
    assert "metrics" not in view["runtimes"][0]
    assert "series" not in view["metrics"]
    assert view["recent_errors"][0]["error_code"] == "timeout"


async def test_search_inferences_rows(server, api):
    page = (await call(server, "search_inferences", status="ok", has_capture=True,
                       limit=3)).structured_content
    assert page["count"] == 3 and page["next_offset"] == 3
    row = page["items"][0]
    assert row["confidence"] == {"mean": 0.8, "min": 0.6, "max": 0.95}
    assert "input_ref" not in row
    assert api.last().url.params["has_capture"] == "true"


async def test_get_inference_returns_capture_and_overlay_as_images(server):
    result = await call(server, "get_inference", log_id="log1")
    kinds = [block.type for block in result.content]
    assert kinds == ["text", "image", "image"]
    assert "captured_images" in result.content[0].text
    # the 3000x2000 capture came back downscaled
    from base64 import b64decode
    from io import BytesIO

    from PIL import Image

    capture = Image.open(BytesIO(b64decode(result.content[1].data)))
    assert max(capture.size) <= 1568
    assert result.content[1].mime_type == "image/jpeg"
    assert result.content[2].mime_type == "image/png"


async def test_get_inference_without_images(server, api):
    result = await call(server, "get_inference", log_id="log1", include_images=False)
    assert [block.type for block in result.content] == ["text"]
    assert all(r.url.host != "minio.test" for r in api.calls)


async def test_presets(server):
    page = (await call(server, "list_presets")).structured_content
    assert page["items"][0]["name"] == "yolov11"
    config = (await call(server, "get_preset", name="yolov11")).structured_content
    assert config["classes"] == ["bumper", "door"]
