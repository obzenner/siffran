"""Claude Stop translation: host mechanics only; application/core own convergence."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from .correlation import PROTOCOL, request_id as new_request_id
from .fail_direction import FailureDirection, blocks_on_failure
from .route import observed_at
from .selector import context_from_payload
from .transport import BridgeTransport, Transport

REPORT_CONVERGENCE = "report_convergence"


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
    """Translate Claude ``Stop`` to the authoritative convergence gate."""
    context_from_payload(payload)
    command: dict = {
        "type": "EvaluateRun",
        "run_id": _handle(run_id),
        "intent": REPORT_CONVERGENCE,
    }
    stamp = observed_at(payload)
    if stamp is not None:
        command["observed_at"] = stamp
    return {
        "protocol": PROTOCOL,
        "request_id": correlation_id or new_request_id(payload, "stop"),
        "command": command,
    }


def dispatch_stop(
    payload: Mapping[str, object], run_id: str, *, transport: Transport | None = None,
    correlation_id: str | None = None,
) -> dict:
    request = build_stop_request(payload, run_id, correlation_id=correlation_id)
    return (transport if transport is not None else BridgeTransport()).dispatch(request)


def _json_line(result: dict) -> str:
    """Stable compact output; one line exactly, matching a hook process' stdout discipline."""
    return json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"


def _async_audit_wait(result: Mapping[str, object]) -> bool:
    reasons, run = result.get("reasons"), result.get("run")
    children = run.get("children") if isinstance(run, Mapping) else None
    return (isinstance(reasons, list) and len(reasons) == 1
        and isinstance(reasons[0], Mapping) and reasons[0].get("code") == "audit.pending"
        and isinstance(run, Mapping) and run.get("status") == "active"
        and isinstance(children, list) and sum(isinstance(c, Mapping)
            and c.get("resource_class") == "audit" and c.get("state") == "pending"
            and bool(c.get("child_id")) for c in children) == 1)


def _human_approval_wait(result: Mapping[str, object]) -> bool:
    reasons, run = result.get("reasons"), result.get("run")
    g = run.get("governance") if isinstance(run, Mapping) else None
    expected = {"pending": "governance.approval_required", "rejected": "governance.approval_required",
                "revision_pending": "governance.revision_required"}
    return (isinstance(g, Mapping) and run.get("status") == "active"
        and g.get("state") in expected and g.get("control_mode") == "deliberative"
        and isinstance(g.get("context"), Mapping) and g["context"].get("ingress") == "mcp_elicitation"
        and isinstance(reasons, list) and len(reasons) == 1 and isinstance(reasons[0], Mapping)
        and reasons[0].get("code") == expected[g["state"]])


def stop_result(response: object) -> StopResult:
    """Settle human/async waits nonterminally; never convert their service Block to convergence."""
    if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
        return StopResult(2, stderr="empirica completion gate returned a malformed response\n")
    result = response["result"]
    kind = result.get("type")
    if kind == "Inert":
        return StopResult(0)
    if kind == "Allow":
        return StopResult(0, stdout=_json_line(result))
    if kind == "Block":
        if _human_approval_wait(result):
            return StopResult(0, stdout=_json_line({"systemMessage":
                "Empirica is paused for human governance input, not converged. Investigation remains blocked; no approval was granted by this pause."}))
        if _async_audit_wait(result):
            return StopResult(0, stdout=_json_line(result))
        reasons = result.get("reasons")
        messages = [row.get("message") or row.get("code") for row in reasons
                    if isinstance(row, dict)] if isinstance(reasons, list) else []
        text = "\n".join(value for value in messages if isinstance(value, str) and value)
        return StopResult(2, stderr=(text or "empirica run is not complete") + "\n")
    if kind == "Fault":
        message = result.get("message")
        text = message if isinstance(message, str) and message else "empirica completion gate fault"
        if blocks_on_failure(response, fallback=FailureDirection.CLOSED):
            return StopResult(2, stderr=text + "\n")
        return StopResult(0, stderr=text + "\n")
    return StopResult(2, stderr="empirica completion gate returned an unknown result\n")
