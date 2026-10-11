"""Pure observer projection: one run's facts for the UI read model (``contracts/empirica-ui-view/v1``).

Every value is the plugin's own derivation, read and never re-decided: claim states, scope and blockers
(``core.evaluation``), the audit summary, residuals, obligations and freshness changes (``core.projection``'s run view),
reason wording (``core.confirmation.reason_text``, the one renderer the author view and the hosts use too) and
host-observed identities. This module adds only classifications the run view has no field for, each a pure function of
the same facts and each owned here (the UI reads them and decides nothing):

* ``evidence``: the strongest kind among a claim's active evidence (a spike, else research, else none);
* ``review``: the claim measured against the latest audit verdict, with the digest ``audit_binding`` binds;
* ``assurance``: whether an approved claim is settled: the gate accepts the audit for it (``evaluation.audit_acceptance``);
* ``decision.status``: answered, waiting, deferred out of scope, unanswered at the stop, or not asked;
* ``outstanding``, ``next`` and ``attention``: what the run still owes, who acts and where, shaped from the one
  projection of the report's ordering (``evaluation.report_requirements``) that the gate and the author view use; this
  module classifies nothing (and never infers liveness);
* ``steps``: the history with its subject, the human outcome and whether the claim it names still stands.

No I/O, no clock and no presentation: labels, colours and layout belong to the UI.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from . import citations, confirmation, governance
from .evaluation import (LATER, EvaluationSnapshot, Requirement, active_evidence, audit_acceptance,
                         audit_attributions, audit_passes, claim_blockers, claim_digest,
                         derive_claims_for_projection, effective_scope_ids, evidence_digest, identity_pair,
                         latest_audit_verdict,
                         pending_decisions, report_requirements)
from .projection import governance_facts, project_runview, spike_result

#: ``evidence`` values, strongest first, and the other vocabularies this module owns
#: (``contracts/empirica-ui-view/v1`` mirrors them; ``scripts/validate_ui_view.py`` holds it).
EVIDENCE_KINDS = ("spike", "research", "none")
REVIEW_STATES = ("passed", "failed", "stale", "changed", "unreviewed", "none")
ASSURANCE_STATES = ("assured", "awaiting_audit", "audit_failed", "not_approved")
DECISION_STATUSES = ("approved", "rejected", "waiting", "unanswered", "deferred", "not_asked")
ATTENTION_STATES = ("needs_you", "not_ended", "ended")
#: Who a pending item waits on, and where it will happen. ``next_report`` and ``explicit_report`` are a host dialog
#: (the first when the host asks by itself, the second only when the author reports explicitly); ``if_available``: a human
#: may be asked at the next report, if the host finds a dialog (it has not looked yet); ``unavailable`` and
#: ``limit`` mean nobody can be asked; ``next_turn`` is the agent's, ``in_progress`` a running audit, ``nobody`` an ended
#: run. An item that waits for the ones before it is ``LATER``, which only the obligations list uses.
ACTORS = ("human", "agent", "auditor")
PLACES = (*dict.fromkeys(confirmation.ASK_WHERE.values()), "next_turn", "in_progress", "nobody")
#: Where a human is certainly shown a question: the places of the ``prompt_error`` values under which a report presents the
#: batch (``REPRESENTABLE``), derived, not listed. An unknown availability (``if_available``, from ``ASKABLE``'s extra
#: ``unobserved``) is a different fact: nobody is told a question waits when the host may find no dialog.
HUMAN_REACHABLE = tuple(dict.fromkeys(confirmation.ASK_WHERE[e] for e in confirmation.REPRESENTABLE))
STEP_SUBJECTS = ("run", "claim", "record")
CLAIM_STANDING = ("current", "replaced", "gone")
#: Whether a spike still holds: its files unchanged, changed since (freshness), or replaced by a later spike.
#: History kinds that record how the run was kept, not what it came to: a page folds them away by default.
BOOKKEEPING_KINDS = ("observation_basis", "attribution")
SPIKE_STATES = ("current", "stale", "superseded")
#: The view's name for each budget ceiling ``governance.BUDGETS`` declares (``max_passes`` -> ``passes``).
BUDGET_NAMES = {ceiling: ceiling.removeprefix("max_") for ceiling in governance.BUDGETS}

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def reason_message(spec: Mapping[str, Any], parameters: Mapping[str, Any], *, ended: bool = False) -> str:
    """A reason's wording for the UI: ``confirmation.reason_text`` (the one renderer; ``ended`` for a run that has stopped),
    and a message the parameters cannot complete is a contract bug, never shown half-formatted."""
    text = confirmation.reason_text(spec, parameters, ended=ended)
    if _PLACEHOLDER.search(text):
        raise ValueError(f"reason parameters {sorted(parameters)} do not complete the message {text!r}")
    return text


def evidence_kind(snapshot: EvaluationSnapshot, claim: Mapping[str, Any]) -> str:
    kinds = {a["kind"] for a in active_evidence(snapshot, claim)}
    return next(kind for kind in EVIDENCE_KINDS if kind in kinds or kind == "none")


def review_state(snapshot: EvaluationSnapshot, claim: Mapping[str, Any],
                 verdict: Mapping[str, Any] | None) -> str:
    """The claim against the latest verdict. ``passed`` needs the verdict to still bind the whole
    argument (``audit_passes``); a pass that no longer binds it, for any reason, is ``stale``."""
    if verdict is None:
        return "none"
    reviewed = {c["claim_id"]: c["evidence_digest"] for c in verdict.get("reviewed_claims", [])}
    if claim["id"] not in reviewed:
        return "unreviewed"
    if reviewed[claim["id"]] != evidence_digest(snapshot, claim):
        return "changed"
    if verdict.get("verdict") == "fail":
        return "failed"
    return "passed" if audit_passes(snapshot, verdict) else "stale"


def assurance(state: str, review: str, accepted: bool) -> str:
    """Whether an approved claim is settled: it is approved, a current pass reviewed it, and the gate accepts the audit
    (``accepted``: ``evaluation.audit_acceptance`` found nothing to refuse: not pending, and independent unless the scope
    exempts it). Nothing here decides what the gate refuses; a claim that is not approved is not settled whatever the audit says."""
    if state != "approved":
        return "not_approved"
    if review == "failed":
        return "audit_failed"
    return "assured" if review == "passed" and accepted else "awaiting_audit"


def _step(n: int, item: Mapping[str, Any], standing: Mapping[str, str]) -> dict[str, Any]:
    """One history artifact: what it is about (the run, a claim, or one research record), the outcome it recorded (a
    human's too), whether it is the human's consent to end the run, and whether the claim it names still stands as
    worded (``standing`` maps each current claim id to its digest)."""
    kind = item.get("kind")
    terminal = kind == confirmation.KIND and item.get("terminal") is True
    claim = None if terminal else item.get("claim_id")
    subject = ("record" if kind == confirmation.KIND and item.get("subject") == "research"
               else "claim" if claim is not None else "run")
    outcome = (item.get("verdict") if kind == "audit_verdict"
               else item.get("outcome") if kind in {"research", "spike", confirmation.KIND} else None)
    digest = item.get("claim_digest")
    return {"n": n, "kind": kind, "subject": subject, "claim": claim, "outcome": outcome,
            "terminal": terminal, "bookkeeping": kind in BOOKKEEPING_KINDS, "command": item.get("command"),
            "standing": (None if claim is None else "gone" if claim not in standing
                         else "replaced" if digest is not None and digest != standing[claim] else "current")}


#: The reason families the observer shows a human as a claim's blocker or a run's residual, and what such a message
#: may be: one plain sentence (the presentation rules live in recovery.md and the settlement notices).
UI_REASON_FAMILIES = ("claim.", "evidence.", "audit.", "budget.", "freeze.", "child.")
UI_MESSAGE_LIMIT = 120
UI_MESSAGE_BANNED = ("derivation", "receipt", "fold")


def message_problems(code: str, spec: Mapping[str, Any]) -> list[str]:
    """Why a registry reason's wordings that reach the UI (its message and its ended wording; the observer passes no
    ``case``) are not plain enough, else ``[]``."""
    if not code.startswith(UI_REASON_FAMILIES):
        return []
    problems = []
    for name, message in (("message", spec["message"]), ("ended_message", spec.get("ended_message", ""))):
        if len(message) > UI_MESSAGE_LIMIT:
            problems.append(f"{code} {name}: {len(message)} characters, over {UI_MESSAGE_LIMIT}")
        problems += [f"{code} {name}: uses the jargon {w!r}" for w in UI_MESSAGE_BANNED if w in message.lower()]
    return problems


def _wait(route: str | None, prompt_error: str | None, active: bool, audit_pending: bool) -> dict[str, str] | None:
    """Who a pending confirmation waits on and where it will happen; ``None`` unless it is pending. ``prompt_error`` is
    the run view's ``confirmation_batch.prompt_error``, the one owner of whether a host will ask."""
    if route is None:
        return None
    who = "auditor" if route == confirmation.AUDIT else "human"
    if not active:
        return {"who": who, "where": "nobody"}
    if who == "auditor":
        return {"who": who, "where": "in_progress" if audit_pending else "next_turn"}
    return {"who": who, "where": confirmation.ask_where(prompt_error or confirmation.route_error(route))}


