"""Bounded proposal consent; pure policy shared by every admission path.

Only private host ingress supplies context/decisions. Operational counters live
in OperationalState.budgets, never in proposal metadata.

ADR-0063: run configuration is sized by the author (all three ceilings plus a
nonblank rationale) and approved before investigation in both control modes.
The placeholder StartRun creates is unapprovable; the first successful approval
is a durable fact; auto never raises a ceiling after that approval and is
bounded by two separate eight-revision allowances; delegated auto is capped by a
fixed 8/1/2 envelope that inputs may only narrow.
"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, NamedTuple

from .canonical import canonical_digest as _canonical_digest

MAX_INTERACTIONS = 128
PROPOSAL_INTERACTIONS = 3
# Each auto material-revision allowance (one before the first successful approval,
# counted from the first complete sized proposal; one after it).
MAX_REVISIONS = 8
# The single immutable operational seed is also the fixed delegated-auto policy
# envelope. Application-supplied sources may only narrow it.
DEFAULT_CEILINGS = MappingProxyType(
    {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 2}
)
# Admitted approval capabilities. The application derives these from the contract-owned
# ingress→capability table; core names only the neutral capability it reasons about.
HUMAN_CONFIGURATION = "human_configuration"
UNAVAILABLE_CAPABILITY = "unavailable"
APPROVAL_KINDS = ("host_ui", "auto")


class Budget(NamedTuple):
    used: str
    resource: str


BUDGETS = {"max_passes": Budget("passes_used", "pass"),
           "max_spawns": Budget("spawns_used", "spawn"),
           "max_audit_spawns": Budget("audit_spawns_used", "audit_spawn")}
CEILINGS = {ceiling: budget.used for ceiling, budget in BUDGETS.items()}


def plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value


def canonical_digest(value: object) -> str:
    """Delegate governance values to the core's sole canonical JSON implementation."""
    return _canonical_digest(plain(value))


def canonical_graph(graph: Mapping[str, Any] | None) -> dict | None:
    if graph is None:
        return None
    return {"root": graph["root"],
            "claims": sorted(plain(graph["claims"]), key=lambda c: c["id"]),
            "edges": sorted(plain(graph["edges"]), key=lambda e: (e["from"], e["to"], e["type"]))}


def proposal_body(goal: str, governance: Mapping) -> dict:
    """Bind only the immutable goal context and approvable run configuration.

    The proposal carries the author sizing rationale, so the digest binds the
    ceilings together with the stated reasoning (ADR-0063)."""
    return {"goal": goal, "configuration": plain(governance["proposal"]),
            "control_mode": governance["control_mode"]}


def proposal_digest(goal: str, governance: Mapping) -> str:
    """Own the canonical identity formula for one governance proposal."""
    return canonical_digest(proposal_body(goal, governance))


def sized(governance: Mapping) -> bool:
    """Whether the proposal has been sized by an author configure_run (rationale present)."""
    return governance["proposal"]["rationale"] is not None


def _initial_context(invocation: Mapping[str, Any]) -> dict:
    """Neutral, boundary-validated invocation facts the core reasons about."""
    return {"author": None, "ingress": "unavailable",
            "approval_capability": UNAVAILABLE_CAPABILITY,
            "delegation": invocation["delegation"],
            "interactive": invocation["interactive"]}


def initial(goal: str, budgets: Mapping, control_mode: str,
             invocation: Mapping[str, Any], delegation_envelope: Mapping | None) -> dict:
    """Create the unapprovable StartRun placeholder and durable authority facts."""
    value = {"state": "pending", "control_mode": control_mode,
             "plan_revision": 0, "approved_digest": None,
             "approval_kind": None, "receipts": [],
             "proposal": {"budgets": {k: budgets[k] for k in CEILINGS},
                          "rationale": None},
             "context": _initial_context(invocation),
             "delegation_envelope": (None if delegation_envelope is None else
                                     {k: delegation_envelope[k] for k in CEILINGS}),
             "first_approval": False,
             "pre_approval_revisions": 0, "post_approval_revisions": 0}
    value["proposal_digest"] = proposal_digest(goal, value)
    return value


def _initial_sizing(current: Mapping, proposal: Mapping) -> bool:
    return current["proposal"]["rationale"] is None and proposal["rationale"] is not None


