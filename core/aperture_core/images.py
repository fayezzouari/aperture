"""Image ingest (R3.2) and the geometry bookkeeping the decoders need."""

from __future__ import annotations

import base64
import binascii
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps

MAX_PIXELS = 80_000_000  # decompression-bomb guard, ~8000x10000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ImageDecodeError(ValueError):
    pass


@dataclass(slots=True)
class LoadedImage:
    array: np.ndarray  # HWC uint8, RGB
    width: int
    height: int
    source: str  # "upload" | "base64" | "url"
    name: str | None = None


@dataclass(slots=True)
class Geometry:
    """Everything needed to map model-space coordinates back to the original image."""

    orig_w: int
    orig_h: int
    net_w: int
    net_h: int
    scale_x: float
    scale_y: float
    pad_x: float
    pad_y: float

    def to_original(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.pad_x) / self.scale_x, (y - self.pad_y) / self.scale_y)


def decode_image_bytes(data: bytes, *, source: str = "upload", name: str | None = None,
                       auto_orient: bool = True) -> LoadedImage:
    if not data:
        raise ImageDecodeError("empty image payload")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # PIL raises a zoo of exception types
        raise ImageDecodeError(f"could not decode image: {exc}") from exc
    if img.width * img.height > MAX_PIXELS:
        raise ImageDecodeError(f"image exceeds {MAX_PIXELS} pixel limit")
    if auto_orient:
        img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    arr = np.asarray(img, dtype=np.uint8)
    return LoadedImage(array=arr, width=img.width, height=img.height, source=source, name=name)


def decode_base64_image(payload: str, *, auto_orient: bool = True) -> LoadedImage:
    raw = payload.strip()
    if raw.startswith("data:"):
        _, _, raw = raw.partition(",")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageDecodeError(f"invalid base64 image: {exc}") from exc
    return decode_image_bytes(data, source="base64", auto_orient=auto_orient)


def encode_png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def encode_jpeg(arr: np.ndarray, quality: int = 88) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def image_stats(arr: np.ndarray) -> dict[str, float]:
    """R5.9 - cheap input statistics captured per request for drift comparison."""
    small = arr[::4, ::4].astype(np.float32)
    gray = small @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    return {
        "width": float(arr.shape[1]),
        "height": float(arr.shape[0]),
        "megapixels": round(arr.shape[0] * arr.shape[1] / 1e6, 4),
        "aspect_ratio": round(arr.shape[1] / max(arr.shape[0], 1), 4),
        "brightness": round(float(gray.mean()) / 255.0, 4),
        "contrast": round(float(gray.std()) / 255.0, 4),
        "saturation": round(float(small.max(axis=2).mean() - small.min(axis=2).mean()) / 255.0, 4),
    }
