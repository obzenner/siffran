"""Active Claude lifecycle entry points.

Each function translates one native hook event, dispatches an exact v2 request through the shared
bridge, and maps the typed result back to Claude's process contract. Located run identity,
child reservation, investigation admission, and audit lifecycle are application-owned.

Removed operations (``void_spawn``/``audit_ticket``/``consume``/``phase``) and author-submitted
trusted actions (``evidence_leaf``/``attribution``/``child_event``/``audit_verdict``) are never
dispatched and never fabricated into another v2 action: the adapter fails closed locally instead.
No run file, Git ref, or host runtime directory is read directly.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from adapters import bridge as application_bridge
from application import protocol as _protocol
from adapters.audit import child_prompt, verdict_from_final_output
from adapters.audit_protocol import (AuditLaunchPlan, AuditProtocol, AuditProtocolError,
                                     IdentityObservation)
from adapters.identity import REVIEWER_FAMILIES, claude_observation, family
from adapters.public_tools import EVIDENCE_ACTIONS, OBSERVE_TOOL
from .completion import dispatch_stop, stop_result
from .restore import dispatch_restore, restore_context
from .route import dispatch_investigation
from .run_start import dispatch_resolve, dispatch_start_run
from .invocation import parse_invocation
from .selector import SelectorError
from .spawn import dispatch_child_reserve, spawn_decision
from .transport import CLAUDE_PROFILE_ID, Result


@dataclass(frozen=True)
class HookPayload(Mapping[str, object]):
    """Validated top-level native hook payload."""

    values: Mapping[str, object]

    @classmethod
    def from_stdin(cls) -> "HookPayload":
        """Parse hook stdin once; malformed or non-object input becomes an empty payload."""
        try:
            value = json.loads(sys.stdin.read() or "{}")
        except (ValueError, TypeError):
            value = {}
        return cls(MappingProxyType(value if isinstance(value, dict) else {}))

    def __getitem__(self, key: str) -> object:
        return self.values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


# Untrusted transcript JSONL is checked only by these clearly named boundary parsers.
def _parse_transcript_messages(path: object) -> list[Mapping] | None:
    """Parse the JSONL transcript into its assistant messages; None on any read error.

    A single reader owns the transcript format so a format change is edited once.
    Rows without a ``message`` field are not messages and are skipped, but an explicitly
    present non-mapping ``message`` is a malformed transcript: the whole scan fails closed
    (returns None) so corruption can never be read as a valid transcript without it.
    """
    if not isinstance(path, str):
        return None
    messages: list[Mapping] = []
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                message = row.get("message", {}) if isinstance(row, Mapping) else {}
                if not isinstance(message, Mapping):
                    return None
                if message.get("role") == "assistant":
                    messages.append(message)
    except (OSError, ValueError, TypeError):
        return None
    return messages


def _served_model(message: Mapping) -> str | None:
    """The single served-model rule: a non-empty model string that is not the synthetic sentinel."""
    candidate = message.get("model")
    return candidate if (isinstance(candidate, str) and candidate
                         and candidate != "<synthetic>") else None


def _parse_current_assistant_model(path: object) -> str | None:
    """Return the chronological last assistant message's served model, if observable.

    A tool/verdict-bearing message without model evidence is an unobservable identity;
    it never inherits an older model.
    """
    messages = _parse_transcript_messages(path)
    return _served_model(messages[-1]) if messages else None


def _parse_transcript_contents(path: object) -> tuple[list[str], str | None]:
    """Return distinct served assistant models and the textual final message."""
    messages = _parse_transcript_messages(path)
    if messages is None:
        return [], None
    models: list[str] = []
    final = None
    for message in messages:
        model = _served_model(message)
        if model is not None and model not in models:
            models.append(model)
        content = message.get("content")
        if isinstance(content, str):
            final = content
        elif isinstance(content, list):
            text = [item.get("text") for item in content
                    if isinstance(item, Mapping) and item.get("type") == "text"
                    and isinstance(item.get("text"), str)]
            if text:
                final = "\n".join(text)
    return models, final


def _parse_transcript_all_text(path: object) -> str | None:
    """Concatenate every assistant text block; None on any read error.

    Used for the fallback verdict scan: the auditor may have written the verdict
    fence in assistant text rather than the handback message.
    """
    messages = _parse_transcript_messages(path)
    if messages is None:
        return None
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if (isinstance(item, Mapping) and item.get("type") == "text"
                        and isinstance(item.get("text"), str)):
                    parts.append(item["text"])
    return "\n".join(parts)


def _parse_transcript_handbacks(path: object) -> tuple[bool, list[str]]:
    """Return whether the transcript scan succeeded and all handback messages.

    A handback containing a valid verdict wins (see ``subagent_stop_main``).
    When no handback carries a verdict, the adapter falls back to scanning all
    assistant text plus handback messages for exactly one valid verdict fence.
    Unreadable or malformed transcripts are distinct from a valid transcript with
    no handback so corruption can never enable the fallback.
    """
    messages = _parse_transcript_messages(path)
    if messages is None:
        return False, []
    handbacks: list[str] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if (not isinstance(item, Mapping) or item.get("type") != "tool_use"
                    or item.get("name") != "SubagentHandback"):
                continue
            tool_input = item.get("input")
            candidate = tool_input.get("message") if isinstance(tool_input, Mapping) else None
            handbacks.append(candidate if isinstance(candidate, str) else "")
    return True, handbacks


def _payload() -> HookPayload:
    return HookPayload.from_stdin()


def _resolve(
    payload: Mapping[str, object], *, strict: bool = False,
) -> tuple[str | None, Result | None]:
    """Return ``(handle, result)`` using only ``ResolveRun`` through the shared bridge.

    Transport and dispatch exceptions always propagate; each caller owns the single handling layer.
    Observational hooks catch them in their outer handler (one stderr diagnostic, exit 0), and
    admission hooks pass ``strict`` and fail closed: only exact ``Inert/no_run`` proves that no cap
    exists, so faults and malformed no-handle responses are unavailable. Non-strict callers treat a
    genuine no-run result as silent.
    """
    response = dispatch_resolve(payload, correlation_id="claude-resolve")
    result = response.result
    run = result.run
    handle = run.id if run is not None else None
    if handle:
        return handle, result
    if strict and not (result.type == "Inert" and result.inert_reason == "no_run"):
        raise RuntimeError("run resolution unavailable")
    return None, result


def _deny(reason: str) -> int:
    print(reason, file=sys.stderr)
    return 2


def _reject_reservation(protocol: AuditProtocol | None, plan: AuditLaunchPlan | None) -> None:
    """Best-effort rollback of a retained reservation; failure must not change the deny."""
    if protocol is None or plan is None:
        return
    try:
        protocol.reject(plan)
    except Exception:  # noqa: BLE001 - rollback failure must not override the blocking deny
        pass


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


def _model_observation(raw, *, source="claude-transcript"):
    return claude_observation(raw, source=source)


def _main_model(payload: Mapping[str, object]) -> str | None:
    return _parse_current_assistant_model(payload.get("transcript_path"))


def _auditor_alias(payload: Mapping[str, object], environ: Mapping[str, str]) -> str | None:
    """Choose only a host-resolvable alias different from the observed main family."""
    configured = environ.get("CLAUDE_CODE_SUBAGENT_MODEL")
    if configured and configured != "inherit":
        return None
    main_model = _main_model(payload)
    main = family(main_model)
    if main is None:
        raise AuditProtocolError(
            "main model family is unobservable; set CLAUDE_CODE_SUBAGENT_MODEL or "
            "ANTHROPIC_DEFAULT_<FAMILY>_MODEL")
    third_party = any(environ.get(key) for key in (
        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"))
    for reviewer_family in REVIEWER_FAMILIES:
        if reviewer_family != main and (not third_party or environ.get(f"ANTHROPIC_DEFAULT_{reviewer_family.upper()}_MODEL")):
            return reviewer_family
    raise AuditProtocolError(
        "no distinct reviewer alias is resolvable; set CLAUDE_CODE_SUBAGENT_MODEL or pin "
        "ANTHROPIC_DEFAULT_FABLE_MODEL, ANTHROPIC_DEFAULT_OPUS_MODEL, "
        "ANTHROPIC_DEFAULT_SONNET_MODEL, or ANTHROPIC_DEFAULT_HAIKU_MODEL")


def _governance_context(payload, handle):
    model = _main_model(payload)
    # to_model is host-native PostModelSwitch input, never author tool content.
    if payload.get("hook_event_name") == "PostModelSwitch":
        model = payload.get("to_model")
    response = application_bridge.trusted_governance_context(CLAUDE_PROFILE_ID, handle, {
        "author": _model_observation(model, source="claude-main-transcript"),
        "ingress": _protocol.host_profile(CLAUDE_PROFILE_ID)["approval_ingress"]})
    if response.get("result", {}).get("type") not in {"Allow", "Inert"}:
        raise RuntimeError("governance context unavailable")


def _emit_start_block(reason: str) -> None:
    json.dump({"decision": "block", "reason": f"Empirica did not start: {reason}"}, sys.stdout)
    sys.stdout.write("\n")


def run_start_main() -> int:
    """UserPromptExpansion: activate and inject the opaque handle/public tool contract."""
    try:
        payload = _payload()
        invocation = parse_invocation(payload, environ=os.environ)
        if invocation.unknown_flags:
            _emit_start_block("unknown flags: " + " ".join(invocation.unknown_flags))
            return 0
        response = dispatch_start_run(payload)
        result = response.result
        run = result.run
        refusal = result.first_reason.message if result.first_reason is not None else None
        handle = run.id if run is not None else None
        fault = result.type == "Fault"
        if fault:
            _emit_start_block(result.code or "unknown failure")
        elif refusal is not None:
            _emit_start_block(refusal)
        elif handle:
            _governance_context(payload, handle)
            context = (
                f"Empirica v2 is active. Opaque run handle: {handle}. "
                "Use empirica_observe for public author actions, empirica_read for the "
                "current RunView/argument/contract, and report_convergence only after "
                "the bound audit is current. Never submit trusted ingress."
            )
            json.dump({"hookSpecificOutput": {"hookEventName": "UserPromptExpansion",
                                              "additionalContext": context}}, sys.stdout)
            sys.stdout.write("\n")
        else:
            _emit_start_block("internal error")
    except Exception:  # noqa: BLE001 - this event must never wedge prompt expansion
        _emit_start_block("internal error")
    return 0


def spawn_main() -> int:
    """PreToolUse:Agent: reserve a real launch via ``child_reserve``, else inert.

    A real launch with real ``purpose``/``role_profile``/``execution`` dispatches ``child_reserve``
    and denies unless the application admits it. Non-launch calls are inert; missing real inputs,
    malformed responses, closed faults, and adapter exceptions fail closed.
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
    plan = None
    audit_protocol = None
    try:
        _governance_context(payload, handle)
        investigation = dispatch_investigation(payload, handle)
        if investigation is not None:
            decision = spawn_decision(investigation)
            if decision.exit_code:
                return _deny(decision.reason or "empirica investigation denied")
        if not is_auditor:
            response = dispatch_child_reserve(
                payload, handle, purpose=reservation_purpose, role_profile=role_profile,
                execution="foreground", correlation_id="claude-child-reserve")
            decision = spawn_decision(response)
            return (_deny(decision.reason or "empirica spawn denied")
                    if decision.exit_code else 0)
        if "model" in tool_input:
            return _deny("empirica auditor launch forbids model overrides")
        alias = _auditor_alias(payload, os.environ)
        audit_protocol = AuditProtocol(CLAUDE_PROFILE_ID, execution="async")
        plan = audit_protocol.prepare(handle, role_profile="empirica:empirica-auditor")
        updated = {
            "subagent_type": "empirica:empirica-auditor",
            "description": "Bound Empirica audit",
            "prompt": child_prompt(plan.argument),
            "run_in_background": True,
            "max_turns": 8,
        }
        if alias is not None:
            updated["model"] = alias
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                          "updatedInput": updated}}, sys.stdout)
        sys.stdout.write("\n")
    except Exception as exc:  # noqa: BLE001 - every active-run launch failure must deny
        _reject_reservation(audit_protocol, plan)
        return _deny(f"empirica spawn denied: {exc}" if isinstance(exc, AuditProtocolError)
                     else "empirica spawn denied: adapter failure")
    return 0