def _material_revision(goal: str, current: Mapping, proposal: Mapping) -> bool:
    """Whether applying ``proposal`` is a counted material revision (not the initial
    complete sized proposal, not an exact no-op replay)."""
    candidate = plain(current)
    candidate["proposal"] = plain(proposal)
    if proposal_digest(goal, candidate) == current["proposal_digest"]:
        return False  # exact no-op replay
    return not _initial_sizing(current, proposal)


def revise(goal: str, current: Mapping, *, proposal=None, context=None) -> dict:
    value = plain(current)
    if proposal is not None:
        value["proposal"] = plain(proposal)
    if context is not None:
        value["context"] = plain(context)
    observed = proposal_digest(goal, value)
    if observed == current["proposal_digest"]:
        return value
    is_initial = _initial_sizing(current, value["proposal"])
    value.update(proposal_digest=observed, plan_revision=current["plan_revision"] + 1,
                 state="revision_pending" if current["approval_kind"] else "pending")
    if value["control_mode"] == "auto" and not is_initial:
        if value["first_approval"]:
            value["post_approval_revisions"] = current["post_approval_revisions"] + 1
        else:
            value["pre_approval_revisions"] = current["pre_approval_revisions"] + 1
    return value


def revision_error(governance: Mapping, goal: str, proposal: Mapping) -> str | None:
    """Refuse an auto proposal/revision that would exceed its material-revision allowance
    before any mutation. Deliberative revisions return to the human and are not capped here."""
    if governance["control_mode"] != "auto":
        return None
    if not _material_revision(goal, governance, proposal):
        return None
    used = (governance["post_approval_revisions"] if governance["first_approval"]
            else governance["pre_approval_revisions"])
    return "governance.revision_exhausted" if used >= MAX_REVISIONS else None


def admission(governance: Mapping) -> str | None:
    if governance["state"] == "approved" and governance["approved_digest"] == governance["proposal_digest"]:
        return None
    return ("governance.revision_required" if governance["approval_kind"] else
            "governance.approval_required")


def interactions_remaining(governance: Mapping) -> dict:
    receipts = governance["receipts"]
    shown = sum(r["plan_revision"] == governance["plan_revision"] for r in receipts)
    return {"proposal": max(0, PROPOSAL_INTERACTIONS - shown),
            "total": MAX_INTERACTIONS - len(receipts)}


def interaction_error(governance: Mapping) -> str | None:
    if min(interactions_remaining(governance).values()) == 0:
        return "governance.interaction_limit"
    return None


def expected_approval_kind(governance: Mapping) -> str:
    """The permitted approval kind for the current phase, from neutral admitted facts.

    Deliberative runs are always human. Delegated auto (no interactive human) is always
    automatic. Interactive auto requires one human approval episode before the first
    successful approval, then is automatic thereafter; interactive authority outranks
    delegation and never silently downgrades (ADR-0063)."""
    if governance["control_mode"] == "deliberative":
        return "host_ui"
    if governance["context"]["interactive"] is True:
        return "host_ui" if not governance["first_approval"] else "auto"
    return "auto"  # delegated auto: no human episode


def configuration_error(state, proposed: Mapping) -> str | None:
    """Enforce consumed minimums and the authority envelope, exactly as ADR-0063 states.

    Before the first successful approval an author may propose within schema bounds
    (including above the seed) except in delegated auto, where the durable operator-narrowed
    envelope caps every proposal. After the first successful approval an automatic acceptance must
    be componentwise no greater than the currently effective approved ceilings; a
    deliberative human may still raise through the dialog."""
    for ceiling, used in CEILINGS.items():
        if proposed["budgets"][ceiling] < state.budgets[used]:
            return "governance.budget_invalid"
    gov = state.governance
    if gov["first_approval"]:
        if gov["control_mode"] == "auto":
            for ceiling in CEILINGS:
                if proposed["budgets"][ceiling] > state.budgets[ceiling]:
                    return "governance.auto_ceiling"
        return None  # deliberative after first approval: the human may raise
    if gov["control_mode"] == "auto" and gov["context"]["interactive"] is not True:
        envelope = gov["delegation_envelope"]
        for ceiling in CEILINGS:
            if proposed["budgets"][ceiling] > envelope[ceiling]:
                return "governance.auto_ceiling"
    return None  # before first approval, interactive auto / deliberative: schema bounds only


