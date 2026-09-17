"""Images in and images out.

In: whatever the agent has - a path on this machine (stdio runs where the user
works), an http(s) URL, a data: URL or bare base64. Out: an MCP `Image` block,
downscaled so a 12-megapixel capture does not become a 16 MB base64 string the
model would only shrink again anyway.
"""

from __future__ import annotations

import base64
import binascii
import io
import os
from dataclasses import dataclass

from mcp.server.mcpserver import Image
from PIL import Image as PILImage
from PIL import UnidentifiedImageError


@dataclass(frozen=True)
class ImageInput:
    kind: str  # "base64" | "url"
    value: str
    size: int = 0  # decoded bytes for base64 inputs; 0 for URLs


def load_input(source: str) -> ImageInput:
    """Turn one agent-supplied image reference into what the API accepts."""
    text = source.strip()
    if not text:
        raise ValueError("empty image reference")
    if text.startswith(("http://", "https://")):
        return ImageInput("url", text)
    if text.startswith("data:"):
        _, _, payload = text.partition(",")
        if not payload:
            raise ValueError("data: URL carries no payload")
        raw = _b64decode(payload, "data URL")
        return ImageInput("base64", base64.b64encode(raw).decode(), len(raw))
    path = os.path.expanduser(text)
    if os.path.isfile(path):
        with open(path, "rb") as handle:
            raw = handle.read()
        return ImageInput("base64", base64.b64encode(raw).decode(), len(raw))
    looks_like_path = text.startswith(("/", "./", "../", "~")) or text.lower().endswith(
        (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")
    )
    if looks_like_path:
        raise ValueError(f"no such file: {text}")
    raw = _b64decode(text, "image")
    return ImageInput("base64", base64.b64encode(raw).decode(), len(raw))


def _b64decode(payload: str, what: str) -> bytes:
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(
            f"{what} is not a file path, an http(s) URL, a data: URL or valid base64"
        ) from exc


def to_image(data: bytes, max_px: int) -> Image:
    """Wrap raw image bytes as an MCP image, bounded to `max_px` on the longest side."""
    try:
        picture = PILImage.open(io.BytesIO(data))
        picture.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("the bytes are not an image") from exc

    source_format = (picture.format or "PNG").upper()
    keep_png = source_format == "PNG" or picture.mode in ("RGBA", "LA", "P")
    if max(picture.size) <= max_px and source_format in ("PNG", "JPEG"):
        return Image(data=data, format="png" if source_format == "PNG" else "jpeg")

    if max(picture.size) > max_px:
        picture.thumbnail((max_px, max_px), PILImage.Resampling.LANCZOS)
    out = io.BytesIO()
    if keep_png:
        picture.save(out, format="PNG", optimize=True)
        return Image(data=out.getvalue(), format="png")
    picture.convert("RGB").save(out, format="JPEG", quality=85)
    return Image(data=out.getvalue(), format="jpeg")


def decode_overlays(overlays: list[str] | None, max_px: int) -> list[Image]:
    """Runtime-rendered overlays arrive as base64 PNGs, one per input image."""
    images: list[Image] = []
    for encoded in overlays or []:
        try:
            images.append(to_image(base64.b64decode(encoded), max_px))
        except (ValueError, binascii.Error):
            continue  # a broken overlay is not worth failing the prediction over
    return images
