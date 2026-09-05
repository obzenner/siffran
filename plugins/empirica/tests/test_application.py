#!/usr/bin/env python3
"""End-to-end contract suite for the host-neutral Empirica application service (ADR-30, ADR-31).

Run: python3 plugins/empirica/tests/test_application.py   (stdlib only, no pytest dependency)
Exit 0 = all checks pass; 1 = at least one failed.

Everything is in-memory: the service is driven through the ``empirica/v1`` wire envelope against
reference :class:`RunRepository`/:class:`ArtifactRepository` fakes and a reference generation
allocator — the same executable-spec fakes the port contract tests use. No filesystem, no Git, no
subprocess. The suite exercises the whole lifecycle plus the hard edges the task calls out:

    * StartRun creates a clean run; resume returns the same handle without clobbering;
    * the graph-update transaction appends the immutable artifact BEFORE it CAS-moves the pointer,
      and a lost CAS retries without ever making an orphan artifact current (races, stale pointers);
    * EvaluateRun loads exactly the pointed graph + evidence, adjudicates, and applies the
      max_passes cap in the application layer (converging, audit-owed, cap → stopped_budget);
    * corrupt/absent state and a stale pointer fail closed with the right wire fault.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent  # plugins/empirica — makes `core` and `application` importable as packages
sys.path.insert(0, str(PLUGIN))

from application import EmpiricaService, knowledge  # noqa: E402
from application.state import OperationalState  # noqa: E402
from core import claims as C  # noqa: E402
from core.records import ABSENT, Artifact, Conflict, Corrupt, Present, Revision, RunKey  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


# --- reference port fakes (mirror tests/test_core.py) ------------------------
_CORRUPT = object()


class FakeRunRepository:
    """CAS-guarded operational-state store keyed by RunKey; opaque monotonic revisions."""

    def __init__(self) -> None:
        self._store: dict[RunKey, object] = {}
        self._counter = 0

    def _mint(self) -> Revision:
        self._counter += 1
        return Revision(f"r{self._counter}")

    def read(self, key: RunKey):
        entry = self._store.get(key)
        if entry is None:
            return ABSENT
        if entry is _CORRUPT:
            return Corrupt("undecodable operational document")
        value, rev = entry
        return Present(value, rev)

    def create(self, key: RunKey, value):
        if key in self._store:
            raise Conflict(key, None, "already exists")
        rev = self._mint()
        self._store[key] = (value, rev)
        return rev

    def compare_and_set(self, key: RunKey, value, expected: Revision):
        entry = self._store.get(key)
        if entry is None:
            raise Conflict(key, expected, "absent")
        if entry is _CORRUPT:
            raise Conflict(key, expected, "corrupt")
        _, rev = entry
        if rev != expected:
            raise Conflict(key, expected, f"stale (stored {rev})")
        new = self._mint()
        self._store[key] = (value, new)
        return new

    def generations(self, project_id: str, run_id: str) -> list[int]:
        return sorted(k.generation for k in self._store
                      if k.project_id == project_id and k.run_id == run_id)

    def inject_corrupt(self, key: RunKey) -> None:
        self._store[key] = _CORRUPT

    def raw_value(self, key: RunKey):
        return self._store[key][0]


class FakeArtifactRepository:
    """Append-only content-addressed store: append is set union (commutative + idempotent)."""

    def __init__(self, order: list | None = None) -> None:
        self._store: dict[RunKey, frozenset] = {}
        self._order = order  # optional op-order log for the race test

    def append(self, key: RunKey, artifact: Artifact) -> None:
        if self._order is not None:
            self._order.append("append")
        self._store[key] = self._store.get(key, frozenset()) | {artifact}

    def read(self, key: RunKey):
        entry = self._store.get(key)
        if entry is None:
            return ABSENT
        return Present(entry, Revision(str(hash(entry))))

    def ids(self, key: RunKey) -> set[str]:
        return {a.artifact_id for a in self._store.get(key, frozenset())}


class FakeGenerationAllocator:
    """Reference allocator: active resumes in place, terminal/corrupt opens the next clean gen."""

    def __init__(self, repo: FakeRunRepository) -> None:
        self._repo = repo

    def allocate(self, project_id: str, run_id: str) -> RunKey:
        gens = self._repo.generations(project_id, run_id)
        if not gens:
            return RunKey(project_id, run_id, 1)
        top = gens[-1]
        key = RunKey(project_id, run_id, top)
        read = self._repo.read(key)
        if read is ABSENT:
            return key
        if isinstance(read, Present):
            state = OperationalState.decode(read.value)
            if state is not None and state.is_active:
                return key
        return RunKey(project_id, run_id, top + 1)


# --- drivers -----------------------------------------------------------------


def make_service(theta=0.8, default_max_passes=8, order=None):
    runs = FakeRunRepository()
    arts = FakeArtifactRepository(order=order)
    svc = EmpiricaService(runs, arts, FakeGenerationAllocator(runs),
                          theta=theta, default_max_passes=default_max_passes)
    return svc, runs, arts


def req(command: dict, request_id="req-1") -> dict:
    return {"protocol": "empirica/v1", "request_id": request_id, "command": command}


def start(svc, project="proj", session="sess", goal="the goal", **kw):
    cmd = {"type": "StartRun", "selector": {"project": project, "session": session}, "goal": goal}
    cmd.update(kw)
    return svc.handle(req(cmd))


def observe(svc, run_id, action, request_id="req-obs"):
    return svc.handle(req({"type": "ObserveAction", "run_id": run_id, "action": action}, request_id))


def evaluate(svc, run_id, intent="report_convergence"):
    return svc.handle(req({"type": "EvaluateRun", "run_id": run_id, "intent": intent}))


def get(svc, run_id):
    return svc.handle(req({"type": "GetRun", "run_id": run_id}))


def result(resp: dict) -> dict:
    return resp["result"]


def handle_of(resp: dict) -> str:
    return resp["result"]["run"]["id"]


# --- fixtures ----------------------------------------------------------------


def single_goal_graph(text="the intent", confidence=0.9, blocked=None, refuted_by=None):
    return {"root": "G0",
            "nodes": {"G0": {"type": "Goal", "text": text, "confidence": confidence,
                             "blocked": blocked, "refuted_by": refuted_by}},
            "edges": []}


def approve_evidence(claim_id="G0", reason="Fold 1 + Fold 2 satisfied"):
    return {"kind": "evidence", "claim_id": claim_id, "purpose": "approve", "ok": True,
            "reason": reason}


def passing_verdict(graph_canon, claim_id="G0", reason="Fold 1 + Fold 2 satisfied", nonce="n1"):
    """A verdict whose per-claim digests match what the service derives, so the audit truly covers
    the run (built with the production digest helpers — this constructs valid auditor input)."""
    text = graph_canon["nodes"][claim_id]["text"]
    ev_id, _ = knowledge.evidence_artifact(claim_id, "approve", True, reason)
    return {"kind": "audit_verdict", "verdict": "pass", "nonce": nonce,
            "argument_digest": C.argument_digest(graph_canon),
            "claims_reviewed": [{"claim_id": claim_id,
                                 "claim_digest": knowledge.claim_digest(text),
                                 "evidence_digest": knowledge.evidence_digest([ev_id])}],
            "findings": []}


def drive_to_converged(svc, **start_kw):
    """StartRun → observe graph, approving evidence, audit spawn + passing verdict. Returns the
    handle and the canonical graph. The audit ticket nonce is SERVER-MINTED (ADR-31 puts tickets on
    the operational plane), so the verdict must carry the nonce the issue operation returned."""
    h = handle_of(start(svc, **start_kw))
    raw = single_goal_graph()
    canon = knowledge.canonicalize_graph(raw)
    observe(svc, h, {"kind": "graph", "graph": raw})
    observe(svc, h, approve_evidence())
    nonce = result(observe(svc, h, {"kind": "audit_ticket"}))["run"]["ticket"]["nonce"]
    observe(svc, h, passing_verdict(canon, nonce=nonce))
    return h, canon


# --- lifecycle ---------------------------------------------------------------


def test_start_creates_active_run():
    svc, runs, _ = make_service()
    resp = start(svc)
    r = result(resp)
    check("L1 StartRun returns an Allow with an active, non-converged run",
          r["type"] == "Allow" and r["converged"] is False and r["run"]["status"] == "active",
          f"got {r}")
    check("L2 response echoes protocol and request_id",
          resp["protocol"] == "empirica/v1" and resp["request_id"] == "req-1", f"got {resp}")
    check("L3 a fresh run starts at revision 0", r["run"]["revision"] == 0, f"got {r['run']}")


def test_get_run_returns_snapshot():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    r = result(get(svc, h))
    check("L4 GetRun returns the active run snapshot",
          r["type"] == "Allow" and r["run"]["id"] == h and r["run"]["status"] == "active",
          f"got {r}")


def test_get_unknown_run_is_inert():
    svc, _, _ = make_service()
    # A syntactically valid handle for a run that was never created.
    from application.wire import encode_handle
    ghost = encode_handle(RunKey("proj", "ghost", 1))
    r = result(get(svc, ghost))
    check("L5 GetRun on an absent run is Inert(no_run)",
          r["type"] == "Inert" and r["reason"] == "no_run", f"got {r}")


def test_resume_same_selector_no_clobber():
    svc, runs, _ = make_service()
    h1 = handle_of(start(svc, goal="first"))
    observe(svc, h1, approve_evidence())  # mutate the run a little
    r2 = result(start(svc, goal="second"))  # same selector -> resume
    check("R1 a second StartRun on the same selector resumes the same handle",
          r2["run"]["id"] == h1, f"got {r2['run']['id']} vs {h1}")
    key = RunKey("proj", "sess", 1)
    check("R2 resume did not overwrite the run's goal",
          runs.raw_value(key)["goal"] == "first", f"got {runs.raw_value(key)}")


def test_generation_isolation_after_terminal():
    svc, runs, _ = make_service(default_max_passes=1)
    # Drive a run to a terminal budget stop, then StartRun again -> fresh generation, clean state.
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph(confidence=0.0)})
    evaluate(svc, h)  # blocks, max_passes=1 -> stopped_budget
    r2 = result(start(svc))
    check("G1 StartRun after a terminal run opens a new generation",
          r2["run"]["id"] != h, f"got {r2['run']['id']}")
    check("G2 the new generation is clean (active, no graph pointer)",
          r2["run"]["status"] == "active" and runs.generations("proj", "sess") == [1, 2]
          and runs.raw_value(RunKey("proj", "sess", 2))["claim_graph_artifact_id"] is None,
          f"gens={runs.generations('proj', 'sess')}")


# --- graph-update transaction ------------------------------------------------


def test_observe_graph_sets_pointer_to_existing_artifact():
    svc, runs, arts = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph()})
    key = RunKey("proj", "sess", 1)
    pointer = runs.raw_value(key)["claim_graph_artifact_id"]
    check("T1 the run pointer is set after a graph update", pointer is not None)
    check("T2 the pointer references an artifact present in the store (no orphan-current)",
          pointer in arts.ids(key), f"pointer {pointer} not in {arts.ids(key)}")
    check("T3 the graph write advanced the wire revision",
          runs.raw_value(key)["revision"] == 1, f"got {runs.raw_value(key)['revision']}")


def test_graph_update_idempotent():
    svc, runs, arts = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph()})
    key = RunKey("proj", "sess", 1)
    rev_after_first = runs.raw_value(key)["revision"]
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph()})  # identical graph again
    check("T4 re-observing an identical graph is a no-op (pointer + revision unchanged)",
          runs.raw_value(key)["revision"] == rev_after_first and len(arts.ids(key)) == 1,
          f"rev={runs.raw_value(key)['revision']} ids={arts.ids(key)}")


def test_invalid_graph_is_refused_without_storing():
    svc, runs, arts = make_service()
    h = handle_of(start(svc))
    bad = {"root": "G0", "nodes": {"G0": {"type": "Goal", "blocked": "made-up-tag"}}, "edges": []}
    r = result(observe(svc, h, {"kind": "graph", "graph": bad}))
    key = RunKey("proj", "sess", 1)
    check("T5 an invalid graph is refused as Fault(invalid_request, closed)",
          r["type"] == "Fault" and r["code"] == "invalid_request" and r["fail_direction"] == "closed",
          f"got {r}")
    check("T6 a refused graph left storage untouched (no append, no pointer)",
          arts.ids(key) == set() and runs.raw_value(key)["claim_graph_artifact_id"] is None,
          f"ids={arts.ids(key)}")


def test_graph_update_appends_before_cas_and_retries_on_race():
    """The load-bearing invariant: append happens before the pointer CAS, and a lost CAS retries
    without ever making an orphan artifact current."""
    order: list[str] = []
    runs = FakeRunRepository()
    arts = FakeArtifactRepository(order=order)

    class OneShotConflictRuns:
        """Wraps the run repo so the FIRST compare_and_set loses to a simulated concurrent writer
        (it advances the stored revision, then raises Conflict). The service must re-read and retry."""

        def __init__(self, inner):
            self._inner = inner
            self._tripped = False

        def read(self, key):
            return self._inner.read(key)

        def create(self, key, value):
            return self._inner.create(key, value)

        def generations(self, p, r):
            return self._inner.generations(p, r)

        def raw_value(self, key):
            return self._inner.raw_value(key)

        def compare_and_set(self, key, value, expected):
            order.append("cas")
            if not self._tripped:
                self._tripped = True
                cur = self._inner.read(key)
                self._inner.compare_and_set(key, cur.value, cur.revision)  # concurrent winner moves
                raise Conflict(key, expected, "a concurrent writer won the race")
            return self._inner.compare_and_set(key, value, expected)

    wrapped = OneShotConflictRuns(runs)
    svc = EmpiricaService(wrapped, arts, FakeGenerationAllocator(runs))
    h = handle_of(start(svc))
    order.clear()
    r = result(observe(svc, h, {"kind": "graph", "graph": single_goal_graph()}))
    key = RunKey("proj", "sess", 1)

    check("Z1 the graph update ultimately succeeds despite the race",
          r["type"] == "Allow" and runs.raw_value(key)["claim_graph_artifact_id"] is not None,
          f"got {r}")
    check("Z2 the artifact was appended BEFORE the first pointer CAS",
          order[0] == "append" and "cas" in order, f"order={order}")
    check("Z3 the CAS was retried (conflict then success)",
          order.count("cas") >= 2, f"order={order}")
    check("Z4 the current pointer references an artifact in the store (never orphan-current)",
          runs.raw_value(key)["claim_graph_artifact_id"] in arts.ids(key),
          f"pointer not in {arts.ids(key)}")


# --- evaluate: converging / converged / audit -------------------------------


def test_evaluate_open_claim_blocks_converging():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph(confidence=0.0)})
    r = result(evaluate(svc, h))
    check("E1 an open claim blocks (still converging)",
          r["type"] == "Block" and r["run"]["status"] == "active", f"got {r}")
    check("E2 a blocked stop attempt records a pass", r["run"]["revision"] >= 2, f"got {r['run']}")


def test_evaluate_converges_with_evidence_and_audit():
    svc, runs, _ = make_service()
    h, _ = drive_to_converged(svc)
    r = result(evaluate(svc, h, intent="report_convergence"))
    check("E3 a graph with evidence + a passing audit converges",
          r["type"] == "Allow" and r["converged"] is True
          and r["run"]["status"] == "converged", f"got {r}")
    check("E4 the converged status is persisted",
          runs.raw_value(RunKey("proj", "sess", 1))["status"] == "converged")
    # A finished run is not re-judged: a second evaluate returns the same converged snapshot.
    r2 = result(evaluate(svc, h))
    check("E5 a finished run is reported, not re-judged",
          r2["type"] == "Allow" and r2["converged"] is True, f"got {r2}")


def test_audit_owed_blocks_without_audit():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph()})
    observe(svc, h, approve_evidence())  # approved, but no auditor spawned
    r = result(evaluate(svc, h))
    check("E6 a converged graph with no independent audit blocks (audit owed)",
          r["type"] == "Block" and "audit" in r["reason"].lower(), f"got {r}")


def test_audit_verdict_must_cover_current_state():
    """A passing verdict whose digests do not match the run's state does not converge it."""
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph()})
    observe(svc, h, approve_evidence())
    observe(svc, h, {"kind": "audit_ticket", "nonce": "n1"})
    observe(svc, h, {"kind": "audit_verdict", "verdict": "pass", "nonce": "n1",
                     "argument_digest": "deadbeef", "claims_reviewed": [], "findings": []})
    r = result(evaluate(svc, h))
    check("E7 a verdict that does not cover the current argument blocks",
          r["type"] == "Block", f"got {r}")