def resolve_submission(governance: Mapping, submission: Mapping,
                       actions: tuple[tuple[str, Mapping], ...]) -> tuple[dict | None, str | None]:
    """Resolve one raw human choice using the finite application-supplied policy.

    A human amendment changes ceilings only; it can never modify or inject a rationale
    (ADR-0063). The original author rationale is retained with the amended ceilings."""
    row = dict(actions).get(submission.get("action"))
    if row is None:
        return None, "governance.decision_conflict"
    submitted = plain(submission["configuration"])
    current = plain(governance["proposal"])
    configuration = {"budgets": submitted["budgets"], "rationale": current["rationale"]}
    changed = configuration["budgets"] != current["budgets"]
    outcome = row["changed_outcome"] if changed else row["unchanged_outcome"]
    resolved = {"outcome": outcome}
    if changed:
        resolved["configuration"] = configuration
    return resolved, None


def decision_error(run_id: str, governance: Mapping, decision: Mapping) -> str | None:
    """Validate one governance decision against current policy; ``inert`` signals an exact
    historical replay that must not reopen authority."""
    outcome = decision.get("outcome")
    fingerprint = canonical_digest(decision)
    prior = next((r for r in governance["receipts"] if r["id"] == decision["receipt_id"]), None)
    if prior:
        # A receipt's approval_kind is immutable; finalizing a human reservation with an
        # auto decision (or vice versa) fails closed.
        if prior["approval_kind"] != decision["approval_kind"]:
            return "governance.receipt_replay"
        if fingerprint in {prior["fingerprint"], prior["presentation_fingerprint"]}:
            return "inert"
        presentation = {k: v for k, v in decision.items()
                        if k not in {"amendment", "submission"}}
        presentation["outcome"] = "present"
        if (prior["outcome"] != "present" or outcome == "present" or
                canonical_digest(presentation) != prior["presentation_fingerprint"]):
            return "governance.receipt_replay"
    # Exact historical replay is settled above. The placeholder otherwise refuses every
    # new decision shape, including a submission without an outcome field.
    if not sized(governance):
        return "governance.approval_required"
    if (decision["run_id"] != run_id or decision["proposal_digest"] != governance["proposal_digest"]
            or decision["plan_revision"] != governance["plan_revision"]):
        return "governance.stale_proposal"
    if prior is None and (reason := interaction_error(governance)):
        return reason
    expected = expected_approval_kind(governance)
    auto_authorized = (governance["first_approval"] or
                       governance["context"]["delegation"] is True)
    if (decision["approval_kind"] == "auto" and not auto_authorized):
        return "governance.approval_unavailable"
    if (decision["approval_kind"] != expected or (expected == "host_ui" and
            governance["context"]["approval_capability"] != HUMAN_CONFIGURATION)):
        return "governance.approval_unavailable"
    if governance["state"] == "approved":
        return "governance.receipt_replay"
    if "submission" in decision and expected != "host_ui":
        return "governance.approval_unavailable"
    if expected == "host_ui" and "submission" not in decision and outcome not in {"present", "dismiss"}:
        return "governance.decision_conflict"
    if outcome == "present":
        # Auto never presents: the single human episode is host_ui; later approvals are auto.
        return None if expected == "host_ui" else "governance.approval_unavailable"
    if expected == "host_ui" and prior is None:
        return "governance.receipt_replay"
    return None


