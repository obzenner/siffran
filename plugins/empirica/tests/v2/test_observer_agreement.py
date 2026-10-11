"""The observer says what the gate and the author view say (U3: one pure action projection, three consumers).

Every run here is driven through the real v2 service. For each, three things are read independently of the observer:

* the report itself (``EvaluateRun report_convergence``): the gate's first reason and the batch it would show;
* the author view (``GetRun``): its obligations (what is unmet, what is held behind an earlier step), its batch;
* the observer's row and detail (``Observer.observe``), which must agree with both.

The oracles are the owners themselves, not a reimplementation (U4): ``GetRun`` and ``GetArgument`` on the live snapshot for
every current fact (audit, claim state, freshness), including runs that ended and whose files changed after, and the real
``HostConfirmation`` mediator, driven against the service, for whether a human is shown a form on the next report.

The invariant has these predicates, and each is mutated when it is added (see PROGRESS, ``## U3 round`` and ``## U4 round``):

* ``assurance`` is ``assured`` exactly when the gate accepts the audit for the claim (independence and its scope semantics
  included);
* ``next`` and ``outstanding`` are the author view's obligations in the gate's order, and ``next`` is the report's block;
* ``needs_you`` holds exactly when the real host mediator presents a form on the next report, whatever else the report
  blocks on first (the gate's first reason and the human's presentability are two facts);
* every current fact of a run, ended or not, equals the owners' answer on the live snapshot; only the stop's own
  residuals are read from the recorded basis, and they equal what the report required immediately before the stop.
"""
from __future__ import annotations

import contextlib
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))  # the validator is a repository script

from adapters.confirmation import HostConfirmation
from core import governance
from core.confirmation import presentable
from governance_setup import AUDITOR, invocation_for
from application import protocol as proto
from application.observer import Observer
from assertions import action_graph, evaluate, get_argument, get_run, observe_action
from observer_store import PROFILE, DriverSource, _Builder, _graph
from validate_ui_view import check

AUDIT_REASONS = {"audit.required", "audit.pending", "audit.failed", "audit.same_model", "audit.producers_mixed",
                 "audit.independence_unverified"}
WHERE = {None: "next_report", "unobserved": "if_available", "already_presented": "explicit_report",
         "unavailable": "unavailable", "interaction_limit": "limit"}
SETUP = {"obligation.route", "obligation.graph", "obligation.approval", "obligation.investigation"}
ONE = [("C0", "ordinary", "A supported claim")]


def _begin(name, claims=None, edges=(), **kw):
    b = _Builder()
    return b, b.begin("probe", name, name, _graph("C0", claims or ONE, list(edges)), **kw)


def _audited(variant):
    def build():
        b, run = _begin(variant)
        b.research(run, "C0")
        b.audit(run, "pass", variant=variant)
        return b, run
    return build


def _mixed():
    b, run = _begin("mixed")
    b.research(run, "C0", ref="first.md")
    answered = b.drv._service.trusted_governance_context(
        run_id=run, payload={"author": AUDITOR, "ingress": proto.host_profile(b.a.DEFAULT_PROFILE)["approval_ingress"]})
    assert answered["result"]["type"] in ("Allow", "Inert"), answered
    b.research(run, "C0", ref="second.md")
    b.audit(run, "pass")
    return b, run


def _human_owned():
    """The only approved claim is a human's decision: nothing was produced, so nobody is independent of anything."""
    b, run = _begin("human-owned", [("C0", "needs-decision", "Ship the cache")])
    digest = b.a.dispatch(b.drv, get_argument(run_id=run))["result"]["argument"]["claims"][0]["wording_digest"]
    b.drv.trusted_evidence_presentation(run, {"run_id": run, "receipt_id": "d1", "key": b.batch_key(run)})
    b.drv.trusted_evidence_confirmation(run, {"run_id": run, "receipt_id": "d1", "decisions": [
        {"subject": "claim", "claim_id": "C0", "claim_digest": digest, "outcome": "approve"}]})
    b.audit(run, "pass")
    return b, run


def _reaudit_pending():
    b, run = _begin("pending")
    b.research(run, "C0")
    b.audit(run, "pass")
    b.a.require_pending_audit_child(b.drv, run)
    return b, run


