"""Pure bounded public projections from an evaluation snapshot."""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from . import governance

from .evaluation import (EvaluationSnapshot, HOST_TIER_UNSUPPORTED, active_evidence, audit_blocker,
                         bootstrap_status,
                         claim_blockers, claim_digest, derive_claims,
                         derive_claims_for_projection, digest,
                         effective_scope_ids, independence)


def _scope(snapshot: EvaluationSnapshot) -> tuple[list[str], list[str]]:
    if snapshot.graph is None:
        return [], []
    all_ids = [c["id"] for c in snapshot.graph["claims"]]
    gating = list(effective_scope_ids(snapshot))
    return gating, [cid for cid in all_ids if cid not in set(gating)]


def _freshness(derivation: Any) -> tuple[list[dict[str, Any]], set[str]]:
    stale = set(derivation.freshness_heads)
    changes = sorted({(change.path, change.state.value)
                      for head in derivation.freshness_heads.values()
                      for change in head.changes})
    return [{"path": path, "state": state} for path, state in changes], stale


def _reason_metadata(metadata: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]],
                     code: str) -> tuple[list[str], list[str]]:
    if code not in metadata:
        raise ValueError(f"missing contract metadata for reason: {code}")
    next_actions, sections = metadata[code]
    return list(next_actions), list(sections)


def _obligation(
    obligation_id: str, required: str, observed: list[dict[str, Any]],
    missing: dict[str, Any] | None, metadata, status: str, terminal_next: list[str] | None = None,
) -> dict[str, Any]:
    if terminal_next is not None:
        next_actions = list(terminal_next)
    else:
        next_actions = [] if missing is None else _reason_metadata(metadata, missing["code"])[0]
    return {"id": obligation_id, "required": required, "observed": observed,
            "missing": missing, "next": next_actions, "status": status}


def _residual(metadata, code: str, parameters: dict[str, Any],
              terminal_next: list[str] | None = None, **extra) -> dict[str, Any]:
    next_actions, sections = _reason_metadata(metadata, code)
    if terminal_next is not None:
        next_actions = list(terminal_next)
    return {**extra, "code": code, "parameters": parameters,
            "next_actions": next_actions, "sections": sections}


def _obligations(snapshot: EvaluationSnapshot, states: Mapping[str, str],
                 blockers: tuple[dict[str, Any], ...],
                 metadata: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]],
                 stale: set[str], terminal_next: list[str] | None = None,
                 ) -> dict[str, list[dict[str, Any]]]:
    state = snapshot.state
    late = bool(snapshot.command and snapshot.command.get("type") == "ObserveAction"
                and snapshot.command["action"].get("kind") == "route"
                and state.investigation_stamp is not None)
    bootstrap = bootstrap_status(snapshot)
    active = [_obligation(row["id"], row["must"], [], None, metadata, row["status"], terminal_next)
              for row in bootstrap["active"]]
    if late:
        active.append(_obligation(
            "obligation.route.late", snapshot.late_route_must, [],
            {"code": "route.late", "target_claim_id": None, "parameters": {}},
            metadata, "violated", terminal_next))
    blockers_by_claim = {row["claim_id"]: row for row in blockers}
    _, deferred = _scope(snapshot)
    if snapshot.graph:
        for claim in snapshot.graph["claims"]:
            blocker = blockers_by_claim.get(claim["id"])
            missing = (None if blocker is None else {
                "code": blocker["reason"], "target_claim_id": blocker["target_claim_id"],
                "parameters": blocker["parameters"]})
            observed = [{"artifact_id": item["artifact_id"], "kind": item["kind"],
                         "outcome": item["outcome"],
                         "stale": item["kind"] == "spike" and item["artifact_id"] in stale}
                        for item in active_evidence(snapshot, claim)]
            row = _obligation(
                "claim:" + claim["id"], claim["text"], observed, missing, metadata,
                "satisfied" if states[claim["id"]] == "approved" else "residual", terminal_next)
            if claim["id"] in deferred:
                row["hold"] = "deferred"
            active.append(row)
    return {"active": active, "deferred": []}


def _residuals(snapshot: EvaluationSnapshot, derivation: Any,
               blockers: tuple[dict[str, Any], ...],
               metadata: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]],
               terminal_next: list[str] | None = None) -> list[dict[str, Any]]:
    status = snapshot.state.status
    _, deferred = _scope(snapshot)
    if snapshot.state.frozen_claim_ids is not None and deferred:
        return [_residual(metadata, "freeze.deferred", {
            "claim_ids": deferred, "deferred_scope_digest": digest(deferred)}, terminal_next)]
    if status == "stopped_budget":
        return [_residual(metadata, "budget.exhausted", {"resource": "pass"}, terminal_next)]
    if status == "stopped_frozen":
        return []
    if status == "stopped_residual" and snapshot.graph:
        residuals = []
        for blocker in blockers:
            residuals.append(_residual(
                metadata, blocker["reason"], blocker["parameters"], terminal_next,
                claim_id=blocker["claim_id"], target_claim_id=blocker["target_claim_id"]))
        if residuals:
            return residuals
        blocker = audit_blocker(snapshot, derivation)
        if blocker:
            return [_residual(metadata, blocker["reason"], blocker["parameters"], terminal_next)]
    return []


