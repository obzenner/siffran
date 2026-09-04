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
    handle and the canonical graph."""
    h = handle_of(start(svc, **start_kw))
    raw = single_goal_graph()
    canon = knowledge.canonicalize_graph(raw)
    observe(svc, h, {"kind": "graph", "graph": raw})
    observe(svc, h, approve_evidence())
    observe(svc, h, {"kind": "audit_ticket", "nonce": "n1"})
    observe(svc, h, passing_verdict(canon))
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
    observe(svc, h, {"kind": "audit_ticket", "nonce": "n1"})
    observe(svc, h, passing_verdict(canon, "G0", nonce="n1"))
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
