"""Host-private governance transactions over the same manifest/CAS boundary."""
from __future__ import annotations

from dataclasses import replace

from core import governance as policy
from core.evaluation import artifact, bootstrap_precondition, valid_graph, frozen_scope_invalid
from core.records import Corrupt
from .location import decode_handle
from .run_state import classify_and_decode
from .snapshot import HistoryCorrupt
from . import protocol as _proto


def transact(coordinator, run_id: str, payload: dict, *, context: bool = False) -> dict:
    c, rid = coordinator, "trusted-governance"
    key = decode_handle(run_id)
    if key is None:
        return c._inert(rid)
    for _ in range(8):
        read = c.runs.read(key)
        if isinstance(read, Corrupt):
            return c._safe_block(key, rid, "run.corrupt")
        if not c._present(read):
            return c._inert(rid)
        classified = classify_and_decode(read.value)
        if classified.kind != "valid":
            return c._safe_block(key, rid, "run.corrupt")
        state = classified.state
        if state.status != "active":
            return c._inert(rid)
        try:
            snapshot = c._assemble(key, state, {"type": "GetRun", "run_id": run_id}, require_graph=False)
        except HistoryCorrupt:
            return c._safe_block(key, rid, "run.corrupt")
        governed, domain, next_state = policy.plain(state.governance), (), state
        try:
            if context:
                if payload["ingress"] == "mcp_elicitation" and not c.profile_id.startswith("claude-code@"):
                    return c._block_from_snapshot(snapshot, rid, "governance.approval_unavailable")
                if payload["ingress"] == "pi_ui" and not c.profile_id.startswith("pi@"):
                    return c._block_from_snapshot(snapshot, rid, "governance.approval_unavailable")
                payload = policy.plain(payload)
                payload["inventory"]["members"].sort(key=lambda m: (m["provider_id"], m["model_id"]))
                prepared = {**governed, "context": payload}
                governed = policy.revise(state.goal, snapshot.graph, governed, context=payload,
                                          proposal=policy.auto_proposal(prepared))
                if governed == policy.plain(state.governance):
                    return c._inert_with_run(rid, snapshot)
            else:
                reason = policy.decision_error(run_id, governed, payload)
                if reason == "inert":
                    # Historical and raw-submission receipts replay before current admission rules.
                    return c._inert_with_run(rid, snapshot)
                if missing := bootstrap_precondition(snapshot, "governance.present"):
                    return c._block_from_snapshot(snapshot, rid, missing[1])
                if reason:
                    return c._block_from_snapshot(snapshot, rid, reason)
                decision = payload
                if "submission" in payload:
                    resolved, reason = policy.resolve_submission(
                        governed, payload["submission"], _proto._GOVERNANCE_DECISIONS)
                    if reason:
                        return c._block_from_snapshot(snapshot, rid, reason)
                    decision = {**payload, **resolved}
                    if "configuration" in decision:
                        decision["amendment"] = {"graph": snapshot.graph,
                                                   "configuration": decision.pop("configuration")}
                outcome = decision["outcome"]
                if outcome == "approve":
                    reason = policy.configuration_error(state, governed["proposal"]) or policy.selection_error(governed)
                    if reason:
                        return c._block_from_snapshot(snapshot, rid, reason)
                    governed.update(state="approved", approved_digest=governed["proposal_digest"],
                                    approval_kind=payload["approval_kind"], change_request=None)
                    next_state = replace(state, modes=governed["proposal"]["modes"],
                                         budgets={**state.budgets, **governed["proposal"]["budgets"]})
                elif outcome in {"amend", "request_changes"} and "amendment" in decision:
                    amendment = decision["amendment"]
                    graph = amendment["graph"]
                    proposed = amendment["configuration"]
                    if not valid_graph(graph) or frozen_scope_invalid(state, graph):
                        return c._block_from_snapshot(snapshot, rid, "graph.invalid")
                    if reason := policy.configuration_error(state, proposed):
                        return c._block_from_snapshot(snapshot, rid, reason)
                    governed = policy.revise(state.goal, graph, governed, proposal=proposed)
                    art = artifact("graph", {"graph": policy.canonical_graph(graph)})
                    domain = (art,)
                    next_state = replace(state, selected_graph_artifact_id=art["artifact_id"])
                if outcome == "request_changes":
                    governed["change_request"] = {"text": decision["change_request"],
                        "plan_revision": decision["plan_revision"],
                        "proposal_digest": decision["proposal_digest"]}
                elif outcome == "reject":
                    governed.update(state="rejected", change_request=None)
                prior = next((r for r in governed["receipts"] if r["id"] == payload["receipt_id"]), None)
                fingerprint = policy.canonical_digest(payload)
                if prior is None:
                    governed["receipts"].append({"id": payload["receipt_id"], "fingerprint": fingerprint,
                        "presentation_fingerprint": fingerprint if outcome == "present" else None,
                        "outcome": outcome, "plan_revision": payload["plan_revision"]})
                else:
                    prior.update(fingerprint=fingerprint, outcome=outcome)
            next_state = replace(next_state, governance=governed)
            committed, _, _, planned = c._commit(key, read.revision, snapshot, next_state, domain)
        except ValueError as exc:
            if str(exc).startswith("governance."):
                return c._block_from_snapshot(snapshot, rid, str(exc))
            return c._fault(rid, "invalid_request")
        except Exception as exc:
            if c._is_conflict(exc):
                continue
            return c._fault(rid, "unavailable")
        c.last_state = committed
        return c._allow(rid, planned)
    return c._fault(rid, "conflict")
