"""Pure bounded public projections from an evaluation snapshot."""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from . import citations, confirmation, governance

from .evaluation import (CONFIRMATION_REASON, EvaluationSnapshot, HOST_TIER_UNSUPPORTED, TERMINAL_STOP_REASON,
                         active_evidence, audit_blocker, audit_covers, audit_pending, bootstrap_status, latest_audit_verdict,
                         claim_blockers, claim_digest, confirmation_batch, derive_claims,
                         derive_claims_for_projection, digest, dossier_artifact_ids, effective_scope_ids,
                         evidence_digest, independence, pending_decisions, pending_human, pending_terminal,
                         progress_basis, report_requirements, terminal_batch, REPORT_STAGES, Requirement)


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


ASKABLE = confirmation.ASKABLE


#: The recovery actions the confirmation rule owns (adds, keeps or drops); no mode's recovery exclusion may name one.
CONFIRMATION_ACTIONS = frozenset({"convergence.report", "human.confirm_evidence"})


def confirmation_next(actions: list[str], route: str | None, prompt_error: str | None) -> list[str]:
    """A confirmation row's recovery actions: the one owner of ``human.confirm_evidence`` and ``convergence.report``.

    With no human to ask (route ``unavailable``) nor yet known to be askable (``unobserved``) ``human.confirm_evidence``
    is not offered. ``convergence.report`` leads exactly when a report would ask a human (``prompt_error`` in
    ``ASKABLE``), and is absent once the batch was shown, the interactions are spent, nobody can be asked, or the host
    reported that it could not ask: the host presents inside the author's next report, so a reader that follows ``next:``
    must be told to make it, and must never be told to repeat a report that asks nobody."""
    drop = CONFIRMATION_ACTIONS if route in {confirmation.UNAVAILABLE, confirmation.UNOBSERVED} else {"convergence.report"}
    rest = [action for action in actions if action not in drop]
    return ["convergence.report", *rest] if prompt_error in ASKABLE else rest


def confirmation_actions(code: str, parameters: Mapping[str, Any] | None, actions: list[str],
                         batch: Mapping[str, Any] | None) -> list[str]:
    """``confirmation_next`` for a confirmation reason (the evidence batch, or the consent to end a deliberative run); every
    other row is unchanged. This is the one place that decides the no-batch case: a confirmation reason with no batch (a
    terminal run, or one that cannot present) is as if nobody can be asked, so it never leads with a report or offers
    ``human.confirm_evidence``."""
    if code not in (CONFIRMATION_REASON, TERMINAL_STOP_REASON):
        return actions
    prompt_error = batch["prompt_error"] if batch is not None else "unavailable"
    if (parameters or {}).get("case") == "declined":
        # The human has just answered no: the agent is not sent to ask again by itself. The human's own word ("stop")
        # does, and the batch stays askable for that request (``prompt_error`` is not changed).
        prompt_error = "already_presented"
    elif code == CONFIRMATION_REASON and prompt_error == "already_presented":
        # Shown and left open, with interactions left (the ceiling outranks "already shown"): an explicit report asks
        # again, so the recovery leads with it, like the reason's text.
        prompt_error = None
    return confirmation_next(actions, (parameters or {}).get("route"), prompt_error)


