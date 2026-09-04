#!/usr/bin/env python3
"""Focused regression suite for the host-neutral decision core (ADR-30, `plugins/empirica/core`).

Run: python3 plugins/empirica/tests/test_core.py   (stdlib only, no pytest dependency)
Exit 0 = all pass; 1 = at least one failed.

These pin the SUBSTANTIVE decision — Allow / Block / Inert / Fault — independently of any host: no
subprocess, no filesystem, no manifest. Every input is constructed in-memory and every oracle is a
stub, which is the whole point of the extraction. Behaviour is checked against the semantics of
`hooks/convergence_gate.main`; a full end-to-end fidelity check against the live gate stays in
`test_hooks.py`.
"""
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
