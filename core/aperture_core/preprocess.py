"""Config-driven preprocessing, shared by the validator and the runtime."""

from __future__ import annotations

import numpy as np
from PIL import Image

from aperture_core.configspec import ModelConfig, ResizeMode
from aperture_core.images import Geometry

_PIL_INTERP = {
    "bilinear": Image.BILINEAR,
    "bicubic": Image.BICUBIC,
    "nearest": Image.NEAREST,
    "area": Image.BOX,
}


def _resize(arr: np.ndarray, w: int, h: int, interp: str) -> np.ndarray:
    if arr.shape[0] == h and arr.shape[1] == w:
        return arr
    img = Image.fromarray(arr).resize((w, h), _PIL_INTERP.get(interp, Image.BILINEAR))
    return np.asarray(img, dtype=np.uint8)


def apply_geometry(arr: np.ndarray, cfg: ModelConfig) -> tuple[np.ndarray, Geometry]:
    """Resize/pad an HWC uint8 image and return the mapping back to source pixels."""
    rc = cfg.preprocess.resize
    oh, ow = arr.shape[0], arr.shape[1]
    net_w, net_h = rc.width, rc.height

    if rc.mode == ResizeMode.none:
        return arr, Geometry(ow, oh, ow, oh, 1.0, 1.0, 0.0, 0.0)

    if rc.mode == ResizeMode.stretch:
        out = _resize(arr, net_w, net_h, rc.interpolation)
        return out, Geometry(ow, oh, net_w, net_h, net_w / ow, net_h / oh, 0.0, 0.0)

    if rc.mode == ResizeMode.letterbox:
        scale = min(net_w / ow, net_h / oh)
        nw, nh = max(1, round(ow * scale)), max(1, round(oh * scale))
        resized = _resize(arr, nw, nh, rc.interpolation)
        canvas = np.full((net_h, net_w, arr.shape[2]), rc.pad_value, dtype=np.uint8)
        # centre the content, matching Ultralytics' letterbox
        pad_x, pad_y = (net_w - nw) // 2, (net_h - nh) // 2
        canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
        return canvas, Geometry(ow, oh, net_w, net_h, scale, scale, float(pad_x), float(pad_y))

    if rc.mode == ResizeMode.shortest_side:
        side = rc.resize_side or min(net_w, net_h)
        scale = side / min(ow, oh)
        nw, nh = max(1, round(ow * scale)), max(1, round(oh * scale))
        out = _resize(arr, nw, nh, rc.interpolation)
        return out, Geometry(ow, oh, nw, nh, scale, scale, 0.0, 0.0)

    # center_crop: resize shortest side then crop the middle - the ImageNet recipe
    side = rc.resize_side or max(net_w, net_h)
    scale = side / min(ow, oh)
    nw, nh = max(1, round(ow * scale)), max(1, round(oh * scale))
    resized = _resize(arr, nw, nh, rc.interpolation)
    x0, y0 = (nw - net_w) // 2, (nh - net_h) // 2
    x0, y0 = max(0, x0), max(0, y0)
    cropped = resized[y0:y0 + net_h, x0:x0 + net_w]
    if cropped.shape[0] != net_h or cropped.shape[1] != net_w:
        cropped = _resize(cropped, net_w, net_h, rc.interpolation)
    return cropped, Geometry(ow, oh, net_w, net_h, scale, scale, float(-x0), float(-y0))


def to_tensor(arr: np.ndarray, cfg: ModelConfig) -> np.ndarray:
    """HWC uint8 -> a single CHW/HWC sample in the model's dtype and colour order."""
    x = arr
    if cfg.preprocess.grayscale or cfg.input.color == "GRAY":
        x = (x.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32))[..., None]
    elif cfg.input.color == "BGR":
        x = x[..., ::-1]

    n = cfg.preprocess.normalize
    if cfg.input.dtype in ("uint8", "int8"):
        out = x.astype(np.uint8 if cfg.input.dtype == "uint8" else np.int8)
    else:
        f = x.astype(np.float32) * n.scale
        mean = np.asarray(n.mean, dtype=np.float32)
        std = np.asarray(n.std, dtype=np.float32)
        if mean.size == 1:
            f = (f - mean[0]) / std[0]
        elif mean.size == f.shape[-1]:
            f = (f - mean) / std
        out = f.astype(np.float16 if cfg.input.dtype == "float16" else np.float32)

    if cfg.input.layout == "NCHW":
        out = np.transpose(out, (2, 0, 1))
    return np.ascontiguousarray(out)


def preprocess_batch(
    images: list[np.ndarray], cfg: ModelConfig
) -> tuple[np.ndarray, list[Geometry]]:
    """Build one NCHW/NHWC batch tensor plus per-image geometry."""
    tensors: list[np.ndarray] = []
    geoms: list[Geometry] = []
    for arr in images:
        resized, geom = apply_geometry(arr, cfg)
        tensors.append(to_tensor(resized, cfg))
        geoms.append(geom)
    shapes = {t.shape for t in tensors}
    if len(shapes) > 1:
        raise ValueError(
            f"cannot batch images with differing model-space shapes {shapes}; "
            "use a fixed resize mode or send them one per request"
        )
    return np.stack(tensors, axis=0), geoms