# --- evaluate: termination / cap --------------------------------------------


def test_cap_converts_block_to_stopped_budget():
    svc, runs, _ = make_service(default_max_passes=2)
    h = handle_of(start(svc, max_passes=2))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph(confidence=0.0)})
    r1 = result(evaluate(svc, h, intent="stop"))
    check("C1 the first blocked stop attempt stays active (pass 1 of 2)",
          r1["type"] == "Block" and r1["run"]["status"] == "active", f"got {r1}")
    r2 = result(evaluate(svc, h, intent="stop"))
    check("C2 exhausting max_passes turns the block into a non-converged stopped_budget Allow",
          r2["type"] == "Allow" and r2["converged"] is False
          and r2["run"]["status"] == "stopped_budget", f"got {r2}")
    check("C3 the budget stop is persisted",
          runs.raw_value(RunKey("proj", "sess", 1))["status"] == "stopped_budget")


def test_continue_intent_is_advisory():
    svc, runs, _ = make_service(default_max_passes=2)
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph(confidence=0.0)})
    key = RunKey("proj", "sess", 1)
    rev_before = runs.raw_value(key)["revision"]
    passes_before = runs.raw_value(key)["passes"]
    r = result(evaluate(svc, h, intent="continue"))
    check("C4 intent=continue reports the block advisorily",
          r["type"] == "Block" and r["run"]["status"] == "active", f"got {r}")
    check("C5 an advisory evaluate writes no state and counts no pass",
          runs.raw_value(key)["revision"] == rev_before
          and runs.raw_value(key)["passes"] == passes_before,
          f"rev {runs.raw_value(key)['revision']} passes {runs.raw_value(key)['passes']}")