def invariant(doc: Mapping) -> bool:
    """Reconcile the complete durable governance history with current operational state."""
    value = doc["governance"]
    receipts = value["receipts"]
    if len({receipt["id"] for receipt in receipts}) != len(receipts):
        return False
    for receipt in receipts:
        if receipt["approval_kind"] not in APPROVAL_KINDS:
            return False
        # The presentation fingerprint is bound to the immutable receipt kind.
        if ((receipt["presentation_fingerprint"] is None)
                != (receipt["approval_kind"] == "auto")):
            return False
        if (receipt["outcome"] == "present"
                and receipt["fingerprint"] != receipt["presentation_fingerprint"]):
            return False
        if not 0 < receipt["plan_revision"] <= value["plan_revision"]:
            return False
    revisions = {receipt["plan_revision"] for receipt in receipts}
    if any(sum(receipt["plan_revision"] == revision for receipt in receipts)
           > PROPOSAL_INTERACTIONS for revision in revisions):
        return False
    if any(len({receipt["proposal_digest"] for receipt in receipts
                if receipt["plan_revision"] == revision}) != 1 for revision in revisions):
        return False
    if any(later["plan_revision"] < earlier["plan_revision"]
           for earlier, later in zip(receipts, receipts[1:])):
        return False

    approving = [receipt for receipt in receipts if receipt["outcome"] == "approve"]
    has_approval = bool(approving)
    has_metadata = (value["approved_digest"] is not None
                    and value["approval_kind"] is not None)
    if ((value["approval_kind"] is None) != (value["approved_digest"] is None)
            or value["approval_kind"] not in {None, *APPROVAL_KINDS}
            or value["first_approval"] != has_approval
            or value["first_approval"] != has_metadata):
        return False
    if approving:
        latest = max(approving, key=lambda receipt: receipt["plan_revision"])
        if (value["approved_digest"] != latest["proposal_digest"]
                or value["approval_kind"] != latest["approval_kind"]):
            return False

    if value["context"]["delegation"] != doc["invocation"]["delegation"]:
        return False
    if value["context"]["interactive"] != doc["invocation"]["interactive"]:
        return False
    delegated = (value["control_mode"] == "auto"
                 and value["context"]["interactive"] is not True)
    envelope = value["delegation_envelope"]
    if delegated != (envelope is not None):
        return False
    if delegated and doc["invocation"]["delegation"] is not True:
        return False
    if envelope is not None and (
            any(envelope[key] > DEFAULT_CEILINGS[key] for key in CEILINGS)
            or any(doc["budgets"][key] > envelope[key] for key in CEILINGS)
            or any(value["proposal"]["budgets"][key] > envelope[key] for key in CEILINGS)):
        return False

    if value["control_mode"] == "deliberative":
        if (value["pre_approval_revisions"] != 0
                or value["post_approval_revisions"] != 0
                or any(receipt["approval_kind"] != "host_ui" for receipt in receipts)):
            return False
    elif sized(value):
        expected_revision = (1 + value["pre_approval_revisions"]
                             + value["post_approval_revisions"])
        if value["plan_revision"] != expected_revision:
            return False
        if not value["first_approval"] and value["post_approval_revisions"] != 0:
            return False
        if approving and min(receipt["plan_revision"] for receipt in approving) != (
                1 + value["pre_approval_revisions"]):
            return False

    if not sized(value):
        if (value["state"] != "pending" or value["plan_revision"] != 0 or receipts
                or value["first_approval"] or has_metadata
                or value["pre_approval_revisions"] != 0
                or value["post_approval_revisions"] != 0
                or any(value["proposal"]["budgets"][key] != doc["budgets"][key]
                       for key in CEILINGS)):
            return False
    if value["state"] == "pending" and value["first_approval"]:
        return False
    if value["state"] == "revision_pending" and not value["first_approval"]:
        return False

    if value["state"] != "approved" and sized(value):
        if any(value["proposal"]["budgets"][ceiling] < doc["budgets"][used]
               for ceiling, used in CEILINGS.items()):
            return False
        if value["control_mode"] == "auto" and value["first_approval"] and any(
                value["proposal"]["budgets"][ceiling] > doc["budgets"][ceiling]
                for ceiling in CEILINGS):
            return False

    if delegated and any(receipt["approval_kind"] != "auto" for receipt in receipts):
        return False
    interactive_auto = (value["control_mode"] == "auto"
                        and value["context"]["interactive"] is True)
    if interactive_auto:
        human_approvals = [receipt for receipt in approving
                           if receipt["approval_kind"] == "host_ui"]
        if len(human_approvals) > 1:
            return False
        if any(receipt["approval_kind"] == "auto" for receipt in receipts):
            if len(human_approvals) != 1:
                return False
        if human_approvals:
            human_epoch = human_approvals[0]["plan_revision"]
            if (any(receipt["approval_kind"] == "auto"
                    and receipt["plan_revision"] <= human_epoch for receipt in receipts)
                    or any(receipt["approval_kind"] == "host_ui"
                           and receipt["plan_revision"] > human_epoch for receipt in receipts)):
                return False

    if value["state"] == "approved":
        matching = [receipt for receipt in approving
                    if receipt["plan_revision"] == value["plan_revision"]
                    and receipt["proposal_digest"] == value["proposal_digest"]
                    and receipt["approval_kind"] == value["approval_kind"]]
        return (sized(value) and value["approved_digest"] == value["proposal_digest"]
                and bool(matching)
                and all(value["proposal"]["budgets"][key] == doc["budgets"][key]
                        for key in CEILINGS))
    return True
