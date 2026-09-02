"""PyTorch backend: TorchScript, a pickled module, or an Ultralytics checkpoint.

ONNX is a *choice* here, not a toll gate. A `.pt` that carries a graph can be
served as it is, on a runtime image that has torch - conversion is for people who
want the smaller, faster, dependency-free artifact, not a tax on everyone.

Three shapes of file arrive, and only the last is genuinely unservable:

  TorchScript archive   a self-contained graph; `torch.jit.load` and run it
  pickled nn.Module     the object and its class; loadable when the class's
                        package is installed, which is why Ultralytics
                        checkpoints work on the torch image and not the slim one
  bare state_dict       tensors with no graph and no class - nothing can execute
                        this without the training code, so it is refused

An Ultralytics checkpoint's eager forward returns the same tensors its ONNX
export does, so the same decoder config reads both and results do not change
with the artifact format.
"""

from __future__ import annotations

import logging

import numpy as np

from runtime.backends.base import Backend, BackendLoadError, IOSpec

log = logging.getLogger("runtime.torch")


class TorchBackend(Backend):
    def __init__(self, path: str, *, device: str = "cpu", fp16: bool = False):
        try:
            import torch
        except ImportError as exc:
            raise BackendLoadError(
                "this runtime image has no torch, so it cannot run a .pt. Start one that can - "
                "`docker compose --profile torch up -d runtime-torch` - and placement will send "
                "this version there. Converting to ONNX is the other option, not the only one."
            ) from exc

        self.torch = torch
        self.device = torch.device("cuda" if device.startswith("cuda") and
                                   torch.cuda.is_available() else "cpu")
        if device.startswith("cuda") and self.device.type == "cpu":
            log.warning("cuda requested but torch reports no CUDA device - running on CPU")

        module = self._load(path)
        module.eval()
        self.fp16 = fp16 and self.device.type == "cuda"
        if self.fp16:
            module.half()
        self.module = module
        self.io = IOSpec(input_names=["input"], output_names=[], provider=str(self.device),
                         dynamic_batch=True)

    def _load(self, path: str):
        """TorchScript first, then a pickled module, then Ultralytics."""
        torch = self.torch
        try:
            return torch.jit.load(path, map_location=self.device)
        except Exception as jit_error:
            log.info("not a torchscript archive, trying an eager load",
                     extra={"error": str(jit_error)[:160]})

        # Ultralytics stores its own classes in the checkpoint, so importing the
        # package first is what makes the unpickle resolve.
        try:
            import ultralytics  # noqa: F401
        except ImportError:
            pass

        try:
            loaded = torch.load(path, map_location=self.device, weights_only=False)
        except Exception as exc:
            raise BackendLoadError(
                f"this file could not be loaded by torch ({str(exc)[:200]}). If it was saved by "
                "a framework whose package is not installed in this runtime, install it or "
                "convert the weights to ONNX."
            ) from exc

        candidate = loaded
        if isinstance(loaded, dict):
            for key in ("model", "ema", "module", "net"):
                if key in loaded and hasattr(loaded[key], "forward"):
                    candidate = loaded[key]
                    break
            else:
                raise BackendLoadError(
                    "this .pt holds tensors but no model object, so nothing can execute it "
                    "without the training code. Export TorchScript or ONNX from the code that "
                    "produced it and upload that."
                )

        if not hasattr(candidate, "forward"):
            raise BackendLoadError(
                f"loaded a {type(candidate).__name__}, which is not a runnable module"
            )

        # Ultralytics keeps its weights in half by default; the pipeline feeds
        # float32, and mixing the two raises rather than casting silently.
        candidate = candidate.float()
        if hasattr(candidate, "fuse"):
            try:
                candidate = candidate.fuse()
            except Exception as exc:  # fusing is an optimisation, never a requirement
                log.info("could not fuse the module", extra={"error": str(exc)[:120]})
        return candidate.to(self.device)

    def infer(self, batch: np.ndarray,
              extra_inputs: dict[str, np.ndarray] | None = None) -> list[np.ndarray]:
        torch = self.torch
        tensor = torch.from_numpy(np.ascontiguousarray(batch)).to(self.device)
        if self.fp16:
            tensor = tensor.half()
        extras = [
            torch.from_numpy(np.ascontiguousarray(value)).to(self.device)
            for _, value in sorted((extra_inputs or {}).items())
        ]
        with torch.inference_mode():
            output = self.module(tensor, *extras)
        flat = _flatten(output)
        if not self.io.output_names:
            self.io.output_names = [f"output_{i}" for i in range(len(flat))]
        return [t.detach().float().cpu().numpy() for t in flat]

    def close(self) -> None:
        self.module = None
        if self.device.type == "cuda":
            self.torch.cuda.empty_cache()


def _flatten(output) -> list:
    if isinstance(output, (list, tuple)):
        out = []
        for item in output:
            out.extend(_flatten(item))
        return out
    if isinstance(output, dict):
        out = []
        for _, value in sorted(output.items()):
            out.extend(_flatten(value))
        return out
    return [output]
