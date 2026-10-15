"""Shape API responses for a model that pays per token.

The console renders forty fields; the agent needs the ones it will reason
about: identifiers it can pass back, states, counts, latencies and class names.
Nothing here invents data - every value comes from the API row it was given.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def pick(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: row[key] for key in keys if key in row and row[key] is not None}


def page(items: list[Any], limit: int, offset: int) -> dict[str, Any]:
    return {
        "items": items,
        "count": len(items),
        "next_offset": offset + limit if len(items) >= limit else None,
    }


# ------------------------------------------------------------------ registry


def project_row(row: dict) -> dict:
    return pick(row, "slug", "name", "description", "model_count", "role")


def model_row(row: dict) -> dict:
    out = pick(row, "id", "slug", "name", "task", "framework", "owner", "labels", "version_count",
               "latest_version", "deployed_versions", "replicas_ready", "replicas_desired",
               "requests_24h", "error_rate_24h", "p95_ms_24h", "archived_at")
    out["tags"] = {tag["name"]: tag.get("version") for tag in row.get("tags") or []}
    return out


def version_row(row: dict) -> dict:
    out = pick(row, "id", "version", "status", "preset", "artifact_format", "weights_bytes",
               "dataset_version", "metrics", "changelog", "source", "created_at", "published_at",
               "deprecated_at", "last_served_at", "tags")
    if row.get("deployment"):
        out["deployment"] = deployment_row(row["deployment"])
    validation = row.get("validation") or {}
    if validation:
        out["validation"] = pick(validation, "status", "errors", "warnings", "reason")
    return out


def version_detail(row: dict, include_config: bool) -> dict:
    out = version_row(row)
    out.update(pick(row, "model_id", "weights_sha256", "training_run_id", "git_commit",
                    "parent_version_id", "created_by"))
    if include_config:
        out["config"] = row.get("config") or {}
        out["artifact_info"] = row.get("artifact_info") or {}
    return out


def deployment_row(row: dict) -> dict:
    out = pick(row, "id", "version", "mode", "traffic_weight", "replicas", "replicas_ready",
               "device", "max_batch_size", "max_wait_ms", "autoscale", "min_replicas",
               "max_replicas", "status", "status_detail", "updated_at")
    states = row.get("replica_states") or []
    if states:
        out["replicas"] = {
            "desired": row.get("replicas"),
            "ready": row.get("replicas_ready"),
            "by_state": dict(Counter(s.get("state", "?") for s in states)),
        }
    return out


def runtime_row(row: dict) -> dict:
    out = pick(row, "id", "hostname", "device", "gpu_name", "total_memory_mb", "free_memory_mb",
               "slots", "status", "last_heartbeat", "fresh")
    loaded = row.get("loaded") or []
    out["loaded"] = [pick(item, "model", "version", "state") if isinstance(item, dict) else item
                     for item in loaded]
    return out


def workflow_row(row: dict, include_graph: bool = False) -> dict:
    out = pick(row, "id", "slug", "name", "description", "owner", "labels", "revision", "status",
               "published_at", "problems", "runs_24h", "error_rate_24h", "p95_ms_24h",
               "models_used")
    if include_graph:
        out["graph"] = row.get("published_graph") or row.get("graph")
    return out


SECRET_HINTS = ("api_key", "apikey", "token", "password", "secret")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if v and any(h in str(k).lower() for h in SECRET_HINTS) else _redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def job_row(row: dict) -> dict:
    out = pick(row, "id", "kind", "status", "progress", "total", "error", "created_at",
               "started_at", "finished_at", "params")
    # the API already masks credentials; an agent's context is no place for one either way
    if "params" in out:
        out["params"] = _redact(out["params"])
    return out


# ------------------------------------------------------------- observability


def metrics_summary(row: dict, include_series: bool) -> dict:
    out = pick(row, "window_minutes", "requests", "errors", "error_rate", "images", "rps",
               "p50_ms", "p95_ms", "p99_ms", "stages", "by_status")
    if include_series:
        out["series"] = row.get("series") or []
    return out


def overview(row: dict) -> dict:
    return {
        "project": project_row(row.get("project") or {}),
        **pick(row, "models", "versions", "deployments", "replicas_ready", "replicas_desired",
               "log_stream_depth"),
        "metrics": metrics_summary(row.get("metrics") or {}, include_series=False),
        "runtimes": [runtime_row(r) for r in row.get("runtimes") or []],
        "recent_errors": [inference_row(r) for r in row.get("recent_errors") or []],
        "firing_alerts": [pick(a, "id", "name", "kind", "metric", "threshold", "state")
                          for a in row.get("firing_alerts") or []],
    }


def inference_row(row: dict) -> dict:
    out = pick(row, "id", "request_id", "model_slug", "version_number", "tag", "route",
               "caller_label", "status", "error_code", "error_message", "batch_size",
               "total_ms", "inference_ms", "queue_ms", "detection_count", "flagged",
               "flag_label", "has_capture", "created_at")
    confidence = row.get("confidence") or {}
    if confidence:
        out["confidence"] = pick(confidence, "mean", "min", "max")
    return out


def inference_detail(row: dict) -> dict:
    out = inference_row(row)
    out.update(pick(row, "trace_id", "model_id", "version_id", "runtime_id", "overrides",
                    "input_stats", "preprocess_ms", "postprocess_ms", "gateway_ms"))
    out["output"] = row.get("output") or {}
    out["captured_images"] = len(row.get("image_urls") or [])
    out["captured_overlays"] = len(row.get("overlay_urls") or [])
    return out


def audit_row(row: dict) -> dict:
    return pick(row, "id", "created_at", "action", "actor_type", "actor_label", "target_type",
                "target_label", "target_id", "payload", "client_ip")


# ----------------------------------------------------------------- inference


def prediction_summary(response: dict, top: int = 10) -> dict:
    """What came back, minus masks and raw tensors: enough to answer 'what did it see'."""
    out = pick(response, "request_id", "model", "model_version", "tag", "task", "batch_size",
               "overrides_applied")
    latency = response.get("latency") or {}
    out["latency_ms"] = pick(latency, "total_ms", "queue_ms", "preprocess_ms", "inference_ms",
                             "postprocess_ms", "gateway_ms")
    out["results"] = [image_result_summary(result, top) for result in response.get("results", [])]
    out["overlays_attached"] = len(response.get("overlays") or [])
    return out


def image_result_summary(result: dict, top: int) -> dict:
    out = pick(result, "index", "width", "height", "error")
    instances = (result.get("detections") or []) + (result.get("segments") or [])
    if instances:
        out["instances"] = len(instances)
        out["by_class"] = dict(Counter(d.get("class_name", str(d.get("class_id")))
                                       for d in instances))
        ranked = sorted(instances, key=lambda d: d.get("score", 0), reverse=True)[:top]
        out["top"] = [
            {"class": d.get("class_name"), "score": round(d.get("score", 0), 3),
             "box": [round(v) for v in d.get("box", [])],
             **({"mask": True} if d.get("mask") else {}),
             **({"keypoints": len(d["keypoints"])} if d.get("keypoints") else {})}
            for d in ranked
        ]
    if result.get("classifications"):
        out["classifications"] = [
            {"class": c.get("class_name"), "score": round(c.get("score", 0), 4)}
            for c in result["classifications"][:top]
        ]
    if result.get("texts"):
        out["texts"] = [pick(t, "text", "score") for t in result["texts"][:top * 2]]
    if result.get("embedding") is not None:
        out["embedding_dim"] = result.get("embedding_dim") or len(result["embedding"])
    return out


def workflow_run_summary(response: dict) -> dict:
    out = pick(response, "run_id", "request_id", "workflow", "revision", "status", "model_calls",
               "total_ms", "failed_node", "error")
    out["outputs"] = _summarise_outputs(response.get("outputs") or {})
    out["node_stats"] = [pick(n, "node", "kind", "status", "ms", "items", "error")
                         for n in response.get("node_stats") or []]
    out["previews_attached"] = sum(len(v) for v in (response.get("previews") or {}).values())
    return out


def _summarise_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in outputs.items():
        if isinstance(value, dict) and ("detections" in value or "classifications" in value):
            summary[key] = image_result_summary(value, top=10)
        elif isinstance(value, list) and value and isinstance(value[0], dict) \
                and ("detections" in value[0] or "classifications" in value[0]):
            summary[key] = [image_result_summary(v, top=10) for v in value]
        else:
            summary[key] = value
    return summary
