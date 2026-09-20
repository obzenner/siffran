"""Pure command evaluation over immutable Empirica snapshots."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from . import claims as claim_rules
from .freshness import ActiveSpikeHead, FileBinding, evaluate_freshness
from .run import OperationalState


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
    profile_id: str = ""
    host_tier: str = "observational"
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
    if not isinstance(value.get("root"), str) or not isinstance(claims, (list, tuple)) or not claims:
        return False
    ids: list[str] = []
    for claim in claims:
        if (not isinstance(claim, Mapping) or set(claim) != {"id", "text", "gating", "kind"}
                or not isinstance(claim["id"], str) or not isinstance(claim["text"], str)
                or type(claim["gating"]) is not bool
                or claim["kind"] not in {"ordinary", "needs-experiment", "needs-decision"}):
            return False
        ids.append(claim["id"])
    if len(ids) != len(set(ids)) or value["root"] not in ids or not isinstance(value["edges"], (list, tuple)):
        return False
    for edge in value["edges"]:
        if (not isinstance(edge, Mapping) or set(edge) != {"from", "to", "type"}
                or edge["from"] not in ids or edge["to"] not in ids
                or edge["type"] not in {"SupportedBy", "InContextOf"}):
            return False
    return True


def claim_digest(claim: dict[str, Any]) -> str:
    return digest({"claim_id": claim["id"], "text": claim["text"]})


def active_evidence(snapshot: EvaluationSnapshot, claim: dict[str, Any]) -> list[dict[str, Any]]:
    cd = claim_digest(claim)
    evidence = [a for a in snapshot.history if a.get("kind") in {"research", "spike"}
                and a.get("claim_id") == claim["id"] and a.get("claim_digest") == cd]
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
            heads.append(ActiveSpikeHead(item["artifact_id"], claim["id"],
                                         item["harness_request_id"], bindings))
    return tuple(heads)


def stale_artifact_ids(snapshot: EvaluationSnapshot) -> set[str]:
    if not snapshot.observations:
        return set()
    return {head.artifact_id for head in evaluate_freshness(
        active_spike_heads(snapshot), snapshot.observations).stale_heads}


def claim_state(snapshot: EvaluationSnapshot, claim: dict[str, Any]) -> str:
    evidence = active_evidence(snapshot, claim)
    research = [a for a in evidence if a["kind"] == "research"]
    supporting = any(a["outcome"] == "supporting" for a in research)
    refuting = any(a["outcome"] == "refuting" for a in research)
    spikes = [a for a in evidence if a["kind"] == "spike"]
    spike_ok = bool(spikes and spikes[-1]["outcome"] == "pass"
                    and spikes[-1]["artifact_id"] not in stale_artifact_ids(snapshot))
    approved = supporting and (claim["kind"] == "ordinary" or
                               (claim["kind"] == "needs-experiment" and spike_ok))
    node = {"type": "claim", "text": claim["text"], "kind": claim["kind"],
            "confidence": 1.0 if approved else 0.0,
            "blocked": "needs-decision" if claim["kind"] == "needs-decision" else None,
            "evidence": [a["artifact_id"] for a in evidence],
            "refuted_by": "research" if refuting else None}
    graph = {"root": claim["id"], "nodes": {claim["id"]: node}, "edges": []}
    return claim_rules.state_of(
        graph, claim["id"], 1.0,
        lambda _claim_id, purpose: refuting if purpose == "refute" else approved,
    )


def _decision(snapshot: EvaluationSnapshot, state: OperationalState, result: str = "Allow",
              artifacts: tuple[dict[str, Any], ...] = (), reason: str | None = None,
              parameters: dict[str, Any] | None = None, affected: str | None = None) -> Decision:
    return Decision(result, StateIntent(state, artifacts), reason,
                    tuple((parameters or {}).items()), affected)


def plan_spike_request(snapshot: EvaluationSnapshot, action: Mapping[str, Any]) -> Decision:
    """Purely admit and seal a spike request against current graph/research."""
    state = snapshot.state
    if snapshot.graph is None:
        return _decision(snapshot, state, "Block", reason="graph.invalid")
    claim = next((c for c in snapshot.graph["claims"] if c["id"] == action["claim_id"]), None)
    if claim is None:
        return _decision(snapshot, state, "Block", reason="graph.invalid")
    research = [a for a in active_evidence(snapshot, claim)
                if a["kind"] == "research" and a["outcome"] == "supporting"]
    if not research:
        return _decision(snapshot, state, "Block", reason="claim.spike_prerequisite_missing",
                         affected="claim:" + claim["id"])
    sealed = artifact("spike_request", {
        "claim_id": claim["id"], "claim_digest": claim_digest(claim),
        "statement_digest": digest(action), "harness_request_id": digest(action),
        "command": action["command"], "command_digest": digest(action["command"]),
        "dependent_files": sorted(action["dependent_files"]),
        "prerequisite_research_ids": [a["artifact_id"] for a in research],
    })
    return _decision(snapshot, state, artifacts=(sealed,))


def plan_spike_result(snapshot: EvaluationSnapshot, request_body: Mapping[str, Any],
                      facts: Any) -> Decision:
    """Purely admit immutable harness facts and plan one spike result artifact."""
    state = snapshot.state
    if snapshot.graph is None:
        return _decision(snapshot, state, "Block", reason="graph.invalid")
    claim = next((c for c in snapshot.graph["claims"]
                  if c["id"] == request_body["claim_id"]), None)
    if claim is None or claim_digest(claim) != request_body["claim_digest"]:
        return _decision(snapshot, state, "Block", reason="graph.invalid")
    prior = [a for a in snapshot.history if a.get("kind") == "spike"
             and a.get("claim_id") == claim["id"]
             and a.get("claim_digest") == request_body["claim_digest"]]
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
    })
    return _decision(snapshot, state, artifacts=(result,))


def covered_artifact_ids(snapshot: EvaluationSnapshot) -> list[str]:
    """Return the exact active evidence set whose producer identity an audit covers."""
    if snapshot.graph is None:
        return []
    gating = (set(snapshot.state.frozen_claim_ids)
              if snapshot.state.frozen_claim_ids is not None else
              {claim["id"] for claim in snapshot.graph["claims"] if claim["gating"]})
    return [artifact["artifact_id"]
            for claim in snapshot.graph["claims"]
            if claim["id"] in gating and claim_state(snapshot, claim) == "approved"
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
                and any(child["child_id"] == child_id and child["purpose"] == "audit"
                        and child["state"] == "pending" for child in snapshot.state.children))
    return False


_MODEL_ALIASES = {"default", "latest", "opus", "sonnet", "haiku", "fable", "mini"}


def identity_pair(value: Mapping[str, Any] | None) -> tuple[str, str] | None:
    """Return a concrete dispatcher identity; tier/latest aliases are never identities."""
    if not value or value.get("observed_by") != "host":
        return None
    provider, model = value.get("provider_id"), value.get("model_id")
    if (not isinstance(provider, str) or not provider or not isinstance(model, str)
            or not model or model.lower() in _MODEL_ALIASES):
        return None
    return provider, model


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
    if snapshot.graph is None:
        return {}
    all_ids = [c["id"] for c in snapshot.graph["claims"]]
    gating = (list(snapshot.state.frozen_claim_ids)
              if snapshot.state.frozen_claim_ids is not None
              else [c["id"] for c in snapshot.graph["claims"] if c["gating"]])
    deferred = [cid for cid in all_ids if cid not in set(gating)]
    visible = [a["artifact_id"] for a in snapshot.history
               if a.get("kind") in {"research", "spike_request", "spike"}]
    reviewed = []
    for claim in snapshot.graph["claims"]:
        if claim["id"] in gating and claim_state(snapshot, claim) == "approved":
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


def audit_passes(snapshot: EvaluationSnapshot, verdict: Mapping[str, Any]) -> bool:
    expected = audit_binding(snapshot)
    return (verdict.get("verdict") == "pass" and
            all(_plain(verdict.get(key)) == value for key, value in expected.items()))


def evaluate_snapshot(snapshot: EvaluationSnapshot, command: dict[str, Any]) -> Decision:
    """Apply one validated command without performing I/O."""
    state, kind = snapshot.state, command["type"]
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
            if state.route_stamp is None:
                return _decision(snapshot, state, "Block", reason="route.required",
                                 affected="obligation.route")
            if state.investigation_stamp is not None:
                return _decision(snapshot, state)
            return _decision(snapshot, replace(state, stamp_seq=state.stamp_seq + 1,
                                                investigation_stamp=state.stamp_seq + 1))
        if akind == "graph":
            graph = action.get("payload")
            if not valid_graph(graph):
                return _decision(snapshot, state, "Block", reason="graph.invalid")
            art = artifact("graph", {"graph": graph})
            return _decision(snapshot, replace(state, selected_graph_artifact_id=art["artifact_id"]),
                             artifacts=(art,))
        if akind in {"research", "freeze"} and snapshot.graph is None:
            return _decision(snapshot, state, "Block", reason="graph.invalid")
        if akind == "research":
            claims = {c["id"]: c for c in snapshot.graph["claims"]}
            claim = claims.get(action["claim_id"])
            if claim is None:
                return _decision(snapshot, state, "Block", reason="graph.invalid")
            art = artifact("research", {
                "claim_id": claim["id"], "claim_digest": claim_digest(claim),
                "statement_digest": digest(action.get("payload", {})),
                "outcome": "supporting" if action["result"] == "supports" else "refuting",
                "source_kind": action["source_kind"],
                "source_ref": str(action.get("payload", {}).get("source_ref", action["source_kind"])),
            })
            return _decision(snapshot, state, artifacts=(art,))
        if akind == "freeze":
            if state.frozen_claim_ids is not None:
                return _decision(snapshot, state, "Inert")
            ids = tuple(c["id"] for c in snapshot.graph["claims"] if c["gating"])
            return _decision(snapshot, replace(state, frozen_claim_ids=ids))
        if akind == "configure_run":
            modes, budgets = dict(state.modes), dict(state.budgets)
            modes.update(action.get("modes", {}))
            for key, value in action.get("budgets", {}).items():
                used_key = "passes_used" if key == "max_passes" else "spawns_used"
                if value < budgets[used_key]:
                    return _decision(snapshot, state, "Block", reason="budget.exhausted",
                                     parameters={"resource": "pass" if key == "max_passes" else "spawn"})
                budgets[key] = value
            return _decision(snapshot, replace(state, modes=modes, budgets=budgets))
        if akind == "child_reserve":
            execution = action["execution"]
            if action["purpose"] == "audit" and any(
                child["purpose"] == "audit"
                and child["state"] in {"reserved", "launching", "pending"}
                for child in state.children
            ):
                return _decision(snapshot, state, "Block", reason="audit.pending")
            if execution == "async" and snapshot.host_tier != "full_async":
                reason = ("host.audit_output_unobservable" if snapshot.host_tier == "observational"
                          else "host.async_unsupported")
                return _decision(snapshot, state, "Block", reason=reason)
            if state.budgets["spawns_used"] >= state.budgets["max_spawns"]:
                return _decision(snapshot, state, "Block", reason="budget.exhausted",
                                 parameters={"resource": "spawn"})
            ordinal = len(state.children) + 1
            seed = {"run_id": snapshot.run_id, "ordinal": ordinal,
                    "purpose": action["purpose"], "role_profile": action["role_profile"]}
            child_id = "ch-" + digest(seed).removeprefix("sha256:")
            child = {"child_id": child_id, "purpose": action["purpose"], "state": "reserved",
                     "spent": False, "refunded": False, "deadline": None, "native_id": None,
                     "first_terminal_fingerprint": None,
                     "capability_ref": digest({"capability": seed}),
                     "audit_operation_id": None, "audit_argument": None,
                     "audit_role_profile": None}
            budgets = dict(state.budgets)
            budgets["spawns_used"] += 1
            return _decision(snapshot, replace(state, budgets=budgets,
                                                children=state.children + (child,)))
        return _decision(snapshot, state, "Fault", reason="unsupported")

    if kind == "EvaluateRun":
        if snapshot.graph is None:
            return _decision(snapshot, state, "Block", reason="graph.missing")
        intent = command["intent"]
        if intent == "continue":
            return _decision(snapshot, state)
        states = [(c, claim_state(snapshot, c)) for c in snapshot.graph["claims"]]
        gating = set(state.frozen_claim_ids) if state.frozen_claim_ids is not None else {
            c["id"] for c in snapshot.graph["claims"] if c["gating"]}
        scoped = [(c, cs) for c, cs in states if c["id"] in gating]
        if intent == "stop":
            if state.budgets["passes_used"] >= state.budgets["max_passes"]:
                return _decision(snapshot, replace(state, status="stopped_budget"))
            deferred = [c for c, _ in states if c["id"] not in gating]
            status = "stopped_frozen" if deferred else "stopped_residual"
            return _decision(snapshot, replace(state, status=status))
        missing = next(((c, cs) for c, cs in scoped if cs != "approved"), None)
        if missing:
            c, cs = missing
            evidence = active_evidence(snapshot, c)
            research = [a for a in evidence if a["kind"] == "research"]
            spikes = [a for a in evidence if a["kind"] == "spike"]
            if cs == "discarded":
                reason, parameters = "claim.refuted", {}
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
        derivation = digest({"graph": snapshot.graph,
                             "claims": [(c["id"], claim_state(snapshot, c),
                                         [a["artifact_id"] for a in active_evidence(snapshot, c)])
                                        for c in snapshot.graph["claims"]],
                             "frozen": state.frozen_claim_ids})
        if derivation != state.last_derivation_digest:
            if state.budgets["passes_used"] >= state.budgets["max_passes"]:
                return _decision(snapshot, state, "Block", reason="budget.exhausted",
                                 parameters={"resource": "pass"})
            budgets = dict(state.budgets)
            budgets["passes_used"] += 1
            state = replace(state, budgets=budgets, last_derivation_digest=derivation)
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
        if auditor_pair == covered_pair:
            return _decision(snapshot, state, "Block", reason="audit.same_model")
        return _decision(snapshot, replace(state, status="converged"))

    return _decision(snapshot, state, "Fault", reason="unsupported")