def project_prompt_error(result: Mapping[str, Any], prompt_error: str | None,
                         reasons: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The response the core would have projected had the batch's ``prompt_error`` been this one, for a host that knows
    something about its own call that the response it holds does not: the batch, and the recovery actions of every
    confirmation obligation row and reason (not the residuals, which exist only on a terminal run, where there is no
    batch), through the same rule (``confirmation_actions``). A result with no batch is returned as it is. Pure; it
    changes nothing the core stores. The rows it patches already carry the mode's recovery exclusions, which never name a
    confirmation action, so it need not apply them."""
    run = result.get("run")
    batch = run.get("confirmation_batch") if isinstance(run, Mapping) else None
    if batch is None:
        return dict(result)
    terminal = confirmation.is_terminal(batch)
    # The terminal question is spent per request: a shown one is not "already presented", it is a declined end.
    projected_batch = {**batch, "prompt_error": None if terminal and prompt_error == "already_presented" else prompt_error}

    def obligation(row: Mapping[str, Any]) -> dict[str, Any]:
        missing = row["missing"] or {}
        return {**row, "next": confirmation_actions(
            missing.get("code", ""), missing.get("parameters"), row["next"], projected_batch)}

    def reason(row: Mapping[str, Any]) -> dict[str, Any]:
        row = _recased(row, prompt_error, reasons)
        return {**row, "next_actions": confirmation_actions(
            row["code"], row["parameters"], row["next_actions"], projected_batch)}

    projected = {**run, "confirmation_batch": projected_batch,
                 "obligations": {**run["obligations"], "active": [obligation(r) for r in run["obligations"]["active"]]}}
    projected_reasons = [reason(r) for r in result.get("reasons", ())]
    return {**result, "run": projected, **({"reasons": projected_reasons} if "reasons" in result else {})}


def _recased(row: Mapping[str, Any], prompt_error: str | None, reasons: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """The confirmation or terminal-step reason with the case (and, given the contract's reasons, the text) that is true for this
    ``prompt_error``; every other row as it is."""
    if row["code"] == CONFIRMATION_REASON:
        parameters = {**{k: v for k, v in row["parameters"].items() if k != "asking"}, **confirmation.asking(prompt_error)}
        text = confirmation.reason_text(reasons[row["code"]], parameters) if reasons else row.get("message")
        return {**row, "parameters": parameters, **({"message": text} if text is not None else {})}
    if row["code"] != TERMINAL_STOP_REASON:
        return row
    parameters = {**row["parameters"], "case": confirmation.terminal_case(
        prompt_error, row["parameters"].get("case") == "declined")}
    text = confirmation.reason_text(reasons[row["code"]], parameters) if reasons else row.get("message")
    return {**row, "parameters": parameters, **({"message": text} if text is not None else {})}


def project_host_failure(result: Mapping[str, Any], host_failure: str,
                         reasons: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """``project_prompt_error`` for what the host reports about its own call, the way the core applies it when it is
    told (``confirmation.apply_host_report``): a refusal the core already knows stands."""
    run = result.get("run")
    batch = run.get("confirmation_batch") if isinstance(run, Mapping) else None
    return project_prompt_error(result, confirmation.apply_host_report(batch["prompt_error"], host_failure), reasons) \
        if batch is not None else dict(result)


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


def _observed(snapshot: EvaluationSnapshot, item: Mapping[str, Any], stale: set[str]) -> dict[str, Any]:
    """One active evidence record as an obligation shows it; ``confirm`` names the route of a
    research record that still needs confirming, from the moment it is recorded."""
    row = {"artifact_id": item["artifact_id"], "kind": item["kind"], "outcome": item["outcome"],
           "stale": item["kind"] == "spike" and item["artifact_id"] in stale}
    if item["kind"] != "research":
        return row
    route = confirmation.pending_route(snapshot, item)
    decided = confirmation.receipt(snapshot, item)
    return {**row, **({"confirm": route} if route is not None else {}),
            **({"note": decided["note"]} if decided is not None and "note" in decided else {})}


def _rejected(snapshot: EvaluationSnapshot, claim: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Research a human rejected for this wording of the claim: no longer evidence, but the human's note
    is addressed to the author and belongs on the obligation it left open."""
    digest_now = claim_digest(claim)
    rows = []
    for item in snapshot.history:
        if (item.get("kind") == "research" and item.get("claim_id") == claim["id"]
                and item.get("claim_digest") == digest_now and confirmation.rejected(snapshot, item)):
            decided = confirmation.receipt(snapshot, item)
            rows.append({"artifact_id": item["artifact_id"], "kind": "research", "outcome": item["outcome"],
                         "stale": False, "rejected": True,
                         **({"note": decided["note"]} if "note" in decided else {})})
    return rows


def _obligations(snapshot: EvaluationSnapshot, derivation: Any, states: Mapping[str, str],
                 blockers: tuple[dict[str, Any], ...],
                 metadata: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]],
                 stale: set[str], terminal_next: list[str] | None = None,
                 ) -> dict[str, list[dict[str, Any]]]:
    state = snapshot.state
    late = (snapshot.command["type"] == "ObserveAction"
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
    # What the report requires, in the gate's order (``core.evaluation.report_requirements``): the claim rows below are its
    # claim stage, the rows after them its human, pass, audit and consent stages, and a row behind an earlier stage is held.
    owed = report_requirements(snapshot, derivation, blockers)
    early = min((REPORT_STAGES.index(r.stage) for r in owed), default=None)
    if snapshot.graph:
        for claim in snapshot.graph["claims"]:
            blocker = blockers_by_claim.get(claim["id"])
            missing = (None if blocker is None else {
                "code": blocker["reason"], "target_claim_id": blocker["target_claim_id"],
                "parameters": blocker["parameters"]})
            observed = [_observed(snapshot, item, stale) for item in active_evidence(snapshot, claim)]
            observed += _rejected(snapshot, claim)
            row = _obligation(
                "claim:" + claim["id"], claim["text"], observed, missing, metadata,
                "satisfied" if states[claim["id"]] == "approved" else "residual", terminal_next)
            if claim["id"] in deferred:
                row["hold"] = "deferred"
            elif terminal_next is None and blocker is not None and early is not None and REPORT_STAGES.index(
                    "confirmation" if blocker["reason"] == CONFIRMATION_REASON else "claim") > early:
                row["hold"] = "blocked"
            active.append(row)
        rows = {r.obligation: r for r in owed if r.stage in {"confirmation", "passes", "audit", "terminal"}
                and not r.obligation.startswith("claim:")}
        for obligation_id, required in (snapshot.stage_obligations[s] for s in ("confirmation", "passes")):
            if obligation_id in rows:
                active.append(_requirement_row(rows[obligation_id], metadata, terminal_next))
        # The audit position: satisfied when the gate has nothing to say there, else what the gate says (held while an
        # earlier stage is unmet).
        obligation_id, required = snapshot.audit_obligation
        active.append(_requirement_row(rows[obligation_id], metadata, terminal_next) if obligation_id in rows else
                      _obligation(obligation_id, required, [], None, metadata, "satisfied", terminal_next))
        consent = rows.get(snapshot.stage_obligations["terminal"][0])
        if consent is not None:
            active.append(_requirement_row(consent, metadata, terminal_next))
    return {"active": active, "deferred": []}


def _requirement_row(requirement: Requirement, metadata, terminal_next: list[str] | None) -> dict[str, Any]:
    """The obligation a stage of the report is listed as: what the gate would answer there (its reason, parameters), held
    while an earlier stage is unmet."""
    row = _obligation(requirement.obligation, requirement.required, [],
                      {"code": requirement.reason, "target_claim_id": None, "parameters": dict(requirement.parameters)},
                      metadata, "residual", terminal_next)
    return row if requirement.admissible or terminal_next is not None else {**row, "hold": "blocked"}


def _residuals(snapshot: EvaluationSnapshot, derivation: Any,
               blockers: tuple[dict[str, Any], ...],
               metadata: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]],
               terminal_next: list[str] | None = None) -> list[dict[str, Any]]:
    status = snapshot.state.status
    _, deferred = _scope(snapshot)
    residuals: list[dict[str, Any]] = []
    if snapshot.state.frozen_claim_ids is not None and deferred:
        # What the freeze left out, and then what the required claims it kept still lack (a stop does not resolve them).
        # The scope is an annotation: it never stands in for a stage of the report (the budget, the audit) left unmet.
        residuals.append(_residual(metadata, "freeze.deferred", {
            "claim_ids": deferred, "deferred_scope_digest": digest(deferred)}, terminal_next))
    if status == "stopped_budget":
        return [*residuals, _residual(metadata, "budget.exhausted", {"resource": "pass"}, terminal_next)]
    if status in {"stopped_residual", "stopped_frozen"} and snapshot.graph:
        owed: list[dict[str, Any]] = []
        for claim_id, records in pending_human(snapshot, _scope(snapshot)[0]).items():
            owed.append(_residual(
                metadata, "evidence.confirmation_required",
                confirmation.parameters(snapshot, records, pending_decisions(snapshot, derivation)),
                terminal_next,
                claim_id=claim_id, target_claim_id=claim_id))
        for blocker in blockers:
            owed.append(_residual(
                metadata, blocker["reason"], blocker["parameters"], terminal_next,
                claim_id=blocker["claim_id"], target_claim_id=blocker["target_claim_id"]))
        residuals += owed
        if not owed:
            blocker = audit_blocker(snapshot, derivation)
            if blocker:
                residuals.append(_residual(metadata, blocker["reason"], blocker["parameters"], terminal_next))
    return residuals


