"""Pure Claude run-start/resume translation plus an injectable bridge dispatch.

``StartRun`` removes the actor wire field, nests explicit max values under ``budgets`` and omits
absent values; ``ResolveRun`` resolves a run from its selector.  Both produce exact v2 envelopes
(D6-C spec §3/C2).  This module is deliberately inactive: no hook imports it directly.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from .correlation import PROTOCOL, request_id as new_request_id
from .invocation import Invocation, parse_invocation
from .selector import context_from_payload, selector_from_payload
from .transport import BridgeTransport, Transport

FALLBACK_GOAL = "empirica run (goal unspecified)"


def invocation_details(
    payload: Mapping[str, object], *, environ: Mapping[str, str] | None = None,
) -> Invocation:
    """Return the complete, reviewable mode resolution used by StartRun and the doctor."""
    return parse_invocation(
        payload,
        environ=os.environ if environ is None else environ,
        fallback_goal=FALLBACK_GOAL,
    )


def _budget(environ: Mapping[str, str], name: str, minimum: int) -> int | None:
    try:
        value = int(environ[name])
    except (KeyError, ValueError):
        return None
    return value if value >= minimum else None


def build_start_run_request(
    payload: Mapping[str, object],
    *,
    correlation_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Translate one validated Claude payload into an exact v2 ``StartRun`` envelope.

    No ``actor`` field is emitted.  Explicit max values are nested under ``budgets`` and omitted
    when absent; resolved modes are emitted only when non-empty.
    """
    context_from_payload(payload)  # validate cwd/session together before deriving the selector
    invocation = invocation_details(payload, environ=environ)
    command: dict[str, Any] = {
        "type": "StartRun",
        "selector": selector_from_payload(payload),
        "goal": invocation.goal,
    }
    if invocation.modes:
        command["modes"] = invocation.modes
    env = os.environ if environ is None else environ
    budgets: dict[str, int] = {}
    for field, env_name, minimum in (
        ("max_passes", "EMPIRICA_MAX_PASSES", 1),
        ("max_spawns", "EMPIRICA_MAX_SPAWNS", 0),
        ("max_audit_spawns", "EMPIRICA_MAX_AUDIT_SPAWNS", 0),
    ):
        if (value := _budget(env, env_name, minimum)) is not None:
            budgets[field] = value
    if budgets:
        command["budgets"] = budgets
    return {
        "protocol": PROTOCOL,
        "request_id": correlation_id or new_request_id(payload, "run-start"),
        "command": command,
    }


def build_resolve_request(
    payload: Mapping[str, object], *, correlation_id: str | None = None,
) -> dict:
    """Translate one validated Claude payload into an exact v2 ``ResolveRun`` envelope."""
    return {
        "protocol": PROTOCOL,
        "request_id": correlation_id or new_request_id(payload, "resolve"),
        "command": {"type": "ResolveRun", "selector": selector_from_payload(payload)},
    }


def dispatch_start_run(
    payload: Mapping[str, object],
    *,
    transport: Transport | None = None,
    correlation_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict:
    """Translate and dispatch through the shared bridge (or an injected parity-test transport)."""
    request = build_start_run_request(
        payload, correlation_id=correlation_id, environ=environ,
    )
    return (transport if transport is not None else BridgeTransport()).dispatch(request)


def dispatch_resolve(
    payload: Mapping[str, object], *, transport: Transport | None = None,
    correlation_id: str | None = None,
) -> dict:
    request = build_resolve_request(payload, correlation_id=correlation_id)
    return (transport if transport is not None else BridgeTransport()).dispatch(request)
