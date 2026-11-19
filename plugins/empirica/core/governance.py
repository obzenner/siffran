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

import re
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
#: The recorded document binding's shape (ADR-0072, PR 07: ``receipts[].document_digest``).
_DOCUMENT_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


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


def _ceilings_as_ints(budgets: Mapping) -> dict:
    """Every ceiling as the exact ``int`` the HITL document renders (Copilot X2, #48).

    The wire's ``integer`` admits integral floats, so a sized proposal can arrive carrying
    ``8.0``; run/4 stores each ceiling as the one representation the document's renderer demands
    (``core.hitl._integer``): before this normalization an admitted ``8.0`` made a context fetch
    Fault with ``invalid_request`` and a changed-context fetch raise after its own commit.
    ``revise`` is the one admission point every proposal path flows through — author
    ``configure_run`` resolutions, human amendments, auto — so state, dialog rows and the
    document agree. A non-integral float, a bool or a non-number stays exactly as it arrived:
    the request schema and the renderer refuse it elsewhere, fail closed."""
    normalized = plain(budgets)
    for name, value in normalized.items():
        if type(value) is float and value.is_integer():
            normalized[name] = int(value)
    return normalized


def revise(goal: str, current: Mapping, *, proposal=None, context=None) -> dict:
    value = plain(current)
    if proposal is not None:
        value["proposal"] = plain(proposal)
        value["proposal"]["budgets"] = _ceilings_as_ints(value["proposal"]["budgets"])
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
    if not revisions_exhausted(governance) or not _material_revision(goal, governance, proposal):
        return None
    return "governance.revision_exhausted"


def revisions_exhausted(governance: Mapping) -> bool:
    """Whether the auto material-revision allowance of the phase the run is in is spent (before the first approval, then
    after it): any further change to the configuration is refused. Deliberative revisions return to the human and are not
    capped. The one predicate ``revision_error`` and the next action (``configuration_stuck``) both read; the run view
    publishes the same keys, so an adapter reads it from the view too."""
    if governance["control_mode"] != "auto":
        return False
    used = (governance["post_approval_revisions"] if governance["first_approval"]
            else governance["pre_approval_revisions"])
    return used >= MAX_REVISIONS


def configuration_stuck(governance: Mapping, confirmations: int, budgets: Mapping) -> bool:
    """Whether a sized configuration waiting on a human's dialog can neither be shown nor changed: this configuration's
    presentations are spent (an unchanged proposal opens no dialog) and the revision allowance is spent (a changed one is
    refused with ``governance.revision_exhausted``). Nothing can proceed, so the only way on is the honest stop."""
    return (sized(governance) and expected_approval_kind(governance, raises(governance, budgets)) == "host_ui"
            and revisions_exhausted(governance)
            and interactions_remaining(governance, confirmations)["proposal"] == 0)


def admission(governance: Mapping) -> str | None:
    """Reason code blocking governed work until the current proposal is approved, else ``None``."""
    if governance["state"] == "approved" and governance["approved_digest"] == governance["proposal_digest"]:
        return None
    return ("governance.revision_required" if governance["approval_kind"] else
            "governance.approval_required")


def interactions_remaining(governance: Mapping, confirmations: int) -> dict:
    """Host-dialog presentations left: ``proposal`` for the current plan revision and ``total``.

    The one count of human interactions: a configuration receipt and an evidence presentation (ADR-0067,
    ``confirmations`` of them) each spend one unit of ``total``; every path that admits or shows an
    interaction reads this function."""
    receipts = governance["receipts"]
    shown = sum(r["plan_revision"] == governance["plan_revision"] for r in receipts)
    return {"proposal": max(0, PROPOSAL_INTERACTIONS - shown),
            "total": max(0, MAX_INTERACTIONS - len(receipts) - confirmations)}


def interaction_error(governance: Mapping, confirmations: int) -> str | None:
    """``governance.interaction_limit`` once either interaction allowance is spent, else ``None``."""
    if min(interactions_remaining(governance, confirmations).values()) == 0:
        return "governance.interaction_limit"
    return None


#: The configuration states that wait on a human's answer: a proposal not approved yet (``pending``, or
#: ``revision_pending`` after the first approval) and one the human rejected (the human says what to propose instead).
HUMAN_WAIT_STATES = frozenset({"pending", "revision_pending", "rejected"})


def raises(governance: Mapping, budgets: Mapping) -> bool:
    """Whether the standing proposal asks for more than the run has approved: the run holds a first approval and some
    proposed ceiling is above the approved one in ``budgets``. Derived at read time from the two (nothing stores it) and
    published in the run view's governance (``raises``), so a host reads the fact and never re-derives it. Before the first
    approval nothing is approved yet, so nothing is raised."""
    return bool(governance["first_approval"]
                and any(governance["proposal"]["budgets"][ceiling] > budgets[ceiling] for ceiling in CEILINGS))