_HIDDEN = re.compile(r"[\\\x00-\x1f\x7f-\x9f\u00ad\u061c\u200b-\u200f\u2028-\u202e\u2060\u2066-\u2069\ufeff]")


def safe_text(item: object) -> str:
    def escape(match):
        code = ord(match.group())
        return "\\\\" if code == 92 else (f"\\x{code:02x}" if code <= 255 else f"\\u{code:04x}")
    return _HIDDEN.sub(escape, str(item))


def governance_dialog(goal: str, value: Mapping, controls: Mapping,
                      invocation: Mapping | None = None) -> dict[str, Any]:
    """Project governance state into host-neutral, display-safe dialog data."""
    proposal = value["proposal"]
    budgets = [
        {"key": key, "label": controls["budgets"][key]["label"],
         "short": controls["budgets"][key]["short"], "help": controls["budgets"][key]["help"],
         "value": proposal["budgets"][key], "used": value["budgets"][used_key],
         "minimum": max(1 if key == "max_passes" else 0, value["budgets"][used_key]),
         "maximum": controls["budgets"][key]["maximum"]}
        for key, used_key in governance.CEILINGS.items()
    ]
    return {"epoch": value["plan_revision"], "control_mode": value["control_mode"],
            "state": value["state"], "reviews_left": {
                "proposal": value["interactions_remaining"]["proposal"],
                "total": value["interactions_remaining"]["total"]},
            "goal": safe_text(goal),
            "invocation": None if invocation is None else {
                key: safe_text(invocation[key]) if key in {"host", "signal"} else invocation[key]
                for key in ("host", "interactive", "signal", "delegation")},
            "budgets": budgets}


def project_governance(snapshot: EvaluationSnapshot) -> dict:
    value = governance.plain(snapshot.state.governance)
    bootstrap = bootstrap_status(snapshot)
    value.update(interactions_remaining=governance.interactions_remaining(snapshot.state.governance),
                 prompt_error=governance.interaction_error(snapshot.state.governance))
    value.pop("receipts")
    value.update(budgets=dict(snapshot.state.budgets),
                 remaining={ceiling: snapshot.state.budgets[ceiling] - snapshot.state.budgets[used]
                            for ceiling, used in governance.CEILINGS.items()},
                 request_ready=bootstrap["request_ready"], display_ready=bootstrap["display_ready"],
                 next_action=bootstrap["next_actions"][-1] if bootstrap["next_actions"] else "run.inspect")
    return value


def project_presentation(snapshot: EvaluationSnapshot) -> dict:
    """Return the private host-neutral dialog and scope for governance mediation."""
    value = project_governance(snapshot)
    return {"dialog": governance_dialog(snapshot.state.goal, value,
                                         snapshot.governance_controls,
                                         snapshot.state.invocation),
            "scope": governance.canonical_graph(snapshot.graph)}


_STOPPED_CHILD_STATES = frozenset({"launch_rejected", "failed", "cancelled", "timed_out", "orphaned"})


def _child_summary(child: Mapping[str, Any], recovery_action: str) -> dict[str, Any]:
    """Project one child. A stopped child always names its recovery (the schema requires it):
    ``child.retry`` while the run is active, the terminal guidance once the run is terminal."""
    row = {"child_id": child["child_id"], "purpose": child["purpose"],
           "resource_class": child["resource_class"], "state": child["state"]}
    if child.get("deadline") is not None:
        row["deadline"] = str(child["deadline"])
    if child["state"] in _STOPPED_CHILD_STATES:
        row["recovery_action"] = recovery_action
    return row


def project_runview(snapshot: EvaluationSnapshot, relevant_sections: list[str] | None = None) -> dict[str, Any]:
    derivation = derive_claims_for_projection(snapshot)
    changes, stale = _freshness(derivation)
    blockers = claim_blockers(snapshot, derivation)
    metadata = {reason: (next_actions, sections)
                for reason, next_actions, sections in snapshot.reason_metadata}
    states = derivation.states
    bootstrap = bootstrap_status(snapshot)
    terminal = snapshot.state.status != "active"
    terminal_next = _reason_metadata(metadata, "run.terminal")[0] if terminal else None
    obligations = _obligations(snapshot, states, blockers, metadata, stale, terminal_next)
    residuals = _residuals(snapshot, derivation, blockers, metadata, terminal_next)
    recovery_action = terminal_next[0] if terminal else "child.retry"
    children = [_child_summary(child, recovery_action) for child in snapshot.state.children]
    return {
        "id": snapshot.run_id, "goal": snapshot.state.goal,
        "invocation": governance.plain(snapshot.state.invocation), "status": snapshot.state.status,
        "governance": project_governance(snapshot),
        "contract": {"id": snapshot.contract_id, "version": snapshot.contract_version,
                     "digest": snapshot.contract_digest,
                     "relevant_sections": list(["protocol"] if relevant_sections is None
                                               else relevant_sections)},
        "obligations": obligations,
        "residuals": residuals,
        "freshness": {"changes": changes}, "children": children,
        "next_actions": terminal_next if terminal else bootstrap["next_actions"],
        "untrusted_delimiters": dict(snapshot.untrusted_delimiters),
        "host": {"profile_id": snapshot.profile_id, "tier": snapshot.host_tier,
                 "missing_capabilities": ([HOST_TIER_UNSUPPORTED[snapshot.host_tier]]
                    if snapshot.host_tier in HOST_TIER_UNSUPPORTED else [])},
    }


