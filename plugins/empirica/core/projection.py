"""Pure bounded public projections from an evaluation snapshot."""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from . import governance

from .evaluation import (EvaluationSnapshot, active_evidence, audit_attributions, bootstrap_status,
                         claim_blockers, claim_digest, derive_claims, digest, identity_pair,
                         post_claim_blocker, stale_artifact_ids)


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


def _reason_metadata(metadata: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]],
                     code: str) -> tuple[list[str], list[str]]:
    if code not in metadata:
        raise ValueError(f"missing contract metadata for reason: {code}")
    next_actions, sections = metadata[code]
    return list(next_actions), list(sections)


def _obligations(snapshot: EvaluationSnapshot, states: Mapping[str, str],
                 blockers: tuple[dict[str, Any], ...],
                 metadata: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]],
                 stale: set[str]) -> dict[str, list[dict[str, Any]]]:
    state = snapshot.state
    late = bool(snapshot.command and snapshot.command.get("type") == "ObserveAction"
                and snapshot.command["action"].get("kind") == "route"
                and state.investigation_stamp is not None)
    bootstrap = bootstrap_status(snapshot)
    active = [{"id": row["id"], "required": row["must"], "observed": [],
               "missing": None, "next": [], "status": row["status"]}
              for row in bootstrap["active"]]
    if late:
        next_actions, _ = _reason_metadata(metadata, "route.late")
        active.append({"id": "obligation.route.late",
                       "required": "Do not reroute after investigation.",
                       "observed": [],
                       "missing": {"code": "route.late", "target_claim_id": None,
                                   "parameters": {}},
                       "next": next_actions, "status": "violated"})
    blockers_by_claim = {row["claim_id"]: row for row in blockers}
    _, deferred = _scope(snapshot)
    if snapshot.graph:
        for claim in snapshot.graph["claims"]:
            blocker = blockers_by_claim.get(claim["id"])
            missing = (None if blocker is None else {
                "code": blocker["reason"], "target_claim_id": blocker["target_claim_id"],
                "parameters": blocker["parameters"]})
            next_actions = ([] if blocker is None else
                            _reason_metadata(metadata, blocker["reason"])[0])
            observed = [{"artifact_id": item["artifact_id"], "kind": item["kind"],
                         "outcome": item["outcome"],
                         "stale": item["kind"] == "spike" and item["artifact_id"] in stale}
                        for item in active_evidence(snapshot, claim)]
            row = {"id": "claim:" + claim["id"], "required": claim["text"],
                   "observed": observed, "missing": missing, "next": next_actions,
                   "status": "satisfied" if states[claim["id"]] == "approved" else "residual"}
            if claim["id"] in deferred:
                row["hold"] = "deferred"
            active.append(row)
    return {"active": active, "deferred": []}


def _residuals(snapshot: EvaluationSnapshot, derivation: Any,
               blockers: tuple[dict[str, Any], ...],
               metadata: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]]) -> list[dict[str, Any]]:
    status = snapshot.state.status
    _, deferred = _scope(snapshot)
    if snapshot.state.frozen_claim_ids is not None and deferred:
        next_actions, sections = _reason_metadata(metadata, "freeze.deferred")
        return [{"code": "freeze.deferred", "parameters": {
            "claim_ids": deferred, "deferred_scope_digest": digest(deferred)},
            "next_actions": next_actions, "sections": sections}]
    if status == "stopped_budget":
        next_actions, sections = _reason_metadata(metadata, "budget.exhausted")
        return [{"code": "budget.exhausted", "parameters": {"resource": "pass"},
                 "next_actions": next_actions, "sections": sections}]
    if status == "stopped_frozen":
        return []
    if status == "stopped_residual" and snapshot.graph:
        residuals = []
        for blocker in blockers:
            next_actions, sections = _reason_metadata(metadata, blocker["reason"])
            residuals.append({"claim_id": blocker["claim_id"],
                              "target_claim_id": blocker["target_claim_id"],
                              "code": blocker["reason"],
                              "parameters": blocker["parameters"],
                              "next_actions": next_actions, "sections": sections})
        if residuals:
            return residuals
        derivation_digest = digest({"graph": snapshot.graph,
                                    "claims": [(claim["id"], derivation.states[claim["id"]],
                                                [item["artifact_id"] for item in active_evidence(
                                                    snapshot, claim)])
                                               for claim in snapshot.graph["claims"]],
                                    "frozen": snapshot.state.frozen_claim_ids})
        blocker = post_claim_blocker(snapshot, derivation, derivation_digest)
        if blocker:
            next_actions, sections = _reason_metadata(metadata, blocker["reason"])
            return [{"code": blocker["reason"], "parameters": blocker["parameters"],
                     "next_actions": next_actions, "sections": sections}]
    return []


_HIDDEN = re.compile(r"[\\\x00-\x1f\x7f-\x9f\u00ad\u061c\u200b-\u200f\u2028-\u202e\u2060\u2066-\u2069\ufeff]")


def safe_text(item: object) -> str:
    def escape(match):
        code = ord(match.group())
        return "\\\\" if code == 92 else (f"\\x{code:02x}" if code <= 255 else f"\\u{code:04x}")
    return _HIDDEN.sub(escape, str(item))