def route_main() -> int:
    """PreToolUse: deny active-run investigation until the core admits its witness."""
    payload = _payload()
    tool_input = payload.get("tool_input")
    if payload.get("agent_id") and isinstance(tool_input, Mapping):
        tool_name = payload.get("tool_name")
        action = tool_input.get("action")
        kind = action.get("kind") if isinstance(action, Mapping) else None
        if (isinstance(tool_name, str) and tool_name.endswith(OBSERVE_TOOL)
                and kind in EVIDENCE_ACTIONS):
            return _deny(
                "empirica evidence admission denied: subagent producer is not the main author")
    try:
        handle, _ = _resolve(payload, strict=True)
        if handle is None:
            return 0
        _governance_context(payload, handle)
        response = dispatch_investigation(payload, handle)
        if response is None:
            return 0
        decision = spawn_decision(response)
        if decision.exit_code:
            return _deny(decision.reason or "empirica investigation denied")
    except Exception:  # noqa: BLE001 - active-run investigation must fail closed
        return _deny("empirica investigation denied: adapter failure")
    return 0



def completion_main() -> int:
    """Stop: fail closed for an active/blocked run, silent when no run exists."""
    payload = _payload()
    try:
        handle, _ = _resolve(payload, strict=True)
        if handle is None:
            return 0  # exact no-active-run response → nothing to gate
        mapped = stop_result(dispatch_stop(payload, handle))
    except SelectorError:
        return 0  # malformed non-session Stop events retain observational behavior
    except Exception as exc:  # noqa: BLE001 - completion is the fail-closed boundary
        print(f"empirica completion gate unavailable: {exc}", file=sys.stderr)
        return 2
    if mapped.stdout:
        sys.stdout.write(mapped.stdout)
    if mapped.stderr:
        sys.stderr.write(mapped.stderr)
    return mapped.exit_code


