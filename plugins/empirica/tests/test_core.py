#!/usr/bin/env python3
"""Focused regression suite for the host-neutral decision core (ADR-30, `plugins/empirica/core`).

Run: python3 plugins/empirica/tests/test_core.py   (stdlib only, no pytest dependency)
Exit 0 = all pass; 1 = at least one failed.

These pin the SUBSTANTIVE decision — Allow / Block / Inert / Fault — independently of any host: no
subprocess, no filesystem, no manifest. Every input is constructed in-memory and every oracle is a
stub, which is the whole point of the extraction. Behaviour is checked against the semantics of
`hooks/convergence_gate.main`; a full end-to-end fidelity check against the live gate stays in
the active core/application tests.
"""
import importlib
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).parent
PLUGIN = HERE.parent  # plugins/empirica — makes `core` importable as a package
sys.path.insert(0, str(PLUGIN))

from core import Allow, Block, Fault, Inert, RunState, adjudicate, claims  # noqa: E402
from core.audit import coverage_check  # noqa: E402

THETA = 0.8


# --- graph builders -----------------------------------------------------------

def _node(ntype="Goal", text="claim", confidence=0.0, kind=None, blocked=None,
          refuted_by=None):
    return {"type": ntype, "text": text, "kind": kind, "confidence": confidence,
            "blocked": blocked, "refuted_by": refuted_by, "evidence": []}


def _graph(nodes, edges=None, root="G0"):
    return {"root": root, "nodes": nodes, "edges": edges or []}


def _edge(src, dst, etype="SupportedBy"):
    return {"from": src, "to": dst, "type": etype}


def _approves(*approved_ids):
    """An evidence oracle that approves exactly the given ids (and refutes none)."""
    def evidence(nid, purpose):
        if purpose == "approve":
            return (nid in approved_ids, "Fold 1 + Fold 2 satisfied" if nid in approved_ids
                    else "FOLD 1 MISSING: no research record cites a source")
        return (False, "cannot discard: no evidence refutes this claim")
    return evidence


def _refutes(*refuted_ids):
    def evidence(nid, purpose):
        if purpose == "refute":
            return (nid in refuted_ids, "refuted by research evidence")
        return (False, "FOLD 1 MISSING")
    return evidence


def _pass_audit(recorder=None):
    def audit(approved_digests, argument_digest):
        if recorder is not None:
            recorder["approved"] = approved_digests
            recorder["argument"] = argument_digest
        return (True, "independent audit passed")
    return audit


def _fail_audit(reason="the independent audit FAILED: no findings recorded"):
    return lambda approved_digests, argument_digest: (False, reason)


def _digest_of(nid):
    return {"claim_digest": "a" * 64, "evidence_digest": "b" * 64}


# --- identity / fail matrix ---------------------------------------------------

class IdentityMatrix(unittest.TestCase):
    def test_no_run_is_inert(self):
        self.assertIsInstance(adjudicate(run=None, graph=None, theta=THETA), Inert)

    def test_corrupt_manifest_faults(self):
        run = RunState(status="__corrupt__")
        self.assertIsInstance(adjudicate(run=run, graph=None, theta=THETA), Fault)

    def test_finished_converged_run_allows_converged(self):
        d = adjudicate(run=RunState(status="converged"), graph=None, theta=THETA)
        self.assertIsInstance(d, Allow)
        self.assertTrue(d.converged)
        self.assertEqual(d.status, "finished")

    def test_finished_residual_run_allows_not_converged(self):
        d = adjudicate(run=RunState(status="stopped_residual"), graph=None, theta=THETA)
        self.assertIsInstance(d, Allow)
        self.assertFalse(d.converged)

    def test_legacy_run_without_graph_allows_not_converged(self):
        run = RunState(status="active", is_legacy=True)
        d = adjudicate(run=run, graph=None, theta=THETA)
        self.assertIsInstance(d, Allow)
        self.assertEqual(d.status, "legacy")
        self.assertFalse(d.converged)

    def test_active_run_missing_graph_faults(self):
        d = adjudicate(run=RunState(status="active"), graph=None, theta=THETA)
        self.assertIsInstance(d, Fault)

    def test_active_run_corrupt_graph_faults(self):
        d = adjudicate(run=RunState(status="active"), graph=claims.CORRUPT, theta=THETA)
        self.assertIsInstance(d, Fault)


