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

from .audit import build_audit_verdict_request, child_prompt, verdict_from_final_output
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


def _pretooluse_context(text: str) -> None:
    """Emit non-blocking additional context the model will see, from a PreToolUse hook (exit 0).

    Claude Code sends exit-0 PreToolUse stderr to the debug log only — the model never sees it
    (code.claude.com/docs/en/hooks, ADR-35). The model-visible channel is
    ``hookSpecificOutput.additionalContext`` on stdout; omitting ``permissionDecision`` leaves the
    tool's normal permission flow untouched, so this warns without blocking or auto-approving."""
    json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}},
              sys.stdout)
    sys.stdout.write("\n")


def _warn_if_p1_violation(response: object) -> None:
    """Relay the application's P1 ordering verdict to the model when investigation preceded the
    route (ADR-35). The verdict/reason already ride on the investigate response's ``run.route``
    fragment; this surfaces them at the moment of violation instead of only at the audit."""
    result = response.get("result") if isinstance(response, dict) else None
    run = result.get("run") if isinstance(result, dict) else None
    route = run.get("route") if isinstance(run, dict) else None
    if isinstance(route, dict) and route.get("verdict") == "violation":
        reason = route.get("reason") or "investigation began before the route was announced"
        _pretooluse_context(
            f"empirica P1 violation: {reason} Record your route now via "
            "ObserveAction(kind='route'); this ordering is what the independent audit fails on "
            "(ADR-20 P1).")


def run_start_main() -> int:
    """UserPromptExpansion: best-effort activation, always silent and fail open."""
    payload = _payload()
    try:
        dispatch_start_run(payload)
    except Exception:  # noqa: BLE001 - this event must never wedge prompt expansion
        pass
    return 0


def _contract_text(result: Mapping[str, object]) -> str:
    run = result.get("run")
    contract = run.get("contract") if isinstance(run, Mapping) else None
    if not isinstance(contract, Mapping):
        return ""
    from vendor.obligations import render_text
    return render_text(contract)


def _deny(reason: str, result: Mapping[str, object] | None = None) -> int:
    text = reason
    if result is not None:
        contract = _contract_text(result)
        if contract:
            text += "\n" + contract
    print(text, file=sys.stderr)
    return 2


def _launch_is_executable(tool_input: object) -> bool:
    """Only executable XOR launch shapes spend a reservation; list/management calls pass."""
    if not isinstance(tool_input, Mapping) or tool_input.get("action") == "list":
        return False
    # Claude calls the agent selector subagent_type.  Accept agent as the portable spelling,
    # but do not let duplicate aliases turn a malformed call into an executable launch.
    agent = any(tool_input.get(key) is not None for key in
                ("agent", "subagent_type", "subagentType", "agent_type", "agentType"))
    keys = [agent, tool_input.get("workflowScript") is not None, tool_input.get("resume") is not None]
    return sum(keys) == 1


def _is_auditor(tool_input: Mapping[str, object]) -> bool:
    return "empirica-auditor" in json.dumps(dict(tool_input), sort_keys=True).lower()


def _auditor_model(tool_input: Mapping[str, object]) -> str:
    model = tool_input.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    # This is the declared model in agents/empirica-auditor.md, the source of the rubric too.
    from .audit import _AUDITOR
    frontmatter = _AUDITOR.read_text(encoding="utf-8").split("---", 2)[1]
    match = re.search(r"^model:\s*(\S+)", frontmatter, re.MULTILINE)
    return match.group(1) if match else "empirica-auditor"


def _fault_is_our_invalid_request(response: object, operation: str) -> bool:
    result = response.get("result") if isinstance(response, Mapping) else None
    if isinstance(result, Mapping) and result.get("type") == "Fault" and result.get("code") == "invalid_request":
        print(f"empirica adapter bug: {operation} payload rejected: {result.get('message', 'invalid request')}", file=sys.stderr)
        return True
    return False


def _void_spawn(transport: BridgeTransport, handle: str, nonce: str | None = None,
                reservation_id: str | None = None) -> None:
    action = {"kind": "void_spawn"}
    if nonce is not None:
        action["nonce"] = nonce
    if reservation_id is not None:
        action["reservation_id"] = reservation_id
    transport.dispatch({"protocol": "empirica/v1", "request_id": "claude-void-spawn",
                        "command": {"type": "ObserveAction", "run_id": handle,
                                    "action": action}})


