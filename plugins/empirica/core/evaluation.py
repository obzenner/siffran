"""Pure command evaluation over immutable Empirica snapshots."""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import partial
from types import MappingProxyType
from typing import Any

from .canonical import canonical_digest
from .freshness import ActiveSpikeHead, FileBinding, evaluate_freshness, valid_claim_id
from .run import OperationalState
from . import governance

SPAWN_BUDGET = {"investigation": ("max_spawns", *governance.BUDGETS["max_spawns"]),
                "audit": ("max_audit_spawns", *governance.BUDGETS["max_audit_spawns"])}
HOST_TIER_UNSUPPORTED = {"foreground_only": "host.async_unsupported",
                         "observational": "host.audit_output_unobservable"}
READ_COMMANDS = frozenset({"GetRun", "GetArgument", "RestoreRun"})


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
    return canonical_digest(_plain(value))


def artifact(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = {"kind": kind, **payload}
    return {"artifact_id": digest(body), "body": body}


def _producer(state: OperationalState) -> dict[str, Any] | None:
    """Copy the trusted current author observation without interpreting identity."""
    author = state.governance.get("context", {}).get("author")
    return _plain(author) if isinstance(author, Mapping) else None


@dataclass(frozen=True)
class ContractView:
    reason_metadata: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]
    bootstrap_requirements: tuple[tuple[str, str, str], ...]
    audit_obligation: tuple[str, str]
    bootstrap_operations: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    governance_controls: Mapping[str, Any]
    recovery_exclusions: Mapping[str, tuple[str, ...]]
    late_route_must: str
    untrusted_delimiters: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "governance_controls", _freeze(self.governance_controls))
        object.__setattr__(self, "recovery_exclusions", _freeze(self.recovery_exclusions))
        object.__setattr__(self, "untrusted_delimiters", _freeze(self.untrusted_delimiters))


@dataclass(frozen=True)
class EvaluationSnapshot:
    state: OperationalState
    history: tuple[dict[str, Any], ...]
    graph: dict[str, Any] | None
    contract: ContractView
    observations: tuple[Any, ...] = ()
    observation_basis_id: str = ""
    observation_digest: str = ""
    run_id: str = ""
    contract_id: str = "empirica-public-contract"
    contract_version: str = "2.0.0"
    contract_digest: str = ""
    profile_id: str = ""
    host_tier: str = "observational"
    host_audit_execution: str = "unavailable"
    command: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "history", _freeze(self.history))
        object.__setattr__(self, "graph", _freeze(self.graph))
        object.__setattr__(self, "command", _freeze(self.command))

    @property
    def bootstrap_requirements(self):
        return self.contract.bootstrap_requirements

    @property
    def bootstrap_operations(self):
        return self.contract.bootstrap_operations

    @property
    def audit_obligation(self):
        return self.contract.audit_obligation

    @property
    def reason_metadata(self):
        return self.contract.reason_metadata

    @property
    def governance_controls(self):
        return self.contract.governance_controls

    @property
    def late_route_must(self):
        return self.contract.late_route_must

    @property
    def untrusted_delimiters(self):
        return self.contract.untrusted_delimiters


@dataclass(frozen=True)
class StateIntent:
    state: OperationalState
    artifacts: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ClaimDerivation:
    states: Mapping[str, str]
    blockers: Mapping[str, str]
    scope: tuple[str, ...]
    stale_heads: Mapping[str, ActiveSpikeHead]
    freshness_heads: Mapping[str, ActiveSpikeHead]


@dataclass(frozen=True)
class Decision:
    result_type: str
    intent: StateIntent
    reason_code: str | None = None
    parameters: tuple[tuple[str, Any], ...] = ()
    affected_obligation_id: str | None = None
    observations_consulted: bool = False


