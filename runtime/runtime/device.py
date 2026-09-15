"""GPU detection without hard-depending on torch or pynvml."""

from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger("runtime.device")


def formats() -> list[str]:
    """Artifact formats this image can actually load.

    Placement uses this: a `.pt` has to land on a runtime that has torch, and a
    slim ONNX-only replica should never be handed one. Reporting what is
    importable - rather than assuming from an image tag - means a runtime built
    with extra packages advertises them without any config.
    """
    found = []
    try:
        import onnxruntime  # noqa: F401
        found += ["onnx", "ort"]
    except ImportError:
        pass
    try:
        import torch  # noqa: F401
        found += ["torchscript", "pt"]
    except ImportError:
        pass
    try:
        import ultralytics  # noqa: F401
        found += ["ultralytics"]
    except ImportError:
        pass
    try:
        import rfdetr  # noqa: F401
        found += ["rfdetr"]
    except ImportError:
        pass
    try:
        import tensorrt  # noqa: F401
        found += ["tensorrt", "engine", "plan"]
    except ImportError:
        pass
    return found


def detect() -> dict:
    """Returns {device, gpu_name, total_memory_mb, free_memory_mb, providers}."""
    info = {"device": "cpu", "gpu_name": "", "total_memory_mb": 0, "free_memory_mb": 0,
            "providers": []}
    try:
        import onnxruntime as ort

        info["providers"] = list(ort.get_available_providers())
    except Exception:
        pass

    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=name,memory.total,memory.used",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            first = (out.stdout or "").strip().splitlines()
            if first:
                name, total, used = [part.strip() for part in first[0].split(",")]
                info.update({
                    "device": "cuda:0", "gpu_name": name,
                    "total_memory_mb": int(float(total)),
                    "free_memory_mb": max(0, int(float(total)) - int(float(used))),
                })
        except Exception:
            log.debug("nvidia-smi query failed", exc_info=True)

    if info["device"] == "cpu" and "CUDAExecutionProvider" in info["providers"]:
        info["device"] = "cuda:0"
    return info


def refresh_memory(info: dict) -> dict:
    if not info["device"].startswith("cuda"):
        return info
    smi = shutil.which("nvidia-smi")
    if not smi:
        return info
    try:
        out = subprocess.run([smi, "--query-gpu=memory.total,memory.used",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, check=False)
        line = (out.stdout or "").strip().splitlines()
        if line:
            total, used = [p.strip() for p in line[0].split(",")]
            info["total_memory_mb"] = int(float(total))
            info["free_memory_mb"] = max(0, int(float(total)) - int(float(used)))
    except Exception:
        pass
    return info