def budget_recovery(governance: Mapping, actions) -> str:
    """The case of a ``budget.exhausted`` reason's text, from the recovery its run is offered (``actions``, after the mode's
    exclusions): ``raise_dialog`` where a raise is offered to an interactive auto run (its human approves it in a dialog),
    ``raise_human`` where one is offered to a deliberative run (its human decides it), ``fresh_run`` where ``budget.raise`` is
    not offered (a delegated run has nobody to ask), and ``raise_unavailable`` where a raise is offered but no one can be asked
    to approve it now (the host reported that it can show a human nothing: ``cannot_ask``), so the text explains the listed
    action instead of promising a dialog that cannot come or denying the offer. The contract's ``budget.exhausted`` words each
    case; nothing else words it. Presentation only: no decision reads it."""
    if "budget.raise" not in actions:
        return "fresh_run"
    if cannot_ask(governance):
        return "raise_unavailable"
    return "raise_human" if governance["control_mode"] == "deliberative" else "raise_dialog"


def exclusion_key(governance: Mapping) -> str:
    """The key of the contract's ``recovery_exclusions`` that applies to the run: ``deliberative``, ``auto_interactive``
    (its human can approve a raise) or ``auto_delegated`` (nobody can be asked: the honest stop is the way on)."""
    if governance["control_mode"] == "deliberative":
        return "deliberative"
    return "auto_interactive" if governance["context"]["interactive"] is True else "auto_delegated"


def human_wait(governance: Mapping, prompt_error: str | None, raises: bool) -> bool:
    """Whether the run's configuration waits on its human now: the agent has asked (a sized ``configure_run``, not the
    StartRun placeholder), the configuration is in ``HUMAN_WAIT_STATES`` (not approved yet, or rejected), this phase is a
    human's to approve (``host_ui``), the host that mediated it can show a human its dialog (``approval_capability``) and
    another presentation is still admissible (``prompt_error``, the interaction ceiling: ``interaction_error``). Its
    dialog is open, was closed without an answer, or was answered no; it is shown again at the agent's next
    ``configure_run``, and until the human says how to go on the next act is theirs. Otherwise the agent acts next: it
    has yet to ask, or nobody can be asked (the agent may only stop honestly).

    Read from the governance of the run's state or of its author view (the same keys); the author view publishes the
    answer as ``governance.human_wait``. The one predicate of who acts on a configuration: the report's requirements
    (``evaluation.report_requirements``) and so the observer's standing and ask, and the hosts' turn-end wait, which reads
    the view's answer and adds only its own approval ingress and the sole-blocker check. ``raises`` is the fact ``raises``
    derives (the run view's ``governance.raises``); the state holds none, so the caller names it."""
    return (governance["state"] in _waiting_states(governance) and sized(governance)
            and expected_approval_kind(governance, raises) == "host_ui"
            and governance["context"]["approval_capability"] == HUMAN_CONFIGURATION and prompt_error is None)


def _waiting_states(governance: Mapping) -> frozenset:
    """The states a configuration waits on its human in. A rejected raise of an interactive auto run is the human's
    answer: the agent acts (it stops, or proposes within the approved ceilings), so ``rejected`` does not wait there."""
    if governance["control_mode"] == "auto" and governance["first_approval"]:
        return HUMAN_WAIT_STATES - {"rejected"}
    return HUMAN_WAIT_STATES


def cannot_ask(governance: Mapping) -> bool:
    """Whether the host has reported its context and it cannot put a question to a human: no one can approve or decide a
    raise now. A host that has not reported yet (a null ``author``) is not asked to give up."""
    return (governance["context"]["author"] is not None
            and governance["context"]["approval_capability"] != HUMAN_CONFIGURATION)


def _no_ingress(governance: Mapping, budgets: Mapping) -> bool:
    """Whether a sized proposal waits on a human's approval that a host which has reported its context cannot put to
    one. A host that has not reported yet is not asked to give up. The run's initial context (no ``author``, no ingress)
    is byte-identical to a report of "no author, no ingress", so a null ``author`` is read as "not reported yet": the core
    holds no separate fact that a report arrived."""
    return (sized(governance) and expected_approval_kind(governance, raises(governance, budgets)) == "host_ui"
            and cannot_ask(governance))


