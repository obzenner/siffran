"""Codex CLI 0.146.0 hook translation for the shared ``empirica/v2`` bridge (D6-C C2b).

This module owns native payload parsing and native JSON hook output only.  Run allocation,
ordering, budgets, evidence, audit coverage, and convergence remain in the application/core.

Codex is a complete exact-profile host (``codex-cli@0.146.0``). Public author/read
operations are exposed through the shared MCP server. Hooks allocate and resolve durable runs,
inject the opaque handle, enforce Stop, and own a bounded ``codex exec`` foreground auditor
because native 0.146.0 hooks cannot observe arbitrary child output. Trusted evidence,
attribution, child events, and audit verdicts remain adapter-private.

The deterministic spike harness remains the only machine approver. The managed auditor may
block convergence but cannot manufacture machine evidence or write trusted state through a
model-callable surface.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from adapters.state import project_id, run_id
from adapters import bridge as application_bridge
from adapters.governance import operator_inventory
from .transport import CODEX_PROFILE_ID

from .correlation import PROTOCOL, request_id as new_request_id
from .transport import BridgeTransport, Transport
from .audit import execute_audit

_ACTIVATION = re.compile(
    r"^\s*(?:\$empirica(?::empirica)?|/empirica(?::empirica)?)\b(?P<args>.*)$",
    re.DOTALL,
)
_MODE_FLAGS = {"--multi-provider": "multi_provider", "--cli-exec": "cli_exec"}
_MODE_ENV = {"multi_provider": "EMPIRICA_MODE_MULTI_PROVIDER", "cli_exec": "EMPIRICA_MODE_CLI_EXEC"}
_TRUE = frozenset({"1", "true", "on", "enabled"})
_FALSE = frozenset({"0", "false", "off", "disabled", ""})


class SelectorError(ValueError):
    """A Codex payload does not carry enough well-typed identity to select a run."""


# --- payload context and selector --------------------------------------------

def context_from_payload(payload: Mapping[str, object]) -> tuple[Path, str]:
    """Return validated ``(cwd, session_id)`` host context.

    Both ``cwd`` and ``session_id`` are required non-empty strings; Codex 0.146.0 always
    supplies them.  A present non-string/empty value is rejected instead of being stringified
    into a surprising repository path.
    """
    cwd = payload.get("cwd")
    session_id = payload.get("session_id")
    if not isinstance(cwd, str) or not cwd:
        raise SelectorError("cwd must be a non-empty string")
    if not isinstance(session_id, str) or not session_id:
        raise SelectorError("session_id must be a non-empty string")
    return Path(cwd), session_id


def selector_from_payload(payload: Mapping[str, object]) -> dict[str, str]:
    """Build the transport-neutral ``StartRun``/``ResolveRun`` selector for a Codex payload."""
    cwd, session_id = context_from_payload(payload)
    return {"project": project_id(cwd), "session": run_id(session_id)}


# --- activation and invocation ------------------------------------------------

def explicit_activation(payload: Mapping[str, object]) -> str | None:
    """Return invocation arguments only when the prompt explicitly starts Empirica."""
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        return None
    match = _ACTIVATION.match(prompt)
    return match.group("args").strip() if match else None


def _activation_args(payload: Mapping[str, object]) -> str:
    args = explicit_activation(payload)
    return args if isinstance(args, str) else ""


def _env_mode(environ: Mapping[str, str], mode: str) -> bool | None:
    raw = environ.get(_MODE_ENV[mode])
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return None


def _resolve_modes(args: str, environ: Mapping[str, str]) -> dict[str, bool]:
    """Resolve env > leading invocation flag > default for each known mode."""
    tokens = args.split()
    flags: dict[str, bool] = {}
    index = 0
    while index < len(tokens) and tokens[index].startswith("--"):
        token = tokens[index]
        if token in _MODE_FLAGS:
            flags[_MODE_FLAGS[token]] = True
        elif token.startswith("--no-") and f"--{token[5:]}" in _MODE_FLAGS:
            flags[_MODE_FLAGS[f"--{token[5:]}"]] = False
        index += 1
    modes: dict[str, bool] = {}
    for mode in ("multi_provider", "cli_exec"):
        env = _env_mode(environ, mode)
        if env is not None:
            modes[mode] = env
        elif mode in flags:
            modes[mode] = flags[mode]
    return modes


def _goal(args: str, fallback: str) -> str:
    tokens = args.split()
    index = 0
    while index < len(tokens) and tokens[index].startswith("--"):
        index += 1
    return " ".join(tokens[index:]).strip() or fallback


def _positive_env(environ: Mapping[str, str], name: str, *, zero: bool = False) -> int | None:
    value = environ.get(name)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= (0 if zero else 1) else None


# --- request builders ---------------------------------------------------------

def build_start_run_request(
    payload: Mapping[str, object], *, correlation_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict | None:
    """Translate an explicit ``$empirica`` prompt into an exact v2 ``StartRun`` envelope.

    No ``actor`` field is emitted.  Explicit max values are nested under ``budgets`` and omitted
    when absent; resolved modes are emitted only when non-empty.  Returns ``None`` when the prompt
    does not explicitly start Empirica.
    """
    args = explicit_activation(payload)
    if args is None:
        return None
    env = os.environ if environ is None else environ
    command: dict = {
        "type": "StartRun",
        "selector": selector_from_payload(payload),
        "goal": _goal(args, "empirica run (goal unspecified)"),
    }
    leading = []
    for token in args.split():
        if not token.startswith("--"):
            break
        leading.append(token)
    if "--auto" in leading:
        command["control_mode"] = "auto"
    modes = _resolve_modes(args, env)
    if modes:
        command["modes"] = modes
    budgets: dict[str, int] = {}
    if (passes := _positive_env(env, "EMPIRICA_MAX_PASSES")) is not None:
        budgets["max_passes"] = passes
    if (spawns := _positive_env(env, "EMPIRICA_MAX_SPAWNS", zero=True)) is not None:
        budgets["max_spawns"] = spawns
    if (audits := _positive_env(env, "EMPIRICA_MAX_AUDIT_SPAWNS", zero=True)) is not None:
        budgets["max_audit_spawns"] = audits
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
    """Translate one validated Codex payload into an exact v2 ``ResolveRun`` envelope."""
    return {
        "protocol": PROTOCOL,
        "request_id": correlation_id or new_request_id(payload, "resolve"),
        "command": {"type": "ResolveRun", "selector": selector_from_payload(payload)},
    }


# --- lifecycle entry points ---------------------------------------------------

def _dispatch(payload: Mapping[str, object], request: dict,
               transport: Transport | None = None) -> dict:
    return (transport if transport is not None else BridgeTransport()).dispatch(request)


def _resolve_run(payload: Mapping[str, object], transport: Transport | None = None, *, strict=False) -> str | None:
    """``ResolveRun`` through the strict bridge shell; return a run handle only when resolved.

    At D6 the no-location run port reports every opaque ID unresolved, so this always
    returns ``None``.  Any transport failure is treated as no resolvable run rather than
    wedging the host event.
    """
    try:
        response = _dispatch(payload, build_resolve_request(payload), transport)
    except Exception:
        if strict:
            raise
        return None
    result = response.get("result") if isinstance(response, dict) else None
    run = result.get("run") if isinstance(result, dict) else None
    handle = run.get("id") if isinstance(run, dict) else None
    if strict and not handle and not (isinstance(result, dict) and result.get("type") == "Inert" and result.get("reason") == "no_run"):
        raise RuntimeError("run resolution unavailable")
    return handle if isinstance(handle, str) and handle else None


def _context_output(event: str, context: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}


def _start(payload: dict) -> dict | None:
    """UserPromptSubmit: best-effort activation, always fail open."""
    request = build_start_run_request(payload)
    if request is None:
        return None
    response = _dispatch(payload, request)
    result = response.get("result", {}) if isinstance(response, dict) else {}
    if result.get("type") == "Fault":
        code = result.get("code")
        text = code if isinstance(code, str) and code else "unknown"
        return {"systemMessage": f"empirica activation failed: {text}"}
    run = result.get("run", {}) if isinstance(result, dict) else {}
    handle = run.get("id", "unresolved")
    if handle != "unresolved":
        _refresh_governance(payload, handle)
    context = (
        f"Empirica v2 is active. Opaque run handle: {handle}. "
        "Use empirica_observe for route/graph/research/spike/freeze actions, "
        "empirica_read for the complete RunView and audit argument, and "
        "report_convergence only after the Codex Stop hook's bound managed audit. "
        "Trusted ingress is never model-callable."
    )
    return _context_output("UserPromptSubmit", context)


def _refresh_governance(payload: dict, handle: str) -> dict:
    model = payload.get("model")
    return application_bridge.trusted_governance_context(CODEX_PROFILE_ID, handle, {
        "inventory": operator_inventory(), "author": {"provider_id": "openai", "model_id": model}
        if isinstance(model, str) and model else None, "ingress": "unavailable"})


def _pre_tool_use(payload: dict) -> dict | None:
    """Only exact absence is inert. Unknown storage/approval denies native investigation."""
    try:
        handle = _resolve_run(payload, strict=True)
        if handle is None:
            return None
        refreshed = _refresh_governance(payload, handle)
        if refreshed.get("result", {}).get("type") not in {"Allow", "Inert"}:
            raise RuntimeError("governance context unavailable")
        name = payload.get("tool_name", "")
        if name in {prefix + tool for prefix in ("", "mcp__empirica__")
                    for tool in ("empirica_read", "empirica_observe", "report_convergence")}:
            return None
        result = _dispatch(payload, {"protocol": PROTOCOL, "request_id": new_request_id(payload, "investigate"),
            "command": {"type": "ObserveAction", "run_id": handle, "action": {"kind": "investigate"}}})["result"]
        if result.get("type") == "Allow":
            return None
        reason = result.get("reasons", [{}])[0].get("message", "Empirica investigation denied")
    except SelectorError:
        return None
    except Exception:
        reason = "Empirica run/approval unavailable; investigation denied"
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                    "permissionDecisionReason": reason}}


def build_evaluate_request(payload: Mapping[str, object], run_id: str) -> dict:
    return {"protocol": PROTOCOL, "request_id": new_request_id(payload, "evaluate"),
            "command": {"type": "EvaluateRun", "run_id": run_id,
                        "intent": "report_convergence"}}


def _audit_required(result: Mapping[str, object]) -> bool:
    reasons = result.get("reasons")
    if not isinstance(reasons, list):
        return False
    return any(isinstance(reason, Mapping)
               and reason.get("code") in {"audit.required", "audit.pending", "audit.stale"}
               for reason in reasons)


def _stop(payload: dict) -> dict | None:
    """Stop: enforce convergence and run one adapter-owned bound audit when it is due."""
    try:
        handle = _resolve_run(payload, strict=True)
        if handle is None:
            return None
        _refresh_governance(payload, handle)
        response = _dispatch(payload, build_evaluate_request(payload, handle))
        result = response.get("result", {}) if isinstance(response, dict) else {}
        if isinstance(result, Mapping) and _audit_required(result):
            execute_audit(payload, handle)
            response = _dispatch(payload, build_evaluate_request(payload, handle))
    except Exception:  # active located run: evaluation/audit failure must deny Stop
        return {"decision": "block", "reason": "Empirica convergence gate unavailable."}
    result = response.get("result", {}) if isinstance(response, dict) else {}
    if result.get("type") == "Allow" and result.get("converged") is True:
        return {"continue": True}
    if result.get("type") == "Block":
        reasons = result.get("reasons", [])
        message = (reasons[0].get("message") if reasons and isinstance(reasons[0], dict)
                   else "Empirica convergence is blocked.")
        return {"decision": "block", "reason": message}
    if result.get("type") == "Inert":
        return None
    return {"decision": "block", "reason": "Empirica convergence gate unavailable."}


def _restore(payload: dict) -> dict | None:
    """SessionStart:compact: ``ResolveRun`` through the strict shell; inert when unresolved."""
    if payload.get("source") != "compact":
        return None
    _resolve_run(payload)
    return None  # D6: no active run to restore (D7 owns run identity)


def _payload() -> dict:
    try:
        value = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def main(argv: list[str] | None = None) -> int:
    action = (argv or [""])[0]
    payload = _payload()
    try:
        output = {
            "activate": _start,
            "pre-tool-use": _pre_tool_use,
            "stop": _stop,
            "restore": _restore,
        }[action](payload)
    except KeyError:
        print(f"unknown Codex hook action: {action}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - never wedge a host event; unresolved → inert
        output = None
    if output is not None:
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
