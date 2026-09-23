"""Host-private governance transactions over the same manifest/CAS boundary."""
from __future__ import annotations

from dataclasses import replace

from core import governance as policy
from core.evaluation import artifact, valid_graph, frozen_scope_invalid
from core.records import Corrupt
from .location import decode_handle
from .run_state import classify_and_decode
from .snapshot import HistoryCorrupt


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
                    return c._inert_with_run(rid, snapshot)
                if reason:
                    return c._block_from_snapshot(snapshot, rid, reason)
                outcome = payload["outcome"]
                if outcome == "approve":
                    if snapshot.graph is None:
                        return c._block_from_snapshot(snapshot, rid, "graph.missing")
                    reason = policy.configuration_error(state, governed["proposal"]) or policy.selection_error(governed)
                    if reason:
                        return c._block_from_snapshot(snapshot, rid, reason)
                    governed.update(state="approved", approved_digest=governed["proposal_digest"],
                                    approval_kind=payload["approval_kind"])
                    next_state = replace(state, modes=governed["proposal"]["modes"],
                                         budgets={**state.budgets, **governed["proposal"]["budgets"]})
                elif outcome == "amend":
                    amendment = payload["amendment"]
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
                elif outcome == "reject":
                    governed["state"] = "rejected"
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
