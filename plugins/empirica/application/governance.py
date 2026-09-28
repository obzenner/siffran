"""Host-private governance transactions over the same manifest/CAS boundary."""
from __future__ import annotations

from dataclasses import replace

from core import governance as policy
from core.evaluation import bootstrap_precondition
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
        # A corrupt/undecodable/history-corrupt run has no usable snapshot, so there is no author
        # RunView to present: fail closed with a runless Fault rather than a synthetic-run Block.
        if isinstance(read, Corrupt):
            return c._fault(rid, "corrupt_run")
        if not c._present(read):
            return c._inert(rid)
        classified = classify_and_decode(read.value)
        if classified.kind != "valid":
            return c._fault(rid, "corrupt_run")
        state = classified.state
        if state.status != "active":
            return c._inert(rid)
        try:
            snapshot = c._assemble(key, state, {"type": "GetRun", "run_id": run_id}, require_graph=False)
        except HistoryCorrupt:
            return c._fault(rid, "corrupt_run")
        governed, domain, next_state = policy.plain(state.governance), (), state
        try:
            if context:
                profile = _proto.host_profile(c.profile_id)
                if payload["ingress"] not in {"unavailable", profile["approval_ingress"]}:
                    return c._block_from_snapshot(snapshot, rid, "governance.approval_unavailable",
                                                  presentation=True)
                payload = policy.plain(payload)
                payload["approval_capability"] = _proto.APPROVAL_CAPABILITY[payload["ingress"]]
                governed = policy.revise(state.goal, governed, context=payload)
                if governed == policy.plain(state.governance):
                    return c._inert_with_run(rid, snapshot, presentation=True)
            else:
                reason = policy.decision_error(run_id, governed, payload)
                if reason == "inert":
                    # Historical and raw-submission receipts replay before current admission rules.
                    return c._inert_with_run(rid, snapshot, presentation=True)
                if missing := bootstrap_precondition(snapshot, "governance.present"):
                    return c._block_from_snapshot(snapshot, rid, missing[1], presentation=True)
                if reason:
                    return c._block_from_snapshot(snapshot, rid, reason, presentation=True)
                decision = payload
                if "submission" in payload:
                    resolved, reason = policy.resolve_submission(
                        governed, payload["submission"], _proto._GOVERNANCE_DECISIONS)
                    if reason:
                        return c._block_from_snapshot(snapshot, rid, reason, presentation=True)
                    decision = {**payload, **resolved}
                    if "configuration" in decision:
                        decision["amendment"] = decision.pop("configuration")
                outcome = decision["outcome"]
                if outcome == "approve":
                    reason = policy.configuration_error(state, governed["proposal"])
                    if reason:
                        return c._block_from_snapshot(snapshot, rid, reason, presentation=True)
                    governed.update(state="approved", approved_digest=governed["proposal_digest"],
                                    approval_kind=payload["approval_kind"])
                    next_state = replace(state, modes=governed["proposal"]["modes"],
                                         budgets={**state.budgets, **governed["proposal"]["budgets"]})
                elif outcome == "amend" and "amendment" in decision:
                    proposed = decision["amendment"]
                    if reason := policy.configuration_error(state, proposed):
                        return c._block_from_snapshot(snapshot, rid, reason, presentation=True)
                    governed = policy.revise(state.goal, governed, proposal=proposed)
                if outcome == "reject":
                    governed.update(state="rejected")
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
                return c._block_from_snapshot(snapshot, rid, str(exc), presentation=True)
            return c._fault(rid, "invalid_request")
        except Exception as exc:
            if c._is_conflict(exc):
                continue
            return c._fault(rid, "unavailable")
        c.last_state = committed
        return c._allow(rid, planned, presentation=True)
    return c._fault(rid, "conflict")
