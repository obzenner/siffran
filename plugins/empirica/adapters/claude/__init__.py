"""Inactive Claude Code adapter building blocks for the ``empirica/v1`` bridge.

These modules translate Claude-shaped lifecycle payloads without registering hooks.  Activation is
intentionally separate: existing hooks and ``hooks.json`` remain the active implementation until a
later cutover.
"""

from .correlation import CorrelationError, correlate, request_id
from .fail_direction import FailureDirection, blocks_on_failure, failure_direction
from .run_start import build_start_run_request, dispatch_start_run
from .selector import PayloadContext, SelectorError, context_from_payload, selector_from_payload
from .transport import BridgeTransport, Transport, dispatch

__all__ = [
    "BridgeTransport",
    "CorrelationError",
    "FailureDirection",
    "PayloadContext",
    "SelectorError",
    "Transport",
    "blocks_on_failure",
    "build_start_run_request",
    "context_from_payload",
    "correlate",
    "dispatch",
    "dispatch_start_run",
    "failure_direction",
    "request_id",
    "selector_from_payload",
]