def _record(snapshot: EvaluationSnapshot, record: Mapping[str, Any], prompt_error: str | None,
            audit_pending: bool) -> dict[str, Any]:
    """One research record as the human sees it: its source and quote, how it is verified (its class and
    status), who it waits on and where (null unless its confirmation is pending), whether the
    auditor must retrieve it, and the author's reason no spike could establish it and the human's note."""
    decided = confirmation.receipt(snapshot, record)
    return {"source": record["source_ref"], "source_kind": record["source_kind"],
            "quote": record["citation"], "outcome": record["outcome"],
            "class": citations.verification_class(record["source_kind"], record["source_ref"]),
            "status": confirmation.status(snapshot, record),
            "wait": _wait(confirmation.pending_route(snapshot, record), prompt_error,
                          snapshot.state.status == "active", audit_pending),
            "retrieve": confirmation.retrieve(snapshot, record),
            "spike_unavailable": record.get("spike_unavailable"),
            "note": decided.get("note") if decided is not None else None}


def _decision(snapshot: EvaluationSnapshot, claim: Mapping[str, Any], gating: bool, waiting: bool,
              prompt_error: str | None) -> dict[str, Any] | None:
    """A ``needs-decision`` claim's standing: ``approved`` or ``rejected`` once the human answered; else ``deferred``
    when it is outside the run's scope (nobody is asked), ``waiting`` while the human is asked (``where``), ``unanswered``
    when the run ended with it open, and ``not_asked`` when it is in scope but nothing asks (it was refuted, say)."""
    if claim["kind"] != "needs-decision":
        return None
    answer = confirmation.decision(snapshot, claim["id"], claim_digest(claim))
    active = snapshot.state.status == "active"
    status = (("approved" if answer["outcome"] == confirmation.APPROVE else "rejected") if answer
              else "deferred" if not gating
              else ("waiting" if active else "unanswered") if waiting else "not_asked")
    return {"status": status, "outcome": answer["outcome"] if answer else None,
            "answer": answer.get("answer") if answer else None,
            "where": (confirmation.ask_where(prompt_error or confirmation.route_error(confirmation.human_route(snapshot)))
                      if status == "waiting" else None)}


