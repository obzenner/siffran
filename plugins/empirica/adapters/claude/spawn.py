"""Claude host translation for ordinary-child ``child_reserve`` operations.

Audit reservation bypasses this translator and is owned by ``AuditProtocol``. Ordinary native
Agent launches use it only when purpose, role profile, and execution are concrete host inputs;
otherwise it fails closed and never synthesizes a capability. The application service remains the
sole owner of the spawn cap and its atomic reservation (D8 owns admission).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .correlation import PROTOCOL, request_id as new_request_id
from .fail_direction import FailureDirection
from .route import observed_at
from .selector import context_from_payload
from .transport import BridgeTransport, Transport

_EXECUTIONS = frozenset({"foreground", "async"})


@dataclass(frozen=True)
class SpawnDecision:
    """Claude-native outcome for a PreToolUse gate."""

    exit_code: int
    reason: str = ""


def _handle(run_id: object) -> str:
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty application run handle")
    return run_id


def build_child_reserve_request(
    payload: Mapping[str, object], run_id: str, *,
    purpose: str, role_profile: str, execution: str,
    deadline: str | None = None, correlation_id: str | None = None,
) -> dict:
    """Build ``ObserveAction(child_reserve)`` from real host inputs.

    ``purpose``, ``role_profile`` and ``execution`` must all be real non-empty host inputs;
    ``execution`` must be a canonical execution mode.  A ``deadline``, when supplied, is a string
    or null.  No capability is synthesized.
    """
    context_from_payload(payload)
    if not isinstance(purpose, str) or not purpose.strip():
        raise ValueError("purpose must be a non-empty string")
    if not isinstance(role_profile, str) or not role_profile.strip():
        raise ValueError("role_profile must be a non-empty string")
    if not isinstance(execution, str) or execution not in _EXECUTIONS:
        raise ValueError(f"execution must be one of {sorted(_EXECUTIONS)}")
    if deadline is not None and not isinstance(deadline, str):
        raise ValueError("deadline must be a string or null")
    action: dict = {
        "kind": "child_reserve",
        "purpose": purpose,
        "role_profile": role_profile,
        "execution": execution,
    }
    if deadline is not None:
        action["deadline"] = deadline
    command: dict = {"type": "ObserveAction", "run_id": _handle(run_id), "action": action}
    stamp = observed_at(payload)
    if stamp is not None:
        command["observed_at"] = stamp
    return {
        "protocol": PROTOCOL,
        "request_id": correlation_id or new_request_id(payload, "child-reserve"),
        "command": command,
    }


def dispatch_child_reserve(
    payload: Mapping[str, object], run_id: str, *,
    purpose: str, role_profile: str, execution: str,
    deadline: str | None = None, transport: Transport | None = None,
    correlation_id: str | None = None,
) -> dict:
    request = build_child_reserve_request(
        payload, run_id, purpose=purpose, role_profile=role_profile, execution=execution,
        deadline=deadline, correlation_id=correlation_id,
    )
    return (transport if transport is not None else BridgeTransport()).dispatch(request)


def spawn_decision(response: object) -> SpawnDecision:
    """Map a typed reservation response to Claude's allow(0)/deny(2) convention.

    Block is the application's explicit cap denial.  Faults obey their typed direction; a closed
    ``Fault`` denies.  Malformed, non-object, and mismatched responses deny the native launch — a
    real reservation must never be silently admitted by a corrupt or absent result.  An open
    ``Fault`` remains open; a terminal run's application ``Allow`` is open.
    """
    if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
        return SpawnDecision(2, "spawn denied: malformed reservation response")
    result = response["result"]
    kind = result.get("type")
    if kind == "Block":
        reason = result.get("reason")
        if not isinstance(reason, str):
            reasons = result.get("reasons")
            first = reasons[0] if isinstance(reasons, list) and reasons else None
            if isinstance(first, dict):
                reason = first.get("message") or first.get("code")
        return SpawnDecision(2, reason if isinstance(reason, str) else "spawn denied")
    if kind == "Fault":
        if result.get("fail_direction") == FailureDirection.OPEN.value:
            return SpawnDecision(0)
        message = result.get("message")
        return SpawnDecision(2, message if isinstance(message, str) else "spawn gate failed closed")
    if kind == "Allow":
        return SpawnDecision(0)
    return SpawnDecision(2, "spawn denied: reservation was not admitted")