# --- converging / converged ---------------------------------------------------

class Convergence(unittest.TestCase):
    def _active(self, **kw):
        return RunState(status="active", **kw)

    def test_open_claim_blocks_and_reports_the_missing_fold(self):
        g = _graph({"G0": _node(text="the intent", confidence=0.0)})
        d = adjudicate(run=self._active(), graph=g, theta=THETA,
                       evidence=_approves(), audit=_pass_audit(), digest_of=_digest_of)
        self.assertIsInstance(d, Block)
        self.assertEqual(d.kind, "converging")
        self.assertEqual(len(d.open_claims), 1)
        self.assertEqual(d.open_claims[0].claim_id, "G0")
        self.assertIn("FOLD 1", d.open_claims[0].reason)

    def test_converged_graph_with_passing_audit_allows_converged(self):
        g = _graph({"G0": _node(confidence=0.9)})
        recorder = {}
        d = adjudicate(run=self._active(), graph=g, theta=THETA,
                       evidence=_approves("G0"), audit=_pass_audit(recorder),
                       digest_of=_digest_of)
        self.assertIsInstance(d, Allow)
        self.assertTrue(d.converged)
        self.assertEqual(d.status, "converged")
        self.assertEqual(d.audit, "passed")
        # the audit was consulted with the approved claim's digests and the argument shape
        self.assertIn("G0", recorder["approved"])
        self.assertEqual(recorder["argument"], claims.argument_digest(g))

    def test_converged_graph_with_failing_audit_blocks(self):
        g = _graph({"G0": _node(confidence=0.9)})
        d = adjudicate(run=self._active(), graph=g, theta=THETA,
                       evidence=_approves("G0"), audit=_fail_audit(), digest_of=_digest_of)
        self.assertIsInstance(d, Block)
        self.assertEqual(d.kind, "audit_failed")
        self.assertIn("FAILED", d.audit_reason)

    def test_no_audit_oracle_never_converges(self):
        g = _graph({"G0": _node(confidence=0.9)})
        d = adjudicate(run=self._active(), graph=g, theta=THETA,
                       evidence=_approves("G0"), audit=None, digest_of=_digest_of)
        self.assertIsInstance(d, Block)
        self.assertEqual(d.kind, "audit_failed")


# --- residuals, refutation, freeze -------------------------------------------

