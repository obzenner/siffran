"""Inactive Claude Code adapter building blocks for the ``empirica/v1`` bridge.

These modules translate Claude-shaped lifecycle payloads without registering hooks.  Activation is
intentionally separate: existing hooks and ``hooks.json`` remain the active implementation until a
later cutover.
"""

from .completion import StopResult, build_stop_request, dispatch_stop, stop_result
from .correlation import CorrelationError, correlate, request_id
from .dispatch import (
    build_dispatch_request,
    dispatch_actor,
    dispatch_advice,
    dispatched_harness,
)
from .fail_direction import FailureDirection, blocks_on_failure, failure_direction
from .invocation import Invocation, build_mode_request, parse_invocation
from .knowledge import (
    SpikeExecution,
    build_attribution_request,
    build_audit_ticket_request,
    build_audit_verdict_request,
    build_graph_request,
    build_regate_requests,
    build_research_request,
    build_spike_request,
    run_spike,
)
from .preflight import diagnose
from .route import (
    INVESTIGATIVE_TOOLS,
    build_investigation_request,
    build_route_announcement_request,
    dispatch_investigation,
    dispatch_route_announcement,
    observed_at,
)
from .restore import build_restore_request, dispatch_restore, restore_context
from .run_start import build_start_run_request, dispatch_start_run, invocation_details
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
    "Invocation",
    "PayloadContext",
    "SelectorError",
    "SpawnDecision",
    "StopResult",
    "SpikeExecution",
    "Transport",
    "INVESTIGATIVE_TOOLS",
    "blocks_on_failure",
    "build_dispatch_request",
    "build_attribution_request",
    "build_audit_ticket_request",
    "build_audit_verdict_request",
    "build_graph_request",
    "build_investigation_request",
    "build_mode_request",
    "build_reserve_spawn_request",
    "build_regate_requests",
    "build_research_request",
    "build_restore_request",
    "build_route_announcement_request",
    "build_start_run_request",
    "build_spike_request",
    "build_stop_request",
    "context_from_payload",
    "correlate",
    "dispatch",
    "dispatch_actor",
    "dispatch_advice",
    "diagnose",
    "dispatch_investigation",
    "dispatch_reserve_spawn",
    "dispatch_restore",
    "dispatch_route_announcement",
    "dispatch_start_run",
    "dispatch_stop",
    "dispatched_harness",
    "failure_direction",
    "invocation_details",
    "observed_at",
    "parse_invocation",
    "request_id",
    "run_spike",
    "restore_context",
    "selector_from_payload",
    "spawn_decision",
    "stop_result",
]
