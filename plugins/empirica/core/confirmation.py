"""Which research still needs a confirmation, by whom, and what a human receipt decided (ADR-0067).

A ``verbatim`` record is verified by the service; a ``retrievable`` or ``observed`` one needs a
confirmation the service cannot give. Its *route* is derived, never stored:

* ``audit`` — a ``retrievable`` record whose retrieval kind the host's auditor is observed to cover
  (``invocation.audit_retrieval``); the audit gate confirms it.
* ``human`` — every other record, when a human can be shown a dialog now (``human_available``).
* ``unobserved`` — every other record, when the run could be asked (a human ingress, interactive or not known
  otherwise, not delegated) but the host has not yet observed a surface (it looks inside ``report_convergence``). The
  requirement stays open and the author is told a human is asked when it reports, if the host can show a dialog.
* ``unavailable`` — every other record, when nobody can; the requirement stays open.

A human is shown the pending set once per derivation: the host records an ``evidence_presentation``
(which spends one human interaction, counted by ``core.governance``), and the human's receipt of
decisions names that presentation. A human answers through ``evidence_confirmation`` artifacts: ``confirm``/``reject`` for a research
record, ``approve``/``reject`` for a ``needs-decision`` claim. The functions read a snapshot's
history and run state only (duck-typed), so they stay pure.
"""
from __future__ import annotations

from typing import Any

from . import citations, governance

KIND = "evidence_confirmation"
PRESENTATION = "evidence_presentation"
AUDIT, HUMAN, UNAVAILABLE, UNOBSERVED = "audit", "human", "unavailable", "unobserved"
CONFIRM, REJECT, APPROVE = "confirm", "reject", "approve"
VERIFIED_BY_SERVICE, CONFIRMED_BY_HUMAN, REJECTED_BY_HUMAN, PENDING = (
    "verified_by_service", "confirmed_by_human", "rejected_by_human", "pending")
#: The routes that wait on a human (the ones the end-of-pass gate holds the run for).
HUMAN_ROUTES = (HUMAN, UNAVAILABLE, UNOBSERVED)
#: What a host reports when it could not do the mediation a report asks of it (``EvaluateRun.host_failure``): it could
#: not record whether a human can be asked, or could not show the batch. A fact about one call, never stored.
HOST_FAILURES = ("surface_unrecorded", "presentation_failed")
#: The ``confirmation_batch.prompt_error`` values the core derives from the run itself (null when a dialog may be shown).
#: What a host reports about one call (``HOST_FAILURES``) is projected into that call's response only and never stored,
#: so no later reader of the run (the observer UI) sees it.
PROMPT_ERRORS = (UNAVAILABLE, UNOBSERVED, "already_presented", "interaction_limit")
#: The routes a *human* is told (the observer UI).
TOLD_ROUTES = (HUMAN, UNAVAILABLE)


def told_route(route: str) -> str:
    """The route as a human is told it: an unobserved surface reads as the ordinary pending question (``human``), because
    the host looks for the surface at the author's next report. The one place ``unobserved`` is told as ``human``."""
    return HUMAN if route == UNOBSERVED else route


def told_prompt_error(prompt_error: str | None) -> str | None:
    """``prompt_error`` as a human is told it: ``unobserved`` is no error (see ``told_route``)."""
    return None if prompt_error == UNOBSERVED else prompt_error


def audit_retrieval(snapshot: Any) -> frozenset[str]:
    """The retrieval kinds this run's auditor was observed to cover at ``StartRun``."""
    return frozenset(snapshot.state.invocation["audit_retrieval"])


def human_route(snapshot: Any) -> str:
    """Where everything that waits on a human goes, from the run and what its host has observed.

    ``unavailable`` for a profile without a human approval ingress, a delegated run and a non-interactive run, whatever
    the host observes (they have the ingress on paper and nobody to ask); otherwise the host's last observation of its
    surface (``invocation.human_surface``): ``human``, ``unavailable``, or ``unobserved`` while it has not looked."""
    context = snapshot.state.governance["context"]
    invocation = snapshot.state.invocation
    if (context["approval_capability"] != governance.HUMAN_CONFIGURATION
            or invocation["delegation"] or invocation["interactive"] is False):
        return UNAVAILABLE
    surface = invocation["human_surface"]
    return surface if surface in (HUMAN, UNOBSERVED) else UNAVAILABLE


def human_available(snapshot: Any) -> bool:
    """Whether a human can be shown a dialog now: the host has observed a surface (``human_route`` is ``human``)."""
    return human_route(snapshot) == HUMAN


def record_route(snapshot: Any, record: Any) -> str | None:
    """The route a research record's confirmation takes, or ``None`` when the service verified it."""
    record_class = citations.verification_class(record["source_kind"], record["source_ref"])
    if record_class == citations.VERBATIM:
        return None
    if (record_class == citations.RETRIEVABLE
            and citations.retrieval_kind(record["source_ref"]) in audit_retrieval(snapshot)):
        return AUDIT
    return human_route(snapshot)