# --- residual / refuted / frozen --------------------------------------------


def test_blocked_residual_allows_non_converged():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph(blocked="needs-decision")})
    r = result(evaluate(svc, h))
    check("S1 a blocked residual allows a non-converged stop",
          r["type"] == "Allow" and r["converged"] is False
          and r["run"]["status"] == "stopped_residual", f"got {r}")


def test_refuted_root_allows_non_converged():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph(refuted_by="ref1")})
    observe(svc, h, {"kind": "evidence", "claim_id": "G0", "purpose": "refute", "ok": True,
                     "reason": "refuted by evidence"})
    r = result(evaluate(svc, h))
    check("S2 a refuted root allows a stop but never converges",
          r["type"] == "Allow" and r["converged"] is False
          and r["run"].get("root_refuted") is True, f"got {r}")


def test_frozen_run_defers_post_freeze_claims():
    """Inject a frozen run directly (freeze is not a wired action) to prove the service threads
    frozen_claims into adjudicate: G0 committed at freeze, G1 derived after -> deferred."""
    svc, runs, arts = make_service()
    h = handle_of(start(svc))
    key = RunKey("proj", "sess", 1)
    raw = {"root": "G0",
           "nodes": {"G0": {"type": "Goal", "text": "root", "confidence": 0.9},
                     "G1": {"type": "Goal", "text": "later", "confidence": 0.0}},
           "edges": [{"from": "G0", "to": "G1", "type": "SupportedBy"}]}
    canon = knowledge.canonicalize_graph(raw)
    observe(svc, h, {"kind": "graph", "graph": raw})
    observe(svc, h, approve_evidence("G0"))
    nonce = result(observe(svc, h, {"kind": "audit_ticket"}))["run"]["ticket"]["nonce"]
    observe(svc, h, passing_verdict(canon, "G0", nonce=nonce))
    # Freeze committed only G0.
    stored = runs.raw_value(key)
    stored["frozen_claims"] = ["G0"]
    r = result(evaluate(svc, h))
    check("S3 a frozen run defers post-freeze claims and closes non-converged",
          r["type"] == "Allow" and r["converged"] is False
          and r["run"]["status"] == "stopped_frozen"
          and r["run"].get("deferred") == ["G1"], f"got {r}")


