"""Assemble immutable snapshots from reachable committed history and the workspace capture."""
from __future__ import annotations

import json
import re
from typing import Any

from core.canonical import canonical_json
from core.evaluation import (EvaluationSnapshot, active_spike_heads, digest,
                             frozen_scope_invalid, graph_refusal)
from core import citations
from core.freshness import (FileObservation, FreshnessContractError, ObservationState, observations_digest,
                            required_paths)
from .ports import ObservationSnapshot
from core.records import Artifact
from core.run import OperationalState
from core.governance import proposal_digest
from . import protocol as _proto
from .history_records import (
    HISTORY_KINDS, MANIFEST_KEYS, MANIFEST_KIND, MANIFEST_VERSION, OBSERVATION_BASIS_KEYS,
    OBSERVATION_BASIS_KIND, OBSERVATION_BASIS_VERSION, OBSERVATION_ROW_KEYS,
    OBSERVATION_SOURCE_KEYS, POLICY_INPUT_KEYS,
)
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
    return Artifact(digest(body), canonical_json(body))


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


def _wrong_version(value: Any, expected: int) -> bool:
    return type(value) is not int or value != expected


def _manifest(value: Artifact) -> dict[str, Any]:
    item = _decode(value)
    version = item.get("version")
    if (set(item) != MANIFEST_KEYS or item.get("kind") != MANIFEST_KIND
            or _wrong_version(version, MANIFEST_VERSION)):
        raise HistoryCorrupt("malformed reachable manifest")
    parent = item["parent"]
    ids = item["artifact_ids"]
    if (parent is not None and not _valid_digest(parent)) or not isinstance(ids, list):
        raise HistoryCorrupt("malformed manifest references")
    if any(not _valid_digest(aid) for aid in ids) or len(ids) != len(set(ids)):
        raise HistoryCorrupt("malformed or duplicate manifest artifact IDs")
    if (type(item["observation_basis_id"]) is not str or not item["observation_basis_id"]
            or not _valid_digest(item["observation_digest"])
            or not _valid_digest(item["next_state_digest"])
            or not _valid_digest(item["observation_basis_digest"])):
        raise HistoryCorrupt("malformed manifest witnesses")
    return item


def _observation_basis(value: Artifact) -> dict[str, Any]:
    item = _decode(value)
    if (set(item) != OBSERVATION_BASIS_KEYS
            or item["kind"] != OBSERVATION_BASIS_KIND
            or _wrong_version(item["version"], OBSERVATION_BASIS_VERSION)
            or not isinstance(item["sources"], list) or not item["sources"]):
        raise HistoryCorrupt("malformed observation basis")
    policy = item["policy_inputs"]
    if (not isinstance(policy, dict) or set(policy) != POLICY_INPUT_KEYS
            or not all(type(policy[key]) is str and policy[key]
                       for key in ("contract_id", "contract_version", "profile_id"))
            or not _valid_digest(policy["contract_digest"])
            or item["clock_inputs"] != []):
        raise HistoryCorrupt("malformed observation basis")
    for source in item["sources"]:
        if (not isinstance(source, dict)
                or set(source) != OBSERVATION_SOURCE_KEYS
                or not isinstance(source["name"], str) or not source["name"]
                or not isinstance(source["basis_id"], str) or not source["basis_id"]
                or not _valid_digest(source["digest"])
                or not isinstance(source["observations"], list)):
            raise HistoryCorrupt("malformed observation basis source")
        observations = []
        try:
            for row in source["observations"]:
                if not isinstance(row, dict) or set(row) != OBSERVATION_ROW_KEYS:
                    raise ValueError("malformed persisted observation")
                observations.append(FileObservation(
                    row["path"], ObservationState(row["state"]), row["sha256"]))
        except (TypeError, ValueError) as exc:
            raise HistoryCorrupt("malformed persisted observation") from exc
        if observations_digest(observations) != source["digest"]:
            raise HistoryCorrupt("persisted observation digest mismatch")
    return item


