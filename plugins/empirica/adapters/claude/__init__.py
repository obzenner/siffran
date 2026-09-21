"""Claude Code 2.1.270 driver and request translators for ``empirica/v2``.

Thin hooks in ``hooks/`` activate these modules. Public builders exclude trusted ingress; the
Claude lifecycle binds the shared durable audit operation to exact SubagentStart/Stop native IDs.
"""

from .completion import REPORT_CONVERGENCE, StopResult, build_stop_request, dispatch_stop, stop_result
from .correlation import PROTOCOL, CorrelationError, correlate, request_id
from .dispatch import (
    bash_command,
    build_dispatch_request,
    dispatch_advice,
    dispatch_dispatch,
    dispatched_harness,
)
from .fail_direction import FailureDirection, blocks_on_failure, failure_direction
from .invocation import (
    Invocation,
    MODES,
    build_configure_run_request,
    parse_invocation,
)
from .preflight import diagnose
from .restore import (
    build_get_argument_request,
    build_restore_request,
    dispatch_get_argument,
    dispatch_restore,
    restore_context,
)
from .route import (
    INVESTIGATIVE_TOOLS,
    build_investigation_request,
    build_route_announcement_request,
    dispatch_investigation,
    dispatch_route_announcement,
    observed_at,
)
from .run_start import (
    FALLBACK_GOAL,
    build_resolve_request,
    build_start_run_request,
    dispatch_resolve,
    dispatch_start_run,
    invocation_details,
)
from .selector import PayloadContext, SelectorError, context_from_payload, selector_from_payload
from .spawn import (
    SpawnDecision,
    build_child_reserve_request,
    dispatch_child_reserve,
    spawn_decision,
)
from .transport import CLAUDE_PROFILE_ID, BridgeTransport, Transport, dispatch

__all__ = [
    "BridgeTransport",
    "CLAUDE_PROFILE_ID",
    "CorrelationError",
    "FALLBACK_GOAL",
    "FailureDirection",
    "INVESTIGATIVE_TOOLS",
    "Invocation",
    "MODES",
    "PayloadContext",
    "PROTOCOL",
    "REPORT_CONVERGENCE",
    "SelectorError",
    "SpawnDecision",
    "StopResult",
    "Transport",
    "bash_command",
    "blocks_on_failure",
    "build_child_reserve_request",
    "build_configure_run_request",
    "build_dispatch_request",
    "build_get_argument_request",
    "build_investigation_request",
    "build_resolve_request",
    "build_restore_request",
    "build_route_announcement_request",
    "build_start_run_request",
    "build_stop_request",
    "context_from_payload",
    "correlate",
    "diagnose",
    "dispatch",
    "dispatch_advice",
    "dispatch_child_reserve",
    "dispatch_dispatch",
    "dispatch_get_argument",
    "dispatch_investigation",
    "dispatch_resolve",
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
    "restore_context",
    "selector_from_payload",
    "spawn_decision",
    "stop_result",
]
