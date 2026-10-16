"""Host memory accounting, so a runtime refuses a version instead of being OOM-killed.

Loading every assigned version regardless of what fits is how a runtime ends up
killed by the kernel mid-request, restarted, handed the same assignments, and
killed again. Before a load the store asks this module whether the version's
expected footprint fits inside the budget, and after the load it records what
the version actually cost, so the next estimate - here, on another replica, or
after a restart - is a measurement rather than a guess.

The budget is `AP_RUNTIME_MEMORY_BUDGET_MB` when set, otherwise a fraction of
the container's cgroup limit, otherwise of the host's RAM.
"""

from __future__ import annotations

import json
import logging
import os
import resource
import sys
import threading

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