def review_text(goal: str, graph: Mapping | None, value: Mapping) -> str:
    def fence(item):
        lines.append("| " + safe_text(item))
    proposal, context = value["proposal"], value["context"]
    lines = [f"EMPIRICA RUN CONFIGURATION — epoch {value['plan_revision']}, mode {value['control_mode']}",
             "Approve the CURRENT displayed configuration; edits are submitted for another review and are NOT approved yet.",
             "The goal is read-only context and is not controlled by this decision.",
             "Every line beginning '| ' is UNTRUSTED quoted data. Controls, bidi characters, and backslashes are visibly escaped.", "", "GOAL (READ-ONLY)"]
    fence(goal)
    lines += ["", "CONFIGURATION"]
    for key, label in (("max_passes", "Investigation passes"), ("max_spawns", "Child spawns"), ("max_audit_spawns", "Audit spawns")):
        lines.append(f"  {label}: proposed {proposal['budgets'][key]}, already used {value['budgets'][governance.CEILINGS[key]]}")
    modes = proposal["modes"]
    lines += [f"  multi_provider (cross-provider actors): {modes['multi_provider']}",
              f"  cli_exec (external model/actor CLI use): {modes['cli_exec']}"]
    lines += ["", f"STATE — {value['state']}; dialogs left {value['interactions_remaining']['proposal']} this epoch, {value['interactions_remaining']['total']} total"]
    lines += ["", "TECHNICAL DETAIL (secondary)", f"  proposal digest {value['proposal_digest']}", f"  ingress {context['ingress']} · configuration epoch {value['plan_revision']}"]
    return "\n".join(lines)


def project_governance(snapshot: EvaluationSnapshot) -> dict:
    value = governance.plain(snapshot.state.governance)
    bootstrap = bootstrap_status(snapshot)
    value.update(interactions_remaining=governance.interactions_remaining(snapshot.state.governance),
                 prompt_error=governance.interaction_error(snapshot.state.governance))
    value.pop("receipts")
    value.update(scope=governance.canonical_graph(snapshot.graph),
                 budgets=dict(snapshot.state.budgets),
                 remaining={ceiling: snapshot.state.budgets[ceiling] - snapshot.state.budgets[used]
                            for ceiling, used in governance.CEILINGS.items()},
                 request_ready=bootstrap["request_ready"], display_ready=bootstrap["display_ready"],
                 next_action=bootstrap["next_actions"][-1] if bootstrap["next_actions"] else "run.inspect")
    value["review_text"] = review_text(snapshot.state.goal, snapshot.graph, value)
    return value


def project_runview(snapshot: EvaluationSnapshot, relevant_sections: list[str] | None = None) -> dict[str, Any]:
    changes, stale = _freshness(snapshot)
    derivation = derive_claims(snapshot)
    blockers = claim_blockers(snapshot, derivation)
    metadata = {reason: (next_actions, sections)
                for reason, next_actions, sections in snapshot.reason_metadata}
    states = derivation.states
    bootstrap = bootstrap_status(snapshot)
    children = []
    for child in snapshot.state.children:
        row = {"child_id": child["child_id"], "purpose": child["purpose"],
               "resource_class": child["resource_class"], "state": child["state"]}
        if child.get("deadline") is not None:
            row["deadline"] = str(child["deadline"])
        if child["state"] in {"launch_rejected", "failed", "cancelled", "timed_out", "orphaned"}:
            row["recovery_action"] = "child.retry"
        children.append(row)
    return {
        "id": snapshot.run_id, "goal": snapshot.state.goal, "status": snapshot.state.status,
        "modes": dict(snapshot.state.modes),
        "governance": project_governance(snapshot),
        "contract": {"id": snapshot.contract_id, "version": snapshot.contract_version,
                     "digest": snapshot.contract_digest,
                     "relevant_sections": list(["protocol"] if relevant_sections is None
                                               else relevant_sections)},
        "obligations": _obligations(snapshot, states, blockers, metadata, stale),
        "residuals": _residuals(snapshot, derivation, blockers, metadata),
        "freshness": {"changes": changes}, "children": children,
        "next_actions": bootstrap["next_actions"],
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
    audit_children = [c for c in snapshot.state.children if c["resource_class"] == "audit"]
    state = ("passed" if audits and audits[-1]["verdict"] == "pass" else
             "failed" if audits else
             "pending" if any(c["state"] in {"reserved", "launching", "pending"}
                              for c in audit_children) else "required")
    verdict = audits[-1] if audits else {}
    auditor, producers = audit_attributions(snapshot, verdict)
    auditor_class = identity_pair(auditor)
    producer_classes = [identity_pair(producer) for producer in producers]
    if auditor_class is None or not producer_classes or any(value is None for value in producer_classes):
        independence = "unverified"
    elif len(set(producer_classes)) != 1:
        independence = "unverified"
    elif auditor_class == producer_classes[0]:
        independence = "same_model"
    else:
        independence = "distinct"
    return {"state": state, "independence": independence,
            "reviewed_argument_digest": verdict.get("argument_digest"),
            "reviewed_goal_digest": verdict.get("goal_digest"),
            "reviewed_frozen_scope_digest": verdict.get("frozen_scope_digest"),
            "reviewed_deferred_scope_digest": verdict.get("deferred_scope_digest"),
            "reviewed_claims": governance.plain(verdict.get("reviewed_claims", []))}


def project_argument(snapshot: EvaluationSnapshot) -> dict[str, Any]:
    if snapshot.graph is None:
        raise ValueError("argument projection requires a valid graph")
    gating, deferred = _scope(snapshot)
    changes, _ = _freshness(snapshot)
    del changes
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
        "goal_digest": digest(snapshot.state.goal), "frozen_scope_digest": frozen_digest,
        "deferred_scope_digest": digest(deferred),
        "untrusted_delimiters": {"open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
                                 "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"},
        "claims": claims, "edges": [dict(edge) for edge in snapshot.graph["edges"]], "artifacts": artifacts,
        "audit": _audit(snapshot),
    })
