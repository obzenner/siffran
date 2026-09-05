"""Inactive ``PreToolUse:Bash`` actor-dispatch attribution translation.

Classification is intentionally conservative: only an actor CLI in command position (or behind a
small transparent-wrapper set) plus its execution flag counts.  Ordinary Bash is inert.  The caller
supplies the dispatcher-side actor it selected; the application operation forces CLI attribution to
``witnessed`` and persists it under CAS.
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from pathlib import Path

from .correlation import request_id as new_request_id
from .route import observed_at
from .selector import context_from_payload
from .transport import BridgeTransport, Transport

PROTOCOL = "empirica/v1"
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
    payload: Mapping[str, object], run_id: str, actor: Mapping[str, object], *,
    claim_id: str | None = None, correlation_id: str | None = None,
) -> dict | None:
    """Build ``ObserveAction(dispatch)`` for a Bash actor invocation, else ``None``.

    ``actor`` is dispatcher-side input, not actor self-report.  Harness and source type are filled
    when omitted; model/provider fields are passed through for application validation.  CLI exec is
    marked witnessed because this adapter observed the exact process the dispatcher selected.
    """
    context_from_payload(payload)
    command_text = bash_command(payload)
    harness = dispatched_harness(command_text)
    if harness is None:
        return None
    if not isinstance(actor, Mapping):
        raise ValueError("actor must be a mapping supplied by the dispatcher")
    typed_actor = dict(actor)
    typed_actor.setdefault("source_type", "LLM_JUDGE")
    typed_actor.setdefault("harness", harness)
    action: dict = {"kind": "dispatch", "actor": typed_actor, "witnessed": True}
    if claim_id is not None:
        if not isinstance(claim_id, str):
            raise ValueError("claim_id must be a string or null")
        if claim_id:
            action["claim_id"] = claim_id
    envelope_command: dict = {
        "type": "ObserveAction", "run_id": _handle(run_id), "action": action,
    }
    stamp = observed_at(payload)
    if stamp is not None:
        envelope_command["observed_at"] = stamp
    return {
        "protocol": PROTOCOL,
        "request_id": correlation_id or new_request_id(payload, "dispatch"),
        "command": envelope_command,
    }


def dispatch_actor(
    payload: Mapping[str, object], run_id: str, actor: Mapping[str, object], *,
    claim_id: str | None = None, transport: Transport | None = None,
    correlation_id: str | None = None,
) -> tuple[dict | None, str | None]:
    """Dispatch typed attribution and return ``(response, advice)`` for the host to surface."""
    context = context_from_payload(payload)
    request = build_dispatch_request(
        payload, run_id, actor, claim_id=claim_id, correlation_id=correlation_id,
    )
    command = bash_command(payload)
    advice = dispatch_advice(command, run_id)
    if request is None:
        return None, None
    target = transport if transport is not None else BridgeTransport(context.cwd)
    return target.dispatch(request), advice