def observational_diagnostic(hook: str, exc: BaseException) -> str:
    """One stderr line naming the hook, exception class, and whitespace-normalized message (no traceback; the hook payload is not serialized)."""
    message = " ".join(str(exc).split())
    return f"empirica {hook} hook failed: {type(exc).__name__}: {message}"


def _observational_failure(hook: str, exc: BaseException) -> int:
    """Report one failure of a host-observational hook on stderr and exit 0.

    These hooks never gate the host; the Stop hook still fails closed on whatever they left open.
    """
    print(observational_diagnostic(hook, exc), file=sys.stderr)
    return 0


def restore_main() -> int:
    """SessionStart:compact: bounded restore context; exit 0, failure diagnostic on stderr."""
    payload = _payload()
    try:
        handle, _ = _resolve(payload)
        if handle is not None:
            AuditProtocol(CLAUDE_PROFILE_ID).reconcile_orphans(handle, native_prefix="claude-session-restore", include_pending=False)
            if context := restore_context(dispatch_restore(payload, handle)):
                print(context)
    except Exception as exc:  # noqa: BLE001 - restore never wedges session start
        return _observational_failure("SessionStart:compact restore", exc)
    return 0


def _parse_durable_plan(handle: str, child_id: str) -> AuditLaunchPlan | None:
    operation = application_bridge.trusted_audit_plan(CLAUDE_PROFILE_ID, handle, child_id)
    if not isinstance(operation, Mapping):
        return None
    role = operation.get("role_profile")
    operation_id = operation.get("operation_id")
    argument = operation.get("argument")
    if (role != "empirica:empirica-auditor" or not isinstance(operation_id, str)
            or not operation_id.startswith("sha256:") or len(operation_id) != 71
            or not isinstance(argument, Mapping)):
        return None
    return AuditLaunchPlan(
        CLAUDE_PROFILE_ID, handle, child_id, role, argument, operation_id)