class Residuals(unittest.TestCase):
    def _active(self, **kw):
        return RunState(status="active", **kw)

    def test_blocked_residual_allows_without_owing_an_audit(self):
        g = _graph({"G0": _node(blocked="needs-decision")})
        # audit oracle would raise if called — a blocked residual owes no audit.
        def boom(*_):
            raise AssertionError("audit must not be consulted for a blocked residual")
        d = adjudicate(run=self._active(), graph=g, theta=THETA,
                       evidence=_approves(), audit=boom, digest_of=_digest_of)
        self.assertIsInstance(d, Allow)
        self.assertFalse(d.converged)
        self.assertEqual(d.status, "stopped_residual")
        self.assertIsNone(d.audit)
        self.assertEqual(d.blocked, ("G0",))

    def test_budget_blocked_is_reported_as_non_converged(self):
        g = _graph({"G0": _node(blocked="needs-budget")})
        d = adjudicate(run=self._active(), graph=g, theta=THETA,
                       evidence=_approves(), audit=_pass_audit(), digest_of=_digest_of)
        self.assertIsInstance(d, Allow)
        self.assertFalse(d.converged)
        self.assertEqual(d.budget_blocked, ("G0",))
        self.assertIn("budget", d.note.lower())

    def test_refuted_root_allows_but_never_converges(self):
        g = _graph({"G0": _node(refuted_by="ref1")})
        d = adjudicate(run=self._active(), graph=g, theta=THETA,
                       evidence=_refutes("G0"), audit=_pass_audit(), digest_of=_digest_of)
        self.assertIsInstance(d, Allow)
        self.assertFalse(d.converged)
        self.assertTrue(d.root_refuted)

    def test_frozen_run_defers_post_freeze_claims_and_can_close(self):
        # G0 committed at freeze; G1 derived after → deferred. G0 approved, audit passes.
        g = _graph(
            {"G0": _node(text="root", confidence=0.9),
             "G1": _node(text="later", confidence=0.0)},
            edges=[_edge("G0", "G1")],
        )
        d = adjudicate(run=self._active(frozen_claims=("G0",)), graph=g, theta=THETA,
                       evidence=_approves("G0"), audit=_pass_audit(), digest_of=_digest_of)
        self.assertIsInstance(d, Allow)
        self.assertFalse(d.converged)
        self.assertEqual(d.status, "stopped_frozen")
        self.assertEqual(d.deferred, ("G1",))


# --- P1 / attribution report on the allow path --------------------------------

class ReportOnAllow(unittest.TestCase):
    def test_p1_violation_is_reported_not_blocked(self):
        g = _graph({"G0": _node(confidence=0.9)})
        d = adjudicate(run=RunState(status="active"), graph=g, theta=THETA,
                       evidence=_approves("G0"), audit=_pass_audit(), digest_of=_digest_of,
                       route_verdict=("violation", "route announced after investigation"))
        self.assertIsInstance(d, Allow)
        self.assertTrue(d.converged)  # P1 is reported, never blocks a passing audit
        self.assertEqual(d.p1_violation, "route announced after investigation")
        self.assertIn("P1", d.note)

    def test_vacuous_attribution_is_attached(self):
        g = _graph({"G0": _node(confidence=0.9)})
        attr = {"findings": [], "coverage": {"vacuous": True}, "note": "could not check"}
        d = adjudicate(run=RunState(status="active"), graph=g, theta=THETA,
                       evidence=_approves("G0"), audit=_pass_audit(), digest_of=_digest_of,
                       attribution=attr)
        self.assertIsInstance(d, Allow)
        self.assertEqual(d.attribution, attr)

    def test_clean_attribution_is_not_attached(self):
        g = _graph({"G0": _node(confidence=0.9)})
        attr = {"findings": [], "coverage": {"vacuous": False}, "note": "no clash"}
        d = adjudicate(run=RunState(status="active"), graph=g, theta=THETA,
                       evidence=_approves("G0"), audit=_pass_audit(), digest_of=_digest_of,
                       attribution=attr)
        self.assertIsNone(d.attribution)


# --- claim-state derivation (the anti-forgery core) ---------------------------

class ClaimState(unittest.TestCase):
    # claims.state_of takes a BOOL-returning oracle (the (ok, reason) tuple form is the
    # adjudicate-level injection, unwrapped by adjudicate before it reaches here).
    @staticmethod
    def _ok(*approve_ids):
        return lambda nid, purpose: purpose == "approve" and nid in approve_ids

    def test_forged_state_field_is_ignored(self):
        # A node "claiming" it is approved by carrying a state field, with no evidence, stays open.
        node = _node(confidence=0.99)
        node["state"] = "approved"
        g = _graph({"G0": node})
        self.assertEqual(claims.state_of(g, "G0", THETA, self._ok()), claims.STATE_OPEN)

    def test_confidence_without_evidence_stays_open(self):
        g = _graph({"G0": _node(confidence=1.0)})
        self.assertEqual(claims.state_of(g, "G0", THETA, self._ok()), claims.STATE_OPEN)

    def test_high_confidence_with_evidence_approves(self):
        g = _graph({"G0": _node(confidence=0.9)})
        self.assertEqual(claims.state_of(g, "G0", THETA, self._ok("G0")),
                         claims.STATE_APPROVED)

    def test_argument_digest_changes_when_an_edge_is_detached(self):
        nodes = {"G0": _node(), "G1": _node(text="child")}
        with_edge = _graph(dict(nodes), edges=[_edge("G0", "G1")])
        without_edge = _graph(dict(nodes), edges=[])
        self.assertNotEqual(claims.argument_digest(with_edge),
                            claims.argument_digest(without_edge))


