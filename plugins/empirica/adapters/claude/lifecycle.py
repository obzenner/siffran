"""Active Claude lifecycle entry points (D6-C).

Each function translates one native hook event, dispatches an exact v2 request through the shared
bridge, and maps the typed result back to Claude's process contract with honest native
unsupported/fail-closed behavior.  Run identity/location is D7-owned: at D6 the no-location run
port reports every opaque ID unresolved, so ``ResolveRun`` returns unsupported/closed and no run
handle is resolved — state-bearing gates therefore have no active run to enforce and are inert,
while their fail-closed response mapping (``stop_result``/``spawn_decision``) is retained and
exercised for the D7+ cases that do return a handle.

Removed operations (``void_spawn``/``audit_ticket``/``consume``/``phase``) and author-submitted
trusted actions (``evidence_leaf``/``attribution``/``child_event``/``audit_verdict``) are never
dispatched and never fabricated into another v2 action: the adapter fails closed locally instead.
No run file, Git ref, or host runtime directory is read directly.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping

from adapters import bridge as application_bridge
from adapters.audit import child_prompt, verdict_from_final_output
from adapters.audit_protocol import (AuditLaunchPlan, AuditProtocol, AuditProtocolError,
                                     IdentityObservation)
from .completion import dispatch_stop, stop_result
from .dispatch import bash_command, dispatched_harness
from .restore import dispatch_restore, restore_context
from .route import dispatch_investigation
from .run_start import dispatch_resolve, dispatch_start_run
from .spawn import dispatch_child_reserve, spawn_decision
from .transport import CLAUDE_PROFILE_ID


def _payload() -> dict:
    try:
        value = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _resolve(
    payload: Mapping[str, object], *, strict: bool = False,
) -> tuple[str | None, dict | None]:
    """Return ``(handle, result)`` using only ``ResolveRun`` through the shared bridge.

    Observational hooks preserve non-wedging behavior. Admission hooks pass ``strict``: only exact
    ``Inert/no_run`` proves that no cap exists; transport failure, faults, and malformed no-handle
    responses are unavailable and must fail closed.
    """
    try:
        response = dispatch_resolve(payload, correlation_id="claude-resolve")
    except Exception:  # noqa: BLE001 - admission callers distinguish unavailable from no-run
        if strict:
            raise
        return None, None
    result = response.get("result") if isinstance(response, dict) else None
    run = result.get("run") if isinstance(result, dict) else None
    handle = run.get("id") if isinstance(run, dict) else None
    if isinstance(handle, str) and handle:
        return handle, result if isinstance(result, dict) else None
    if strict and not (isinstance(result, dict) and result.get("type") == "Inert"
                       and result.get("reason") == "no_run"):
        raise RuntimeError("run resolution unavailable")
    return None, result if isinstance(result, dict) else None


def _deny(reason: str, result: Mapping[str, object] | None = None) -> int:
    print(reason, file=sys.stderr)
    if result is not None:
        run = result.get("run") if isinstance(result, Mapping) else None
        contract = run.get("contract") if isinstance(run, Mapping) else None
        if isinstance(contract, Mapping) and isinstance(contract.get("sections"), Mapping):
            from vendor.obligations import render_text
            print(render_text(contract), file=sys.stderr)
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


def _agent_identity(tool_input: Mapping[str, object]) -> tuple[str | None, str | None]:
    """Return ``(purpose, role_profile)`` as real host inputs, else ``(None, None)``."""
    purpose = tool_input.get("prompt")
    purpose = purpose if isinstance(purpose, str) and purpose.strip() else None
    role_profile = None
    for key in ("subagent_type", "agent", "subagentType", "agentType", "agent_type"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            role_profile = value.strip()
            break
    return purpose, role_profile


def run_start_main() -> int:
    """UserPromptExpansion: activate and inject the opaque handle/public tool contract."""
    payload = _payload()
    try:
        response = dispatch_start_run(payload)
        result = response.get("result", {}) if isinstance(response, dict) else {}
        run = result.get("run", {}) if isinstance(result, Mapping) else {}
        handle = run.get("id") if isinstance(run, Mapping) else None
        if isinstance(handle, str) and handle:
            context = (
                f"Empirica v2 is active. Opaque run handle: {handle}. "
                "Use empirica_observe for public author actions, empirica_read for the "
                "current RunView/argument/contract, and report_convergence only after "
                "the bound audit is current. Never submit trusted ingress."
            )
            json.dump({"hookSpecificOutput": {"hookEventName": "UserPromptExpansion",
                                              "additionalContext": context}}, sys.stdout)
            sys.stdout.write("\n")
    except Exception:  # noqa: BLE001 - this event must never wedge prompt expansion
        pass
    return 0


def spawn_main() -> int:
    """PreToolUse:Agent: reserve a real launch via ``child_reserve``, else inert.

    A real launch with real ``purpose``/``role_profile``/``execution`` dispatches ``child_reserve``;
    the D6 service returns unsupported/closed (D8 owns admission), which the gate maps to a deny
    when a handle exists.  Non-launch (list/management/ambiguous) calls are inert.  Missing real
    inputs fail closed locally.  When an active run exists, a malformed response, a closed
    ``Fault``, or an adapter exception all deny the native launch — never silently admitted.
    No ``void_spawn``/``audit_ticket`` is dispatched or fabricated.
    """
    payload = _payload()
    tool_input = payload.get("tool_input")
    if not _launch_is_executable(tool_input):
        return 0
    assert isinstance(tool_input, Mapping)
    purpose, role_profile = _agent_identity(tool_input)
    if not purpose or not role_profile:
        return _deny("empirica spawn denied: missing real purpose or role profile")
    is_auditor = role_profile == "empirica:empirica-auditor"
    reservation_purpose = "audit" if is_auditor else purpose
    try:
        handle, _ = _resolve(payload, strict=True)
    except Exception:  # noqa: BLE001 - executable launch resolution must fail closed
        return _deny("empirica spawn denied: run resolution unavailable")
    if handle is None:
        return 0  # exact no-active-run response → no cap to enforce
    try:
        investigation = dispatch_investigation(payload, handle)
        if investigation is not None:
            decision = spawn_decision(investigation)
            if decision.exit_code:
                result = investigation.get("result") if isinstance(investigation, Mapping) else None
                return _deny(decision.reason or "empirica investigation denied",
                             result if isinstance(result, Mapping) else None)
        if not is_auditor:
            response = dispatch_child_reserve(
                payload, handle, purpose=reservation_purpose, role_profile=role_profile,
                execution="foreground", correlation_id="claude-child-reserve")
            decision = spawn_decision(response)
            return (_deny(decision.reason or "empirica spawn denied",
                          response.get("result") if isinstance(response, Mapping) else None)
                    if decision.exit_code else 0)
        if "model" in tool_input:
            return _deny("empirica auditor launch forbids model overrides")
        plan = AuditProtocol(CLAUDE_PROFILE_ID, execution="async").prepare(
            handle, role_profile="empirica:empirica-auditor")
        updated = {
            "subagent_type": "empirica:empirica-auditor",
            "description": "Bound Empirica audit",
            "prompt": child_prompt(plan.argument),
            "run_in_background": True,
            "max_turns": 8,
        }
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                          "updatedInput": updated}}, sys.stdout)
        sys.stdout.write("\n")
    except AuditProtocolError as exc:
        return _deny(f"empirica spawn denied: {exc}")
    except Exception:  # noqa: BLE001 - active-run launch failures deny below
        return _deny("empirica spawn denied: adapter failure")
    return 0


def route_main() -> int:
    """PreToolUse: deny active-run investigation until the core admits its witness."""
    payload = _payload()
    try:
        handle, _ = _resolve(payload, strict=True)
        if handle is None:
            return 0
        response = dispatch_investigation(payload, handle)
        if response is None:
            return 0
        decision = spawn_decision(response)
        if decision.exit_code:
            result = response.get("result") if isinstance(response, Mapping) else None
            return _deny(decision.reason or "empirica investigation denied",
                         result if isinstance(result, Mapping) else None)
    except Exception:  # noqa: BLE001 - active-run investigation must fail closed
        return _deny("empirica investigation denied: adapter failure")
    return 0


def dispatch_main() -> int:
    """PreToolUse:Bash: record a recognized CLI actor dispatch; fail open."""
    payload = _payload()
    try:
        harness = dispatched_harness(bash_command(payload))
        if harness is None:
            return 0
        handle, _ = _resolve(payload)
        if handle is None:
            return 0  # no active run → nothing to attribute
        from .dispatch import dispatch_dispatch
        _, advice = dispatch_dispatch(payload, handle, correlation_id="claude-dispatch")
        if advice:
            json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "additionalContext": advice}}, sys.stdout)
            sys.stdout.write("\n")
    except Exception:  # noqa: BLE001 - unrecognised/failed Bash classification fails open
        return 0
    return 0


def completion_main() -> int:
    """Stop: fail closed for an active/blocked run, silent when no run exists."""
    payload = _payload()
    try:
        handle, _ = _resolve(payload)
        if handle is None:
            return 0  # no active run to gate (D7 owns run identity/evaluation)
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
            AuditProtocol(CLAUDE_PROFILE_ID).reconcile_orphans(handle, native_prefix="claude-session-restore", include_pending=False)
            if context := restore_context(dispatch_restore(payload, handle)):
                print(context)
    except Exception:  # noqa: BLE001 - restore never wedges session start
        pass
    return 0


def _transcript_observation(path: object) -> tuple[str | None, str | None]:
    """Return the last concrete assistant model and textual final message."""
    if not isinstance(path, str) or not path:
        return None, None
    model = final = None
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                message = row.get("message", {}) if isinstance(row, dict) else {}
                if message.get("role") != "assistant":
                    continue
                candidate = message.get("model")
                if isinstance(candidate, str) and candidate:
                    model = candidate
                content = message.get("content")
                if isinstance(content, str):
                    final = content
                elif isinstance(content, list):
                    text = [item.get("text") for item in content
                            if isinstance(item, Mapping) and item.get("type") == "text"
                            and isinstance(item.get("text"), str)]
                    if text:
                        final = "\n".join(text)
    except (OSError, ValueError, TypeError):
        return model, final
    return model, final


def _transcript_handbacks(path: object) -> list[str] | None:
    """Return host-observed SubagentHandback messages, or None when unreadable.

    Claude 2.1.278 may append explanatory assistant prose after delivering the
    actual final report through SubagentHandback.  The handback tool input is
    therefore the authoritative candidate whenever one is present.
    """
    if not isinstance(path, str) or not path:
        return None
    messages: list[str] = []
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                message = row.get("message", {}) if isinstance(row, dict) else {}
                if message.get("role") != "assistant":
                    continue
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for item in content:
                    if (not isinstance(item, Mapping) or item.get("type") != "tool_use"
                            or item.get("name") != "SubagentHandback"):
                        continue
                    tool_input = item.get("input")
                    candidate = tool_input.get("message") if isinstance(tool_input, Mapping) else None
                    if isinstance(candidate, str):
                        messages.append(candidate)
                    else:
                        messages.append("")
    except (OSError, ValueError, TypeError):
        return None
    return messages


def _durable_plan(handle: str, child_id: str) -> AuditLaunchPlan | None:
    operation = application_bridge.trusted_audit_plan(CLAUDE_PROFILE_ID, handle, child_id)
    if not isinstance(operation, Mapping):
        return None
    role = operation.get("role_profile")
    operation_id = operation.get("operation_id")
    argument = operation.get("argument")
    if (role != "empirica:empirica-auditor" or not isinstance(operation_id, str)
            or not operation_id.startswith("sha256:") or len(operation_id) != 71
            or not isinstance(argument, dict)):
        return None
    return AuditLaunchPlan(
        CLAUDE_PROFILE_ID, handle, child_id, role, argument, operation_id)


def _reserved_plan(handle: str, result: Mapping[str, object]) -> AuditLaunchPlan | None:
    run = result.get("run", {})
    children = run.get("children", []) if isinstance(run, Mapping) else []
    reserved = [child for child in children if isinstance(child, Mapping)
                and child.get("resource_class") == "audit" and child.get("state") == "reserved"]
    if len(reserved) != 1:
        return None
    child_id = reserved[0].get("child_id")
    if not isinstance(child_id, str):
        return None
    return _durable_plan(handle, child_id)


def agent_failure_main() -> int:
    """PostToolUseFailure reconciles an admitted auditor that never reached SubagentStart."""
    payload = _payload()
    try:
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, Mapping):
            return 0
        _, role = _agent_identity(tool_input)
        if role != "empirica:empirica-auditor":
            return 0
        handle, result = _resolve(payload)
        if handle is None or not isinstance(result, Mapping):
            return 0
        plan = _reserved_plan(handle, result)
        if plan is not None:
            AuditProtocol(CLAUDE_PROFILE_ID).reject(plan)
    except Exception:
        return 0
    return 0


def subagent_start_main() -> int:
    """SubagentStart binds the exact native agent id to one reserved audit operation."""
    payload = _payload()
    try:
        if payload.get("agent_type") != "empirica:empirica-auditor":
            return 0
        native_id = payload.get("agent_id")
        handle, result = _resolve(payload)
        if handle is None or not isinstance(result, Mapping) or not isinstance(native_id, str):
            return 0
        plan = _reserved_plan(handle, result)
        if plan is None:
            return 0
        AuditProtocol(CLAUDE_PROFILE_ID).observe_started(plan, native_id)
    except Exception:  # observational hook; Stop still fails closed on an open audit
        return 0
    return 0


def subagent_stop_main() -> int:
    """SubagentStop admits only the verdict from its exact bound native execution."""
    payload = _payload()
    try:
        if payload.get("agent_type") != "empirica:empirica-auditor":
            return 0
        native_id = payload.get("agent_id")
        handle, result = _resolve(payload)
        if handle is None or not isinstance(result, Mapping) or not isinstance(native_id, str):
            return 0
        run = result.get("run", {})
        children = run.get("children", []) if isinstance(run, Mapping) else []
        child_id = application_bridge.trusted_resolve_child(
            CLAUDE_PROFILE_ID, handle, native_id)
        child = next((item for item in children if isinstance(item, Mapping)
                      and item.get("child_id") == child_id
                      and item.get("resource_class") == "audit"
                      and item.get("state") == "pending"), None)
        plan = _durable_plan(handle, str(child_id)) if child_id else None
        if child is None or plan is None:
            return 0
        child_path = payload.get("agent_transcript_path")
        auditor_model, transcript_final = _transcript_observation(child_path)
        author_model, _ = _transcript_observation(payload.get("transcript_path"))
        handbacks = _transcript_handbacks(child_path)
        if handbacks:
            # A handback is authoritative. Duplicate or malformed handbacks fail
            # closed rather than falling back to later assistant prose.
            verdict = verdict_from_final_output(handbacks[0]) if len(handbacks) == 1 else None
        else:
            output = payload.get("last_assistant_message") or transcript_final
            verdict = verdict_from_final_output(output)
        protocol = AuditProtocol(CLAUDE_PROFILE_ID)
        if verdict is None:
            protocol.observe_failure(plan, native_id, "failed")
            print("empirica: auditor returned no valid verdict; audit failed.", file=sys.stderr)
            return 0
        protocol.observe_identities(
            plan, native_id,
            author=IdentityObservation(
                "anthropic" if author_model else None, author_model, "host",
                "claude-parent-transcript"),
            auditor=IdentityObservation(
                "anthropic" if auditor_model else None, auditor_model, "host",
                "claude-child-transcript"),
        )
        if not protocol.observe_verdict(plan, native_id, verdict):
            print("empirica: auditor verdict rejected; audit failed.", file=sys.stderr)
    except Exception:  # noqa: BLE001 - SubagentStop is observational to the host
        return 0
    return 0
