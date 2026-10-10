"""Claude Stop translation: host mechanics only; application/core own convergence."""
from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from application.protocol import (
    BUDGET_EXHAUSTED_NOTICE, EVIDENCE_REPORT_NOTICE, HUMAN_WAIT_NOTICE, PAUSED_FOR_EVIDENCE_NOTICE, PAUSED_FOR_HUMAN_NOTICE,
    UNATTENDED_CONTINUE_NOTICE, render_notice)
from core.governance import expected_approval_kind
from core.projection import ASKABLE
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
    """True only for the single pending async audit of an active run: the turn settles and its result continues the run.
    The same rule is Pi's `auditPending`, case for case (`adapters/pi/test/fixtures/turn-end-predicates.json`); both require
    the child to have an id."""
    run = result.run
    return (len(result.reasons) == 1 and result.reasons[0].code == "audit.pending"
        and run is not None and run.status == "active"
        and sum(child.resource_class == "audit" and child.state == "pending"
                and bool(child.child_id) for child in run.children) == 1)


_HUMAN_WAIT_REASON = {"pending": "governance.approval_required",
                      "rejected": "governance.approval_required",
                      "revision_pending": "governance.revision_required"}


def _human_approval_wait(result: Result) -> bool:
    """True only for the sole legitimate human-approval blocker of an active run.

    The phase must call for a human decision (ADR-0063), the host dialog must be reachable, and a
    new presentation must still be admissible; an exhausted, delegated, or post-approval auto
    run keeps blocking rather than pausing."""
    run = result.run
    if run is None or run.governance is None or run.status != "active":
        return False
    governance = run.governance
    interactions = governance["interactions_remaining"]
    return (governance.state in _HUMAN_WAIT_REASON
            and expected_approval_kind(governance) == "host_ui"
            and governance.context.ingress == "mcp_elicitation"
            and governance["prompt_error"] is None
            and interactions["proposal"] > 0 and interactions["total"] > 0
            and len(result.reasons) == 1
            and result.reasons[0].code == _HUMAN_WAIT_REASON[governance.state])


def _evidence_report_directive(result: Result) -> bool:
    """True only when the human confirmation is the sole blocker, a human can be asked, and the batch has not been shown.

    What a turn end does with it is the turn-end table (``_turn_end_mode``). The core returns
    ``evidence.confirmation_required`` only when nothing else blocks the run. Only a ``prompt_error`` in
    ``projection.ASKABLE`` is directed (a human may be asked: no dialog is ruled out yet; the core gives such a batch a
    human route, so no route check is needed); any other value means no dialog will come, so the Stop blocks with the
    real reason."""
    run = result.run
    if run is None or run.status != "active" or len(result.reasons) != 1:
        return False
    reason, batch = result.reasons[0], run.get("confirmation_batch")
    return (reason.code == "evidence.confirmation_required"
            and batch is not None and batch["prompt_error"] in ASKABLE)


def _budget_exhausted_wait(result: Result) -> bool:
    """True only for the sole ``budget.exhausted`` blocker of an active run (ADR-0063 exhaustion rule).

    The core already refused the only recovery the Block once listed, so blocking the turn again
    cannot help; mixed reasons and non-active runs keep blocking."""
    run = result.run
    return (run is not None and run.status == "active" and len(result.reasons) == 1
            and result.reasons[0].code == "budget.exhausted")


def _turn_end_mode(result: Result) -> str | None:
    """How a turn end of an active run goes, read only from the run view the core returned.

    ``"pause"`` for deliberative, ``"auto"`` for every ``auto`` run (interactive before or after its first approval,
    delegated); ``None`` for an unknown view or a run that is not active. The turn-end table is owned by ADR-0068 and
    ``turn_end`` in ``host-boundaries.json``."""
    run = result.run
    if run is None or run.governance is None or run.status != "active":
        return None
    control_mode = run.governance["control_mode"]
    return {"deliberative": "pause", "auto": "auto"}.get(control_mode)


def _reason_text(result: Result) -> str:
    """The core's own reason text for every blocker, as one sentence ending in punctuation (every blocking Stop message)."""
    text = " ".join(" ".join(row.message or "" for row in result.reasons).split())
    text = text or "the run is not complete."
    return text if text[-1] in ".!?" else text + "."


def _basis_file(payload: Mapping[str, object]) -> Path:
    session = "".join(ch for ch in str(payload.get("session_id", "")) if ch.isalnum() or ch in "-_")[:80] or "session"
    return Path(tempfile.gettempdir()) / f"empirica-stop-basis-{session}"


def unchanged_basis(payload: Mapping[str, object], result: Result) -> bool:
    """Remember the core's ``run.basis`` for this session and say whether it is the one the previous Stop remembered. The
    adapter composes nothing: progress is the core's basis (``core.evaluation.progress_basis``), on every host. Unreadable or
    unwritable memory, or a view without a basis, reads as "changed": the continuation is repeated and Claude's own cap of
    eight bounds it."""
    basis = result.run.get("basis") if result.run is not None else None
    if not isinstance(basis, str):
        return False
    path = _basis_file(payload)
    try:
        previous = path.read_text(encoding="utf-8") if path.exists() else None
    except OSError:
        previous = None
    try:
        path.write_text(basis, encoding="utf-8")
    except OSError:
        return False
    return previous == basis


def _settlement(notice: str) -> StopResult:
    """Exit 0 with a contract-owned ``systemMessage`` and no claim of convergence."""
    return StopResult(0, stdout=json.dumps(
        {"systemMessage": notice}, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")


def stop_result(response: object, *, stop_hook_active: bool, no_progress: bool = False) -> StopResult:
    """Translate a core evaluation to the Stop hook's process result; a settlement never claims convergence.

    The outcome is the cell of the turn-end table (ADR-0068, ``turn_end`` in ``host-boundaries.json``) for the run's
    control mode and state. A deliberative turn end pauses, first or later, so ``stop_hook_active`` has no role there; it
    only marks a *later* turn end of an auto chain, which gets the way out (``unattended_continue``)."""
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
            return _settlement(HUMAN_WAIT_NOTICE)
        if _budget_exhausted_wait(result):
            return _settlement(render_notice(BUDGET_EXHAUSTED_NOTICE, resource=result.reasons[0]["parameters"]["resource"]))
        if _async_audit_wait(result):
            return StopResult(0, stdout=_hook_stdout(result))
        mode = _turn_end_mode(result)
        if mode == "pause":
            if _evidence_report_directive(result):
                return _settlement(PAUSED_FOR_EVIDENCE_NOTICE)
            return _settlement(render_notice(PAUSED_FOR_HUMAN_NOTICE, reason=_reason_text(result)))
        if mode == "auto" and stop_hook_active and no_progress:  # continued once already and the core's basis did not move
            return _settlement(render_notice(UNATTENDED_CONTINUE_NOTICE, reason=_reason_text(result)))
        if _evidence_report_directive(result):
            return StopResult(2, stderr=EVIDENCE_REPORT_NOTICE + "\n")
        if mode == "auto" and stop_hook_active:
            return StopResult(2, stderr=render_notice(UNATTENDED_CONTINUE_NOTICE, reason=_reason_text(result)) + "\n")
        return StopResult(2, stderr=_reason_text(result) + "\n")
    if kind == "Fault":
        text = result.message or "empirica completion gate fault"
        if blocks_on_failure(response, fallback=FailureDirection.CLOSED):
            return StopResult(2, stderr=text + "\n")
        return StopResult(0, stderr=text + "\n")
    return StopResult(2, stderr="empirica completion gate returned an unknown result\n")