# --- pure audit coverage decision ---------------------------------------------

class AuditCoverage(unittest.TestCase):
    APPROVED = {"G0": {"claim_digest": "c" * 64, "evidence_digest": "e" * 64}}

    def _verdict(self, **over):
        base = {"verdict": "pass", "nonce": "n1", "argument_digest": "d" * 64,
                "claims_reviewed": [{"claim_id": "G0", "claim_digest": "c" * 64,
                                     "evidence_digest": "e" * 64}],
                "findings": []}
        base.update(over)
        return base

    def test_no_tickets_fails(self):
        ok, why = coverage_check([], self._verdict(), self.APPROVED)
        self.assertFalse(ok)
        self.assertIn("no independent audit", why)

    def test_missing_verdict_fails(self):
        ok, _ = coverage_check([{"nonce": "n1"}], None, self.APPROVED)
        self.assertFalse(ok)

    def test_nonce_mismatch_fails(self):
        ok, why = coverage_check([{"nonce": "other"}], self._verdict(), self.APPROVED)
        self.assertFalse(ok)
        self.assertIn("nonce", why)

    def test_failing_verdict_fails(self):
        v = self._verdict(verdict="fail", findings=["citation does not support the claim"])
        ok, why = coverage_check([{"nonce": "n1"}], v, self.APPROVED)
        self.assertFalse(ok)
        self.assertIn("citation does not support", why)

    def test_full_coverage_passes(self):
        ok, why = coverage_check([{"nonce": "n1"}], self._verdict(), self.APPROVED,
                                 argument_digest="d" * 64)
        self.assertTrue(ok)
        self.assertIn("passed", why)

    def test_reworded_claim_is_uncovered(self):
        v = self._verdict(claims_reviewed=[{"claim_id": "G0", "claim_digest": "STALE" + "c" * 59,
                                            "evidence_digest": "e" * 64}])
        ok, why = coverage_check([{"nonce": "n1"}], v, self.APPROVED)
        self.assertFalse(ok)
        self.assertIn("REWORDED", why)

    def test_argument_digest_mismatch_fails(self):
        ok, why = coverage_check([{"nonce": "n1"}], self._verdict(argument_digest="x" * 64),
                                 self.APPROVED, argument_digest="d" * 64)
        self.assertFalse(ok)
        self.assertIn("DIFFERENT argument", why)


"""Contract tests for the empirica persistence ports (ADR-31).

Run: python3 plugins/empirica/tests/test_core.py   (stdlib only, no pytest dependency)
Exit 0 = all checks pass; 1 = at least one failed.

These are *contract* tests, not adapter tests: they exercise the ports through minimal in-memory
fakes and assert the four semantics every real adapter must also honour —
    * CAS               — compare_and_set is conditional on the opaque revision;
    * generation isolation — a write under one RunKey.generation is invisible to another;
    * append union      — artifact append is commutative and idempotent (set union);
    * first-write-wins  — create/compare_and_set never clobber a concurrent writer.
Plus the absent-vs-corrupt distinction and the explicit migration path. The fakes are the
executable spec of the contract; a filesystem or database adapter is correct iff it passes the
same suite.
"""
# The core package uses relative imports, so import it as the package `core` with the plugin
# root on sys.path, matching the production module layout.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # plugins/empirica
sys.path.insert(0, str(PLUGIN_ROOT))
core = importlib.import_module("core")

