"""Codex CLI 0.146.0 driver and request translators for ``empirica/v2``.

Native hooks activate the shared public bridge and bounded managed audit process. Trusted ingress
has no public builder. Because this exact Codex profile cannot observe the process's resolved model,
its auditor identity remains unverified and convergence fails closed.
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