def _automatic_way_on(governance: Mapping, budgets: Mapping) -> bool:
    """Whether a raise is pending while a revision within the approved ceilings is still admitted: the non-raising revision
    of this run is automatic (interactive auto after its first approval; every deliberative proposal goes to the human, and
    a delegated run's raise is refused), so it asks nobody and spends no interaction."""
    return (sized(governance) and raises(governance, budgets) and expected_approval_kind(governance, False) == "auto"
            and not revisions_exhausted(governance))


def raise_without_ingress(governance: Mapping, budgets: Mapping) -> bool:
    """Whether a pending raise (the only human question after the first approval) cannot be asked because no human can be
    reached, while an automatic revision is still admitted: that proposal or the honest stop is the way on."""
    return _automatic_way_on(governance, budgets) and _no_ingress(governance, budgets)


def raise_unasked(governance: Mapping, confirmations: int, budgets: Mapping) -> bool:
    """Whether a pending raise cannot be asked, for want of a human to ask or of an interaction to ask with (the run's total
    allowance is spent), while an automatic revision is still admitted: the way on is that proposal or the honest stop. A
    revision within the approved ceilings asks nobody, so a spent allowance does not close it."""
    return _automatic_way_on(governance, budgets) and (
        _no_ingress(governance, budgets) or interactions_remaining(governance, confirmations)["total"] == 0)


def nobody_to_ask(governance: Mapping, budgets: Mapping) -> bool:
    """Whether a sized proposal waits on a human's approval that nobody can give and no automatic way on exists: the agent
    may only stop honestly. Where a revision within the approved ceilings is still admitted (``raise_without_ingress``)
    the stop is not alone."""
    return _no_ingress(governance, budgets) and not raise_without_ingress(governance, budgets)


def expected_approval_kind(governance: Mapping, raises: bool) -> str:
    """The permitted approval kind for the current phase, from neutral admitted facts.

    Deliberative runs are always human. Delegated auto (no interactive human) is always
    automatic. Interactive auto requires one human approval episode before the first
    successful approval, then is automatic thereafter, except that a proposal that raises a ceiling (``raises``) is a
    further human episode; interactive authority outranks delegation and never silently downgrades (ADR-0063, ADR-0073).
    ``raises`` is the fact the function ``raises`` derives from the ceilings, and the run view publishes it as
    ``governance.raises``; a stored governance holds none, so every caller names it and none can omit it."""
    if governance["control_mode"] == "deliberative":
        return "host_ui"
    if governance["context"]["interactive"] is True:
        return "host_ui" if not governance["first_approval"] or raises else "auto"
    return "auto"  # delegated auto: no human episode


def configuration_error(state, proposed: Mapping) -> str | None:
    """Enforce consumed minimums and the authority envelope, exactly as ADR-0063 states.

    Before the first successful approval an author may propose within schema bounds
    (including above the seed) except in delegated auto, where the durable operator-narrowed
    envelope caps every proposal. After the first successful approval an automatic acceptance must
    be componentwise no greater than the currently effective approved ceilings; a
    deliberative human may still raise through the dialog, and so may the human of an interactive
    auto run (a raise is theirs to approve); a delegated auto run has nobody to ask and is refused."""
    for ceiling, used in CEILINGS.items():
        if proposed["budgets"][ceiling] < state.budgets[used]:
            return "governance.budget_invalid"
    gov = state.governance
    if gov["first_approval"]:
        if gov["control_mode"] == "auto" and gov["context"]["interactive"] is not True:
            for ceiling in CEILINGS:
                if proposed["budgets"][ceiling] > state.budgets[ceiling]:
                    return "governance.auto_ceiling"
        return None  # after the first approval a human may raise: deliberative, and interactive auto
    if gov["control_mode"] == "auto" and gov["context"]["interactive"] is not True:
        envelope = gov["delegation_envelope"]
        for ceiling in CEILINGS:
            if proposed["budgets"][ceiling] > envelope[ceiling]:
                return "governance.auto_ceiling"
    return None  # before first approval, interactive auto / deliberative: schema bounds only


def ceiling_bound(governance: Mapping) -> str:
    """Which text of ``governance.auto_ceiling`` is true (its ``bound``): ``approved`` once a first approval fixed the ceilings (a delegated
    run is held to what it approved), else ``envelope`` (a delegated run is held to the operator's envelope). The core
    chooses; the contract holds one text per case."""
    return "approved" if governance["first_approval"] else "envelope"


def refusal_parameters(governance: Mapping, reason: str) -> dict[str, str]:
    """The typed parameters of a configuration refusal: only ``governance.auto_ceiling`` has any (its ``bound``)."""
    return {"bound": ceiling_bound(governance)} if reason == "governance.auto_ceiling" else {}


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


