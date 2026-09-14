"""Reusable in-process transport from Claude adapters to the shared v2 composition bridge.

The Claude hooks run in-process and reach the one shared bridge (:mod:`adapters.bridge`) with a
fixed exact registry profile (``claude-code@2.1.270``); there is no host default, no ``cwd`` and no
fallback to another host (D6-C spec §3/C2, §4).  Correlation is exact v2 (:func:`correlate`).
"""
from __future__ import annotations

from typing import Protocol

from adapters import bridge

from .correlation import correlate

#: Fixed exact registry profile supplied at bridge construction, never in a public request.
CLAUDE_PROFILE_ID = "claude-code@2.1.270"


class Transport(Protocol):
    """Narrow injectable transport seam used by translators and their parity tests."""

    def dispatch(self, request: dict) -> dict: ...


class BridgeTransport:
    """Dispatch requests through :mod:`adapters.bridge`, never through a hook-local service."""

    __slots__ = ()

    def __init__(self) -> None:
        pass

    def dispatch(self, request: dict) -> dict:
        return correlate(request, bridge.handle(request, profile_id=CLAUDE_PROFILE_ID))


def dispatch(request: dict) -> dict:
    """One-shot convenience form of :class:`BridgeTransport`."""
    return BridgeTransport().dispatch(request)
