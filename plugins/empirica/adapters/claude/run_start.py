"""Pure Claude run-start/resume translation plus an injectable bridge dispatch.

``StartRun`` requires an explicit goal, carries trusted invocation provenance, nests explicit max
values under ``budgets``, and omits absent values. ``ResolveRun`` resolves a run from its selector.
Both produce exact v2 envelopes (D6-C spec §3/C2).
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from adapters.invocation import provenance

from .correlation import PROTOCOL, request_id as new_request_id
from .invocation import parse_invocation
from .selector import context_from_payload, selector_from_payload
from .transport import CLAUDE_PROFILE_ID, Transport, dispatch_with


_ENTRYPOINT_INTERACTIVE = {"cli": True, "sdk-cli": False}


def _parse_transcript_entrypoint(path: object) -> str | None:
    """Parse the first concrete entrypoint from untrusted transcript JSONL."""
    if not isinstance(path, str):
        return None
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if isinstance(row, dict) and isinstance(row.get("entrypoint"), str):
                    return row["entrypoint"]
    except (OSError, ValueError):
        return None
    return None


def invocation_provenance(payload: Mapping[str, object], environ: Mapping[str, str]) -> dict[str, object]:
    entrypoint = environ.get("CLAUDE_CODE_ENTRYPOINT")
    signal = "CLAUDE_CODE_ENTRYPOINT" if entrypoint is not None else "transcript.entrypoint unavailable"
    if entrypoint is None and (observed := _parse_transcript_entrypoint(payload.get("transcript_path"))) is not None:
        entrypoint, signal = observed, "transcript.entrypoint"
    return provenance("claude", _ENTRYPOINT_INTERACTIVE.get(entrypoint), signal, environ,
                      profile_id=CLAUDE_PROFILE_ID)


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
    """Build exact v2 StartRun with no actor and only supplied budgets."""
    context_from_payload(payload)  # validate cwd/session together before deriving the selector
    env = os.environ if environ is None else environ
    invocation = parse_invocation(payload, environ=env)
    command: dict[str, Any] = {
        "type": "StartRun",
        "selector": selector_from_payload(payload),
        "goal": invocation.goal,
        "invocation": invocation_provenance(payload, env),
    }
    if invocation.control_mode == "auto":
        command["control_mode"] = "auto"
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
    return dispatch_with(transport, request)


def dispatch_resolve(
    payload: Mapping[str, object], *, transport: Transport | None = None,
    correlation_id: str | None = None,
) -> dict:
    request = build_resolve_request(payload, correlation_id=correlation_id)
    return dispatch_with(transport, request)