def valid_graph(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"root", "claims", "edges"}:
        return False
    claims = value.get("claims")
    if not isinstance(value.get("root"), str) or not isinstance(claims, (list, tuple)) or not 1 <= len(claims) <= 32:
        return False
    ids: list[str] = []
    for claim in claims:
        if (not isinstance(claim, Mapping) or set(claim) != {"id", "text", "gating", "kind"}
                or not valid_claim_id(claim["id"]) or not isinstance(claim["text"], str)
                or not 1 <= len(claim["text"]) <= 2048
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


def active_spike_heads(
    history: tuple[dict[str, Any], ...], graph: dict[str, Any] | None,
) -> tuple[ActiveSpikeHead, ...]:
    """Return each claim's latest current spike in observable graph order."""
    if graph is None:
        return ()
    return tuple(
        ActiveSpikeHead(item["artifact_id"], claim["id"], item["harness_request_id"],
                        tuple(FileBinding(row["path"], row["sha256"])
                              for row in item["file_bindings"]))
        for claim in graph["claims"]
        if (spikes := [row for row in history
                       if row.get("kind") == "spike"
                       and row.get("claim_id") == claim["id"]
                       and row.get("claim_digest") == claim_digest(claim)])
        for item in spikes[-1:]
    )


def _freshness_sensitive(snapshot: EvaluationSnapshot, claim: Mapping[str, Any]) -> bool:
    """Whether this claim's current state depends on workspace freshness."""
    if claim["kind"] != "needs-experiment":
        return False
    evidence = active_evidence(snapshot, claim)
    outcomes = {item["outcome"] for item in evidence if item["kind"] == "research"}
    spikes = [item for item in evidence if item["kind"] == "spike"]
    return outcomes == {"supporting"} and bool(spikes and spikes[-1]["outcome"] == "pass")


def claim_freshness_relevant(snapshot: EvaluationSnapshot) -> bool:
    """Whether deriving current claim state must consult workspace observations."""
    return snapshot.graph is not None and any(
        _freshness_sensitive(snapshot, claim) for claim in snapshot.graph["claims"])


def evaluate_stale_heads(snapshot: EvaluationSnapshot) -> Mapping[str, ActiveSpikeHead]:
    """Evaluate bound-file freshness independently of claim gate relevance."""
    if not snapshot.observations:
        return MappingProxyType({})
    freshness = evaluate_freshness(
        active_spike_heads(snapshot.history, snapshot.graph), snapshot.observations
    )
    return MappingProxyType({head.artifact_id: head for head in freshness.stale_heads})


def claim_conflicted(snapshot: EvaluationSnapshot, claim: dict[str, Any]) -> bool:
    return {a["outcome"] for a in active_evidence(snapshot, claim) if a["kind"] == "research"} >= {"supporting", "refuting"}


def effective_scope_ids(snapshot: EvaluationSnapshot) -> tuple[str, ...]:
    if snapshot.graph is None:
        return ()
    return (tuple(snapshot.state.frozen_claim_ids)
            if snapshot.state.frozen_claim_ids is not None else
            tuple(c["id"] for c in snapshot.graph["claims"] if c["gating"]))


def local_claim_state(snapshot: EvaluationSnapshot, claim: dict[str, Any],
                      stale: set[str]) -> str:
    evidence = active_evidence(snapshot, claim)
    research = [a for a in evidence if a["kind"] == "research"]
    supporting = any(a["outcome"] == "supporting" for a in research)
    refuting = any(a["outcome"] == "refuting" for a in research)
    spikes = [a for a in evidence if a["kind"] == "spike"]
    spike_failed = bool(spikes and spikes[-1]["outcome"] == "fail")
    sensitive = _freshness_sensitive(snapshot, claim)
    spike_ok = bool(sensitive and spikes[-1]["artifact_id"] not in stale)
    if supporting and refuting and not spike_failed:
        return "open"
    if spike_failed or refuting:
        return "discarded"
    if claim["kind"] == "needs-decision":
        return "blocked"
    approved = supporting and (claim["kind"] == "ordinary" or
                               (claim["kind"] == "needs-experiment" and spike_ok))
    return "approved" if approved else "open"


def claim_blockers(snapshot: EvaluationSnapshot,
                   derivation: ClaimDerivation | None = None) -> tuple[dict[str, Any], ...]:
    """Derive every scoped claim blocker in stable graph order.

    ``claim_id`` is the scoped claim whose approval is blocked; ``target_claim_id``
    follows dependency redirection to the claim that must actually be discharged.
    This is the sole claim-level blocker classification used by both the gate and
    public projections.
    """
    if snapshot.graph is None:
        return ()
    derivation = derive_claims(snapshot) if derivation is None else derivation
    unresolved = [claim_id for claim_id in derivation.scope
                  if derivation.states[claim_id] != "approved"]
    if not unresolved:
        return ()
    claims = {claim["id"]: claim for claim in snapshot.graph["claims"]}
    blockers: list[dict[str, Any]] = []
    for claim_id in unresolved:
        target_id = derivation.blockers.get(claim_id, claim_id)
        claim = claims[target_id]
        state = derivation.states[target_id]
        evidence = active_evidence(snapshot, claim)
        research = [item for item in evidence if item["kind"] == "research"]
        spikes = [item for item in evidence if item["kind"] == "spike"]
        parameters: dict[str, Any] = {}
        if state == "discarded":
            reason = "claim.refuted"
        elif state == "blocked":
            reason = "claim.human_decision"
        elif claim_conflicted(snapshot, claim):
            reason = "evidence.conflict"
        elif (any(item["outcome"] == "supporting" for item in research)
              and claim["kind"] == "needs-experiment"):
            sensitive = _freshness_sensitive(snapshot, claim)
            stale_head = (derivation.stale_heads.get(spikes[-1]["artifact_id"])
                          if sensitive else None)
            if stale_head is not None:
                parameters = {"changes": [{"path": change.path, "state": change.state.value}
                                           for change in stale_head.changes]}
            reason = "claim.spike_stale" if stale_head is not None else "claim.spike_missing"
        else:
            historical_research = any(
                item.get("kind") == "research" and item.get("claim_id") == target_id
                for item in snapshot.history)
            reason = ("claim.research_unbound" if historical_research
                      else "claim.research_missing")
        blockers.append({"claim_id": claim_id, "target_claim_id": target_id,
                         "reason": reason, "parameters": parameters})
    return tuple(blockers)


def _derive_claims(snapshot: EvaluationSnapshot,
                   freshness_heads: Mapping[str, ActiveSpikeHead]) -> ClaimDerivation:
    if snapshot.graph is None:
        empty = MappingProxyType({})
        return ClaimDerivation(empty, empty, (), empty, empty)
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
    # Freshness changes are always carried when requested for projection; only relevant claims
    # consume them when deriving gate state.
    stale_heads = freshness_heads if claim_freshness_relevant(snapshot) else MappingProxyType({})
    stale = set(stale_heads)
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
    return ClaimDerivation(MappingProxyType(states), MappingProxyType(blockers), scoped,
                           stale_heads, freshness_heads)


def derive_claims(snapshot: EvaluationSnapshot) -> ClaimDerivation:
    """Derive gate state, consulting freshness only when claim state depends on it."""
    heads = (evaluate_stale_heads(snapshot) if claim_freshness_relevant(snapshot)
             else MappingProxyType({}))
    return _derive_claims(snapshot, heads)


def derive_claims_for_projection(snapshot: EvaluationSnapshot) -> ClaimDerivation:
    """Derive claims while always carrying observable freshness for an honest RunView."""
    return _derive_claims(snapshot, evaluate_stale_heads(snapshot))


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
            if governance.interaction_error(governed):
                actions.append("residual.accept")
            else:
                actions.append("governance.propose")
        elif facts["route.recorded"] and not facts["investigation.recorded"]:
            actions.append("investigation.record")
    request_ready = active_run and bootstrap_precondition(snapshot, "configure_run") is None
    display_ready = (active_run and bootstrap_precondition(snapshot, "governance.present") is None
                     and governance.interaction_error(governed) is None
                     and governance.sized(governed))
    return {"active": active, "next_actions": actions,
            "request_ready": request_ready, "display_ready": display_ready}


def _decision(snapshot: EvaluationSnapshot, state: OperationalState, result: str = "Allow", artifacts: tuple[dict[str, Any], ...] = (), reason: str | None = None, parameters: dict[str, Any] | None = None, affected: str | None = None, *, observations_consulted: bool = False) -> Decision:
    return Decision(result, StateIntent(state, artifacts), reason, tuple((parameters or {}).items()),
                    affected, observations_consulted)


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
        "producer": _producer(state),
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
        "producer": request_body["producer"],
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
    """Validate the trusted reviewer identity against the exact audit operation."""
    if payload.get("subject_kind") != "auditor":
        return False
    child_id = payload.get("child_id")
    return (payload.get("covered_artifact_ids") == []
            and isinstance(child_id, str)
            and any(child["child_id"] == child_id and child["resource_class"] == "audit"
                    and child["state"] == "pending" for child in snapshot.state.children))


def identity_pair(value: Mapping[str, Any] | None) -> str | None:
    """Return an opaque host-observed identity class without interpreting it."""
    identity = value.get("identity") if value and value.get("observed_by") == "host" else None
    return identity if isinstance(identity, str) and identity else None


def audit_attributions(
    snapshot: EvaluationSnapshot, verdict: Mapping[str, Any],
) -> tuple[Mapping[str, Any] | None, list[Mapping[str, Any] | None]]:
    """Select the reviewer and per-artifact producers covered by this verdict."""
    attrs = [item for item in snapshot.history if item.get("kind") == "attribution"]
    auditor = next((item for item in reversed(attrs)
                    if item.get("subject_kind") == "auditor"
                    and item.get("child_id") == verdict.get("child_id")), None)
    by_id = {item.get("artifact_id"): item for item in snapshot.history}
    producers = [by_id.get(artifact_id, {}).get("producer")
                 for artifact_id in covered_artifact_ids(snapshot)]
    return auditor, producers


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


INDEPENDENCE_REASONS = {
    "unverified": "audit.independence_unverified",
    "mixed": "audit.producers_mixed",
    "same_model": "audit.same_model",
}


def independence(snapshot: EvaluationSnapshot, verdict: Mapping[str, Any]) -> str:
    """Classify reviewer/producer independence from opaque host identities."""
    auditor, producers = audit_attributions(snapshot, verdict)
    auditor_class = identity_pair(auditor)
    producer_classes = [identity_pair(producer) for producer in producers]
    if auditor_class is None or not producer_classes or any(value is None for value in producer_classes):
        return "unverified"
    if len(set(producer_classes)) != 1:
        return "mixed"
    return "same_model" if auditor_class == producer_classes[0] else "distinct"


def derivation_digest(snapshot: EvaluationSnapshot, derivation: ClaimDerivation) -> str:
    """Identify the current claim derivation for pass charging and residuals."""
    return digest({"graph": snapshot.graph,
                   "claims": [(claim["id"], derivation.states[claim["id"]],
                               [item["artifact_id"] for item in active_evidence(snapshot, claim)])
                              for claim in snapshot.graph["claims"]],
                   "frozen": snapshot.state.frozen_claim_ids})


def pass_budget_blocker(snapshot: EvaluationSnapshot, digest_value: str) -> dict[str, Any] | None:
    state = snapshot.state
    if (digest_value != state.last_derivation_digest
            and state.budgets["passes_used"] >= state.budgets["max_passes"]):
        return {"reason": "budget.exhausted", "parameters": {"resource": "pass"}}
    return None


def audit_blocker(snapshot: EvaluationSnapshot, derivation: ClaimDerivation) -> dict[str, Any] | None:
    """Derive the first audit blocker after every scoped claim is approved."""
    state = snapshot.state
    if any(child["resource_class"] == "audit"
           and child["state"] in {"reserved", "launching", "pending"}
           and audit_operation_current(snapshot, child) for child in state.children):
        return {"reason": "audit.pending", "parameters": {}}
    audits = [item for item in snapshot.history if item.get("kind") == "audit_verdict"]
    if not audits:
        return {"reason": "audit.required", "parameters": {}}
    audit = audits[-1]
    if not audit_passes(snapshot, audit):
        return {"reason": "audit.failed", "parameters": {}}
    if any(claim_id not in set(derivation.scope)
           for claim_id in (claim["id"] for claim in snapshot.graph["claims"])):
        return None
    classification = independence(snapshot, audit)
    reason = INDEPENDENCE_REASONS.get(classification)
    return None if reason is None else {"reason": reason, "parameters": {}}


def evaluate_snapshot(snapshot: EvaluationSnapshot, command: dict[str, Any]) -> Decision:
    """Apply one validated command without performing I/O."""
    state, kind = snapshot.state, command["type"]
    if frozen_scope_invalid(state, snapshot.graph):
        return _decision(snapshot, state, "Block", reason="graph.invalid")
    if state.status != "active":
        if kind in READ_COMMANDS or (
                kind == "EvaluateRun" and command["intent"] in {"stop", "report_convergence"}):
            return _decision(snapshot, state)
        return _decision(snapshot, state, "Inert")

    if kind == "GetArgument":
        return (_decision(snapshot, state) if snapshot.graph is not None
                else _decision(snapshot, state, "Block", reason="graph.invalid"))
    if kind in READ_COMMANDS:
        return _decision(snapshot, state)

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
            art = artifact("graph", {"graph": graph})
            return _decision(snapshot, replace(state, selected_graph_artifact_id=art["artifact_id"]),
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
                "producer": _producer(state),
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
            proposed["budgets"] = {**proposed["budgets"], **action["budgets"]}
            proposed["rationale"] = action["rationale"]
            if reason := governance.configuration_error(state, proposed):
                if reason == "governance.budget_invalid":
                    ceiling = next(k for k, used in governance.CEILINGS.items() if proposed["budgets"][k] < state.budgets[used])
                    resource = governance.BUDGETS[ceiling].resource
                    return _decision(snapshot, state, "Block", reason="budget.exhausted", parameters={"resource": resource})
                return _decision(snapshot, state, "Block", reason=reason)
            if reason := governance.revision_error(state.governance, state.goal, proposed):
                return _decision(snapshot, state, "Block", reason=reason)
            try:
                governed = governance.revise(state.goal, state.governance, proposal=proposed)
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
                reason = HOST_TIER_UNSUPPORTED.get(snapshot.host_tier, "host.async_unsupported")
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
                     "audit_role_profile": None}
            budgets = dict(state.budgets)
            budgets[used_key] += 1
            return _decision(snapshot, replace(state, budgets=budgets,
                                                children=state.children + (child,)))
        return _decision(snapshot, state, "Fault", reason="unsupported")

    if kind == "EvaluateRun":
        intent = command["intent"]
        if intent == "continue":
            return _decision(snapshot, state)
        if intent == "report_convergence":
            blocked = _investigation_block(snapshot)
            if blocked is not None:
                return blocked
        observations_consulted = claim_freshness_relevant(snapshot)
        decide = partial(_decision, snapshot, observations_consulted=observations_consulted)
        derivation = derive_claims(snapshot)
        states = [(c, derivation.states[c["id"]]) for c in snapshot.graph["claims"]]
        gating = set(derivation.scope)
        if intent == "stop":
            if state.budgets["passes_used"] >= state.budgets["max_passes"]:
                return decide(replace(state, status="stopped_budget"))
            deferred = [c for c, _ in states if c["id"] not in gating]
            status = "stopped_frozen" if deferred else "stopped_residual"
            return decide(replace(state, status=status))
        blockers = claim_blockers(snapshot, derivation)
        if blockers:
            blocker = blockers[0]
            return decide(state, "Block", reason=blocker["reason"],
                          parameters=blocker["parameters"],
                          affected="claim:" + blocker["target_claim_id"])
        current_digest = derivation_digest(snapshot, derivation)
        blocker = pass_budget_blocker(snapshot, current_digest)
        if blocker:
            return decide(state, "Block", reason=blocker["reason"],
                          parameters=blocker["parameters"])
        if current_digest != state.last_derivation_digest:
            budgets = dict(state.budgets)
            budgets["passes_used"] += 1
            state = replace(state, budgets=budgets, last_derivation_digest=current_digest)
        blocker = audit_blocker(snapshot, derivation)
        if blocker:
            return decide(state, "Block", reason=blocker["reason"],
                          parameters=blocker["parameters"], affected="obligation.audit")
        deferred = [c for c, _ in states if c["id"] not in gating]
        if deferred:
            return decide(replace(state, status="stopped_frozen"))
        return decide(replace(state, status="converged"))

    return _decision(snapshot, state, "Fault", reason="unsupported")
