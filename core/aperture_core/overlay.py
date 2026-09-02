"""Rendering predictions over the source image.

Used by upload validation (R1.7), the playground (R6.2), the config editor's
live preview (R6.3) and the inference explorer (R5.6). One renderer means an
overlay always looks the same wherever it appears in the product.
"""

from __future__ import annotations

import colorsys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from aperture_core.masks import decode_rle
from aperture_core.results import ImageResult

_SKELETON = [
    (0, 1), (0, 2), (1, 3), (2, 4), (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
]


def class_color(class_id: int) -> tuple[int, int, int]:
    """Stable, well-spread hue per class id - the same colour in every view."""
    hue = ((class_id * 0.6180339887) % 1.0)
    r, g, b = colorsys.hsv_to_rgb(hue, 0.72, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def render(image: np.ndarray, result: ImageResult, *, mask_alpha: float = 0.4,
           line_width: int | None = None, show_labels: bool = True) -> Image.Image:
    base = Image.fromarray(image).convert("RGBA")
    w, h = base.size
    lw = line_width or max(2, round(min(w, h) / 320))
    font = _font(max(12, round(min(w, h) / 45)))

    mask_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    mdraw = ImageDraw.Draw(mask_layer)
    for det in (result.segments or []):
        if not det.mask:
            continue
        color = class_color(det.class_id)
        alpha = int(255 * mask_alpha)
        if det.mask.format == "polygon" and det.mask.polygons:
            for ring in det.mask.polygons:
                if len(ring) >= 6:
                    mdraw.polygon([(ring[i], ring[i + 1]) for i in range(0, len(ring) - 1, 2)],
                                  fill=(*color, alpha))
        elif det.mask.counts:
            m = decode_rle(det.mask.counts, det.mask.size)
            if m.shape != (h, w):
                m = np.asarray(Image.fromarray(m * 255).resize((w, h), Image.NEAREST)) > 127
            tint = np.zeros((h, w, 4), dtype=np.uint8)
            tint[..., 0], tint[..., 1], tint[..., 2] = color
            tint[..., 3] = (np.asarray(m).astype(bool) * alpha).astype(np.uint8)
            mask_layer.alpha_composite(Image.fromarray(tint))
    base.alpha_composite(mask_layer)

    draw = ImageDraw.Draw(base)
    for det in (result.detections or []):
        color = class_color(det.class_id)
        x1, y1, x2, y2 = det.box
        draw.rectangle([x1, y1, x2, y2], outline=(*color, 255), width=lw)
        if show_labels:
            label = f"{det.class_name} {det.score:.2f}"
            tb = draw.textbbox((0, 0), label, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            ly = max(0, y1 - th - 6)
            draw.rectangle([x1, ly, x1 + tw + 8, ly + th + 6], fill=(*color, 235))
            draw.text((x1 + 4, ly + 3), label, fill=(16, 16, 18, 255), font=font)
        if det.keypoints:
            pts = det.keypoints
            for a, b in _SKELETON:
                if a < len(pts) and b < len(pts) and pts[a].score > 0.3 and pts[b].score > 0.3:
                    draw.line([pts[a].x, pts[a].y, pts[b].x, pts[b].y], fill=(*color, 220),
                              width=max(1, lw - 1))
            for kp in pts:
                if kp.score > 0.3:
                    r = lw + 1
                    draw.ellipse([kp.x - r, kp.y - r, kp.x + r, kp.y + r], fill=(255, 255, 255, 235),
                                 outline=(*color, 255))

    for i, cls in enumerate((result.classifications or [])[:5]):
        color = class_color(cls.class_id)
        y = 8 + i * (font.size + 8)
        label = f"{cls.class_name}  {cls.score:.3f}"
        tb = draw.textbbox((0, 0), label, font=font)
        draw.rectangle([8, y, 16 + tb[2] - tb[0], y + tb[3] - tb[1] + 6], fill=(*color, 220))
        draw.text((12, y + 3), label, fill=(16, 16, 18, 255), font=font)

    for i, tr in enumerate((result.texts or [])[:20]):
        label = f"{tr.text}  ({tr.score:.2f})"
        if tr.box:
            x1, y1, x2, y2 = tr.box
            draw.rectangle([x1, y1, x2, y2], outline=(0, 220, 255, 255), width=lw)
            draw.text((x1, max(0, y1 - font.size - 4)), label, fill=(0, 220, 255, 255), font=font)
        else:
            draw.text((10, 10 + i * (font.size + 4)), label, fill=(0, 220, 255, 255), font=font)

    if result.embedding_dim:
        draw.text((10, 10), f"embedding · dim {result.embedding_dim}",
                  fill=(255, 255, 255, 230), font=font)

    return base.convert("RGB")


def render_png(image: np.ndarray, result: ImageResult, **kw) -> bytes:
    import io

    buf = io.BytesIO()
    render(image, result, **kw).save(buf, format="PNG", compress_level=6)
    return buf.getvalue()