# --- fail-closed: corrupt / stale pointer / absent ---------------------------


def test_corrupt_run_faults_closed():
    svc, runs, _ = make_service()
    h = handle_of(start(svc))
    runs.inject_corrupt(RunKey("proj", "sess", 1))
    rg = result(get(svc, h))
    re_ = result(evaluate(svc, h))
    check("F1 GetRun on a corrupt run faults closed",
          rg["type"] == "Fault" and rg["code"] == "corrupt_run"
          and rg["fail_direction"] == "closed", f"got {rg}")
    check("F2 EvaluateRun on a corrupt run faults closed",
          re_["type"] == "Fault" and re_["code"] == "corrupt_run", f"got {re_}")


def test_stale_pointer_faults_closed():
    svc, runs, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph()})
    # Corrupt the pointer to name an artifact that is not in the store.
    key = RunKey("proj", "sess", 1)
    stored = runs.raw_value(key)
    stored["claim_graph_artifact_id"] = "0" * 64
    r = result(evaluate(svc, h))
    check("F3 a run pointer to an absent graph artifact faults closed as corrupt_artifacts",
          r["type"] == "Fault" and r["code"] == "corrupt_artifacts"
          and r["fail_direction"] == "closed", f"got {r}")


def test_active_run_without_graph_faults_closed():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    r = result(evaluate(svc, h))  # active, but no graph observed yet
    check("F4 evaluating an active run with no graph faults closed",
          r["type"] == "Fault" and r["fail_direction"] == "closed", f"got {r}")


def test_absent_run_evaluate_is_inert():
    svc, _, _ = make_service()
    from application.wire import encode_handle
    ghost = encode_handle(RunKey("proj", "ghost", 1))
    r = result(evaluate(svc, ghost))
    check("F5 evaluating an absent run is Inert(no_run)",
          r["type"] == "Inert" and r["reason"] == "no_run", f"got {r}")


def test_observe_on_finished_run_conflicts():
    svc, _, _ = make_service()
    h, _ = drive_to_converged(svc)
    evaluate(svc, h)  # -> converged (finished)
    r = result(observe(svc, h, approve_evidence("G1")))
    check("F6 observing onto a finished run is refused as a conflict",
          r["type"] == "Fault" and r["code"] == "conflict", f"got {r}")


# --- spawn budget: reservation + configuration (ADR-17) ----------------------


def restore(svc, run_id):
    return svc.handle(req({"type": "RestoreRun", "run_id": run_id}))


def test_reserve_spawn_under_cap_counts():
    svc, runs, _ = make_service()
    h = handle_of(start(svc, max_spawns=2))
    r = result(observe(svc, h, {"kind": "reserve_spawn"}))
    check("B1 reserving under the cap allows and counts one spawn",
          r["type"] == "Allow" and r["run"]["spawn"]["reserved"] is True
          and r["run"]["spawn"]["spawns"] == 1 and r["run"]["spawn"]["remaining"] == 1,
          f"got {r}")
    check("B1b the reservation is persisted",
          runs.raw_value(RunKey("proj", "sess", 1))["spawns"] == 1)


def test_reserve_spawn_past_cap_blocks_without_counting():
    svc, runs, _ = make_service()
    h = handle_of(start(svc, max_spawns=1))
    observe(svc, h, {"kind": "reserve_spawn"})  # uses the only slot
    r = result(observe(svc, h, {"kind": "reserve_spawn"}))
    check("B2 reserving past the cap is a Block",
          r["type"] == "Block" and r["run"]["spawn"]["reserved"] is False, f"got {r}")
    check("B2b a denied spawn did not increment the counter",
          runs.raw_value(RunKey("proj", "sess", 1))["spawns"] == 1)


def test_reserve_spawn_unbounded_does_not_count():
    svc, runs, _ = make_service()
    h = handle_of(start(svc))  # no max_spawns -> unbounded
    r = result(observe(svc, h, {"kind": "reserve_spawn"}))
    check("B3 an unbounded budget reserves without counting",
          r["type"] == "Allow" and r["run"]["spawn"]["remaining"] is None
          and r["run"]["spawn"]["spawns"] == 0, f"got {r}")
    check("B3b the run's revision did not advance (no write for an unbounded reserve)",
          runs.raw_value(RunKey("proj", "sess", 1))["revision"] == 0)


def test_configure_budget_sets_cap():
    svc, runs, _ = make_service()
    h = handle_of(start(svc))  # starts unbounded
    observe(svc, h, {"kind": "configure_budget", "max_spawns": 1})
    check("B4 configuring a cap makes reservation enforce it",
          runs.raw_value(RunKey("proj", "sess", 1))["max_spawns"] == 1)
    observe(svc, h, {"kind": "reserve_spawn"})
    r = result(observe(svc, h, {"kind": "reserve_spawn"}))
    check("B4b the configured cap is enforced by later reservations",
          r["type"] == "Block", f"got {r}")


def test_configure_budget_rejects_bad_cap():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    r = result(observe(svc, h, {"kind": "configure_budget", "max_spawns": -1}))
    r2 = result(observe(svc, h, {"kind": "configure_budget", "max_spawns": True}))
    check("B5 a negative cap is refused as invalid_request",
          r["type"] == "Fault" and r["code"] == "invalid_request", f"got {r}")
    check("B5b a boolean cap is refused (bool is not a budget)",
          r2["type"] == "Fault" and r2["code"] == "invalid_request", f"got {r2}")


