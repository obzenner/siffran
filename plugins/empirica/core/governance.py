"""Bounded proposal consent; pure policy shared by every admission path.

Only private host ingress supplies context/decisions. Operational counters live
in OperationalState.budgets, never in proposal metadata.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

MAX_INTERACTIONS = 128
PROPOSAL_INTERACTIONS = 3
CEILINGS = {"max_passes": "passes_used", "max_spawns": "spawns_used",
            "max_audit_spawns": "audit_spawns_used"}


def plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value


def canonical_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()


def canonical_graph(graph: Mapping[str, Any] | None) -> dict | None:
    if graph is None:
        return None
    return {"root": graph["root"],
            "claims": sorted(plain(graph["claims"]), key=lambda c: c["id"]),
            "edges": sorted(plain(graph["edges"]), key=lambda e: (e["from"], e["to"], e["type"]))}


def proposal_body(goal: str, graph: Mapping | None, governance: Mapping) -> dict:
    """Bind only the immutable goal context and approvable run configuration."""
    return {"goal": goal, "configuration": plain(governance["proposal"]),
            "control_mode": governance["control_mode"]}


def initial(goal: str, budgets: Mapping, modes: Mapping, control_mode: str = "deliberative") -> dict:
    value = {"state": "pending", "control_mode": control_mode,
             "plan_revision": 0, "approved_digest": None,
             "approval_kind": None, "receipts": [],
             "proposal": {"budgets": {k: budgets[k] for k in CEILINGS},
                          "modes": dict(modes)},
             "context": {"author": None, "ingress": "unavailable"}}
    value["proposal_digest"] = canonical_digest(proposal_body(goal, None, value))
    return value


def revise(goal: str, graph: Mapping | None, current: Mapping, *, proposal=None, context=None) -> dict:
    value = plain(current)
    if proposal is not None:
        value["proposal"] = plain(proposal)
    if context is not None:
        value["context"] = plain(context)
    observed = canonical_digest(proposal_body(goal, graph, value))
    if observed == current["proposal_digest"]:
        return value
    value.update(proposal_digest=observed, plan_revision=current["plan_revision"] + 1,
                 state="revision_pending" if current["approval_kind"] else "pending")
    return value


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


def configuration_error(state, proposed: Mapping) -> str | None:
    for ceiling, used in CEILINGS.items():
        if proposed["budgets"][ceiling] < state.budgets[used]:
            return "governance.budget_invalid"
        if state.governance["control_mode"] == "auto" and proposed["budgets"][ceiling] > state.budgets[ceiling]:
            return "governance.auto_ceiling"
    return None


def resolve_submission(governance: Mapping, submission: Mapping,
                       actions: tuple[tuple[str, Mapping], ...]) -> tuple[dict | None, str | None]:
    """Resolve one raw human choice using the finite application-supplied policy."""
    row = dict(actions).get(submission.get("action"))
    if row is None:
        return None, "governance.decision_conflict"
    feedback = submission.get("feedback", "")
    if not isinstance(feedback, str) or ((row["feedback"] == "required") != bool(feedback.strip())):
        return None, "governance.decision_conflict"
    configuration = plain(submission["configuration"])
    changed = configuration != plain(governance["proposal"])
    outcome = row["changed_outcome"] if changed else row["unchanged_outcome"]
    resolved = {"outcome": outcome}
    if changed:
        resolved["configuration"] = configuration
    return resolved, None


def decision_error(run_id: str, governance: Mapping, decision: Mapping) -> str | None:
    fingerprint = canonical_digest(decision)
    prior = next((r for r in governance["receipts"] if r["id"] == decision["receipt_id"]), None)
    if prior:
        if fingerprint in {prior["fingerprint"], prior["presentation_fingerprint"]}:
            return "inert"
        presentation = {k: v for k, v in decision.items()
                        if k not in {"amendment", "submission"}}
        presentation["outcome"] = "present"
        if (prior["outcome"] != "present" or decision.get("outcome") == "present" or
                canonical_digest(presentation) != prior["presentation_fingerprint"]):
            return "governance.receipt_replay"
    if (decision["run_id"] != run_id or decision["proposal_digest"] != governance["proposal_digest"]
            or decision["plan_revision"] != governance["plan_revision"]):
        return "governance.stale_proposal"
    if prior is None and (reason := interaction_error(governance)):
        return reason
    expected = "auto" if governance["control_mode"] == "auto" else "host_ui"
    if decision["approval_kind"] != expected or (expected == "host_ui" and
            governance["context"]["ingress"] not in {"mcp_elicitation", "pi_ui"}):
        return "governance.approval_unavailable"
    if governance["state"] == "approved":
        return "governance.receipt_replay"
    outcome = decision.get("outcome")
    if "submission" in decision and expected != "host_ui":
        return "governance.approval_unavailable"
    if expected == "host_ui" and "submission" not in decision and outcome not in {"present", "dismiss"}:
        return "governance.decision_conflict"
    if outcome == "present":
        return None if expected == "host_ui" else "governance.approval_unavailable"
    if expected == "host_ui" and prior is None:
        return "governance.receipt_replay"
    return None


def invariant(doc: Mapping) -> bool:
    value = doc["governance"]
    receipts = value["receipts"]
    if (len({r["id"] for r in receipts}) != len(receipts)
            or any((r["presentation_fingerprint"] is None) != (value["control_mode"] == "auto")
                   or (r["outcome"] == "present" and r["fingerprint"] != r["presentation_fingerprint"])
                   for r in receipts)
            or any(r["plan_revision"] > value["plan_revision"] for r in receipts)
            or any(sum(r["plan_revision"] == revision for r in receipts) > PROPOSAL_INTERACTIONS
                   for revision in {r["plan_revision"] for r in receipts})):
        return False
    if (value["approval_kind"] is None) != (value["approved_digest"] is None):
        return False
    if value["approval_kind"] not in {None, "auto" if value["control_mode"] == "auto" else "host_ui"}:
        return False
    if value["state"] == "approved":
        return (value["approved_digest"] == value["proposal_digest"] and bool(receipts)
                and value["proposal"]["modes"] == doc["modes"]
                and all(value["proposal"]["budgets"][k] == doc["budgets"][k] for k in CEILINGS))
    return True
