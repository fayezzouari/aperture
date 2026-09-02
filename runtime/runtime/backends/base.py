"""What every inference backend must provide."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass
class IOSpec:
    input_names: list[str] = field(default_factory=list)
    output_names: list[str] = field(default_factory=list)
    input_shapes: dict[str, list] = field(default_factory=dict)
    output_shapes: dict[str, list] = field(default_factory=dict)
    provider: str = ""
    dynamic_batch: bool = True


class Backend(ABC):
    """A loaded set of weights, ready to be called with an NCHW/NHWC batch."""

    io: IOSpec

    @abstractmethod
    def infer(self, batch: np.ndarray,
              extra_inputs: dict[str, np.ndarray] | None = None) -> list[np.ndarray]:
        """Run one forward pass. Must be safe to call from a worker thread.

        `extra_inputs` carries the model's non-image inputs for this batch - a
        geometry vector, a prompt, a text embedding. Each array's first axis is
        the batch axis, aligned with `batch`.
        """

    @abstractmethod
    def close(self) -> None:
        ...

    def warmup_inputs(self, batch: int) -> dict[str, np.ndarray]:
        """Placeholder values for the model's non-image inputs, for warm-up only.

        Warm-up exists to compile kernels and allocate buffers, so zeros of the
        right shape are enough - but a multi-input model cannot run *at all*
        without them, and a version that never warms up never becomes routable.
        """
        return {}

    @property
    def supports_batching(self) -> bool:
        return self.io.dynamic_batch


class BackendLoadError(RuntimeError):
    pass


class MissingInputError(ValueError):
    """The caller left out an input the model requires. A client error, not ours."""
