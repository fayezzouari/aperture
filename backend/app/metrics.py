"""R5.4 - Prometheus series, labelled so every panel can slice by model and version."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, multiprocess

REGISTRY = CollectorRegistry()

LATENCY_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.25, 0.4, 0.6, 1.0, 2.0, 5.0, 10.0, 30.0,
)

requests_total = Counter(
    "aperture_inference_requests_total",
    "Inference requests by outcome",
    ["project", "model", "version", "route", "status"],
    registry=REGISTRY,
)

images_total = Counter(
    "aperture_inference_images_total",
    "Images processed",
    ["project", "model", "version"],
    registry=REGISTRY,
)

request_latency = Histogram(
    "aperture_inference_latency_seconds",
    "End-to-end gateway latency",
    ["model", "version", "route"],
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)

stage_latency = Histogram(
    "aperture_inference_stage_seconds",
    "Per-stage latency (R5.2)",
    ["model", "version", "stage"],
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)

errors_total = Counter(
    "aperture_inference_errors_total",
    "Inference errors by machine-readable code",
    ["model", "version", "code"],
    registry=REGISTRY,
)

rejected_total = Counter(
    "aperture_requests_rejected_total",
    "Requests refused before reaching a runtime (backpressure, limits, auth)",
    ["reason"],
    registry=REGISTRY,
)

replicas_ready = Gauge(
    "aperture_replicas_ready",
    "Replicas reporting ready per version",
    ["model", "version"],
    registry=REGISTRY,
)

replicas_desired = Gauge(
    "aperture_replicas_desired",
    "Replicas requested per version",
    ["model", "version"],
    registry=REGISTRY,
)

queue_depth = Gauge(
    "aperture_runtime_queue_depth",
    "Queued requests per runtime and version",
    ["runtime", "version"],
    registry=REGISTRY,
)

runtime_gpu_memory = Gauge(
    "aperture_runtime_gpu_memory_mb",
    "GPU memory reported by a runtime",
    ["runtime", "kind"],
    registry=REGISTRY,
)

runtime_up = Gauge(
    "aperture_runtime_up",
    "1 when a runtime heartbeat is fresh",
    ["runtime", "device"],
    registry=REGISTRY,
)

batch_size = Histogram(
    "aperture_batch_size",
    "Images per batch actually executed on a runtime",
    ["model", "version"],
    buckets=(1, 2, 4, 8, 16, 32, 64, 128),
    registry=REGISTRY,
)

job_gauge = Gauge(
    "aperture_jobs",
    "Async jobs by kind and status",
    ["kind", "status"],
    registry=REGISTRY,
)


def observe_stages(model: str, version: str, latency: dict) -> None:
    for stage in ("queue", "preprocess", "inference", "postprocess"):
        value = latency.get(f"{stage}_ms")
        if value is not None:
            stage_latency.labels(model, version, stage).observe(value / 1000.0)