def test_reserve_spawn_race_never_exceeds_cap():
    """A lost CAS on a reservation must re-read and respect the cap a concurrent winner set — two
    racers against a cap of 1 spend exactly one slot, never two."""
    runs = FakeRunRepository()
    arts = FakeArtifactRepository()

    class OneShotReserveRace:
        """The FIRST reservation CAS loses to a concurrent writer that itself reserved the only
        slot; the service must re-read (now at the cap) and deny rather than double-spend."""

        def __init__(self, inner):
            self._inner = inner
            self._tripped = False

        def read(self, key):
            return self._inner.read(key)

        def create(self, key, value):
            return self._inner.create(key, value)

        def generations(self, p, r):
            return self._inner.generations(p, r)

        def raw_value(self, key):
            return self._inner.raw_value(key)

        def compare_and_set(self, key, value, expected):
            if not self._tripped:
                self._tripped = True
                cur = self._inner.read(key)
                won = dict(cur.value)
                won["spawns"] = won.get("spawns", 0) + 1  # a concurrent winner took the slot
                self._inner.compare_and_set(key, won, cur.revision)
                raise Conflict(key, expected, "a concurrent reservation won the race")
            return self._inner.compare_and_set(key, value, expected)

    wrapped = OneShotReserveRace(runs)
    svc = EmpiricaService(wrapped, arts, FakeGenerationAllocator(runs))
    h = handle_of(start(svc, max_spawns=1))
    r = result(observe(svc, h, {"kind": "reserve_spawn"}))
    key = RunKey("proj", "sess", 1)
    check("B6 a lost reservation CAS re-reads and denies at the cap",
          r["type"] == "Block", f"got {r}")
    check("B6b the cap was never exceeded (exactly one slot spent)",
          runs.raw_value(key)["spawns"] == 1, f"spawns={runs.raw_value(key)['spawns']}")


# --- phase machine (ADR-21 M1) ----------------------------------------------


def test_phase_advances_monotonically():
    svc, runs, _ = make_service()
    h = handle_of(start(svc))
    check("P0 a fresh run starts in the route phase",
          runs.raw_value(RunKey("proj", "sess", 1))["phase"] == "route")
    r = result(observe(svc, h, {"kind": "phase", "phase": "resolve"}))
    check("P1 a forward phase transition is applied and reported",
          r["type"] == "Allow" and r["run"]["phase"] == "resolve", f"got {r}")
    observe(svc, h, {"kind": "phase", "phase": "audit"})  # skipping ahead is still forward
    check("P1b a forward skip is allowed",
          runs.raw_value(RunKey("proj", "sess", 1))["phase"] == "audit")


def test_phase_regression_is_refused():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "phase", "phase": "assess"})
    r = result(observe(svc, h, {"kind": "phase", "phase": "route"}))
    check("P2 a backward phase transition faults as a conflict",
          r["type"] == "Fault" and r["code"] == "conflict", f"got {r}")


def test_phase_unknown_value_is_invalid():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    r = result(observe(svc, h, {"kind": "phase", "phase": "nonsense"}))
    check("P3 an unknown phase is refused as invalid_request",
          r["type"] == "Fault" and r["code"] == "invalid_request", f"got {r}")


def test_phase_self_transition_is_noop():
    svc, runs, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "phase", "phase": "resolve"})
    rev = runs.raw_value(RunKey("proj", "sess", 1))["revision"]
    r = result(observe(svc, h, {"kind": "phase", "phase": "resolve"}))
    check("P4 a self-transition is an allowed no-op that writes nothing",
          r["type"] == "Allow"
          and runs.raw_value(RunKey("proj", "sess", 1))["revision"] == rev, f"got {r}")


# --- P1 ordering: first-write-wins route + first investigation ---------------


def test_route_before_investigation_is_ok():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "route", "reason": "deps classified"})
    r = result(observe(svc, h, {"kind": "investigate"}))
    check("O1 route announced before investigation → P1 ok",
          r["run"]["route"]["verdict"] == "ok", f"got {r}")


def test_investigation_before_route_is_violation():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "investigate"})
    observe(svc, h, {"kind": "route"})
    snap = result(restore(svc, h))["run"]["snapshot"]
    check("O2 investigation before any route → P1 violation (derived from write order)",
          snap["route"]["verdict"] == "violation", f"got {snap['route']}")


def test_route_is_first_write_wins():
    svc, runs, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "route", "reason": "first"})
    seq = runs.raw_value(RunKey("proj", "sess", 1))["route_seq"]
    rev = runs.raw_value(RunKey("proj", "sess", 1))["revision"]
    r = result(observe(svc, h, {"kind": "route", "reason": "second"}))
    stored = runs.raw_value(RunKey("proj", "sess", 1))
    check("O3 a second route is a no-op — the first write wins",
          r["type"] == "Allow" and stored["route_seq"] == seq
          and stored["route_reason"] == "first" and stored["revision"] == rev, f"got {stored}")


def test_route_ordering_race_first_write_wins():
    """Two route stamps racing: the loser re-reads, sees a route already recorded, and no-ops —
    the earliest write's sequence position stands."""
    runs = FakeRunRepository()
    arts = FakeArtifactRepository()

    class OneShotRouteRace:
        def __init__(self, inner):
            self._inner = inner
            self._tripped = False

        def read(self, key):
            return self._inner.read(key)

        def create(self, key, value):
            return self._inner.create(key, value)

        def generations(self, p, r):
            return self._inner.generations(p, r)

        def raw_value(self, key):
            return self._inner.raw_value(key)

        def compare_and_set(self, key, value, expected):
            if not self._tripped:
                self._tripped = True
                cur = self._inner.read(key)
                won = dict(cur.value)
                won["route_seq"] = won.get("stamp_seq", 0) + 1
                won["stamp_seq"] = won["route_seq"]
                won["route_reason"] = "winner"
                self._inner.compare_and_set(key, won, cur.revision)
                raise Conflict(key, expected, "a concurrent route won")
            return self._inner.compare_and_set(key, value, expected)

    wrapped = OneShotRouteRace(runs)
    svc = EmpiricaService(wrapped, arts, FakeGenerationAllocator(runs))
    h = handle_of(start(svc))
    r = result(observe(svc, h, {"kind": "route", "reason": "loser"}))
    stored = runs.raw_value(RunKey("proj", "sess", 1))
    check("O4 a lost route CAS re-reads and defers to the winner (first write wins)",
          r["type"] == "Allow" and stored["route_reason"] == "winner", f"got {stored}")


# --- modes (ADR-24/28) -------------------------------------------------------


