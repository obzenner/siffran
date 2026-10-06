"""Pure observer projection: one run's facts for the UI read model (``contracts/empirica-ui-view/v1``).

Every value is the plugin's own derivation, read and never re-decided: claim states, scope and
blockers (``core.evaluation``), the audit state and findings, residuals and freshness changes
(``core.projection``'s run view), and host-observed identities. This module adds exactly two
classifications the run view has no field for. Both are pure functions of the same facts:

* ``evidence``: the strongest kind among a claim's active evidence (a spike, else research, else
  none);
* ``review``: the claim measured against the latest audit verdict, using the same evidence digest
  that ``audit_binding`` binds into a verdict.

It also owns the one place a reason's registry message is filled in (:func:`reason_message`), so a
residual and a claim blocker word the same reason the same way.

No I/O, no clock and no presentation: labels, colours and counts belong to the UI.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from . import citations, confirmation, governance
from .evaluation import (EvaluationSnapshot, active_evidence, audit_attributions, audit_passes,
                         claim_blockers, claim_digest, derive_claims_for_projection, effective_scope_ids,
                         evidence_digest, identity_pair, pending_decisions)
from .projection import project_runview

#: ``evidence`` values, strongest first, and ``review`` values: the two vocabularies this module
#: owns (``contracts/empirica-ui-view/v1`` mirrors them; ``scripts/validate_ui_view.py`` holds it).
EVIDENCE_KINDS = ("spike", "research", "none")
REVIEW_STATES = ("passed", "failed", "stale", "changed", "unreviewed", "none")
#: The view's name for each budget ceiling ``governance.BUDGETS`` declares (``max_passes`` ->
#: ``passes``).
BUDGET_NAMES = {ceiling: ceiling.removeprefix("max_") for ceiling in governance.BUDGETS}


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def reason_message(template: str, parameters: Mapping[str, Any]) -> str:
    """A reason's registry message with its typed parameters filled in. Every placeholder of the
    template must be a parameter of the reason and every value a scalar (a list is joined); a
    template the parameters cannot complete is a contract bug, never shown half-formatted."""
    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in parameters:
            raise ValueError(f"reason parameter {name!r} missing for message {template!r}")
        value = parameters[name]
        values = value if isinstance(value, list) else [value]
        if not all(isinstance(v, str | int | float) and not isinstance(v, bool) for v in values):
            raise ValueError(f"reason parameter {name!r} is not a scalar or a list of scalars")
        return ", ".join(str(v) for v in values)
    return _PLACEHOLDER.sub(fill, template)


def _latest_verdict(snapshot: EvaluationSnapshot) -> Mapping[str, Any] | None:
    settled = [a for a in snapshot.history if a.get("kind") == "audit_verdict"]
    return settled[-1] if settled else None


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


def _step(n: int, item: Mapping[str, Any]) -> dict[str, Any]:
    kind = item.get("kind")
    outcome = (item.get("verdict") if kind == "audit_verdict"
               else item.get("outcome") if kind in {"research", "spike"} else None)
    return {"n": n, "kind": kind, "outcome": outcome,
            "claim": item.get("claim_id"), "command": item.get("command")}


#: The reason families the observer shows a human as a claim's blocker or a run's residual, and what such a message
#: may be: one plain sentence (the presentation rules live in recovery.md and the settlement notices, R3 M3).
UI_REASON_FAMILIES = ("claim.", "evidence.", "audit.", "budget.", "freeze.", "child.")
UI_MESSAGE_LIMIT = 120
UI_MESSAGE_BANNED = ("derivation", "receipt", "fold")


def message_problems(code: str, message: str) -> list[str]:
    """Why a registry message that reaches the UI is not plain enough, else ``[]``."""
    if not code.startswith(UI_REASON_FAMILIES):
        return []
    problems = [f"{code}: {len(message)} characters, over {UI_MESSAGE_LIMIT}"] if len(message) > UI_MESSAGE_LIMIT else []
    return problems + [f"{code}: uses the jargon {w!r}" for w in UI_MESSAGE_BANNED if w in message.lower()]


#: What a human-owned wait reads as once it cannot be asked now. ``route`` (who confirms) stays ``audit``, ``human``
#: or ``unavailable`` while a host will ask; otherwise it names why nobody will: the batch was already shown
#: (``shown``), the run's human interactions are spent (``limit``), or the run is not active (``ended``).
SPENT_ROUTES = {"already_presented": "shown", "interaction_limit": "limit"}
ENDED = "ended"


def ask_route(snapshot: EvaluationSnapshot, route: str | None, prompt_error: str | None) -> str | None:
    """The route as the human is told it. ``prompt_error`` is the run view's ``confirmation_batch.prompt_error``,
    the one owner of whether a host will ask (the observer re-derives nothing)."""
    if route is None or route == confirmation.AUDIT:
        return route
    if snapshot.state.status != "active":
        return ENDED
    return SPENT_ROUTES.get(prompt_error, route)


def _record(snapshot: EvaluationSnapshot, record: Mapping[str, Any], prompt_error: str | None) -> dict[str, Any]:
    """One research record as the human sees it: its source and quote, how it is verified (its class and
    status), where it still waits (``route``: null unless its confirmation is pending), whether the
    auditor must retrieve it, and the author's reason no spike could establish it and the human's note."""
    decided = confirmation.receipt(snapshot, record)
    return {"source": record["source_ref"], "source_kind": record["source_kind"],
            "quote": record["citation"], "outcome": record["outcome"],
            "class": citations.verification_class(record["source_kind"], record["source_ref"]),
            "status": confirmation.status(snapshot, record),
            "route": ask_route(snapshot, confirmation.pending_route(snapshot, record), prompt_error),
            "retrieve": confirmation.retrieve(snapshot, record),
            "spike_unavailable": record.get("spike_unavailable"),
            "note": decided.get("note") if decided is not None else None}


def _decision(snapshot: EvaluationSnapshot, claim: Mapping[str, Any], waiting: bool,
              prompt_error: str | None) -> dict[str, Any] | None:
    """A ``needs-decision`` claim's human answer: ``outcome`` null while it waits (then ``route`` says
    on whom), ``approve`` or ``reject`` once answered."""
    if claim["kind"] != "needs-decision":
        return None
    answer = confirmation.decision(snapshot, claim["id"], claim_digest(claim))
    return {"outcome": answer["outcome"] if answer else None,
            "answer": answer.get("answer") if answer else None,
            "route": ask_route(snapshot, confirmation.human_route(snapshot), prompt_error) if waiting else None}


def _claim_detail(snapshot: EvaluationSnapshot, claim: Mapping[str, Any],
                  blocker_message: str | None, waiting: bool, prompt_error: str | None) -> dict[str, Any]:
    active = active_evidence(snapshot, claim)
    spikes = [a for a in active if a["kind"] == "spike" and a.get("outcome") == "pass"]
    digest = claim_digest(claim)
    return {"id": claim["id"], "blocker_message": blocker_message,
            "records": [_record(snapshot, a, prompt_error) for a in snapshot.history
                        if a.get("kind") == "research" and a.get("claim_id") == claim["id"]
                        and a.get("claim_digest") == digest],
            "decision": _decision(snapshot, claim, waiting, prompt_error),
            "research": sum(a["kind"] == "research" and a.get("outcome") == "supporting"
                            for a in active),
            "spikes": len(spikes),
            "spikes_ever": sum(a.get("kind") == "spike" and a.get("claim_id") == claim["id"]
                               for a in snapshot.history),
            "commands": [a["command"] for a in spikes]}


def budgets(state: Any) -> dict[str, dict[str, int]]:
    return {name: {"used": state.budgets[governance.CEILINGS[ceiling]], "max": state.budgets[ceiling]}
            for ceiling, name in BUDGET_NAMES.items()}


def control(state: Any) -> dict[str, str]:
    return {"mode": state.governance["control_mode"], "approval": state.governance["state"]}


def project(snapshot: EvaluationSnapshot, messages: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The run's row facts for the store summary (audit state, graph, per-claim facts) and its detail
    (budgets, governance, audit, residuals, freshness, history and evidence), from one derivation.
    ``messages`` maps each reason code to the public-contract registry's message."""
    view = project_runview(snapshot)
    verdict = _latest_verdict(snapshot)
    graph = snapshot.graph
    claims = graph["claims"] if graph is not None else []
    derivation = derive_claims_for_projection(snapshot) if graph is not None else None
    blockers = ({b["claim_id"]: b for b in claim_blockers(snapshot, derivation)}
                if derivation is not None else {})
    scope = set(effective_scope_ids(snapshot)) if graph is not None else set()
    deciding = set(pending_decisions(snapshot, derivation)) if derivation is not None else set()
    batch = view["confirmation_batch"]
    prompt_error = batch["prompt_error"] if batch is not None else None
    # What waits on a human is the run view's ``confirmation_batch`` and nothing else: empty unless the run is
    # active and a host could present it. Per claim: how many of the batch's records are its, and whether it is
    # one of the batch's decisions.
    owner = {a["artifact_id"]: a.get("claim_id") for a in snapshot.history if a.get("kind") == "research"}
    batched = [owner.get(i) for i in (batch["record_ids"] if batch is not None else [])]
    asked = set(batch["claim_ids"]) if batch is not None else set()
    row = {
        "audit": view["audit"]["state"],
        "root": graph["root"] if graph is not None else None,
        "edges": [{"from": e["from"], "to": e["to"]} for e in graph["edges"]] if graph is not None else [],
        "claims": [{
            "id": c["id"], "text": c["text"], "kind": c["kind"], "gating": c["id"] in scope,
            "state": derivation.states[c["id"]],
            "blocker": blockers[c["id"]]["reason"] if c["id"] in blockers else None,
            "blocker_claim": blockers[c["id"]]["target_claim_id"] if c["id"] in blockers else None,
            "evidence": evidence_kind(snapshot, c),
            "review": review_state(snapshot, c, verdict),
        } for c in claims],
    }
    author = snapshot.state.governance.get("context", {}).get("author")
    auditor = audit_attributions(snapshot, verdict)[0] if verdict is not None else None
    detail = governance.plain({
        "goal": snapshot.state.goal,
        "budgets": budgets(snapshot.state),
        "control": control(snapshot.state),
        "audit": {"independence": view["audit"]["independence"], "findings": list(view["audit"]["findings"])},
        "residuals": [{"code": r["code"], "claim": r.get("claim_id"),
                       "message": reason_message(messages[r["code"]], r["parameters"])}
                      for r in view["residuals"]],
        "prompt_error": prompt_error,
        "waiting": [{"claim": c["id"], "route": ask_route(snapshot, confirmation.human_route(snapshot), prompt_error),
                     "records": batched.count(c["id"]), "decision": c["id"] in asked}
                    for c in claims if c["id"] in batched or c["id"] in asked],
        "stale": view["freshness"]["changes"],
        "author": identity_pair(author if isinstance(author, Mapping) else None),
        "auditor": identity_pair(auditor),
        "steps": [_step(n, item) for n, item in enumerate(snapshot.history, start=1)],
        "claims": [_claim_detail(snapshot, c, reason_message(messages[blockers[c["id"]]["reason"]],
                                                            blockers[c["id"]]["parameters"])
                                 if c["id"] in blockers else None, c["id"] in deciding, prompt_error)
                   for c in claims],
    })
    return row, detail
