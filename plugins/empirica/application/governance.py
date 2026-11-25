"""Host-private governance transactions over the same manifest/CAS boundary."""
from __future__ import annotations

from dataclasses import replace

from core import confirmation, environment, governance as policy, hitl
from core.evaluation import bootstrap_precondition
from core.records import Corrupt
from .location import decode_handle
from .run_state import classify_and_decode
from .snapshot import HistoryCorrupt
from . import protocol as _proto


def _with_document(response: dict, *, run_id: str, snapshot) -> dict:
    """ADR-0072 (Q1, PR 07): the private ``governance_context`` presentation result carries the
    rendered ``document: {text, digest}`` beside the private dialog — the pair the core hands the
    adapter, so no adapter re-renders a dict. Omitted where this state's configuration judgment
    is not a human's now (there is no document to fetch); the decision legs never carry it — a
    refusal's own presentation is the dialog alone, and the fetch is a fresh context ask."""
    presentation = response["result"].get("presentation")
    if presentation is not None:
        document = hitl.configuration_pair(run_id=run_id, snapshot=snapshot)
        if document is None:
            presentation.pop("document", None)
        else:
            presentation["document"] = document
    return response


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
                payload["delegation"] = state.invocation["delegation"]
                payload["interactive"] = state.invocation["interactive"]
                governed = policy.revise(state.goal, governed, context=payload)
                if governed == policy.plain(state.governance):
                    return _with_document(
                        c._inert_with_run(rid, snapshot, presentation=True),
                        run_id=run_id, snapshot=snapshot)
            else:
                reason = policy.decision_error(run_id, governed, payload,
                                             confirmation.interactions(snapshot), state.budgets)
                if reason == "inert":
                    # Historical and raw-submission receipts replay before current admission rules.
                    return c._inert_with_run(rid, snapshot, presentation=True)
                if missing := bootstrap_precondition(snapshot, "governance.present"):
                    return c._block_from_snapshot(snapshot, rid, missing[1], presentation=True)
                if reason:
                    return c._block_from_snapshot(snapshot, rid, reason, presentation=True)
                decision = payload
                superseded_budgets = None  # the amend branch's priors; null for every other outcome
                if "submission" in payload:
                    resolved, reason = policy.resolve_submission(
                        governed, payload["submission"], _proto.governance_decisions())
                    if reason:
                        return c._block_from_snapshot(snapshot, rid, reason, presentation=True)
                    decision = {**payload, **resolved}
                    if "configuration" in decision:
                        decision["amendment"] = decision.pop("configuration")
                outcome = decision["outcome"]
                if outcome == "approve":
                    reason = policy.configuration_error(state, governed["proposal"])
                    if reason:
                        return c._block_from_snapshot(snapshot, rid, reason, policy.refusal_parameters(governed, reason),
                                                      presentation=True)
                    governed.update(state="approved", approved_digest=governed["proposal_digest"],
                                    approval_kind=payload["approval_kind"], first_approval=True)
                    next_state = replace(state,
                                         budgets={**state.budgets, **governed["proposal"]["budgets"]})
                elif outcome == "amend" and "amendment" in decision:
                    # A human amendment changes ceilings only and keeps the rationale (ADR-0063).
                    proposed = {**decision["amendment"],
                                "rationale": governed["proposal"]["rationale"]}
                    if reason := policy.configuration_error(state, proposed):
                        return c._block_from_snapshot(snapshot, rid, reason, policy.refusal_parameters(governed, reason),
                                                      presentation=True)
                    if reason := policy.revision_error(governed, state.goal, proposed):
                        return c._block_from_snapshot(snapshot, rid, reason, presentation=True)
                    # ADR-0072: the priors the confirm form shows as "(was …)" must be read from the
                    # persisted state — the proposal the human actually amended — BEFORE the revise
                    # below replaces it; afterwards they can be derived from nowhere.
                    superseded_budgets = policy.plain(
                        {key: state.governance["proposal"]["budgets"][key]
                         for key in policy.CEILINGS})
                    governed = policy.revise(state.goal, governed, proposal=proposed)
                if outcome == "reject":
                    governed.update(state="rejected")
                # ADR-0072 (Q3, PR 07; the contract step PR 13): every existing refusal wins
                # first — replay, staleness, interaction ceilings, budget and revision errors —
                # and the document check comes last, spending nothing: a present bound to another
                # state's document never writes a receipt, and a decision must name the document
                # its own present bound. The wire already requires the member for every host_ui
                # envelope, so the gate reads it unconditionally.
                # A receipt whose payload diverges from its own present, digest included, is the
                # mixed envelope the decision gate refuses first as governance.receipt_replay
                # (review-2 7a), so the prior-digest clause below is defense in depth only.
                prior = next((r for r in governed["receipts"]
                              if r["id"] == payload["receipt_id"]), None)
                if decision["approval_kind"] == "host_ui":
                    given = decision["document_digest"]
                    expected = hitl.document_digest(
                        hitl.configuration_for(run_id=run_id, snapshot=snapshot))
                    if (expected != given
                            # defense in depth: the decision gate refuses a digest that
                            # differs from its present's recorded one first as
                            # governance.receipt_replay; this clause keeps the recorded binding
                            # refuse-closed even for an unvalidated direct transact caller
                            or (prior is not None and prior.get("document_digest") != given)):
                        return c._block_from_snapshot(
                            snapshot, rid, "governance.document_mismatch", presentation=True)
                else:
                    given = None
                fingerprint = policy.canonical_digest(payload)
                if prior is None:
                    byte_digest = given
                    governed["receipts"].append({"id": payload["receipt_id"], "fingerprint": fingerprint,
                        "presentation_fingerprint": fingerprint if outcome == "present" else None,
                        "outcome": outcome, "plan_revision": payload["plan_revision"],
                        "proposal_digest": payload["proposal_digest"],
                        "approval_kind": payload["approval_kind"],
                        "superseded_budgets": superseded_budgets,
                        "document_digest": byte_digest})
                else:
                    prior.update(fingerprint=fingerprint, outcome=outcome,
                                 superseded_budgets=superseded_budgets)
            if governed["plan_revision"] != state.governance["plan_revision"]:
                # Spec §5 (mutual exclusion, both ways): this transaction's own revision paths — the
                # human amendment above and the host context report below are the other one — admit
                # a changed configuration and close a pending environment revision as superseded
                # without approval, exactly as the author configure_run does: the member row,
                # receipts and all, moves to the immutable history, so a reply of the closed
                # revision can never approve under the unapproved configuration that follows.
                member, history = environment.superseded_member(state)
                next_state = replace(next_state, environment=member,
                                     environment_history=history)
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
        response = c._allow(rid, planned, presentation=True)
        if context:
            return _with_document(response, run_id=run_id, snapshot=planned)
        return response
    return c._fault(rid, "conflict")