def index_artifacts(stored: Any) -> dict[str, Artifact]:
    """The physical artifacts by content address; an unreachable malformed orphan is not authoritative and is skipped."""
    values = getattr(stored, "value", stored)
    if values is None:
        raise HistoryCorrupt("missing artifact store")
    by_id: dict[str, Artifact] = {}
    try:
        for value in values:
            if not isinstance(value, Artifact) or not _valid_digest(value.artifact_id):
                continue
            prior = by_id.get(value.artifact_id)
            if prior is not None and prior != value:
                raise HistoryCorrupt("colliding physical artifact IDs")
            by_id[value.artifact_id] = value
    except TypeError as exc:
        raise HistoryCorrupt("artifact store is not iterable") from exc
    return by_id


def manifest_chain(head: Any, by_id: dict[str, Artifact]) -> list[dict[str, Any]]:
    """The committed manifests from genesis to ``head``, following ``parent`` links; each must be present and well formed."""
    if not _valid_digest(head):
        raise HistoryCorrupt("missing committed manifest head")
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
    return chain


def committed_artifacts(chain: list[dict[str, Any]], by_id: dict[str, Artifact],
                        before_manifest: Any = None) -> tuple[dict[str, Any], ...]:
    """The domain artifacts the chain references, in commit order. A referenced artifact that is missing, repeated, or itself
    a manifest is corrupt history. ``before_manifest`` (optional) is called with each manifest first, to add its own checks."""
    ordered: list[dict[str, Any]] = []
    referenced: set[str] = set()
    parent: str | None = None
    for item in chain:
        if item["parent"] != parent:
            raise HistoryCorrupt("broken manifest parent")
        if before_manifest is not None:
            before_manifest(item)
        for aid in item["artifact_ids"]:
            if aid in referenced:
                raise HistoryCorrupt("duplicate committed artifact reference")
            value = by_id.get(aid)
            if value is None:
                raise HistoryCorrupt("missing committed domain artifact")
            domain = _decode(value)
            if domain.get("kind") == MANIFEST_KIND:
                raise HistoryCorrupt("manifest referenced as domain artifact")
            referenced.add(aid)
            ordered.append(domain)
        parent = item["artifact_id"]
    return tuple(ordered)


def traverse_history(state: OperationalState, stored: Any) -> tuple[dict[str, Any], ...]:
    by_id = index_artifacts(stored)
    chain = manifest_chain(state.committed_artifact_head_id, by_id)
    prior_basis: str | None = None
    seen_bases: set[str] = set()

    def check_basis(item: dict[str, Any]) -> None:
        nonlocal prior_basis
        basis_digest = item["observation_basis_digest"]
        basis_value = by_id.get(basis_digest)
        if basis_value is None:
            raise HistoryCorrupt("missing observation basis")
        _observation_basis(basis_value)
        changed = basis_digest != prior_basis
        basis_appended = basis_digest in item["artifact_ids"]
        if basis_appended != (changed and basis_digest not in seen_bases):
            raise HistoryCorrupt("observation basis deduplication mismatch")
        seen_bases.add(basis_digest)
        prior_basis = basis_digest

    ordered = committed_artifacts(chain, by_id, check_basis)
    if chain[-1]["next_state_digest"] != state_digest(state):
        raise HistoryCorrupt("latest manifest state witness mismatch")
    # Redundant with the state-digest witness above, but preserves the more specific diagnosis.
    if chain[-1]["observation_basis_digest"] != state.observation_basis_digest:
        raise HistoryCorrupt("latest observation basis state witness mismatch")
    return ordered


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
    if graph_refusal(graph, admission=False) is not None:
        raise HistoryCorrupt("selected graph is missing, malformed, or structurally invalid")
    if frozen_scope_invalid(state, graph):
        raise HistoryCorrupt("selected graph conflicts with frozen semantic identity")
    return graph


