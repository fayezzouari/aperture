"""Canned /v1 responses shaped like the backend's response models."""

from __future__ import annotations

import base64
import io

from PIL import Image as PILImage

NOW = "2026-09-17T10:00:00+00:00"


def png_bytes(width: int, height: int, color=(200, 40, 40)) -> bytes:
    out = io.BytesIO()
    PILImage.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


def jpeg_bytes(width: int, height: int) -> bytes:
    out = io.BytesIO()
    PILImage.new("RGB", (width, height), (10, 120, 200)).save(out, format="JPEG")
    return out.getvalue()


SMALL_PNG = png_bytes(64, 48)
SMALL_PNG_B64 = base64.b64encode(SMALL_PNG).decode()
BIG_JPEG = jpeg_bytes(3000, 2000)

ME = {"id": "u1", "email": "dev@aperture.test", "name": "Dev", "provider": "local",
      "role": "developer", "is_active": True, "last_login_at": NOW,
      "last_workspace_id": "w1"}

WORKSPACES = [
    {"id": "w1", "slug": "cloud", "name": "Cloud", "description": "", "created_at": NOW,
     "role": "developer", "model_count": 2, "member_count": 3},
]

MODEL = {
    "id": "m1", "workspace_id": "w1", "slug": "part-detection", "name": "Part detection",
    "task": "detection", "owner": "vision", "framework": "onnx", "description": "Finds parts",
    "labels": ["prod"], "created_at": NOW, "updated_at": NOW, "archived_at": None,
    "version_count": 3, "latest_version": 3,
    "tags": [{"id": "t1", "name": "production", "version_id": "v3", "version": 3,
              "previous_version_id": "v2", "locked": False, "updated_at": NOW,
              "updated_by": None}],
    "deployed_versions": [3], "replicas_ready": 2, "replicas_desired": 2,
    "requests_24h": 1200, "error_rate_24h": 0.01, "p95_ms_24h": 71.5,
}

VERSION = {
    "id": "v3", "model_id": "m1", "version": 3, "status": "published", "preset": "yolov11",
    "artifact_format": "onnx", "weights_bytes": 48210944, "weights_sha256": "abc",
    "artifact_info": {"opset": 17}, "config": {"preprocess": {"resize": "letterbox"},
                                                 "postprocess": {"score_threshold": 0.25},
                                                 "classes": ["bumper", "door"]},
    "training_run_id": None, "dataset_version": "Parts-V2-10", "metrics": {"mAP50": 0.71},
    "git_commit": None, "changelog": "September retrain", "source": "upload",
    "parent_version_id": "v2", "validation": {"status": "ready", "errors": [], "warnings": [],
                                             "raw": {"huge": "blob"}},
    "created_by": "u1", "created_at": NOW, "published_at": NOW, "deprecated_at": None,
    "last_served_at": NOW, "tags": ["production"], "deployment": None,
}

DEPLOYMENT = {
    "id": "d1", "model_id": "m1", "version_id": "v3", "version": 3, "mode": "primary",
    "traffic_weight": 100, "replicas": 2, "device": "auto", "max_batch_size": 8,
    "max_wait_ms": 10, "max_queue_depth": 64, "request_timeout_ms": 5000, "autoscale": False,
    "min_replicas": 1, "max_replicas": 4, "target_queue_depth": 8, "status": "live",
    "status_detail": "", "created_at": NOW, "updated_at": NOW, "replicas_ready": 2,
    "replica_states": [{"runtime_id": "r1", "state": "ready"}, {"runtime_id": "r2",
                                                                 "state": "ready"}],
}

INFERENCE = {
    "id": "log1", "request_id": "req-1", "trace_id": None, "model_id": "m1",
    "model_slug": "part-detection", "version_id": "v3", "version_number": 3,
    "tag": "production", "route": "primary", "caller_type": "api_key",
    "caller_label": "shop-app", "status": "ok", "http_status": 200, "error_code": None,
    "error_message": None, "batch_size": 1, "queue_ms": 1.0, "preprocess_ms": 12.0,
    "inference_ms": 20.0, "postprocess_ms": 1.0, "gateway_ms": 3.0, "total_ms": 37.0,
    "runtime_id": "r1", "detection_count": 2, "confidence": {"mean": 0.8, "min": 0.6,
                                                              "max": 0.95},
    "input_stats": {"width": 1280}, "input_ref": {}, "overrides": {}, "flagged": False,
    "flag_label": None, "created_at": NOW, "has_capture": True,
}

