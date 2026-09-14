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

import hashlib
import json
import sys
from collections.abc import Mapping

from adapters import bridge as application_bridge
from .audit import child_prompt, verdict_from_final_output
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


def _resolve(payload: Mapping[str, object]) -> tuple[str | None, dict | None]:
    """Return ``(handle, result)`` using only ``ResolveRun`` through the shared bridge.

    At D6 the no-location run port reports unresolved, so no handle is returned.  Any transport
    failure is treated as no resolvable run rather than wedging the host event.
    """
    try:
        response = dispatch_resolve(payload, correlation_id="claude-resolve")
    except Exception:  # noqa: BLE001 - never wedge a host event on transport failure
        return None, None
    result = response.get("result") if isinstance(response, dict) else None
    run = result.get("run") if isinstance(result, dict) else None
    handle = run.get("id") if isinstance(run, dict) else None
    return (handle if isinstance(handle, str) and handle else None,
            result if isinstance(result, dict) else None)


def _deny(reason: str, result: Mapping[str, object] | None = None) -> int:
    print(reason, file=sys.stderr)
    if result is not None:
        run = result.get("run") if isinstance(result, Mapping) else None
        contract = run.get("contract") if isinstance(run, Mapping) else None
        if isinstance(contract, Mapping):
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
    """UserPromptExpansion: best-effort activation, always silent and fail open."""
    payload = _payload()
    try:
        dispatch_start_run(payload)
    except Exception:  # noqa: BLE001 - this event must never wedge prompt expansion
        pass
    return 0


def _event(state: str, native_id: str, result_digest: str | None = None) -> dict:
    raw = {"state": state, "native_id": native_id, "result_digest": result_digest}
    fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**raw, "fingerprint": fingerprint}


def _argument(handle: str) -> dict | None:
    response = application_bridge.handle({"protocol": "empirica/v2",
        "request_id": "claude-audit-argument",
        "command": {"type": "GetArgument", "run_id": handle}}, CLAUDE_PROFILE_ID)
    result = response.get("result", {})
    value = result.get("argument") if result.get("type") == "Allow" else None
    return value if isinstance(value, dict) else None


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
    is_auditor = "empirica-auditor" in role_profile.lower()
    reservation_purpose = "audit" if is_auditor else purpose
    handle, _ = _resolve(payload)
    if handle is None:
        return 0  # no active run → no cap to enforce (D8 owns the spawn cap)
    try:
        response = dispatch_child_reserve(
            payload, handle, purpose=reservation_purpose, role_profile=role_profile, execution="foreground",
            correlation_id="claude-child-reserve",
        )
        decision = spawn_decision(response)
        if decision.exit_code:
            return _deny(decision.reason or "empirica spawn denied",
                         response.get("result") if isinstance(response, Mapping) else None)
        if is_auditor:
            result = response.get("result", {})
            run = result.get("run", {}) if isinstance(result, Mapping) else {}
            children = run.get("children", []) if isinstance(run, Mapping) else []
            child = next((c for c in reversed(children)
                          if c.get("purpose") == reservation_purpose and c.get("state") == "reserved"), None)
            argument = _argument(handle)
            if not isinstance(child, Mapping) or argument is None:
                return _deny("empirica auditor admission unavailable")
            child_id = child["child_id"]
            native_id = str(payload.get("tool_use_id") or child_id)
            application_bridge.trusted_child_event(
                CLAUDE_PROFILE_ID, handle, child_id, _event("launching", native_id))
            application_bridge.trusted_child_event(
                CLAUDE_PROFILE_ID, handle, child_id, _event("pending", native_id))
            evidence_ids = [a["artifact_id"] for a in argument.get("artifacts", [])
                            if a.get("kind") in {"research", "spike"}]
            model = payload.get("model") if isinstance(payload.get("model"), str) else None
            application_bridge.trusted_attribution(CLAUDE_PROFILE_ID, handle, {
                "subject_kind": "covered_actor", "subject_id": "claude-author",
                "child_id": None, "provider_id": "anthropic" if model else None,
                "model_id": model, "observed_by": "host",
                "covered_artifact_ids": evidence_ids})
            auditor_model = (tool_input.get("model")
                             if isinstance(tool_input.get("model"), str)
                             else "claude-opus-4-8")
            application_bridge.trusted_attribution(CLAUDE_PROFILE_ID, handle, {
                "subject_kind": "auditor", "subject_id": "claude-auditor",
                "child_id": child_id, "provider_id": "anthropic",
                "model_id": auditor_model, "observed_by": "configuration",
                "covered_artifact_ids": []})
            updated = dict(tool_input)
            updated["prompt"] = ((str(updated.get("prompt")) + "\n\n")
                                 if updated.get("prompt") else "") + child_prompt(argument)
            json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "updatedInput": updated}}, sys.stdout)
            sys.stdout.write("\n")
    except Exception:  # noqa: BLE001 - active-run launch failures deny below
        return _deny("empirica spawn denied: adapter failure")
    return 0


def route_main() -> int:
    """PreToolUse: investigative observation; best effort and non-blocking."""
    payload = _payload()
    try:
        handle, _ = _resolve(payload)
        if handle is not None:
            dispatch_investigation(payload, handle)
    except Exception:  # noqa: BLE001 - observational event never blocks tools
        pass
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
            context = restore_context(dispatch_restore(payload, handle))
            if context:
                print(context)
    except Exception:  # noqa: BLE001 - restore never wedges session start
        pass
    return 0


def _transcript_final(path: object) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    final = None
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                message = row.get("message", {}) if isinstance(row, dict) else {}
                if message.get("role") == "assistant":
                    content = message.get("content")
                    if isinstance(content, str):
                        final = content
    except (OSError, ValueError, TypeError):
        return None
    return final


def subagent_stop_main() -> int:
    """SubagentStop records a foreground auditor verdict through private application ingress."""
    payload = _payload()
    try:
        if "empirica-auditor" not in str(payload.get("agent_type", "")).lower():
            return 0
        handle, result = _resolve(payload)
        if handle is None or not isinstance(result, Mapping):
            return 0
        run = result.get("run", {})
        children = run.get("children", []) if isinstance(run, Mapping) else []
        child = next((c for c in reversed(children)
                      if c.get("purpose") == "audit" and c.get("state") == "pending"), None)
        output = payload.get("last_assistant_message") or _transcript_final(
            payload.get("agent_transcript_path") or payload.get("transcript_path"))
        verdict = verdict_from_final_output(output)
        if not isinstance(child, Mapping) or verdict is None:
            print("empirica: auditor returned no valid verdict; audit remains open.", file=sys.stderr)
            return 0
        response = application_bridge.trusted_audit_verdict(
            CLAUDE_PROFILE_ID, handle, child["child_id"], verdict)
        if response.get("result", {}).get("type") not in {"Allow", "Inert"}:
            print("empirica: auditor verdict rejected; audit remains open.", file=sys.stderr)
    except Exception:  # noqa: BLE001 - SubagentStop is observational to the host
        return 0
    return 0