def spawn_main() -> int:
    """PreToolUse:Agent: reserve, ticket, and privately inject an auditor's dossier."""
    payload = _payload()
    tool_input = payload.get("tool_input")
    if not _launch_is_executable(tool_input):
        return 0
    assert isinstance(tool_input, Mapping)
    try:
        handle, _ = _resolve(payload)
        if handle is None:
            return 0
        response = dispatch_reserve_spawn(payload, handle)
        if _fault_is_our_invalid_request(response, "reserve_spawn"):
            return 2
        decision = spawn_decision(response)
        if decision.exit_code:
            return _deny(decision.reason or "empirica spawn denied",
                         response.get("result") if isinstance(response, Mapping) else None)
        if not _is_auditor(tool_input):
            return 0
        transport = BridgeTransport(context_from_payload(payload).cwd)
        reserve_result = response.get("result") if isinstance(response, Mapping) else None
        reserve_run = reserve_result.get("run") if isinstance(reserve_result, Mapping) else None
        reserve_view = reserve_run.get("spawn") if isinstance(reserve_run, Mapping) else None
        reservation_id = reserve_view.get("reservation_id") if isinstance(reserve_view, Mapping) else None
        actor = {"model": _auditor_model(tool_input), "harness": "claude-code",
                 "provider": "anthropic", "source_type": "LLM_JUDGE", "attribution": "declared"}
        ticket = transport.dispatch({"protocol": "empirica/v1", "request_id": "claude-audit-ticket",
                                     "command": {"type": "ObserveAction", "run_id": handle,
                                                 "action": {"kind": "audit_ticket", "actor": actor,
                                                            "reservation_id": reservation_id,
                                                            "witnessed": False}}})
        if _fault_is_our_invalid_request(ticket, "audit_ticket"):
            if isinstance(reservation_id, str):
                _void_spawn(transport, handle, reservation_id=reservation_id)
            return 2
        ticket_result = ticket.get("result") if isinstance(ticket, Mapping) else None
        if isinstance(ticket_result, Mapping) and ticket_result.get("type") == "Block":
            if isinstance(reservation_id, str):
                _void_spawn(transport, handle, reservation_id=reservation_id)
            return _deny(str(ticket_result.get("reason") or "empirica audit ticket denied"), ticket_result)
        if not isinstance(ticket_result, Mapping) or ticket_result.get("type") == "Fault":
            if isinstance(reservation_id, str):
                _void_spawn(transport, handle, reservation_id=reservation_id)
            return _deny(str(ticket_result.get("message") if isinstance(ticket_result, Mapping) else "empirica audit ticket unavailable"), ticket_result if isinstance(ticket_result, Mapping) else None)
        run = ticket_result.get("run")
        ticket_view = run.get("ticket") if isinstance(run, Mapping) else None
        nonce = ticket_view.get("nonce") if isinstance(ticket_view, Mapping) else None
        if not isinstance(nonce, str) or not nonce:
            return _deny("empirica audit ticket unavailable")
        argument_response = transport.dispatch({"protocol": "empirica/v1", "request_id": "claude-get-argument",
                                                "command": {"type": "GetArgument", "run_id": handle}})
        if _fault_is_our_invalid_request(argument_response, "GetArgument"):
            _void_spawn(transport, handle, nonce)
            return _deny("empirica audit argument unavailable", argument_response.get("result") if isinstance(argument_response, Mapping) else None)
        argument_result = argument_response.get("result") if isinstance(argument_response, Mapping) else None
        argument_run = argument_result.get("run") if isinstance(argument_result, Mapping) else None
        argument = argument_run.get("argument") if isinstance(argument_run, Mapping) else None
        text = argument.get("text") if isinstance(argument, Mapping) else None
        if not isinstance(argument_result, Mapping) or argument_result.get("type") in {"Fault", "Block"} or not isinstance(text, str):
            _void_spawn(transport, handle, nonce)
            return _deny("empirica audit argument unavailable", argument_result if isinstance(argument_result, Mapping) else None)
        updated = dict(tool_input)
        existing = updated.get("prompt")
        updated["prompt"] = ((str(existing) + "\n\n") if isinstance(existing, str) and existing else "") + child_prompt(text, nonce)
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}}, sys.stdout)
        sys.stdout.write("\n")
    except Exception:  # noqa: BLE001 - PreToolUse resource gates fail open on adapter failure
        return 0
    return 0


def _transcript_final_assistant(path: object) -> str | None:
    """Best-effort fallback for older payloads missing documented last_assistant_message."""
    if not isinstance(path, str) or not path:
        return None
    final: str | None = None
    try:
        for line in open(path, encoding="utf-8"):
            event = json.loads(line)
            if not isinstance(event, Mapping):
                continue
            message = event.get("message")
            role = message.get("role") if isinstance(message, Mapping) else event.get("role")
            if event.get("type") != "assistant" and role != "assistant":
                continue
            content = message.get("content") if isinstance(message, Mapping) else event.get("content")
            if isinstance(content, str):
                final = content
            elif isinstance(content, list):
                final = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, Mapping) and part.get("type", "text") == "text")
    except (OSError, ValueError, TypeError):
        return None
    return final


def subagent_stop_main() -> int:
    """SubagentStop observes an auditor's final answer; it never blocks the child."""
    payload = _payload()
    try:
        if "empirica-auditor" not in str(payload.get("agent_type", "")).lower():
            return 0
        handle, _ = _resolve(payload)
        if handle is None:
            return 0
        final = payload.get("last_assistant_message")
        text = final if isinstance(final, str) else _transcript_final_assistant(payload.get("agent_transcript_path"))
        verdict = verdict_from_final_output(text)
        if verdict is None:
            print("empirica: auditor returned no valid verdict; audit obligation remains open.", file=sys.stderr)
            return 0
        response = BridgeTransport(context_from_payload(payload).cwd).dispatch(
            build_audit_verdict_request(handle, verdict, request_id="claude-audit-verdict"))
        result = response.get("result") if isinstance(response, Mapping) else None
        outcome = result.get("type") if isinstance(result, Mapping) else "unavailable"
        contract = _contract_text(result) if isinstance(result, Mapping) else ""
        print(f"empirica: host recorded auditor verdict ({verdict.get('verdict', 'unknown')}): {outcome}" +
              ("\n" + contract if contract else ""), file=sys.stderr)
    except Exception:  # noqa: BLE001 - SubagentStop is strictly observational
        return 0
    return 0


def route_main() -> int:
    """PreToolUse investigative observation: best effort; non-blocking P1 warning to the model."""
    payload = _payload()
    try:
        handle, _ = _resolve(payload)
        if handle is not None:
            _warn_if_p1_violation(dispatch_investigation(payload, handle))
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
            return _deny(decision.reason or "empirica CLI dispatch denied",
                         reserved.get("result") if isinstance(reserved, Mapping) else None)
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
            # exit-0 PreToolUse stderr is invisible to the model (ADR-35); use the context channel.
            _pretooluse_context(advice)
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