_HIDDEN = re.compile(r"[\\\x00-\x1f\x7f-\x9f\u00ad\u061c\u200b-\u200f\u2028-\u202e\u2060\u2066-\u2069\ufeff]")


def safe_text(item: object) -> str:
    def escape(match):
        code = ord(match.group())
        return "\\\\" if code == 92 else (f"\\x{code:02x}" if code <= 255 else f"\\u{code:04x}")
    return _HIDDEN.sub(escape, str(item))


# The neutral invocation facts a run view and the governance dialog show. The stored invocation may
# carry further host-recorded members (``host_runtime``) that are not projected here (see ``project_invocation``).
INVOCATION_FACTS = ("host", "interactive", "signal", "delegation", "audit_retrieval", "human_surface")


def project_invocation(invocation: Mapping) -> dict[str, Any]:
    """The shown invocation facts, in contract order; host-recorded extras (``host_runtime``) are not projected here,
    except ``package`` and ``version`` of an unreviewed runtime, which ``_unreviewed_runtime`` projects into the run view."""
    return {key: governance.plain(invocation[key]) for key in INVOCATION_FACTS}


def governance_dialog(goal: str, value: Mapping, controls: Mapping,
                      invocation: Mapping | None = None) -> dict[str, Any]:
    """Project governance state into host-neutral, display-safe dialog data.

    The author sizing rationale is carried as neutral escaped text; hosts render it
    under the unverified label (ADR-0063). It is not a command, authority claim, or
    approvable conclusion."""
    proposal = value["proposal"]
    budgets = [
        {"key": key, "label": controls["budgets"][key]["label"],
         "short": controls["budgets"][key]["short"], "help": controls["budgets"][key]["help"],
         "value": proposal["budgets"][key], "used": value["budgets"][used_key],
         "minimum": max(1 if key == "max_passes" else 0, value["budgets"][used_key]),
         "maximum": controls["budgets"][key]["maximum"]}
        for key, used_key in governance.CEILINGS.items()
    ]
    rationale = proposal["rationale"]
    return {"epoch": value["plan_revision"], "control_mode": value["control_mode"],
            "state": value["state"], "reviews_left": {
                "proposal": value["interactions_remaining"]["proposal"],
                "total": value["interactions_remaining"]["total"]},
            "goal": safe_text(goal),
            "rationale": None if rationale is None else safe_text(rationale),
            "rationale_label": controls["rationale_label"],
            "amendment_warning": controls["amendment_warning"],
            "invocation": None if invocation is None else {
                key: safe_text(value) if key in {"host", "signal"} else value
                for key, value in project_invocation(invocation).items()},
            "budgets": budgets}


