"""Active Claude lifecycle entry points.

Every function in this module translates one native hook event, resolves the run through the
versioned application API, and maps the typed result back to Claude's process contract.  It never
reads the operational store, Git refs, or host-specific runtime directories directly.
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping

from .completion import dispatch_stop, stop_result
from .dispatch import bash_command, dispatch_advice, dispatched_harness
from .restore import dispatch_restore, restore_context
from .route import dispatch_investigation
from .run_start import dispatch_start_run
from .selector import context_from_payload, selector_from_payload
from .spawn import dispatch_reserve_spawn, spawn_decision
from .transport import BridgeTransport


def _payload() -> dict:
    try:
        value = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _resolve(payload: Mapping[str, object]) -> tuple[str | None, dict | None]:
    """Return ``(handle, result)`` using only ResolveRun through the shared bridge."""
    context = context_from_payload(payload)
    request = {
        "protocol": "empirica/v1",
        "request_id": "claude-resolve",
        "command": {"type": "ResolveRun", "selector": selector_from_payload(payload)},
    }
    response = BridgeTransport(context.cwd).dispatch(request)
    result = response.get("result") if isinstance(response, dict) else None
    run = result.get("run") if isinstance(result, dict) else None
    handle = run.get("id") if isinstance(run, dict) else None
    return (handle if isinstance(handle, str) and handle else None,
            result if isinstance(result, dict) else None)


def run_start_main() -> int:
    """UserPromptExpansion: best-effort activation, always silent and fail open."""
    payload = _payload()
    try:
        dispatch_start_run(payload)
    except Exception:  # noqa: BLE001 - this event must never wedge prompt expansion
        pass
    return 0


def spawn_main() -> int:
    """PreToolUse:Agent: reserve one slot and issue an audit ticket for auditor dispatches."""
    payload = _payload()
    try:
        handle, _ = _resolve(payload)
        if handle is None:
            return 0
        response = dispatch_reserve_spawn(payload, handle)
        decision = spawn_decision(response)
        if decision.exit_code:
            if decision.reason:
                print(decision.reason, file=sys.stderr)
            return decision.exit_code
        tool_input = payload.get("tool_input")
        candidates = ("subagent_type", "subagentType", "agent_type", "agentType", "name")
        is_auditor = isinstance(tool_input, Mapping) and any(
            "empirica-auditor" in str(tool_input.get(key, "")) for key in candidates
        )
        if is_auditor:
            BridgeTransport(context_from_payload(payload).cwd).dispatch({
                "protocol": "empirica/v1", "request_id": "claude-audit-ticket",
                "command": {"type": "ObserveAction", "run_id": handle,
                            "action": {"kind": "audit_ticket"}},
            })
    except Exception:  # noqa: BLE001 - PreToolUse resource gates fail open on adapter failure
        return 0
    return 0


def route_main() -> int:
    """PreToolUse investigative observation: best effort and always silent."""
    payload = _payload()
    try:
        handle, _ = _resolve(payload)
        if handle is not None:
            dispatch_investigation(payload, handle)
    except Exception:  # noqa: BLE001 - observational event never blocks tools
        pass
    return 0


def _model_from_command(command: str) -> str | None:
    match = re.search(r"(?:--model(?:=|\s+)|-m\s+)([^\s'\"]+)", command)
    return match.group(1) if match else None


def dispatch_main() -> int:
    """PreToolUse:Bash: gate recognised CLI actors and record witnessed attribution."""
    payload = _payload()
    try:
        command = bash_command(payload)
        harness = dispatched_harness(command)
        if harness is None:
            return 0
        handle, result = _resolve(payload)
        if handle is None or not isinstance(result, dict):
            return 0
        context = context_from_payload(payload)
        restored = dispatch_restore(payload, handle)
        run = restored.get("result", {}).get("run", {})
        modes = run.get("snapshot", {}).get("modes", {}) if isinstance(run, dict) else {}
        if not isinstance(modes, dict) or not modes.get("cli_exec"):
            return 0
        reserved = dispatch_reserve_spawn({**payload, "tool_name": "Agent"}, handle)
        decision = spawn_decision(reserved)
        if decision.exit_code:
            print(decision.reason or "empirica CLI dispatch denied", file=sys.stderr)
            return decision.exit_code
        model = _model_from_command(command or "")
        if model:
            BridgeTransport(context.cwd).dispatch({
                "protocol": "empirica/v1", "request_id": "claude-dispatch",
                "command": {"type": "ObserveAction", "run_id": handle,
                            "action": {"kind": "dispatch", "witnessed": True,
                                       "actor": {"model": model, "harness": harness,
                                                 "source_type": "LLM_JUDGE"}}},
            })
        advice = dispatch_advice(command, handle)
        if advice:
            print(advice, file=sys.stderr)
    except Exception:  # noqa: BLE001 - unrecognised/failed Bash classification fails open
        return 0
    return 0


def completion_main() -> int:
    """Stop: fail closed for an active/corrupt run, silent when no run exists."""
    payload = _payload()
    try:
        handle, resolved = _resolve(payload)
        if handle is None:
            if isinstance(resolved, dict) and resolved.get("type") == "Fault":
                message = resolved.get("message") or "empirica run lookup failed"
                print(message, file=sys.stderr)
                return 2
            return 0
        mapped = stop_result(dispatch_stop(payload, handle))
    except Exception as exc:  # noqa: BLE001 - completion is the fail-closed boundary
        print(f"empirica completion gate unavailable: {exc}", file=sys.stderr)
        return 2
    if mapped.stdout:
        sys.stdout.write(mapped.stdout)
    if mapped.stderr:
        sys.stderr.write(mapped.stderr)
    return mapped.exit_code


def restore_main() -> int:
    """SessionStart:compact: observational, bounded restore context, always exit zero."""
    payload = _payload()
    try:
        handle, _ = _resolve(payload)
        if handle is not None:
            context = restore_context(dispatch_restore(payload, handle))
            if context:
                print(context)
    except Exception:  # noqa: BLE001 - restore never wedges session start
        pass
    return 0
