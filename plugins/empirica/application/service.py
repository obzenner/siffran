"""The host-neutral Empirica application service (ADR-30, ADR-31).

:class:`EmpiricaService` is the orchestration layer between the ``empirica/v1`` wire contract and the
pure decision core. It is the first real caller of ``core.convergence.adjudicate``: it loads the run
from the injected persistence ports, assembles the neutral facts and pure verdicts the adjudicator
needs, and turns the returned :class:`~core.decisions.Decision` into a wire response — applying the
one policy the core deliberately omits, the ``max_passes`` cap (``core/convergence.py`` docstring).

It names no filesystem, Git, Claude, or Pi concept. Everything it touches is a port:

* :class:`~core.ports.RunRepository` — the operational-state document (status, pass counter, and the
  pointer to the current claim graph), CAS-guarded.
* :class:`~core.ports.ArtifactRepository` — the append-only knowledge plane (graphs, evidence, audit
  records).
* :class:`GenerationAllocator` — resolves which generation a StartRun opens. Active resumes in place;
  terminal/corrupt opens the next, clean generation. This is a separate port because the
  ``RunRepository`` protocol deliberately cannot enumerate generations (ADR-31 keys every operation
  on a full :class:`~core.records.RunKey`).

The graph-update transaction (``_update_graph``) is the load-bearing bit: **append the immutable
graph artifact first, then compare-and-set the pointer to it.** A losing CAS never advances the
pointer, so the pointer can only ever name a graph that was actually appended — a CAS conflict leaves
an orphan artifact (harmless, immutable) but never makes an orphan *current*.
"""
from __future__ import annotations

from typing import Protocol

from core import Allow, Block, Fault, Inert, Present, RunKey, RunState, adjudicate, claims
from core.records import ABSENT, Conflict, Corrupt

from . import knowledge, wire
from .state import DEFAULT_MAX_PASSES, DEFAULT_THETA, OperationalState

# Bound on the graph-pointer CAS retry loop. Each retry re-reads the run and re-attempts the swap;
# contention clears in one extra pass per concurrent winner, so this only trips on a live-lock.
_MAX_CAS_RETRIES = 32


class GenerationAllocator(Protocol):
    """Resolves the :class:`RunKey` a StartRun should open for a ``(project_id, run_id)``.

    Contract (ADR-31): return the *active* run's own key so it resumes in place, or the *next*
    (empty) generation's key when the latest is terminal or corrupt, so stale budgets and verdicts
    never become current and the old generation stays intact for rollback. The filesystem
    ``adapters.state.GenerationAllocator`` satisfies this by shape.
    """

    def allocate(self, project_id: str, run_id: str) -> RunKey: ...


