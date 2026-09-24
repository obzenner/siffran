"""Assemble immutable snapshots from reachable committed history and D5 capture."""
from __future__ import annotations

import json
import re
from typing import Any

from core.evaluation import (EvaluationSnapshot, claim_digest, digest, frozen_scope_invalid,
                             valid_graph)
from core.freshness import ActiveSpikeHead, FileBinding
from core.records import Artifact
from core.run import OperationalState
from core.governance import canonical_digest, proposal_body
from . import protocol as _proto
from .observation import build_observation_snapshot
from .run_state import encode_state

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class HistoryCorrupt(ValueError):
    pass


class GraphInvalid(ValueError):
    pass


def state_digest(state: OperationalState) -> str:
    doc = encode_state(state)
    doc.pop("committed_artifact_head_id", None)
    return digest(doc)


def make_artifact(body: dict[str, Any]) -> Artifact:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return Artifact(digest(body), raw)


def _decode(value: Artifact) -> dict[str, Any]:
    try:
        body = json.loads(value.body)
    except (TypeError, ValueError) as exc:
        raise HistoryCorrupt("malformed reachable artifact") from exc
    if not isinstance(body, dict) or digest(body) != value.artifact_id:
        raise HistoryCorrupt("reachable artifact content address mismatch")
    return {"artifact_id": value.artifact_id, **body}


def _valid_digest(value: Any) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _manifest(value: Artifact) -> dict[str, Any]:
    item = _decode(value)
    required = {"artifact_id", "kind", "version", "parent", "artifact_ids",
                "observation_basis_id", "observation_digest", "next_state_digest"}
    if set(item) != required or item["kind"] != "transaction_manifest" or type(item["version"]) is not int or item["version"] != 1:
        raise HistoryCorrupt("malformed reachable manifest")
    parent = item["parent"]
    ids = item["artifact_ids"]
    if (parent is not None and not _valid_digest(parent)) or not isinstance(ids, list):
        raise HistoryCorrupt("malformed manifest references")
    if any(not _valid_digest(aid) for aid in ids) or len(ids) != len(set(ids)):
        raise HistoryCorrupt("malformed or duplicate manifest artifact IDs")
    if (type(item["observation_basis_id"]) is not str or not item["observation_basis_id"]
            or not _valid_digest(item["observation_digest"])
            or not _valid_digest(item["next_state_digest"])):
        raise HistoryCorrupt("malformed manifest witnesses")
    return item


def traverse_history(state: OperationalState, stored: Any) -> tuple[dict[str, Any], ...]:
    head = state.committed_artifact_head_id
    if not _valid_digest(head):
        raise HistoryCorrupt("missing committed manifest head")
    values = getattr(stored, "value", stored)
    if values is None:
        raise HistoryCorrupt("missing artifact store")
    by_id: dict[str, Artifact] = {}
    try:
        for value in values:
            if not isinstance(value, Artifact) or not _valid_digest(value.artifact_id):
                continue  # unreachable malformed physical orphan is not authoritative
            prior = by_id.get(value.artifact_id)
            if prior is not None and prior != value:
                raise HistoryCorrupt("colliding physical artifact IDs")
            by_id[value.artifact_id] = value
    except TypeError as exc:
        raise HistoryCorrupt("artifact store is not iterable") from exc
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor: str | None = head
    while cursor is not None:
        if cursor in seen:
            raise HistoryCorrupt("cyclic manifest chain")
        seen.add(cursor)
        value = by_id.get(cursor)
        if value is None:
            raise HistoryCorrupt("missing reachable manifest")
        item = _manifest(value)
        chain.append(item)
        cursor = item["parent"]
    chain.reverse()
    if not chain or chain[0]["parent"] is not None:
        raise HistoryCorrupt("manifest chain lacks genesis")
    ordered: list[dict[str, Any]] = []
    referenced: set[str] = set()
    parent: str | None = None
    for item in chain:
        if item["parent"] != parent:
            raise HistoryCorrupt("broken manifest parent")
        for aid in item["artifact_ids"]:
            if aid in referenced:
                raise HistoryCorrupt("duplicate committed artifact reference")
            value = by_id.get(aid)
            if value is None:
                raise HistoryCorrupt("missing committed domain artifact")
            domain = _decode(value)
            if domain.get("kind") == "transaction_manifest":
                raise HistoryCorrupt("manifest referenced as domain artifact")
            referenced.add(aid)
            ordered.append(domain)
        parent = item["artifact_id"]
    if chain[-1]["next_state_digest"] != state_digest(state):
        raise HistoryCorrupt("latest manifest state witness mismatch")
    return tuple(ordered)


