from __future__ import annotations

import json
from base64 import b64decode
from io import BytesIO

import pytest
from PIL import Image

from aperture_mcp.images import load_input, to_image
from aperture_mcp.resources import extract_config_schema

from . import fixtures as fx


def test_load_input_forms(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(fx.SMALL_PNG)
    assert load_input(str(path)) == load_input(fx.SMALL_PNG_B64)
    assert load_input(str(path)).size == len(fx.SMALL_PNG)
    assert load_input("https://x/y.jpg").kind == "url"
    assert load_input(f"data:image/png;base64,{fx.SMALL_PNG_B64}").value == fx.SMALL_PNG_B64
    with pytest.raises(ValueError, match="no such file"):
        load_input("/missing/x.jpg")
    with pytest.raises(ValueError, match="not a file path"):
        load_input("definitely not base64!!")
    with pytest.raises(ValueError, match="empty"):
        load_input("  ")


def test_to_image_downscales_and_keeps_aspect():
    image = to_image(fx.BIG_JPEG, max_px=1568)
    decoded = Image.open(BytesIO(image.data))
    assert decoded.size == (1568, 1045)
    assert image._mime_type == "image/jpeg"


def test_to_image_leaves_small_images_alone():
    image = to_image(fx.SMALL_PNG, max_px=1568)
    assert image.data == fx.SMALL_PNG and image._mime_type == "image/png"


def test_to_image_rejects_garbage():
    with pytest.raises(ValueError, match="not an image"):
        to_image(b"hello", max_px=100)


def test_config_schema_extraction_follows_refs():
    schema = extract_config_schema(fx.OPENAPI)
    assert set(schema["$defs"]) == {"ModelConfig", "PreprocessConfig", "PostprocessConfig",
                                    "NmsConfig"}
    assert schema["$ref"] == "#/$defs/ModelConfig"
    assert "error" in extract_config_schema({"components": {"schemas": {}}})


async def test_resources(server):
    contents = list(await server.read_resource("aperture://presets/yolov11"))
    assert json.loads(contents[0].content)["classes"] == ["bumper", "door"]

    contents = list(await server.read_resource("aperture://schema/config"))
    assert "PreprocessConfig" in json.loads(contents[0].content)["$defs"]

    contents = list(await server.read_resource(
        "aperture://models/part-detection/versions/3/config"))
    assert json.loads(contents[0].content)["preprocess"] == {"resize": "letterbox"}

    contents = list(await server.read_resource("aperture://models/part-detection"))
    card = json.loads(contents[0].content)
    assert card["model"]["slug"] == "part-detection" and card["deployments"]

    contents = list(await server.read_resource("aperture://me"))
    assert json.loads(contents[0].content) == fx.ME


def test_fixture_overlay_is_a_real_png():
    assert b64decode(fx.PREDICT_RESPONSE["overlays"][0])[:4] == b"\x89PNG"
