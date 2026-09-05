"""Reusable in-process transport from Claude adapters to the shared composition bridge."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from adapters import bridge

from .correlation import correlate


class Transport(Protocol):
    """Narrow injectable transport seam used by translators and their parity tests."""

    def dispatch(self, request: dict) -> dict: ...


class BridgeTransport:
    """Dispatch requests through :mod:`adapters.bridge`, never through a hook-local service."""

    def __init__(self, cwd: str | Path) -> None:
        self.cwd = Path(cwd)

    def dispatch(self, request: dict) -> dict:
        return correlate(request, bridge.handle(request, cwd=self.cwd))


def dispatch(request: dict, *, cwd: str | Path) -> dict:
    """One-shot convenience form of :class:`BridgeTransport`."""
    return BridgeTransport(cwd).dispatch(request)