def _reserved_plan(handle: str, result: Result) -> AuditLaunchPlan | None:
    run = result.run
    children = run.children if run is not None else ()
    reserved = [child for child in children
                if child.resource_class == "audit" and child.state == "reserved"]
    if len(reserved) != 1:
        return None
    return _parse_durable_plan(handle, reserved[0].child_id)


def agent_failure_main() -> int:
    """PostToolUseFailure reconciles an admitted auditor that never reached SubagentStart.

    Exit 0 always; a failure writes one diagnostic line to stderr.
    """
    payload = _payload()
    try:
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, Mapping):
            return 0
        _, role = _agent_identity(tool_input)
        if role != "empirica:empirica-auditor":
            return 0
        handle, result = _resolve(payload)
        if handle is None or result is None:
            return 0
        plan = _reserved_plan(handle, result)
        if plan is not None:
            AuditProtocol(CLAUDE_PROFILE_ID).reject(plan)
    except Exception as exc:  # noqa: BLE001 - observational; Stop still fails closed on an open audit
        return _observational_failure("PostToolUseFailure", exc)
    return 0


def subagent_start_main() -> int:
    """SubagentStart binds the exact native agent id to one reserved audit operation.

    Exit 0 always; a failure writes one diagnostic line to stderr.
    """
    payload = _payload()
    try:
        if payload.get("agent_type") != "empirica:empirica-auditor":
            return 0
        native_id = payload.get("agent_id")
        handle, result = _resolve(payload)
        if handle is None or result is None or not isinstance(native_id, str):
            return 0
        plan = _reserved_plan(handle, result)
        if plan is None:
            return 0
        AuditProtocol(CLAUDE_PROFILE_ID).observe_started(plan, native_id)
    except Exception as exc:  # noqa: BLE001 - observational; Stop still fails closed on an open audit
        return _observational_failure("SubagentStart", exc)
    return 0


