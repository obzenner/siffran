"""Pure observer projection: one run's facts for the UI read model (``contracts/empirica-ui-view/v1``).

Every value is the plugin's own derivation, read and never re-decided: claim states, scope and
blockers (``core.evaluation``), the audit state and findings, residuals and freshness changes
(``core.projection``'s run view), and host-observed identities. This module adds exactly two
classifications the run view has no field for. Both are pure functions of the same facts:

* ``evidence``: the strongest kind among a claim's active evidence (a spike, else research, else
  none);
* ``review``: the claim measured against the latest audit verdict, using the same evidence digest
  that ``audit_binding`` binds into a verdict.

No I/O, no clock and no presentation: labels, colours and counts belong to the UI.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import governance
from .evaluation import (EvaluationSnapshot, active_evidence, audit_attributions, audit_passes,
                         claim_blockers, derive_claims_for_projection, effective_scope_ids,
                         evidence_digest, identity_pair)
from .projection import project_runview

#: ``evidence`` values, strongest first, and ``review`` values: the two vocabularies this module
#: owns (``contracts/empirica-ui-view/v1`` mirrors them; ``scripts/validate_ui_view.py`` holds it).
EVIDENCE_KINDS = ("spike", "research", "none")
REVIEW_STATES = ("passed", "failed", "stale", "changed", "unreviewed", "none")
#: The view's name for each budget ceiling ``governance.BUDGETS`` declares (``max_passes`` ->
#: ``passes``).
BUDGET_NAMES = {ceiling: ceiling.removeprefix("max_") for ceiling in governance.BUDGETS}


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


def _claim_detail(snapshot: EvaluationSnapshot, claim: Mapping[str, Any],
                  blocker_message: str | None) -> dict[str, Any]:
    active = active_evidence(snapshot, claim)
    spikes = [a for a in active if a["kind"] == "spike" and a.get("outcome") == "pass"]
    return {"id": claim["id"], "blocker_message": blocker_message,
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
        "residuals": [{"code": r["code"], "claim": r.get("claim_id"), "message": messages[r["code"]]}
                      for r in view["residuals"]],
        "stale": view["freshness"]["changes"],
        "author": identity_pair(author if isinstance(author, Mapping) else None),
        "auditor": identity_pair(auditor),
        "steps": [_step(n, item) for n, item in enumerate(snapshot.history, start=1)],
        "claims": [_claim_detail(snapshot, c, messages[blockers[c["id"]]["reason"]] if c["id"] in blockers else None)
                   for c in claims],
    })
    return row, detail