def governance_facts(state: Any, spent: int | None) -> dict:
    """What a run's governance is, from its state alone: the configuration, the ceilings and what is left of them, and the
    human interactions left (``spent`` is the evidence presentations counted from the history; ``None`` when that history
    was not read, so the interactions are unknown, never a full allowance). The one source of these facts: the run view
    adds to it (``project_governance``) and the observer shapes it."""
    value = governance.plain(state.governance)
    known = spent is not None
    value.update(interactions_remaining=governance.interactions_remaining(state.governance, spent) if known else None,
                 prompt_error=governance.interaction_error(state.governance, spent) if known else None)
    value.pop("receipts")
    value.pop("delegation_envelope")
    value.update(budgets=dict(state.budgets),
                 remaining={ceiling: state.budgets[ceiling] - state.budgets[used]
                            for ceiling, used in governance.CEILINGS.items()})
    return value


def project_governance(snapshot: EvaluationSnapshot) -> dict:
    value = governance_facts(snapshot.state, confirmation.interactions(snapshot))
    bootstrap = bootstrap_status(snapshot)
    actions = bootstrap["next_actions"]  # empty for a terminal run or when bootstrap is complete
    value.update(request_ready=bootstrap["request_ready"], display_ready=bootstrap["display_ready"],
                 next_action=actions[-1] if actions else snapshot.default_next_action,
                 # who acts on the configuration: the one predicate, published so a host reads it, never re-derives it
                 human_wait=governance.human_wait(value, value["prompt_error"]))
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


