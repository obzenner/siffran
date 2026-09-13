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
import secrets
import json
from typing import Protocol

from core import Allow, Block, Fault, Inert, Present, RunKey, RunState, adjudicate, claims, budget
from core import obligations as obligation_projection
from core.records import ABSENT, Conflict, Corrupt
from vendor.obligations import Contract, revise, project, render_text, verify

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
                 default_max_passes: int = DEFAULT_MAX_PASSES,
                 stall_deadline_sec: float = 1800.0,
                 max_idle_stops: int = 50) -> None:
        self._runs = runs
        self._artifacts = artifacts
        self._allocator = allocator
        self._theta = theta
        self._default_max_passes = default_max_passes
        # Wall-clock "time since last knowledge progress" bound for an idle-waiting stop (ADR budget
        # fix). 30 min ≈ 2.2× the measured 825s worst-case audit; the composition root overrides it
        # from EMPIRICA_STALL_DEADLINE_SEC. This is what makes an idle wait terminate without a pass.
        self._stall_deadline_sec = stall_deadline_sec
        # Clock-free consecutive-no-progress backstop: the wall-clock deadline above is gated on an
        # OPTIONAL `observed_at`, so a clockless caller (Claude Code hook input carries no timestamp
        # and the host may omit its stamp) could otherwise block forever. This bounds an idle run in
        # stop COUNT with no dependence on any clock. Overridden from EMPIRICA_MAX_IDLE_STOPS.
        self._max_idle_stops = max_idle_stops

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
        if ctype == wire.CMD_GET_ARGUMENT:
            return self._get_argument(command)
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
        # The author's actor is optional bookkeeping (ADR-24): a record without a usable model
        # degrades to "no author recorded" rather than refusing to start the run (actors.py). Only a
        # non-object is a malformed request.
        raw_actor = command.get("actor")
        if raw_actor is not None and not isinstance(raw_actor, dict):
            raise wire.InvalidRequest("actor must be an object")
        author_actor = actors.normalise(raw_actor) if raw_actor is not None else None

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
                                     theta=self._theta, modes=modes, author_actor=author_actor)
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

    # --- GetArgument ---------------------------------------------------------

    def _get_argument(self, command: dict) -> dict:
        """Return a host-neutral, nonce-free audit dossier for the current argument."""
        key = wire.decode_handle(wire.require(command, "run_id", str))
        read = self._runs.read(key)
        if read is ABSENT:
            return wire.inert("no_run")
        if isinstance(read, Corrupt):
            return wire.fault(wire.FAULT_CORRUPT_RUN, read.reason)
        state = OperationalState.decode(read.value)
        if state is None:
            return wire.fault(wire.FAULT_CORRUPT_RUN, "run document is unreadable")
        try:
            graph, graph_fault = self._load_graph(key, state)
        except knowledge.KnowledgeError as exc:
            return wire.fault(wire.FAULT_CORRUPT_ARTIFACTS, str(exc))
        if graph_fault is not None:
            return graph_fault
        if not isinstance(graph, dict):
            return wire.allow(False, self._run_view(key, state, argument={
                "argument_digest": None, "theta": state.theta, "frozen_claims": state.frozen_claims,
                "claims": [], "tickets": self._public_tickets(state),
                "text": self._render_dossier(state, None, [], None)}))
        evidence = knowledge.build_evidence_oracle(self._knowledge.evidence, self._knowledge.evidence_leaves)
        def evidence_ok(nid, purpose):
            return evidence(nid, purpose)[0]
        gating = sorted(claims.gating_goals(graph, state.theta, evidence_ok))
        approving = knowledge.approving_evidence_ids(self._knowledge.evidence)
        digest_of = knowledge.build_digest_of(graph, approving, self._knowledge.evidence_leaves)
        rows = [self._argument_claim(graph, nid, state.theta, evidence_ok, digest_of(nid)) for nid in gating]
        argument_digest = claims.argument_digest(graph)
        return wire.allow(False, self._run_view(key, state, argument={
            "argument_digest": argument_digest, "theta": state.theta,
            "frozen_claims": list(state.frozen_claims) if state.frozen_claims is not None else None,
            "claims": rows, "tickets": self._public_tickets(state),
            "text": self._render_dossier(state, argument_digest, rows, self._contract_view(key, state))}))

    def _public_tickets(self, state: OperationalState) -> list[dict]:
        return [{k: t[k] for k in ("seq", "model", "harness", "issued_at", "consumed") if k in t}
                for t in state.audit_tickets]

    def _argument_claim(self, graph, nid, theta, evidence, digests):
        node = graph["nodes"][nid]
        leaves = []
        for record in knowledge.active_evidence_leaves(self._knowledge.evidence_leaves):
            statement = record.get("statement", {})
            subject = statement.get("subject", []) if isinstance(statement, dict) else []
            if not subject or not isinstance(subject[0], dict) or subject[0].get("name") != nid:
                continue
            predicate = statement.get("predicate", {})
            if not isinstance(predicate, dict):
                continue
            fold = knowledge.evidence_fold(statement)
            if fold not in ("research", "spike"):
                continue
            leaf = {"evidence_id": record.get("evidence_id"), "fold": fold,
                    "kind": predicate.get("kind"), "citation": predicate.get("citation") or predicate.get("source"),
                    "source": predicate.get("source"), "result": predicate.get("result"),
                    "gate": predicate.get("gate"), "command": predicate.get("command")}
            leaves.append(leaf)
        leaves.sort(key=lambda e: str(e.get("evidence_id", "")))
        return {"id": nid, "text": node["text"], "kind": node.get("kind"),
                "state": claims.state_of(graph, nid, theta, evidence), "confidence": node["confidence"],
                "parent": next((e["from"] for e in graph["edges"] if e["type"] == "SupportedBy" and e["to"] == nid), None),
                **digests, "evidence": leaves}

    def _render_dossier(self, state, argument_digest, rows, contract):
        lines = [f"Goal: {state.goal}", f"Contract: {contract.get('contract_id') if contract else 'none'}",
                 f"argument_digest: {argument_digest or 'none'}", f"theta: {state.theta}"]
        for row in rows:
            lines.append(f"{row['id']} [{row['state']}] {row['text']}  claim_digest={row['claim_digest']} evidence_digest={row['evidence_digest']}")
            for leaf in row["evidence"]:
                if leaf["fold"] == "research":
                    lines.append(f"  - research: {leaf.get('citation') or leaf.get('source') or ''} → {leaf.get('result') or ''} ({leaf.get('evidence_id')})")
                else:
                    lines.append(f"  - spike: {leaf.get('command') or ''} → gate {leaf.get('gate') or ''} ({leaf.get('evidence_id')})")
        if contract:
            lines.extend(("", render_text(contract)))
        return "\n".join(lines)

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
            "audit_tickets": self._public_tickets(state),
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
        contract = self._contract_view(key, state)
        return wire.allow(state.status == wire.STATUS_CONVERGED,
                          self._run_view(key, state, snapshot=snapshot, contract=contract))

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
            # A LATE audit verdict is admissible on a terminal run when its nonce matches an ISSUED
            # ticket: the auditor may have been dispatched before the run stopped (e.g. on the pass
            # budget while it waited), so its work should become durable. It CANNOT flip the run to
            # converged — `adjudicate` refuses to re-judge a finished run — so admitting it is safe;
            # it is a pure knowledge append that leaves the terminal status unchanged. A verdict with
            # no matching issued ticket still faults closed (an unattributable late write).
            if kind == knowledge.KIND_AUDIT_VERDICT:
                payload = self._verdict_payload(action)
                rejected = self._reject_ticket_use(key, state, payload["nonce"])
                if rejected is not None:
                    return rejected
                return self._record_audit_verdict(key, payload)
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
                knowledge.evidence_leaf_artifact(evidence_id, statement, verdicts, supersedes),
                sync_contract=True)
        if kind == knowledge.KIND_AUDIT_VERDICT:
            payload = self._verdict_payload(action)
            rejected = self._reject_ticket_use(key, state, payload["nonce"])
            if rejected is not None:
                return rejected
            return self._record_audit_verdict(key, payload)
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
        if kind == wire.KIND_VOID_SPAWN:
            return self._void_spawn(key, action)
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
            key, state, knowledge.evidence_artifact(claim_id, purpose, ok, reason),
            sync_contract=True)

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
                         artifact_pair: tuple[str, str], *, sync_contract: bool = False) -> dict:
        """Append one immutable knowledge artifact and acknowledge with the (unchanged) run snapshot.

        Evidence and audit records do not touch the operational state — they are pure appends to the
        knowledge plane — so the run's revision is unchanged (ADR-31 append is unconditional)."""
        art_id, body = artifact_pair
        self._artifacts.append(key, knowledge_artifact(art_id, body))
        # Evidence can make the complete argument audit-ready.  That synthetic requirement has a
        # real append-only lifecycle: add it at the first all-approved observation and let later
        # graph revisions explicitly retire/replace it.
        if sync_contract and state.is_active:
            read = self._runs.read(key)
            current = OperationalState.decode(read.value) if isinstance(read, Present) else None
            if current is not None and current.claim_graph_artifact_id is not None:
                try:
                    graph, fault = self._load_graph(key, current)
                except knowledge.KnowledgeError:
                    graph, fault = None, True
                if isinstance(graph, dict) and fault is None:
                    revised = self._revise_contract(key, current, graph, art_id,
                                                    "argument evidence changed")
                    if revised is not None:
                        try:
                            self._runs.compare_and_set(key, revised.encode(), read.revision)
                            state = revised
                        except Conflict:
                            pass
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

    def _reject_ticket_use(self, key: RunKey, state: OperationalState, nonce: str) -> dict | None:
        ticket = next((item for item in state.audit_tickets if item.get("nonce") == nonce), None)
        if ticket is None:
            return wire.block("audit verdict rejected: no issued ticket matches that nonce",
                              self._run_view(key, state))
        if ticket.get("void"):
            return wire.block("audit verdict rejected: ticket was voided",
                              self._run_view(key, state))
        if ticket.get("consumed"):
            return wire.block("audit verdict rejected: consumed ticket replay",
                              self._run_view(key, state))
        return None

    def _record_audit_verdict(self, key: RunKey, payload: dict) -> dict:
        """Consume the one-shot capability before appending its verdict.

        Consumption first is fail-closed: a crash can leave an audit owed, but can never leave an
        accepted verdict with a reusable ticket.
        """
        consumed = self._consume_audit_ticket(
            key, {"nonce": payload["nonce"], "reject_replay": True})
        if not isinstance(consumed, dict) or consumed.get("type") != "Allow":
            return consumed
        art_id, body = knowledge.audit_verdict_artifact(payload)
        self._artifacts.append(key, knowledge_artifact(art_id, body))
        return consumed

    def _reserve_spawn(self, key: RunKey) -> dict:
        """Atomically reserve ONE spawn against the cap (ADR-17). Unbounded → allow and count nothing.
        Cap reached → Block, count unchanged (a denied spawn did not happen). Otherwise increment the
        counter under CAS — the ground-truth reservation a host gate enforces on."""
        def mutate(state: OperationalState):
            reservation_id = f"reservation-{state.reservation_seq + 1}"
            reservation = {"id": reservation_id, "state": "reserved"}
            if state.max_spawns is None:
                return state.evolve(reservation_seq=state.reservation_seq + 1,
                                    reservations=(*state.reservations, reservation))
            if not state.can_reserve_spawn():
                return wire.block(
                    f"spawn budget exhausted: {state.spawns}/{state.max_spawns} used; the spawn is "
                    f"DENIED (ADR-17). Resolve remaining unknowns without spawning, tag them "
                    f"blocked=needs-budget, or raise max_spawns.",
                    self._run_view(key, state, spawn=self._spawn_view(state, reserved=False)))
            return state.evolve(spawns=state.spawns + 1,
                                reservation_seq=state.reservation_seq + 1,
                                reservations=(*state.reservations, reservation))
        result = self._commit(key, mutate)
        if isinstance(result, dict):
            return result
        return wire.allow(False, self._run_view(
            key, result, spawn=self._spawn_view(
                result, reserved=True, reservation_id=result.reservations[-1]["id"])))

    def _configure_budget(self, key: RunKey, action: dict) -> dict:
        """Set (or clear) the spawn cap and/or RAISE the a-priori pass ceiling (ADR-17/19/28).

        ``max_spawns`` is a non-negative int or null (unbounded). ``max_passes`` is a positive int
        and may ONLY be raised — the ceiling is lifted by a human here, never by the run (SKILL.md;
        the run derives its working cap under this fixed ceiling), so a value below the current
        ceiling is refused as a conflict rather than silently shrinking the budget. At least one of
        the two fields must be present. Under the same CAS as any state write, so an operator's
        change cannot clobber a concurrent reservation."""
        has_spawns = "max_spawns" in action
        has_passes = "max_passes" in action
        if not has_spawns and not has_passes:
            raise wire.InvalidRequest("configure_budget requires max_spawns and/or max_passes")
        cap = action.get("max_spawns")
        if has_spawns and cap is not None and (not isinstance(cap, int)
                                               or isinstance(cap, bool) or cap < 0):
            raise wire.InvalidRequest("max_spawns must be a non-negative integer or null")
        ceiling = action.get("max_passes")
        if has_passes and (not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling < 1):
            raise wire.InvalidRequest("max_passes must be a positive integer")

        def mutate(state: OperationalState):
            changes: dict = {}
            if has_spawns and state.max_spawns != cap:
                changes["max_spawns"] = cap
            if has_passes:
                if ceiling < state.max_passes:
                    return wire.fault(
                        wire.FAULT_CONFLICT,
                        f"max_passes may only be raised: {ceiling} is below the current ceiling "
                        f"{state.max_passes} (the run derives its working cap under this ceiling)")
                if ceiling != state.max_passes:
                    changes["max_passes"] = ceiling
            if not changes:  # already at these values — no-op, do not churn the revision
                return wire.allow(False, self._run_view(key, state, spawn=self._spawn_view(state)))
            return state.evolve(**changes)
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

        # A freeze is an application knowledge event.  Its immutable artifact is the authority for
        # the hold-transition revision; append it before the CAS just like a graph artifact.
        freeze_body = json.dumps({"kind": "freeze", "claims": list(committed)}, sort_keys=True,
                                 separators=(",", ":"))
        freeze_artifact_id = knowledge.content_address(freeze_body)
        self._artifacts.append(key, knowledge_artifact(freeze_artifact_id, freeze_body))
        for _ in range(_MAX_CAS_RETRIES):
            read = self._runs.read(key)
            if not isinstance(read, Present):
                return wire.fault(wire.FAULT_CONFLICT, "run disappeared during freeze")
            state = OperationalState.decode(read.value)
            if state is None or not state.is_active:
                return wire.fault(wire.FAULT_CONFLICT, "run is not active")
            if state.frozen_claims is not None:
                return wire.allow(False, self._run_view(
                    key, state, frozen_claims=list(state.frozen_claims),
                    note="already frozen (first write wins)"))
            seq = state.stamp_seq + 1
            candidate = state.evolve(frozen_claims=committed, freeze_seq=seq, stamp_seq=seq)
            try:
                graph, fault = self._load_graph(key, candidate)
            except knowledge.KnowledgeError:
                graph, fault = None, True
            if isinstance(graph, dict) and fault is None:
                revised = self._revise_contract(key, candidate, graph, freeze_artifact_id, "frozen scope")
                if revised is not None:
                    candidate = revised
            try:
                self._runs.compare_and_set(key, candidate.encode(), read.revision)
            except Conflict:
                continue
            return wire.allow(False, self._run_view(
                key, candidate, frozen_claims=list(candidate.frozen_claims),
                contract=self._contract_view(key, candidate)))
        return wire.fault(wire.FAULT_CONFLICT, "freeze did not converge under contention")


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
        """Mint and persist one unpredictable, one-shot ticket bound to one reservation."""
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

        requested_reservation = action.get("reservation_id")
        if requested_reservation is not None and not isinstance(requested_reservation, str):
            raise wire.InvalidRequest("reservation_id must be a string")

        def mutate(state: OperationalState):
            if actor is not None and state.author_actor is not None and actors.same_actor(actor, state.author_actor):
                return wire.block("audit ticket denied: ADR-24 decorrelation requires an auditor on a different model than the author", self._run_view(key, state, audit={"independence": "same_model"}))
            available = [r for r in state.reservations if r.get("state") == "reserved"
                         and not any(t.get("reservation_id") == r.get("id") for t in state.audit_tickets)]
            reservation = next((r for r in available if r.get("id") == requested_reservation), None)
            if reservation is None and requested_reservation is None and len(available) == 1:
                reservation = available[0]
            if reservation is None:
                return wire.block("audit ticket denied: no matching unbound spawn reservation",
                                  self._run_view(key, state))
            seq = len(state.audit_tickets) + 1
            ticket = {"nonce": secrets.token_hex(16), "seq": seq, "consumed": False,
                      "reservation_id": reservation.get("id") if reservation else None,
                      "model": actor.get("model") if actor else None,
                      "harness": actor.get("harness") if actor else None, "issued_at": None}
            if actor is not None:
                ticket["actor"] = actor
            return state.evolve(audit_tickets=(*state.audit_tickets, ticket))
        result = self._commit(key, mutate)
        if isinstance(result, dict):
            return result
        issued = result.audit_tickets[-1]
        return wire.allow(False, self._run_view(
            key, result, ticket={"nonce": issued["nonce"], "seq": issued["seq"]}))

    def _void_spawn(self, key: RunKey, action: dict) -> dict:
        """Idempotently release the exact reservation bound to a named ticket."""
        nonce = action.get("nonce")
        reservation_arg = action.get("reservation_id")
        if nonce is not None and not isinstance(nonce, str):
            raise wire.InvalidRequest("nonce must be a string or null")
        if reservation_arg is not None and not isinstance(reservation_arg, str):
            raise wire.InvalidRequest("reservation_id must be a string or null")
        def mutate(state: OperationalState):
            ticket = next((t for t in state.audit_tickets if nonce is not None
                           and t.get("nonce") == nonce), None)
            reservation_id = ticket.get("reservation_id") if ticket is not None else reservation_arg
            if nonce is not None and (ticket is None or ticket.get("void")):
                return wire.block("spawn void ignored: unknown or already-void ticket",
                                  self._run_view(key, state))
            reservation = next((r for r in state.reservations
                                if r.get("id") == reservation_id), None)
            if reservation is None or reservation.get("state") != "reserved":
                return wire.block("spawn void ignored: reservation is not releasable",
                                  self._run_view(key, state))
            tickets = tuple({**t, "void": True} if nonce is not None and t.get("nonce") == nonce else t
                            for t in state.audit_tickets)
            reservations = tuple({**r, "state": "void"} if r.get("id") == reservation_id else r
                                 for r in state.reservations)
            return state.evolve(spawns=max(0, state.spawns - 1), audit_tickets=tickets,
                                reservations=reservations)
        result = self._commit(key, mutate)
        if isinstance(result, dict):
            return result
        return wire.allow(False, self._run_view(key, result, spawn=self._spawn_view(result)))

    def _consume_audit_ticket(self, key: RunKey, action: dict) -> dict:
        """Mark an issued ticket consumed (ADR-20 P6). Idempotent: consuming an already-consumed
        ticket is a no-op. An unknown nonce is a caller error, not a race — rejected as
        invalid_request. Consumption is bookkeeping; the audit coverage decision still binds on the
        issued nonce, so this never changes whether a run may converge."""
        nonce = wire.require(action, "nonce", str)
        reject_replay = action.get("reject_replay", False) is True

        def mutate(state: OperationalState):
            if not any(t["nonce"] == nonce for t in state.audit_tickets):
                return wire.fault(wire.FAULT_INVALID_REQUEST,
                                  "no audit ticket matches that nonce")
            if all(t.get("consumed") for t in state.audit_tickets if t["nonce"] == nonce):
                if reject_replay:
                    return wire.block("audit verdict rejected: consumed ticket replay",
                                      self._run_view(key, state))
                return wire.allow(False, self._run_view(
                    key, state, note="ticket already consumed"))
            updated = tuple({**t, "consumed": True} if t["nonce"] == nonce else t
                            for t in state.audit_tickets)
            return state.evolve(audit_tickets=updated)
        for _ in range(_MAX_CAS_RETRIES):
            read = self._runs.read(key)
            if not isinstance(read, Present):
                return wire.fault(wire.FAULT_CONFLICT, "run unavailable while consuming ticket")
            state = OperationalState.decode(read.value)
            if state is None:
                return wire.fault(wire.FAULT_CORRUPT_RUN, "run document is unreadable")
            outcome = mutate(state)
            if isinstance(outcome, dict):
                return outcome
            try:
                self._runs.compare_and_set(key, outcome.encode(), read.revision)
            except Conflict:
                continue
            return wire.allow(state.status == wire.STATUS_CONVERGED,
                              self._run_view(key, outcome))
        return wire.fault(wire.FAULT_CONFLICT, "ticket consumption did not converge")

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
            # On the SAME write that moves the pointer, (re)derive the scope-based WORKING budget.
            # The a-priori CEILING (`max_passes`) is fixed at the FIRST graph from the seeded scope
            # and is NEVER raised by the run thereafter — only a human may raise it, via
            # `configure_budget` (SKILL.md; ADR-19/28). Auto-raising it on every graph update let a
            # scope-growing loop lift its own ceiling without limit and never terminate, which is the
            # regression this closes. On later graphs the working cap is re-derived under that FIXED
            # ceiling (`budget.derive` clamps to `min(max_passes, open+2)`), so scope growth pushes
            # `working_passes` UP TO but never past the ceiling. `max_spawns` is derived only when no
            # explicit operator cap is set (never overridden).
            changes: dict = {"claim_graph_artifact_id": art_id}
            seed_open = budget.open_gating_claim_count(canonical)
            if state.working_passes is None:
                # First graph: fix the ceiling from the seeded scope, never lowering an existing cap.
                ceiling = max(state.max_passes, budget.ceiling_for(seed_open))
                derived_passes, derived_spawns = budget.derive(canonical, ceiling)
                changes["max_passes"] = ceiling
                changes["working_passes"] = derived_passes
                if state.max_spawns is None:
                    changes["max_spawns"] = derived_spawns
            else:
                # Later graph: re-derive the working cap under the FIXED ceiling; max_passes unchanged.
                derived_passes, derived_spawns = budget.derive(canonical, state.max_passes)
                changes["working_passes"] = derived_passes
                if state.max_spawns is None:
                    changes["max_spawns"] = derived_spawns
            # The graph artifact is the authority for this contract revision.  Build the
            # revision before the CAS so the graph pointer and contract pointer move together.
            new_state = state.evolve(**changes)
            revised = self._revise_contract(key, new_state, canonical, art_id, "graph write")
            if revised is not None:
                new_state = revised
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
        # Optional host clock (epoch seconds): the host stamps it because Claude Code hook input
        # carries no timestamp. It threads through to the stall-deadline decision in _finalize_block.
        observed_at = wire.optional_epoch(command, "observed_at")
        for _ in range(_MAX_CAS_RETRIES):
            outcome = self._evaluate_once(key, advisory=(intent == wire.INTENT_CONTINUE),
                                          observed_at=observed_at)
            if outcome is not _RETRY:
                return outcome
        return wire.fault(wire.FAULT_CONFLICT, "evaluation did not converge under contention")

    def _evaluate_once(self, key: RunKey, *, advisory: bool, observed_at: float | None = None):
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

        # A progress token over what already loaded here: the pointer plus the sizes of the knowledge
        # sets. It changes ⇔ the argument or its evidence/verdicts/tickets grew since the last stop —
        # which is exactly what makes a stop count a PASS rather than an idle wait (change B).
        progress_token = _progress_token(state.claim_graph_artifact_id, know.evidence,
                                         know.evidence_leaves, know.verdicts, state.audit_tickets)

        run_state = RunState(status="active", is_legacy=state.is_legacy,
                             frozen_claims=state.frozen_claims)
        decision = adjudicate(run=run_state, graph=graph, theta=state.theta,
                             evidence=evidence, audit=audit,
                             route_verdict=state.route_p1_verdict(),
                             digest_of=digest_of, attribution=attribution)
        return self._finalize(key, read, state, decision, advisory=advisory,
                              progress_token=progress_token, observed_at=observed_at)

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
                  decision, *, advisory: bool, progress_token: str,
                  observed_at: float | None) -> dict:
        """Turn a core :class:`Decision` into a wire result, applying the pass-budget cap and
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
            return self._finalize_block(key, read, state, decision, advisory,
                                        progress_token=progress_token, observed_at=observed_at)
        # Allow: the core blesses a stop (converged, residual, refuted, or frozen).
        if advisory:
            return wire.allow(decision.converged, self._decision_run(
                key, state, decision, contract=self._contract_view(key, state)))
        new_state = state.evolve(status=decision.status)
        persisted = self._cas(key, read.revision, new_state)
        if persisted is _RETRY:
            return _RETRY
        contract = self._contract_view(key, persisted)
        artifact_id = persisted.contract_artifact_id
        return wire.allow(decision.converged, self._decision_run(
            key, persisted, decision, contract=contract, contract_artifact_id=artifact_id))

    def _finalize_block(self, key: RunKey, read: Present, state: OperationalState,
                        decision: Block, advisory: bool, *, progress_token: str,
                        observed_at: float | None) -> dict:
        """A block. Advisory reads report it untouched. A real stop attempt counts a pass ONLY when
        it made knowledge progress (change B): an idle wait — an identical stop while an async
        auditor runs — must not burn the budget. The two outcomes give the termination guarantee:

        * PROGRESS (token changed; a first stop always counts, as the prior token is None) → +1 pass,
          reset the stall clock AND the idle-stop counter; when the pass count reaches the working
          cap (``working_passes`` or, absent one, ``max_passes``) the block becomes a non-converged
          ``stopped_budget`` stop.
        * NO PROGRESS (identical stop) → pass UNCHANGED, idle-stop counter +1. The run keeps blocking
          until EITHER bound trips (whichever is earlier): the clock-free consecutive-idle count
          reaches ``max_idle_stops``, or — when a host clock IS present — the wall-clock time since
          the last progress exceeds ``stall_deadline_sec``. Either way it stops ``stopped_residual``,
          so an audit that never returns cannot wedge the run open, WITH OR WITHOUT a host clock.
        """
        if advisory:
            return wire.block(decision.reason, self._run_view(key, state),
                              contract=self._contract_view(key, state))

        if progress_token != state.last_stop_digest:
            new_passes = state.passes + 1
            cap = state.working_passes or state.max_passes
            # Progress resets both idle backstops (count and clock seed).
            changes: dict = {"passes": new_passes, "last_stop_digest": progress_token,
                             "idle_stops": 0}
            if observed_at is not None:
                changes["last_progress_ts"] = observed_at
            if new_passes >= cap:
                changes["status"] = wire.STATUS_STOPPED_BUDGET
                persisted = self._cas(key, read.revision, state.evolve(**changes))
                if persisted is _RETRY:
                    return _RETRY
                note = (f"NON-CONVERGED: reached pass budget={cap} without convergence "
                        f"({decision.reason})")
                contract = self._contract_view(key, persisted, include_budget=True)
                artifact_id = persisted.contract_artifact_id
                return wire.allow(False, self._run_view(key, persisted, note=note, contract=contract,
                                                        contract_artifact_id=artifact_id))
            persisted = self._cas(key, read.revision, state.evolve(**changes))
            if persisted is _RETRY:
                return _RETRY
            return wire.block(decision.reason, self._run_view(key, persisted),
                              contract=self._contract_view(key, persisted))

        # No progress: an idle wait. Do not cost a pass, but count it against two backstops. The
        # clock-free count fires REGARDLESS of `observed_at`; the wall-clock deadline is an additional
        # EARLIER bound when a host clock is present. Terminate on EITHER. Measure the stall from the
        # last progress — or, on the first stop that lacked a clock, from this observation (so the
        # window starts at the first stop, never at run creation).
        new_idle_stops = state.idle_stops + 1
        seed_ts = state.last_progress_ts if state.last_progress_ts is not None else observed_at
        stalled_by_clock = (observed_at is not None and seed_ts is not None
                            and (observed_at - seed_ts) > self._stall_deadline_sec)
        stalled_by_count = new_idle_stops >= self._max_idle_stops
        if stalled_by_clock or stalled_by_count:
            persisted = self._cas(
                key, read.revision,
                state.evolve(status=wire.STATUS_STOPPED_RESIDUAL, idle_stops=new_idle_stops))
            if persisted is _RETRY:
                return _RETRY
            note = (f"no knowledge progress for {int(observed_at - seed_ts)}s; "
                    "audit did not return or the loop stalled") if stalled_by_clock else (
                f"no knowledge progress across {new_idle_stops} stops; "
                "audit did not return or the loop stalled")
            contract = self._contract_view(key, persisted, include_stall=True)
            artifact_id = persisted.contract_artifact_id
            return wire.allow(False, self._run_view(key, persisted, note=note, contract=contract,
                                                    contract_artifact_id=artifact_id))

        # Not yet stalled: record the incremented idle count (and seed the stall clock on the first
        # stop that lacked one) and keep blocking.
        changes = {"idle_stops": new_idle_stops}
        if state.last_progress_ts is None and observed_at is not None:
            changes["last_progress_ts"] = observed_at
        persisted = self._cas(key, read.revision, state.evolve(**changes))
        if persisted is _RETRY:
            return _RETRY
        return wire.block(decision.reason, self._run_view(key, persisted),
                          contract=self._contract_view(key, persisted))

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
        return wire.allow(state.status == wire.STATUS_CONVERGED,
                          self._run_view(key, state, contract=self._contract_view(key, state)))

    def _run_view(self, key: RunKey, state: OperationalState, note: str | None = None,
                  **extra: object) -> dict:
        # Intent is operational state, but it is also model-facing boundary data: before the first
        # graph there is deliberately no contract, so a host must not have to recover the user's
        # resolved invocation from history.  These additive fields are present on every run view.
        extra.setdefault("goal", state.goal)
        extra.setdefault("modes", dict(state.modes))
        audit = extra.get("audit")
        if not isinstance(audit, dict):
            audit = {"result": audit} if audit is not None else {}
        audit.setdefault("independence", self._independence(state))
        extra["audit"] = audit
        # B1 (amended): `run.contract` rides on EVERY run view, not only the paths that remembered to
        # pass it. Found live: a spawn-budget Block reached the Pi agent as prose only. Callers that
        # need the terminal synthetic obligations still pass an explicit, richer `contract=`.
        if "contract" not in extra:
            extra["contract"] = self._contract_view(key, state)
        return wire.run_obj(wire.encode_handle(key), state.status, state.revision, note=note, **extra)

    # --- operation view fragments --------------------------------------------

    def _spawn_view(self, state: OperationalState, *, reserved: bool | None = None,
                    reservation_id: str | None = None, note: str | None = None) -> dict:
        """The spawn-budget fragment for a reserve/configure acknowledgement. ``remaining`` is null
        when unbounded (JSON has no infinity), which the wire distinguishes from a numeric cap."""
        view: dict = {"spawns": state.spawns, "max_spawns": state.max_spawns,
                      "remaining": None if state.max_spawns is None
                      else int(state.spawns_remaining)}
        if reserved is not None:
            view["reserved"] = reserved
        if reservation_id is not None:
            view["reservation_id"] = reservation_id
        if note:
            view["note"] = note
        return view

    def _independence(self, state: OperationalState) -> str:
        """Report, without overclaiming, what recorded concrete model identities establish."""
        auditor = next((t.get("actor") for t in reversed(state.audit_tickets)
                        if isinstance(t.get("actor"), dict)), None)
        author = state.author_actor
        if not isinstance(author, dict) or not isinstance(auditor, dict):
            return "unverified"
        if actors.is_tier_alias(author.get("model")) or actors.is_tier_alias(auditor.get("model")):
            return "unverified"
        return "same_model" if actors.same_actor(author, auditor) else "decorrelated"

    def _route_view(self, state: OperationalState) -> dict:
        """The P1 ordering fragment: the write-order witnesses and the derived verdict."""
        verdict, reason = state.route_p1_verdict()
        return {"route_seq": state.route_seq,
                "first_investigation_seq": state.first_investigation_seq,
                "verdict": verdict, "reason": reason}

    def _contract_view(self, key: RunKey, state: OperationalState, *, include_budget: bool = False,
                       include_stall: bool = False) -> dict | None:
        """Project the persisted revision against current observations.

        The graph is consulted only for current observations and the two synthetic run-level
        budget/stall obligations.  Those synthetic obligations are intentionally view-time only:
        they describe a terminal gate, not a revision of the claim contract.
        """
        contract = self._load_contract(key, state.contract_artifact_id)
        if contract is None:
            return None
        try:
            graph, fault = self._load_graph(key, state)
        except knowledge.KnowledgeError:
            return None
        if fault is not None or not isinstance(graph, dict):
            return None
        evidence = knowledge.build_evidence_oracle(self._knowledge.evidence,
                                                   self._knowledge.evidence_leaves)
        def evidence_ok(nid: str, purpose: str) -> bool:
            return evidence(nid, purpose)[0]

        gating = claims.gating_goals(graph, state.theta, evidence_ok)
        approved = tuple(sorted(nid for nid in gating
                                if claims.state_of(graph, nid, state.theta, evidence_ok)
                                == claims.STATE_APPROVED))
        # The audit is intentionally a view-time obligation, like budget/stall: whether an
        # independent review currently covers this immutable argument is an observation, not a
        # graph-write revision.  It therefore never churns durable B5 lineage.
        audit_digest = None
        audit_ok = False
        if gating and len(approved) == len(gating):
            audit_digest = claims.argument_digest(graph)
            digest_of = knowledge.build_digest_of(graph, knowledge.approving_evidence_ids(self._knowledge.evidence),
                                                  self._knowledge.evidence_leaves)
            audit = knowledge.build_audit_oracle(list(state.audit_tickets), self._knowledge.verdicts)
            audit_ok, _ = audit({nid: digest_of(nid) for nid in approved}, audit_digest)
        observations = obligation_projection.observations_from_knowledge(
            self._knowledge.evidence_leaves, self._knowledge.verdicts)
        # A verdict with the matching digest is not itself coverage: it must also have the issued
        # nonce and complete approved-claim review.  Do not let an invalid passing verdict satisfy
        # the rendered audit obligation while the convergence decision correctly remains blocked.
        if audit_digest is not None and not audit_ok:
            observations = tuple(item for item in observations
                                 if not (item.kind == "judgment" and item.ref == f"audit/{audit_digest}"))
        accepted = tuple(item for item in observations if obligation_projection.trusted(item))
        # Keep synthetic audit/budget/stall obligations out of durable revisions; add them only to
        # this wire projection, retaining the persisted revision lineage for live claim obligations.
        include_budget = include_budget or state.status == wire.STATUS_STOPPED_BUDGET
        include_stall = include_stall or state.status == wire.STATUS_STOPPED_RESIDUAL
        if audit_digest is not None or include_budget or include_stall:
            synthetic = obligation_projection.contract_for_graph(
                contract.contract_id, contract.revision, graph, state.theta, evidence,
                frozen_claims=state.frozen_claims, include_budget=include_budget,
                include_stall=include_stall, audit_digest=audit_digest)
            existing_ids = {item.id for item in contract.obligations}
            extras = tuple(item for item in synthetic.obligations
                           if (item.id.startswith("empirica/run/") or item.id.startswith("empirica/audit/"))
                           and item.id not in existing_ids)
            contract = Contract(contract.contract_id, contract.revision,
                                contract.obligations + extras, contract.provenance,
                                contract.parent_revision, contract.supersedes, contract.retired)
        return project(contract, verify(contract, accepted, obligation_projection.trusted), accepted)

    def _load_contract(self, key: RunKey, artifact_id: str | None) -> Contract | None:
        if artifact_id is None:
            return None
        records = self._artifacts.read(key)
        if not isinstance(records, Present):
            return None
        for artifact in records.value:
            if artifact.artifact_id == artifact_id:
                try:
                    return Contract.from_json(json.loads(artifact.body))
                except (TypeError, ValueError, json.JSONDecodeError):
                    return None
        return None

    def _revise_contract(self, key: RunKey, state: OperationalState, graph: dict,
                         authority: str, reason: str) -> OperationalState | None:
        """Append a real contract revision when the canonical claim-obligation set changes.

        Called inside the graph/freeze CAS retry loops: a retry re-loads the persisted revision and
        recomputes this diff, so no stale revision pointer can win a concurrent update.
        """
        records = self._artifacts.read(key)
        if isinstance(records, Corrupt):
            return None
        know = knowledge.Knowledge.from_artifacts(records.value if isinstance(records, Present) else frozenset())
        evidence = knowledge.build_evidence_oracle(know.evidence, know.evidence_leaves)
        def evidence_ok(nid: str, purpose: str) -> bool:
            return evidence(nid, purpose)[0]
        gating = claims.gating_goals(graph, state.theta, evidence_ok)
        approved = [nid for nid in gating if claims.state_of(
            graph, nid, state.theta, evidence_ok) == claims.STATE_APPROVED]
        audit_digest = claims.argument_digest(graph) if gating and len(approved) == len(gating) else None
        desired = obligation_projection.contract_for_graph(
            f"empirica/{wire.encode_handle(key)}", 1, graph, state.theta, evidence,
            frozen_claims=state.frozen_claims, audit_digest=audit_digest)
        previous = self._load_contract(key, state.contract_artifact_id)
        if previous is None:
            if not desired.obligations:
                return None
            # Contract's frozen public constructor starts at revision 1; this is the initial,
            # all-additions revision corresponding to the requested conceptual empty revision 0.
            next_contract = Contract(desired.contract_id, 1, desired.obligations, desired.provenance)
        else:
            # Revision-qualified replacement ids are an implementation consequence of the frozen
            # library's global no-id-reuse rule.  Match later graph projections back to that live
            # id by provenance before diffing, so an unrelated graph write does not re-add a retired
            # base id.
            prior_by_claim = {item.because[0]: item.id for item in previous.obligations
                              if item.because and not item.id.startswith("empirica/audit/")
                              and not item.id.startswith("empirica/run/")}
            normalized_desired = []
            for item in desired.obligations:
                prior_id = (prior_by_claim.get(item.because[0]) if item.because
                            and not item.id.startswith("empirica/audit/")
                            and not item.id.startswith("empirica/run/") else None)
                if prior_id and prior_id != item.id:
                    item = type(item)(prior_id, item.mode, item.must, item.witnesses, item.because,
                                      item.hold, item.hold_reason, item.severity)
                normalized_desired.append(item)
            old = {item.id: item for item in previous.obligations}
            new = {item.id: item for item in normalized_desired}
            retire = set(old) - set(new)
            additions = [item for ident, item in new.items() if ident not in old]
            changed = [ident for ident in set(old) & set(new) if old[ident].to_json() != new[ident].to_json()]
            # The frozen library forbids reusing a retired id.  A changed claim therefore gets a
            # stable revision-qualified replacement while retaining the old obligation explicitly.
            for ident in changed:
                retire.add(ident)
                item = new[ident]
                additions.append(type(item)(f"{ident}/revision-{previous.revision + 1}", item.mode,
                                             item.must, item.witnesses, item.because,
                                             item.hold, item.hold_reason, item.severity))
            if not retire and not additions:
                return None
            refuted = next((graph["nodes"][item.because[0]].get("refuted_by")
                             for item in previous.obligations
                             if item.id in retire and item.because
                             and graph["nodes"].get(item.because[0], {}).get("refuted_by")), None)
            revision_reason = (f"refuted by {refuted}" if isinstance(refuted, str) and refuted
                               else (_change_reason(old, new, changed) if changed else reason))
            revision_authority = refuted if isinstance(refuted, str) and refuted else authority
            next_contract = revise(previous, add=additions, retire=sorted(retire),
                                   reason=revision_reason, authority=revision_authority)
        body = json.dumps(next_contract.to_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        artifact_id = knowledge.content_address(body)
        self._artifacts.append(key, knowledge_artifact(artifact_id, body))
        return state.evolve(contract_artifact_id=artifact_id, contract_revision=next_contract.revision)

    def _persist_contract(self, key: RunKey, contract: dict | None) -> str | None:
        """Compatibility helper: terminal paths reuse, rather than append, the current revision."""
        return None

    def _decision_run(self, key: RunKey, state: OperationalState, decision: Allow,
                      **extra: object) -> dict:
        """The run view for a finalised Allow, carrying the adjudicator's advisory reporting fields
        (note, deferred, blocked, audit, P1) so a caller sees *why* a stop is or is not convergence."""
        audit = decision.audit if isinstance(decision.audit, dict) else {"result": decision.audit}
        audit.setdefault("independence", self._independence(state))
        return wire.run_obj(
            wire.encode_handle(key), state.status, state.revision,
            note=decision.note, deferred=list(decision.deferred) or None,
            blocked=list(decision.blocked) or None,
            budget_blocked=list(decision.budget_blocked) or None,
            audit=audit, p1_violation=decision.p1_violation,
            p1_unverified=decision.p1_unverified,
            root_refuted=decision.root_refuted or None,
            attribution=decision.attribution, **extra)

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


def _progress_token(pointer: str | None, evidence, evidence_leaves, verdicts,
                    audit_tickets) -> str:
    """A digest of the run's knowledge SIZE at a stop: the graph pointer plus the counts of
    evidence, evidence leaves, audit verdicts, and issued tickets. It changes exactly when the
    argument or its evidence grew, so comparing it to the last stop's token tells a real pass from
    an idle wait (change B) — cheap, since every input already loaded for adjudication."""
    raw = (f"{pointer}:{len(evidence)}:{len(evidence_leaves)}:"
           f"{len(verdicts)}:{len(audit_tickets)}")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _change_reason(old: dict, new: dict, changed: list) -> str:
    """Name what actually changed on the replaced obligations — text, hold, or several things — so
    a retirement record never says "reworded" about a claim whose wording did not move (e.g. a
    freeze that only changed its hold)."""
    kinds: set[str] = set()
    for ident in changed:
        before, after = old[ident], new[ident]
        if before.must != after.must:
            kinds.add("reworded")
        if (before.hold, before.hold_reason) != (after.hold, after.hold_reason):
            kinds.add("hold changed")
        if before.witnesses != after.witnesses or before.mode != after.mode:
            kinds.add("witnesses changed")
    if len(kinds) == 1:
        return f"claim {kinds.pop()}"
    return "claim changed (" + ", ".join(sorted(kinds)) + ")" if kinds else "claim changed"