ABSENT = core.ABSENT
Artifact = core.Artifact
Conflict = core.Conflict
Corrupt = core.Corrupt
MigrationReport = core.MigrationReport
Present = core.Present
Revision = core.Revision
RunKey = core.RunKey

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """Record one assertion. `ok` is coerced to a real bool so a caller that passes a container
    can never make the summation in main() raise and abort the whole suite."""
    results.append((name, bool(ok), detail))


# --- In-memory fakes: the executable spec of each port ----------------------
_CORRUPT = object()  # a stored blob that cannot be decoded


class FakeRunRepository:
    """Reference RunRepository: a dict keyed by RunKey, revisions minted from a counter.

    The revision is deliberately an opaque monotonic token, NOT the value's hash, so a test that
    accidentally reconstructed a revision from the value would fail — proving callers must round-
    trip the token they were given."""

    def __init__(self) -> None:
        self._store: dict[RunKey, object] = {}  # key -> (value, Revision) | _CORRUPT
        self._counter = 0

    def _mint(self) -> Revision:
        self._counter += 1
        return Revision(f"r{self._counter}")

    def read(self, key: RunKey):
        entry = self._store.get(key)
        if entry is None:
            return ABSENT
        if entry is _CORRUPT:
            return Corrupt("undecodable")
        value, rev = entry
        return Present(value, rev)

    def create(self, key: RunKey, value):
        if key in self._store:  # present or corrupt — either way, not ours to create
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

    def _inject_corrupt(self, key: RunKey) -> None:
        """Test affordance: simulate an on-store blob that no longer decodes."""
        self._store[key] = _CORRUPT


class FakeArtifactRepository:
    """Reference ArtifactRepository: a dict of key -> frozenset[Artifact]. Append is set union, so
    commutativity and idempotence fall out of set semantics — exactly the contract adapters owe."""

    def __init__(self) -> None:
        self._store: dict[RunKey, frozenset] = {}

    def append(self, key: RunKey, artifact: Artifact) -> None:
        self._store[key] = self._store.get(key, frozenset()) | {artifact}

    def read(self, key: RunKey):
        entry = self._store.get(key)
        if entry is None:
            return ABSENT
        # An opaque, order-independent digest of the set stands in for a real content revision.
        rev = Revision(str(hash(entry)))
        return Present(entry, rev)


@dataclass
class FakeMigration:
    """Reference MigrationPort: copies operational state and artifacts source -> target, leaving
    source intact. The only sanctioned bridge across a generation boundary (ADR-31)."""

    runs: FakeRunRepository
    artifacts: FakeArtifactRepository

    def migrate(self, source: RunKey, target: RunKey) -> MigrationReport:
        runs_moved = 0
        src_run = self.runs.read(source)
        if isinstance(src_run, Present):
            self.runs.create(target, src_run.value)  # first-write-wins guards target
            runs_moved = 1
        arts_moved = 0
        src_arts = self.artifacts.read(source)
        if isinstance(src_arts, Present):
            for art in src_arts.value:
                self.artifacts.append(target, art)
            arts_moved = len(src_arts.value)
        return MigrationReport(source, target, runs_moved, arts_moved)


K = RunKey("proj", "run-1", 1)


