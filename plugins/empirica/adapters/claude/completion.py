"""Claude Stop translation: host mechanics only; application/core own convergence."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from .correlation import PROTOCOL, request_id as new_request_id
from .fail_direction import FailureDirection, blocks_on_failure
from .route import observed_at
from .selector import context_from_payload
from .transport import Response, Result, Transport, dispatch_with

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
) -> Response:
    request = build_stop_request(payload, run_id, correlation_id=correlation_id)
    return dispatch_with(transport, request)


def _hook_stdout(result: Result) -> str:
    """Render a Stop result as the author plain-text view (one trailing newline)."""
    from adapters.author_view import render_author_view
    return render_author_view(result.as_dict()) + "\n"


def _async_audit_wait(result: Result) -> bool:
    run = result.run
    return (len(result.reasons) == 1 and result.reasons[0].code == "audit.pending"
        and run is not None and run.status == "active"
        and sum(child.resource_class == "audit" and child.state == "pending"
                and bool(child.child_id) for child in run.children) == 1)


def _human_approval_wait(result: Result) -> bool:
    run = result.run
    governance = run.governance if run is not None else None
    expected = {"pending": "governance.approval_required", "rejected": "governance.approval_required",
                "revision_pending": "governance.revision_required"}
    state = governance.state if governance is not None else None
    context = governance.context if governance is not None else None
    return (run is not None and governance is not None and run.status == "active"
        and state in expected and governance.control_mode == "deliberative"
        and context is not None and context.ingress == "mcp_elicitation"
        and len(result.reasons) == 1 and result.reasons[0].code == expected[state])


def stop_result(response: object) -> StopResult:
    """Settle human/async waits nonterminally; never convert their service Block to convergence."""
    if not isinstance(response, Response):
        return StopResult(2, stderr="empirica completion gate returned a malformed response\n")
    result = response.result
    kind = result.type
    if kind == "Inert":
        return StopResult(0)
    if kind == "Allow":
        return StopResult(0, stdout=_hook_stdout(result))
    if kind == "Block":
        if _human_approval_wait(result):
            message = {
                "systemMessage": (
                    "Empirica is paused for human governance input, not converged. "
                    "Investigation remains blocked; no approval was granted by this pause."
                )
            }
            return StopResult(0, stdout=json.dumps(
                message, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
        if _async_audit_wait(result):
            return StopResult(0, stdout=_hook_stdout(result))
        messages = [row.message or row.code for row in result.reasons]
        text = "\n".join(value for value in messages if value)
        return StopResult(2, stderr=(text or "empirica run is not complete") + "\n")
    if kind == "Fault":
        text = result.message or "empirica completion gate fault"
        if blocks_on_failure(response, fallback=FailureDirection.CLOSED):
            return StopResult(2, stderr=text + "\n")
        return StopResult(0, stderr=text + "\n")
    return StopResult(2, stderr="empirica completion gate returned an unknown result\n")