def test_set_modes_merges():
    svc, runs, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "mode", "modes": {"cli_exec": True}})
    r = result(observe(svc, h, {"kind": "mode", "modes": {"multi_provider": True}}))
    check("M1 setting a mode merges rather than clearing the other",
          r["run"]["modes"] == {"cli_exec": True, "multi_provider": True}, f"got {r}")


def test_set_modes_rejects_unknown_and_non_bool():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    r = result(observe(svc, h, {"kind": "mode", "modes": {"cli_exex": True}}))
    r2 = result(observe(svc, h, {"kind": "mode", "modes": {"cli_exec": "yes"}}))
    check("M2 an unknown mode key is refused (a typo cannot look enabled)",
          r["type"] == "Fault" and r["code"] == "invalid_request", f"got {r}")
    check("M3 a non-boolean mode value is refused",
          r2["type"] == "Fault" and r2["code"] == "invalid_request", f"got {r2}")


# --- freeze (ADR-26) ---------------------------------------------------------


def test_freeze_commits_scope_first_write_wins():
    svc, runs, _ = make_service()
    h = handle_of(start(svc))
    r = result(observe(svc, h, {"kind": "freeze", "claims": ["G1", "G0", "G0"]}))
    check("FZ1 freeze commits the deduplicated, sorted scope",
          r["run"]["frozen_claims"] == ["G0", "G1"], f"got {r}")
    stored = runs.raw_value(RunKey("proj", "sess", 1))
    rev = stored["revision"]
    r2 = result(observe(svc, h, {"kind": "freeze", "claims": ["G2"]}))
    stored2 = runs.raw_value(RunKey("proj", "sess", 1))
    check("FZ2 a re-freeze is a no-op — the first commitment stands",
          r2["type"] == "Allow" and stored2["frozen_claims"] == ["G0", "G1"]
          and stored2["revision"] == rev, f"got {stored2}")


# --- actor dispatch attribution (ADR-24) -------------------------------------


def test_dispatch_records_declared_attribution():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    r = result(observe(svc, h, {"kind": "dispatch",
                                "actor": {"model": "claude-opus-4-8"}, "claim_id": "G0"}))
    d = r["run"]["dispatch"]
    check("D1 a dispatch records a declared actor by default",
          d["actor"]["model"] == "claude-opus-4-8" and d["actor"]["attribution"] == "declared"
          and d["claim_id"] == "G0", f"got {d}")


def test_dispatch_witnessed_flag():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    r = result(observe(svc, h, {"kind": "dispatch", "witnessed": True,
                                "actor": {"model": "gpt-5.6-sol", "attribution": "declared"}}))
    check("D2 a witnessed dispatch is recorded witnessed (the dispatcher invoked it)",
          r["run"]["dispatch"]["actor"]["attribution"] == "witnessed", f"got {r['run']['dispatch']}")


def test_dispatch_in_session_cannot_claim_witnessed():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    r = result(observe(svc, h, {"kind": "dispatch",
                                "actor": {"model": "gpt-5.6-sol", "attribution": "witnessed"}}))
    check("D3 an in-session dispatch claiming witnessed is forced to declared",
          r["run"]["dispatch"]["actor"]["attribution"] == "declared", f"got {r['run']['dispatch']}")


def test_dispatch_rejects_policy_excluded_model():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    r = result(observe(svc, h, {"kind": "dispatch", "actor": {"model": "claude-fable-5"}}))
    check("D4 a policy-excluded model is refused as invalid_request",
          r["type"] == "Fault" and r["code"] == "invalid_request", f"got {r}")


# --- audit ticket issue / consume (ADR-20 P6, ADR-31) ------------------------


def test_audit_ticket_issue_mints_deterministic_nonce():
    from application.service import _mint_nonce
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    r = result(observe(svc, h, {"kind": "audit_ticket"}))
    tk = r["run"]["ticket"]
    check("A1 issuing a ticket mints the derived per-spawn nonce",
          tk["seq"] == 1 and tk["nonce"] == _mint_nonce(RunKey("proj", "sess", 1), 1), f"got {tk}")


def test_audit_ticket_issue_increments_seq():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    n1 = result(observe(svc, h, {"kind": "audit_ticket"}))["run"]["ticket"]
    n2 = result(observe(svc, h, {"kind": "audit_ticket"}))["run"]["ticket"]
    check("A2 a second issue gets a distinct nonce at the next ordinal",
          n2["seq"] == 2 and n2["nonce"] != n1["nonce"], f"got {n1} {n2}")


def test_audit_ticket_consume_is_idempotent():
    svc, runs, _ = make_service()
    h = handle_of(start(svc))
    nonce = result(observe(svc, h, {"kind": "audit_ticket"}))["run"]["ticket"]["nonce"]
    observe(svc, h, {"kind": "consume_audit_ticket", "nonce": nonce})
    tickets = runs.raw_value(RunKey("proj", "sess", 1))["audit_tickets"]
    check("A3 consuming a ticket marks it consumed",
          tickets[0]["consumed"] is True, f"got {tickets}")
    rev = runs.raw_value(RunKey("proj", "sess", 1))["revision"]
    r2 = result(observe(svc, h, {"kind": "consume_audit_ticket", "nonce": nonce}))
    check("A3b consuming again is an idempotent no-op",
          r2["type"] == "Allow"
          and runs.raw_value(RunKey("proj", "sess", 1))["revision"] == rev, f"got {r2}")


def test_audit_ticket_consume_unknown_nonce():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    r = result(observe(svc, h, {"kind": "consume_audit_ticket", "nonce": "deadbeef"}))
    check("A4 consuming an unknown nonce faults as invalid_request",
          r["type"] == "Fault" and r["code"] == "invalid_request", f"got {r}")


# --- terminal-run fail-open for unrelated actions ----------------------------


def test_reserve_spawn_on_terminal_run_fails_open():
    svc, _, _ = make_service(default_max_passes=1)
    h = handle_of(start(svc, max_spawns=1))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph(confidence=0.0)})
    evaluate(svc, h)  # -> stopped_budget (terminal)
    r = result(observe(svc, h, {"kind": "reserve_spawn"}))
    check("TF1 reserving a spawn on a terminal run fails OPEN (nothing left to gate)",
          r["type"] == "Allow" and r["run"]["spawn"]["reserved"] is True, f"got {r}")


def test_lifecycle_action_on_terminal_run_faults_closed():
    svc, _, _ = make_service(default_max_passes=1)
    h = handle_of(start(svc, max_spawns=1))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph(confidence=0.0)})
    evaluate(svc, h)  # -> stopped_budget (terminal)
    r = result(observe(svc, h, {"kind": "phase", "phase": "audit"}))
    r2 = result(observe(svc, h, {"kind": "configure_budget", "max_spawns": 9}))
    check("TF2 a lifecycle transition on a terminal run faults closed (conflict)",
          r["type"] == "Fault" and r["code"] == "conflict", f"got {r}")
    check("TF2b configuring a budget on a terminal run faults closed",
          r2["type"] == "Fault" and r2["code"] == "conflict", f"got {r2}")