def _argument_changed_after_pass():
    b, run = _begin("changed", [("C0", "ordinary", "Root"), ("C1", "ordinary", "Leaf")], [("C0", "C1")])
    b.research(run, "C1")
    b.research(run, "C0", ref="other.md")
    b.audit(run, "pass")
    b.research(run, "C1", ref="third.md")
    return b, run


def _pass_exhausted():
    b, run = _begin("passes", budgets={"max_passes": 1, "max_spawns": 1, "max_audit_spawns": 2})
    b.research(run, "C0")
    b.evaluate(run, "report_convergence")
    b.research(run, "C0", ref="second.md")
    return b, run


def _audit_spawns_spent():
    b, run = _begin("spawns", budgets={"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 1})
    b.research(run, "C0")
    b.audit(run, "fail", ["The claim is not covered."])
    return b, run


def _frozen_consent_pending():
    b, run = _begin("frozen")
    b.research(run, "C0")
    b.freeze(run)
    b.a.dispatch(b.drv, observe_action(run_id=run, action=action_graph(
        _graph("C0", [("C0", "ordinary", "A supported claim"), ("C1", "ordinary", "A deferred claim")], [("C0", "C1")]))))
    b.audit(run, "pass")
    return b, run


def _human_with_agent_work():
    return _begin("early", [("C0", "ordinary", "Unresearched root"), ("C1", "needs-decision", "Human decision")],
                  [("C0", "C1")])


def _parallel_batch():
    b, run = _begin("parallel", [("C0", "ordinary", "Open root"), ("C1", "ordinary", "Supported leaf")], [("C0", "C1")])
    b.research(run, "C1")
    b.cite(run, "C1", "web", "https://example.invalid/spec", "Additional external support")
    return b, run


def _decision_alone():
    return _begin("decision", [("C0", "needs-decision", "Pick the default")])


def _evidence_waiting(**kw):
    def build():
        b, run = _begin("evidence", **kw)
        b.cite(run, "C0", "web", "https://example.invalid/spec", "External support")
        return b, run
    return build


def _evidence_shown_and_left_open():
    b, run = _begin("shown")
    b.cite(run, "C0", "web", "https://example.invalid/spec", "External support")
    b.drv.trusted_evidence_presentation(run, {"run_id": run, "receipt_id": "p1", "key": b.batch_key(run)})
    return b, run


def _stopped_then_drifted():
    b, run = _begin("drift", [("C0", "needs-experiment", "Spike-backed claim")])
    b.research(run, "C0")
    b.spike(run, "C0", "probe-command", {"src.py": b"old\n"})
    b.evaluate(run, "stop")
    b.write("src.py", b"new\n")
    return b, run


def _unapproved_amendment():
    b, run = _begin("amendment")
    b.a.dispatch(b.drv, observe_action(run_id=run, action={
        "kind": "configure_run", "budgets": {"max_passes": 3, "max_spawns": 0, "max_audit_spawns": 1},
        "rationale": "Small amended scope."}))
    return b, run


def _unobserved():
    """A host that started the run and has not yet looked for a dialog: whether a human can be asked is unknown."""
    b, run = _begin("unobserved", [("C0", "needs-decision", "Ship it?")], surface=None,
                    invocation=invocation_for(PROFILE, human_surface="unobserved", signal="host startup"))
    return b, run


def _unobserved_record():
    b, run = _begin("unobserved-record", surface=None,
                    invocation=invocation_for(PROFILE, human_surface="unobserved", signal="host startup"))
    b.cite(run, "C0", "web", "https://example.invalid/spec", "External support")
    return b, run


def _unknown_interactivity():
    """The host did not say whether the session is interactive: that stays unknown (null), not false."""
    return _begin("unknown-interactive", invocation=invocation_for(PROFILE, interactive=None, signal="v2 harness"))


def _auto_delegated():
    b, run = _begin("auto", control_mode="auto", surface="unavailable",
                    invocation=invocation_for(PROFILE, delegation=True, human_surface="unavailable", signal="v2 harness"))
    b.cite(run, "C0", "web", "https://example.invalid/spec", "External support")
    return b, run


def _stop(b, run):
    """The run's state of requirements immediately before the stop (the author view), then the stop."""
    b.before = b.a.dispatch(b.drv, get_run(run_id=run))["result"]["run"]
    b.evaluate(run, "stop")


def _converged(drift=False):
    def build():
        b, run = _begin("converged", [("C0", "needs-experiment", "Spike-backed claim")])
        b.research(run, "C0")
        b.spike(run, "C0", "proof-command", {"bound.py": b"old\n"})
        b.audit(run, "pass")
        b.evaluate(run, "report_convergence")
        if drift:
            b.write("bound.py", b"new\n")
        return b, run
    return build


def _frozen_stopped(verdict=None):
    def build():
        b, run = _begin("frozen-stop")
        b.research(run, "C0")
        b.freeze(run)
        b.a.dispatch(b.drv, observe_action(run_id=run, action=action_graph(
            _graph("C0", [("C0", "ordinary", "A supported claim"), ("C1", "ordinary", "A deferred claim")], [("C0", "C1")]))))
        if verdict:
            b.audit(run, verdict, ["The claim is not covered."])
        _stop(b, run)
        return b, run
    return build


def _stopped_then_drifted_as(status):
    """A run that ended as ``status`` with a passing experiment, and the experiment's file changed afterwards."""
    def build():
        kw = {"budgets": {"max_passes": 1, "max_spawns": 1, "max_audit_spawns": 2}} if status == "stopped_budget" else {}
        b, run = _begin("drift-" + status, [("C0", "needs-experiment", "Spike-backed claim")], **kw)
        b.research(run, "C0")
        b.spike(run, "C0", "probe-command", {"src.py": b"old\n"})
        if status == "stopped_budget":
            b.evaluate(run, "report_convergence")
            b.research(run, "C0", ref="second.md")
        if status == "stopped_frozen":
            b.freeze(run)
            b.a.dispatch(b.drv, observe_action(run_id=run, action=action_graph(_graph("C0", [
                ("C0", "needs-experiment", "Spike-backed claim"), ("C1", "ordinary", "A deferred claim")], [("C0", "C1")]))))
        _stop(b, run)
        b.write("src.py", b"new\n")
        return b, run
    return build


def _parallel_batch_and_decision():
    b, run = _begin("parallel-decision", [("C0", "ordinary", "Open root"), ("C1", "needs-decision", "Choose the default"),
                                          ("C2", "ordinary", "Supported leaf")], [("C0", "C1"), ("C0", "C2")])
    b.research(run, "C2")
    b.cite(run, "C2", "web", "https://example.invalid/spec", "Additional external support")
    return b, run


def _later_and_reask():
    """A real host showed the batch once and the human chose Later: only an explicit report asks again."""
    b, run = _begin("reask")
    b.cite(run, "C0", "web", "https://example.invalid/spec", "External support")
    _mediate(b, run, can_present=True)
    return b, run


def _stopped_residual(**kw):
    def build():
        b, run = _begin("stop", [("C0", "ordinary", "Unresearched root"), ("C1", "ordinary", "Leaf")], [("C0", "C1")])
        b.research(run, "C1")
        _stop(b, run)
        return b, run
    return build


def _mediate(b, run, *, can_present):
    """What the real host mediator does at the next report: its form count (it observes its surface, evaluates the
    report and shows the core's batch when the batch is presentable)."""
    forms = []
    later = lambda message, schema: forms.append(message) or {"action": "cancel"}  # noqa: E731
    host = HostConfirmation(PROFILE, elicit=later if can_present else None, timeout=lambda: 900,
                            dispatch=lambda request, _profile: b.drv.request(request),
                            presentation_ingress=lambda _p, run_id, payload: b.drv.trusted_evidence_presentation(run_id, payload),
                            confirmation_ingress=lambda _p, run_id, payload: b.drv.trusted_evidence_confirmation(run_id, payload),
                            surface_ingress=lambda _p, run_id, payload: b.drv.trusted_human_surface(run_id, payload["surface"]))
    host.observe(run)
    result = b.a.dispatch(b.drv, evaluate(run_id=run, intent="report_convergence"))["result"]
    # The owner's own answer once the host has looked (the snapshot of this very report): is its batch one that is presented?
    after_looking = presentable((result.get("run") or {}).get("confirmation_batch"))
    host(result, lambda failure=None: b.a.dispatch(b.drv, evaluate(run_id=run, intent="report_convergence"))["result"])
    return len(forms), after_looking


#: name -> (how the run is built, the claims the gate accepts the audit for, by construction)
VARIANTS = {
    "audit_distinct": (_audited("distinct"), {"C0"}),
    "audit_same_model": (_audited("same_model"), set()),
    "audit_unverified": (_audited("unverified"), set()),
    "audit_mixed": (_mixed, set()),
    "audit_human_owned": (_human_owned, {"C0"}),
    "audit_pending_after_pass": (_reaudit_pending, set()),
    "audit_stale_after_change": (_argument_changed_after_pass, set()),
    "pass_exhausted": (_pass_exhausted, set()),
    "audit_spawns_spent": (_audit_spawns_spent, set()),
    "frozen_consent_pending": (_frozen_consent_pending, {"C0"}),
    "human_before_agent_work": (_human_with_agent_work, set()),
    "parallel_batch": (_parallel_batch, set()),
    "decision_alone": (_decision_alone, set()),
    "evidence_waiting": (_evidence_waiting(), set()),
    "evidence_nobody_to_ask": (_evidence_waiting(surface="unavailable"), set()),
    "evidence_shown_and_left_open": (_evidence_shown_and_left_open, set()),
    "stopped_then_drifted": (_stopped_then_drifted, set()),
    "complete": (_audited("distinct"), {"C0"}),
    "converged": (_converged(), {"C0"}),
    "stopped_budget_then_drift": (_stopped_then_drifted_as("stopped_budget"), set()),
    "stopped_frozen_then_drift": (_stopped_then_drifted_as("stopped_frozen"), set()),
    "converged_then_drift": (_converged(drift=True), set()),
    "stopped_residual": (_stopped_residual(), set()),
    "parallel_batch_and_decision": (_parallel_batch_and_decision, set()),
    "frozen_unaudited_stopped": (_frozen_stopped(), set()),
    "frozen_audit_rejected_stopped": (_frozen_stopped("fail"), set()),
    "evidence_interaction_limit": (_evidence_shown_and_left_open, set()),
    "later_and_reask": (_later_and_reask, set()),
    "unknown_interactivity": (_unknown_interactivity, set()),
    "unobserved_with_dialog": (_unobserved, set()),
    "unobserved_without_dialog": (_unobserved, set()),
    "unobserved_record_with_dialog": (_unobserved_record, set()),
    "unobserved_record_without_dialog": (_unobserved_record, set()),
    "unapproved_amendment": (_unapproved_amendment, set()),
    "auto_delegated": (_auto_delegated, set()),
}


#: Variants whose host has nobody to show a form to, and whose human interactions are already spent.
NO_SURFACE = {"evidence_nobody_to_ask", "auto_delegated", "unobserved_without_dialog", "unobserved_record_without_dialog"}
#: Variants whose host has not looked for a dialog yet: whether a human can be asked is unknown until it does.
UNOBSERVED = {"unobserved_with_dialog", "unobserved_without_dialog", "unobserved_record_with_dialog", "unobserved_record_without_dialog"}
LIMIT = {"evidence_interaction_limit"}


class ObserverAgreementTests(unittest.TestCase):
    def _read(self, b, run):
        view, details = Observer(DriverSource(b.store)).observe()
        row = view["projects"][0]["runs"][0]
        self.assertEqual(check(view, details), [], "the contract and its validator hold")
        core = b.a.dispatch(b.drv, get_run(run_id=run))["result"]["run"]
        argument = b.a.dispatch(b.drv, get_argument(run_id=run))["result"]["argument"]
        gate = b.a.dispatch(b.drv, evaluate(run_id=run, intent="report_convergence"))["result"]
        return row, details[row["key"]], core, argument, gate

    def test_INVARIANT_the_observer_says_what_the_gate_and_author_view_say(self):
        for name, (build, assured) in VARIANTS.items():
            limit = mock.patch.object(governance, "MAX_INTERACTIONS", 1) if name in LIMIT else contextlib.nullcontext()
            with self.subTest(variant=name), limit:
                b, run = build()
                row, detail, core, argument, gate = self._read(b, run)
                self._assurance(row, gate, assured)
                self._current(row, detail, core, argument)
                self._obligations(row, detail, core, gate)
                self._residuals(row, detail, b)
                self._attention(row, detail, b, run, name not in NO_SURFACE, name in UNOBSERVED)

    # --- 1. assurance is audit acceptance -----------------------------------------------------------------------

    def _assurance(self, row, gate, assured):
        got = {c["id"] for c in row["claims"] if c["assurance"] == "assured"}
        self.assertEqual(got, assured, "assured exactly where the audit is accepted")
        codes = {r["code"] for r in gate.get("reasons", [])}
        if codes & AUDIT_REASONS:
            self.assertEqual(got, set(), f"the gate refuses the audit ({sorted(codes & AUDIT_REASONS)}): nothing is assured")
        elif row["state"] == "active" and (gate["type"] == "Allow" or "governance.terminal_stop_pending" in codes):
            self.assertEqual(got, {c["id"] for c in row["claims"] if c["state"] == "approved" and c["gating"]},
                             "the gate went past the audit: every approved claim in scope is assured")

    # --- 2. every current fact is the owners' answer on the live snapshot, whatever became of the run -------------------

    def _current(self, row, detail, core, argument):
        """The audit, each claim's state, the freshness of its proof and the files that changed are what ``GetRun`` and
        ``GetArgument`` say of the run as it is now: a run that ended is not exempt, its files may have changed since."""
        facts = ("verdict", "current", "pending", "independence")
        self.assertEqual(row["audit"], {k: core["audit"][k] for k in facts}, "the audit is the author view's, as of now")
        self.assertEqual((row["session"]["interactive"], row["session"]["delegated"]),
                         (core["invocation"]["interactive"], core["invocation"]["delegation"]),
                         "the session's recorded context is the author view's, unknown staying unknown")
        theirs = {c["claim_id"]: c["state"] for c in argument["claims"]}
        self.assertEqual({c["id"]: c["state"] for c in row["claims"]}, theirs, "claim states are the argument's")
        self.assertEqual(row["progress"]["approved"], sum(c["gating"] and c["state"] == "approved" for c in argument["claims"]))
        for c in row["claims"]:
            if c["assurance"] == "assured":
                audit = core["audit"]
                self.assertEqual((theirs[c["id"]], audit["verdict"], audit["current"], audit["pending"]),
                                 ("approved", "passed", True, False), "assured needs an approved claim and a current pass")
        self.assertEqual(detail["stale"], core["freshness"]["changes"], "the files that changed are the author view's")
        if row["state"] != "active" and detail["now"] is not None:
            # ``now`` is every requirement the report would still make of the run as it is now, whatever its terminal status
            # (the end already resolved the human's consent to end it): the author view's unmet obligations.
            unmet = {r["missing"]["code"] for r in core["obligations"]["active"]
                     if r["status"] != "satisfied" and r.get("hold") != "deferred" and r["missing"]}
            self.assertEqual({x["code"] for x in detail["now"]}, unmet - {"governance.terminal_stop_pending"},
                             "now lists every current requirement, not only the terminal status's own residuals")
            self.assertTrue(detail["now"] or not unmet, "and it is not empty when something is owed")
        rows = {r["id"]: r for r in core["obligations"]["active"]}
        for claim in detail["claims"]:
            owed = rows.get("claim:" + claim["id"], {}).get("observed", [])
            self.assertEqual(sum(p["state"] == "stale" for p in claim["proof"]),
                             sum(o["kind"] == "spike" and o["stale"] for o in owed), "a spike is stale as the author view says")

    # --- 3. outstanding and next are the author view's obligations in the gate's order --------------------------

    def _obligations(self, row, detail, core, gate):
        if row["state"] != "active":
            self.assertEqual((detail["outstanding"], row["next"]), ([], None))
            return
        unmet = [r for r in core["obligations"]["active"] if r["status"] != "satisfied" and r.get("hold") != "deferred"]
        need = lambda r: r["missing"]["code"] if r["missing"] else r["id"]  # noqa: E731
        self.assertEqual({o["need"] for o in detail["outstanding"]}, {need(r) for r in unmet},
                         "outstanding lists every unmet obligation the author view lists, and nothing else")
        batch = core["confirmation_batch"]
        for item in detail["outstanding"]:
            held = all(r.get("hold") == "blocked" for r in unmet if need(r) == item["need"])
            asked = item["need"] == "evidence.confirmation_required" and batch is not None and "terminal" not in batch
            if asked:  # a question the host will put whatever the agent still owes: its place is the batch's own
                self.assertEqual(item["where"], WHERE[batch["prompt_error"]], "a batch is asked where its prompt_error says")
            else:
                self.assertEqual(item["where"] == "later", held, f"{item['need']} is held exactly when the author view holds it")
        if batch is not None and "terminal" not in batch:
            self.assertIn("evidence.confirmation_required", {o["need"] for o in detail["outstanding"]},
                          "a batch the author view holds is listed, even while agent work comes first")
        admissible = [r for r in unmet if r.get("hold") != "blocked"]
        if not admissible:
            self.assertIsNone(row["next"])
            return
        by_need = {o["need"]: o for o in detail["outstanding"]}
        if row["attention"] == "needs_you":
            self.assertEqual(row["next"]["who"], "human", "a row that needs a human names the human's question")
        else:
            self.assertEqual(row["next"]["message"], by_need[need(admissible[0])]["message"], "next is the first admissible obligation")
        reasons = gate.get("reasons", [])
        if reasons and need(admissible[0]) not in SETUP:
            self.assertEqual(need(admissible[0]), reasons[0]["code"], "the gate's first reason is the first admissible obligation")

    # --- 4. what a stop left is what the report required immediately before it --------------------------------------

    def _residuals(self, row, detail, b):
        before = getattr(b, "before", None)
        if row["state"] == "active" or before is None:
            return
        codes = {r["code"] for r in detail["residuals"]}
        unmet = [r for r in before["obligations"]["active"] if r["status"] != "satisfied" and r.get("hold") != "deferred"]
        admissible = [r["missing"]["code"] for r in unmet if r.get("hold") != "blocked" and r["missing"]]
        allowed = {r["missing"]["code"] for r in unmet if r["missing"]} | {"freeze.deferred", "budget.exhausted"}
        self.assertTrue(set(admissible) <= codes, f"the stop left what the report required first ({admissible}); it left {sorted(codes)}")
        self.assertLessEqual(codes - {"governance.terminal_stop_pending"}, allowed, "and nothing the report did not require")
        if detail["stop"]["frozen"] and detail["stop"]["deferred"]:
            self.assertIn("freeze.deferred", codes, "a frozen stop names the scope it left out")

    # --- 5. needs_you is a form the real host mediator shows on the next report -------------------------------------

    def _attention(self, row, detail, b, run, surface, unknown=False):
        if row["state"] != "active":
            self.assertEqual(row["attention"], "ended")
            return
        humans = [o for o in detail["outstanding"] if o["who"] == "human"]
        waits = [c["decision"]["where"] for c in detail["claims"] if c["decision"] and c["decision"]["where"]]
        waits += [r["wait"]["where"] for c in detail["claims"] for r in c["records"] if r["wait"] and r["wait"]["who"] == "human"]
        shown, after_looking = _mediate(b, run, can_present=surface)
        self.assertEqual(shown > 0, after_looking, "the host shows a form exactly when the owner, after the host looked, says it can")
        if unknown:
            # Before the host looks, whether a human can be asked is unknown: told as unknown, never as a definite question.
            self.assertEqual(shown > 0, surface, "and it is the dialog the host has, which nobody knew before the report")
            self.assertNotEqual(row["attention"], "needs_you", "an unknown availability is not a question a human is owed")
            self.assertTrue(humans and {o["where"] for o in humans} == {"if_available"}, humans)
            self.assertEqual(set(waits), {"if_available"}, "so are the claim's and the record's own places")
            return
        self.assertEqual(row["attention"] == "needs_you", shown > 0,
                         f"the host mediator {'shows' if shown else 'does not show'} a form on the next report")
        asked = [o for o in humans if o["where"] in {"next_report", "explicit_report"}]
        self.assertEqual(bool(asked), shown > 0, "the outstanding list says where that form is put")


if __name__ == "__main__":
    unittest.main()