def decision_error(run_id: str, governance: Mapping, decision: Mapping,
                   confirmations: int, budgets: Mapping) -> str | None:
    """Validate one governance decision against current policy (``budgets``: the ceilings in force, which say whether the
    proposal raises one); ``inert`` signals an exact historical replay that must not reopen authority."""
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
    if prior is None and interaction_error(governance, confirmations):
        # the run's total allowance spent: nothing can be shown; only this configuration's three presentations: changed
        # ceilings are a new configuration
        return ("governance.interaction_limit" if interactions_remaining(governance, confirmations)["total"] == 0
                else "governance.configuration_shown")
    expected = expected_approval_kind(governance, raises(governance, budgets))
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
    if outcome == "amend" and "amendment" in decision:
        amendment = decision["amendment"]
        # Copilot X1 (#47): an explicit amendment that changes no ceiling names numbers the
        # standing proposal already records — a replay of the standing configuration, so it
        # must not reopen authority nor write a receipt. Committing it would append an amend
        # receipt whose revision never advanced (``revise`` refuses a no-op), and run/4's
        # invariant then classifies the whole run corrupt. A human amendment never reaches this:
        # ``resolve_submission`` resolves an unchanged human edit to ``approve`` — the no-op
        # host_ui amend has no refusal reason of its own to mirror — so only an explicit auto
        # amendment can arrive here; it is refused before any mutation, nothing spent.
        if (isinstance(amendment, Mapping)
                and isinstance(amendment.get("budgets"), Mapping)
                and amendment["budgets"] == governance["proposal"]["budgets"]):
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
        # The amendment's superseded ceilings ride their receipt (ADR-0072, run/4): an amend
        # receipt must name them — after the revise they can be derived nowhere else — and no
        # other outcome may carry them. An amend receipt always names a strictly earlier revision
        # than the one its amendment brought about.
        if receipt["outcome"] == "amend":
            superseded = receipt.get("superseded_budgets")
            if (not isinstance(superseded, Mapping) or set(superseded) != set(CEILINGS)
                    or receipt["plan_revision"] >= value["plan_revision"]):
                return False
        elif receipt.get("superseded_budgets") is not None:
            return False
        # The document binding the decision named (ADR-0072, receipts[].document_digest; the
        # contract step, PR 13): every host_ui receipt names the sha256 of the bytes the core
        # rendered and verified — a human approval without its document cannot decode — and an
        # auto receipt records null exactly: auto answers no human's question. The gate side
        # (the application) refuses a decision whose digest differs from the one its present
        # receipt recorded, so a finalized receipt keeps the document its present bound; the
        # member's presence is the state schema's rule, checked at every decode.
        recorded = receipt.get("document_digest")
        if receipt["approval_kind"] == "auto":
            if recorded is not None:
                return False
        elif not (isinstance(recorded, str)
                  and _DOCUMENT_DIGEST.fullmatch(recorded) is not None):
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
        # A pending proposal above the approved ceilings is a raise: only an interactive auto run (its human decides it) holds one.
        if (value["control_mode"] == "auto" and value["first_approval"]
                and value["context"]["interactive"] is not True and any(
                    value["proposal"]["budgets"][ceiling] > doc["budgets"][ceiling]
                    for ceiling in CEILINGS)):
            return False

    if delegated and any(receipt["approval_kind"] != "auto" for receipt in receipts):
        return False
    interactive_auto = (value["control_mode"] == "auto"
                        and value["context"]["interactive"] is True)
    if interactive_auto:
        # One human episode opens the run; after it a human is asked again only about a raise. The stored history
        # cannot say which earlier revision raised, so it is held to what it can show: later human receipts follow the
        # first approval, no revision is approved twice by a human, and the standing proposal holds a human receipt
        # only if it raises a ceiling.
        human_approvals = sorted((receipt for receipt in approving if receipt["approval_kind"] == "host_ui"),
                                 key=lambda receipt: receipt["plan_revision"])
        if len({receipt["plan_revision"] for receipt in human_approvals}) != len(human_approvals):
            return False
        if any(receipt["approval_kind"] == "auto" for receipt in receipts) and not human_approvals:
            return False
        if human_approvals:
            human_epoch = human_approvals[0]["plan_revision"]
            if any(receipt["approval_kind"] == "auto" and receipt["plan_revision"] <= human_epoch
                   for receipt in receipts):
                return False
            if value["state"] != "approved" and any(
                    receipt["approval_kind"] == "host_ui" and receipt["plan_revision"] == value["plan_revision"]
                    for receipt in receipts) and not (
                    value["first_approval"] and any(
                        value["proposal"]["budgets"][ceiling] > doc["budgets"][ceiling] for ceiling in CEILINGS)):
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
