"""Pure Claude run-start translation plus an injectable bridge dispatch.

This module is deliberately inactive: no existing hook imports it and no ``hooks.json`` entry names
it.  It preserves the payload variants accepted by the current run-start hook while translating the
operation into the host-neutral ``empirica/v1`` ``StartRun`` command.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from .correlation import request_id as new_request_id
from .invocation import Invocation, parse_invocation
from .selector import context_from_payload, selector_from_payload
from .transport import BridgeTransport, Transport

PROTOCOL = "empirica/v1"
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


def goal_and_modes(
    payload: Mapping[str, object], *, environ: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, bool]]:
    """Compatibility projection of :func:`invocation_details`."""
    invocation = invocation_details(payload, environ=environ)
    return invocation.goal, invocation.modes


def _max_passes(environ: Mapping[str, str]) -> int | None:
    raw = environ.get("EMPIRICA_MAX_PASSES")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 1 else None


def _max_spawns(environ: Mapping[str, str]) -> int | None:
    raw = environ.get("EMPIRICA_MAX_SPAWNS")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def build_start_run_request(
    payload: Mapping[str, object],
    *,
    correlation_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Translate one validated Claude payload into an ``empirica/v1`` StartRun envelope."""
    # Validate cwd/session together before deriving either selector component.
    context_from_payload(payload)
    goal, modes = goal_and_modes(payload, environ=environ)
    command: dict[str, Any] = {
        "type": "StartRun",
        "selector": selector_from_payload(payload),
        "goal": goal,
    }
    if modes:
        command["modes"] = modes
    max_passes = _max_passes(os.environ if environ is None else environ)
    if max_passes is not None:
        command["max_passes"] = max_passes
    max_spawns = _max_spawns(os.environ if environ is None else environ)
    if max_spawns is not None:
        command["max_spawns"] = max_spawns
    return {
        "protocol": PROTOCOL,
        "request_id": correlation_id or new_request_id(payload, "run-start"),
        "command": command,
    }


def dispatch_start_run(
    payload: Mapping[str, object],
    *,
    transport: Transport | None = None,
    correlation_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict:
    """Translate and dispatch through the shared bridge (or an injected parity-test transport)."""
    context = context_from_payload(payload)
    request = build_start_run_request(
        payload, correlation_id=correlation_id, environ=environ,
    )
    target = transport if transport is not None else BridgeTransport(context.cwd)
    return target.dispatch(request)
