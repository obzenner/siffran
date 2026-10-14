"""Pure command evaluation over immutable Empirica snapshots."""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from types import MappingProxyType
from typing import Any

from .canonical import canonical_digest
from .freshness import ActiveSpikeHead, FileBinding, ObservationState, evaluate_freshness, valid_claim_id
from .run import OperationalState
from . import citations, confirmation, governance

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
    #: A bootstrap requirement's words when its human acts on it, by obligation id (the contract's ``waiting``).
    bootstrap_waiting: Mapping[str, str]
    audit_obligation: tuple[str, str]
    #: ``(obligation id, must)`` of the report's other stages: ``confirmation``, ``passes`` and ``terminal``.
    stage_obligations: Mapping[str, tuple[str, str]]
    bootstrap_operations: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    governance_controls: Mapping[str, Any]
    recovery_exclusions: Mapping[str, tuple[str, ...]]
    late_route_must: str
    default_next_action: str
    untrusted_delimiters: Mapping[str, str]
    #: The most items one human batch carries: the request schema's ``maxItems`` for a receipt's decisions.
    batch_limit: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage_obligations", _freeze(self.stage_obligations))
        object.__setattr__(self, "bootstrap_waiting", _freeze(self.bootstrap_waiting))
        object.__setattr__(self, "governance_controls", _freeze(self.governance_controls))
        object.__setattr__(self, "recovery_exclusions", _freeze(self.recovery_exclusions))
        object.__setattr__(self, "untrusted_delimiters", _freeze(self.untrusted_delimiters))


@dataclass(frozen=True)
class EvaluationSnapshot:
    state: OperationalState
    history: tuple[dict[str, Any], ...]
    graph: dict[str, Any] | None
    contract: ContractView
    command: dict[str, Any]
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
    #: The workspace's capture of the file a submitted research record cites (``core.citations``), when
    #: the command is such a record: an object with ``observation`` (state, sha256) and ``content``.
    cited: Any = None
    #: Where the run's workspace lives, when the workspace says (an absolute citation inside it is refused).
    workspace_root: str | None = None

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
    def stage_obligations(self):
        return self.contract.stage_obligations

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
    def default_next_action(self):
        return self.contract.default_next_action

    @property
    def untrusted_delimiters(self):
        return self.contract.untrusted_delimiters

    @property
    def batch_limit(self):
        return self.contract.batch_limit


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


GRAPH_INVALID = "graph.invalid"
GRAPH_ROOT_NOT_GATING = "graph.root_not_gating"


def graph_refusal(value: Any, *, admission: bool = True) -> str | None:
    """Why ``value`` is not a claim graph, else ``None``: ``graph.invalid`` for a structure the core cannot read, and (when
    ``admission``) ``graph.root_not_gating`` for a well-formed graph whose root is outside the effective scope.

    The root is the goal-level assurance claim, and a claim's state comes from its own evidence plus its scoped children
    (ADR-0045), so a root outside the effective scope could never converge: that is an admission rule, applied when a
    graph is recorded. A graph read back from history is held to the structure only (``admission=False``)."""
    if not _well_formed_graph(value):
        return GRAPH_INVALID
    root = next(claim for claim in value["claims"] if claim["id"] == value["root"])
    return GRAPH_ROOT_NOT_GATING if admission and not root["gating"] else None


def valid_graph(value: Any) -> bool:
    """Whether ``value`` may be recorded as the claim graph (``graph_refusal`` finds nothing to refuse)."""
    return graph_refusal(value) is None


def _well_formed_graph(value: Any) -> bool:
    """Whether ``value`` has the closed shape of a claim graph: bounded claims and edges, a root connected to every claim."""
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
    """Digest binding a claim's id and text; evidence for an edited claim no longer matches it."""
    return digest({"claim_id": claim["id"], "text": claim["text"]})


def active_evidence(snapshot: EvaluationSnapshot, claim: dict[str, Any]) -> list[dict[str, Any]]:
    """Research and spike artifacts bound to the claim's current digest; only the latest spike is
    active, and research a human rejected is not."""
    cd = claim_digest(claim)
    evidence = [a for a in snapshot.history if a.get("kind") in {"research", "spike"} and a.get("claim_id") == claim["id"] and a.get("claim_digest") == cd
                and not (a["kind"] == "research" and confirmation.rejected(snapshot, a))]
    spikes = [a for a in evidence if a["kind"] == "spike"]
    active_spike = spikes[-1:] if spikes else []
    return [a for a in evidence if a["kind"] == "research"] + active_spike


def claim_confirmations(snapshot: EvaluationSnapshot, claim: dict[str, Any]) -> list[dict[str, Any]]:
    """The human receipts that bear on the claim's active evidence, and its own decision if any."""
    receipts = [r for a in active_evidence(snapshot, claim) if a["kind"] == "research"
                if (r := confirmation.receipt(snapshot, a)) is not None]
    answer = confirmation.decision(snapshot, claim["id"], claim_digest(claim))
    return receipts + ([answer] if answer is not None else [])


def evidence_digest(snapshot: EvaluationSnapshot, claim: dict[str, Any]) -> str:
    """The digest an audit verdict binds per reviewed claim: its ordered active evidence ids and the
    human receipts on them, so an audit that predates a confirmation is stale."""
    return digest([a["artifact_id"] for a in active_evidence(snapshot, claim)]
                  + [r["artifact_id"] for r in claim_confirmations(snapshot, claim)])


#: The artifact kinds the audit dossier covers; ``argument_digest`` binds every one of them.
#: It binds the confirmation artifacts, not the projected ``verification`` rows: a row's ``retrieve``
#: comes from ``invocation.audit_retrieval``, which is fixed when the run starts and re-checked when an
#: audit launches. A capability that could change mid-run would have to enter the digest.
DOSSIER_KINDS = frozenset({"research", "spike_request", "spike", confirmation.KIND})


#: The artifacts that are the work of the run: what an author or a human records. A presentation (a host showing a batch) and
#: the observation basis are not work, so they never move ``progress_basis``.
PROGRESS_KINDS = frozenset({"graph", "research", "spike_request", "spike", "attribution", "audit_verdict", confirmation.KIND})


