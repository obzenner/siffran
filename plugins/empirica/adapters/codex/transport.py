"""In-process Codex transport to the shared Empirica composition bridge."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from adapters import bridge

from .correlation import correlate


class Transport(Protocol):
    def dispatch(self, request: dict) -> dict: ...


class BridgeTransport:
    def __init__(self, cwd: str | Path) -> None:
        self.cwd = Path(cwd)

    def dispatch(self, request: dict) -> dict:
        return correlate(request, bridge.handle(request, cwd=self.cwd))


def dispatch(request: dict, *, cwd: str | Path) -> dict:
    return BridgeTransport(cwd).dispatch(request)
