"""preprocess -> infer -> postprocess, with the per-stage timings of R5.2."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import numpy as np

from aperture_core.configspec import ModelConfig
from aperture_core.images import Geometry
from aperture_core.preprocess import apply_geometry, preprocess_batch, to_tensor
from aperture_core.postprocess import postprocess
from aperture_core.results import ImageResult
from aperture_core.sandbox import HookError, run_hook

InferFn = Callable[[np.ndarray], list[np.ndarray]]


@dataclass(slots=True)
class StageTimings:
    preprocess_ms: float = 0.0
    inference_ms: float = 0.0
    postprocess_ms: float = 0.0

    @property
    def total_ms(self) -> float:
        return self.preprocess_ms + self.inference_ms + self.postprocess_ms

    def as_dict(self) -> dict:
        d = asdict(self)
        d["total_ms"] = round(self.total_ms, 3)
        return {k: round(v, 3) for k, v in d.items()}


@dataclass(slots=True)
class PipelineOutput:
    results: list[ImageResult]
    timings: StageTimings
    geometries: list[Geometry] = field(default_factory=list)


def _hook_preprocess(images: list[np.ndarray], cfg: ModelConfig
                     ) -> tuple[np.ndarray, list[Geometry]]:
    tensors, geoms = [], []
    for arr in images:
        res = run_hook(
            kind="preprocess", source=cfg.hook.preprocess_source or "", fn="preprocess",
            config=cfg.model_dump(mode="json"), image=arr,
            timeout_ms=cfg.hook.timeout_ms, memory_mb=cfg.hook.memory_mb,
        )
        tensor = res.arrays.get("tensor")
        if tensor is None:
            raise HookError("preprocess hook returned no tensor")
        tensors.append(tensor)
        g = res.extra.get("geometry") or {}
        _, default = apply_geometry(arr, cfg)
        geoms.append(
            Geometry(
                orig_w=arr.shape[1], orig_h=arr.shape[0],
                net_w=int(g.get("net_w", default.net_w)), net_h=int(g.get("net_h", default.net_h)),
                scale_x=float(g.get("scale_x", default.scale_x)),
                scale_y=float(g.get("scale_y", default.scale_y)),
                pad_x=float(g.get("pad_x", default.pad_x)),
                pad_y=float(g.get("pad_y", default.pad_y)),
            )
        )
    return np.stack(tensors, axis=0), geoms


def _hook_postprocess(outputs: list[np.ndarray], cfg: ModelConfig, geoms: list[Geometry]
                      ) -> list[ImageResult]:
    res = run_hook(
        kind="postprocess", source=cfg.hook.postprocess_source or "", fn="postprocess",
        config=cfg.model_dump(mode="json"), outputs=outputs,
        geometry=asdict(geoms[0]) if geoms else {},
        timeout_ms=cfg.hook.timeout_ms, memory_mb=cfg.hook.memory_mb,
    )
    payload = res.extra.get("results")
    if payload is None:
        raise HookError("postprocess hook must return {'results': [...]} matching the API contract")
    return [ImageResult.model_validate(item) for item in payload]


def run_pipeline(images: list[np.ndarray], cfg: ModelConfig, infer: InferFn) -> PipelineOutput:
    t = StageTimings()

    t0 = time.perf_counter()
    if cfg.hook.enabled and cfg.hook.preprocess_source:
        batch, geoms = _hook_preprocess(images, cfg)
    else:
        batch, geoms = preprocess_batch(images, cfg)
    t.preprocess_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    outputs = infer(batch)
    t.inference_ms = (time.perf_counter() - t1) * 1000

    t2 = time.perf_counter()
    if cfg.hook.enabled and cfg.hook.postprocess_source:
        results = _hook_postprocess(list(outputs), cfg, geoms)
    else:
        results = postprocess(list(outputs), cfg, geoms)
    t.postprocess_ms = (time.perf_counter() - t2) * 1000

    for i, r in enumerate(results):
        r.index = i
    return PipelineOutput(results=results, timings=t, geometries=geoms)


def single_tensor(image: np.ndarray, cfg: ModelConfig) -> tuple[np.ndarray, Geometry]:
    """One-image variant used by the shape check in the upload validator."""
    resized, geom = apply_geometry(image, cfg)
    return to_tensor(resized, cfg)[None, ...], geom
