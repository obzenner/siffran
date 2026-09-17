"""Strict v2 persisted-state classification and codec (D6 spec §4–5).

Consumes the internal state schema and protocol identity loaded once by ``application.protocol``
(no duplicate root/json/PublicContract loads). ``classify_and_decode(raw)`` performs only identity
classification before semantic decoding, returning a typed frozen ``Classification`` with ``kind``
(``old_unsupported`` | ``current_corrupt`` | ``valid``) and, only when valid, a recursively
immutable ``RunState`` whose ``encode()`` reproduces the exact closed document without defaults.
Procedural invariants (unique child IDs, counter bounds, stamp bounds, finite deadlines) are owned
by the codec, not the JSON schema. No defaults, migration, or mutation.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import jsonschema

from core.run import OperationalState
from . import protocol as _proto

_STATE_SCHEMA = _proto._STATE_SCHEMA
_PROTOCOL = _proto._PROTOCOL
_STATE_SCHEMA_ID = _proto._STATE_SCHEMA_ID


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


def decode_state(doc: dict[str, Any]) -> OperationalState:
    return OperationalState(
        protocol=doc["protocol"], state_schema=doc["state_schema"], goal=doc["goal"],
        status=doc["status"], modes=doc["modes"], budgets=doc["budgets"],
        selected_graph_artifact_id=doc["selected_graph_artifact_id"],
        frozen_claim_ids=None if doc["frozen_claim_ids"] is None else tuple(doc["frozen_claim_ids"]),
        route_stamp=doc["route_stamp"], investigation_stamp=doc["investigation_stamp"],
        stamp_seq=doc["stamp_seq"], last_derivation_digest=doc["last_derivation_digest"],
        children=tuple(doc["children"]), committed_artifact_head_id=doc["committed_artifact_head_id"],
    )


def encode_state(state: OperationalState) -> dict[str, Any]:
    return {
        "protocol": state.protocol, "state_schema": state.state_schema, "goal": state.goal,
        "status": state.status, "modes": _thaw(state.modes), "budgets": _thaw(state.budgets),
        "selected_graph_artifact_id": state.selected_graph_artifact_id,
        "frozen_claim_ids": None if state.frozen_claim_ids is None else list(state.frozen_claim_ids),
        "route_stamp": state.route_stamp, "investigation_stamp": state.investigation_stamp,
        "stamp_seq": state.stamp_seq, "last_derivation_digest": state.last_derivation_digest,
        "children": _thaw(state.children),
        "committed_artifact_head_id": state.committed_artifact_head_id,
    }


@dataclass(frozen=True)
class Classification:
    """Typed immutable identity classification before semantic decode."""

    kind: str
    state: OperationalState | None = None


def _procedural_ok(doc: dict) -> bool:
    """Check unique child IDs, counter bounds, stamp bounds, and finite deadlines."""
    seen: set[str] = set()
    for ch in doc.get("children", []):
        cid = ch.get("child_id")
        if cid in seen:
            return False
        seen.add(cid)
        if ch.get("purpose") == "audit":
            if (not isinstance(ch.get("audit_operation_id"), str)
                    or not isinstance(ch.get("audit_argument"), dict)
                    or not isinstance(ch.get("audit_role_profile"), str)):
                return False
        elif (ch.get("audit_operation_id") is not None or ch.get("audit_argument") is not None
              or ch.get("audit_role_profile") is not None):
            return False
        dl = ch.get("deadline")
        if dl is not None and (
            isinstance(dl, bool) or not isinstance(dl, (int, float)) or not math.isfinite(dl)
        ):
            return False
    b = doc.get("budgets", {})
    if b.get("passes_used", 0) > b.get("max_passes", 0):
        return False
    if b.get("spawns_used", 0) > b.get("max_spawns", 0):
        return False
    seq = doc.get("stamp_seq", 0)
    for key in ("route_stamp", "investigation_stamp"):
        s = doc.get(key)
        if s is not None and s > seq:
            return False
    return True


def classify_and_decode(raw: Any) -> Classification:
    """Classify identity before decode (D6 spec §5).

    1. non-object or exact protocol==empirica/v2 + missing/wrong state_schema → current_corrupt;
    2. object with missing/null/empty/v1/future/unknown protocol → old_unsupported;
    3. exact protocol and exact state_schema → validate full schema + procedural invariants;
    4. valid → immutable RunState; invalid → current_corrupt.

    Never selects artifacts, defaults fields, migrates, infers terminality, or mutates input.
    """
    if not isinstance(raw, dict):
        return Classification("current_corrupt")
    if raw.get("protocol") == _PROTOCOL:
        if raw.get("state_schema") != _STATE_SCHEMA_ID:
            return Classification("current_corrupt")
        try:
            jsonschema.validate(instance=raw, schema=_STATE_SCHEMA)
        except jsonschema.ValidationError:
            return Classification("current_corrupt")
        if not _procedural_ok(raw):
            return Classification("current_corrupt")
        return Classification("valid", decode_state(raw))
    return Classification("old_unsupported")
