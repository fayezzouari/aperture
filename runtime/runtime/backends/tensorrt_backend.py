"""TensorRT engine backend.

Engines are built for one GPU architecture, one TensorRT version and one driver.
Aperture therefore treats an engine as a *device-specific artifact*: the runtime
refuses to load one it cannot deserialise instead of silently falling back, so a
mismatch shows up as a failed replica with a clear reason rather than as a
mysterious accuracy or latency change.
"""

from __future__ import annotations

import logging

import numpy as np

from runtime.backends.base import Backend, BackendLoadError, IOSpec

log = logging.getLogger("runtime.tensorrt")


class TensorRTBackend(Backend):
    def __init__(self, path: str, *, device: str = "cuda:0"):
        try:
            import pycuda.autoinit  # noqa: F401  (initialises the CUDA context)
            import pycuda.driver as cuda
            import tensorrt as trt
        except ImportError as exc:
            raise BackendLoadError(
                "TensorRT and pycuda are not in this runtime image - use the "
                "aperture-runtime:trt image for .engine artifacts, or deploy the ONNX version"
            ) from exc

        self.trt, self.cuda = trt, cuda
        logger = trt.Logger(trt.Logger.WARNING)
        with open(path, "rb") as fh, trt.Runtime(logger) as runtime:
            engine = runtime.deserialize_cuda_engine(fh.read())
        if engine is None:
            raise BackendLoadError(
                "this engine could not be deserialised - it was almost certainly built for a "
                "different GPU, TensorRT version or driver. Rebuild it on this node."
            )
        self.engine = engine
        self.context = engine.create_execution_context()

        names = [engine.get_tensor_name(i) for i in range(engine.num_io_tensors)]
        self.input_names = [n for n in names
                            if engine.get_tensor_mode(n) == trt.TensorIOMode.INPUT]
        self.output_names = [n for n in names
                             if engine.get_tensor_mode(n) == trt.TensorIOMode.OUTPUT]
        self.io = IOSpec(
            input_names=self.input_names, output_names=self.output_names,
            input_shapes={n: list(engine.get_tensor_shape(n)) for n in self.input_names},
            output_shapes={n: list(engine.get_tensor_shape(n)) for n in self.output_names},
            provider="TensorRT",
            dynamic_batch=any(d < 0 for d in engine.get_tensor_shape(self.input_names[0])),
        )
        self.stream = cuda.Stream()

    def infer(self, batch: np.ndarray,
              extra_inputs: dict[str, np.ndarray] | None = None) -> list[np.ndarray]:
        if extra_inputs:
            raise NotImplementedError(
                "the TensorRT backend serves single-input engines; bind extra inputs by "
                "building the engine with them folded in, or serve the ONNX version"
            )
        cuda, trt = self.cuda, self.trt
        name = self.input_names[0]
        batch = np.ascontiguousarray(batch, dtype=np.float32)
        self.context.set_input_shape(name, batch.shape)

        device_in = cuda.mem_alloc(batch.nbytes)
        cuda.memcpy_htod_async(device_in, batch, self.stream)
        self.context.set_tensor_address(name, int(device_in))

        host_outputs, device_outputs = [], []
        for output in self.output_names:
            shape = tuple(self.context.get_tensor_shape(output))
            dtype = trt.nptype(self.engine.get_tensor_dtype(output))
            host = cuda.pagelocked_empty(int(np.prod(shape)), dtype)
            device = cuda.mem_alloc(host.nbytes)
            self.context.set_tensor_address(output, int(device))
            host_outputs.append((host, shape))
            device_outputs.append(device)

        self.context.execute_async_v3(stream_handle=self.stream.handle)
        for (host, _), device in zip(host_outputs, device_outputs):
            cuda.memcpy_dtoh_async(host, device, self.stream)
        self.stream.synchronize()
        return [host.reshape(shape).copy() for host, shape in host_outputs]

    def close(self) -> None:
        self.context = None
        self.engine = None