def _claim_detail(snapshot: EvaluationSnapshot, claim: Mapping[str, Any], gating: bool,
                  blocker_message: str | None, waiting: bool, prompt_error: str | None,
                  audit_pending: bool, stale: set[str]) -> dict[str, Any]:
    active = {a["artifact_id"] for a in active_evidence(snapshot, claim)}
    digest = claim_digest(claim)
    return {"id": claim["id"], "blocker_message": blocker_message,
            "records": [_record(snapshot, a, prompt_error, audit_pending) for a in snapshot.history
                        if a.get("kind") == "research" and a.get("claim_id") == claim["id"]
                        and a.get("claim_digest") == digest],
            "decision": _decision(snapshot, claim, gating, waiting, prompt_error),
            "research": sum(a["kind"] == "research" and a.get("outcome") == "supporting"
                            for a in active_evidence(snapshot, claim)),
            "proof": [_spike(a, a["artifact_id"] in active, a["artifact_id"] in stale) for a in snapshot.history
                      if a.get("kind") == "spike" and a.get("claim_id") == claim["id"]
                      and a.get("claim_digest") == digest]}


def _spike(art: Mapping[str, Any], active: bool, stale: bool) -> dict[str, Any]:
    """One executed spike as the plugin's dossier states it (``projection.spike_result``), with whether it still holds: the
    freshness owner's verdict on the files it bound (``stale``), or that a later spike replaced it. The command's output
    is not retained, and the fact says so."""
    fact = spike_result(art, active)
    return {"command": fact["command"], "outcome": fact["outcome"], "exit_code": fact["exit_code"],
            "gate": fact["spike_gate"], "files": [{"path": f["path"], "sha256": f["sha256"]} for f in fact["file_bindings"]],
            "state": "superseded" if not active else "stale" if stale else "current", "output": "not_retained"}