def recovery_actions(excluded_actions: tuple[str, ...], actions: list[str]) -> list[str]:
    """Apply an admitted neutral exclusion policy to one recovery-action list."""
    return [action for action in actions if action not in excluded_actions]


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
    obligations = _obligations(snapshot, derivation, states, blockers, metadata, stale, terminal_next)
    residuals = _residuals(snapshot, derivation, blockers, metadata, terminal_next)
    control_mode = snapshot.state.governance["control_mode"]
    exclusions = snapshot.contract.recovery_exclusions[control_mode]
    batch = _batch(snapshot, derivation)
    for residual in residuals:
        residual["next_actions"] = recovery_actions(exclusions, confirmation_actions(
            residual["code"], residual["parameters"], residual["next_actions"], batch))
    for obligation in obligations["active"]:
        missing = obligation["missing"] or {}
        # Nothing is asked of the author for a requirement that waits on an earlier one (the report would not name it).
        obligation["next"] = ([] if obligation.get("hold") == "blocked" else recovery_actions(
            exclusions, confirmation_actions(missing.get("code", ""), missing.get("parameters"), obligation["next"], batch)))
    recovery_action = terminal_next[0] if terminal else "child.retry"
    children = [_child_summary(child, recovery_action) for child in snapshot.state.children]
    audit = _audit(snapshot)
    return {
        "id": snapshot.run_id, "goal": snapshot.state.goal,
        "invocation": project_invocation(governance.plain(snapshot.state.invocation)),
        "status": snapshot.state.status,
        "basis": progress_basis(snapshot, blockers),
        # A failed verdict's findings are what the author must act on; pass findings stay in GetArgument.
        "audit": {key: audit[key] for key in ("verdict", "current", "pending", "independence")}
                 | {"findings": audit["findings"] if audit["verdict"] == "failed" else []},
        "governance": project_governance(snapshot),
        "confirmation_batch": batch,
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
                    if snapshot.host_tier in HOST_TIER_UNSUPPORTED else []),
                 **_unreviewed_runtime(snapshot.state.invocation)},
        # Selected exactly once at StartRun. This is the immutable run binding, not current
        # authority-pointer or Docker health, so this pure projection performs no discovery.
        "spike_backend": governance.plain(snapshot.state.spike_backend),
    }


