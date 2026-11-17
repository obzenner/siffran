"""Claude Stop translation: host mechanics only; application/core own convergence."""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from application.protocol import (
    BUDGET_EXHAUSTED_NOTICE, HUMAN_WAIT_NOTICE, PAUSED_FOR_EVIDENCE_NOTICE, PAUSED_FOR_HUMAN_NOTICE,
    UNATTENDED_CONTINUE_NOTICE, UNATTENDED_RESIDUAL_NOTICE, render_notice)
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
    #: Set only when the turn goes on in an auto chain: the count of turn ends on the run's basis to remember (``count_turn_end``).
    chain_count: int | None = None


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


#: The reasons an unapproved configuration blocks with (``governance.approval_required`` before the first approval or
#: after a rejection, ``governance.revision_required`` for a revision): the blocker the host may pause on.
_CONFIGURATION_REASONS = frozenset({"governance.approval_required", "governance.revision_required"})


def _human_approval_wait(result: Result) -> bool:
    """True only for the sole legitimate human-approval blocker of an active run.

    Whether the configuration waits on its human is the core's answer, published in the run view
    (``governance.human_wait``: ``core.governance.human_wait``, the predicate the observer reads too); the host adds only
    its own ingress (the dialog is Claude's MCP form) and that the configuration's blocker is the run's sole one. An exhausted,
    delegated, or post-approval auto run keeps blocking rather than pausing."""
    run = result.run
    if run is None or run.governance is None or run.status != "active":
        return False
    return (run.governance["human_wait"] is True
            and run.governance.context.ingress == "mcp_elicitation"
            and len(result.reasons) == 1 and result.reasons[0].code in _CONFIGURATION_REASONS)


def _evidence_report_directive(result: Result) -> bool:
    """True only when the human confirmation is the sole blocker, a human can be asked, and the batch has not been shown.

    What a turn end does with it is the turn-end table (``_turn_end_mode``): a deliberative run pauses with
    ``paused_for_evidence``. An auto run asks no one, so the core returns it no such reason and the table has no cell for
    it. The core returns ``evidence.confirmation_required`` only when nothing else blocks the run. Only a ``prompt_error`` in
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
    """True only for the sole ``budget.exhausted`` blocker of an active run (ADR-0063 exhaustion rule) that has no recovery
    left for the agent to take.

    Blocking the turn again cannot help a run whose recovery the core refused (a delegated auto run's raise) or
    whose recovery is the human's to decide (a deliberative run's). An auto run the core still offers ``budget.raise``
    (its human approves a raise) is not waiting: the agent proposes it, so the turn goes on. Mixed reasons and
    non-active runs keep blocking."""
    run = result.run
    if not (run is not None and run.status == "active" and len(result.reasons) == 1
            and result.reasons[0].code == "budget.exhausted"):
        return False
    offered = "budget.raise" in (result.reasons[0].get("next_actions") or ())
    return not (offered and run.governance is not None and run.governance["control_mode"] == "auto")


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


#: K: the K-th turn end of a chain on one observation basis ends the chain with the residual notice (K - 1 continuations).
#: The registry's ``turn_end.bounds`` defines it once; a test reads the registry against this constant, and against Pi's.
#: Claude has no T: the host's own cap of eight consecutive continuations bounds a chain's total.
CONTINUATION_K = 3


def _basis_file(payload: Mapping[str, object]) -> Path:
    session = "".join(ch for ch in str(payload.get("session_id", "")) if ch.isalnum() or ch in "-_")[:80] or "session"
    return Path(tempfile.gettempdir()) / f"empirica-stop-basis-{session}"


def _remembered(path: Path) -> tuple[str, int] | None:
    """The chain's record, ``(basis, count)``, as the adapter wrote it, else ``None``. A 7.0.x file held the bare basis, and
    anything unreadable (not JSON, the wrong shape, a count that is not a non-negative integer) is read the same way: as a
    changed basis with count 0."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    basis, count = value.get("basis"), value.get("count")
    if isinstance(basis, str) and type(count) is int and count >= 0:
        return basis, count
    return None


