"""Model residency: pull weights, load a backend, warm it up, keep it resident.

R4.4 - "no cold start on first request" is the whole point of this file. A
version becomes routable only after its warm-up passes, which is also what makes
R4.6 work: the control plane sees `ready` and only then does traffic move.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
from botocore.exceptions import ClientError

from aperture_core.configspec import Backend as BackendKind
from aperture_core.configspec import ModelConfig
from runtime.backends.base import Backend, BackendLoadError
from runtime.batcher import Batcher
from runtime.settings import settings

log = logging.getLogger("runtime.store")


@dataclass
class Loaded:
    version_id: str
    model_slug: str
    version: int
    config: ModelConfig
    backend: Backend | None = None
    batcher: Batcher | None = None
    state: str = "pending"     # pending | loading | warming | ready | failed | draining
    detail: str = ""
    loaded_at: datetime | None = None
    memory_mb: int = 0
    requests: int = 0
    batches: int = 0
    desired_state: str = "ready"
    assignment: dict = field(default_factory=dict)

    def report(self) -> dict:
        return {
            "version_id": self.version_id,
            "state": self.state,
            "detail": self.detail[:500],
            "queue_depth": self.batcher.queue_depth if self.batcher else 0,
            "in_flight": self.batcher.in_flight if self.batcher else 0,
            "loaded_at": self.loaded_at.isoformat() if self.loaded_at else None,
            "memory_mb": self.memory_mb,
            "requests": self.requests,
            "batches": self.batches,
        }


class ModelStore:
    def __init__(self, device_info: dict):
        self.device_info = device_info
        self.models: dict[str, Loaded] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._semaphore = asyncio.Semaphore(max(1, settings.infer_workers))
        os.makedirs(settings.cache_dir, exist_ok=True)

    # ------------------------------------------------------------------ access

    def get(self, version_id: str) -> Loaded | None:
        return self.models.get(version_id)

    def ready_versions(self) -> list[str]:
        return [v for v, m in self.models.items() if m.state == "ready"]

    def report(self) -> list[dict]:
        return [m.report() for m in self.models.values()]

    # ------------------------------------------------------- desired-state sync

    async def apply_assignments(self, assignments: list[dict]) -> None:
        """Converge on what the control plane says this runtime should hold."""
        wanted = {a["version_id"]: a for a in assignments}

        for version_id, assignment in wanted.items():
            existing = self.models.get(version_id)
            if existing is None:
                if len(self.models) >= settings.max_loaded_versions:
                    log.warning("at the loaded-version cap, refusing a new assignment",
                                extra={"version_id": version_id,
                                       "cap": settings.max_loaded_versions})
                    continue
                self.models[version_id] = Loaded(
                    version_id=version_id, model_slug=assignment.get("model_slug", ""),
                    version=assignment.get("version", 0),
                    config=ModelConfig.model_validate(assignment.get("config") or {}),
                    assignment=assignment,
                )
                asyncio.create_task(self._load(version_id))
            else:
                existing.desired_state = assignment.get("desired_state", "ready")
                if existing.batcher is not None:
                    existing.batcher.reconfigure(assignment.get("batching") or {})
                if existing.state == "failed" and _weights_changed(existing.assignment, assignment):
                    existing.assignment = assignment
                    asyncio.create_task(self._load(version_id))

        for version_id in [v for v in self.models if v not in wanted]:
            asyncio.create_task(self.unload(version_id, reason="unassigned"))

    async def _load(self, version_id: str) -> None:
        lock = self._locks.setdefault(version_id, asyncio.Lock())
        async with lock:
            entry = self.models.get(version_id)
            if entry is None or entry.state in ("ready", "loading", "warming"):
                return
            entry.state, entry.detail = "loading", ""
            assignment = entry.assignment
            started = time.perf_counter()
            try:
                path = await self._fetch_weights(assignment)
                backend = await asyncio.to_thread(self._build_backend, entry.config, path,
                                                  assignment)
                entry.backend = backend
                entry.batcher = Batcher(
                    version_id=version_id, backend=backend,
                    semaphore=self._semaphore, **_batching(assignment)
                )
                await entry.batcher.start()

                entry.state = "warming"
                await self._warmup(entry)

                entry.state = "ready"
                entry.loaded_at = datetime.now(UTC)
                entry.memory_mb = int(os.path.getsize(path) / 1e6)
                log.info("version ready", extra={
                    "version_id": version_id, "model": entry.model_slug,
                    "version": entry.version, "provider": backend.io.provider,
                    "load_seconds": round(time.perf_counter() - started, 2),
                })
            except Exception as exc:
                entry.state = "failed"
                entry.detail = str(exc)[:2000]
                log.error("version failed to load", extra={"version_id": version_id,
                                                           "error": entry.detail})

    async def _fetch_weights(self, assignment: dict) -> str:
        key = assignment.get("weights_key")
        bucket = assignment.get("weights_bucket")
        digest = assignment.get("weights_sha256") or ""
        if not key:
            raise BackendLoadError("this version has no weights in object storage")

        filename = os.path.basename(key)
        folder = os.path.join(settings.cache_dir, digest[:16] or "nodigest")
        path = os.path.join(folder, filename)
        if os.path.exists(path) and (not digest or await _verify(path, digest)):
            return path

        os.makedirs(folder, exist_ok=True)
        tmp = path + ".part"
        try:
            await asyncio.to_thread(_s3().download_file, bucket, key, tmp)
        except ClientError as exc:
            raise BackendLoadError(f"could not download weights from {bucket}/{key}: {exc}") from exc

        if digest and not await _verify(tmp, digest):
            os.remove(tmp)
            raise BackendLoadError(
                "the downloaded weights do not match the checksum recorded at upload - refusing "
                "to serve them (R1.8)"
            )
        os.replace(tmp, path)
        await asyncio.to_thread(self._trim_cache)
        return path

    def _build_backend(self, config: ModelConfig, path: str, assignment: dict) -> Backend:
        device = _device_for(assignment.get("device", "auto"), self.device_info)
        fmt = (assignment.get("artifact_format") or "").lower()
        kind = config.backend

        if fmt == "tensorrt" or kind == BackendKind.tensorrt:
            from runtime.backends.tensorrt_backend import TensorRTBackend

            return TensorRTBackend(path, device=device if device != "cpu" else "cuda:0")
        if fmt in ("pt", "torchscript") or kind in (BackendKind.torch, BackendKind.torchscript):
            from runtime.backends.torch_backend import TorchBackend

            return TorchBackend(path, device=device,
                                fp16=config.input.dtype == "float16")
        if fmt == "safetensors":
            raise BackendLoadError(
                "safetensors holds tensors but no graph, so it cannot be served on its own - "
                "export ONNX or TorchScript from the training code and upload that"
            )
        from runtime.backends.onnx_backend import OnnxBackend

        return OnnxBackend(path, device=device, threads=settings.onnx_threads,
                           static_inputs=(config.extra or {}).get("static_inputs"))

    async def _warmup(self, entry: Loaded) -> None:
        """Run the real pipeline shape a few times so the first real request is warm."""
        config = entry.config
        sizes = config.warmup.batch_sizes or [1]
        iterations = max(config.warmup.iterations, 0)
        if not iterations:
            return
        channels = 1 if (config.preprocess.grayscale or config.input.color == "GRAY") \
            else config.input.channels
        height, width = config.preprocess.resize.height, config.preprocess.resize.width
        dtype = {"float32": np.float32, "float16": np.float16, "uint8": np.uint8,
                 "int8": np.int8}[config.input.dtype]

        # A multi-input model needs its other inputs to run at all. Prefer values
        # pinned in the config (they are representative), else zeros of the shape
        # the graph declares.
        pinned = (config.extra or {}).get("warmup_inputs") \
            or (config.extra or {}).get("validation_inputs") or {}

        for size in sizes:
            shape = ((size, channels, height, width) if config.input.layout == "NCHW"
                     else (size, height, width, channels))
            batch = np.zeros(shape, dtype=dtype)
            extras = entry.backend.warmup_inputs(size)
            for name, value in pinned.items():
                array = np.asarray(value, dtype=np.float32)
                if array.ndim == 1:
                    array = array[None, ...]
                if array.shape[0] != size:
                    array = np.repeat(array[:1], size, axis=0)
                extras[name] = array
            for _ in range(iterations):
                await asyncio.to_thread(entry.backend.infer, batch, extras or None)
        log.info("warmed up", extra={"version_id": entry.version_id, "iterations": iterations,
                                     "batch_sizes": sizes})

    async def unload(self, version_id: str, *, reason: str = "requested") -> None:
        entry = self.models.get(version_id)
        if entry is None:
            return
        entry.state = "draining"
        entry.detail = reason
        if entry.batcher is not None:
            await entry.batcher.drain()
        if entry.backend is not None:
            await asyncio.to_thread(entry.backend.close)
        self.models.pop(version_id, None)
        self._locks.pop(version_id, None)
        log.info("version unloaded", extra={"version_id": version_id, "reason": reason})

    async def shutdown(self) -> None:
        for version_id in list(self.models):
            await self.unload(version_id, reason="shutdown")

    def _trim_cache(self) -> None:
        """Keep the weights cache under its budget, oldest-touched first."""
        budget = settings.cache_max_gb * 1e9
        entries = []
        total = 0
        for folder, _, files in os.walk(settings.cache_dir):
            for name in files:
                path = os.path.join(folder, name)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                total += stat.st_size
                entries.append((stat.st_atime, stat.st_size, path))
        if total <= budget:
            return
        in_use = {m.assignment.get("weights_sha256", "")[:16] for m in self.models.values()}
        for _, size, path in sorted(entries):
            if any(marker and marker in path for marker in in_use):
                continue
            try:
                os.remove(path)
                total -= size
                log.info("evicted cached weights", extra={"path": path})
            except OSError:
                pass
            if total <= budget * 0.9:
                break
        for folder, dirs, files in os.walk(settings.cache_dir, topdown=False):
            if not dirs and not files and folder != settings.cache_dir:
                shutil.rmtree(folder, ignore_errors=True)


def _batching(assignment: dict) -> dict:
    batching = assignment.get("batching") or {}
    return {
        "max_batch_size": int(batching.get("max_batch_size", settings.default_max_batch_size)),
        "max_wait_ms": int(batching.get("max_wait_ms", settings.default_max_wait_ms)),
        "max_queue_depth": int(batching.get("max_queue_depth",
                                            settings.default_max_queue_depth)),
    }


def _weights_changed(old: dict, new: dict) -> bool:
    return old.get("weights_sha256") != new.get("weights_sha256") or \
        old.get("weights_key") != new.get("weights_key")


def _device_for(requested: str, info: dict) -> str:
    requested = (requested or "auto").lower()
    if requested == "cpu":
        return "cpu"
    if requested.startswith("cuda"):
        return requested
    override = (settings.device or "auto").lower()
    if override != "auto":
        return override
    return info.get("device", "cpu")


async def _verify(path: str, digest: str) -> bool:
    def _sum() -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(8 * 1024 * 1024), b""):
                hasher.update(block)
        return hasher.hexdigest()

    return await asyncio.to_thread(_sum) == digest


_client = None


def _s3():
    global _client
    if _client is None:
        import boto3
        from botocore.client import Config

        _client = boto3.client(
            "s3", endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4",
                          s3={"addressing_style": "path" if settings.s3_force_path_style
                              else "auto"},
                          retries={"max_attempts": 5, "mode": "standard"},
                          connect_timeout=10, read_timeout=600),
        )
    return _client