def transact_environment(coordinator, run_id: str, payload: dict) -> dict:
    """The environment's private receipt transaction (Empirica 7.2 §5) — the same write-first
    verification path as the configuration decisions above, one receipt engine with it: the
    binding and replay classification are ``core.environment.receipt_error`` over
    ``core.governance.replay_verdict`` (the entire reply's fingerprint, its own recorded
    presentation), and this loop is the same read-verify-commit boundary — every existing
    refusal (replay, staleness, an altered receipt, the decided member) wins before anything is
    written, then the presentation row or the finalizing member-and-row commit in one CAS. E8
    completes this same path with the environment document's render: a decision already names the
    document its own presentation recorded (the entire-reply fingerprint), and E8's renderer E8's renderer verifies that digest against
    the document the core renders, exactly as the configuration legs above verify theirs, so no
    second verification path is ever opened.

    As above governance: a corrupt or undecodable run fails closed with a runless Fault, an
    absent, terminal or otherwise inactive run is read as nobody's answer, and the CAS retry
    replans against whatever the repository now holds — a superseding revision means the reply
    is classified (stale, or an exact replay of an earlier revision's stored row) and commits
    nothing."""
    c, rid = coordinator, "trusted-environment"
    key = decode_handle(run_id)
    if key is None:
        return c._inert(rid)
    for _ in range(8):
        read = c.runs.read(key)
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
        try:
            environment.validate_decision(dict(payload))
            reason = environment.receipt_error(run_id, state, payload)
            if reason == "inert":
                return c._inert_with_run(rid, snapshot)
            if reason:
                return c._fault_with_run(rid, snapshot)  # fail closed: nothing decides, nothing written
            fingerprint = policy.canonical_digest(policy.plain(dict(payload)))
            member = policy.plain(state.environment)
            if payload["outcome"] == "present":
                member = environment.presented_member(member, payload, fingerprint)
            else:
                member = environment.decided_member(member, payload, fingerprint)
            committed, _, _, planned = c._commit(
                key, read.revision, snapshot, replace(state, environment=member), ())
        except ValueError:
            return c._fault(rid, "invalid_request")
        except Exception as exc:
            if c._is_conflict(exc):
                continue
            return c._fault(rid, "unavailable")
        c.last_state = committed
        return c._allow(rid, planned)
    return c._fault(rid, "conflict")
