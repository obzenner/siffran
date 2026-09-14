"""Inactive ``PreToolUse:Bash`` actor-dispatch translation to exact v2 ``dispatch``.

Classification is intentionally conservative: only an actor CLI in command position (or behind a
small transparent-wrapper set) plus its execution flag counts.  Ordinary Bash is inert.  The
adapter emits only ``{kind: "dispatch", target, claim_id?}``; actor/witnessed telemetry is not
public admission (D6-C spec §3/C2).
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from pathlib import Path

from .correlation import PROTOCOL, request_id as new_request_id
from .route import observed_at
from .selector import context_from_payload
from .transport import BridgeTransport, Transport

DISPATCH_SIGNATURES = {
    "claude": ("-p", "--print"),
    "codex": ("exec",),
    "pi": ("-p", "--print", "--mode"),
}
_SEPARATORS = re.compile(r"\|\||&&|;|\||\n")
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_TRANSPARENT_WRAPPERS = frozenset({
    "env", "command", "exec", "nohup", "nice", "time", "timeout", "stdbuf", "sudo", "doas",
    "xargs", "setsid", "script",
})
_SESSION_FLAGS = ("--session-id", "resume", "--resume", "--fork-session")


def _handle(run_id: object) -> str:
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty application run handle")
    return run_id


def bash_command(payload: Mapping[str, object]) -> str | None:
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, Mapping) else None
    return command if isinstance(command, str) else None


def dispatched_harness(command: object) -> str | None:
    """Return the actor CLI used to run a model, only when it appears in command position."""
    if not isinstance(command, str) or not command.strip():
        return None
    for segment in _SEPARATORS.split(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        index = 0
        while index < len(tokens) and _ENV_ASSIGNMENT.match(tokens[index]):
            index += 1
        if index >= len(tokens):
            continue
        wrapped = Path(tokens[index]).name in _TRANSPARENT_WRAPPERS
        candidates = range(index, len(tokens)) if wrapped else (index,)
        for candidate in candidates:
            harness = Path(tokens[candidate]).name
            if harness in DISPATCH_SIGNATURES and any(
                flag in tokens[candidate + 1:] for flag in DISPATCH_SIGNATURES[harness]
            ):
                return harness
    return None


def dispatch_advice(command: object, run_id: str) -> str | None:
    """Best-effort session-pinning advice for a real cold dispatch; never a denial."""
    harness = dispatched_harness(command)
    if harness is None or any(flag in (command or "") for flag in _SESSION_FLAGS):
        return None
    return (
        f"empirica: this `{harness}` dispatch pins no session and starts cold; derive and pass a "
        f"per-(run, claim) session for run `{run_id}` to preserve claim context."
    )


def build_dispatch_request(
    payload: Mapping[str, object], run_id: str, *, claim_id: str | None = None,
    correlation_id: str | None = None,
) -> dict | None:
    """Build ``ObserveAction(dispatch)`` for a Bash actor invocation, else ``None``.

    Only ``target`` (the dispatched actor CLI) and an optional ``claim_id`` are emitted; no actor
    or witnessed telemetry is fabricated as public admission.
    """
    context_from_payload(payload)
    command_text = bash_command(payload)
    harness = dispatched_harness(command_text)
    if harness is None:
        return None
    action: dict = {"kind": "dispatch", "target": harness}
    if claim_id is not None:
        if not isinstance(claim_id, str):
            raise ValueError("claim_id must be a string or null")
        if claim_id:
            action["claim_id"] = claim_id
    command: dict = {"type": "ObserveAction", "run_id": _handle(run_id), "action": action}
    stamp = observed_at(payload)
    if stamp is not None:
        command["observed_at"] = stamp
    return {
        "protocol": PROTOCOL,
        "request_id": correlation_id or new_request_id(payload, "dispatch"),
        "command": command,
    }


def dispatch_dispatch(
    payload: Mapping[str, object], run_id: str, *, claim_id: str | None = None,
    transport: Transport | None = None, correlation_id: str | None = None,
) -> tuple[dict | None, str | None]:
    """Dispatch typed attribution and return ``(response, advice)`` for the host to surface."""
    context_from_payload(payload)
    request = build_dispatch_request(
        payload, run_id, claim_id=claim_id, correlation_id=correlation_id,
    )
    command = bash_command(payload)
    advice = dispatch_advice(command, run_id)
    if request is None:
        return None, None
    return (transport if transport is not None else BridgeTransport()).dispatch(request), advice
