"""What every tool closes over: the settings and one API client."""

from __future__ import annotations

from dataclasses import dataclass

from .client import ApertureClient
from .settings import Settings


@dataclass
class AppState:
    settings: Settings
    client: ApertureClient
