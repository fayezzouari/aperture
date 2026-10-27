"""Host memory accounting, so a runtime refuses a version instead of being OOM-killed.

Loading every assigned version regardless of what fits is how a runtime ends up
killed by the kernel mid-request, restarted, handed the same assignments, and
killed again. Before a load the store asks this module whether the version's
expected footprint fits inside the budget, and after the load it records what
the version actually cost, so the next estimate - here, on another replica, or
after a restart - is a measurement rather than a guess.

The budget is `AP_RUNTIME_MEMORY_BUDGET_MB` when set, otherwise a fraction of
the container's cgroup limit, otherwise of the host's RAM.

Requests draw on the same budget. Resident versions leave some headroom, and
every in-flight request needs working memory on top: the decoded image, its
tensor, the batch it joins, masks at the original resolution. `Admission`
reserves that per request before anything is decoded, so a burst of large
images is turned away with a retryable queue_full instead of getting the
process cgroup-OOM-killed and every resident version reloaded.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import resource
import sys
import threading
import time
from dataclasses import dataclass

from runtime.settings import settings

log = logging.getLogger("runtime.memory")

# cgroup v1 reports "no limit" as a huge page-aligned number rather than "max"
_NO_LIMIT = 1 << 60


def _read(path: str) -> str | None:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def cgroup_limit_mb() -> int | None:
    """The container's memory limit, or None when it has none."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        raw = _read(path)
        if raw and raw != "max":
            try:
                value = int(raw)
            except ValueError:
                continue
            if 0 < value < _NO_LIMIT:
                return value // (1024 * 1024)
    return None


def host_total_mb() -> int:
    raw = _read("/proc/meminfo")
    if raw:
        for line in raw.splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        return 0


def rss_mb() -> int:
    """Resident memory of this process right now."""
    raw = _read("/proc/self/status")
    if raw:
        for line in raw.splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    # no procfs (macOS dev runs): the peak is the best number available
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak // (1024 * 1024) if sys.platform == "darwin" else peak // 1024


def budget_mb() -> int:
    if settings.memory_budget_mb > 0:
        return settings.memory_budget_mb
    ceiling = cgroup_limit_mb() or host_total_mb()
    return int(ceiling * settings.memory_budget_fraction)


class Ledger:
    """What each set of weights cost to hold resident, keyed by its checksum.

    Persisted next to the weights cache (a volume), so a restarted runtime
    admits versions by what they measured last time, not by a fresh guess.
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.costs: dict[str, int] = {}
        try:
            with open(path) as fh:
                self.costs = {k: int(v) for k, v in json.load(fh).items()}
        except (OSError, ValueError):
            self.costs = {}

    def get(self, key: str) -> int | None:
        return self.costs.get(key) if key else None

    def record(self, key: str, cost_mb: int) -> None:
        if not key or cost_mb <= 0:
            return
        with self._lock:
            self.costs[key] = cost_mb
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w") as fh:
                    json.dump(self.costs, fh)
                os.replace(tmp, self.path)
            except OSError:
                log.debug("could not persist memory costs", exc_info=True)


def estimate_mb(weights_bytes: int, measured_mb: int | None) -> int:
    """Expected resident cost of a version: what it measured, or a multiple of its weights.

    An inference session holds the weights plus its arena, intermediate
    activations and per-thread scratch; several times the file size is typical
    on CPU, so the multiplier errs on the side of refusing.
    """
    if measured_mb:
        return measured_mb
    weights_mb = weights_bytes / (1024 * 1024)
    return max(settings.memory_min_version_mb,
               int(weights_mb * settings.memory_estimate_factor))


def ceiling_mb() -> int:
    """The hard line for requests: just under the point where the kernel kills the process.

    The placement budget deliberately stops short of the limit (80% by default)
    so the rest is there for in-flight requests; requests are measured against
    the limit itself, minus a safety margin.
    """
    if settings.request_memory_ceiling_mb > 0:
        return settings.request_memory_ceiling_mb
    return int((cgroup_limit_mb() or host_total_mb()) * settings.request_memory_ceiling_fraction)


def request_need_mb(sizes: list[tuple[int, int]]) -> int:
    """Working memory a request is expected to need, from its images' dimensions."""
    decoded = sum(w * h * 3 for w, h in sizes)
    return max(1, int(decoded * settings.request_memory_factor / (1024 * 1024)))


@dataclass(slots=True)
class Ticket:
    version_id: str
    images: int
    reserved_mb: int


