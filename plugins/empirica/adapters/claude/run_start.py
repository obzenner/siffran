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
from .selector import context_from_payload, selector_from_payload
from .transport import BridgeTransport, Transport

PROTOCOL = "empirica/v1"
FALLBACK_GOAL = "empirica run (goal unspecified)"
_FLAGS = {"--multi-provider": "multi_provider", "--cli-exec": "cli_exec"}


def invocation_args(payload: Mapping[str, object]) -> str:
    """Read Claude's current ``command_args`` shape or its documented prompt fallback."""
    args = payload.get("command_args")
    if isinstance(args, str) and args.strip():
        return args
    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        parts = prompt.split(None, 1)
        if len(parts) == 2:
            return parts[1]
    return ""


def goal_and_modes(payload: Mapping[str, object]) -> tuple[str, dict[str, bool]]:
    """Parse only the leading mode-flag run and return the remaining invocation as the goal."""
    tokens = invocation_args(payload).split()
    modes: dict[str, bool] = {}
    index = 0
    while index < len(tokens) and tokens[index].startswith("--"):
        token = tokens[index]
        if token in _FLAGS:
            modes[_FLAGS[token]] = True
        elif token.startswith("--no-") and f"--{token[5:]}" in _FLAGS:
            modes[_FLAGS[f"--{token[5:]}"]] = False
        # Unknown leading flags are stripped exactly like the current parser. Their future
        # reporting belongs to activation, not this transport-neutral StartRun slice.
        index += 1
    return " ".join(tokens[index:]).strip() or FALLBACK_GOAL, modes


def _max_passes(environ: Mapping[str, str]) -> int | None:
    raw = environ.get("EMPIRICA_MAX_PASSES")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 1 else None


def build_start_run_request(
    payload: Mapping[str, object],
    *,
    correlation_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Translate one validated Claude payload into an ``empirica/v1`` StartRun envelope."""
    # Validate cwd/session together before deriving either selector component.
    context_from_payload(payload)
    goal, modes = goal_and_modes(payload)
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