def subagent_stop_main() -> int:
    """SubagentStop admits only the verdict from its exact bound native execution.

    Exit 0 always; a failure writes one diagnostic line to stderr.
    """
    payload = _payload()
    try:
        if payload.get("agent_type") != "empirica:empirica-auditor":
            return 0
        native_id = payload.get("agent_id")
        handle, result = _resolve(payload)
        if handle is None or result is None or not isinstance(native_id, str):
            return 0
        run = result.run
        children = run.children if run is not None else ()
        child_id = application_bridge.trusted_resolve_child(
            CLAUDE_PROFILE_ID, handle, native_id)
        child = next((item for item in children
                      if item.child_id == child_id
                      and item.resource_class == "audit"
                      and item.state == "pending"), None)
        plan = _parse_durable_plan(handle, str(child_id)) if child_id else None
        if child is None or plan is None:
            return 0
        child_path = payload.get("agent_transcript_path")
        auditor_models, _ = _parse_transcript_contents(child_path)
        auditor_model = auditor_models[0] if len(auditor_models) == 1 else None
        handback_scan_ok, handbacks = _parse_transcript_handbacks(child_path)
        if not handback_scan_ok:
            verdict = None
        else:
            # A handback containing a valid verdict wins. Multiple valid handback
            # verdicts fail closed (two different verdicts).
            handback_verdicts = [v for v in
                (verdict_from_final_output(hb) for hb in handbacks) if v is not None]
            if handback_verdicts:
                verdict = handback_verdicts[0] if len(handback_verdicts) == 1 else None
            else:
                # No handback verdict: accept the transcript only if it contains
                # exactly one valid verdict fence across all assistant text and
                # handbacks. Two fences, a malformed fence, or none → fail closed.
                all_text = _parse_transcript_all_text(child_path)
                if all_text is None:
                    verdict = None
                else:
                    combined = "\n".join([all_text, *handbacks]) if handbacks else all_text
                    verdict = verdict_from_final_output(combined)
        protocol = AuditProtocol(CLAUDE_PROFILE_ID)
        if verdict is None or auditor_model is None:
            protocol.observe_failure(plan, native_id, "failed")
            reason = "no valid verdict" if verdict is None else "served model is unobservable or mixed"
            print(f"empirica: auditor {reason}; audit failed.", file=sys.stderr)
            return 0
        protocol.observe_reviewer(
            plan, native_id,
            auditor=IdentityObservation(
                _model_observation(auditor_model)["provider_id"], auditor_model,
                "claude-subagent-transcript"),
        )
        if not protocol.observe_verdict(plan, native_id, verdict):
            print("empirica: auditor verdict rejected; audit failed.", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - SubagentStop is observational to the host
        return _observational_failure("SubagentStop", exc)
    return 0
