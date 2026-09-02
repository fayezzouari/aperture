"""ONNX Runtime backend - the default, and the one we recommend for serving.

Provider selection is explicit rather than "whatever is installed": a runtime
that was meant to be on the GPU and quietly fell back to CPU is a latency
regression nobody notices until the queue backs up, so the fallback is logged
loudly and reported in the heartbeat.
"""

from __future__ import annotations

import logging

import numpy as np

from runtime.backends.base import Backend, BackendLoadError, IOSpec, MissingInputError

log = logging.getLogger("runtime.onnx")


class OnnxBackend(Backend):
    def __init__(self, path: str, *, device: str = "cpu", threads: int = 0,
                 static_inputs: dict | None = None):
        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover
            raise BackendLoadError("onnxruntime is not installed in this runtime image") from exc

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level = 3
        if threads:
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = max(1, threads // 2)

        available = set(ort.get_available_providers())
        wanted: list = []
        if device.startswith("cuda"):
            index = int(device.split(":")[1]) if ":" in device else 0
            if "TensorrtExecutionProvider" in available:
                wanted.append(("TensorrtExecutionProvider",
                               {"device_id": index, "trt_fp16_enable": True}))
            if "CUDAExecutionProvider" in available:
                wanted.append(("CUDAExecutionProvider", {"device_id": index}))
            if not wanted:
                log.warning(
                    "cuda was requested but onnxruntime only offers %s - running on CPU",
                    sorted(available),
                )
        wanted.append("CPUExecutionProvider")

        try:
            self.session = ort.InferenceSession(path, sess_options=options, providers=wanted)
        except Exception as exc:
            raise BackendLoadError(f"onnxruntime could not load this model: {exc}") from exc

        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        if not inputs:
            raise BackendLoadError("this ONNX graph declares no inputs")

        self.primary_input = inputs[0].name
        self.static_inputs = {k: np.asarray(v, dtype=np.float32)
                              for k, v in (static_inputs or {}).items()}
        # Inputs beyond the image may be satisfied per request (the caller sends
        # them under `inputs`) or once at load time (config.extra.static_inputs).
        # Only complain at load time; a per-request name is checked on use.
        self.secondary_inputs = [i.name for i in inputs[1:]]
        self._input_dtypes = {i.name: _numpy_dtype(i.type) for i in inputs}

        self.io = IOSpec(
            input_names=[i.name for i in inputs],
            output_names=[o.name for o in outputs],
            input_shapes={i.name: list(i.shape) for i in inputs},
            output_shapes={o.name: list(o.shape) for o in outputs},
            provider=self.session.get_providers()[0],
            dynamic_batch=_dynamic_batch(inputs[0].shape),
        )
        self._input_dtype = _numpy_dtype(inputs[0].type)
        log.info("loaded onnx model", extra={"provider": self.io.provider,
                                             "inputs": self.io.input_names,
                                             "outputs": self.io.output_names})

    def infer(self, batch: np.ndarray,
              extra_inputs: dict[str, np.ndarray] | None = None) -> list[np.ndarray]:
        if self._input_dtype is not None and batch.dtype != self._input_dtype:
            batch = batch.astype(self._input_dtype)
        feed = {self.primary_input: batch}

        for name in self.secondary_inputs:
            supplied = (extra_inputs or {}).get(name)
            if supplied is not None:
                value = np.asarray(supplied)
            elif name in self.static_inputs:
                value = self.static_inputs[name]
            else:
                raise MissingInputError(
                    f"this model needs an input named '{name}' alongside the image. Send it as "
                    f'inputs={{"{name}": [...]}} on the request, or pin a constant under '
                    "config.extra.static_inputs"
                )
            if value.ndim == 0 or value.shape[0] != batch.shape[0]:
                # a single row broadcast across the batch (a static input, or one
                # value the caller meant for every image)
                value = np.repeat(np.asarray(value)[None, ...], batch.shape[0], axis=0) \
                    if value.shape and value.shape[0] != batch.shape[0] else value
            dtype = self._input_dtypes.get(name) or np.float32
            feed[name] = np.ascontiguousarray(value, dtype=dtype)

        return list(self.session.run(self.io.output_names, feed))

    def warmup_inputs(self, batch: int) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        for name in self.secondary_inputs:
            if name in self.static_inputs:
                value = self.static_inputs[name]
                if value.ndim and value.shape[0] != batch:
                    value = np.repeat(np.asarray(value)[None, ...], batch, axis=0) \
                        if value.shape[0] == 1 or value.ndim == 1 else value
                out[name] = value
                continue
            shape = [
                batch if index == 0 else (dim if isinstance(dim, int) and dim > 0 else 1)
                for index, dim in enumerate(self.io.input_shapes.get(name) or [batch, 1])
            ]
            dtype = self._input_dtypes.get(name) or np.float32
            out[name] = np.zeros(tuple(shape), dtype=dtype)
        return out

    def close(self) -> None:
        self.session = None


def _dynamic_batch(shape: list) -> bool:
    if not shape:
        return False
    first = shape[0]
    return not isinstance(first, int) or first <= 0


def _numpy_dtype(onnx_type: str):
    return {
        "tensor(float)": np.float32,
        "tensor(float16)": np.float16,
        "tensor(uint8)": np.uint8,
        "tensor(int8)": np.int8,
        "tensor(int32)": np.int32,
        "tensor(int64)": np.int64,
        "tensor(double)": np.float64,
    }.get(onnx_type)
