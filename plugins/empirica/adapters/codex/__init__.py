"""Codex CLI 0.146.0 adapter for the shared ``empirica/v2`` bridge (D6-C C2b).

These modules translate Codex-shaped lifecycle payloads into exact v2 requests without
registering hooks.  The adapter retains only the ``StartRun`` builder (activation) and the
``ResolveRun`` builder; the four hook entrypoints ``ResolveRun`` through the strict bridge shell
and return inert when unresolved (D6 no-location run port).  Removed operations
(``void_spawn``/``audit_ticket``/``consume`` ticket/``phase``) and author-submitted trusted
actions (``evidence_leaf``/``attribution``/``child_event``/``audit_verdict``) have no public
builder here: the adapter fails closed locally rather than fabricating capability.  The thin
hooks under ``hooks/`` remain the active implementation.
"""

from .correlation import PROTOCOL, CorrelationError, correlate, request_id
from .lifecycle import (
    SelectorError,
    build_resolve_request,
    build_start_run_request,
    explicit_activation,
)
from .transport import CODEX_PROFILE_ID, BridgeTransport, Transport, dispatch

__all__ = [
    "BridgeTransport",
    "CODEX_PROFILE_ID",
    "CorrelationError",
    "PROTOCOL",
    "SelectorError",
    "Transport",
    "build_resolve_request",
    "build_start_run_request",
    "correlate",
    "dispatch",
    "explicit_activation",
    "request_id",
]