# --- RestoreRun snapshot (ADR-31) --------------------------------------------


def test_restore_returns_operational_snapshot():
    svc, _, _ = make_service()
    h = handle_of(start(svc, max_spawns=3))
    observe(svc, h, {"kind": "phase", "phase": "resolve"})
    observe(svc, h, {"kind": "mode", "modes": {"cli_exec": True}})
    snap = result(restore(svc, h))["run"]["snapshot"]
    check("RS1 RestoreRun returns the operational plane (phase, budget, modes, route)",
          snap["phase"] == "resolve" and snap["spawn"]["max_spawns"] == 3
          and snap["modes"] == {"cli_exec": True} and "route" in snap
          and snap["has_graph"] is False, f"got {snap}")


def test_restore_includes_graph_view():
    svc, _, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph(confidence=0.0)})
    snap = result(restore(svc, h))["run"]["snapshot"]
    check("RS2 RestoreRun reports the claim-graph resume counts when a graph exists",
          snap["has_graph"] is True and snap["graph"]["gating"] == 1
          and snap["graph"]["open"] == 1, f"got {snap.get('graph')}")


def test_restore_absent_is_inert():
    svc, _, _ = make_service()
    from application.wire import encode_handle
    ghost = encode_handle(RunKey("proj", "ghost", 1))
    r = result(restore(svc, ghost))
    check("RS3 RestoreRun on an absent run is Inert(no_run)",
          r["type"] == "Inert" and r["reason"] == "no_run", f"got {r}")


def test_restore_corrupt_faults_closed():
    svc, runs, _ = make_service()
    h = handle_of(start(svc))
    runs.inject_corrupt(RunKey("proj", "sess", 1))
    r = result(restore(svc, h))
    check("RS4 RestoreRun on a corrupt run faults closed",
          r["type"] == "Fault" and r["code"] == "corrupt_run"
          and r["fail_direction"] == "closed", f"got {r}")


def test_restore_stale_pointer_faults_closed():
    svc, runs, _ = make_service()
    h = handle_of(start(svc))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph()})
    runs.raw_value(RunKey("proj", "sess", 1))["claim_graph_artifact_id"] = "0" * 64
    r = result(restore(svc, h))
    check("RS5 RestoreRun with a stale graph pointer faults closed (corrupt_artifacts)",
          r["type"] == "Fault" and r["code"] == "corrupt_artifacts", f"got {r}")


# --- corruption fails closed (decode strictness) -----------------------------


def _corrupt_field(svc, runs, **overrides):
    """Start a run, then poke a load-bearing field to a corrupt value in the stored document."""
    h = handle_of(start(svc))
    stored = runs.raw_value(RunKey("proj", "sess", 1))
    stored.update(overrides)
    return h


def test_corrupt_theta_faults_closed_not_raises():
    svc, runs, _ = make_service()
    h = _corrupt_field(svc, runs, theta=[])  # non-numeric theta once crashed float()
    r = result(get(svc, h))  # must return a Fault envelope, never raise
    check("X1 a non-numeric theta faults closed as corrupt_run (no crash through the wire)",
          r["type"] == "Fault" and r["code"] == "corrupt_run", f"got {r}")


def test_non_finite_theta_faults_closed():
    svc, runs, _ = make_service()
    h = _corrupt_field(svc, runs, theta=float("inf"))
    r = result(evaluate(svc, h))
    check("X2 a non-finite theta faults closed",
          r["type"] == "Fault" and r["code"] == "corrupt_run", f"got {r}")


def test_unknown_status_faults_closed_not_fail_open():
    """An unknown status must NOT read as terminal (which would fail a reserve OPEN from corrupt
    state) — it must fault closed."""
    svc, runs, _ = make_service()
    h = _corrupt_field(svc, runs, status="totally-made-up")
    r = result(observe(svc, h, {"kind": "reserve_spawn"}))
    check("X3 an unknown status faults closed (never a terminal fail-open)",
          r["type"] == "Fault" and r["code"] == "corrupt_run", f"got {r}")


def test_non_positive_max_passes_faults_closed():
    svc, runs, _ = make_service()
    h = _corrupt_field(svc, runs, max_passes=0)
    r = result(get(svc, h))
    check("X4 a non-positive max_passes faults closed",
          r["type"] == "Fault" and r["code"] == "corrupt_run", f"got {r}")


def test_malformed_verdict_blocks_not_crashes():
    """A stored verdict whose claims_reviewed carries a non-dict entry once crashed coverage_check;
    it must instead fail the run closed (a Block), never raise through the wire."""
    svc, _, arts = make_service()
    h = handle_of(start(svc))
    raw = single_goal_graph()
    observe(svc, h, {"kind": "graph", "graph": raw})
    observe(svc, h, approve_evidence())
    nonce = result(observe(svc, h, {"kind": "audit_ticket"}))["run"]["ticket"]["nonce"]
    # Inject a structurally broken verdict artifact directly into the knowledge store.
    from application.service import knowledge_artifact
    from application.knowledge import audit_verdict_artifact
    art_id, body = audit_verdict_artifact({
        "verdict": "pass", "nonce": nonce, "argument_digest": None,
        "claims_reviewed": [1, {"claim_id": "G0"}], "findings": [None]})
    arts.append(RunKey("proj", "sess", 1), knowledge_artifact(art_id, body))
    r = result(evaluate(svc, h))
    check("X5 a malformed stored verdict fails closed (Block), it does not crash the wire",
          r["type"] == "Block", f"got {r}")


def test_handle_rejects_boolean_and_negative_generation():
    import base64
    import json as _json
    from application.wire import decode_handle, InvalidRequest
    bad_bool = base64.urlsafe_b64encode(
        _json.dumps({"p": "proj", "r": "sess", "g": True}).encode()).decode()
    bad_neg = base64.urlsafe_b64encode(
        _json.dumps({"p": "proj", "r": "sess", "g": -1}).encode()).decode()
    ok_bool = ok_neg = False
    try:
        decode_handle(bad_bool)
    except InvalidRequest:
        ok_bool = True
    try:
        decode_handle(bad_neg)
    except InvalidRequest:
        ok_neg = True
    check("X6 a boolean generation in a handle is rejected (bool is not an int gen)", ok_bool)
    check("X7 a negative generation in a handle is rejected", ok_neg)