def confirmation(snapshot: Any, **subject: Any) -> dict[str, Any] | None:
    """The latest human confirmation whose fields include ``subject``, else ``None``."""
    return next((item for item in reversed(snapshot.history)
                 if item.get("kind") == KIND
                 and all(item.get(key) == value for key, value in subject.items())), None)


def receipt(snapshot: Any, record: Any) -> dict[str, Any] | None:
    """The human receipt that decided a research record, else ``None``."""
    return confirmation(snapshot, subject="research", record_id=record["artifact_id"])


def decision(snapshot: Any, claim_id: str, digest: str) -> dict[str, Any] | None:
    """The human's answer to a ``needs-decision`` claim as currently worded, else ``None``."""
    return confirmation(snapshot, subject="claim", claim_id=claim_id, claim_digest=digest)


def rejected(snapshot: Any, record: Any) -> bool:
    """Whether a human rejected the record, which makes it inactive."""
    decided = receipt(snapshot, record)
    return decided is not None and decided["outcome"] == REJECT


def status(snapshot: Any, record: Any) -> str:
    """The record's verification status as the dossier states it."""
    if record_route(snapshot, record) is None:
        return VERIFIED_BY_SERVICE
    decided = receipt(snapshot, record)
    if decided is None:
        return PENDING
    return CONFIRMED_BY_HUMAN if decided["outcome"] == CONFIRM else REJECTED_BY_HUMAN


def pending_route(snapshot: Any, record: Any) -> str | None:
    """The route of a record that still needs confirming, else ``None``."""
    route = record_route(snapshot, record)
    return route if route is not None and receipt(snapshot, record) is None else None


def counts(snapshot: Any, record: Any) -> bool:
    """Whether the record may support approval: nothing waits on a human for it."""
    return pending_route(snapshot, record) not in HUMAN_ROUTES


def retrieve(snapshot: Any, record: Any) -> bool:
    """Whether the auditor must retrieve the record's source (its kind is observed as covered)."""
    return citations.retrieval_kind(record["source_ref"]) in audit_retrieval(snapshot)


def parameters(snapshot: Any, record_ids: list[str], claim_ids: list[str]) -> dict[str, Any]:
    """The ``evidence.confirmation_required`` parameters: the records waiting on a human (every one of
    them takes the same route) and the ``needs-decision`` claims the same batch asks about, decisions
    and records together being one batch for one human."""
    return {"route": human_route(snapshot), "record_ids": record_ids,
            **({"claim_ids": claim_ids} if claim_ids else {})}


def presentation(snapshot: Any, receipt_id: str) -> dict[str, Any] | None:
    """The recorded presentation a receipt answers, else ``None``."""
    return next((item for item in snapshot.history
                 if item.get("kind") == PRESENTATION and item["receipt_id"] == receipt_id), None)


def interactions(snapshot: Any) -> int:
    """How many human interactions evidence presentations spent (``core.governance`` adds them up)."""
    return sum(item.get("kind") == PRESENTATION for item in snapshot.history)


def presented(snapshot: Any, key: str) -> bool:
    """Whether the batch with this key was already shown to a human (each key is shown once)."""
    return any(item.get("kind") == PRESENTATION and item["key"] == key for item in snapshot.history)


def prompt_error(snapshot: Any, key: str) -> str | None:
    """Why the batch with this key cannot be presented now, else ``None``. In order: ``unavailable`` (nobody to ask),
    ``already_presented`` (a changed derivation is the only thing that asks again), ``interaction_limit`` (the run's
    human interactions are spent; known, so it comes before anything the host has yet to find out), then what the host
    reports about *this* call: its ``host_failure`` (``surface_unrecorded`` or ``presentation_failed``: it could not
    record its surface or could not show the batch, so nobody was asked) and ``unobserved`` (it has not looked for a
    surface; it looks inside its next report). Mirrors ``governance.prompt_error``."""
    route = human_route(snapshot)
    if route == UNAVAILABLE:
        return route
    if presented(snapshot, key):
        return "already_presented"
    if governance.interactions_remaining(snapshot.state.governance, interactions(snapshot))["total"] == 0:
        return "interaction_limit"
    return apply_host_report(UNOBSERVED if route == UNOBSERVED else None, snapshot.command.get("host_failure"))


def apply_host_report(prompt_error: str | None, host_failure: str | None) -> str | None:
    """The ``prompt_error`` once the host's report of this call is applied: a failure replaces only what a report would
    still have asked about (nothing, or ``unobserved``); a refusal the core already knows stands. The one rule both
    ``prompt_error`` and the in-process projection of a failure (``projection.project_host_failure``) use. The host's
    report is one of ``HOST_FAILURES`` (the request schema's enum)."""
    return host_failure if host_failure and prompt_error in {None, UNOBSERVED} else prompt_error
