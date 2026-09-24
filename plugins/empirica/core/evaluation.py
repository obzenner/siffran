"""Pure command evaluation over immutable Empirica snapshots."""
from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from .freshness import ActiveSpikeHead, FileBinding, evaluate_freshness
from .run import OperationalState
from . import governance

SPAWN_BUDGET = {"investigation": ("max_spawns", "spawns_used", "spawn"),
                "audit": ("max_audit_spawns", "audit_spawns_used", "audit_spawn")}


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(v) for v in value)
    return value


def digest(value: Any) -> str:
    raw = json.dumps(_plain(value), sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def artifact(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = {"kind": kind, **payload}
    return {"artifact_id": digest(body), "body": body}


@dataclass(frozen=True)
class EvaluationSnapshot:
    state: OperationalState
    history: tuple[dict[str, Any], ...]
    graph: dict[str, Any] | None
    observations: tuple[Any, ...] = ()
    observation_basis_id: str = ""
    observation_digest: str = ""
    run_id: str = ""
    contract_id: str = "empirica-public-contract"
    contract_version: str = "2.0.0"
    contract_digest: str = ""
    bootstrap_requirements: tuple[tuple[str, str, str], ...] = ()
    bootstrap_operations: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()
    profile_id: str = ""
    host_tier: str = "observational"
    host_audit_execution: str = "unavailable"
    command: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "history", _freeze(self.history))
        object.__setattr__(self, "graph", _freeze(self.graph))
        object.__setattr__(self, "command", _freeze(self.command))


@dataclass(frozen=True)
class StateIntent:
    state: OperationalState
    artifacts: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ClaimDerivation:
    states: Mapping[str, str]
    blockers: Mapping[str, str]
    scope: tuple[str, ...]


@dataclass(frozen=True)
class Decision:
    result_type: str
    intent: StateIntent
    reason_code: str | None = None
    parameters: tuple[tuple[str, Any], ...] = ()
    affected_obligation_id: str | None = None


def valid_graph(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"root", "claims", "edges"}:
        return False
    claims = value.get("claims")
    if not isinstance(value.get("root"), str) or not isinstance(claims, (list, tuple)) or not 1 <= len(claims) <= 32:
        return False
    ids: list[str] = []
    for claim in claims:
        if (not isinstance(claim, Mapping) or set(claim) != {"id", "text", "gating", "kind"}
                or not isinstance(claim["id"], str) or not claim["id"] or not isinstance(claim["text"], str)
                or len(claim["id"]) > 128 or not 1 <= len(claim["text"]) <= 2048
                or type(claim["gating"]) is not bool
                or claim["kind"] not in {"ordinary", "needs-experiment", "needs-decision"}):
            return False
        ids.append(claim["id"])
    if len(ids) != len(set(ids)) or value["root"] not in ids or not isinstance(value["edges"], (list, tuple)):
        return False
    if len(value["edges"]) > 128:
        return False
    children: dict[str, list[str]] = {claim_id: [] for claim_id in ids}
    seen_edges: set[tuple[str, str]] = set()
    indegree = {claim_id: 0 for claim_id in ids}
    for edge in value["edges"]:
        if (not isinstance(edge, Mapping) or set(edge) != {"from", "to", "type"}
                or edge["from"] not in children or edge["to"] not in children
                or edge["type"] != "SupportedBy" or edge["from"] == edge["to"]):
            return False
        pair = (edge["from"], edge["to"])
        if pair in seen_edges:
            return False
        seen_edges.add(pair)
        children[pair[0]].append(pair[1])
        indegree[pair[1]] += 1
    queue = [claim_id for claim_id in ids if indegree[claim_id] == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(ids):
        return False
    reachable, stack = set(), [value["root"]]
    while stack:
        current = stack.pop()
        if current not in reachable:
            reachable.add(current)
            stack.extend(children[current])
    return len(reachable) == len(ids)


def frozen_semantic_digest(graph: Mapping[str, Any] | None,
                           frozen_ids: tuple[str, ...] | None) -> str | None:
    if graph is None or frozen_ids is None:
        return None
    claims = {claim["id"]: claim for claim in graph["claims"]}
    if any(claim_id not in claims for claim_id in frozen_ids):
        return None
    frozen = set(frozen_ids)
    payload = {
        "claims": [{key: claims[claim_id][key] for key in ("id", "text", "kind", "gating")}
                   for claim_id in frozen_ids],
        "edges": [{"from": source, "to": target, "type": edge_type}
                  for source, target, edge_type in sorted(
                      (edge["from"], edge["to"], edge["type"]) for edge in graph["edges"]
                      if edge["from"] in frozen and edge["to"] in frozen)],
    }
    return digest(payload)


def frozen_scope_invalid(state: OperationalState, graph: Mapping[str, Any] | None) -> bool:
    if state.frozen_claim_ids is None:
        return state.frozen_semantic_digest is not None
    observed = frozen_semantic_digest(graph, state.frozen_claim_ids)
    return observed is None or observed != state.frozen_semantic_digest


def claim_digest(claim: dict[str, Any]) -> str:
    return digest({"claim_id": claim["id"], "text": claim["text"]})


def active_evidence(snapshot: EvaluationSnapshot, claim: dict[str, Any]) -> list[dict[str, Any]]:
    cd = claim_digest(claim)
    evidence = [a for a in snapshot.history if a.get("kind") in {"research", "spike"} and a.get("claim_id") == claim["id"] and a.get("claim_digest") == cd]
    spikes = [a for a in evidence if a["kind"] == "spike"]
    active_spike = spikes[-1:] if spikes else []
    return [a for a in evidence if a["kind"] == "research"] + active_spike


def active_spike_heads(snapshot: EvaluationSnapshot) -> tuple[ActiveSpikeHead, ...]:
    if snapshot.graph is None:
        return ()
    heads = []
    for claim in snapshot.graph["claims"]:
        spikes = [a for a in active_evidence(snapshot, claim) if a["kind"] == "spike"]
        if spikes:
            item = spikes[-1]
            bindings = tuple(FileBinding(b["path"], b["sha256"]) for b in item["file_bindings"])
            heads.append(ActiveSpikeHead(item["artifact_id"], claim["id"], item["harness_request_id"], bindings))
    return tuple(heads)


def stale_artifact_ids(snapshot: EvaluationSnapshot) -> set[str]:
    if not snapshot.observations:
        return set()
    return {head.artifact_id for head in evaluate_freshness(active_spike_heads(snapshot), snapshot.observations).stale_heads}


def claim_conflicted(snapshot: EvaluationSnapshot, claim: dict[str, Any]) -> bool:
    return {a["outcome"] for a in active_evidence(snapshot, claim) if a["kind"] == "research"} >= {"supporting", "refuting"}


def effective_scope_ids(snapshot: EvaluationSnapshot) -> tuple[str, ...]:
    if snapshot.graph is None:
        return ()
    return (tuple(snapshot.state.frozen_claim_ids)
            if snapshot.state.frozen_claim_ids is not None else
            tuple(c["id"] for c in snapshot.graph["claims"] if c["gating"]))


def local_claim_state(snapshot: EvaluationSnapshot, claim: dict[str, Any],
                      stale: set[str] | None = None) -> str:
    evidence = active_evidence(snapshot, claim)
    research = [a for a in evidence if a["kind"] == "research"]
    supporting = any(a["outcome"] == "supporting" for a in research)
    refuting = any(a["outcome"] == "refuting" for a in research)
    spikes = [a for a in evidence if a["kind"] == "spike"]
    spike_failed = bool(spikes and spikes[-1]["outcome"] == "fail")
    spike_ok = bool(spikes and spikes[-1]["outcome"] == "pass"
                    and spikes[-1]["artifact_id"] not in (
                        stale_artifact_ids(snapshot) if stale is None else stale))
    if supporting and refuting and not spike_failed:
        return "open"
    if spike_failed or refuting:
        return "discarded"
    if claim["kind"] == "needs-decision":
        return "blocked"
    approved = supporting and (claim["kind"] == "ordinary" or
                               (claim["kind"] == "needs-experiment" and spike_ok))
    return "approved" if approved else "open"


def derive_claims(snapshot: EvaluationSnapshot) -> ClaimDerivation:
    if snapshot.graph is None:
        return ClaimDerivation(MappingProxyType({}), MappingProxyType({}), ())
    claims = snapshot.graph["claims"]
    ids = [claim["id"] for claim in claims]
    index = {claim_id: position for position, claim_id in enumerate(ids)}
    children: dict[str, list[str]] = {claim_id: [] for claim_id in ids}
    indegree = {claim_id: 0 for claim_id in ids}
    for edge in snapshot.graph["edges"]:
        children[edge["from"]].append(edge["to"])
        indegree[edge["to"]] += 1
    ready = deque(claim_id for claim_id in ids if indegree[claim_id] == 0)
    order: list[str] = []
    while ready:
        current = ready.popleft()
        order.append(current)
        for child in sorted(children[current], key=index.__getitem__):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    stale = stale_artifact_ids(snapshot)
    local = {claim["id"]: local_claim_state(snapshot, claim, stale) for claim in claims}
    scoped = effective_scope_ids(snapshot)
    scope = set(scoped)
    states: dict[str, str] = {}
    blockers: dict[str, str] = {}
    for claim_id in reversed(order):
        own = local[claim_id]
        if claim_id not in scope or own != "approved":
            states[claim_id] = own
            if own != "approved":
                blockers[claim_id] = claim_id
            continue
        failed = next((child for child in sorted(children[claim_id], key=index.__getitem__)
                       if child in scope and states[child] != "approved"), None)
        if failed is None:
            states[claim_id] = "approved"
        else:
            states[claim_id] = "open"
            blockers[claim_id] = blockers.get(failed, failed)
    return ClaimDerivation(MappingProxyType(states), MappingProxyType(blockers), scoped)


def _bootstrap_facts(snapshot: EvaluationSnapshot) -> dict[str, bool]:
    """Primitive bootstrap facts: pure core code, never contract expressions."""
    state, governed = snapshot.state, snapshot.state.governance
    return {
        "route.recorded": state.route_stamp is not None,
        "graph.selected": snapshot.graph is not None,
        "governance.approved": snapshot.graph is not None and governance.admission(governed) is None,
        "investigation.recorded": state.investigation_stamp is not None,
    }


def bootstrap_precondition(snapshot: EvaluationSnapshot, operation: str) -> tuple[str, str] | None:
    """Return the first unmet member of the finite, application-supplied operation binding."""
    operations = dict(snapshot.bootstrap_operations)
    if operation not in operations:
        raise ValueError(f"unknown bootstrap operation: {operation}")
    facts = _bootstrap_facts(snapshot)
    return next(((predicate, (governance.admission(snapshot.state.governance) or reason)
                  if predicate == "governance.approved" else reason)
                 for predicate, reason in operations[operation] if not facts[predicate]), None)


def bootstrap_status(snapshot: EvaluationSnapshot) -> dict[str, Any]:
    facts = _bootstrap_facts(snapshot)
    state, governed = snapshot.state, snapshot.state.governance
    active = []
    for predicate, obligation_id, must in snapshot.bootstrap_requirements:
        if ((predicate == "governance.approved" and not facts["graph.selected"])
                or (predicate == "investigation.recorded" and not facts["governance.approved"])):
            continue
        active.append({"id": obligation_id, "must": must,
                       "status": "satisfied" if facts[predicate] else "residual"})
    actions = []
    active_run = state.status == "active"
    if active_run:
        if not facts["route.recorded"]:
            actions.append("route.record")
        if not facts["graph.selected"]:
            actions.append("graph.record")
        elif not facts["governance.approved"]:
            context_error = governance.context_error(governed)
            if governance.interaction_error(governed):
                actions.append("residual.accept")
            elif context_error and governed["context"]["inventory"]["source"] != "unknown":
                actions.append("host.repair_context")
            else:
                actions.append("governance.propose")
        elif facts["route.recorded"] and not facts["investigation.recorded"]:
            actions.append("investigation.record")
    request_ready = active_run and bootstrap_precondition(snapshot, "configure_run") is None
    display_ready = (active_run and bootstrap_precondition(snapshot, "governance.present") is None
                     and governance.context_error(governed) is None
                     and governance.interaction_error(governed) is None)
    return {"active": active, "next_actions": actions,
            "request_ready": request_ready, "display_ready": display_ready}


def _decision(snapshot: EvaluationSnapshot, state: OperationalState, result: str = "Allow", artifacts: tuple[dict[str, Any], ...] = (), reason: str | None = None, parameters: dict[str, Any] | None = None, affected: str | None = None) -> Decision:
    return Decision(result, StateIntent(state, artifacts), reason, tuple((parameters or {}).items()), affected)


def _investigation_block(snapshot: EvaluationSnapshot) -> Decision | None:
    if missing := bootstrap_precondition(snapshot, "investigate"):
        predicate, reason = missing
        affected = "obligation.route" if predicate == "route.recorded" else None
        return _decision(snapshot, snapshot.state, "Block", reason=reason, affected=affected)
    if snapshot.state.investigation_stamp is None:
        return _decision(snapshot, snapshot.state, "Block", reason="investigation.required",
                         affected="obligation.investigation")
    return None


def plan_spike_request(snapshot: EvaluationSnapshot, action: Mapping[str, Any]) -> Decision:
    """Purely admit and seal a spike request against current graph/research."""
    state = snapshot.state
    blocked = _investigation_block(snapshot)
    if blocked is not None:
        return blocked
    if snapshot.graph is None or frozen_scope_invalid(state, snapshot.graph):
        return _decision(snapshot, state, "Block", reason="graph.invalid")
    claim = next((c for c in snapshot.graph["claims"] if c["id"] == action["claim_id"]), None)
    if claim is None:
        return _decision(snapshot, state, "Block", reason="graph.invalid")
    research = [a for a in active_evidence(snapshot, claim) if a["kind"] == "research" and a["outcome"] == "supporting"]
    if not research:
        return _decision(snapshot, state, "Block", reason="claim.spike_prerequisite_missing", affected="claim:" + claim["id"])
    sealed = artifact("spike_request", {
        "claim_id": claim["id"], "claim_digest": claim_digest(claim),
        "statement_digest": digest(action), "harness_request_id": digest(action),
        "command": action["command"], "command_digest": digest(action["command"]),
        "dependent_files": sorted(action["dependent_files"]),
        "prerequisite_research_ids": [a["artifact_id"] for a in research],
        "route_stamp": state.route_stamp, "investigation_stamp": state.investigation_stamp,
    })
    return _decision(snapshot, state, artifacts=(sealed,))


def plan_spike_result(snapshot: EvaluationSnapshot, request_body: Mapping[str, Any], facts: Any) -> Decision:
    """Purely admit immutable harness facts and plan one spike result artifact."""
    state = snapshot.state
    blocked = _investigation_block(snapshot)
    if blocked is not None:
        return blocked
    if snapshot.graph is None or frozen_scope_invalid(state, snapshot.graph):
        return _decision(snapshot, state, "Block", reason="graph.invalid")
    claim = next((c for c in snapshot.graph["claims"] if c["id"] == request_body["claim_id"]), None)
    if claim is None or claim_digest(claim) != request_body["claim_digest"]:
        return _decision(snapshot, state, "Block", reason="graph.invalid")
    prior = [a for a in snapshot.history if a.get("kind") == "spike" and a.get("claim_id") == claim["id"] and a.get("claim_digest") == request_body["claim_digest"]]
    result = artifact("spike", {
        "claim_id": claim["id"], "claim_digest": request_body["claim_digest"],
        "statement_digest": facts.result_digest,
        "harness_request_id": request_body["harness_request_id"],
        "command": request_body["command"], "command_digest": request_body["command_digest"],
        "prerequisite_research_ids": request_body["prerequisite_research_ids"],
        "file_bindings": [{"path": b.path, "sha256": b.sha256} for b in facts.file_bindings],
        "exit_code": facts.exit_code, "spike_gate": facts.gate.value,
        "outcome": "pass" if facts.exit_code == 0 else "fail",
        "supersedes": prior[-1]["artifact_id"] if prior else None,
        "route_stamp": state.route_stamp, "investigation_stamp": state.investigation_stamp,
    })
    return _decision(snapshot, state, artifacts=(result,))


def covered_artifact_ids(snapshot: EvaluationSnapshot) -> list[str]:
    """Return the exact active evidence set whose producer identity an audit covers."""
    if snapshot.graph is None or frozen_scope_invalid(snapshot.state, snapshot.graph):
        return []
    derivation = derive_claims(snapshot)
    gating = set(derivation.scope)
    return [artifact["artifact_id"] for claim in snapshot.graph["claims"]
            if claim["id"] in gating and derivation.states[claim["id"]] == "approved"
            for artifact in active_evidence(snapshot, claim)]


def valid_attribution(snapshot: EvaluationSnapshot, payload: Mapping[str, Any]) -> bool:
    """Validate trusted identity facts against the exact current audit operation."""
    subject = payload.get("subject_kind")
    if subject == "covered_actor":
        covered = list(payload.get("covered_artifact_ids", []))
        active = set(covered_artifact_ids(snapshot))
        return (payload.get("child_id") is None and bool(covered)
                and len(covered) == len(set(covered)) and set(covered) == active)
    if subject == "auditor":
        child_id = payload.get("child_id")
        return (payload.get("covered_artifact_ids") == []
                and isinstance(child_id, str)
                and any(child["child_id"] == child_id and child["resource_class"] == "audit"
                        and child["state"] == "pending" for child in snapshot.state.children))
    return False


def identity_pair(value: Mapping[str, Any] | None) -> tuple[str, str] | None:
    """Return a concrete dispatcher identity; tier/latest aliases are never identities."""
    if not value or value.get("observed_by") != "host":
        return None
    normalized = governance.model_key(value)
    return ("concrete-model/1", normalized) if normalized else None


def audit_attributions(
    snapshot: EvaluationSnapshot, verdict: Mapping[str, Any],
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """Select only identities bound to this verdict child and current evidence set."""
    attrs = [item for item in snapshot.history if item.get("kind") == "attribution"]
    auditor = next((item for item in reversed(attrs)
                    if item.get("subject_kind") == "auditor"
                    and item.get("child_id") == verdict.get("child_id")), None)
    active = set(covered_artifact_ids(snapshot))
    covered = next((item for item in reversed(attrs)
                    if item.get("subject_kind") == "covered_actor"
                    and item.get("child_id") is None
                    and bool(item.get("covered_artifact_ids"))
                    and len(item.get("covered_artifact_ids", []))
                    == len(set(item.get("covered_artifact_ids", [])))
                    and set(item.get("covered_artifact_ids", [])) == active), None)
    return auditor, covered


def audit_binding(snapshot: EvaluationSnapshot) -> dict[str, Any]:
    """Derive the exact audit dossier binding from the current immutable snapshot."""
    if snapshot.graph is None or frozen_scope_invalid(snapshot.state, snapshot.graph):
        return {}
    all_ids = [c["id"] for c in snapshot.graph["claims"]]
    derivation = derive_claims(snapshot)
    gating = list(derivation.scope)
    deferred = [cid for cid in all_ids if cid not in set(gating)]
    visible = [a["artifact_id"] for a in snapshot.history
               if a.get("kind") in {"research", "spike_request", "spike"}]
    reviewed = []
    for claim in snapshot.graph["claims"]:
        if claim["id"] in gating and derivation.states[claim["id"]] == "approved":
            reviewed.append({"claim_id": claim["id"],
                             "evidence_digest": digest(
                                 [a["artifact_id"] for a in active_evidence(snapshot, claim)])})
    return {"argument_digest": digest({"graph": snapshot.graph, "evidence": visible}),
            "goal_digest": digest(snapshot.state.goal),
            "frozen_scope_digest": (None if snapshot.state.frozen_claim_ids is None
                                    else digest(gating)),
            "deferred_scope_digest": digest(deferred),
            "reviewed_claims": reviewed,
            "scope_review": ("pass" if snapshot.state.frozen_claim_ids is not None else None)}


def audit_operation_current(snapshot: EvaluationSnapshot, child: Mapping[str, Any]) -> bool:
    """Compare the immutable launch dossier, including scope and freshness-derived coverage."""
    expected = audit_binding(snapshot)
    dossier = child["audit_argument"]
    reviewed = [{"claim_id": c["claim_id"], "evidence_digest": c["evidence_digest"]}
                for c in dossier["claims"] if c["gating"] and c["state"] == "approved"]
    operation = digest({"run_id": snapshot.run_id, "child_id": child["child_id"],
                        "argument_digest": dossier["argument_digest"],
                        "governance_digest": snapshot.state.governance["proposal_digest"],
                        "plan_revision": snapshot.state.governance["plan_revision"]})
    return (child["audit_operation_id"] == operation and bool(expected) and reviewed == expected["reviewed_claims"]
            and all(dossier[key] == expected[key] for key in (
                "argument_digest", "goal_digest", "frozen_scope_digest", "deferred_scope_digest")))


def audit_passes(snapshot: EvaluationSnapshot, verdict: Mapping[str, Any]) -> bool:
    expected = audit_binding(snapshot)
    child = next((c for c in snapshot.state.children if c["child_id"] == verdict.get("child_id")), None)
    return (child is not None and audit_operation_current(snapshot, child) and bool(expected) and verdict.get("verdict") == "pass" and
            all(_plain(verdict.get(key)) == value for key, value in expected.items()))


def evaluate_snapshot(snapshot: EvaluationSnapshot, command: dict[str, Any]) -> Decision:
    """Apply one validated command without performing I/O."""
    state, kind = snapshot.state, command["type"]
    if frozen_scope_invalid(state, snapshot.graph):
        return _decision(snapshot, state, "Block", reason="graph.invalid")
    if state.status != "active":
        if kind in {"GetRun", "GetArgument", "RestoreRun"} or (
                kind == "EvaluateRun" and command["intent"] in {"stop", "report_convergence"}):
            return _decision(snapshot, state)
        return _decision(snapshot, state, "Inert")

    if kind in {"GetRun", "RestoreRun"}:
        return _decision(snapshot, state)
    if kind == "GetArgument":
        return (_decision(snapshot, state) if snapshot.graph is not None
                else _decision(snapshot, state, "Block", reason="graph.invalid"))

    if kind == "EvaluateRun" and command["intent"] == "stop" and snapshot.graph is None:
        return _decision(snapshot, replace(state, status="stopped_residual"))
    operation = (command["action"]["kind"] if kind == "ObserveAction"
                 and command["action"]["kind"] in {"route", "graph", "configure_run", "investigate"}
                 else "report_convergence" if kind == "EvaluateRun"
                 and command["intent"] == "report_convergence" else None)
    if operation and (missing := bootstrap_precondition(snapshot, operation)):
        predicate, reason = missing
        affected = {"route.recorded": "obligation.route",
                    "investigation.recorded": "obligation.investigation"}.get(predicate)
        return _decision(snapshot, state, "Block", reason=reason, affected=affected)
    preparation = (kind == "ObserveAction" and command["action"]["kind"] in {"route", "graph", "configure_run"})
    honest_stop = kind == "EvaluateRun" and command["intent"] == "stop"
    if not preparation and not honest_stop and (reason := governance.admission(state.governance)):
        return _decision(snapshot, state, "Block", reason=reason)

    if kind == "ObserveAction":
        action = command["action"]
        akind = action["kind"]
        if akind == "route":
            if state.investigation_stamp is not None:
                return _decision(snapshot, state, "Block", reason="route.late",
                                 affected="obligation.route.late")
            if state.route_stamp is not None:
                return _decision(snapshot, state)
            return _decision(snapshot, replace(state, stamp_seq=state.stamp_seq + 1,
                                                route_stamp=state.stamp_seq + 1))
        if akind == "investigate":
            if state.investigation_stamp is not None:
                return _decision(snapshot, state)
            return _decision(snapshot, replace(state, stamp_seq=state.stamp_seq + 1,
                                                investigation_stamp=state.stamp_seq + 1))
        if akind == "graph":
            graph = action.get("payload")
            if not valid_graph(graph) or frozen_scope_invalid(state, graph):
                return _decision(snapshot, state, "Block", reason="graph.invalid")
            graph = governance.canonical_graph(graph)
            try:
                governed = governance.revise(state.goal, graph, state.governance)
            except ValueError as exc:
                return _decision(snapshot, state, "Block", reason=str(exc))
            art = artifact("graph", {"graph": graph})
            return _decision(snapshot, replace(state, selected_graph_artifact_id=art["artifact_id"], governance=governed),
                             artifacts=(art,))
        if akind in {"research", "freeze"} and snapshot.graph is None:
            return _decision(snapshot, state, "Block", reason="graph.invalid")
        if akind == "research":
            blocked = _investigation_block(snapshot)
            if blocked is not None:
                return blocked
            claims = {c["id"]: c for c in snapshot.graph["claims"]}
            claim = claims.get(action["claim_id"])
            if claim is None:
                return _decision(snapshot, state, "Block", reason="graph.invalid")
            payload = action["payload"]
            art = artifact("research", {
                "claim_id": claim["id"], "claim_digest": claim_digest(claim),
                "statement_digest": digest(payload),
                "outcome": "supporting" if action["result"] == "supports" else "refuting",
                "source_kind": action["source_kind"], "source_ref": payload["source_ref"],
                "citation": payload["citation"],
                "route_stamp": state.route_stamp,
                "investigation_stamp": state.investigation_stamp,
                **({"observed_content_digest": payload["observed_content_digest"]} if
                   "observed_content_digest" in payload else {}),
            })
            return _decision(snapshot, state, artifacts=(art,))
        if akind == "freeze":
            if state.frozen_claim_ids is not None:
                return _decision(snapshot, state, "Inert")
            ids = tuple(c["id"] for c in snapshot.graph["claims"] if c["gating"])
            semantic_digest = frozen_semantic_digest(snapshot.graph, ids)
            return _decision(snapshot, replace(state, frozen_claim_ids=ids,
                                                frozen_semantic_digest=semantic_digest))
        if akind == "configure_run":
            proposed = governance.plain(state.governance["proposal"])
            proposed["modes"].update(action.get("modes", {}))
            proposed["budgets"].update(action.get("budgets", {}))
            for key in ("auditor", "allow_same_model"):
                if key in action:
                    proposed[key] = governance.plain(action[key])
            if reason := governance.configuration_error(state, proposed):
                if reason == "governance.budget_invalid":
                    ceiling = next(k for k, used in governance.CEILINGS.items() if proposed["budgets"][k] < state.budgets[used])
                    resource = {"max_passes": "pass", "max_spawns": "spawn", "max_audit_spawns": "audit_spawn"}[ceiling]
                    return _decision(snapshot, state, "Block", reason="budget.exhausted", parameters={"resource": resource})
                return _decision(snapshot, state, "Block", reason=reason)
            try:
                governed = governance.revise(state.goal, snapshot.graph, state.governance, proposal=proposed)
            except ValueError as exc:
                return _decision(snapshot, state, "Block", reason=str(exc))
            return _decision(snapshot, replace(state, governance=governed))
        if akind == "child_reserve":
            blocked = _investigation_block(snapshot)
            if blocked is not None:
                return blocked
            execution = action["execution"]
            resource_class = action["resource_class"]
            if (execution == "async" and snapshot.host_tier != "full_async"
                    and not (resource_class == "audit"
                             and snapshot.host_audit_execution == "async")):
                reason = ("host.audit_output_unobservable" if snapshot.host_tier == "observational"
                          else "host.async_unsupported")
                return _decision(snapshot, state, "Block", reason=reason)
            if resource_class == "audit":
                children = list(state.children)
                for index, child in enumerate(children):
                    if child["resource_class"] != "audit" or child["state"] not in {
                            "reserved", "launching", "pending"}:
                        continue
                    if child["state"] != "pending" or audit_operation_current(snapshot, child):
                        return _decision(snapshot, state, "Block", reason="audit.pending")
                    # Logical cancellation is not a native stop or a refundable launch rejection.
                    children[index] = {**child, "state": "cancelled",
                        "first_terminal_fingerprint": digest({"stale_audit": child["audit_operation_id"],
                                                              "binding": audit_binding(snapshot)})}
                state = replace(state, children=tuple(children))
            limit_key, used_key, resource = SPAWN_BUDGET[resource_class]
            if state.budgets[used_key] >= state.budgets[limit_key]:
                return _decision(snapshot, snapshot.state, "Block", reason="budget.exhausted",
                                 parameters={"resource": resource})
            ordinal = len(state.children) + 1
            seed = {"run_id": snapshot.run_id, "ordinal": ordinal,
                    "purpose": action["purpose"], "role_profile": action["role_profile"],
                    "resource_class": resource_class}
            child_id = "ch-" + digest(seed).removeprefix("sha256:")
            child = {"child_id": child_id, "purpose": action["purpose"],
                     "resource_class": resource_class, "state": "reserved",
                     "spent": False, "refunded": False, "deadline": None, "native_id": None,
                     "first_terminal_fingerprint": None,
                     "capability_ref": digest({"capability": seed}),
                     "audit_operation_id": None, "audit_argument": None,
                     "audit_role_profile": None, "audit_auditor": None, "audit_inventory_digest": None}
            budgets = dict(state.budgets)
            budgets[used_key] += 1
            return _decision(snapshot, replace(state, budgets=budgets,
                                                children=state.children + (child,)))
        return _decision(snapshot, state, "Fault", reason="unsupported")

    if kind == "EvaluateRun":
        intent = command["intent"]
        if intent == "continue":
            return _decision(snapshot, state)
        derivation = derive_claims(snapshot)
        states = [(c, derivation.states[c["id"]]) for c in snapshot.graph["claims"]]
        gating = set(derivation.scope)
        scoped = [(c, cs) for c, cs in states if c["id"] in gating]
        if intent == "stop":
            if state.budgets["passes_used"] >= state.budgets["max_passes"]:
                return _decision(snapshot, replace(state, status="stopped_budget"))
            deferred = [c for c, _ in states if c["id"] not in gating]
            status = "stopped_frozen" if deferred else "stopped_residual"
            return _decision(snapshot, replace(state, status=status))
        blocked = _investigation_block(snapshot)
        if blocked is not None:
            return blocked
        missing = next(((c, cs) for c, cs in scoped if cs != "approved"), None)
        if missing:
            c, _ = missing
            target_id = derivation.blockers.get(c["id"], c["id"])
            c = next(claim for claim in snapshot.graph["claims"] if claim["id"] == target_id)
            cs = derivation.states[target_id]
            evidence = active_evidence(snapshot, c)
            research = [a for a in evidence if a["kind"] == "research"]
            spikes = [a for a in evidence if a["kind"] == "spike"]
            if cs == "discarded":
                reason, parameters = "claim.refuted", {}
            elif cs == "blocked":
                reason, parameters = "claim.human_decision", {}
            elif claim_conflicted(snapshot, c):
                reason, parameters = "evidence.conflict", {}
            elif any(a["outcome"] == "supporting" for a in research) and c["kind"] == "needs-experiment":
                stale = stale_artifact_ids(snapshot)
                if spikes and spikes[-1]["artifact_id"] in stale:
                    stale_head = next(h for h in evaluate_freshness(
                        active_spike_heads(snapshot), snapshot.observations).stale_heads
                                      if h.artifact_id == spikes[-1]["artifact_id"])
                    parameters = {"changes": [{"path": change.path, "state": change.state.value}
                                               for change in stale_head.changes]}
                    reason = "claim.spike_stale"
                else:
                    reason, parameters = "claim.spike_missing", {}
            else:
                historical_research = any(a.get("kind") == "research" and a.get("claim_id") == c["id"]
                                          for a in snapshot.history)
                reason = "claim.research_unbound" if historical_research else "claim.research_missing"
                parameters = {}
            return _decision(snapshot, state, "Block", reason=reason, parameters=parameters,
                             affected="claim:" + c["id"])
        derivation_digest = digest({"graph": snapshot.graph,
                                    "claims": [(c["id"], derivation.states[c["id"]],
                                                [a["artifact_id"] for a in active_evidence(snapshot, c)])
                                               for c in snapshot.graph["claims"]],
                                    "frozen": state.frozen_claim_ids})
        if derivation_digest != state.last_derivation_digest:
            if state.budgets["passes_used"] >= state.budgets["max_passes"]:
                return _decision(snapshot, state, "Block", reason="budget.exhausted",
                                 parameters={"resource": "pass"})
            budgets = dict(state.budgets)
            budgets["passes_used"] += 1
            state = replace(state, budgets=budgets, last_derivation_digest=derivation_digest)
        if any(child["resource_class"] == "audit" and child["state"] in {"reserved", "launching", "pending"}
               and audit_operation_current(snapshot, child) for child in state.children):
            return _decision(snapshot, state, "Block", reason="audit.pending")
        audits = [a for a in snapshot.history if a.get("kind") == "audit_verdict"]
        if not audits:
            return _decision(snapshot, state, "Block", reason="audit.required")
        audit = audits[-1]
        if not audit_passes(snapshot, audit):
            return _decision(snapshot, state, "Block", reason="audit.failed")
        deferred = [c for c, _ in states if c["id"] not in gating]
        if deferred:
            return _decision(snapshot, replace(state, status="stopped_frozen"))
        auditor, covered = audit_attributions(snapshot, audit)
        auditor_pair, covered_pair = identity_pair(auditor), identity_pair(covered)
        if auditor_pair is None or covered_pair is None:
            return _decision(snapshot, state, "Block", reason="audit.independence_unverified")
        if governance.model_key(auditor) != governance.model_key(state.governance["proposal"]["auditor"]) or governance.model_key(covered) != governance.model_key(state.governance["context"]["author"]):
            return _decision(snapshot, state, "Block", reason="governance.identity_mismatch")
        if auditor_pair == covered_pair and (governance.selection_error(state.governance) or not state.governance["proposal"]["allow_same_model"]):
            return _decision(snapshot, state, "Block", reason="audit.same_model")
        return _decision(snapshot, replace(state, status="converged"))

    return _decision(snapshot, state, "Fault", reason="unsupported")