INFERENCE_DETAIL = {
    **INFERENCE,
    "output": {"results": [{"detections": [{"class_name": "door", "score": 0.9}]}]},
    "capture": {"images": [{"key": "cap/1.jpg"}], "overlays": [{"key": "cap/1.ov.png"}]},
    "image_urls": ["http://minio.test/captures/cap/1.jpg?X-Amz-Signature=abc"],
    "overlay_urls": ["http://minio.test/captures/cap/1.ov.png?X-Amz-Signature=def"],
}

PREDICT_RESPONSE = {
    "request_id": "req-9", "model": "part-detection", "model_version": 3, "version_id": "v3",
    "tag": "production", "task": "detection", "batch_size": 1,
    "results": [{"index": 0, "width": 1280, "height": 720, "detections": [
        {"box": [220.0, 118.0, 580.0, 400.0], "score": 0.93, "class_id": 0,
         "class_name": "bumper", "mask": {"format": "rle", "counts": "..."}},
        {"box": [10.0, 10.0, 50.0, 50.0], "score": 0.41, "class_id": 1, "class_name": "door"},
        {"box": [60.0, 10.0, 90.0, 50.0], "score": 0.39, "class_id": 1, "class_name": "door"},
    ]}],
    "latency": {"queue_ms": 1.0, "preprocess_ms": 12.0, "inference_ms": 20.0,
                "postprocess_ms": 1.0, "runtime_ms": 33.0, "gateway_ms": 3.0,
                "total_ms": 37.0},
    "runtime_id": "r1", "trace_id": None, "overrides_applied": {"score_threshold": 0.3},
    "overlays": [base64.b64encode(png_bytes(1280, 720)).decode()],
}

OVERVIEW = {
    "models": 2, "versions": 5, "deployments": 2,
    "replicas_ready": 2, "replicas_desired": 2,
    "runtimes": [{"id": "r1", "url": "http://runtime:9000", "hostname": "rt-1",
                  "device": "cpu", "gpu_name": "", "total_memory_mb": 0, "free_memory_mb": 0,
                  "slots": 8, "status": "ready", "loaded": [{"model": "part-detection",
                                                             "version": 3, "state": "ready"}],
                  "metrics": {"noise": 1}, "last_heartbeat": NOW, "fresh": True}],
    "metrics": {"window_minutes": 60, "requests": 100, "errors": 1, "error_rate": 0.01,
                "images": 100, "rps": 0.03, "p50_ms": 30.0, "p95_ms": 70.0, "p99_ms": 90.0,
                "stages": {"inference_ms": 20.0}, "by_status": {"ok": 99, "error": 1},
                "series": [{"t": NOW, "requests": 1}]},
    "recent_errors": [{**INFERENCE, "status": "error", "error_code": "timeout"}],
    "firing_alerts": [], "log_stream_depth": 0,
}

ERROR_403 = {"error": {"code": "insufficient_role",
                       "message": "this action needs the 'developer' role, your role is 'viewer'",
                       "request_id": "req-403"}}

OPENAPI = {
    "openapi": "3.1.0",
    "components": {"schemas": {
        "ModelConfig": {"type": "object", "properties": {
            "preprocess": {"$ref": "#/components/schemas/PreprocessConfig"},
            "postprocess": {"anyOf": [{"$ref": "#/components/schemas/PostprocessConfig"},
                                      {"type": "null"}]},
        }},
        "PreprocessConfig": {"type": "object", "properties": {"resize": {"type": "string"}}},
        "PostprocessConfig": {"type": "object", "properties": {
            "nms": {"$ref": "#/components/schemas/NmsConfig"}}},
        "NmsConfig": {"type": "object"},
        "Unrelated": {"type": "object"},
    }},
}

PRESETS = [{"name": "yolov11", "label": "YOLO11", "family": "yolo", "task": "detection",
            "description": ""}]
