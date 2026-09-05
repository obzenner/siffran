"""Inactive Claude ``PreToolUse:Agent`` translation to ``ReserveSpawn``.

The module registers no hook.  It only translates an intercepted Agent/Task payload into the
existing host-neutral ``ObserveAction(reserve_spawn)`` operation and maps its typed result back to
Claude's exit-code convention.  The application service remains the sole owner of the cap and its
atomic reservation.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .correlation import request_id as new_request_id
from .fail_direction import FailureDirection, blocks_on_failure
from .route import observed_at
from .selector import context_from_payload
from .transport import BridgeTransport, Transport

PROTOCOL = "empirica/v1"
_AGENT_TOOLS = frozenset({"Agent", "Task"})


@dataclass(frozen=True)
class SpawnDecision:
    """Claude-native outcome for a PreToolUse gate."""

    exit_code: int
    reason: str = ""


def _handle(run_id: object) -> str:
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty application run handle")
    return run_id


def build_reserve_spawn_request(
    payload: Mapping[str, object], run_id: str, *, correlation_id: str | None = None,
) -> dict | None:
    """Build ``ObserveAction(reserve_spawn)`` for Agent/Task, or ``None`` for another tool."""
    context_from_payload(payload)
    if payload.get("tool_name") not in _AGENT_TOOLS:
        return None
    command: dict = {
        "type": "ObserveAction",
        "run_id": _handle(run_id),
        "action": {"kind": "reserve_spawn"},
    }
    stamp = observed_at(payload)
    if stamp is not None:
        command["observed_at"] = stamp
    return {
        "protocol": PROTOCOL,
        "request_id": correlation_id or new_request_id(payload, "reserve-spawn"),
        "command": command,
    }


def dispatch_reserve_spawn(
    payload: Mapping[str, object], run_id: str, *, transport: Transport | None = None,
    correlation_id: str | None = None,
) -> dict | None:
    """Dispatch a reservation through the shared bridge; non-Agent events remain inert."""
    context = context_from_payload(payload)
    request = build_reserve_spawn_request(payload, run_id, correlation_id=correlation_id)
    if request is None:
        return None
    target = transport if transport is not None else BridgeTransport(context.cwd)
    return target.dispatch(request)


def spawn_decision(response: object) -> SpawnDecision:
    """Map a typed reservation response to Claude's allow(0)/deny(2) convention.

    Block is the application's explicit cap denial.  Faults obey their typed direction; malformed
    or unavailable adapter responses use the PreToolUse gate's fail-open fallback.  Consequently a
    terminal run's application ``Allow`` is open, while a corrupt run's closed ``Fault`` denies.
    """
    if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
        return SpawnDecision(0)
    result = response["result"]
    if result.get("type") == "Block":
        reason = result.get("reason")
        return SpawnDecision(2, reason if isinstance(reason, str) else "spawn denied")
    if result.get("type") == "Fault" and blocks_on_failure(
        response, fallback=FailureDirection.OPEN,
    ):
        message = result.get("message")
        return SpawnDecision(2, message if isinstance(message, str) else "spawn gate failed closed")
    return SpawnDecision(0)
