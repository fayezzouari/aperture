"""Liveness for the background processes.

Neither the job worker nor the log sink serves HTTP, and `pgrep` is not in a
slim image, so each one touches a file and the container healthcheck asserts it
is recent. The worker writes it from a cron tick rather than a plain timer,
which makes the check say something worth knowing: the *scheduler* is alive, not
merely the process.
"""

from __future__ import annotations

import os
import time

WORKER_FILE = "/tmp/aperture-worker.alive"
LOGSINK_FILE = "/tmp/aperture-logsink.alive"
CONVERTER_FILE = "/tmp/aperture-converter.alive"
MAX_AGE_S = 120


def touch(path: str) -> None:
    with open(path, "w") as fh:
        fh.write(str(time.time()))


def is_fresh(path: str, max_age: float = MAX_AGE_S) -> bool:
    try:
        return (time.time() - os.path.getmtime(path)) < max_age
    except OSError:
        return False


def main() -> int:
    """`python -m app.jobs.heartbeat <path>` - the container healthcheck."""
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else WORKER_FILE
    return 0 if is_fresh(path) else 1


if __name__ == "__main__":
    raise SystemExit(main())
