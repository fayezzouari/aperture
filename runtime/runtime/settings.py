"""Runtime configuration. Everything the runtime needs to find its control plane."""

from __future__ import annotations

import os
import socket
import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AP_", env_file=".env", extra="ignore")

    runtime_id: str = ""
    advertise_host: str = ""
    port: int = 9100

    control_plane_url: str = "http://api:8000"
    runtime_internal_token: str = "change-me-runtime-token"
    heartbeat_interval_s: float = 5.0

    # Weights are pulled straight from object storage, never through the API.
    s3_endpoint: str = "http://minio:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "aperture"
    s3_secret_key: str = "aperture-secret"
    s3_force_path_style: bool = True

    cache_dir: str = "/var/cache/aperture"
    cache_max_gb: float = 64.0

    device: str = "auto"           # auto | cpu | cuda | cuda:N
    onnx_threads: int = 0          # 0 = let onnxruntime decide
    max_loaded_versions: int = 8
    default_max_batch_size: int = 8
    default_max_wait_ms: int = 8
    default_max_queue_depth: int = 64
    infer_workers: int = 2         # concurrent blocking inference calls per process
    # Memory admission: a version that would push the process past the budget
    # is refused rather than loaded. 0 = a fraction of the cgroup limit (or of
    # host RAM when the container has no limit).
    memory_budget_mb: int = 0
    memory_budget_fraction: float = 0.8
    # Until a version has been measured, its cost is estimated as this multiple
    # of its weights file, and never below the floor.
    memory_estimate_factor: float = 3.0
    memory_min_version_mb: int = 64
    # Requests are admitted against the same budget: a request reserves its
    # decoded RGB size (width x height x 3, read from the image header) times
    # this factor, which covers the tensor, the batch and full-resolution masks.
    # One that does not fit next to what is already in flight gets queue_full.
    request_memory_factor: float = 6.0
    # Requests may use memory up to this line (0 = this fraction of the cgroup
    # limit, or of host RAM); past it the kernel would kill the process.
    request_memory_ceiling_mb: int = 0
    # each image also reserves the activation memory measured at warm-up, never
    # less than this floor (warm-up can under-measure once the arena is primed)
    request_activation_min_mb: int = 16
    request_memory_ceiling_fraction: float = 0.95
    log_level: str = "INFO"
    build: str = "aperture-runtime/0.1.0"

    def resolved_runtime_id(self) -> str:
        if self.runtime_id:
            return self.runtime_id
        host = os.environ.get("HOSTNAME") or socket.gethostname()
        return f"{host}-{uuid.uuid4().hex[:6]}"

    def resolved_url(self) -> str:
        host = self.advertise_host or os.environ.get("HOSTNAME") or socket.gethostname()
        return f"http://{host}:{self.port}"


settings = RuntimeSettings()