def _unreviewed_runtime(invocation: Mapping) -> dict[str, Any]:
    """``{"unreviewed_runtime": {package, version}}`` for an audit runtime recorded at StartRun without a compat
    record (``reviewed`` false or absent; ADR-0069), else nothing. ``reviewed`` is the adapter's claim: the run view
    reads it and nothing here verifies it."""
    subagents = ((governance.plain(invocation).get("host_runtime") or {}).get("subagents") or {})
    if subagents.get("reviewed") is True or not subagents:
        return {}
    return {"unreviewed_runtime": {"package": subagents["package"], "version": subagents["version"]}}


def _batch(snapshot: EvaluationSnapshot, derivation: Any) -> dict[str, Any] | None:
    """What a human would be shown now (``null`` when nothing waits), with why it cannot be presented:
    the host presents when this is not ``null`` and ``prompt_error`` is ``null`` (a report asks inside the call; one
    whose ``prompt_error`` is ``unobserved`` first observes its surface, so it is null by the time it is evaluated;
    see ``confirmation_next``), whatever else blocks."""
    if snapshot.graph is None or snapshot.state.status != "active":
        return None
    batch = confirmation_batch(snapshot, derivation)
    # The terminal step of a deliberative run (ADR-0063 amendment) is asked by the evaluation that reaches it: a stop
    # asks it whatever evidence is pending (moot, the run is ending), a report only once nothing else is pending. The
    # view of any other command shows the pending evidence batch, if any, and never an end nobody has asked for.
    command = snapshot.command
    if command.get("type") == "EvaluateRun" and (
            status := pending_terminal(snapshot, command.get("intent", ""))) is not None:
        batch = terminal_batch(snapshot, derivation, status)
    if batch is None:
        return None
    held = {a["artifact_id"]: a for a in snapshot.history if a.get("kind") == "research"}
    return {"key": batch["key"], "record_ids": batch["record_ids"],
            # A terminal step asks about the run, not about a claim of the argument: its status is in ``terminal``.
            "claim_ids": [] if confirmation.is_terminal(batch) else [row["claim_id"] for row in batch["claims"]],
            **({"terminal": batch["terminal"]} if confirmation.is_terminal(batch) else {}),
            # What the author reads of the batch: where each record is cited from and for which claim.
            "records": [{"claim_id": held[i]["claim_id"], "source_kind": held[i]["source_kind"],
                         "source_ref": held[i]["source_ref"]} for i in batch["record_ids"] if i in held],
            "prompt_error": confirmation.prompt_error(snapshot, batch["key"], terminal=confirmation.is_terminal(batch))}


def _audit(snapshot: EvaluationSnapshot) -> dict[str, Any]:
    """The one summary of the audit, from the predicates ``audit_blocker`` uses, so it and the audit obligation row cannot
    disagree. Four facts, never one overloaded word: the latest recorded ``verdict`` (``passed``, ``failed`` or ``none``),
    its ``current``cy (it still binds the current argument, goal and scope; ``None`` with no verdict), whether a
    re-audit is ``pending`` (a current child that has not settled), and the ``independence`` of a settled verdict
    (``None`` otherwise). Only a settled audit reports what it reviewed and what it found: a pending re-audit supersedes
    the previous verdict's coverage."""
    latest = latest_audit_verdict(snapshot)
    pending = audit_pending(snapshot)
    settled = latest is not None and not pending
    verdict = "none" if latest is None else "passed" if latest.get("verdict") == "pass" else "failed"
    current = None if latest is None else audit_covers(snapshot, latest)
    seen = latest if settled else {}
    return {"verdict": verdict, "current": current, "pending": pending,
            "independence": independence(snapshot, latest) if settled else None,
            "reviewed_argument_digest": seen.get("argument_digest"),
            "reviewed_goal_digest": seen.get("goal_digest"),
            "reviewed_frozen_scope_digest": seen.get("frozen_scope_digest"),
            "reviewed_deferred_scope_digest": seen.get("deferred_scope_digest"),
            "reviewed_claims": governance.plain(seen.get("reviewed_claims", [])),
            # Findings belong to the verdict that produced them: a pass that no longer covers the argument has nothing
            # to address, so it reports none.
            "findings": governance.plain(seen["findings"] if seen and (verdict == "failed" or current) else [])}