# --- CAS --------------------------------------------------------------------
def test_cas_conditional_on_revision():
    repo = FakeRunRepository()
    rev0 = repo.create(K, {"status": "active", "passes": 0})
    got = repo.read(K)
    check("A1 read after create is Present with a revision",
          isinstance(got, Present) and got.revision == rev0, f"got {got}")
    rev1 = repo.compare_and_set(K, {"status": "active", "passes": 1}, expected=rev0)
    check("A2 CAS with the current revision succeeds and advances the revision",
          rev1 != rev0 and isinstance(repo.read(K), Present), f"rev1={rev1}")
    stale = False
    try:
        repo.compare_and_set(K, {"status": "x"}, expected=rev0)  # rev0 is now stale
    except Conflict:
        stale = True
    check("A3 CAS with a stale revision raises Conflict", stale)
    check("A4 the refused CAS did not mutate the value",
          repo.read(K).value == {"status": "active", "passes": 1}, f"got {repo.read(K)}")
    rev2 = repo.compare_and_set(K, {"status": "done"}, expected=rev1)
    check("A5 CAS with the fresh revision after a failed attempt still works", rev2 != rev1)


# --- first-write-wins -------------------------------------------------------
def test_first_write_wins():
    repo = FakeRunRepository()
    repo.create(K, {"status": "active"})
    lost = False
    try:
        repo.create(K, {"status": "clobbered"})  # a second creator races and loses
    except Conflict:
        lost = True
    check("B1 a second create on an existing key raises Conflict (first-writer-wins)", lost)
    check("B2 the first writer's value survives the losing create",
          repo.read(K).value == {"status": "active"}, f"got {repo.read(K)}")
    fresh = FakeRunRepository()
    absent_cas = False
    try:
        fresh.compare_and_set(K, {"status": "x"}, expected=Revision("r1"))
    except Conflict:
        absent_cas = True
    check("B3 compare_and_set on an absent key raises Conflict (no blind upsert)", absent_cas)


# --- generation isolation ---------------------------------------------------
def test_generation_isolation_runs():
    repo = FakeRunRepository()
    g1 = RunKey("proj", "run-1", 1)
    g2 = RunKey("proj", "run-1", 2)  # same project+run, next generation
    repo.create(g1, {"status": "active", "gen": 1})
    check("C1 a write under generation 1 is ABSENT at generation 2",
          repo.read(g2) is ABSENT, f"got {repo.read(g2)}")
    # Generation 2 can be created independently; it does not disturb generation 1.
    repo.create(g2, {"status": "active", "gen": 2})
    check("C2 generation 2 holds its own value", repo.read(g2).value["gen"] == 2)
    check("C3 generation 1 is untouched by the generation-2 write",
          repo.read(g1).value["gen"] == 1, f"got {repo.read(g1)}")


def test_generation_isolation_artifacts():
    repo = FakeArtifactRepository()
    g1 = RunKey("proj", "run-1", 1)
    g2 = RunKey("proj", "run-1", 2)
    repo.append(g1, Artifact("a", "body-a"))
    check("C4 artifacts appended at generation 1 are ABSENT at generation 2",
          repo.read(g2) is ABSENT, f"got {repo.read(g2)}")
    check("C5 generation 1 still sees its artifact",
          repo.read(g1).value == frozenset({Artifact("a", "body-a")}))


# --- append union (commutative + idempotent) --------------------------------
def test_append_is_commutative_and_idempotent():
    a, b, c = Artifact("a", "A"), Artifact("b", "B"), Artifact("c", "C")

    r1 = FakeArtifactRepository()
    for art in (a, b, c):
        r1.append(K, art)
    r2 = FakeArtifactRepository()
    for art in (c, a, b):  # different order
        r2.append(K, art)
    check("D1 append is commutative: order does not change the resulting set",
          r1.read(K).value == r2.read(K).value == frozenset({a, b, c}),
          f"{r1.read(K)} vs {r2.read(K)}")

    r3 = FakeArtifactRepository()
    for art in (a, a, a, b):  # duplicates
        r3.append(K, art)
    check("D2 append is idempotent: appending the same artifact is a no-op",
          r3.read(K).value == frozenset({a, b}), f"got {r3.read(K)}")

    r4 = FakeArtifactRepository()
    r4.append(K, a)
    v_before = r4.read(K).value
    r4.append(K, a)
    check("D3 re-appending an existing artifact leaves the set unchanged",
          r4.read(K).value == v_before == frozenset({a}))

    check("D4 an empty artifact store reads ABSENT, not an empty Present",
          FakeArtifactRepository().read(K) is ABSENT)


