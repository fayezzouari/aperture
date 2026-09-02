"""R1.9 + R7.8 - running user-supplied pre/post-processing safely enough.

The hook runs in a short-lived child process started with `python -I` (no site
packages of the caller's making, no inherited env), under POSIX resource limits,
in an empty working directory, with dangerous imports refused by an import hook.
Results cross the boundary as an npz archive loaded with allow_pickle=False, so a
malicious hook cannot execute code in the serving process by way of its return
value.

This is a hardening layer, not a security boundary on its own: the boundary is
the container the runtime ships in. Deployments that accept hooks from
untrusted authors should run runtimes with a restricted seccomp profile,
read-only rootfs, no network namespace and gVisor/Kata, which is how the
Kubernetes chart configures the hook-enabled runtime pool.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass

import numpy as np

BLOCKED_MODULES = {
    "socket", "ssl", "subprocess", "multiprocessing", "http", "urllib", "urllib3",
    "requests", "httpx", "ftplib", "smtplib", "telnetlib", "asyncio", "ctypes",
    "shutil", "pathlib", "importlib", "pickle", "marshal", "webbrowser", "pty",
}

ALLOWED_TOP_LEVEL = {"numpy", "math", "json", "itertools", "functools", "collections",
                     "statistics", "typing", "dataclasses", "enum", "re", "cmath", "random"}

_CHILD = r'''
import builtins, io, json, os, resource, sys

MEM = int(os.environ["AP_HOOK_MEM_MB"]) * 1024 * 1024
CPU = max(1, int(os.environ["AP_HOOK_TIMEOUT_MS"]) // 1000 + 1)
resource.setrlimit(resource.RLIMIT_AS, (MEM, MEM))
resource.setrlimit(resource.RLIMIT_CPU, (CPU, CPU))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
try:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
except (ValueError, OSError):
    pass

BLOCKED = set(json.loads(os.environ["AP_HOOK_BLOCKED"]))
ALLOWED = set(json.loads(os.environ["AP_HOOK_ALLOWED"]))
_real_import = builtins.__import__

def _guarded(name, globals=None, locals=None, fromlist=(), level=0):
    top = name.split(".")[0]
    if top in BLOCKED or top not in ALLOWED:
        raise ImportError(f"import of '{name}' is not allowed inside an Aperture hook")
    return _real_import(name, globals, locals, fromlist, level)

builtins.__import__ = _guarded
builtins.open = lambda *a, **k: (_ for _ in ()).throw(
    PermissionError("filesystem access is not allowed inside an Aperture hook"))

import numpy as np  # noqa: E402  (imported before the guard would matter)

payload = np.load(sys.argv[1], allow_pickle=False)
meta = json.loads(str(payload["__meta__"]))
arrays = {k: payload[k] for k in payload.files if not k.startswith("__")}

ns = {"__name__": "aperture_hook", "np": np, "numpy": np}
exec(compile(meta["source"], "<aperture-hook>", "exec"), ns)
fn = ns.get(meta["fn"])
if not callable(fn):
    raise RuntimeError(f"hook does not define a callable named '{meta['fn']}'")

if meta["kind"] == "preprocess":
    out = fn(arrays["image"], meta["config"])
    result = {"tensor": np.ascontiguousarray(np.asarray(out))}
    extra = {}
else:
    ordered = [arrays[f"out_{i}"] for i in range(meta["n_outputs"])]
    out = fn(ordered, meta["config"], meta["geometry"])
    if not isinstance(out, dict):
        raise RuntimeError("postprocess hook must return a dict")
    result, extra = {}, {}
    for k, v in out.items():
        if isinstance(v, np.ndarray):
            result[k] = v
        else:
            extra[k] = v

buf = io.BytesIO()
np.savez(buf, __extra__=np.array(json.dumps(extra, default=str)), **result)
sys.stdout.buffer.write(buf.getvalue())
sys.stdout.buffer.flush()
'''


class HookError(RuntimeError):
    pass


@dataclass(slots=True)
class HookResult:
    arrays: dict[str, np.ndarray]
    extra: dict


def validate_source(source: str) -> list[str]:
    """Cheap static screen so an obviously unsafe hook is refused before deploy."""
    import ast

    problems: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error on line {exc.lineno}: {exc.msg}"]
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""])
            for n in names:
                top = n.split(".")[0]
                if top in BLOCKED_MODULES or top not in ALLOWED_TOP_LEVEL:
                    problems.append(f"import of '{n}' is not allowed (line {node.lineno})")
        if isinstance(node, ast.Name) and node.id in {"eval", "exec", "compile", "__import__",
                                                      "globals", "locals", "vars", "open"}:
            problems.append(f"use of '{node.id}' is not allowed (line {node.lineno})")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            problems.append(f"dunder attribute access '{node.attr}' is not allowed "
                            f"(line {node.lineno})")
    return problems


def run_hook(*, kind: str, source: str, fn: str, config: dict, timeout_ms: int = 2000,
             memory_mb: int = 512, image: np.ndarray | None = None,
             outputs: list[np.ndarray] | None = None,
             geometry: dict | None = None) -> HookResult:
    problems = validate_source(source)
    if problems:
        raise HookError("; ".join(problems[:5]))

    meta = {
        "kind": kind, "source": source, "fn": fn, "config": config,
        "geometry": geometry or {}, "n_outputs": len(outputs or []),
    }
    arrays: dict[str, np.ndarray] = {}
    if image is not None:
        arrays["image"] = np.ascontiguousarray(image)
    for i, out in enumerate(outputs or []):
        arrays[f"out_{i}"] = np.ascontiguousarray(out)

    with tempfile.TemporaryDirectory(prefix="aperture-hook-") as tmp:
        payload_path = os.path.join(tmp, "payload.npz")
        np.savez(payload_path, __meta__=np.array(json.dumps(meta)), **arrays)
        child_path = os.path.join(tmp, "child.py")
        with open(child_path, "w") as fh:
            fh.write(_CHILD)
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": tmp,
            "AP_HOOK_MEM_MB": str(memory_mb),
            "AP_HOOK_TIMEOUT_MS": str(timeout_ms),
            "AP_HOOK_BLOCKED": json.dumps(sorted(BLOCKED_MODULES)),
            "AP_HOOK_ALLOWED": json.dumps(sorted(ALLOWED_TOP_LEVEL)),
            "PYTHONHASHSEED": "0",
        }
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-S", "-B", child_path, payload_path],
                capture_output=True, timeout=timeout_ms / 1000.0, env=env, cwd=tmp, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HookError(f"hook exceeded its {timeout_ms} ms budget") from exc

        if proc.returncode != 0:
            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-4:]
            raise HookError("hook failed: " + " | ".join(tail or ["no stderr"]))

        try:
            loaded = np.load(io.BytesIO(proc.stdout), allow_pickle=False)
        except Exception as exc:
            raise HookError(f"hook returned unreadable data: {exc}") from exc

        extra = json.loads(str(loaded["__extra__"])) if "__extra__" in loaded.files else {}
        return HookResult(
            arrays={k: loaded[k] for k in loaded.files if not k.startswith("__")}, extra=extra
        )
