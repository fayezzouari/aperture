from __future__ import annotations

import json
from base64 import b64decode
from io import BytesIO

import httpx
from PIL import Image

from . import fixtures as fx
from .conftest import call, call_error


async def test_predict_sends_base64_and_returns_summary_plus_overlay(server, api, tmp_path):
    picture = tmp_path / "damage.png"
    picture.write_bytes(fx.SMALL_PNG)

    result = await call(server, "predict", model="part-detection", images=[str(picture)],
                        overrides={"score_threshold": 0.3})
    body = api.last_json()
    assert body["images"] == [fx.SMALL_PNG_B64]
    assert body["include_overlay"] is True
    assert body["overrides"] == {"score_threshold": 0.3}
    assert "image_urls" not in body
    assert "ondemand" not in api.last().url.params

    assert [block.type for block in result.content] == ["text", "image"]
    summary = json.loads(result.content[0].text)
    assert summary["model_version"] == 3
    assert summary["results"][0]["by_class"] == {"bumper": 1, "door": 2}
    assert summary["results"][0]["top"][0] == {"class": "bumper", "score": 0.93,
                                               "box": [220, 118, 580, 400], "mask": True}
    assert summary["latency_ms"]["total_ms"] == 37.0
    assert "overlays" not in summary  # the PNG travels as an image block, not as text

    overlay = Image.open(BytesIO(b64decode(result.content[1].data)))
    assert overlay.size == (1280, 720)
    assert result.content[1].mime_type == "image/png"


async def test_predict_passes_urls_through_and_flags_ondemand(server, api):
    await call(server, "predict", model="part-detection",
               images=["https://cdn.example/a.jpg"], ondemand=True, ref="production")
    body = api.last_json()
    assert body["image_urls"] == ["https://cdn.example/a.jpg"]
    assert "images" not in body
    assert api.last().url.params["ondemand"] == "true"


async def test_predict_accepts_data_urls_and_bare_base64(server, api):
    await call(server, "predict", model="part-detection",
               images=[f"data:image/png;base64,{fx.SMALL_PNG_B64}", fx.SMALL_PNG_B64])
    assert api.last_json()["images"] == [fx.SMALL_PNG_B64, fx.SMALL_PNG_B64]


async def test_predict_rejects_nonsense_before_calling_the_api(server, api):
    text = await call_error(server, "predict", model="part-detection",
                            images=["/nowhere/photo.jpg"])
    assert "invalid_request: no such file: /nowhere/photo.jpg" in text
    assert not api.calls

    text = await call_error(server, "predict", model="part-detection", images=[])
    assert "at least one image" in text


async def test_predict_roboflow_format_is_passed_through_verbatim(server, api):
    api.routes[("POST", "/v1/models/part-detection/production/predict")] = {
        "inference_id": "x", "predictions": []}
    result = await call(server, "predict", model="part-detection", images=[fx.SMALL_PNG_B64],
                        format="roboflow")
    assert api.last().url.params["format"] == "roboflow"
    assert json.loads(result.content[0].text) == {"inference_id": "x", "predictions": []}


async def test_run_workflow(server, api):
    api.routes[("POST", "/v1/workflows/damage/run")] = {
        "run_id": "run1", "request_id": "req-w", "workflow": "damage", "revision": 2,
        "status": "ok", "outputs": {"parts": fx.PREDICT_RESPONSE["results"][0],
                                    "severity": "high"},
        "node_stats": [{"node": "detect", "kind": "model", "status": "ok", "ms": 30.0}],
        "model_calls": 2, "total_ms": 80.0, "failed_node": None, "error": None,
        "previews": {"crop": [fx.SMALL_PNG_B64]},
    }
    result = await call(server, "run_workflow", slug="damage", images=[fx.SMALL_PNG_B64],
                        params={"region": "eu"}, draft=True)
    body = api.last_json()
    assert body["params"] == {"region": "eu"} and body["draft"] is True
    summary = json.loads(result.content[0].text)
    assert summary["outputs"]["parts"]["by_class"] == {"bumper": 1, "door": 2}
    assert summary["outputs"]["severity"] == "high"
    assert summary["previews_attached"] == 1
    assert result.content[1].type == "image"


async def test_api_errors_become_one_line_tool_errors(server, api):
    api.routes[("POST", "/v1/models/part-detection/production/predict")] = httpx.Response(
        503, json={"error": {"code": "model_not_deployed", "message": "no live replica",
                             "request_id": "req-503"}})
    text = await call_error(server, "predict", model="part-detection", images=[fx.SMALL_PNG_B64])
    assert "model_not_deployed: no live replica (request req-503)" in text
    assert "ondemand=true" in text