def _basis_of(result: Result) -> str | None:
    basis = result.run.get("basis") if result.run is not None else None
    return basis if isinstance(basis, str) else None


def count_turn_end(payload: Mapping[str, object], result: Result, *, stop_hook_active: bool) -> int:
    """How many turn ends of this chain the core's ``run.basis`` has now seen, this one included (1 on a first turn end and
    on a changed basis). A chain continues only while Claude sends ``stop_hook_active`` true, so any other Stop starts a new
    one and the record is not read. The adapter composes nothing: progress is the core's basis
    (``core.evaluation.progress_basis``), on every host. A view without a basis, or an unreadable record, reads as "changed"."""
    basis = _basis_of(result)
    previous = _remembered(_basis_file(payload)) if stop_hook_active and basis is not None else None
    return previous[1] + 1 if previous is not None and previous[0] == basis else 1


def remember_chain(payload: Mapping[str, object], result: Result | None, count: int | None) -> None:
    """Keep ``{basis, count}`` after a turn end that continued (``count`` is its ``StopResult.chain_count``); after any other
    outcome the chain has ended, so the record is removed. The write is atomic (a temporary file in the same directory, then a
    rename), so a reader never sees half a record. An unwritable record only means the next turn end reads "changed": the
    continuation is repeated and Claude's own cap bounds it."""
    path = _basis_file(payload)
    basis = _basis_of(result) if result is not None else None
    try:
        if count is None or basis is None:
            path.unlink(missing_ok=True)
            return
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False) as handle:
            handle.write(json.dumps({"basis": basis, "count": count}, sort_keys=True))
            staged = Path(handle.name)
        os.replace(staged, path)
    except OSError:
        return


def _settlement(notice: str) -> StopResult:
    """Exit 0 with a contract-owned ``systemMessage`` and no claim of convergence."""
    return StopResult(0, stdout=json.dumps(
        {"systemMessage": notice}, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")


def stop_result(response: object, *, stop_hook_active: bool, turn_count: int = 1) -> StopResult:
    """Translate a core evaluation to the Stop hook's process result; a settlement never claims convergence.

    The outcome is the cell of the turn-end table (ADR-0068, ``turn_end`` in ``host-boundaries.json``) for the run's control
    mode and state. A deliberative turn end pauses, first or later, so ``stop_hook_active`` has no role there; it only marks
    a *later* turn end of an auto chain. ``turn_count`` is this turn end's count on the run's basis (``count_turn_end()``): the
    K-th ends the chain with the residual notice, whoever acts."""
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
        if mode == "auto":
            reason = _reason_text(result)
            acts = result.run.get("turn_end_actor")
            if turn_count >= CONTINUATION_K or (acts == "none" and turn_count > 1):
                # the K-th turn end on one basis, or the none actor's second: the honest end, the one notice that offers the stop
                return _settlement(render_notice(UNATTENDED_RESIDUAL_NOTICE, reason=reason))
            if acts == "none":
                # The author has no step that approves the claims as they stand: the agent is handed the residual notice as the
                # directive (a settled turn would hand the run to nobody), to end honestly or revise the graph.
                return StopResult(2, stderr=render_notice(UNATTENDED_RESIDUAL_NOTICE, reason=reason) + "\n", chain_count=turn_count)
            if stop_hook_active:
                return StopResult(2, stderr=render_notice(UNATTENDED_CONTINUE_NOTICE, reason=reason) + "\n", chain_count=turn_count)
            return StopResult(2, stderr=reason + "\n", chain_count=turn_count)
        return StopResult(2, stderr=_reason_text(result) + "\n")
    if kind == "Fault":
        text = result.message or "empirica completion gate fault"
        if blocks_on_failure(response, fallback=FailureDirection.CLOSED):
            return StopResult(2, stderr=text + "\n")
        return StopResult(0, stderr=text + "\n")
    return StopResult(2, stderr="empirica completion gate returned an unknown result\n")
