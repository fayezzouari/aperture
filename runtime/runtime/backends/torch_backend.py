"""PyTorch backend: TorchScript, a pickled module, an Ultralytics checkpoint, or
an RF-DETR state_dict.

ONNX is a *choice* here, not a toll gate. A `.pt` that carries a graph can be
served as it is, on a runtime image that has torch - conversion is for people who
want the smaller, faster, dependency-free artifact, not a tax on everyone.

Four shapes of file arrive:

  TorchScript archive   a self-contained graph; `torch.jit.load` and run it
  pickled nn.Module     the object and its class; loadable when the class's
                        package is installed, which is why Ultralytics
                        checkpoints work on the torch image and not the slim one
  RF-DETR state_dict    tensors with no graph, but the layer shapes carry the
                        architecture and `rfdetr` can rebuild it - served when
                        that package is installed, and only when every
                        checkpoint tensor finds a home (see `aperture_core.rfdetr`)
  bare state_dict       anything else with no graph and no recoverable
                        architecture - nothing can execute it without the
                        training code, so it is refused

An Ultralytics checkpoint's eager forward returns the same tensors its ONNX
export does, so the same decoder config reads both and results do not change
with the artifact format. RF-DETR's does too, with one difference the config has
to carry: the ONNX exporter prepends a dead logit column and the eager head does
not, so a version served as `.pt` wants `class_offset: 0` where its exported
twin wants 1.
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

        # RF-DETR's head answers as a dict that also carries the auxiliary
        # decoder outputs; those are training signal, not predictions, so its
        # outputs are picked by name rather than flattened.
        self._rfdetr = False
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
                rebuilt = self._rebuild(path, loaded)
                if rebuilt is not None:
                    return rebuilt
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

    def _rebuild(self, path: str, loaded: dict):
        """A tensors-only checkpoint whose architecture is recoverable, rebuilt.

        Only RF-DETR qualifies today, and only because its layer shapes state the
        architecture unambiguously. Returns None when this is not one of those,
        so the caller can raise the message it already has.
        """
        from aperture_core.rfdetr import geometry_from_state_dict, inner_module, tensors_of

        tensors = tensors_of(loaded)
        if not any("transformer.decoder" in key for key in tensors):
            return None

        detected = geometry_from_state_dict(loaded)
        try:
            from aperture_core.rfdetr import build as rfdetr_build

            model, variant, geometry = rfdetr_build(path, device=str(self.device),
                                                    detected=detected)
        except ImportError as exc:
            raise BackendLoadError(
                "these are RF-DETR weights, which need the rfdetr package to rebuild. This "
                "runtime image does not have it - start one that does "
                "(docker compose --profile torch up -d runtime-torch), or convert to ONNX."
            ) from exc
        except Exception as exc:
            raise BackendLoadError(
                f"these RF-DETR weights could not be rebuilt: {str(exc)[:300]}"
            ) from exc

        module = inner_module(model)
        if module is None:
            raise BackendLoadError(
                f"{variant} rebuilt, but its inner module could not be reached"
            )
        log.info("serving rf-detr weights directly",
                 extra={"variant": variant,
                        "dec_layers": geometry.get("dec_layers"),
                        "num_queries": geometry.get("num_queries"),
                        "resolution": geometry.get("resolution")})
        self._rfdetr = True
        return module.to(self.device)

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
        flat = _rfdetr_outputs(output) if self._rfdetr else _flatten(output)
        if not self.io.output_names:
            self.io.output_names = [f"output_{i}" for i in range(len(flat))]
        return [t.detach().float().cpu().numpy() for t in flat]

    def close(self) -> None:
        self.module = None
        if self.device.type == "cuda":
            self.torch.cuda.empty_cache()


def _rfdetr_outputs(output) -> list:
    """Boxes, logits, masks - in the order the `rf-detr` presets index them.

    The head also returns `aux_outputs` and `enc_outputs`. Flattening the dict
    would fold those in and shift every index, so the three that are predictions
    are taken by name and the rest ignored.
    """
    from aperture_core.rfdetr import OUTPUT_ORDER

    if not isinstance(output, dict):
        return _flatten(output)
    picked = [output[name] for name in OUTPUT_ORDER if name in output]
    if not picked:
        raise RuntimeError(
            "this rf-detr head returned none of " + ", ".join(OUTPUT_ORDER) +
            f" - got {sorted(output)}"
        )
    return picked


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