def progress_basis(snapshot: EvaluationSnapshot, blockers: Sequence[Mapping[str, Any]]) -> str:
    """The one owner of "did the run move" (ADR-0068): a digest of the blockers (code and parameters) and of the run state that
    an author's or a host's action can change: status, the bootstrap facts, the governance revision, the graph, the frozen scope, every recorded
    artifact of the work, the audit children's states. A read (``GetRun``, ``GetArgument``), a presentation and the budget
    counters, which move on every evaluation, never change it. Hosts remember the value at their last continuation and compare
    only that."""
    state = snapshot.state
    return digest({
        "status": state.status, "facts": _bootstrap_facts(snapshot),
        "governance": [state.governance.get("plan_revision"), state.governance.get("proposal_digest")],
        "blockers": [[b["reason"], b["parameters"]] for b in blockers],
        "graph": snapshot.graph,
        "frozen": None if state.frozen_claim_ids is None else sorted(state.frozen_claim_ids),
        "work": [a["artifact_id"] for a in snapshot.history if a.get("kind") in PROGRESS_KINDS],
        "children": [[c["child_id"], c["state"]] for c in state.children],
    })


def _terminal_consent_record(item: Mapping[str, Any]) -> bool:
    """A human's answer to the terminal step, not evidence about a claim. It is marked by the core when it records the
    answer to the presentation of a terminal batch (``_confirmation_artifact``); the name of an id says nothing, because
    a claim may be called ``stopped_budget``."""
    return item.get("kind") == confirmation.KIND and item.get("terminal") is True


def dossier_artifact_ids(history: tuple[dict[str, Any], ...]) -> list[str]:
    """Ids of the artifacts the dossier covers, in history order. The consent to end the run is not among them: it is
    given after the audit that the end depends on, and must not make that audit stale."""
    return [a["artifact_id"] for a in history if a.get("kind") in DOSSIER_KINDS and not _terminal_consent_record(a)]


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
    return claim_state_cause(snapshot, claim, stale)[0]


def claim_state_cause(snapshot: EvaluationSnapshot, claim: dict[str, Any],
                      stale: set[str]) -> tuple[str, Mapping[str, Any] | None]:
    """A claim's own state, before its supporters are considered, and for a ``discarded`` one the recorded artifact that
    discarded it: the failed spike, else the first refuting research, else the human's answer that rejected a
    ``needs-decision`` claim (``None`` for every other state). The one rule of a claim's own state: the gate's
    ``local_claim_state`` is its first element, and the observer's dropped claims read their cause from it."""
    evidence = active_evidence(snapshot, claim)
    research = [a for a in evidence if a["kind"] == "research"]
    supporting = any(a["outcome"] == "supporting" and confirmation.counts(snapshot, a)
                     for a in research)
    refuting = any(a["outcome"] == "refuting" for a in research)
    spikes = [a for a in evidence if a["kind"] == "spike"]
    spike_failed = bool(spikes and spikes[-1]["outcome"] == "fail")
    sensitive = _freshness_sensitive(snapshot, claim)
    spike_ok = bool(sensitive and spikes[-1]["artifact_id"] not in stale)
    if supporting and refuting and not spike_failed:
        return "open", None
    if spike_failed:
        return "discarded", spikes[-1]
    if refuting:
        return "discarded", next(a for a in research if a["outcome"] == "refuting")
    if claim["kind"] == "needs-decision":
        answer = confirmation.decision(snapshot, claim["id"], claim_digest(claim))
        if answer is None:
            return "blocked", None
        return ("approved", None) if answer["outcome"] == confirmation.APPROVE else ("discarded", answer)
    approved = supporting and (claim["kind"] == "ordinary" or
                               (claim["kind"] == "needs-experiment" and spike_ok))
    return ("approved" if approved else "open"), None


def _awaiting_human(snapshot: EvaluationSnapshot, research: list[dict[str, Any]]) -> list[str]:
    """Ids of the supporting records a claim waits on, when none of them can approve it yet."""
    supporting = [a for a in research if a["outcome"] == "supporting"]
    if not supporting or any(confirmation.counts(snapshot, a) for a in supporting):
        return []
    return [a["artifact_id"] for a in supporting]


CONFIRMATION_REASON = "evidence.confirmation_required"
#: The reason a deliberative run's terminal step is blocked on the human's consent (ADR-0063 amendment, ADR-0068).
TERMINAL_STOP_REASON = "governance.terminal_stop_pending"
#: What a deliberative run may end as without converging; also the id of the terminal item of a confirmation batch.
TERMINAL_STATUSES = ("stopped_frozen", "stopped_residual", "stopped_budget")


def pending_human(snapshot: EvaluationSnapshot, scope: tuple[str, ...]) -> dict[str, list[str]]:
    """Ids of the research records in scope that still wait on a human, by claim."""
    pending = {}
    for claim in snapshot.graph["claims"]:
        if claim["id"] in scope and (records := [
                a["artifact_id"] for a in active_evidence(snapshot, claim)
                if a["kind"] == "research"
                and confirmation.pending_route(snapshot, a) in confirmation.HUMAN_ROUTES]):
            pending[claim["id"]] = records
    return pending


def pending_decisions(snapshot: EvaluationSnapshot, derivation: ClaimDerivation) -> list[str]:
    """Ids of the gating ``needs-decision`` claims still waiting for the human's answer."""
    return [c["id"] for c in snapshot.graph["claims"]
            if c["id"] in derivation.scope and c["kind"] == "needs-decision"
            and derivation.states[c["id"]] == "blocked"]


def terminal_digest(snapshot: EvaluationSnapshot, derivation: ClaimDerivation, status: str) -> str:
    """What a human's consent to end the run as ``status`` is bound to: the status, the run's derivation and the dossier (the
    graph and every artifact of it, a human's confirmation receipts included but never the terminal consents themselves), so
    a changed argument, evidence, receipt or frozen scope needs a new answer (the terminal item's wording digest)."""
    return digest({"terminal": status, "derivation": derivation_digest(snapshot, derivation),
                   "dossier": digest({"graph": snapshot.graph, "evidence": dossier_artifact_ids(snapshot.history)})})


def terminal_key(status: str, wording: str) -> str:
    """The key of the terminal batch for ``status`` and its wording digest. A host that holds a terminal batch checks it
    against this before it shows it; the core admits a presentation only while the key is one of its own candidates."""
    return digest({"terminal": status, "digest": wording})