def governance_view(value: Mapping[str, Any]) -> dict[str, Any]:
    """The configuration of the run, shaped from ``projection.governance_facts``: its mode and approval, the ceilings in
    force with what they have used, the ceilings proposed (only while a proposal waits on the human, else ``None``: a
    settled run shows no numbers nobody is asked about), and the human interactions left (``None`` when the history that
    counts them was not read)."""
    proposed = value["proposal"]["budgets"]
    waiting = value["state"] in {"pending", "revision_pending"}
    left = value["interactions_remaining"]
    return {"mode": value["control_mode"], "approval": value["state"],
            "proposal_pending": waiting,
            "ceilings": {name: {"used": value["budgets"][governance.CEILINGS[ceiling]], "max": value["budgets"][ceiling]}
                         for ceiling, name in BUDGET_NAMES.items()},
            "proposed": ({name: proposed[ceiling] for ceiling, name in BUDGET_NAMES.items()} if waiting else None),
            "interactions": None if left is None else {"left": left["total"], "of": governance.MAX_INTERACTIONS}}


def session_fact(invocation: Mapping[str, Any]) -> dict[str, Any]:
    """Where the session can be found again, and what its host recorded about it at StartRun: whether it is ``interactive``
    (null when the host did not say) and whether the run is ``delegated``; unknown (null label and locator) when the host
    recorded no session. Nothing here says whether the session is alive."""
    session = invocation.get("session")
    return {"host": invocation["host"], "label": session["label"] if session else None,
            "locator": session["locator"] if session else None,
            "interactive": invocation["interactive"], "delegated": invocation["delegation"]}


