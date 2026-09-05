"""Inactive Claude ``Stop`` translation and exact native result mapping.

The adapter owns only host mechanics: ``Stop`` asks the application to
``EvaluateRun(report_convergence)`` and maps the typed result to Claude's documented
exit/stdout/stderr contract.  It registers no hook and reads no run files.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from .correlation import request_id as new_request_id
from .fail_direction import FailureDirection, blocks_on_failure
from .selector import context_from_payload
from .transport import BridgeTransport, Transport

PROTOCOL = "empirica/v1"


@dataclass(frozen=True)
class StopResult:
    """The complete process result an eventual ``Stop`` entry point must emit."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


def _handle(run_id: object) -> str:
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty application run handle")
    return run_id


def build_stop_request(
    payload: Mapping[str, object], run_id: str, *, correlation_id: str | None = None,
) -> dict:
    """Translate a Claude ``Stop`` payload to the one authoritative convergence gate."""
    context_from_payload(payload)
    return {
        "protocol": PROTOCOL,
        "request_id": correlation_id or new_request_id(payload, "stop"),
        "command": {
            "type": "EvaluateRun",
            "run_id": _handle(run_id),
            "intent": "report_convergence",
        },
    }


def dispatch_stop(
    payload: Mapping[str, object], run_id: str, *, transport: Transport | None = None,
    correlation_id: str | None = None,
) -> dict:
    context = context_from_payload(payload)
    request = build_stop_request(payload, run_id, correlation_id=correlation_id)
    return (transport if transport is not None else BridgeTransport(context.cwd)).dispatch(request)


def _json_line(result: dict) -> str:
    """Stable compact output; one line exactly, matching a hook process' stdout discipline."""
    return json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"


def stop_result(response: object) -> StopResult:
    """Map every typed wire result to Claude's Stop process contract.

    * ``Block`` and fail-closed ``Fault``: exit 2, reason/message on stderr, no stdout.
    * ``Allow``: exit 0 and the typed result on stdout (including honest cap termination,
      convergence, and already-terminal runs).
    * ``Inert``: silent exit 0; no active run exists to gate.
    * malformed/transport failures: fail closed for completion, with a deterministic diagnostic.

    Keeping the streams mutually exclusive matters: Claude treats exit-2 stderr as the blocking
    reason, while exit-0 stdout is context/report data.
    """
    if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
        return StopResult(2, stderr="empirica completion gate returned a malformed response\n")
    result = response["result"]
    kind = result.get("type")
    if kind == "Inert":
        return StopResult(0)
    if kind == "Allow":
        return StopResult(0, stdout=_json_line(result))
    if kind == "Block":
        reason = result.get("reason")
        text = reason if isinstance(reason, str) and reason else "empirica run is not complete"
        return StopResult(2, stderr=text + "\n")
    if kind == "Fault":
        message = result.get("message")
        text = message if isinstance(message, str) and message else "empirica completion gate fault"
        if blocks_on_failure(response, fallback=FailureDirection.CLOSED):
            return StopResult(2, stderr=text + "\n")
        return StopResult(0, stderr=text + "\n")
    return StopResult(2, stderr="empirica completion gate returned an unknown result\n")