def terminal_consented(snapshot: EvaluationSnapshot, derivation: ClaimDerivation, status: str) -> bool:
    """Whether a human approved ending the run as ``status`` for this derivation (an accepted receipt of the batch)."""
    wording = terminal_digest(snapshot, derivation, status)
    return any(_terminal_consent_record(a) and a.get("claim_id") == status and a.get("claim_digest") == wording
               and a.get("outcome") == confirmation.APPROVE for a in snapshot.history)


def terminal_batch(snapshot: EvaluationSnapshot, derivation: ClaimDerivation, status: str) -> dict[str, Any]:
    """The batch of a terminal step: the one item asking whether to end the run as ``status``. It holds nothing else
    (ADR-0063): pending evidence either comes first at a report or is moot at a stop."""
    wording = terminal_digest(snapshot, derivation, status)
    return {"key": terminal_key(status, wording), "record_ids": [],
            "claims": [{"claim_id": status, "claim_digest": wording}], "terminal": {"status": status, "digest": wording}}


def terminal_askable(snapshot: EvaluationSnapshot) -> bool:
    """Whether the consent to end the run can be asked at all: a route is recorded and a graph is selected (nothing is
    started before a route, so a stop then ends the run without a question). The one predicate of the projection of the
    terminal batch, of the gate that asks it and of the admission of its presentation (``_human_batch_block``)."""
    return terminal_unaskable(snapshot) is None


def terminal_unaskable(snapshot: EvaluationSnapshot) -> tuple[str, str | None] | None:
    """Why the consent to end the run cannot be asked yet, as ``(reason, affected)``, else ``None``: the one place that reads the
    route and the graph for it."""
    facts = _bootstrap_facts(snapshot)
    if not facts["route.recorded"]:
        return "route.required", "obligation.route"
    if not facts["graph.selected"]:
        return "graph.missing", None
    return None


def pending_terminal(snapshot: EvaluationSnapshot, intent: str) -> str | None:
    """The ``stopped_*`` status ``intent`` would commit but for the human's consent, when this deliberative run needs it;
    else ``None``. It is the gate itself run without the consent check, so the batch and the gate cannot disagree."""
    if (snapshot.graph is None or snapshot.state.status != "active"
            or snapshot.state.governance["control_mode"] != "deliberative" or not terminal_askable(snapshot)):
        return None
    hypothetical = replace(snapshot, command={"type": "EvaluateRun", "run_id": snapshot.run_id, "intent": intent})
    decision = _evaluate(hypothetical, hypothetical.command, consent=False)
    status = decision.intent.state.status
    return status if decision.result_type == "Allow" and status in TERMINAL_STATUSES else None


def batch_candidates(snapshot: EvaluationSnapshot, derivation: ClaimDerivation) -> list[dict[str, Any]]:
    """Every batch a presentation may name now: the pending evidence batch, and the terminal batch each intent (a
    report, a stop) would ask. The presentation names its key, so the host never has to say which step asked."""
    found = [] if (batch := confirmation_batch(snapshot, derivation)) is None else [batch]
    for intent in ("report_convergence", "stop"):
        if (status := pending_terminal(snapshot, intent)) is not None:
            candidate = terminal_batch(snapshot, derivation, status)
            if all(candidate["key"] != other["key"] for other in found):
                found.append(candidate)
    return found


def confirmation_batch(snapshot: EvaluationSnapshot, derivation: ClaimDerivation) -> dict[str, Any] | None:
    """What a human would be shown now, or ``None`` when nothing waits on one.

    One batch for one human: the gating ``needs-decision`` claims first, in graph order, then the research
    records, in their claim's graph order and then by submission. At most ``batch_limit`` items are shown;
    the rest wait, and are shown after a decision changes the derivation. The ``key`` identifies the
    derivation and exactly the shown items, so a batch is never presented twice and a changed derivation
    is a new one."""
    claims = {c["id"]: c for c in snapshot.graph["claims"]}
    waiting = pending_decisions(snapshot, derivation)
    records = [i for ids in pending_human(snapshot, derivation.scope).values() for i in ids]
    if not waiting and not records:
        return None
    limit = snapshot.batch_limit
    shown_claims = waiting[:limit]
    shown_records = records[:limit - len(shown_claims)]
    rows = [{"claim_id": cid, "claim_digest": claim_digest(claims[cid])} for cid in shown_claims]
    key = digest({"derivation": derivation_digest(snapshot, derivation),
                  "records": sorted(shown_records), "claims": [(r["claim_id"], r["claim_digest"]) for r in rows]})
    return {"key": key, "record_ids": shown_records, "claims": rows}


def human_batch(snapshot: EvaluationSnapshot, derivation: ClaimDerivation,
                blockers: tuple[dict[str, Any], ...] | None = None) -> dict[str, Any] | None:
    """The batch the gate asks a human about now: the pending batch, once nothing the author can do alone is left. The
    one predicate the report, the audit reservation and (as ``confirmation_batch``) the view's audit row order by."""
    blockers = claim_blockers(snapshot, derivation) if blockers is None else blockers
    if any(b["reason"] != CONFIRMATION_REASON for b in blockers):
        return None
    return confirmation_batch(snapshot, derivation)


def _spike_requirement(snapshot: EvaluationSnapshot, claim: Mapping[str, Any], derivation: ClaimDerivation,
                       research: list[dict[str, Any]],
                       spikes: list[dict[str, Any]]) -> tuple[str, dict[str, Any], bool] | None:
    """The spike reason of a ``needs-experiment`` claim whose research supports it, its parameters and whether
    the author can act on it now (no passing, fresh spike yet); ``None`` for any other claim."""
    if claim["kind"] != "needs-experiment" or not any(item["outcome"] == "supporting" for item in research):
        return None
    stale_head = (derivation.stale_heads.get(spikes[-1]["artifact_id"])
                  if spikes and _freshness_sensitive(snapshot, claim) else None)
    if stale_head is not None:
        return "claim.spike_stale", {"changes": [{"path": change.path, "state": change.state.value}
                                                   for change in stale_head.changes]}, True
    return "claim.spike_missing", {}, not (spikes and spikes[-1].get("outcome") == "pass")


