"""Who is calling, when the server is shared.

Over stdio the server belongs to one person and their token sits in the
environment. Over HTTP one process serves everyone, so each request brings its
own credential in the Authorization header, and the workspace in
X-Aperture-Workspace. The HTTP layer binds that pair here for the duration of a
request; the client and the error hints read it back. Nothing is cached per
caller and nothing outlives the request.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Caller:
    token: str
    workspace: str | None = None


_current: ContextVar[Caller | None] = ContextVar("aperture_caller", default=None)


def current() -> Caller | None:
    return _current.get()


def bind(caller: Caller):
    """Returns the reset token; pass it to `unbind` when the request is done."""
    return _current.set(caller)


def unbind(token) -> None:
    _current.reset(token)
