"""R4.2 / R4.3 - dynamic request batching with explicit backpressure.

One consumer task per loaded version pulls waiting requests off a bounded queue,
concatenates them up to `max_batch_size` (waiting at most `max_wait_ms` for more
to show up) and runs a single forward pass. Preprocessing and postprocessing stay
in the request handler, so they overlap with the GPU instead of serialising
behind it.

When the queue is full the request is refused with `queue_full` rather than
queued anyway: a silent pileup turns a capacity problem into a timeout storm.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger("runtime.batcher")


class QueueFull(RuntimeError):
    def __init__(self, depth: int, limit: int):
        self.depth, self.limit = depth, limit
        super().__init__(f"queue depth {depth} has reached its limit of {limit}")


class Deadline(RuntimeError):
    pass


@dataclass
class _Item:
    tensor: np.ndarray
    future: asyncio.Future
    count: int
    deadline: float
    # named non-image inputs for this request, batch axis first
    extras: dict[str, np.ndarray] = field(default_factory=dict)
    enqueued_at: float = field(default_factory=time.perf_counter)


@dataclass
class BatchResult:
    outputs: list[np.ndarray]
    queue_ms: float
    inference_ms: float
    batch_executed: int


class Batcher:
    def __init__(self, *, version_id: str, backend, semaphore: asyncio.Semaphore,
                 max_batch_size: int = 8, max_wait_ms: int = 8, max_queue_depth: int = 64):
        self.version_id = version_id
        self.backend = backend
        self.semaphore = semaphore
        self.max_batch_size = max(1, max_batch_size)
        self.max_wait_ms = max(0, max_wait_ms)
        self.max_queue_depth = max(1, max_queue_depth)
        self.queue: asyncio.Queue[_Item] = asyncio.Queue()
        self.in_flight = 0
        self.batches = 0
        self.requests = 0
        self.splittable = True
        self._task: asyncio.Task | None = None
        self._stopping = False

    # ------------------------------------------------------------------ control

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"batcher:{self.version_id}")

    def reconfigure(self, batching: dict) -> None:
        if not batching:
            return
        self.max_batch_size = max(1, int(batching.get("max_batch_size", self.max_batch_size)))
        self.max_wait_ms = max(0, int(batching.get("max_wait_ms", self.max_wait_ms)))
        self.max_queue_depth = max(1, int(batching.get("max_queue_depth", self.max_queue_depth)))

    async def drain(self) -> None:
        """Let in-flight work finish, refuse anything new, then stop."""
        self._stopping = True
        deadline = time.perf_counter() + 30
        while (self.queue.qsize() or self.in_flight) and time.perf_counter() < deadline:
            await asyncio.sleep(0.05)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    @property
    def queue_depth(self) -> int:
        return self.queue.qsize()

    # ------------------------------------------------------------------ submit

    async def submit(self, tensor: np.ndarray, *, deadline_ms: int,
                     extras: dict[str, np.ndarray] | None = None) -> BatchResult:
        if self._stopping:
            raise QueueFull(self.queue_depth, self.max_queue_depth)
        depth = self.queue.qsize()
        if depth >= self.max_queue_depth:
            raise QueueFull(depth, self.max_queue_depth)

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        item = _Item(tensor=tensor, future=future, count=tensor.shape[0],
                     deadline=time.perf_counter() + deadline_ms / 1000,
                     extras=extras or {})
        await self.queue.put(item)
        try:
            return await asyncio.wait_for(future, timeout=deadline_ms / 1000)
        except TimeoutError as exc:
            raise Deadline(
                f"the request exceeded its {deadline_ms} ms budget while queued or executing"
            ) from exc

    # -------------------------------------------------------------------- loop

    async def _run(self) -> None:
        while True:
            first = await self.queue.get()
            batch = [first]
            collected = first.count

            if self.max_batch_size > 1 and self.splittable:
                window = self.max_wait_ms / 1000
                started = time.perf_counter()
                while collected < self.max_batch_size:
                    remaining = window - (time.perf_counter() - started)
                    if remaining <= 0:
                        break
                    try:
                        nxt = await asyncio.wait_for(self.queue.get(), timeout=remaining)
                    except TimeoutError:
                        break
                    if nxt.tensor.shape[1:] != first.tensor.shape[1:] or \
                            set(nxt.extras) != set(first.extras):
                        # a differently-shaped request, or one with different named
                        # inputs, cannot ride along; put it back and run it next
                        self.queue.put_nowait(nxt)
                        break
                    batch.append(nxt)
                    collected += nxt.count

            await self._execute(batch)

    async def _execute(self, batch: list[_Item]) -> None:
        # nothing tracks queue.join(), so task_done() is deliberately not used here
        alive = [item for item in batch if not item.future.done()]
        if not alive:
            return

        now = time.perf_counter()
        for item in list(alive):
            if item.deadline < now:
                if not item.future.done():
                    item.future.set_exception(
                        Deadline("the request expired in the queue before it could run")
                    )
                alive.remove(item)
        if not alive:
            return

        tensor = alive[0].tensor if len(alive) == 1 else np.concatenate(
            [item.tensor for item in alive], axis=0
        )
        extras: dict[str, np.ndarray] = {}
        if alive[0].extras:
            for name in alive[0].extras:
                parts = [np.atleast_2d(np.asarray(item.extras[name])) for item in alive]
                extras[name] = parts[0] if len(parts) == 1 else np.concatenate(parts, axis=0)
        total = int(tensor.shape[0])
        self.in_flight += total

        started = time.perf_counter()
        try:
            async with self.semaphore:
                outputs = await asyncio.to_thread(self.backend.infer, tensor, extras or None)
            inference_ms = (time.perf_counter() - started) * 1000

            if len(alive) == 1:
                self._resolve(alive[0], outputs, inference_ms, total)
            else:
                sliced = self._split(outputs, [item.count for item in alive], total)
                if sliced is None:
                    # this graph does not keep the batch axis - stop batching it and retry
                    log.warning(
                        "disabling batching for this version: its outputs do not carry a "
                        "batch axis of the expected size",
                        extra={"version_id": self.version_id},
                    )
                    self.splittable = False
                    self.max_batch_size = 1
                    for item in alive:
                        self.queue.put_nowait(item)
                    return
                for item, item_outputs in zip(alive, sliced):
                    self._resolve(item, item_outputs, inference_ms, total)

            self.batches += 1
            self.requests += len(alive)
        except Exception as exc:
            for item in alive:
                if not item.future.done():
                    item.future.set_exception(exc)
        finally:
            self.in_flight -= total

    def _resolve(self, item: _Item, outputs: list[np.ndarray], inference_ms: float,
                 batch_executed: int) -> None:
        if item.future.done():
            return
        item.future.set_result(BatchResult(
            outputs=outputs,
            queue_ms=(time.perf_counter() - item.enqueued_at) * 1000 - inference_ms,
            inference_ms=inference_ms,
            batch_executed=batch_executed,
        ))

    @staticmethod
    def _split(outputs: list[np.ndarray], counts: list[int], total: int
               ) -> list[list[np.ndarray]] | None:
        for output in outputs:
            if output.ndim == 0 or output.shape[0] != total:
                return None
        result: list[list[np.ndarray]] = []
        offset = 0
        for count in counts:
            result.append([output[offset:offset + count] for output in outputs])
            offset += count
        return result