def graph_from_history(state: OperationalState, history: tuple[dict[str, Any], ...],
                       *, required: bool) -> dict[str, Any] | None:
    selected = state.selected_graph_artifact_id
    if selected is None:
        if state.frozen_claim_ids is not None or state.frozen_semantic_digest is not None:
            raise HistoryCorrupt("frozen semantic identity has no selected graph")
        if required:
            raise GraphInvalid("no selected graph")
        return None
    item = next((a for a in history if a["artifact_id"] == selected), None)
    graph = item.get("graph") if item and item.get("kind") == "graph" else None
    if not valid_graph(graph):
        raise HistoryCorrupt("selected graph is missing, malformed, or structurally invalid")
    if frozen_scope_invalid(state, graph):
        raise HistoryCorrupt("selected graph conflicts with frozen semantic identity")
    return graph


def active_spike_heads(history: tuple[dict[str, Any], ...], graph: dict[str, Any] | None) -> tuple[ActiveSpikeHead, ...]:
    if graph is None:
        return ()
    claims = {c["id"]: c for c in graph["claims"]}
    latest: dict[str, dict[str, Any]] = {}
    for item in history:
        claim = claims.get(item.get("claim_id"))
        if (item.get("kind") == "spike" and claim is not None
                and item.get("claim_digest") == claim_digest(claim)):
            latest[claim["id"]] = item
    heads = []
    for claim_id in sorted(latest):
        item = latest[claim_id]
        bindings = tuple(FileBinding(b["path"], b["sha256"]) for b in item["file_bindings"])
        heads.append(ActiveSpikeHead(item["artifact_id"], claim_id,
                                     item["harness_request_id"], bindings))
    return tuple(heads)


def validate_investigation_history(state: OperationalState,
                                   history: tuple[dict[str, Any], ...]) -> None:
    evidence_kinds = {"research", "spike_request", "spike", "attribution", "audit_verdict"}
    evidence = [item for item in history if item.get("kind") in evidence_kinds]
    if not evidence:
        return
    if state.route_stamp is None or state.investigation_stamp is None:
        raise HistoryCorrupt("investigation evidence has no route-order witnesses")
    for item in evidence:
        route_stamp = item.get("route_stamp")
        investigation_stamp = item.get("investigation_stamp")
        if (type(route_stamp) is not int or type(investigation_stamp) is not int
                or route_stamp != state.route_stamp
                or investigation_stamp != state.investigation_stamp):
            raise HistoryCorrupt("investigation evidence conflicts with route-order witnesses")


def assemble(state: OperationalState, stored: Any, workspace: Any, *, run_id: str,
             profile_id: str, command: dict[str, Any], require_graph: bool = False) -> EvaluationSnapshot:
    history = traverse_history(state, stored)
    validate_investigation_history(state, history)
    graph = graph_from_history(state, history, required=require_graph)
    if canonical_digest(proposal_body(state.goal, graph, state.governance)) != state.governance["proposal_digest"]:
        raise HistoryCorrupt("proposal digest conflicts with selected graph/context")
    observation = build_observation_snapshot(active_spike_heads(history, graph), workspace)
    profile = _proto._PROFILES[profile_id]
    return EvaluationSnapshot(
        state=state, history=history, graph=graph,
        observations=observation.observations, observation_basis_id=observation.basis_id,
        observation_digest=observation.digest, run_id=run_id,
        contract_id=_proto._PUBLIC_CONTRACT["id"],
        contract_version=_proto._PUBLIC_CONTRACT["version"], contract_digest=_proto._DIGEST,
        bootstrap_requirements=_proto._BOOTSTRAP_REQUIREMENTS,
        bootstrap_operations=_proto._BOOTSTRAP_OPERATIONS,
        profile_id=profile_id, host_tier=profile["current_tier"],
        host_audit_execution=profile["audit_execution"], command=command,
    )
