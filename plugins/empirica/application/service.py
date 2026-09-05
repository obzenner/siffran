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

import hashlib
from typing import Protocol

from core import Allow, Block, Fault, Inert, Present, RunKey, RunState, adjudicate, claims
from core.records import ABSENT, Conflict, Corrupt

from . import actors, knowledge, wire
from .state import DEFAULT_MAX_PASSES, DEFAULT_THETA, MODES, PHASES, OperationalState

# Bound on the graph-pointer CAS retry loop. Each retry re-reads the run and re-attempts the swap;
# contention clears in one extra pass per concurrent winner, so this only trips on a live-lock.
_MAX_CAS_RETRIES = 32

# Internal sentinel: a finalize CAS lost its race and the caller must re-read and retry. Never
# escapes the service — the EvaluateRun retry loop consumes it (ADR-31 CAS retries).
_RETRY = object()


class GenerationAllocator(Protocol):
    """Resolves run generations for lifecycle start and read-only host lookup.

    ``resolve`` returns the latest existing generation without allocating one. ``allocate`` returns
    the active generation or the next clean generation for StartRun.
    """

    def resolve(self, project_id: str, run_id: str) -> RunKey | None: ...

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
        if ctype == wire.CMD_RESOLVE_RUN:
            return self._resolve_run(command)
        if ctype == wire.CMD_GET_RUN:
            return self._get_run(command)
        if ctype == wire.CMD_RESTORE_RUN:
            return self._restore_run(command)
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

    # --- ResolveRun ----------------------------------------------------------

    def _resolve_run(self, command: dict) -> dict:
        """Resolve a host selector to the latest run without creating a generation.

        This is the only lookup lifecycle adapters need between StartRun and later events.  Keeping
        it in the application boundary prevents Claude hooks from inspecting the global state
        layout, and unlike StartRun it can never relaunch a terminal run.
        """
        selector = wire.require(command, "selector", dict)
        project = wire.require(selector, "project", str)
        session = wire.require(selector, "session", str)
        key = self._allocator.resolve(project, session)
        if key is None:
            return wire.inert("no_run")
        read = self._runs.read(key)
        if read is ABSENT:
            return wire.inert("no_run")
        if isinstance(read, Corrupt):
            return wire.fault(wire.FAULT_CORRUPT_RUN, read.reason)
        state = OperationalState.decode(read.value)
        if state is None:
            return wire.fault(wire.FAULT_CORRUPT_RUN, "run document is unreadable")
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

    # --- RestoreRun ----------------------------------------------------------

    def _restore_run(self, command: dict) -> dict:
        """Return an enriched, read-only snapshot of the whole operational plane so a host can rebuild
        its in-session view after a compaction or a resume without touching any side file (ADR-31;
        the host-neutral successor to the legacy ``state_restore`` re-injection). Writes nothing.

        Fail directions match the reads it composes: an absent run is Inert(no_run), a corrupt run
        document faults closed, and a corrupt knowledge plane faults closed on the graph resume view
        rather than reporting a run that cannot be trusted as restorable."""
        key = wire.decode_handle(wire.require(command, "run_id", str))
        read = self._runs.read(key)
        if read is ABSENT:
            return wire.inert("no_run")
        if isinstance(read, Corrupt):
            return wire.fault(wire.FAULT_CORRUPT_RUN, read.reason)
        state = OperationalState.decode(read.value)
        if state is None:
            return wire.fault(wire.FAULT_CORRUPT_RUN, "run document is unreadable")

        snapshot: dict = {
            "phase": state.phase,
            "passes": state.passes,
            "max_passes": state.max_passes,
            "theta": state.theta,
            "spawn": self._spawn_view(state),
            "modes": dict(state.modes),
            "route": self._route_view(state),
            "audit_tickets": [dict(t) for t in state.audit_tickets],
            "dispatches": [dict(d) for d in state.dispatches],
            "has_graph": state.claim_graph_artifact_id is not None,
        }
        if state.frozen_claims is not None:
            snapshot["frozen_claims"] = list(state.frozen_claims)

        # A derived claim-graph resume view, when the run has an argument and it (and the knowledge
        # plane) are readable. A corrupt knowledge plane faults closed; a merely-absent pointer just
        # omits the graph view. This never re-adjudicates — it reports, it does not stop the run.
        if state.claim_graph_artifact_id is not None:
            graph_view = self._restore_graph_view(key, state)
            if isinstance(graph_view, dict) and graph_view.get("__fault__"):
                return graph_view["__fault__"]
            if graph_view is not None:
                snapshot["graph"] = graph_view
        return wire.allow(state.status == wire.STATUS_CONVERGED,
                          self._run_view(key, state, snapshot=snapshot))

    def _restore_graph_view(self, key: RunKey, state: OperationalState):
        """The gating/open/blocked/deferred counts for the run's current graph, or a fault wrapper on
        a corrupt knowledge plane / stale pointer. ``None`` when the graph is not yet readable."""
        try:
            graph, graph_fault = self._load_graph(key, state)
        except knowledge.KnowledgeError as exc:
            return {"__fault__": wire.fault(wire.FAULT_CORRUPT_ARTIFACTS, str(exc))}
        if graph_fault is not None:
            return {"__fault__": graph_fault}
        if not isinstance(graph, dict):
            return None
        ev = knowledge.build_evidence_oracle(self._knowledge.evidence,
                                             self._knowledge.evidence_leaves)

        def ev_ok(nid, purpose):
            return ev(nid, purpose)[0]
        gating = claims.gating_goals(graph, state.theta, ev_ok)
        open_claims = claims.pending(graph, state.theta, ev_ok)
        blocked = claims.blocked_residuals(graph, state.theta, ev_ok)
        deferred = ([nid for nid in gating if nid not in set(state.frozen_claims)]
                    if state.frozen_claims is not None else [])
        return {"gating": len(gating), "open": len(open_claims), "blocked": len(blocked),
                "deferred": len(deferred)}

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
            # A finished run's argument is sealed. An action that would edit that argument or add a
            # lifecycle commitment is refused (fail closed) so a late write cannot rewrite a
            # converged/stopped run. But an action UNRELATED to the argument — a spawn-budget gate —
            # fails OPEN: there is nothing left to gate, so refusing it would only wedge unrelated
            # work (mirrors the legacy spawn/dispatch gates returning 0 on an inactive run).
            if kind in wire._TERMINAL_FAIL_OPEN_KINDS:
                return wire.allow(state.status == wire.STATUS_CONVERGED,
                                  self._run_view(key, state,
                                                 spawn=self._spawn_view(state, reserved=True,
                                                     note=f"run is {state.status}; budget not "
                                                          "enforced on a finished run")))
            return wire.fault(wire.FAULT_CONFLICT, f"run is {state.status}, not active")

        # --- knowledge-plane appends (immutable argument; revision unchanged) ---
        if kind == knowledge.KIND_GRAPH:
            return self._update_graph(key, action.get("graph"))
        if kind == knowledge.KIND_EVIDENCE:
            return self._observe_evidence(key, state, action)
        if kind == knowledge.KIND_EVIDENCE_LEAF:
            evidence_id = wire.require(action, "evidence_id", str)
            statement = wire.require(action, "statement", dict)
            verdicts = wire.require(action, "verdicts", dict)
            # Host adapters are the trusted observation boundary: raw model-facing callers must
            # never invent verdicts.  Still validate the boundary structurally so malformed data
            # cannot become an approving truthy value in the knowledge oracle.
            for purpose in ("approve", "refute"):
                verdict = verdicts.get(purpose)
                if (not isinstance(verdict, dict)
                        or not isinstance(verdict.get("ok"), bool)
                        or not isinstance(verdict.get("reason"), str)):
                    raise wire.InvalidRequest(
                        f"verdicts.{purpose} must contain boolean ok and string reason")
            supersedes = action.get("supersedes")
            if supersedes is not None and not isinstance(supersedes, str):
                raise wire.InvalidRequest("supersedes must be an artifact id or null")
            return self._append_and_ack(
                key, state,
                knowledge.evidence_leaf_artifact(evidence_id, statement, verdicts, supersedes))
        if kind == knowledge.KIND_AUDIT_VERDICT:
            return self._append_and_ack(key, state,
                                        knowledge.audit_verdict_artifact(
                                            self._verdict_payload(action)))
        if kind == knowledge.KIND_ATTRIBUTION:
            return self._append_and_ack(key, state,
                                        knowledge.attribution_artifact(
                                            wire.require(action, "report", dict)))

        # --- operational-plane mutations (CAS-guarded state writes) ---
        if kind == wire.KIND_RESERVE_SPAWN:
            return self._reserve_spawn(key)
        if kind == wire.KIND_CONFIGURE_BUDGET:
            return self._configure_budget(key, action)
        if kind == wire.KIND_PHASE:
            return self._transition_phase(key, action)
        if kind == wire.KIND_MODE:
            return self._set_modes(key, action)
        if kind == wire.KIND_FREEZE:
            return self._freeze(key, action)
        if kind == wire.KIND_ROUTE:
            return self._stamp_route(key, action)
        if kind == wire.KIND_INVESTIGATE:
            return self._stamp_investigation(key)
        if kind == wire.KIND_DISPATCH:
            return self._record_dispatch(key, action)
        if kind == knowledge.KIND_AUDIT_TICKET:
            return self._issue_audit_ticket(key, action)
        if kind == wire.KIND_CONSUME_AUDIT_TICKET:
            return self._consume_audit_ticket(key, action)
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
        """Extract the audit-verdict fields ``core.audit.coverage_check`` reads. Missing/mis-typed
        fields are rejected up front so a malformed verdict never lands as an artifact; per-entry
        shape is re-validated when the oracle reads it back (``knowledge._normalise_verdict``), so a
        hand-crafted stored artifact still fails closed rather than crashing the wire boundary."""
        reviewed = action.get("claims_reviewed", [])
        findings = action.get("findings", [])
        if not isinstance(reviewed, list):
            raise wire.InvalidRequest("claims_reviewed must be a list")
        if not isinstance(findings, list):
            raise wire.InvalidRequest("findings must be a list")
        return {
            "verdict": wire.require(action, "verdict", str),
            "nonce": wire.require(action, "nonce", str),
            "argument_digest": action.get("argument_digest"),
            "claims_reviewed": reviewed,
            "findings": findings,
        }

    def _append_and_ack(self, key: RunKey, state: OperationalState,
                         artifact_pair: tuple[str, str]) -> dict:
        """Append one immutable knowledge artifact and acknowledge with the (unchanged) run snapshot.

        Evidence and audit records do not touch the operational state — they are pure appends to the
        knowledge plane — so the run's revision is unchanged (ADR-31 append is unconditional)."""
        art_id, body = artifact_pair
        self._artifacts.append(key, knowledge_artifact(art_id, body))
        return self._run_snapshot(key, state)

    # --- operational-plane operations (CAS-guarded) --------------------------

    def _commit(self, key: RunKey, mutate):
        """CAS-retry a pure operational-state mutation. ``mutate(state)`` returns either a finished
        result dict (a no-op Allow, a Block, or a Fault — nothing is written) or a new
        :class:`OperationalState` to persist. Reads the run fresh each attempt, requires it still
        active, applies ``mutate``, and compare-and-sets against the revision it just read; a losing
        CAS re-reads and retries so a concurrent writer never clobbers the winner (ADR-31).

        A corrupt/absent/terminal read appearing mid-operation fails closed — the same fail-closed
        direction the initial dispatch check took.
        """
        for _ in range(_MAX_CAS_RETRIES):
            read = self._runs.read(key)
            if read is ABSENT:
                return wire.fault(wire.FAULT_CONFLICT, "run disappeared during the operation")
            if isinstance(read, Corrupt):
                return wire.fault(wire.FAULT_CORRUPT_RUN, read.reason)
            state = OperationalState.decode(read.value)
            if state is None:
                return wire.fault(wire.FAULT_CORRUPT_RUN, "run document is unreadable")
            if not state.is_active:
                return wire.fault(wire.FAULT_CONFLICT, f"run is {state.status}, not active")
            outcome = mutate(state)
            if isinstance(outcome, dict):
                return outcome  # a finished result: nothing to persist
            try:
                self._runs.compare_and_set(key, outcome.encode(), read.revision)
            except Conflict:
                continue  # a concurrent writer advanced the run; re-read and retry
            return outcome
        return wire.fault(wire.FAULT_CONFLICT, "operation did not converge under contention")

    def _reserve_spawn(self, key: RunKey) -> dict:
        """Atomically reserve ONE spawn against the cap (ADR-17). Unbounded → allow and count nothing.
        Cap reached → Block, count unchanged (a denied spawn did not happen). Otherwise increment the
        counter under CAS — the ground-truth reservation a host gate enforces on."""
        def mutate(state: OperationalState):
            if state.max_spawns is None:
                return wire.allow(False, self._run_view(
                    key, state, spawn=self._spawn_view(state, reserved=True,
                                                       note="unbounded budget")))
            if not state.can_reserve_spawn():
                return wire.block(
                    f"spawn budget exhausted: {state.spawns}/{state.max_spawns} used; the spawn is "
                    f"DENIED (ADR-17). Resolve remaining unknowns without spawning, tag them "
                    f"blocked=needs-budget, or raise max_spawns.",
                    self._run_view(key, state, spawn=self._spawn_view(state, reserved=False)))
            return state.evolve(spawns=state.spawns + 1)
        result = self._commit(key, mutate)
        if isinstance(result, dict):
            return result
        return wire.allow(False, self._run_view(
            key, result, spawn=self._spawn_view(result, reserved=True)))

    def _configure_budget(self, key: RunKey, action: dict) -> dict:
        """Set (or clear) the spawn cap (ADR-17). ``max_spawns`` is a non-negative int or null
        (unbounded). Under the same CAS as any state write, so an operator's cap change cannot
        clobber a concurrent reservation."""
        if "max_spawns" not in action:
            raise wire.InvalidRequest("missing field: max_spawns")
        cap = action["max_spawns"]
        if cap is not None and (not isinstance(cap, int) or isinstance(cap, bool) or cap < 0):
            raise wire.InvalidRequest("max_spawns must be a non-negative integer or null")

        def mutate(state: OperationalState):
            if state.max_spawns == cap:  # already at this cap — no-op, do not churn the revision
                return wire.allow(False, self._run_view(key, state, spawn=self._spawn_view(state)))
            return state.evolve(max_spawns=cap)
        result = self._commit(key, mutate)
        if isinstance(result, dict):
            return result
        return wire.allow(False, self._run_view(key, result, spawn=self._spawn_view(result)))

    def _transition_phase(self, key: RunKey, action: dict) -> dict:
        """Advance the run's phase (ADR-21 M1). Monotone: a transition may hold or move forward,
        never regress — an illegal transition is refused as a conflict rather than silently applied."""
        phase = wire.require(action, "phase", str)
        if phase not in PHASES:
            raise wire.InvalidRequest(f"unknown phase: {phase!r}; valid phases are {list(PHASES)}")

        def mutate(state: OperationalState):
            if phase == state.phase:
                return wire.allow(False, self._run_view(key, state, phase=state.phase))
            if not state.can_advance_to(phase):
                return wire.fault(
                    wire.FAULT_CONFLICT,
                    f"illegal phase transition {state.phase!r} → {phase!r}: the phase machine is "
                    f"monotone (route → resolve → assess → audit → converged) and may not regress")
            return state.evolve(phase=phase)
        result = self._commit(key, mutate)
        if isinstance(result, dict):
            return result
        return wire.allow(False, self._run_view(key, result, phase=result.phase))

    def _set_modes(self, key: RunKey, action: dict) -> dict:
        """Merge run mode flags (ADR-24/28). A closed vocabulary: an unknown mode key or a non-bool
        value is refused, so a typo cannot look like it enabled a mode. Merges into the existing
        modes so setting one does not silently clear the other."""
        flags = wire.require(action, "modes", dict)
        unknown = sorted(k for k in flags if k not in MODES)
        if unknown:
            raise wire.InvalidRequest(
                f"unknown mode(s) {unknown}; valid modes are {list(MODES)}")
        if any(not isinstance(v, bool) for v in flags.values()):
            raise wire.InvalidRequest("mode values must be booleans")

        def mutate(state: OperationalState):
            merged = {**state.modes, **flags}
            if merged == state.modes:  # nothing changed — no-op, do not churn the revision
                return wire.allow(False, self._run_view(key, state, modes=dict(state.modes)))
            return state.evolve(modes=merged)
        result = self._commit(key, mutate)
        if isinstance(result, dict):
            return result
        return wire.allow(False, self._run_view(key, result, modes=dict(result.modes)))

    def _freeze(self, key: RunKey, action: dict) -> dict:
        """Commit the run's scope (ADR-26): the claims it will discharge. FIRST WRITE WINS — a run
        cannot re-freeze to enlarge (or shrink) a commitment it already made. An empty scope is
        allowed and yields a run that discharges nothing and defers everything (visibly vacuous,
        not illegal)."""
        raw = wire.require(action, "claims", list)
        if any(not isinstance(c, str) for c in raw):
            raise wire.InvalidRequest("claims must be a list of claim-id strings")
        committed = tuple(sorted({c for c in raw if c}))

        def mutate(state: OperationalState):
            if state.frozen_claims is not None:
                return wire.allow(False, self._run_view(
                    key, state, frozen_claims=list(state.frozen_claims),
                    note="already frozen (first write wins)"))
            seq = state.stamp_seq + 1
            return state.evolve(frozen_claims=committed, freeze_seq=seq, stamp_seq=seq)
        result = self._commit(key, mutate)
        if isinstance(result, dict):
            return result
        return wire.allow(False, self._run_view(
            key, result, frozen_claims=list(result.frozen_claims)))

    def _stamp_route(self, key: RunKey, action: dict) -> dict:
        """Record that the run announced its route (ADR-20 P1). FIRST WRITE WINS, and the position is
        taken from this document's own monotone write counter — so a late re-announcement cannot
        backdate the commitment, and the ordering against the first investigative action is a total
        order no caller can forge by choosing a stamp format."""
        reason = action.get("reason", "")
        if not isinstance(reason, str):
            raise wire.InvalidRequest("reason must be a string")

        def mutate(state: OperationalState):
            if state.route_seq is not None:
                return wire.allow(False, self._run_view(
                    key, state, route=self._route_view(state),
                    note="route already announced (first write wins)"))
            seq = state.stamp_seq + 1
            return state.evolve(route_seq=seq, stamp_seq=seq, route_reason=reason)
        result = self._commit(key, mutate)
        if isinstance(result, dict):
            return result
        return wire.allow(False, self._run_view(key, result, route=self._route_view(result)))

    def _stamp_investigation(self, key: RunKey) -> dict:
        """Record the FIRST investigative action (ADR-20 P1). FIRST WRITE WINS: the stamp marks the
        genuine start of evidence-gathering and cannot be pushed later, so a run that investigated
        before announcing its route cannot hide it."""
        def mutate(state: OperationalState):
            if state.first_investigation_seq is not None:
                return wire.allow(False, self._run_view(
                    key, state, route=self._route_view(state),
                    note="first investigation already recorded (first write wins)"))
            seq = state.stamp_seq + 1
            return state.evolve(first_investigation_seq=seq, stamp_seq=seq)
        result = self._commit(key, mutate)
        if isinstance(result, dict):
            return result
        return wire.allow(False, self._run_view(key, result, route=self._route_view(result)))

    def _record_dispatch(self, key: RunKey, action: dict) -> dict:
        """Record who an actor dispatch resolved to and how well we know it (ADR-24). Attribution is
        written by the DISPATCHER, never the actor: a ``witnessed`` claim is honoured only when the
        caller marks the dispatch witnessed (a CLI-exec dispatch it invoked itself); an in-session
        spawn is forced to ``declared`` because it cannot observe the resolved model."""
        raw_actor = wire.require(action, "actor", dict)
        witnessed = action.get("witnessed", False)
        if not isinstance(witnessed, bool):
            raise wire.InvalidRequest("witnessed must be a boolean")
        strength = actors.WITNESSED if witnessed else actors.DECLARED
        actor = actors.normalise(raw_actor, attribution=strength, force_attribution=True)
        if actor is None:
            raise wire.InvalidRequest(
                "actor is not a valid actor record (needs a well-formed, non-policy-excluded model)")
        claim_id = action.get("claim_id")
        if claim_id is not None and not isinstance(claim_id, str):
            raise wire.InvalidRequest("claim_id must be a string or null")

        def mutate(state: OperationalState):
            seq = state.stamp_seq + 1
            record = {"seq": seq, "actor": actor}
            if claim_id:
                record["claim_id"] = claim_id
            return state.evolve(dispatches=(*state.dispatches, record), stamp_seq=seq)
        result = self._commit(key, mutate)
        if isinstance(result, dict):
            return result
        return wire.allow(False, self._run_view(
            key, result, dispatch=dict(result.dispatches[-1])))

    def _issue_audit_ticket(self, key: RunKey, action: dict) -> dict:
        """Issue a fresh audit ticket (ADR-20 P6): mint a per-spawn nonce and record it on the
        operational plane (ADR-31 puts tickets there, not in the knowledge plane). The nonce is
        DERIVED from the run key and the ticket's ordinal — deterministic (no clock, no randomness)
        and computable by anything that can read the run, because its job is BINDING a verdict to a
        recorded spawn, not secrecy. An optional ``actor`` records dispatch attribution (declared
        for an in-session spawn, witnessed only when the caller marks it so)."""
        raw_actor = action.get("actor")
        witnessed = action.get("witnessed", False)
        if not isinstance(witnessed, bool):
            raise wire.InvalidRequest("witnessed must be a boolean")
        actor = None
        if raw_actor is not None:
            strength = actors.WITNESSED if witnessed else actors.DECLARED
            actor = actors.normalise(raw_actor, attribution=strength, force_attribution=True)
            if actor is None:
                raise wire.InvalidRequest("actor is not a valid actor record")

        def mutate(state: OperationalState):
            seq = len(state.audit_tickets) + 1
            ticket = {"nonce": _mint_nonce(key, seq), "seq": seq, "consumed": False}
            if actor is not None:
                ticket["actor"] = actor
            return state.evolve(audit_tickets=(*state.audit_tickets, ticket))
        result = self._commit(key, mutate)
        if isinstance(result, dict):
            return result
        issued = result.audit_tickets[-1]
        return wire.allow(False, self._run_view(
            key, result, ticket={"nonce": issued["nonce"], "seq": issued["seq"]}))

    def _consume_audit_ticket(self, key: RunKey, action: dict) -> dict:
        """Mark an issued ticket consumed (ADR-20 P6). Idempotent: consuming an already-consumed
        ticket is a no-op. An unknown nonce is a caller error, not a race — rejected as
        invalid_request. Consumption is bookkeeping; the audit coverage decision still binds on the
        issued nonce, so this never changes whether a run may converge."""
        nonce = wire.require(action, "nonce", str)

        def mutate(state: OperationalState):
            if not any(t["nonce"] == nonce for t in state.audit_tickets):
                return wire.fault(wire.FAULT_INVALID_REQUEST,
                                  "no audit ticket matches that nonce")
            if all(t.get("consumed") for t in state.audit_tickets if t["nonce"] == nonce):
                return wire.allow(False, self._run_view(
                    key, state, note="ticket already consumed"))
            updated = tuple({**t, "consumed": True} if t["nonce"] == nonce else t
                            for t in state.audit_tickets)
            return state.evolve(audit_tickets=updated)
        result = self._commit(key, mutate)
        if isinstance(result, dict):
            return result
        return wire.allow(False, self._run_view(key, result))

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
        """Adjudicate the run, retrying the whole read → load → adjudicate → finalize transaction on a
        CAS conflict (ADR-31: the application uses RunRepository CAS retries and owns the max_passes
        policy). A losing finalize re-reads: if a concurrent winner terminalized the run it is
        reported (never re-judged), otherwise the decision is recomputed against the fresh state."""
        key = wire.decode_handle(wire.require(command, "run_id", str))
        intent = wire.require(command, "intent", str)
        if intent not in wire._INTENTS:
            raise wire.InvalidRequest(f"unknown intent: {intent!r}")
        for _ in range(_MAX_CAS_RETRIES):
            outcome = self._evaluate_once(key, advisory=(intent == wire.INTENT_CONTINUE))
            if outcome is not _RETRY:
                return outcome
        return wire.fault(wire.FAULT_CONFLICT, "evaluation did not converge under contention")

    def _evaluate_once(self, key: RunKey, *, advisory: bool):
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
        evidence = knowledge.build_evidence_oracle(know.evidence, know.evidence_leaves)
        # Audit tickets live on the OPERATIONAL plane (ADR-31), so coverage is judged against the
        # server-issued spawn nonces recorded there; only the verdict itself is a knowledge artifact.
        audit = knowledge.build_audit_oracle(list(state.audit_tickets), know.verdicts)
        approving = knowledge.approving_evidence_ids(know.evidence)
        digest_of = (knowledge.build_digest_of(graph, approving, know.evidence_leaves)
                     if isinstance(graph, dict) else None)
        attribution = know.attributions[-1] if know.attributions else None

        run_state = RunState(status="active", is_legacy=state.is_legacy,
                             frozen_claims=state.frozen_claims)
        decision = adjudicate(run=run_state, graph=graph, theta=state.theta,
                             evidence=evidence, audit=audit,
                             route_verdict=state.route_p1_verdict(),
                             digest_of=digest_of, attribution=attribution)
        return self._finalize(key, read, state, decision, advisory=advisory)

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
        if persisted is _RETRY:
            return _RETRY
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
            if persisted is _RETRY:
                return _RETRY
            note = (f"NON-CONVERGED: reached max_passes={state.max_passes} without convergence "
                    f"({decision.reason})")
            return wire.allow(False, self._run_view(key, persisted, note=note))
        new_state = state.evolve(passes=new_passes)
        persisted = self._cas(key, read.revision, new_state)
        if persisted is _RETRY:
            return _RETRY
        return wire.block(decision.reason, self._run_view(key, persisted))

    # --- persistence + views -------------------------------------------------

    def _cas(self, key: RunKey, expected, new_state: OperationalState):
        """Persist a state transition under CAS against the storage revision ``expected`` read
        alongside the previous state. Returns the persisted :class:`OperationalState` on success, or
        the ``_RETRY`` sentinel if a concurrent write advanced the run — the EvaluateRun loop then
        re-reads and re-adjudicates rather than clobber the winner's transition (ADR-31 CAS)."""
        try:
            self._runs.compare_and_set(key, new_state.encode(), expected)
        except Conflict:
            return _RETRY
        return new_state

    def _run_snapshot(self, key: RunKey, state: OperationalState) -> dict:
        """A read-only acknowledgement: an Allow carrying the run's current status. Used by StartRun/
        GetRun/ObserveAction, where there is no stop decision to make — just the run's state."""
        return wire.allow(state.status == wire.STATUS_CONVERGED, self._run_view(key, state))

    def _run_view(self, key: RunKey, state: OperationalState, note: str | None = None,
                  **extra: object) -> dict:
        return wire.run_obj(wire.encode_handle(key), state.status, state.revision, note=note,
                            **extra)

    # --- operation view fragments --------------------------------------------

    def _spawn_view(self, state: OperationalState, *, reserved: bool | None = None,
                    note: str | None = None) -> dict:
        """The spawn-budget fragment for a reserve/configure acknowledgement. ``remaining`` is null
        when unbounded (JSON has no infinity), which the wire distinguishes from a numeric cap."""
        view: dict = {"spawns": state.spawns, "max_spawns": state.max_spawns,
                      "remaining": None if state.max_spawns is None
                      else int(state.spawns_remaining)}
        if reserved is not None:
            view["reserved"] = reserved
        if note:
            view["note"] = note
        return view

    def _route_view(self, state: OperationalState) -> dict:
        """The P1 ordering fragment: the write-order witnesses and the derived verdict."""
        verdict, reason = state.route_p1_verdict()
        return {"route_seq": state.route_seq,
                "first_investigation_seq": state.first_investigation_seq,
                "verdict": verdict, "reason": reason}

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


def _mint_nonce(key: RunKey, seq: int) -> str:
    """A per-spawn audit nonce, DERIVED from the run key and the ticket's ordinal. Deterministic (no
    clock, no randomness — a resumable run must recompute identically) and host-neutral (it names
    only the opaque :class:`RunKey` fields, never a path). Its job is BINDING a verdict to a
    recorded spawn of this run, not secrecy — anything that can read the run can recompute it."""
    raw = f"{key.project_id}:{key.run_id}:{key.generation}:audit:{seq}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