def test_configure_and_mode_idempotency_no_churn():
    svc, runs, _ = make_service()
    h = handle_of(start(svc, max_spawns=2))
    observe(svc, h, {"kind": "mode", "modes": {"cli_exec": True}})
    key = RunKey("proj", "sess", 1)
    rev = runs.raw_value(key)["revision"]
    observe(svc, h, {"kind": "configure_budget", "max_spawns": 2})  # same cap
    observe(svc, h, {"kind": "mode", "modes": {"cli_exec": True}})   # same mode
    check("X8 re-configuring the same cap / re-setting the same mode writes nothing",
          runs.raw_value(key)["revision"] == rev, f"revision moved to {runs.raw_value(key)['revision']}")


# --- more concurrent races (issue, phase, investigation) ---------------------


class _OneShotConflict:
    """Wrap a run repo so the FIRST compare_and_set loses to a concurrent winner that applies
    ``winner_patch`` to the stored value, then raises Conflict. The service must re-read and retry."""

    def __init__(self, inner, winner_patch):
        self._inner = inner
        self._patch = winner_patch
        self._tripped = False

    def read(self, key):
        return self._inner.read(key)

    def create(self, key, value):
        return self._inner.create(key, value)

    def generations(self, p, r):
        return self._inner.generations(p, r)

    def raw_value(self, key):
        return self._inner.raw_value(key)

    def compare_and_set(self, key, value, expected):
        if not self._tripped:
            self._tripped = True
            cur = self._inner.read(key)
            won = dict(cur.value)
            self._patch(won)
            self._inner.compare_and_set(key, won, cur.revision)
            raise Conflict(key, expected, "a concurrent writer won the race")
        return self._inner.compare_and_set(key, value, expected)


def test_audit_ticket_issue_race_distinct_nonces():
    """Two concurrent issues both compute seq=1; a lost CAS must re-read (len=1) and re-mint seq=2,
    so no two live tickets ever share a nonce or ordinal."""
    from application.service import _mint_nonce
    runs = FakeRunRepository()
    arts = FakeArtifactRepository()

    def winner(doc):
        seq = len(doc.get("audit_tickets", [])) + 1
        doc.setdefault("audit_tickets", []).append(
            {"nonce": _mint_nonce(RunKey("proj", "sess", 1), seq), "seq": seq, "consumed": False})

    wrapped = _OneShotConflict(runs, winner)
    svc = EmpiricaService(wrapped, arts, FakeGenerationAllocator(runs))
    h = handle_of(start(svc))
    tk = result(observe(svc, h, {"kind": "audit_ticket"}))["run"]["ticket"]
    stored = runs.raw_value(RunKey("proj", "sess", 1))["audit_tickets"]
    nonces = {t["nonce"] for t in stored}
    check("Y1 a lost issue CAS re-reads and mints the next ordinal (no nonce/seq collision)",
          tk["seq"] == 2 and len(stored) == 2 and len(nonces) == 2, f"got {tk} stored={stored}")


def test_phase_transition_race_retries():
    runs = FakeRunRepository()
    arts = FakeArtifactRepository()
    wrapped = _OneShotConflict(runs, lambda doc: doc.update({"phase": "resolve"}))
    svc = EmpiricaService(wrapped, arts, FakeGenerationAllocator(runs))
    h = handle_of(start(svc))
    r = result(observe(svc, h, {"kind": "phase", "phase": "audit"}))
    check("Y2 a lost phase CAS re-reads and still advances monotonically",
          r["type"] == "Allow" and r["run"]["phase"] == "audit"
          and runs.raw_value(RunKey("proj", "sess", 1))["phase"] == "audit", f"got {r}")


def test_first_investigation_first_write_wins_race():
    runs = FakeRunRepository()
    arts = FakeArtifactRepository()

    def winner(doc):
        seq = doc.get("stamp_seq", 0) + 1
        doc["first_investigation_seq"] = seq
        doc["stamp_seq"] = seq

    wrapped = _OneShotConflict(runs, winner)
    svc = EmpiricaService(wrapped, arts, FakeGenerationAllocator(runs))
    h = handle_of(start(svc))
    r = result(observe(svc, h, {"kind": "investigate"}))
    stored = runs.raw_value(RunKey("proj", "sess", 1))
    check("Y3 a lost first-investigation CAS defers to the winner (first write wins, one stamp)",
          r["type"] == "Allow" and stored["first_investigation_seq"] == 1
          and stored["stamp_seq"] == 1, f"got {stored}")


def test_evaluate_retries_on_concurrent_write():
    """A benign concurrent operational write between EvaluateRun's read and its finalize CAS must be
    retried (re-read + re-adjudicate), not turned into a spurious conflict."""
    runs = FakeRunRepository()
    arts = FakeArtifactRepository()
    wrapped = _OneShotConflict(runs, lambda doc: doc.update({"spawns": doc.get("spawns", 0) + 1}))
    svc = EmpiricaService(wrapped, arts, FakeGenerationAllocator(runs))
    h = handle_of(start(svc, max_spawns=5))
    observe(svc, h, {"kind": "graph", "graph": single_goal_graph(blocked="needs-decision")})
    r = result(evaluate(svc, h))
    check("Y4 EvaluateRun retries through a lost finalize CAS and still returns its decision",
          r["type"] == "Allow" and r["run"]["status"] == "stopped_residual", f"got {r}")


# --- wire hardening ----------------------------------------------------------


def test_invalid_requests_fault():
    svc, _, _ = make_service()
    bad_protocol = svc.handle({"protocol": "empirica/v0", "request_id": "x",
                               "command": {"type": "GetRun", "run_id": "z"}})
    missing_field = svc.handle(req({"type": "GetRun"}))
    bad_handle = result(get(svc, "not-a-handle"))
    check("W1 an unsupported protocol faults as invalid_request",
          result(bad_protocol)["type"] == "Fault"
          and result(bad_protocol)["code"] == "invalid_request", f"got {bad_protocol}")
    check("W2 a missing required field faults as invalid_request",
          result(missing_field)["type"] == "Fault"
          and result(missing_field)["code"] == "invalid_request", f"got {missing_field}")
    check("W3 a malformed run handle faults as invalid_request",
          bad_handle["type"] == "Fault" and bad_handle["code"] == "invalid_request",
          f"got {bad_handle}")
    check("W4 every response echoes the request_id",
          bad_protocol["request_id"] == "x" and missing_field["request_id"] == "req-1")


def main() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        mark = "ok  " if ok else "FAIL"
        line = f"  [{mark}] {name}"
        if not ok and detail:
            line += f"  — {detail}"
        print(line)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
