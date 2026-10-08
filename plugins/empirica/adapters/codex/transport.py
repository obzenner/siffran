"""In-process Codex transport to the shared v2 composition bridge (D6-C C2b).

The Codex hooks run in-process and reach the one shared bridge (:mod:`adapters.bridge`) with a
fixed registry profile of the host; there is no host default, no ``cwd`` and no
fallback to another host (D6-C spec §3/C2, §4).  Correlation is exact v2 (:func:`correlate`).
"""
from __future__ import annotations

from typing import Protocol

from adapters import bridge
from application.protocol import host_profile_id

from .correlation import correlate

#: Fixed exact registry profile supplied at bridge construction, never in a public request.
CODEX_PROFILE_ID = host_profile_id("codex-cli")


class Transport(Protocol):
    """Narrow injectable transport seam used by translators and their parity tests."""

    def dispatch(self, request: dict) -> dict: ...


class BridgeTransport:
    """Dispatch requests through :mod:`adapters.bridge`, never through a hook-local service."""

    __slots__ = ()

    def __init__(self) -> None:
        pass

    def dispatch(self, request: dict) -> dict:
        return correlate(request, bridge.handle(request, profile_id=CODEX_PROFILE_ID))


def dispatch(request: dict) -> dict:
    """One-shot convenience form of :class:`BridgeTransport`."""
    return BridgeTransport().dispatch(request)