def _group(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Identical rows once: rows alike in ``keys`` merge, collecting their claims, in order of first appearance."""
    merged: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        entry = merged.setdefault(tuple(row[k] for k in keys), {**row, "claims": []})
        entry["claims"] += [c for c in row["claims"] if c not in entry["claims"]]
    return list(merged.values())


def outstanding(requirements: tuple[Requirement, ...], reasons: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What an active run still owes, shaped from ``evaluation.report_requirements`` (the gate's own ordering): each
    requirement with its wording (the gate's reason, or the obligation for the setup the run lacks), who acts and where.
    Identical rows are grouped. Empty when the run is not active."""
    rows = [{"need": r.reason or r.obligation, "claims": list(r.claims), "who": r.actor, "where": r.place,
             "message": reason_message(reasons[r.reason], r.parameters) if r.reason else r.required}
            for r in requirements]
    return _group(rows, ("need", "message", "who", "where"))


def attention(status: str, items: list[Mapping[str, Any]]) -> str:
    """``ended``; ``needs_you`` only when a human is owed a question the host will show (``HUMAN_REACHABLE``: at the next
    report, or at an explicit one), whatever the agent still owes first; the human is never told a question waits that nobody
    can show (unavailable, spent) or that nobody knows can be shown (``if_available``: the host has not looked). Otherwise ``not_ended``, which is all the core knows: liveness is never inferred."""
    if status != "active":
        return "ended"
    return "needs_you" if any(i["who"] == "human" and i["where"] in HUMAN_REACHABLE for i in items) else "not_ended"


def next_step(items: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The one obligation a run's row names. A run that needs a human names the question it will be shown (the reason it is
    listed as needing one), else the first the gate names now (the first not held behind an earlier one, which is also the
    first of ``outstanding`` that is not ``later``); ``None`` when nothing is owed (an ended run, or all requirements met)."""
    now = (next((i for i in items if i["who"] == "human" and i["where"] in HUMAN_REACHABLE), None)
           or next((i for i in items if i["where"] != LATER), None))
    return None if now is None else {key: now[key] for key in ("who", "where", "message")}


def _residual_rows(view: Mapping[str, Any], reasons: Mapping[str, Any], *, ended: bool) -> list[dict[str, Any]]:
    return _group([{"code": r["code"], "claims": [r["claim_id"]] if r.get("claim_id") else [],
                    "message": reason_message(reasons[r["code"]], r["parameters"], ended=ended)}
                   for r in view["residuals"]], ("code", "message"))


def _current_rows(requirements: tuple[Requirement, ...], reasons: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Everything the report would still require of a run that has ended, as it is now: each requirement of
    ``evaluation.report_requirements`` on the live snapshot, whatever the run's terminal status, except the human's consent to end
    it (the end already settled that). The same rows as a residual, worded as a requirement that stands now."""
    return _group([{"code": r.reason, "claims": list(r.claims),
                    "message": reason_message(reasons[r.reason], r.parameters)}
                   for r in requirements if r.reason and r.stage != "terminal"], ("code", "message"))


def project(snapshot: EvaluationSnapshot, reasons: Mapping[str, Any], *, stopped: EvaluationSnapshot | None = None,
            recorded: str = "recorded") -> tuple[dict[str, Any], dict[str, Any]]:
    """The run's row facts for the store summary and its detail, from one derivation. ``reasons`` is the
    public-contract registry's reasons (each with its wording).

    Two time bases, each named. ``snapshot`` is the run as it is now (today's workspace): every current fact (the audit and
    its currency, each claim's state, review and assurance, the freshness of its proof, the files that changed) is computed
    from it by the same owners the author view uses, whatever became of the run. ``stopped`` is the same run as its last
    transaction recorded it, given for a run that has ended; it feeds only the facts *at the stop*: ``residuals`` (what the
    stop left unresolved) and, when today's evaluation differs, ``now`` beside them. ``recorded`` is ``"unavailable"`` when the
    record cannot hold the stop: its residuals are then unknown, never inferred from today's files."""
    view = project_runview(snapshot)
    verdict = latest_audit_verdict(snapshot)
    graph = snapshot.graph
    claims = graph["claims"] if graph is not None else []
    derivation = derive_claims_for_projection(snapshot) if graph is not None else None
    blockers = ({b["claim_id"]: b for b in claim_blockers(snapshot, derivation)}
                if derivation is not None else {})
    scope = set(effective_scope_ids(snapshot)) if graph is not None else set()
    deciding = set(pending_decisions(snapshot, derivation)) if derivation is not None else set()
    batch = view["confirmation_batch"]
    prompt_error = batch["prompt_error"] if batch is not None else None
    ended = snapshot.state.status != "active"
    audit = view["audit"]
    items = ([] if ended else outstanding(report_requirements(snapshot, derivation, tuple(blockers.values())), reasons))
    accepted = derivation is not None and audit_acceptance(snapshot, derivation) is None
    row_claims = []
    for c in claims:
        state = derivation.states[c["id"]]
        review = review_state(snapshot, c, verdict)
        row_claims.append({
            "id": c["id"], "text": c["text"], "kind": c["kind"], "gating": c["id"] in scope, "state": state,
            "blocker": blockers[c["id"]]["reason"] if c["id"] in blockers else None,
            "blocker_claim": blockers[c["id"]]["target_claim_id"] if c["id"] in blockers else None,
            "evidence": evidence_kind(snapshot, c), "review": review,
            "assurance": assurance(state, review, accepted),
        })
    row = {
        "goal": snapshot.state.goal,
        "session": session_fact(governance.plain(snapshot.state.invocation)),
        "attention": attention(snapshot.state.status, items),
        "next": next_step(items),
        "audit": {key: audit[key] for key in ("verdict", "current", "pending", "independence")},
        "progress": {"required": sum(c["gating"] for c in row_claims),
                     "approved": sum(c["gating"] and c["state"] == "approved" for c in row_claims)},
        "root": graph["root"] if graph is not None else None,
        "edges": [{"from": e["from"], "to": e["to"]} for e in graph["edges"]] if graph is not None else [],
        "claims": row_claims,
    }
    author = snapshot.state.governance.get("context", {}).get("author")
    auditor = audit_attributions(snapshot, verdict)[0] if verdict is not None else None
    standing = {c["id"]: claim_digest(c) for c in claims}
    residuals = _residual_rows(project_runview(stopped), reasons, ended=ended) if stopped is not None else None
    stop = None
    if ended:
        stop = {"basis": recorded, "status": snapshot.state.status,
                "frozen": snapshot.state.frozen_claim_ids is not None,
                "required": [c["id"] for c in claims if c["id"] in scope],
                "deferred": [c["id"] for c in claims if c["id"] not in scope]}
    elif residuals is None:
        residuals = _residual_rows(view, reasons, ended=False)
    now = (_current_rows(report_requirements(snapshot, derivation, tuple(blockers.values()), setup=False), reasons)
           if ended and stopped is not None and stopped.observation_digest != snapshot.observation_digest else None)
    changed = view["freshness"]["changes"]
    detail = governance.plain({
        "goal": snapshot.state.goal,
        "governance": governance_view(governance_facts(snapshot.state, confirmation.interactions(snapshot))),
        "audit": {"findings": list(audit["findings"])},
        "stop": stop,
        "residuals": residuals,
        "now": now,
        "outstanding": items,
        "stale": changed,
        "author": identity_pair(author if isinstance(author, Mapping) else None),
        "auditor": identity_pair(auditor),
        "steps": [_step(n, item, standing) for n, item in enumerate(snapshot.history, start=1)],
        "claims": [_claim_detail(snapshot, c, c["id"] in scope,
                                 reason_message(reasons[blockers[c["id"]]["reason"]],
                                                blockers[c["id"]]["parameters"], ended=ended)
                                 if c["id"] in blockers else None, c["id"] in deciding, prompt_error,
                                 audit["pending"], set(derivation.freshness_heads))
                   for c in claims],
    })
    return governance.plain(row), detail
