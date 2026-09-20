"""Pure bounded public projections from an evaluation snapshot."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .evaluation import (EvaluationSnapshot, active_evidence, audit_attributions, claim_conflicted,
                         claim_digest, claim_state, digest, identity_pair, stale_artifact_ids)


def _copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _copy(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_copy(v) for v in value]
    return value


def _scope(snapshot: EvaluationSnapshot) -> tuple[list[str], list[str]]:
    if snapshot.graph is None:
        return [], []
    all_ids = [c["id"] for c in snapshot.graph["claims"]]
    gating = (list(snapshot.state.frozen_claim_ids) if snapshot.state.frozen_claim_ids is not None
              else [c["id"] for c in snapshot.graph["claims"] if c["gating"]])
    return gating, [cid for cid in all_ids if cid not in set(gating)]


def _freshness(snapshot: EvaluationSnapshot) -> tuple[list[dict[str, Any]], set[str]]:
    stale = stale_artifact_ids(snapshot)
    current = {o.path: o for o in snapshot.observations}
    changes: list[dict[str, Any]] = []
    for art in snapshot.history:
        if art.get("kind") != "spike" or art["artifact_id"] not in stale:
            continue
        for binding in art.get("file_bindings", []):
            now = current.get(binding["path"])
            state = now.state.value if now is not None else "missing"
            observed = now.sha256 if now is not None else None
            if state != "present" or observed != binding["sha256"]:
                row = {"path": binding["path"], "state": state}
                if row not in changes:
                    changes.append(row)
    changes.sort(key=lambda c: c["path"])
    return changes, stale


def _obligations(snapshot: EvaluationSnapshot) -> dict[str, list[dict[str, Any]]]:
    state = snapshot.state
    late = bool(snapshot.command and snapshot.command.get("type") == "ObserveAction"
                and snapshot.command["action"].get("kind") == "route"
                and state.investigation_stamp is not None)
    active = [{"id": "obligation.route", "must": "Record routing before investigation.",
               "status": "satisfied" if state.route_stamp is not None else "residual"}]
    if late:
        active.append({"id": "obligation.route.late", "must": "Do not reroute after investigation.",
                       "status": "violated"})
    if state.route_stamp is not None:
        active.append({"id": "obligation.investigation", "must": "Investigate after routing.",
                       "status": "satisfied" if state.investigation_stamp is not None else "residual"})
    gating, deferred = _scope(snapshot)
    if snapshot.graph:
        for claim in snapshot.graph["claims"]:
            row = {"id": "claim:" + claim["id"], "must": "Discharge claim " + claim["id"] + ".",
                   "status": "satisfied" if claim_state(snapshot, claim) == "approved" else "residual"}
            if claim["id"] in deferred:
                row["hold"] = "deferred"
            active.append(row)
    return {"active": active, "deferred": []}


def _residuals(snapshot: EvaluationSnapshot) -> list[dict[str, Any]]:
    status = snapshot.state.status
    _, deferred = _scope(snapshot)
    if snapshot.state.frozen_claim_ids is not None and deferred:
        return [{"code": "freeze.deferred", "parameters": {
            "claim_ids": deferred, "deferred_scope_digest": digest(deferred)},
            "next_actions": ["residual.accept"], "sections": ["freeze"]}]
    if status == "stopped_budget":
        return [{"code": "budget.exhausted", "parameters": {"resource": "pass"},
                 "next_actions": ["budget.raise", "residual.accept"], "sections": ["budget"]}]
    if status == "stopped_frozen":
        return []
    if status == "stopped_residual" and snapshot.graph:
        conflicted = any(claim_conflicted(snapshot, c) for c in snapshot.graph["claims"])
        discarded = any(claim_state(snapshot, c) == "discarded" for c in snapshot.graph["claims"])
        code = "claim.refuted" if discarded else ("evidence.conflict" if conflicted
                                                   else "claim.research_missing")
        metadata = ({"next_actions": ["run.inspect"], "sections": ["claims/refutation"]}
                    if discarded else ({"next_actions": ["run.inspect", "residual.accept"],
                    "sections": ["claims/refutation"]} if conflicted else
                    {"next_actions": ["research.record"], "sections": ["evidence/research"]}))
        return [{"code": code, "parameters": {}, **metadata}]
    return []


def project_runview(snapshot: EvaluationSnapshot, relevant_sections: list[str] | None = None) -> dict[str, Any]:
    changes, _ = _freshness(snapshot)
    children = []
    for child in snapshot.state.children:
        row = {"child_id": child["child_id"], "purpose": child["purpose"], "state": child["state"]}
        if child.get("deadline") is not None:
            row["deadline"] = str(child["deadline"])
        if child["state"] in {"launch_rejected", "failed", "cancelled", "timed_out", "orphaned"}:
            row["recovery_action"] = "child.retry"
        children.append(row)
    return {
        "id": snapshot.run_id, "goal": snapshot.state.goal, "status": snapshot.state.status,
        "modes": dict(snapshot.state.modes),
        "contract": {"id": snapshot.contract_id, "version": snapshot.contract_version,
                     "digest": snapshot.contract_digest,
                     "relevant_sections": list(["protocol"] if relevant_sections is None
                                               else relevant_sections)},
        "obligations": _obligations(snapshot), "residuals": _residuals(snapshot),
        "freshness": {"changes": changes}, "children": children,
        "next_actions": [],
        "untrusted_delimiters": {"open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
                                 "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"},
        "host": {"profile_id": snapshot.profile_id, "tier": snapshot.host_tier,
                 "missing_capabilities": (["host.async_unsupported"]
                    if snapshot.host_tier == "foreground_only" else
                    ["host.audit_output_unobservable"]
                    if snapshot.host_tier == "observational" else [])},
    }


def _audit(snapshot: EvaluationSnapshot) -> dict[str, Any]:
    audits = [a for a in snapshot.history if a.get("kind") == "audit_verdict"]
    audit_children = [c for c in snapshot.state.children if c["purpose"] == "audit"]
    state = ("passed" if audits and audits[-1]["verdict"] == "pass" else
             "failed" if audits else
             "pending" if any(c["state"] in {"reserved", "launching", "pending"}
                              for c in audit_children) else "required")
    verdict = audits[-1] if audits else {}
    auditor, covered = audit_attributions(snapshot, verdict)
    auditor_pair, covered_pair = identity_pair(auditor), identity_pair(covered)
    if auditor_pair is None or covered_pair is None:
        independence = "unverified"
    elif auditor_pair == covered_pair:
        independence = "same_model"
    else:
        independence = "decorrelated"
    return {"state": state, "independence": independence,
            "reviewed_argument_digest": verdict.get("argument_digest"),
            "reviewed_goal_digest": verdict.get("goal_digest"),
            "reviewed_frozen_scope_digest": verdict.get("frozen_scope_digest"),
            "reviewed_deferred_scope_digest": verdict.get("deferred_scope_digest"),
            "reviewed_claims": _copy(verdict.get("reviewed_claims", []))}


def project_argument(snapshot: EvaluationSnapshot) -> dict[str, Any]:
    if snapshot.graph is None:
        raise ValueError("argument projection requires a valid graph")
    gating, deferred = _scope(snapshot)
    changes, _ = _freshness(snapshot)
    del changes
    claims: list[dict[str, Any]] = []
    active_ids: set[str] = set()
    for claim in snapshot.graph["claims"]:
        evidence = active_evidence(snapshot, claim)
        ids = [a["artifact_id"] for a in evidence]
        active_ids.update(ids)
        state = claim_state(snapshot, claim)
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
    return _copy({
        "root_claim_id": snapshot.graph["root"], "argument_digest": argument_digest,
        "goal_digest": digest(snapshot.state.goal), "frozen_scope_digest": frozen_digest,
        "deferred_scope_digest": digest(deferred),
        "untrusted_delimiters": {"open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
                                 "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"},
        "claims": claims, "edges": [dict(edge) for edge in snapshot.graph["edges"]], "artifacts": artifacts,
        "audit": _audit(snapshot),
    })