# --- absent vs corrupt ------------------------------------------------------
def test_absent_is_distinct_from_corrupt():
    repo = FakeRunRepository()
    check("E1 an unwritten key reads ABSENT", repo.read(K) is ABSENT)
    repo._inject_corrupt(K)
    got = repo.read(K)
    check("E2 an undecodable blob reads Corrupt (not ABSENT)",
          isinstance(got, Corrupt) and got.reason, f"got {got}")
    check("E3 ABSENT and Corrupt are different outcomes", (repo.read(K) is ABSENT) is False)
    # Fail closed: a corrupt run must not be creatable (that would silently discard it) nor CAS-able.
    refused_create = refused_cas = False
    try:
        repo.create(K, {"status": "new"})
    except Conflict:
        refused_create = True
    try:
        repo.compare_and_set(K, {"status": "new"}, expected=Revision("r1"))
    except Conflict:
        refused_cas = True
    check("E4 create refuses to overwrite a corrupt document (fail closed)", refused_create)
    check("E5 compare_and_set refuses a corrupt document (fail closed)", refused_cas)


# --- explicit migration -----------------------------------------------------
def test_migration_is_the_only_generation_bridge():
    runs = FakeRunRepository()
    arts = FakeArtifactRepository()
    g1 = RunKey("proj", "run-1", 1)
    g2 = RunKey("proj", "run-1", 2)
    runs.create(g1, {"status": "active", "gen": 1})
    arts.append(g1, Artifact("a", "A"))
    arts.append(g1, Artifact("b", "B"))

    check("F1 before migration generation 2 is ABSENT (no implicit crossing)",
          runs.read(g2) is ABSENT and arts.read(g2) is ABSENT)

    report = FakeMigration(runs, arts).migrate(g1, g2)
    check("F2 migration reports what crossed the boundary",
          report == MigrationReport(g1, g2, runs_migrated=1, artifacts_migrated=2),
          f"got {report}")
    check("F3 generation 2 now holds the migrated state",
          runs.read(g2).value == {"status": "active", "gen": 1}
          and arts.read(g2).value == frozenset({Artifact("a", "A"), Artifact("b", "B")}),
          f"runs={runs.read(g2)} arts={arts.read(g2)}")
    check("F4 the source generation is left intact (migration is reversible)",
          runs.read(g1).value == {"status": "active", "gen": 1}
          and arts.read(g1).value == frozenset({Artifact("a", "A"), Artifact("b", "B")}))

    # Migrating onto an occupied target must lose to the first writer, not clobber it.
    clobbered = False
    try:
        FakeMigration(runs, arts).migrate(g1, g2)
    except Conflict:
        clobbered = True
    check("F5 re-migrating onto an occupied generation raises Conflict (first-write-wins holds)",
          clobbered)


# --- record hygiene ---------------------------------------------------------
def test_records_are_immutable_and_value_typed():
    frozen = False
    try:
        RunKey("p", "r", 1).generation = 2  # type: ignore[misc]
    except Exception:
        frozen = True
    check("G1 RunKey is frozen (immutable identity)", frozen)
    check("G2 RunKey has value equality (usable as a dict/set key)",
          RunKey("p", "r", 1) == RunKey("p", "r", 1)
          and RunKey("p", "r", 1) != RunKey("p", "r", 2))
    check("G3 Revision compares by value (opaque token round-trips)",
          Revision("x") == Revision("x") and Revision("x") != Revision("y"))
    check("G4 Artifact is hashable so it can live in the append-union set",
          len({Artifact("a", "A"), Artifact("a", "A")}) == 1)


def main() -> int:
    unit_suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    unit_result = unittest.TextTestRunner(verbosity=2).run(unit_suite)
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
    return 0 if unit_result.wasSuccessful() and passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