def validate_investigation_history(state: OperationalState,
                                   history: tuple[dict[str, Any], ...]) -> None:
    # Every kind of a committed manifest but the graph and the observation basis carries the route-order witnesses.
    evidence_kinds = set(HISTORY_KINDS) - {"graph", OBSERVATION_BASIS_KIND}
    # The consent to end a run (a terminal presentation or its answer) is not investigation evidence: ending never requires
    # starting the work, so it may be recorded before the investigation and must stay valid after it. It still
    # carries the run's route witness.
    for item in history:
        if item.get("kind") in {"evidence_presentation", "evidence_confirmation"} and (
                isinstance(item.get("terminal"), (dict, bool)) and item.get("terminal")):
            if type(item.get("route_stamp")) is not int or item["route_stamp"] != state.route_stamp:
                raise HistoryCorrupt("terminal consent conflicts with the route-order witness")
    evidence = [item for item in history if item.get("kind") in evidence_kinds
                and not (isinstance(item.get("terminal"), (dict, bool)) and item.get("terminal"))]
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


def recorded_observations(history: tuple[dict[str, Any], ...], state: OperationalState,
                          heads: Any) -> ObservationSnapshot | None:
    """What the workspace held over ``heads`` when the run's last transaction ran: the workspace source of the run's latest
    recorded observation basis (the one its state names), or ``None`` when that record does not hold every path the heads
    bind. Nothing is read from the workspace; the record is what the run was judged against then."""
    basis = next((item for item in history if item["artifact_id"] == state.observation_basis_digest
                  and item.get("kind") == OBSERVATION_BASIS_KIND), None)
    source = next((s for s in (basis or {}).get("sources", ()) if s.get("name") == "workspace"), None)
    if source is None:
        return None
    rows = {row["path"]: row for row in source["observations"]}
    paths = required_paths(heads)
    if any(path not in rows for path in paths):
        return None
    observations = tuple(FileObservation(p, ObservationState(rows[p]["state"]), rows[p]["sha256"]) for p in paths)
    return ObservationSnapshot(observations, source["basis_id"], observations_digest(observations))


def assemble(state: OperationalState, stored: Any, workspace: Any, *, run_id: str,
             profile_id: str, command: dict[str, Any], require_graph: bool = False) -> EvaluationSnapshot:
    history = traverse_history(state, stored)
    validate_investigation_history(state, history)
    graph = graph_from_history(state, history, required=require_graph)
    if proposal_digest(state.goal, state.governance) != state.governance["proposal_digest"]:
        raise HistoryCorrupt("proposal digest conflicts with selected graph/context")
    observation = build_observation_snapshot(active_spike_heads(history, graph), workspace)
    profile = _proto.host_profile(profile_id)
    cited = _cited(command, workspace)
    return EvaluationSnapshot(
        state=state, history=history, graph=graph, contract=_proto.CONTRACT_VIEW,
        observations=observation.observations, observation_basis_id=observation.basis_id,
        observation_digest=observation.digest, run_id=run_id,
        **_proto.policy_inputs(profile_id),
        host_tier=profile["current_tier"],
        host_audit_execution=profile["audit_execution"], command=command, cited=cited,
        workspace_root=_root(workspace) if command.get("type") == "ObserveAction" else None,
    )


def _root(workspace: Any) -> str | None:
    root = getattr(workspace, "root", None)
    return None if root is None else str(root)


def _cited(command: dict[str, Any], workspace: Any) -> Any:
    """The workspace's capture of the repository file a research action cites (``core.citations``),
    or ``None``: not a research action, a source not checked there, a path the workspace refuses, or
    no workspace (the core then reads the file as unavailable)."""
    action = command.get("action") if command.get("type") == "ObserveAction" else None
    if not action or action.get("kind") != "research" or workspace is None:
        return None
    path = citations.cited_file(action["source_kind"], action["payload"]["source_ref"])
    if path is None:
        return None
    try:
        (captured,) = workspace.observe((path,)).files
    except (ValueError, OSError, FreshnessContractError):
        return None
    return captured
