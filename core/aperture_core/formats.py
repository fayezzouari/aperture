"""Response formats other than Aperture's own.

Models trained on Roboflow are usually already being called through Roboflow's
inference API, and the client code around them expects *its* response shape. So
Aperture can answer in that shape: `?format=roboflow` returns the same fields,
with the same names and the same box convention, and the client does not change.

The mapping, precisely:

    Aperture                          Roboflow
    box [x1, y1, x2, y2] (corners) →  x, y (CENTRE) + width, height
    score                          →  confidence
    class_name                     →  class
    class_id                       →  class_id
    mask polygons                  →  points: [{x, y}, ...]
    keypoints                      →  keypoints: [{x, y, confidence, class_name}]
    results[i].width/height        →  image: {width, height}
    latency.total_ms               →  time (seconds)
    request_id                     →  inference_id

Single-label classification returns a `predictions` list plus `top`,
`confidence` and `predicted_classes`; multi-label returns `predictions` keyed by
class name, which is what Roboflow does for a sigmoid head.
"""

from __future__ import annotations

import uuid
from typing import Any

from aperture_core.configspec import Decoder, Task
from aperture_core.results import ImageResult

ROBOFLOW = "roboflow"
APERTURE = "aperture"
FORMATS = (APERTURE, ROBOFLOW)


def _detection_entry(detection, *, with_points: bool) -> dict[str, Any]:
    x1, y1, x2, y2 = detection.box
    entry: dict[str, Any] = {
        "x": round((x1 + x2) / 2, 1),
        "y": round((y1 + y2) / 2, 1),
        "width": round(x2 - x1, 1),
        "height": round(y2 - y1, 1),
        "confidence": round(detection.score, 5),
        "class": detection.class_name,
        "class_id": detection.class_id,
        "detection_id": str(uuid.uuid4()),
    }
    if with_points and detection.mask is not None:
        # A mask that traces to nothing - a handful of pixels, or one that
        # thresholded away entirely - would leave `points: []`. Roboflow never
        # emits that, and a client reading it finds a segmentation detection
        # carrying no geometry at all; the one in cloud fails the whole batch
        # over it rather than silently dropping a shape. The box is real model
        # output and is the honest fallback, so degenerate masks answer with
        # their extent instead of with nothing.
        entry["points"] = _points(detection) or _box_ring(x1, y1, x2, y2)
    if detection.keypoints:
        entry["keypoints"] = [
            {
                "x": round(keypoint.x, 1),
                "y": round(keypoint.y, 1),
                "confidence": round(keypoint.score, 5),
                "class_id": index,
                "class_name": keypoint.name or f"kp_{index}",
            }
            for index, keypoint in enumerate(detection.keypoints)
        ]
    return entry


def _box_ring(x1: float, y1: float, x2: float, y2: float) -> list[dict[str, float]]:
    """The detection's extent as a four-point ring, clockwise from top-left."""
    return [{"x": round(x, 1), "y": round(y, 1)}
            for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2))]


def _points(detection) -> list[dict[str, float]]:
    """Roboflow's polygon form. An RLE mask is traced to a polygon on the way."""
    mask = detection.mask
    if mask is None:
        return []
    rings = mask.polygons
    if not rings and mask.counts and mask.size:
        from aperture_core.masks import decode_rle, encode_polygons

        rings = (encode_polygons(decode_rle(mask.counts, mask.size)) or {}).get("polygons") or []
    if not rings:
        return []
    # the largest ring is the object; holes are not part of Roboflow's shape
    ring = max(rings, key=len)
    return [{"x": round(ring[i], 1), "y": round(ring[i + 1], 1)}
            for i in range(0, len(ring) - 1, 2)]


def to_roboflow(result: ImageResult, *, task: str, decoder: str, inference_id: str,
                time_s: float, model: str | None = None,
                version: int | None = None) -> dict[str, Any]:
    """One image's predictions, in Roboflow's response shape."""
    payload: dict[str, Any] = {
        "inference_id": inference_id,
        "time": round(time_s, 6),
        "image": {"width": int(result.width), "height": int(result.height)},
    }
    if model:
        payload["model_id"] = f"{model}/{version}" if version is not None else model

    if result.error:
        payload["predictions"] = []
        payload["error"] = result.error
        return payload

    if task == Task.classification.value or result.classifications is not None:
        items = result.classifications or []
        multi_label = decoder == Decoder.classifier_sigmoid.value
        if multi_label:
            payload["predictions"] = {
                item.class_name: {"confidence": round(item.score, 5), "class_id": item.class_id}
                for item in items
            }
            payload["predicted_classes"] = [item.class_name for item in items
                                            if item.score >= 0.5]
        else:
            payload["predictions"] = [
                {"class": item.class_name, "class_id": item.class_id,
                 "confidence": round(item.score, 5)}
                for item in items
            ]
            if items:
                payload["top"] = items[0].class_name
                payload["confidence"] = round(items[0].score, 5)
            payload["predicted_classes"] = [item.class_name for item in items[:1]]
        return payload

    if result.embedding is not None:
        payload["embeddings"] = result.embedding
        payload["predictions"] = []
        return payload

    if result.texts:
        payload["predictions"] = [
            {"class": item.text, "class_id": 0, "confidence": round(item.score, 5),
             **({"x": round((item.box[0] + item.box[2]) / 2, 1),
                 "y": round((item.box[1] + item.box[3]) / 2, 1),
                 "width": round(item.box[2] - item.box[0], 1),
                 "height": round(item.box[3] - item.box[1], 1)} if item.box else {})}
            for item in result.texts
        ]
        return payload

    segmentation = task == Task.segmentation.value or bool(result.segments)
    source = result.segments if segmentation and result.segments else (result.detections or [])
    payload["predictions"] = [
        _detection_entry(detection, with_points=segmentation) for detection in source
    ]
    return payload


def to_roboflow_batch(results: list[ImageResult], *, task: str, decoder: str,
                      inference_id: str, time_s: float, model: str | None = None,
                      version: int | None = None) -> Any:
    """A single image answers with one object; several answer with a list.

    That asymmetry is Roboflow's, not ours - matching it is the whole point.
    """
    payloads = [
        to_roboflow(result, task=task, decoder=decoder,
                    inference_id=f"{inference_id}-{index}" if len(results) > 1 else inference_id,
                    time_s=time_s, model=model, version=version)
        for index, result in enumerate(results)
    ]
    return payloads[0] if len(payloads) == 1 else payloads