def _audit(snapshot: EvaluationSnapshot) -> dict[str, Any]:
    audits = [a for a in snapshot.history if a.get("kind") == "audit_verdict"]
    audit_children = [c for c in snapshot.state.children if c["resource_class"] == "audit"]
    state = ("passed" if audits and audits[-1]["verdict"] == "pass" else
             "failed" if audits else
             "pending" if any(c["state"] in {"reserved", "launching", "pending"}
                              for c in audit_children) else "required")
    verdict = audits[-1] if audits else {}
    classification = independence(snapshot, verdict)
    return {"state": state, "independence": classification,
            "reviewed_argument_digest": verdict.get("argument_digest"),
            "reviewed_goal_digest": verdict.get("goal_digest"),
            "reviewed_frozen_scope_digest": verdict.get("frozen_scope_digest"),
            "reviewed_deferred_scope_digest": verdict.get("deferred_scope_digest"),
            "reviewed_claims": governance.plain(verdict.get("reviewed_claims", []))}


def project_argument(snapshot: EvaluationSnapshot) -> dict[str, Any]:
    if snapshot.graph is None:
        raise ValueError("argument projection requires a valid graph")
    gating, deferred = _scope(snapshot)
    claims: list[dict[str, Any]] = []
    active_ids: set[str] = set()
    states = derive_claims(snapshot).states
    for claim in snapshot.graph["claims"]:
        evidence = active_evidence(snapshot, claim)
        ids = [a["artifact_id"] for a in evidence]
        active_ids.update(ids)
        state = states[claim["id"]]
        claims.append({
            "claim_id": claim["id"], "text": claim["text"],
            "wording_digest": claim_digest(claim), "state": state, "kind": claim["kind"],
            "gating": claim["id"] in gating, "evidence_digest": digest(ids),
            "active_evidence_ids": ids,
        })
    artifacts: list[dict[str, Any]] = []
    for sequence, art in enumerate(snapshot.history):
        kind = art.get("kind")
        if kind not in {"research", "spike_request", "spike"}:
            continue
        common = {"sequence": sequence, "artifact_id": art["artifact_id"],
                  "claim_id": art["claim_id"], "claim_digest": art["claim_digest"],
                  "kind": kind, "statement_digest": art["statement_digest"]}
        if kind == "research":
            common.update(active=art["artifact_id"] in active_ids, outcome=art["outcome"],
                          source_kind=art["source_kind"], source_ref=art["source_ref"],
                          citation=art["citation"])
            if "observed_content_digest" in art:
                common["observed_content_digest"] = art["observed_content_digest"]
        elif kind == "spike_request":
            common.update(harness_request_id=art["harness_request_id"], command=art["command"],
                          command_digest=art["command_digest"], dependent_files=art["dependent_files"],
                          prerequisite_research_ids=art["prerequisite_research_ids"])
        else:
            common.update(active=art["artifact_id"] in active_ids, outcome=art["outcome"],
                          harness_request_id=art["harness_request_id"], command=art["command"],
                          command_digest=art["command_digest"],
                          prerequisite_research_ids=art["prerequisite_research_ids"],
                          file_bindings=art["file_bindings"], exit_code=art["exit_code"],
                          spike_gate=art["spike_gate"], supersedes=art.get("supersedes"))
        artifacts.append(common)
    frozen_digest = None if snapshot.state.frozen_claim_ids is None else digest(gating)
    argument_digest = digest({"graph": snapshot.graph, "evidence": [a["artifact_id"] for a in artifacts]})
    return governance.plain({
        "root_claim_id": snapshot.graph["root"], "argument_digest": argument_digest,
        "goal": snapshot.state.goal, "goal_digest": digest(snapshot.state.goal),
        "frozen_scope_digest": frozen_digest,
        "deferred_scope_digest": digest(deferred),
        "untrusted_delimiters": dict(snapshot.untrusted_delimiters),
        "claims": claims, "edges": [dict(edge) for edge in snapshot.graph["edges"]], "artifacts": artifacts,
        "route_stamp": snapshot.state.route_stamp,
        "investigation_stamp": snapshot.state.investigation_stamp,
        "audit": _audit(snapshot),
    })
