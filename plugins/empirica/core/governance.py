"""Bounded proposal consent; pure policy shared by every admission path.

Only private host ingress supplies context/decisions. Operational counters live
in OperationalState.budgets, never in proposal metadata.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

AUTO_REVISION_LIMIT = 8
MAX_REVISIONS = 64
MAX_INTERACTIONS = 128
PROPOSAL_INTERACTIONS = 3
CEILINGS = {"max_passes": "passes_used", "max_spawns": "spawns_used",
            "max_audit_spawns": "audit_spawns_used"}

# Selected concrete spellings from Anthropic model pages and their Bedrock column. No family/latest aliases or inference-profile ARN guessing.
# https://platform.claude.com/docs/en/about-claude/models/overview
_MODEL_IDS = {
    "claude-opus-4-8": "claude-opus-4-8",
    "claude-fable-5-1": "claude-fable-5-1",
    "claude-opus-5-5": "claude-opus-5-5",
    "claude-sonnet-5": "claude-sonnet-5",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001-v1:0",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-opus-4-6": "claude-opus-4-6-v1",
}


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


def normalized_model_identity(provider_id: object, model_id: object) -> str | None:
    if not isinstance(provider_id, str) or not isinstance(model_id, str):
        return None
    if provider_id == "anthropic" and model_id in _MODEL_IDS:
        return model_id
    if provider_id in {"bedrock", "amazon-bedrock", "amazon-bedrock-eu",
                       "amazon-bedrock-us", "amazon-bedrock-global"}:
        for canonical, wire in _MODEL_IDS.items():
            if model_id in {prefix + "anthropic." + wire for prefix in ("", "us.", "eu.", "global.", "apac.")}:
                return canonical
    # Concrete dated OpenAI snapshots only; exact IDs, never moving aliases.
    if provider_id == "openai" and model_id in {
            "gpt-4.1-2025-04-14", "gpt-4.1-mini-2025-04-14", "gpt-4o-2024-08-06"}:
        return "openai/" + model_id
    return None


def model_key(row: Mapping[str, Any] | None) -> str | None:
    return normalized_model_identity(row.get("provider_id"), row.get("model_id")) if row else None


def identity_relation(author: Mapping[str, Any] | None, auditor: Mapping[str, Any] | None) -> str:
    if not author or not auditor or any(r.get("observed_by") != "host" for r in (author, auditor)):
        return "unknown_equivalence"
    left, right = model_key(author), model_key(auditor)
    return ("unknown_equivalence" if left is None or right is None else
            "same_model" if left == right else "different_model")


def canonical_graph(graph: Mapping[str, Any] | None) -> dict | None:
    if graph is None:
        return None
    return {"root": graph["root"],
            "claims": sorted(plain(graph["claims"]), key=lambda c: c["id"]),
            "edges": sorted(plain(graph["edges"]), key=lambda e: (e["from"], e["to"], e["type"]))}


def proposal_body(goal: str, graph: Mapping | None, governance: Mapping) -> dict:
    return {"goal": goal, "graph": canonical_graph(graph),
            "configuration": plain(governance["proposal"]),
            "context": plain(governance["context"]),
            "control_mode": governance["control_mode"],
            "revision_limit": governance["revision_limit"]}


def initial(goal: str, budgets: Mapping, modes: Mapping, control_mode: str = "deliberative") -> dict:
    value = {"state": "pending", "control_mode": control_mode,
             "revision_limit": AUTO_REVISION_LIMIT if control_mode == "auto" else MAX_REVISIONS,
             "plan_revision": 0, "revisions_used": 0, "approved_digest": None,
             "approval_kind": None, "receipts": [], "change_request": None,
             "proposal": {"budgets": {k: budgets[k] for k in CEILINGS},
                          "modes": dict(modes), "auditor": None, "allow_same_model": False},
             "context": {"inventory": {"members": [], "source": "unknown",
                          "complete": False, "authorized": False}, "author": None,
                         "ingress": "unavailable"}}
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
    spent = current["revisions_used"] + int(current["approval_kind"] is not None)
    if spent > current["revision_limit"]:
        raise ValueError("governance.revision_limit")
    value.update(proposal_digest=observed, plan_revision=current["plan_revision"] + 1,
                 revisions_used=spent,
                 state="revision_pending" if current["approval_kind"] else "pending")
    return value


def admission(governance: Mapping) -> str | None:
    if governance["state"] == "approved" and governance["approved_digest"] == governance["proposal_digest"]:
        return None
    if governance["revisions_used"] >= governance["revision_limit"]:
        return "governance.revision_limit"
    return ("governance.revision_required" if governance["approval_kind"] else
            "governance.approval_required")


def inventory_status(inventory: Mapping) -> str:
    if not inventory["complete"] or not inventory["authorized"] or inventory["source"] == "unknown":
        return "unknown"
    keys = {model_key(m) for m in inventory["members"]}
    if None in keys:
        return "partial"
    return "zero" if not keys else "singleton" if len(keys) == 1 else "multiple"


def context_error(governance: Mapping) -> str | None:
    context = governance["context"]
    inventory, author = context["inventory"], context["author"]
    if inventory_status(inventory) in {"unknown", "zero"}:
        return "governance.inventory_unknown"
    if model_key(author) is None:
        return "governance.author_unknown"
    if model_key(author) not in {model_key(m) for m in inventory["members"]}:
        return "governance.identity_mismatch"
    return None


def selection_error(governance: Mapping) -> str | None:
    if reason := context_error(governance):
        return reason
    context, proposed = governance["context"], governance["proposal"]
    inventory, author, selected = context["inventory"], context["author"], proposed["auditor"]
    if selected is None:
        return "governance.auditor_required"
    if model_key(selected) is None:
        return "governance.auditor_unknown"
    if selected not in inventory["members"]:
        return "governance.identity_mismatch"
    if model_key(author) == model_key(selected):
        if inventory_status(inventory) != "singleton" or not proposed["allow_same_model"]:
            return "audit.same_model"
    elif proposed["allow_same_model"]:
        return "governance.identity_mismatch"
    return None


def auto_proposal(governance: Mapping) -> dict:
    proposed = plain(governance["proposal"])
    if governance["control_mode"] != "auto" or proposed["auditor"] is not None or context_error(governance):
        return proposed
    inventory = governance["context"]["inventory"]
    author = model_key(governance["context"]["author"])
    members = sorted(inventory["members"], key=lambda m: (m["provider_id"], m["model_id"]))
    eligible = [m for m in members if model_key(m) is not None and model_key(m) != author]
    singleton = inventory_status(inventory) == "singleton"
    if eligible or singleton:
        proposed.update(auditor=plain(eligible[0] if eligible else members[0]), allow_same_model=singleton)
    return proposed


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


def decision_error(run_id: str, governance: Mapping, decision: Mapping) -> str | None:
    fingerprint = canonical_digest(decision)
    prior = next((r for r in governance["receipts"] if r["id"] == decision["receipt_id"]), None)
    if prior:
        if fingerprint in {prior["fingerprint"], prior["presentation_fingerprint"]}:
            return "inert"
        presentation = {k: v for k, v in decision.items()
                        if k not in {"amendment", "change_request"}}
        presentation["outcome"] = "present"
        if (prior["outcome"] != "present" or decision["outcome"] == "present" or
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
    if decision["outcome"] == "request_changes" and expected == "auto":
        # Guidance is deliberative host_ui feedback; auto has no human to author it.
        return "governance.approval_unavailable"
    if decision["outcome"] == "present":
        return context_error(governance) if expected == "host_ui" else "governance.approval_unavailable"
    if expected == "host_ui" and prior is None:
        return context_error(governance) or "governance.receipt_replay"
    return None


def change_request_valid(governance: Mapping) -> bool:
    """Stored guidance refers to a displayed (past or current) revision and is never blank."""
    request = governance["change_request"]
    if request is None:
        return True
    return (bool(request["text"].strip())
            and 0 <= request["plan_revision"] <= governance["plan_revision"]
            and governance["control_mode"] != "auto")


def invariant(doc: Mapping) -> bool:
    value = doc["governance"]
    if value["revision_limit"] != (AUTO_REVISION_LIMIT if value["control_mode"] == "auto" else MAX_REVISIONS):
        return False
    if value["revisions_used"] > value["revision_limit"] or value["revisions_used"] > value["plan_revision"]:
        return False
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
    if not change_request_valid(value):
        return False
    if value["state"] == "approved":
        return (value["change_request"] is None
                and value["approved_digest"] == value["proposal_digest"] and bool(receipts)
                and selection_error(value) is None
                and value["proposal"]["modes"] == doc["modes"]
                and all(value["proposal"]["budgets"][k] == doc["budgets"][k] for k in CEILINGS))
    return True