class Admission:
    """In-flight working memory, reserved per request below the memory ceiling.

    The headroom is the ceiling minus the process's resident size when nothing
    is in flight, so resident versions and the interpreter are already counted;
    the reservations of requests that are still running are what gets added.

    A request reserves its image buffers, plus activation memory for every image
    that takes its version past the most it has ever run at once. Below that
    high-water mark the inference arena already holds the room (it does not
    shrink), and the idle size already counts it; above it the arena grows.
    """

    TRIM_EVERY_S = 2.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reserved_mb = 0
        self.in_flight = 0
        self.idle_rss_mb = rss_mb()
        self._last_trim = 0.0
        self._busy: dict[str, int] = {}        # images in flight per version
        self._high_water: dict[str, int] = {}  # most images a version's arena has held
        self.waiting = 0                       # requests parked in reserve() for memory
        self._freed: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def warmed(self, version_id: str, images: int) -> None:
        """A version was (re)loaded and warmed at this batch size: its arena starts here."""
        with self._lock:
            self._high_water[version_id] = max(1, images)

    def headroom_mb(self) -> int:
        return max(0, ceiling_mb() - self.idle_rss_mb)

    @staticmethod
    def request_space_mb() -> int:
        """Room set aside for requests by design: what the placement budget leaves free.

        A request bigger than this cannot be promised anywhere, however idle; a
        smaller one refused now only has to wait or go to another replica.
        """
        return max(0, ceiling_mb() - budget_mb())

    def try_reserve(self, version_id: str, images: int, buffers_mb: int,
                    activation_mb: int) -> tuple[Ticket | None, int, int]:
        """(ticket or None, what the request needs, headroom). Never blocks."""
        with self._lock:
            if self.in_flight == 0:
                self.idle_rss_mb = rss_mb()
            headroom = self.headroom_mb()
            busy = self._busy.get(version_id, 0) + images
            beyond = max(0, busy - self._high_water.get(version_id, 1))
            need = buffers_mb + activation_mb * beyond
            if self.reserved_mb + need > headroom:
                return None, need, headroom
            self.reserved_mb += need
            self.in_flight += 1
            self._busy[version_id] = busy
            self._high_water[version_id] = max(self._high_water.get(version_id, 1), busy)
            return Ticket(version_id, images, need), need, headroom

    async def reserve(self, version_id: str, images: int, buffers_mb: int, activation_mb: int,
                      timeout_s: float, max_waiting: int) -> tuple[Ticket | None, int, int]:
        """`try_reserve`, waiting up to `timeout_s` for in-flight requests to free memory.

        A replica whose arena holds one batch serves one request at a time; the
        next one is not too large, only early. Refusing it turned every second
        concurrent caller into an error, so it waits here instead, the way the
        batcher queues work. It gives up - and the caller gets queue_full - when
        nothing is in flight to wait for, when `max_waiting` requests are
        already parked, or when the time runs out.
        """
        ticket, need, headroom = self.try_reserve(version_id, images, buffers_mb, activation_mb)
        if ticket is not None or self.in_flight == 0 or self.waiting >= max_waiting:
            return ticket, need, headroom
        self._loop = asyncio.get_running_loop()
        deadline = self._loop.time() + timeout_s
        self.waiting += 1
        try:
            while True:
                # take the event before retrying: a release after this point sets it,
                # and nothing between the retry and the wait yields to the loop
                if self._freed is None:
                    self._freed = asyncio.Event()
                freed = self._freed
                ticket, need, headroom = self.try_reserve(
                    version_id, images, buffers_mb, activation_mb)
                left = deadline - self._loop.time()
                if ticket is not None or self.in_flight == 0 or left <= 0:
                    break
                try:
                    await asyncio.wait_for(freed.wait(), timeout=left)
                except asyncio.TimeoutError:
                    ticket, need, headroom = self.try_reserve(
                        version_id, images, buffers_mb, activation_mb)
                    break
        finally:
            self.waiting -= 1
        return ticket, need, headroom

    def _wake(self) -> None:
        # each release wakes every waiter once; the ones that still do not fit park again
        freed, self._freed = self._freed, None
        if freed is not None:
            freed.set()

    def release(self, ticket: Ticket) -> None:
        with self._lock:
            self.reserved_mb = max(0, self.reserved_mb - ticket.reserved_mb)
            self.in_flight = max(0, self.in_flight - 1)
            left = self._busy.get(ticket.version_id, 0) - ticket.images
            if left > 0:
                self._busy[ticket.version_id] = left
            else:
                self._busy.pop(ticket.version_id, None)
            idle = self.in_flight == 0
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._wake)
        if idle:
            self._trim()

    def _trim(self) -> None:
        # glibc keeps freed request buffers in its heap; hand them back so the
        # idle size the next burst is measured against does not creep upward
        now = time.monotonic()
        if now - self._last_trim < self.TRIM_EVERY_S or not sys.platform.startswith("linux"):
            return
        self._last_trim = now
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass

    def summary(self) -> dict:
        with self._lock:
            if self.in_flight == 0:
                self.idle_rss_mb = rss_mb()  # keep the reported headroom current
        return {"inflight_requests": self.in_flight, "inflight_reserved_mb": self.reserved_mb,
                "request_headroom_mb": self.headroom_mb(), "ceiling_mb": ceiling_mb()}
