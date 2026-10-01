"""Strict v2 persisted-state classification and codec (D6 spec §4–5).

Only the exact current protocol/schema identity is decoded. Every other value is corrupt; there is
no old-state category, field default, migration, or mutation.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import jsonschema

from core.evaluation import SPAWN_BUDGET
from core.run import OperationalState
from core.governance import invariant
from . import protocol as _proto

_PROTOCOL = _proto.protocol_id()
_STATE_SCHEMA_ID = _proto.state_schema_id()
_AUDIT_DOSSIER = jsonschema.Draft202012Validator(
    {"$ref": "#/$defs/argumentView", "$defs": _proto.response_schema()["$defs"]},
    registry=_proto.schema_registry())


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


def decode_state(doc: dict[str, Any]) -> OperationalState:
    return OperationalState(
        protocol=doc["protocol"], state_schema=doc["state_schema"], goal=doc["goal"],
        invocation=doc["invocation"], status=doc["status"], budgets=doc["budgets"],
        governance=doc["governance"],
        selected_graph_artifact_id=doc["selected_graph_artifact_id"],
        frozen_claim_ids=None if doc["frozen_claim_ids"] is None else tuple(doc["frozen_claim_ids"]),
        frozen_semantic_digest=doc["frozen_semantic_digest"],
        route_stamp=doc["route_stamp"], investigation_stamp=doc["investigation_stamp"],
        stamp_seq=doc["stamp_seq"], last_derivation_digest=doc["last_derivation_digest"],
        children=tuple(doc["children"]), observation_basis_digest=doc["observation_basis_digest"],
        committed_artifact_head_id=doc["committed_artifact_head_id"],
    )


def encode_state(state: OperationalState) -> dict[str, Any]:
    return {
        "protocol": state.protocol, "state_schema": state.state_schema, "goal": state.goal,
        "invocation": _thaw(state.invocation), "status": state.status,
        "budgets": _thaw(state.budgets),
        "governance": _thaw(state.governance),
        "selected_graph_artifact_id": state.selected_graph_artifact_id,
        "frozen_claim_ids": None if state.frozen_claim_ids is None else list(state.frozen_claim_ids),
        "frozen_semantic_digest": state.frozen_semantic_digest,
        "route_stamp": state.route_stamp, "investigation_stamp": state.investigation_stamp,
        "stamp_seq": state.stamp_seq, "last_derivation_digest": state.last_derivation_digest,
        "children": _thaw(state.children),
        "observation_basis_digest": state.observation_basis_digest,
        "committed_artifact_head_id": state.committed_artifact_head_id,
    }


@dataclass(frozen=True)
class Classification:
    """Typed immutable identity classification before semantic decode."""

    kind: str
    state: OperationalState | None = None


def governed_progress_is_valid(doc: Mapping[str, Any]) -> bool:
    """Require successful configuration approval before governed work can exist."""
    governance = doc["governance"]
    governed_progress = (doc["investigation_stamp"] is not None
                         or bool(doc["children"])
                         or doc["status"] == "converged")
    successful_approval = (governance["first_approval"] is True
                           and any(receipt["outcome"] == "approve"
                                   for receipt in governance["receipts"]))
    return not governed_progress or successful_approval


def _procedural_ok(doc: dict) -> bool:
    """Check governance, capability, progress, counters, stamps, and deadlines."""
    if not invariant(doc):
        return False
    context = doc["governance"]["context"]
    if _proto.APPROVAL_CAPABILITY.get(context["ingress"]) != context["approval_capability"]:
        return False
    if not governed_progress_is_valid(doc):
        return False
    seen: set[str] = set()
    charged = {"investigation": 0, "audit": 0}
    for ch in doc.get("children", []):
        cid = ch.get("child_id")
        if cid in seen:
            return False
        seen.add(cid)
        resource_class = ch.get("resource_class")
        if resource_class == "audit":
            if (ch.get("purpose") != "audit" or not isinstance(ch.get("audit_operation_id"), str)
                    or not isinstance(ch.get("audit_argument"), dict)
                    or not _AUDIT_DOSSIER.is_valid(ch["audit_argument"])
                    or not isinstance(ch.get("audit_role_profile"), str)):
                return False
        elif (ch.get("audit_operation_id") is not None or ch.get("audit_argument") is not None
              or ch.get("audit_role_profile") is not None):
            return False
        if not ch.get("refunded"):
            charged[resource_class] += 1
        dl = ch.get("deadline")
        if dl is not None and (
            isinstance(dl, bool) or not isinstance(dl, (int, float)) or not math.isfinite(dl)
        ):
            return False
    if sum(ch["resource_class"] == "audit" and ch["state"] in {"reserved", "launching", "pending"}
           for ch in doc.get("children", [])) > 1:
        return False
    b = doc.get("budgets", {})
    if b.get("passes_used", 0) > b.get("max_passes", 0):
        return False
    for resource_class, (limit, used, _) in SPAWN_BUDGET.items():
        if b.get(used, 0) > b.get(limit, 0) or b.get(used) != charged[resource_class]:
            return False
    seq = doc.get("stamp_seq", 0)
    route = doc.get("route_stamp")
    investigation = doc.get("investigation_stamp")
    if route is not None and route < 1:
        return False
    if investigation is not None and (route is None or investigation < 1 or route >= investigation):
        return False
    if (doc.get("children") or doc.get("status") == "converged") and investigation is None:
        return False
    frozen = doc.get("frozen_claim_ids")
    if (frozen is None) != (doc.get("frozen_semantic_digest") is None):
        return False
    for key in ("route_stamp", "investigation_stamp"):
        s = doc.get(key)
        if s is not None and s > seq:
            return False
    return True


def classify_and_decode(raw: Any) -> Classification:
    """Decode only an exact valid v2 document; classify every other value as corrupt."""
    if (not isinstance(raw, dict) or raw.get("protocol") != _PROTOCOL
            or raw.get("state_schema") != _STATE_SCHEMA_ID):
        return Classification("current_corrupt")
    try:
        _proto.schema_validator("state").validate(raw)
    except jsonschema.ValidationError:
        return Classification("current_corrupt")
    if not _procedural_ok(raw):
        return Classification("current_corrupt")
    return Classification("valid", decode_state(raw))