def _verification(snapshot: EvaluationSnapshot, record: Mapping[str, Any]) -> dict[str, Any]:
    """The dossier's statement of how one research record is verified and what the auditor owes it."""
    row = {"class": citations.verification_class(record["source_kind"], record["source_ref"]),
           "status": confirmation.status(snapshot, record),
           "retrieve": confirmation.retrieve(snapshot, record)}
    decided = confirmation.receipt(snapshot, record)
    if decided is None:
        return row
    # The human's note is addressed to the author, so the dossier carries it with the receipt.
    return {**row, "receipt": decided["artifact_id"], **({"note": decided["note"]} if "note" in decided else {})}


def _human_decision(snapshot: EvaluationSnapshot, claim: Mapping[str, Any]) -> dict[str, Any]:
    """A ``needs-decision`` claim's human answer, when there is one, for the claim row."""
    answer = confirmation.decision(snapshot, claim["id"], claim_digest(claim))
    if answer is None:
        return {}
    return {"human_decision": {"outcome": answer["outcome"], "answer": answer.get("answer")}}


def spike_result(art: Mapping[str, Any], active: bool) -> dict[str, Any]:
    """The dossier's statement of one executed spike: what ran, how it ended, and the files it was bound to. The one place
    these facts are read from the artifact (``project_argument`` and the observer both); the command's output is not retained."""
    return {"active": active, "outcome": art["outcome"], "harness_request_id": art["harness_request_id"],
            "command": art["command"], "command_digest": art["command_digest"],
            "prerequisite_research_ids": art["prerequisite_research_ids"], "file_bindings": art["file_bindings"],
            "exit_code": art["exit_code"], "spike_gate": art["spike_gate"], "supersedes": art.get("supersedes")}


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
            "gating": claim["id"] in gating, "evidence_digest": evidence_digest(snapshot, claim),
            "active_evidence_ids": ids,
            **_human_decision(snapshot, claim),
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
            common.update({key: art[key] for key in ("observed_content_digest", "spike_unavailable")
                           if key in art})
            common["verification"] = _verification(snapshot, art)
        elif kind == "spike_request":
            common.update(harness_request_id=art["harness_request_id"], command=art["command"],
                          command_digest=art["command_digest"], dependent_files=art["dependent_files"],
                          prerequisite_research_ids=art["prerequisite_research_ids"])
        else:
            common.update(spike_result(art, art["artifact_id"] in active_ids))
        artifacts.append(common)
    frozen_digest = None if snapshot.state.frozen_claim_ids is None else digest(gating)
    argument_digest = digest({"graph": snapshot.graph,
                              "evidence": dossier_artifact_ids(snapshot.history)})
    return governance.plain({
        "root_claim_id": snapshot.graph["root"], "argument_digest": argument_digest,
        "goal": snapshot.state.goal, "goal_digest": digest(snapshot.state.goal),
        "frozen_scope_digest": frozen_digest,
        "deferred_scope_digest": digest(deferred),
        "untrusted_delimiters": dict(snapshot.untrusted_delimiters),
        "claims": claims, "edges": [dict(edge) for edge in snapshot.graph["edges"]], "artifacts": artifacts,
        "route_stamp": snapshot.state.route_stamp,
        "investigation_stamp": snapshot.state.investigation_stamp,
        # The dossier repeats the exact StartRun binding so an auditor can interpret every
        # admitted spike artifact without consulting mutable machine authority.
        "spike_backend": governance.plain(snapshot.state.spike_backend),
        "audit": _audit(snapshot),
    })
