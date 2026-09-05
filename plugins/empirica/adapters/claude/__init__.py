"""Inactive Claude Code adapter building blocks for the ``empirica/v1`` bridge.

These modules translate Claude-shaped lifecycle payloads without registering hooks.  Activation is
intentionally separate: existing hooks and ``hooks.json`` remain the active implementation until a
later cutover.
"""

from .correlation import CorrelationError, correlate, request_id
from .dispatch import (
    build_dispatch_request,
    dispatch_actor,
    dispatch_advice,
    dispatched_harness,
)
from .fail_direction import FailureDirection, blocks_on_failure, failure_direction
from .route import (
    INVESTIGATIVE_TOOLS,
    build_investigation_request,
    build_route_announcement_request,
    dispatch_investigation,
    dispatch_route_announcement,
    observed_at,
)
from .run_start import build_start_run_request, dispatch_start_run
from .selector import PayloadContext, SelectorError, context_from_payload, selector_from_payload
from .spawn import (
    SpawnDecision,
    build_reserve_spawn_request,
    dispatch_reserve_spawn,
    spawn_decision,
)
from .transport import BridgeTransport, Transport, dispatch

__all__ = [
    "BridgeTransport",
    "CorrelationError",
    "FailureDirection",
    "PayloadContext",
    "SelectorError",
    "SpawnDecision",
    "Transport",
    "INVESTIGATIVE_TOOLS",
    "blocks_on_failure",
    "build_dispatch_request",
    "build_investigation_request",
    "build_reserve_spawn_request",
    "build_route_announcement_request",
    "build_start_run_request",
    "context_from_payload",
    "correlate",
    "dispatch",
    "dispatch_actor",
    "dispatch_advice",
    "dispatch_investigation",
    "dispatch_reserve_spawn",
    "dispatch_route_announcement",
    "dispatch_start_run",
    "dispatched_harness",
    "failure_direction",
    "observed_at",
    "request_id",
    "selector_from_payload",
    "spawn_decision",
]