class EmpiricaService:
    """Dispatches ``empirica/v1`` requests against the injected ports."""

    def __init__(self, runs, artifacts, allocator: GenerationAllocator, *,
                 theta: float = DEFAULT_THETA,
                 default_max_passes: int = DEFAULT_MAX_PASSES) -> None:
        self._runs = runs
        self._artifacts = artifacts
        self._allocator = allocator
        self._theta = theta
        self._default_max_passes = default_max_passes

    # --- dispatch ------------------------------------------------------------

    def handle(self, request: object) -> dict:
        """Validate a request envelope and dispatch it, always returning a well-formed response
        envelope (ADR-30). A malformed request becomes ``Fault(invalid_request)`` rather than an
        exception — a caller must be able to correlate every reply with its request id."""
        try:
            request_id, command = wire.parse_envelope(request)
        except wire.InvalidRequest as exc:
            rid = request.get("request_id") if isinstance(request, dict) else None
            return wire.envelope(rid if isinstance(rid, str) and rid else "unknown",
                                 wire.fault(wire.FAULT_INVALID_REQUEST, str(exc)))
        try:
            result = self._dispatch(command)
        except wire.InvalidRequest as exc:
            result = wire.fault(wire.FAULT_INVALID_REQUEST, str(exc))
        return wire.envelope(request_id, result)

    def _dispatch(self, command: dict) -> dict:
        ctype = command["type"]
        if ctype == wire.CMD_START_RUN:
            return self._start_run(command)
        if ctype == wire.CMD_GET_RUN:
            return self._get_run(command)
        if ctype == wire.CMD_OBSERVE_ACTION:
            return self._observe(command)
        if ctype == wire.CMD_EVALUATE_RUN:
            return self._evaluate(command)
        return wire.fault(wire.FAULT_UNSUPPORTED, f"unsupported command: {ctype}")

    # --- StartRun ------------------------------------------------------------

    def _start_run(self, command: dict) -> dict:
        selector = wire.require(command, "selector", dict)
        project = wire.require(selector, "project", str)
        session = wire.require(selector, "session", str)
        goal = wire.require(command, "goal", str)
        max_passes = command.get("max_passes", self._default_max_passes)
        if not isinstance(max_passes, int) or isinstance(max_passes, bool) or max_passes < 1:
            raise wire.InvalidRequest("max_passes must be a positive integer")
        max_spawns = command.get("max_spawns")
        if max_spawns is not None and (not isinstance(max_spawns, int)
                                       or isinstance(max_spawns, bool) or max_spawns < 0):
            raise wire.InvalidRequest("max_spawns must be a non-negative integer or null")
        modes = command.get("modes") or {}

        key = self._allocator.allocate(project, session)
        read = self._runs.read(key)
        if isinstance(read, Corrupt):
            return wire.fault(wire.FAULT_CORRUPT_RUN, read.reason)
        if isinstance(read, Present):
            # The allocator handed back an existing generation: resume it in place, never clobber
            # (ADR-31). A terminal generation here means the allocator chose not to bump; report it
            # as-is rather than overwrite a finished run's record.
            state = OperationalState.decode(read.value)
            if state is None:
                return wire.fault(wire.FAULT_CORRUPT_RUN, "existing run document is unreadable")
            return self._run_snapshot(key, state)

        state = OperationalState.new(goal=goal, max_passes=max_passes, max_spawns=max_spawns,
                                     theta=self._theta, modes=modes)
        try:
            self._runs.create(key, state.encode())
        except Conflict:
            # Lost a creation race: another session created this generation first. Re-read and
            # resume the winner's run rather than fail — the caller's intent (a run exists here) holds.
            read = self._runs.read(key)
            if isinstance(read, Present):
                winner = OperationalState.decode(read.value)
                if winner is not None:
                    return self._run_snapshot(key, winner)
            return wire.fault(wire.FAULT_CONFLICT, "run creation raced and the winner is unreadable")
        return self._run_snapshot(key, state)

    # --- GetRun --------------------------------------------------------------

    def _get_run(self, command: dict) -> dict:
        key = wire.decode_handle(wire.require(command, "run_id", str))
        read = self._runs.read(key)
        if read is ABSENT:
            return wire.inert("no_run")
        if isinstance(read, Corrupt):
            return wire.fault(wire.FAULT_CORRUPT_RUN, read.reason)
        state = OperationalState.decode(read.value)
        if state is None:
            return wire.fault(wire.FAULT_CORRUPT_RUN, "run document is unreadable")
        return self._run_snapshot(key, state)

    # --- ObserveAction -------------------------------------------------------

    def _observe(self, command: dict) -> dict:
        key = wire.decode_handle(wire.require(command, "run_id", str))
        action = wire.require(command, "action", dict)
        kind = wire.require(action, "kind", str)

        read = self._runs.read(key)
        if read is ABSENT:
            return wire.inert("no_run")
        if isinstance(read, Corrupt):
            return wire.fault(wire.FAULT_CORRUPT_RUN, read.reason)
        state = OperationalState.decode(read.value)
        if state is None:
            return wire.fault(wire.FAULT_CORRUPT_RUN, "run document is unreadable")
        if not state.is_active:
            # A finished run's knowledge is sealed; refuse to append onto it rather than let a late
            # observation rewrite a converged/stopped run's argument.
            return wire.fault(wire.FAULT_CONFLICT, f"run is {state.status}, not active")

        if kind == knowledge.KIND_GRAPH:
            return self._update_graph(key, action.get("graph"))
        if kind == knowledge.KIND_EVIDENCE:
            return self._observe_evidence(key, state, action)
        if kind == knowledge.KIND_AUDIT_TICKET:
            return self._append_and_ack(key, state,
                                        knowledge.audit_ticket_artifact(
                                            wire.require(action, "nonce", str)))
        if kind == knowledge.KIND_AUDIT_VERDICT:
            return self._append_and_ack(key, state,
                                        knowledge.audit_verdict_artifact(
                                            self._verdict_payload(action)))
        if kind == knowledge.KIND_ATTRIBUTION:
            return self._append_and_ack(key, state,
                                        knowledge.attribution_artifact(
                                            wire.require(action, "report", dict)))
        if kind == "route":
            return self._observe_route(key, read.revision, state, action)
        return wire.fault(wire.FAULT_UNSUPPORTED, f"unsupported action kind: {kind}")

    def _observe_evidence(self, key: RunKey, state: OperationalState, action: dict) -> dict:
        claim_id = wire.require(action, "claim_id", str)
        purpose = wire.require(action, "purpose", str)
        if purpose not in knowledge._PURPOSES:
            raise wire.InvalidRequest(f"purpose must be approve|refute, got {purpose!r}")
        ok = wire.require(action, "ok", bool)
        reason = action.get("reason", "")
        if not isinstance(reason, str):
            raise wire.InvalidRequest("reason must be a string")
        return self._append_and_ack(
            key, state, knowledge.evidence_artifact(claim_id, purpose, ok, reason))

    def _verdict_payload(self, action: dict) -> dict:
        """Extract the audit-verdict fields ``core.audit.coverage_check`` reads. Missing fields are
        rejected up front so a malformed verdict never lands as an artifact."""
        return {
            "verdict": wire.require(action, "verdict", str),
            "nonce": wire.require(action, "nonce", str),
            "argument_digest": action.get("argument_digest"),
            "claims_reviewed": action.get("claims_reviewed", []),
            "findings": action.get("findings", []),
        }

    def _append_and_ack(self, key: RunKey, state: OperationalState,
                         artifact_pair: tuple[str, str]) -> dict:
        """Append one immutable knowledge artifact and acknowledge with the (unchanged) run snapshot.

        Evidence and audit records do not touch the operational state — they are pure appends to the
        knowledge plane — so the run's revision is unchanged (ADR-31 append is unconditional)."""
        art_id, body = artifact_pair
        self._artifacts.append(key, knowledge_artifact(art_id, body))
        return self._run_snapshot(key, state)

    def _observe_route(self, key: RunKey, expected, state: OperationalState, action: dict) -> dict:
        """Record the P1 routing verdict on the operational state (a run-level commitment, not a
        claim). CAS'd like any state write."""
        verdict = wire.require(action, "verdict", str)
        reason = action.get("reason", "")
        if not isinstance(reason, str):
            raise wire.InvalidRequest("reason must be a string")
        new_state = state.evolve(route_verdict=(verdict, reason))
        persisted = self._cas(key, expected, new_state)
        if isinstance(persisted, dict):
            return persisted
        return self._run_snapshot(key, persisted)

    def _update_graph(self, key: RunKey, raw_graph: object) -> dict:
        """The graph-update transaction: validate/canonicalise, content-address, append the immutable
        artifact, then CAS the run pointer to it (ADR-31).

        Ordering is the invariant: the artifact is appended *before* the pointer moves, and the
        pointer moves only under CAS. So the pointer can only ever name a graph already in the store;
        a lost CAS re-reads and retries, leaving at worst an orphan artifact (immutable, harmless) —
        never an orphan made current.
        """
        try:
            canonical = knowledge.canonicalize_graph(raw_graph)
        except knowledge.InvalidGraph as exc:
            return wire.fault(wire.FAULT_INVALID_REQUEST, str(exc))

        art_id, body = knowledge.graph_artifact(canonical)
        self._artifacts.append(key, knowledge_artifact(art_id, body))  # append FIRST (idempotent)

        for _ in range(_MAX_CAS_RETRIES):
            read = self._runs.read(key)
            if read is ABSENT:
                return wire.fault(wire.FAULT_CONFLICT, "run disappeared during graph update")
            if isinstance(read, Corrupt):
                return wire.fault(wire.FAULT_CORRUPT_RUN, read.reason)
            state = OperationalState.decode(read.value)
            if state is None:
                return wire.fault(wire.FAULT_CORRUPT_RUN, "run document is unreadable")
            if not state.is_active:
                return wire.fault(wire.FAULT_CONFLICT, f"run is {state.status}, not active")
            if state.claim_graph_artifact_id == art_id:
                return self._run_snapshot(key, state)  # already current — idempotent no-op
            new_state = state.evolve(claim_graph_artifact_id=art_id)
            try:
                self._runs.compare_and_set(key, new_state.encode(), read.revision)
            except Conflict:
                continue  # a concurrent writer moved the pointer; re-read and retry (never orphan-current)
            return self._run_snapshot(key, new_state)
        return wire.fault(wire.FAULT_CONFLICT, "graph pointer update did not converge")

    # --- EvaluateRun ---------------------------------------------------------

    def _evaluate(self, command: dict) -> dict:
        key = wire.decode_handle(wire.require(command, "run_id", str))
        intent = wire.require(command, "intent", str)
        if intent not in wire._INTENTS:
            raise wire.InvalidRequest(f"unknown intent: {intent!r}")

        read = self._runs.read(key)
        if read is ABSENT:
            return wire.inert("no_run")
        if isinstance(read, Corrupt):
            return wire.fault(wire.FAULT_CORRUPT_RUN, read.reason)
        state = OperationalState.decode(read.value)
        if state is None:
            return wire.fault(wire.FAULT_CORRUPT_RUN, "run document is unreadable")
        if not state.is_active:
            # Already finished: report it, never re-judge (mirrors adjudicate's "finished" branch).
            return self._run_snapshot(key, state)

        # Assemble the neutral facts and pure verdicts, then adjudicate.
        try:
            graph, graph_fault = self._load_graph(key, state)
        except knowledge.KnowledgeError as exc:
            return wire.fault(wire.FAULT_CORRUPT_ARTIFACTS, str(exc))
        if graph_fault is not None:
            return graph_fault

        know = self._knowledge  # set by _load_graph
        evidence = knowledge.build_evidence_oracle(know.evidence)
        audit = knowledge.build_audit_oracle(know.tickets, know.verdicts)
        approving = knowledge.approving_evidence_ids(know.evidence)
        digest_of = knowledge.build_digest_of(graph, approving) if isinstance(graph, dict) else None
        attribution = know.attributions[-1] if know.attributions else None

        run_state = RunState(status="active", is_legacy=state.is_legacy,
                             frozen_claims=state.frozen_claims)
        decision = adjudicate(run=run_state, graph=graph, theta=state.theta,
                             evidence=evidence, audit=audit,
                             route_verdict=state.route_verdict,
                             digest_of=digest_of, attribution=attribution)
        return self._finalize(key, read, state, decision, advisory=(intent == wire.INTENT_CONTINUE))

    def _load_graph(self, key: RunKey, state: OperationalState):
        """Load the graph the run's pointer names, plus the whole knowledge set (cached on self).

        Returns ``(graph, fault_or_None)``. ``graph`` is the canonical graph dict, ``claims.CORRUPT``
        for a structurally broken stored graph, or ``None`` when the run has no pointer yet (an
        active run with no argument — the adjudicator faults closed). A pointer that names an
        artifact absent from the store is a *stale pointer* and faults closed here.
        """
        arts = self._artifacts.read(key)
        if isinstance(arts, Corrupt):
            self._knowledge = knowledge.Knowledge()
            return None, wire.fault(wire.FAULT_CORRUPT_ARTIFACTS, arts.reason)
        artifacts = arts.value if isinstance(arts, Present) else frozenset()
        self._knowledge = knowledge.Knowledge.from_artifacts(artifacts)

        pointer = state.claim_graph_artifact_id
        if pointer is None:
            return None, None  # no argument yet -> adjudicate returns Fault (missing graph)
        if pointer not in self._knowledge.graphs:
            return None, wire.fault(
                wire.FAULT_CORRUPT_ARTIFACTS,
                "run pointer references a graph artifact absent from the store (stale pointer)")
        graph = self._knowledge.graphs[pointer]
        return (graph if isinstance(graph, dict) else claims.CORRUPT), None

    def _finalize(self, key: RunKey, read: Present, state: OperationalState,
                  decision, *, advisory: bool) -> dict:
        """Turn a core :class:`Decision` into a wire result, applying the ``max_passes`` cap and
        persisting the resulting status transition (the one policy the core omits).

        ``advisory`` (intent="continue") means the caller is asking "should I keep going?", not
        trying to stop: the decision is reported against the live run but **no state is written and
        no pass is counted** — only a stop attempt (report_convergence|stop) moves the run.
        """
        if isinstance(decision, Inert):  # unreachable for an active run; report defensively
            return wire.inert("no_run")
        if isinstance(decision, Fault):
            return self._fault_from_reason(decision.reason)
        if isinstance(decision, Block):
            return self._finalize_block(key, read, state, decision, advisory)
        # Allow: the core blesses a stop (converged, residual, refuted, or frozen).
        if advisory:
            return wire.allow(decision.converged, self._decision_run(key, state, decision))
        new_state = state.evolve(status=decision.status)
        persisted = self._cas(key, read.revision, new_state)
        if isinstance(persisted, dict):
            return persisted
        return wire.allow(decision.converged, self._decision_run(key, persisted, decision))

    def _finalize_block(self, key: RunKey, read: Present, state: OperationalState,
                        decision: Block, advisory: bool) -> dict:
        """A block. Advisory reads report it untouched. A real stop attempt records a pass; when the
        pass budget is exhausted the block becomes a non-converged ``stopped_budget`` stop (ADR-31 /
        the ADR-30 note that the *adapter* applies the cap), otherwise the run stays active."""
        if advisory:
            return wire.block(decision.reason, self._run_view(key, state))
        new_passes = state.passes + 1
        if new_passes >= state.max_passes:
            new_state = state.evolve(passes=new_passes, status=wire.STATUS_STOPPED_BUDGET)
            persisted = self._cas(key, read.revision, new_state)
            if isinstance(persisted, dict):
                return persisted
            note = (f"NON-CONVERGED: reached max_passes={state.max_passes} without convergence "
                    f"({decision.reason})")
            return wire.allow(False, self._run_view(key, persisted, note=note))
        new_state = state.evolve(passes=new_passes)
        persisted = self._cas(key, read.revision, new_state)
        if isinstance(persisted, dict):
            return persisted
        return wire.block(decision.reason, self._run_view(key, persisted))

    # --- persistence + views -------------------------------------------------

    def _cas(self, key: RunKey, expected, new_state: OperationalState):
        """Persist a state transition under CAS against the storage revision ``expected`` read
        alongside the previous state. Returns the persisted :class:`OperationalState` on success, or
        a ``Fault(conflict)`` result dict if a concurrent write advanced the run — an evaluation
        racing another must not silently clobber the winner's transition."""
        try:
            self._runs.compare_and_set(key, new_state.encode(), expected)
        except Conflict:
            return wire.fault(wire.FAULT_CONFLICT,
                              "run changed during the operation; re-read and retry")
        return new_state

    def _run_snapshot(self, key: RunKey, state: OperationalState) -> dict:
        """A read-only acknowledgement: an Allow carrying the run's current status. Used by StartRun/
        GetRun/ObserveAction, where there is no stop decision to make — just the run's state."""
        return wire.allow(state.status == wire.STATUS_CONVERGED, self._run_view(key, state))

    def _run_view(self, key: RunKey, state: OperationalState, note: str | None = None) -> dict:
        return wire.run_obj(wire.encode_handle(key), state.status, state.revision, note=note)

    def _decision_run(self, key: RunKey, state: OperationalState, decision: Allow) -> dict:
        """The run view for a finalised Allow, carrying the adjudicator's advisory reporting fields
        (note, deferred, blocked, audit, P1) so a caller sees *why* a stop is or is not convergence."""
        return wire.run_obj(
            wire.encode_handle(key), state.status, state.revision,
            note=decision.note, deferred=list(decision.deferred) or None,
            blocked=list(decision.blocked) or None,
            budget_blocked=list(decision.budget_blocked) or None,
            audit=decision.audit, p1_violation=decision.p1_violation,
            p1_unverified=decision.p1_unverified,
            root_refuted=decision.root_refuted or None,
            attribution=decision.attribution)

    def _fault_from_reason(self, reason: str) -> dict:
        """Map an adjudicator ``Fault`` reason onto a wire fault code. A graph-related fault is a
        corrupt/missing argument (``corrupt_artifacts``); anything else is a corrupt run record. Both
        fail closed — the core faulted because it could not trust the state enough to allow a stop."""
        code = wire.FAULT_CORRUPT_ARTIFACTS if "graph" in reason else wire.FAULT_CORRUPT_RUN
        return wire.fault(code, reason)


def knowledge_artifact(artifact_id: str, body: str):
    """Build the domain :class:`~core.records.Artifact` for a knowledge record. Kept here (not in
    ``knowledge``) so that module stays a pure encoder with no dependency on the record type."""
    from core.records import Artifact

    return Artifact(artifact_id, body)