#: The claim reasons whose parameters carry the claim they are about (``claim_id``) and whose contract text names it.
CLAIM_NAMED_REASONS = frozenset({"claim.research_missing", "claim.research_unbound", "claim.spike_missing", "claim.refuted"})


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
            reason, parameters = "evidence.confirmation_required", confirmation.parameters(
                snapshot, [], pending_decisions(snapshot, derivation))
        elif claim_conflicted(snapshot, claim):
            reason = "evidence.conflict"
        elif (spike := _spike_requirement(snapshot, claim, derivation, research, spikes)) and spike[2]:
            # What the author can do now comes before waiting on a human; the batch is still projected.
            reason, parameters = spike[0], spike[1]
        elif awaiting := _awaiting_human(snapshot, research):
            reason, parameters = "evidence.confirmation_required", confirmation.parameters(
                snapshot, awaiting, pending_decisions(snapshot, derivation))
        elif spike:
            reason, parameters = spike[0], spike[1]
        else:
            historical_research = any(
                item.get("kind") == "research" and item.get("claim_id") == target_id
                and item.get("claim_digest") != claim_digest(claim)
                for item in snapshot.history)
            reason = ("claim.research_unbound" if historical_research
                      else "claim.research_missing")
        if reason in CLAIM_NAMED_REASONS:  # the reason's text names the claim to discharge, not "this claim"
            parameters = {**parameters, "claim_id": target_id}
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
    """Bootstrap obligations, ordered ``next_actions`` (empty for a terminal run or once bootstrap is complete), and readiness flags."""
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
            if governance.interaction_error(governed, confirmation.interactions(snapshot)):
                actions.append("residual.accept")
            else:
                actions.append("governance.propose")
        elif facts["route.recorded"] and not facts["investigation.recorded"]:
            actions.append("investigation.record")
    request_ready = active_run and bootstrap_precondition(snapshot, "configure_run") is None
    display_ready = (active_run and bootstrap_precondition(snapshot, "governance.present") is None
                     and governance.interaction_error(governed, confirmation.interactions(snapshot)) is None
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


def _confirmation_artifact(snapshot: EvaluationSnapshot, receipt_id: str,
                           item: Mapping[str, Any]) -> dict[str, Any] | None:
    """The artifact recording one receipt decision, or ``None`` when it names nothing in this run."""
    stamps = {"route_stamp": snapshot.state.route_stamp,
              "investigation_stamp": snapshot.state.investigation_stamp}
    base = {"receipt_id": receipt_id, "outcome": item["outcome"], "approval_kind": "host_ui"}
    if item["subject"] == "research":
        record = next((a for a in snapshot.history if a["artifact_id"] == item["artifact_id"]
                       and a.get("kind") == "research"), None)
        if record is None:
            return None
        # ``record_id``, not ``artifact_id``: the latter names the confirmation itself.
        body = {"subject": "research", "record_id": record["artifact_id"],
                "claim_id": record["claim_id"], "claim_digest": record["claim_digest"],
                **({"note": item["note"]} if "note" in item else {})}
    else:
        shown = confirmation.presentation(snapshot, receipt_id)
        ended = ((shown or {}).get("terminal") or {})
        body = {"subject": "claim", "claim_id": item["claim_id"], "claim_digest": item["claim_digest"],
                **({"answer": item["answer"]} if "answer" in item else {}),
                # The answer to the terminal item of a presentation is the consent to end; nothing else is marked.
                **({"terminal": True} if (item["claim_id"], item["claim_digest"]) == (ended.get("status"), ended.get("digest"))
                   else {})}
    return artifact(confirmation.KIND, {**body, **base, **stamps})


def _human_batch_block(snapshot: EvaluationSnapshot, *, terminal: bool = False) -> Decision | None:
    """Why no human receipt or presentation is admitted now, else ``None``. An evidence batch is asked only of an investigated
    run (every record of a human's answer carries the run's route and investigation witnesses). The consent to end a run is
    its own admission: ending never requires approving or starting the work, so a route and a graph are enough."""
    state = snapshot.state
    if terminal:
        if (why := terminal_unaskable(snapshot)) is not None:
            return _decision(snapshot, state, "Block", reason=why[0], affected=why[1])
    elif (blocked := _investigation_block(snapshot)) is not None:
        return blocked
    if snapshot.graph is None or frozen_scope_invalid(state, snapshot.graph):
        return _decision(snapshot, state, "Block", reason="graph.invalid")
    if not confirmation.human_available(snapshot):
        return _decision(snapshot, state, "Block", reason="governance.approval_unavailable")
    return None


def plan_evidence_presentation(snapshot: EvaluationSnapshot, payload: Mapping[str, Any]) -> Decision:
    """Purely record that the host showed the human the batch it names.

    The receipt id names the presentation; sending it again is ``Inert``. A presentation of nothing is a conflict. Each
    presentation spends one human interaction, so the interaction ceiling is what bounds asking: a batch left on Later,
    cancelled or timed out may be shown again by an explicit report (ADR-0067); only the host's explicit
    ``report_convergence`` presents (either intent: a report or a stop), never the Stop hook's pause or an auto continuation."""
    state = snapshot.state
    wanted = None if snapshot.graph is None else next(
        (b for b in batch_candidates(snapshot, derive_claims(snapshot)) if b["key"] == payload["key"]), None)
    if (blocked := _human_batch_block(snapshot, terminal=confirmation.is_terminal(wanted))) is not None:
        return blocked
    if confirmation.presentation(snapshot, payload["receipt_id"]) is not None:
        return _decision(snapshot, state, "Inert")
    batch = wanted
    # The presentation names the batch the host showed; any other (a record joined, a wording changed) is refused
    # and nothing is spent, so what a human was shown is exactly what is recorded as presented.
    # A batch already shown may be shown again by the next explicit report (bounded below by the interaction ceiling).
    if batch is None:
        return _decision(snapshot, state, "Fault", reason="conflict")
    if governance.interactions_remaining(
            state.governance, confirmation.interactions(snapshot))["total"] == 0:
        return _decision(snapshot, state, "Block", reason="governance.interaction_limit")
    return _decision(snapshot, state, artifacts=(artifact(confirmation.PRESENTATION, {
        "receipt_id": payload["receipt_id"], "key": batch["key"], "record_ids": batch["record_ids"],
        "claims": batch["claims"], **({"terminal": batch["terminal"]} if confirmation.is_terminal(batch) else {}),
        "approval_kind": "host_ui", "route_stamp": state.route_stamp,
        "investigation_stamp": state.investigation_stamp}),))


def plan_evidence_confirmation(snapshot: EvaluationSnapshot, payload: Mapping[str, Any]) -> Decision:
    """Purely admit one human receipt of per-item decisions and plan its confirmation artifacts.

    The receipt id must name the current presentation (the pending set has not changed since it was
    shown) and every decision must be about something that presentation showed. An identical replay is
    ``Inert``. A decision for something unknown, no longer current, not waiting on a human, or already
    decided differently fails closed, and so does a receipt id reused for other content. Nothing is
    admitted unless every decision is."""
    state = snapshot.state
    shown_first = confirmation.presentation(snapshot, payload["receipt_id"])
    if (blocked := _human_batch_block(snapshot, terminal=confirmation.is_terminal(shown_first))) is not None:
        return blocked
    items = payload["decisions"]
    planned = [_confirmation_artifact(snapshot, payload["receipt_id"], item) for item in items]
    subjects = {(i["subject"], i.get("artifact_id", i.get("claim_id"))) for i in items}
    if None in planned or len(subjects) != len(items):
        return _decision(snapshot, state, "Fault", reason="conflict")
    known = {a["artifact_id"] for a in snapshot.history}
    if all(a["artifact_id"] in known for a in planned):
        return _decision(snapshot, state, "Inert")
    others = [a for a in snapshot.history if a.get("kind") == confirmation.KIND
              and a["receipt_id"] == payload["receipt_id"]]
    shown = confirmation.presentation(snapshot, payload["receipt_id"])
    current = {b["key"] for b in batch_candidates(snapshot, derive_claims(snapshot))}
    if others or shown is None or shown["key"] not in current:
        return _decision(snapshot, state, "Fault", reason="conflict")
    asked = {("research", r) for r in shown["record_ids"]} | {
        ("claim", c["claim_id"], c["claim_digest"]) for c in shown["claims"]}
    answered = {("research", i["artifact_id"]) if i["subject"] == "research"
                else ("claim", i["claim_id"], i["claim_digest"]) for i in items}
    if not answered <= asked:
        return _decision(snapshot, state, "Fault", reason="conflict")
    return _decision(snapshot, state, artifacts=tuple(planned))


def approved_claim_ids(snapshot: EvaluationSnapshot) -> list[str]:
    """Ids of the approved gating claims an audit reviews."""
    if snapshot.graph is None or frozen_scope_invalid(snapshot.state, snapshot.graph):
        return []
    derivation = derive_claims(snapshot)
    return [c["id"] for c in snapshot.graph["claims"]
            if c["id"] in derivation.scope and derivation.states[c["id"]] == "approved"]


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


def audit_exclusions(snapshot: EvaluationSnapshot) -> list[str]:
    """The opaque identity classes an auditor must not run on: the current author's and every producer of the evidence an
    audit would cover (sorted). The verdict check refuses an auditor equal to one of them (``independence``), so a launch
    on one of them would spend a spawn on a verdict that cannot count; the reservation refuses it first."""
    by_id = {item.get("artifact_id"): item for item in snapshot.history}
    producers = [by_id.get(artifact_id, {}).get("producer") for artifact_id in covered_artifact_ids(snapshot)]
    classes = {identity_pair(producer) for producer in [_producer(snapshot.state), *producers]}
    return sorted(value for value in classes if value is not None)


def audit_binding(snapshot: EvaluationSnapshot) -> dict[str, Any]:
    """Derive the exact audit dossier binding from the current immutable snapshot."""
    if snapshot.graph is None or frozen_scope_invalid(snapshot.state, snapshot.graph):
        return {}
    all_ids = [c["id"] for c in snapshot.graph["claims"]]
    derivation = derive_claims(snapshot)
    gating = list(derivation.scope)
    deferred = [cid for cid in all_ids if cid not in set(gating)]
    reviewed = []
    for claim in snapshot.graph["claims"]:
        if claim["id"] in gating and derivation.states[claim["id"]] == "approved":
            reviewed.append({"claim_id": claim["id"],
                             "evidence_digest": evidence_digest(snapshot, claim)})
    return {"argument_digest": digest({"graph": snapshot.graph,
                                             "evidence": dossier_artifact_ids(snapshot.history)}),
            "goal_digest": digest(snapshot.state.goal),
            "frozen_scope_digest": (None if snapshot.state.frozen_claim_ids is None
                                    else digest(gating)),
            "deferred_scope_digest": digest(deferred),
            "reviewed_claims": reviewed,
            "scope_review": ("pass" if snapshot.state.frozen_claim_ids is not None else None)}


def audit_pending(snapshot: EvaluationSnapshot) -> bool:
    """Whether a re-audit is in flight: an audit child that has not settled and whose launch dossier still binds the run.
    The one selector of that fact (acceptance, the audit summary and the observer consume it)."""
    return any(child["resource_class"] == "audit" and child["state"] in {"reserved", "launching", "pending"}
               and audit_operation_current(snapshot, child) for child in snapshot.state.children)


def audit_verdicts(snapshot: EvaluationSnapshot) -> tuple[Mapping[str, Any], ...]:
    """Every recorded audit verdict of the run, in history order. The one selector of that fact."""
    return tuple(item for item in snapshot.history if item.get("kind") == "audit_verdict")


def latest_audit_verdict(snapshot: EvaluationSnapshot) -> Mapping[str, Any] | None:
    """The run's latest recorded audit verdict, else ``None``: the last of ``audit_verdicts``."""
    audits = audit_verdicts(snapshot)
    return audits[-1] if audits else None


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


def audit_covers(snapshot: EvaluationSnapshot, verdict: Mapping[str, Any]) -> bool:
    """Whether a recorded verdict, whatever it decided, still binds the current argument, goal and scope: the one
    owner of an audit's *currency* (``audit_passes`` is a covering pass)."""
    expected = audit_binding(snapshot)
    child = next((c for c in snapshot.state.children if c["child_id"] == verdict.get("child_id")), None)
    return (child is not None and audit_operation_current(snapshot, child) and bool(expected) and
            all(_plain(verdict.get(key)) == value for key, value in expected.items()))


def audit_passes(snapshot: EvaluationSnapshot, verdict: Mapping[str, Any]) -> bool:
    return verdict.get("verdict") == "pass" and audit_covers(snapshot, verdict)


INDEPENDENCE_REASONS = {
    "unverified": "audit.independence_unverified",
    "mixed": "audit.producers_mixed",
    "same_model": "audit.same_model",
}


def independence(snapshot: EvaluationSnapshot, verdict: Mapping[str, Any]) -> str:
    """Classify reviewer/producer independence from opaque host identities."""
    auditor, producers = audit_attributions(snapshot, verdict)
    return _independence(auditor, producers, bool(approved_claim_ids(snapshot)))


def independence_then(snapshot: EvaluationSnapshot, verdict: Mapping[str, Any]) -> str | None:
    """A verdict's independence as it stood when it was recorded, never against today's evidence: its auditor as the
    host attributed it before the verdict, against the producers of exactly the evidence its audit was launched over
    (the launch dossier the run keeps for the audit child: the approved gating claims and their active evidence ids),
    by the same classification as ``independence``. ``None`` when the run no longer holds that dossier."""
    child = next((c for c in snapshot.state.children if c["child_id"] == verdict.get("child_id")), None)
    dossier = child.get("audit_argument") if child is not None else None
    position = next((n for n, item in enumerate(snapshot.history) if item is verdict
                     or item.get("artifact_id") == verdict.get("artifact_id")), None)
    if not isinstance(dossier, Mapping) or position is None:
        return None
    before = snapshot.history[:position]
    auditor = next((item for item in reversed(before) if item.get("kind") == "attribution"
                    and item.get("subject_kind") == "auditor" and item.get("child_id") == verdict.get("child_id")), None)
    reviewed = [c for c in dossier["claims"] if c["gating"] and c["state"] == "approved"]
    by_id = {item.get("artifact_id"): item for item in before}
    producers = [by_id.get(artifact_id, {}).get("producer") for c in reviewed for artifact_id in c["active_evidence_ids"]]
    return _independence(auditor, producers, bool(reviewed))


def _independence(auditor: Mapping[str, Any] | None, producers: list[Mapping[str, Any] | None], approved: bool) -> str:
    """The one classification of an auditor against the producers of the evidence it reviewed."""
    auditor_class = identity_pair(auditor)
    producer_classes = [identity_pair(producer) for producer in producers]
    if auditor_class is not None and not producer_classes and approved:
        # Every reviewed claim is a human's decision with no evidence behind it: nobody produced it, so
        # there is no producer to be independent of. The audit over the argument is still required.
        return "human_owned"
    if auditor_class is None or not producer_classes or any(value is None for value in producer_classes):
        return "unverified"
    if len(set(producer_classes)) != 1:
        return "mixed"
    return "same_model" if auditor_class == producer_classes[0] else "distinct"


#: Audit blockers whose recovery launches a new audit child; ``audit.pending`` is absent because a
#: pending audit needs no further spawn. These name audit-spawn exhaustion when no budget remains.
AUDIT_SPAWN_REASONS = frozenset({"audit.required", "audit.failed", *INDEPENDENCE_REASONS.values()})


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
    """Derive the first audit blocker after every scoped claim is approved.

    A blocker whose recovery needs a new audit spawn is named ``budget.exhausted`` once the audit
    spawn ceiling is reached, so the author is never told to retry what the core would refuse."""
    blocker = audit_acceptance(snapshot, derivation)
    budgets = snapshot.state.budgets
    if (blocker is not None and blocker["reason"] in AUDIT_SPAWN_REASONS
            and budgets["audit_spawns_used"] >= budgets["max_audit_spawns"]):
        return {"reason": "budget.exhausted", "parameters": {"resource": "audit_spawn"}}
    return blocker


def audit_acceptance(snapshot: EvaluationSnapshot, derivation: ClaimDerivation) -> dict[str, Any] | None:
    """Why the gate does not accept the run's audit, as ``{"reason", "parameters"}``, else ``None``: the one predicate of
    audit acceptance (a settled covering pass; independence, unless a claim is out of scope). The report's audit stage
    blocks on it, and a claim is *assured* exactly when it holds for the claim's run (``core.observer.assurance``)."""
    if audit_pending(snapshot):
        return {"reason": "audit.pending", "parameters": {}}
    audit = latest_audit_verdict(snapshot)
    if audit is None:
        return {"reason": "audit.required", "parameters": {}}
    if not audit_passes(snapshot, audit):
        return {"reason": "audit.failed", "parameters": {}}
    if any(claim_id not in set(derivation.scope)
           for claim_id in (claim["id"] for claim in snapshot.graph["claims"])):
        return None
    classification = independence(snapshot, audit)
    reason = INDEPENDENCE_REASONS.get(classification)
    return None if reason is None else {"reason": reason, "parameters": {}}


#: The stages of a report, in the order the gate meets them: the setup a run lacks, the claims that block, the human's
#: answers, the pass ceiling, the audit, the human's consent to end the run. A requirement is *admissible* (the gate names it
#: now) exactly when no earlier stage has an unmet one.
REPORT_STAGES = ("setup", "claim", "confirmation", "passes", "audit", "terminal")
#: The reasons that wait on a human; the auditor acts on ``audit.pending``; everything else is the agent's.
HUMAN_WAITING_REASONS = frozenset({CONFIRMATION_REASON, TERMINAL_STOP_REASON})
#: Where a requirement waits that is not admissible yet: for the stages before it.
LATER = "later"


@dataclass(frozen=True)
class Requirement:
    """One thing the report still requires of a run. ``reason`` and ``parameters`` are the gate's own when it blocks there; a
    setup requirement has no reason and is worded by its obligation (``required``). ``claims`` are the claims it concerns.
    ``admissible`` says whether the gate names it now; ``actor`` (``human``, ``auditor`` or ``agent``) and ``place`` (where it
    happens: ``next_report``, ``explicit_report``, ``unavailable``, ``limit``, ``in_progress``, ``next_turn``, or ``LATER`` while
    it is not admissible) say who acts. The human's evidence batch has its place whether or not it is admissible: its
    presentability is the batch's (``prompt_error``), not the gate's order."""

    stage: str
    obligation: str
    required: str
    reason: str | None
    parameters: Mapping[str, Any]
    claims: tuple[str, ...]
    affected: str | None
    actor: str
    place: str
    admissible: bool


def terminal_consent_parameters(snapshot: EvaluationSnapshot, derivation: ClaimDerivation, status: str,
                                *, consent: bool = True) -> dict[str, Any] | None:
    """The parameters of ``governance.terminal_stop_pending`` when a deliberative run needs the human's consent to end as
    ``status`` and has not got it, else ``None``, with ``prompt_error`` as the host would meet it. The one predicate of the
    gate's ``end`` and of the terminal requirement."""
    if not (consent and snapshot.state.governance["control_mode"] == "deliberative" and terminal_askable(snapshot)
            and not terminal_consented(snapshot, derivation, status)):
        return None
    key = terminal_key(status, terminal_digest(snapshot, derivation, status))
    return {"status": status, "route": confirmation.human_route(snapshot),
            "case": confirmation.terminal_case(confirmation.prompt_error(snapshot, key, terminal=True),
                                               confirmation.presented(snapshot, key))}


def report_requirements(snapshot: EvaluationSnapshot, derivation: ClaimDerivation | None,
                        blockers: Sequence[Mapping[str, Any]] | None = None, *,
                        setup: bool = True) -> tuple[Requirement, ...]:
    """Everything an active run still owes before the report converges it, in the gate's order, each with who acts and where.

    This is the one projection of the report's ordering. The gate blocks on the first requirement (``_evaluate``); the author
    view lists them as its obligations and holds the ones behind an earlier stage (``core.projection``); the observer
    publishes them as what the run owes and names the first admissible one next (``core.observer``). It is pure: nothing is
    committed, so what the gate reaches only after charging a pass is listed without charging it.

    A stage is listed when its predicate holds: the setup the run lacks (``setup``; the gate refuses it before this point);
    every unmet scoped claim; the human's batch whenever anything waits on a human, even behind agent work (held, not
    dropped); and then the pass ceiling, the audit, and the human's consent to end the run, each held until the stages before
    it are clear (``admissible`` is false). Asked of a
    run that has ended, it is what the report would still require of it (the author view's terminal obligations)."""
    found: list[tuple] = []  # (stage, obligation, required, reason, parameters, claims, affected, prompt_error)
    if setup:
        found += [("setup", row["id"], row["must"], None, {}, (), None, None)
                  for row in bootstrap_status(snapshot)["active"] if row["status"] != "satisfied"]
    if snapshot.graph is not None and derivation is not None:
        blockers = claim_blockers(snapshot, derivation) if blockers is None else blockers
        batch = confirmation_batch(snapshot, derivation)
        order = [c["id"] for c in snapshot.graph["claims"]]
        for b in blockers:
            if b["reason"] == CONFIRMATION_REASON and batch is not None:
                continue  # the human's batch below is these claims' requirement, once
            found.append(("confirmation" if b["reason"] == CONFIRMATION_REASON else "claim", "claim:" + b["claim_id"], "",
                          b["reason"], b["parameters"], (b["target_claim_id"],), "claim:" + b["target_claim_id"],
                          confirmation.route_error(confirmation.human_route(snapshot))))
        if batch is not None:
            waiting = {*pending_decisions(snapshot, derivation), *pending_human(snapshot, derivation.scope)}
            found.append(("confirmation", snapshot.stage_obligations["confirmation"][0],
                          snapshot.stage_obligations["confirmation"][1], CONFIRMATION_REASON,
                          confirmation.parameters(snapshot, batch["record_ids"], [r["claim_id"] for r in batch["claims"]],
                                                  batch["key"]),
                          tuple(c for c in order if c in waiting), None, confirmation.prompt_error(snapshot, batch["key"])))
        # The later stages are always listed (held behind an earlier one when there is one): the gate reaches them only
        # then, but what the run will owe is no secret.
        passes = pass_budget_blocker(snapshot, derivation_digest(snapshot, derivation))
        audit = audit_blocker(snapshot, derivation)
        if passes is not None:
            obligation, required = snapshot.stage_obligations["passes"]
            found.append(("passes", obligation, required, passes["reason"], passes["parameters"], (), None, None))
        if audit is not None:
            obligation, required = snapshot.audit_obligation
            found.append(("audit", obligation, required, audit["reason"], audit["parameters"], (), "obligation.audit", None))
        elif passes is None and any(claim_id not in set(derivation.scope) for claim_id in order):
            consent = terminal_consent_parameters(snapshot, derivation, "stopped_frozen")
            if consent is not None:
                obligation, required = snapshot.stage_obligations["terminal"]
                key = terminal_key("stopped_frozen", terminal_digest(snapshot, derivation, "stopped_frozen"))
                found.append(("terminal", obligation, required, TERMINAL_STOP_REASON, consent, (), None,
                              confirmation.prompt_error(snapshot, key, terminal=True)))
    first = min((REPORT_STAGES.index(row[0]) for row in found), default=0)
    # Who acts on a human's step is the human only while the request is with them (P3c): the configuration once the agent
    # has asked and a host can show it (``governance.human_wait``); the end of the run unless the human already declined it
    # (then the agent keeps working until the human tells it to stop again).
    approval = next(o for p, o, _ in snapshot.bootstrap_requirements if p == "governance.approved")
    configuration_waits = governance.human_wait(
        snapshot.state.governance, governance.interaction_error(snapshot.state.governance, confirmation.interactions(snapshot)))
    requirements = []
    for stage, obligation, required, reason, parameters, claims, affected, prompt_error in found:
        human = ((reason in HUMAN_WAITING_REASONS and not (reason == TERMINAL_STOP_REASON
                                                            and parameters["case"] == "declined"))
                 or (stage == "setup" and obligation == approval and configuration_waits))
        actor = "human" if human else "auditor" if reason == "audit.pending" else "agent"
        admissible = REPORT_STAGES.index(stage) == first
        place = ((confirmation.ask_where(prompt_error) if reason else "next_report") if actor == "human"
                 else "in_progress" if actor == "auditor" else "next_turn")
        # Whether the gate names a requirement now (``admissible``) is one fact; whether a human will be shown the question is
        # another, and the host's: the core projects the batch whatever else blocks, and an explicit report presents it
        # (``confirmation.presentable``) even while the agent still owes claim work. So the human's batch is not "later"
        # while the core would admit its presentation (``_human_batch_block``): a run not yet investigating (its route, its
        # approval or its investigation missing) has its presentation refused, so nobody is shown it and it waits (P4).
        asked = (obligation == snapshot.stage_obligations["confirmation"][0]
                 and _investigation_block(snapshot) is None)
        if actor == "human" and stage == "setup":
            required = snapshot.contract.bootstrap_waiting[obligation]  # the human's words, not the agent's obligation
        requirements.append(Requirement(stage, obligation, required, reason, parameters, claims, affected, actor,
                                        place if admissible or asked else LATER, admissible))
    return tuple(requirements)


def evaluate_snapshot(snapshot: EvaluationSnapshot, command: dict[str, Any]) -> Decision:
    """Apply one validated command to the snapshot without performing I/O (see ``_evaluate``)."""
    return _evaluate(snapshot, command, consent=True)


def _evaluate(snapshot: EvaluationSnapshot, command: dict[str, Any], *, consent: bool) -> Decision:
    """``consent=False`` is the gate without the deliberative consent to end non-converged (``pending_terminal`` asks what
    it would have committed); ``evaluate_snapshot`` is the gate itself.

    Branches, in order: an invalid frozen scope blocks; a non-active (terminal) run allows only
    reads and ``stop``/``report_convergence`` evaluation and is otherwise ``Inert``; read commands
    are allowed; a graphless ``stop`` ends ``stopped_residual``; an operation whose bootstrap
    precondition is unmet blocks with that reason; governed work is blocked until the current
    proposal is approved (preparation actions excepted); then each ``ObserveAction`` kind
    (route, investigate, graph, research, freeze, configure_run, child_reserve; any other is
    a ``Fault``) and each
    ``EvaluateRun`` intent (continue, report_convergence, stop) yields its state transition,
    artifacts, or ``Block``.
    """
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
            refusal = graph_refusal(graph)
            if refusal == GRAPH_INVALID or frozen_scope_invalid(state, graph):
                return _decision(snapshot, state, "Block", reason=GRAPH_INVALID)
            if refusal is not None:
                return _decision(snapshot, state, "Block", reason=refusal)
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
            cited = snapshot.cited
            problem = citations.submission_problem(
                action["source_kind"], payload, cited.content if cited else None,
                cited.observation.state if cited else ObservationState.UNREADABLE,
                snapshot.workspace_root)
            if problem is not None:
                return _decision(snapshot, state, "Block", reason="research.citation_unverified",
                                 parameters={"problem": problem})
            art = artifact("research", {
                "claim_id": claim["id"], "claim_digest": claim_digest(claim),
                "statement_digest": digest(payload),
                "outcome": "supporting" if action["result"] == "supports" else "refuting",
                "source_kind": action["source_kind"], "source_ref": payload["source_ref"],
                "citation": payload["citation"],
                "route_stamp": state.route_stamp,
                "investigation_stamp": state.investigation_stamp,
                "producer": _producer(state),
                **{key: payload[key] for key in ("observed_content_digest", "spike_unavailable")
                   if key in payload},
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
                return _decision(snapshot, state, "Block", reason=reason,
                                 parameters=governance.refusal_parameters(state.governance, reason))
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
            if resource_class == "audit" and snapshot.graph is not None:
                # The audit gate comes after the confirmation the view and the report put first (ADR-0067): an audit
                # launched while a human is about to be asked would be staled by the answer and spend its spawn.
                derivation = derive_claims(snapshot)
                batch = human_batch(snapshot, derivation)
                if batch is not None and confirmation.prompt_error(snapshot, batch["key"]) in confirmation.ASKABLE:
                    return _decision(snapshot, state, "Block", reason=CONFIRMATION_REASON,
                                     parameters=confirmation.parameters(
                                         snapshot, batch["record_ids"], [r["claim_id"] for r in batch["claims"]],
                                         batch["key"]))
            auditor_identity = action.get("auditor_identity") if resource_class == "audit" else None
            if auditor_identity is not None and snapshot.graph is not None:
                excluded = audit_exclusions(snapshot)
                if auditor_identity in excluded:
                    return _decision(snapshot, state, "Block", reason="audit.auditor_excluded",
                                     parameters={"identities": excluded, "models": ", ".join(excluded)})
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

        def end(ended: OperationalState) -> Decision:
            """A step that ends the run without converging: a deliberative run needs the human's consent first
            (ADR-0063 amendment); the auto modes commit as before. A refusal is never a terminal state."""
            asked = terminal_consent_parameters(snapshot, derivation, ended.status, consent=consent)
            if asked is not None:
                return decide(state, "Block", reason=TERMINAL_STOP_REASON, parameters=asked)
            return decide(ended)

        if intent == "stop":
            if state.budgets["passes_used"] >= state.budgets["max_passes"]:
                return end(replace(state, status="stopped_budget"))
            deferred = [c for c, _ in states if c["id"] not in gating]
            status = "stopped_frozen" if deferred else "stopped_residual"
            return end(replace(state, status=status))
        # The first requirement of the report's one ordering (the author alone before the human, the passes before the audit)
        # is what the gate names; the observer and the author view list the same requirements.
        owed = report_requirements(snapshot, derivation, setup=False)
        first = owed[0] if owed else None
        if first is not None and first.stage in {"claim", "confirmation", "passes"}:
            return decide(state, "Block", reason=first.reason, parameters=first.parameters, affected=first.affected)
        current_digest = derivation_digest(snapshot, derivation)
        if current_digest != state.last_derivation_digest:
            budgets = dict(state.budgets)
            budgets["passes_used"] += 1
            state = replace(state, budgets=budgets, last_derivation_digest=current_digest)
        if first is not None and first.stage == "audit":
            return decide(state, "Block", reason=first.reason, parameters=first.parameters, affected=first.affected)
        deferred = [c for c, _ in states if c["id"] not in gating]
        if deferred:
            return end(replace(state, status="stopped_frozen"))
        return decide(replace(state, status="converged"))

    return _decision(snapshot, state, "Fault", reason="unsupported")
