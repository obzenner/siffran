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
  residuals are read from the recorded basis, and they equal what the report required immediately before the stop;
* (contract v2) ``standing`` is the precedence over the owners' answers (the mediator's form, ``GetRun``'s audit, status
  and freshness), and every value is reached; ``ask`` is the item ``GetRun`` presents (its governance, its batch or its
  terminal obligation); ``audits`` are the run's audit verdicts in history order, the last ``GetRun``'s; ``dropped`` is
  exactly what ``GetArgument`` discards, each with the artifact that discards it; ``decisions`` are the run's human answers.
"""
from __future__ import annotations

import contextlib
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))  # the validator is a repository script

from adapters.confirmation import HostConfirmation
from adapters.governance import HostGovernance
from core import governance
from core.confirmation import presentable
from governance_setup import AUDITOR, invocation_for
from application import protocol as proto
from adapters.observer import civil
from application.observer import Observer
from application import observer_index as index
from assertions import action_graph, evaluate, get_argument, get_run, observe_action
from core.observer import STANDINGS
from observer_store import (PROFILE, walk_pages, T0, DemoSource, DriverSource, _Builder, _graph, build_demo_store, build_store, copy_store,
                            observed, contract_problems)

#: The core's bootstrap obligations: a human's batch is admitted only once every one is satisfied (``_investigation_block``).
BOOTSTRAP = {"obligation.route", "obligation.graph", "obligation.approval", "obligation.investigation"}
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


def _rejected_amendment():
    """An amendment its human answered no in the host's dialog: the human says how to go on (the hosts' turn end settles
    with ``human_wait``), so the run waits on its human."""
    b, run = _unapproved_amendment()
    b.ask_configuration(run, "rcpt-reject-amendment", outcome="reject")
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
    """What the real host mediators do at the agent's next step: their form count. A configuration not approved yet is
    proposed again (the agent's next ``configure_run``) and the host's governance mediator shows its dialog when a human
    is to approve it (P3c); then the evidence mediator observes its surface, evaluates the report and shows the core's
    batch when the batch is presentable. Each form is closed without an answer."""
    forms = []
    later = lambda message, schema: forms.append(message) or {"action": "cancel"}  # noqa: E731
    g = b.a.dispatch(b.drv, get_run(run_id=run))["result"]["run"]["governance"]
    if g["state"] != "approved" and g["proposal"]["rationale"] is not None:
        drv = copy_store(b.store).driver  # on a copy: the dialog's receipt is not one of the run's answers checked after
        proposed = b.a.dispatch(drv, observe_action(run_id=run, action={
            "kind": "configure_run", "budgets": g["proposal"]["budgets"], "rationale": g["proposal"]["rationale"]}))
        service = drv._service
        HostGovernance(PROFILE, elicit=later if can_present else None,
                       context_ingress=lambda _p, run_id, payload: service.trusted_governance_context(run_id=run_id, payload=payload),
                       decision_ingress=lambda _p, run_id, payload: service.trusted_governance_decision(run_id=run_id, payload=payload)
                       )(proposed["result"])
        if forms:
            return len(forms), True
    host = HostConfirmation(PROFILE, elicit=later if can_present else None, timeout=lambda: 900,
                            dispatch=lambda request, _profile: b.drv.request(request),
                            presentation_ingress=lambda _p, run_id, payload: b.drv.trusted_evidence_presentation(run_id, payload),
                            confirmation_ingress=lambda _p, run_id, payload: b.drv.trusted_evidence_confirmation(run_id, payload),
                            surface_ingress=lambda _p, run_id, payload: b.drv.trusted_human_surface(run_id, payload["surface"]))
    host.observe(run)
    result = b.a.dispatch(b.drv, evaluate(run_id=run, intent="report_convergence"))["result"]
    # The owner's own answer once the host has looked (the snapshot of this very report): is its batch one that is presented,
    # and does the core admit a human's batch at all? It admits none before the run has investigated: the core's own
    # bootstrap obligations (route, graph, approval, investigation) are then not all satisfied, and the presentation is
    # refused (``investigation.required``), so the host shows nothing.
    seen = result.get("run") or {}
    bootstrapped = all(o["status"] == "satisfied" for o in (seen.get("obligations") or {}).get("active", ())
                       if o["id"] in BOOTSTRAP)
    after_looking = presentable(seen.get("confirmation_batch")) and bootstrapped
    host(result, lambda failure=None: b.a.dispatch(b.drv, evaluate(run_id=run, intent="report_convergence"))["result"])
    return len(forms), after_looking


def _refuted():
    b, run = _begin("refuted", [("C0", "ordinary", "Root"), ("C1", "ordinary", "Refuted leaf")], [("C0", "C1")])
    b.research(run, "C1", result="refutes", ref="refutes.md")
    return b, run


def _refuted_and_supported():
    """Research both ways and no failed spike: the gate leaves the claim open, so nothing is dropped."""
    b, run = _begin("both-ways")
    b.research(run, "C0", ref="for.md")
    b.research(run, "C0", result="refutes", ref="against.md")
    return b, run


def _spike_failed():
    b, run = _begin("spike-failed", [("C0", "needs-experiment", "The probe passes")])
    b.research(run, "C0", ref="probe.md")
    b.spike(run, "C0", "probe --strict", {"probe.py": b"x\n"}, exit_code=1)
    return b, run


def _decision_rejected():
    b, run = _begin("decision-no", [("C0", "ordinary", "Root"), ("C1", "needs-decision", "Ship the cache")], [("C0", "C1")])
    digest = next(c["wording_digest"] for c in b.a.dispatch(b.drv, get_argument(run_id=run))["result"]["argument"]["claims"]
                  if c["claim_id"] == "C1")
    b.drv.trusted_evidence_presentation(run, {"run_id": run, "receipt_id": "d1", "key": b.batch_key(run)})
    b.drv.trusted_evidence_confirmation(run, {"run_id": run, "receipt_id": "d1", "decisions": [
        {"subject": "claim", "claim_id": "C1", "claim_digest": digest, "outcome": "reject"}]})
    return b, run


def _regraph_removed():
    """Three claims leave the argument in a regraph: one that research had refuted, one with nothing against it, and one
    with research both ways (the gate leaves that open: nothing discarded it, so it was only removed)."""
    b, run = _begin("regraph", [("C0", "ordinary", "Root"), ("C1", "ordinary", "Refuted"), ("C2", "ordinary", "Unneeded"),
                                ("C3", "ordinary", "Contested")], [("C0", "C1"), ("C0", "C2"), ("C0", "C3")])
    b.research(run, "C1", result="refutes", ref="no.md")
    b.research(run, "C3", ref="yes.md")
    b.research(run, "C3", result="refutes", ref="but.md")
    b.a.dispatch(b.drv, observe_action(run_id=run, action=action_graph(_graph("C0", [("C0", "ordinary", "Root")], []))))
    b.removed = {"C1": "refuted", "C2": "removed", "C3": "removed"}
    return b, run


def _older_verdict_then_other_producer():
    """A distinct pass, then evidence produced by the auditor's own identity and a second pass: the second is mixed, and
    the first stays what it was when it was recorded (it reviewed only the first producer's evidence)."""
    b, run = _begin("older-verdict")
    b.research(run, "C0", ref="first.md")
    b.audit(run, "pass")
    answered = b.drv._service.trusted_governance_context(
        run_id=run, payload={"author": AUDITOR, "ingress": proto.host_profile(b.a.DEFAULT_PROFILE)["approval_ingress"]})
    assert answered["result"]["type"] in ("Allow", "Inert"), answered
    b.research(run, "C0", ref="second.md")
    b.audit(run, "pass")
    return b, run


def _human_after_changed_pass():
    """A pass, then evidence a human must confirm: the pass no longer covers the argument and a human is asked."""
    b, run = _begin("changed-and-asked")
    b.research(run, "C0")
    b.audit(run, "pass")
    b.cite(run, "C0", "web", "https://example.invalid/more", "More external support")
    return b, run


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
    "rejected_amendment": (_rejected_amendment, set()),
    "auto_delegated": (_auto_delegated, set()),
    "refuted": (_refuted, set()),
    "refuted_and_supported": (_refuted_and_supported, set()),
    "spike_failed": (_spike_failed, set()),
    "decision_rejected": (_decision_rejected, set()),
    "regraph_removed": (_regraph_removed, set()),
    "human_after_changed_pass": (_human_after_changed_pass, set()),
    "older_verdict_then_other_producer": (_older_verdict_then_other_producer, set()),
}


#: Variants whose host has nobody to show a form to, and whose human interactions are already spent.
NO_SURFACE = {"evidence_nobody_to_ask", "auto_delegated", "unobserved_without_dialog", "unobserved_record_without_dialog"}
#: Variants whose host has not looked for a dialog yet: whether a human can be asked is unknown until it does.
UNOBSERVED = {"unobserved_with_dialog", "unobserved_without_dialog", "unobserved_record_with_dialog", "unobserved_record_without_dialog"}
LIMIT = {"evidence_interaction_limit"}


class ObserverAgreementTests(unittest.TestCase):
    def _read(self, b, run):
        observer = Observer(DriverSource(b.store))
        view, details = observed(observer)
        row = view["projects"][0]["runs"][0]
        self.assertEqual(contract_problems(view, details), [], "contract v2 and its validator hold")
        rows, full = observer.rows(), observer.details()
        self.v2 = (rows[0], full[rows[0]["key"]])
        core = b.a.dispatch(b.drv, get_run(run_id=run))["result"]["run"]
        argument = b.a.dispatch(b.drv, get_argument(run_id=run))["result"]["argument"]
        gate = b.a.dispatch(b.drv, evaluate(run_id=run, intent="report_convergence"))["result"]
        return row, details[row["key"]], core, argument, gate

    def test_INVARIANT_the_observer_says_what_the_gate_and_author_view_say(self):
        reached = set()
        for name, (build, assured) in VARIANTS.items():
            limit = mock.patch.object(governance, "MAX_INTERACTIONS", 1) if name in LIMIT else contextlib.nullcontext()
            with self.subTest(variant=name), limit:
                b, run = build()
                row, detail, core, argument, gate = self._read(b, run)
                v2 = self.v2
                self._assurance(row, gate, assured)
                self._current(row, detail, core, argument)
                self._obligations(row, detail, core, gate)
                self._residuals(row, detail, b)
                shown = self._attention(row, detail, b, run, name not in NO_SURFACE, name in UNOBSERVED)
                reached.add(self._standing(v2[0], core, shown, name in UNOBSERVED))
                self._ask(v2[0], core)
                self._audits(v2[0], v2[1], core, b)
                self._dropped(v2[1], argument, getattr(b, "removed", {}))
                self._decisions(v2[1], b, run)
        rows = Observer(DriverSource(build_store())).rows()
        reached |= {r["standing"] for r in rows if r["diagnostic"] is not None}
        self.assertEqual(reached, set(STANDINGS), "every standing is reached")

    # --- 6. (v2) standing, ask, audits, dropped and decisions are the owners' answers ------------------------------

    def _standing(self, row, core, shown, unknown):
        """The precedence over the owners' answers, written once here as the specification: a form the real mediator
        shows; a pending re-audit of an active run; a pass that no longer covers the argument or whose proof's files
        changed (``GetRun``'s audit and freshness); an active run; a converged one; the stop's status."""
        audit, active = core["audit"], core["status"] == "active"
        expected = ("needs_human" if active and shown and not unknown
                    else "audit_pending" if active and audit["pending"]
                    else "changed_since_audit" if audit["verdict"] == "passed" and (
                        audit["current"] is False or core["freshness"]["changes"])
                    else "not_ended" if active else "proven" if core["status"] == "converged" else core["status"])
        self.assertEqual(row["standing"], expected, "standing is the precedence over the owners' answers")
        return row["standing"]

    def _ask(self, row, core):
        """What ``GetRun`` presents a human: a configuration waiting on its human (``governance.human_wait``), the
        human's batch (a decision when it holds one, else a source), or the consent the report owes before it ends."""
        g = core["governance"]
        unmet = {r["missing"]["code"] for r in core["obligations"]["active"] if r["status"] != "satisfied" and r["missing"]}
        batch = core["confirmation_batch"]
        if core["status"] != "active":
            expected = None
        elif g["human_wait"]:  # the author view's own answer, which the hosts' turn end reads too
            expected = "approve_revision" if g["first_approval"] else "approve_configuration"
        elif batch is not None and "terminal" not in batch:
            expected = "decide_claim" if batch["claim_ids"] else "confirm_source"
        elif "governance.terminal_stop_pending" in unmet:
            expected = "freeze_scope"
        else:
            expected = None
        self.assertEqual(row["ask"], expected, "ask is the item the author view presents")

    def _audits(self, row, detail, core, b):
        verdicts = [s for s in detail["steps"] if s["kind"] == "audit_verdict"]
        self.assertEqual([a["verdict"] for a in detail["audits"]],
                         ["passed" if s["outcome"] == "pass" else "failed" for s in verdicts], "every verdict, in order")
        self.assertEqual([a["at"] for a in detail["audits"]], [s["at"] for s in verdicts], "each at its step's time")
        self.assertEqual(detail["audits"][-1]["verdict"] if detail["audits"] else "none", core["audit"]["verdict"],
                         "the last is the author view's latest")
        self.assertEqual([a["independence"] for a in detail["audits"]], b.independence_then,
                         "each verdict's independence is what it was when recorded, never today's evidence")

    def _dropped(self, detail, argument, removed):
        discarded = {c["claim_id"] for c in argument["claims"] if c["state"] == "discarded"}
        now = {d["claim"]: d for d in detail["dropped"] if not d["removed"]}
        self.assertEqual(set(now), discarded, "dropped now is exactly what the gate discards")
        for claim_id, d in now.items():
            mine = [a for a in argument["artifacts"] if a.get("claim_id") == claim_id]
            if d["why"] == "refuted":
                self.assertIn((d["source"], d["citation"]), {(a["source_ref"], a["citation"]) for a in mine
                                                             if a["kind"] == "research" and a["outcome"] == "refuting"})
            elif d["why"] == "spike_failed":
                self.assertIn(d["command"], {a.get("command") for a in mine if a["kind"] == "spike"})
            else:
                self.assertEqual(d["why"], "rejected")
                self.assertEqual(next(c for c in argument["claims"] if c["claim_id"] == claim_id)["kind"], "needs-decision")
        self.assertEqual({d["claim"]: d["why"] for d in detail["dropped"] if d["removed"]}, removed,
                         "a regraph's removals, each with the gate's why or none against it")

    def _decisions(self, detail, b, run):
        answers = [s for s in detail["steps"] if s["kind"] == "evidence_confirmation"]
        recorded = [d for d in detail["decisions"] if d["answer"] != "later" and d["asked"] not in {"approve_configuration", "approve_revision"}]
        self.assertEqual([d["at"] for d in recorded], [s["at"] for s in answers], "every recorded answer, at its time")
        state = b.drv._runs.read(next(k for k in b.drv._runs._store)).value
        dialog = [r for r in state["governance"]["receipts"]
                  if r["approval_kind"] == "host_ui" and r["outcome"] in {"approve", "amend", "reject", "dismiss"}]
        configured = [d for d in detail["decisions"] if d["asked"] in {"approve_configuration", "approve_revision"}]
        self.assertEqual(len(configured), len(dialog), "every configuration answer from a dialog, and no auto approval")
        self.assertTrue(all(d["at"] is not None for d in configured), "each at the write that recorded it (ADR-0070)")

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
        # the core admits a batch's presentation only once the run investigates (its route, approval and investigation in
        # the author view's setup rows): before that nobody is shown it, and it waits like any held item (P4)
        investigating = all(r["status"] == "satisfied" for r in core["obligations"]["active"]
                            if r["id"] in ("obligation.route", "obligation.approval", "obligation.investigation"))
        for item in detail["outstanding"]:
            held = all(r.get("hold") == "blocked" for r in unmet if need(r) == item["need"])
            asked = (item["need"] == "evidence.confirmation_required" and batch is not None and "terminal" not in batch
                     and investigating)
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
        by_need: dict = {}
        for o in detail["outstanding"]:  # the first item of each need: two claims may owe the same thing
            by_need.setdefault(o["need"], o)
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
            return 0
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
            return shown
        self.assertEqual(row["attention"] == "needs_you", shown > 0,
                         f"the host mediator {'shows' if shown else 'does not show'} a form on the next report")
        asked = [o for o in humans if o["where"] in {"next_report", "explicit_report"}]
        self.assertEqual(bool(asked), shown > 0, "the outstanding list says where that form is put")
        return shown


class NeedsOrderTests(unittest.TestCase):
    """The needs list agrees with the rows the core answered: exactly the runs that need a human, each at the time its
    question was recorded (the core's ``asked_at``), the oldest first, a question with no recorded time last; never grouped
    by project (the Inbox's "Needs a human" and the Universe's callouts read it as it comes)."""

    def test_the_needs_list_is_the_oldest_question_first_never_by_project(self):
        observer = Observer(DemoSource(build_demo_store(24), 160, now=T0 + 60 * 86400))
        needs, rows = observer.summary()["needs"], observer.rows()
        asking = {r["key"]: r for r in rows if r["standing"] == "needs_human"}
        self.assertEqual(sorted(n["key"] for n in needs), sorted(asking), "exactly the runs that need a human")
        self.assertEqual([n["asked_at"] for n in needs], [asking[n["key"]]["asked_at"] for n in needs], "the core's asked_at")
        timed = [n["asked_at"] for n in needs if n["asked_at"] is not None]
        self.assertGreater(len(set(timed)), 3)
        self.assertEqual(timed, sorted(timed), "the oldest question first")
        self.assertEqual([n["asked_at"] is None for n in needs], sorted(n["asked_at"] is None for n in needs), "untimed last")
        projects = [n["key"].split("/")[0] for n in needs]
        self.assertGreater(sum(a != b for a, b in zip(projects, projects[1:])), len(set(projects)) - 1, "not grouped by project")


    def test_the_needs_bucket_of_a_page_is_the_needs_list_order_across_pages(self):
        # the Inbox's "Needs a human" pages /api/runs filtered to that standing: its rows are the summary's needs, in
        # the same order (the declared order of that bucket), across every page and its cursor
        observer = Observer(DemoSource(build_demo_store(24), 160, now=T0 + 60 * 86400))
        needs = [n["key"] for n in observer.summary()["needs"]]
        walked = [r["key"] for r in walk_pages(lambda c, n: observer.runs({"standing": "needs_human"}, c, n), 7)[0]]
        self.assertEqual(walked, needs, "the page's needs rows in the needs list's order, page after page")
        first = observer.runs({"standing": "needs_human"}, None, 12)["rows"]
        self.assertEqual([r["key"] for r in first], needs[:12], "the Inbox's first rows are the summary's first needs")
        self.assertGreater(len({r["project"] for r in first}), 1, "not one project's")
        whole = [r["key"] for r in observer.runs({}, None, 200)["rows"] if r["standing"] == "needs_human"]
        self.assertEqual(whole, needs[:len(whole)], "unfiltered, the needs bucket keeps that order too")

    def test_a_cursor_inside_the_needs_bucket_names_its_asked_time(self):
        row = {"standing": "needs_human", "asked_at": 12.5, "key": "p/r/1"}
        cursor = index.cursor_of(row, "sha256:" + "a" * 64)
        self.assertEqual(index.cursor_after(cursor)[1], index.order(row))
        untimed = {**row, "asked_at": None}
        self.assertEqual(index.cursor_after(index.cursor_of(untimed, "sha256:" + "a" * 64))[1], index.order(untimed))
        self.assertLess(index.order(row), index.order(untimed), "an untimed question after every timed one")
        self.assertLess(index.order({**row, "asked_at": 1.0, "key": "z"}), index.order(row), "older first, whatever the key")
        other = {"standing": "proven", "asked_at": 3.0, "updated": 5.0, "key": "a"}
        self.assertEqual(index.order(other), index.order({**other, "asked_at": None}), "other buckets: never the asked time")
        with self.assertRaises(ValueError):
            index.cursor_after("a" * 16 + "~0:x:p/r/1")


class BucketOrderTests(unittest.TestCase):
    """Each bucket of a page has one declared order (``observer_index.ORDERS``, the summary's ``orders``): the
    needs-a-human bucket the oldest question first, every other the most recent activity first, an untimed run after every
    timed one, then key. Walked page by page, each bucket the store holds agrees with its declaration (the Inbox's
    sections lead with their first rows)."""

    @classmethod
    def setUpClass(cls):
        cls.observer = Observer(DemoSource(build_demo_store(24), 160, now=T0 + 60 * 86400))  # read only: shared

    def walk(self, standing):
        return walk_pages(lambda c, n: self.observer.runs({"standing": standing}, c, n), 7)[0]

    def test_the_summary_declares_one_order_per_bucket(self):
        orders = self.observer.summary()["orders"]
        self.assertEqual(set(orders), set(STANDINGS), "one order per bucket, every bucket")
        self.assertEqual(orders["needs_human"], {"by": "asked_at", "first": "oldest"})
        self.assertEqual({s: o for s, o in orders.items() if s != "needs_human"},
                         dict.fromkeys((s for s in STANDINGS if s != "needs_human"), {"by": "updated", "first": "newest"}))

    def test_each_bucket_pages_in_its_declared_order(self):
        orders, facets = self.observer.summary()["orders"], self.observer.summary()["facets"]["standing"]
        checked = 0
        for standing, n in facets.items():
            with self.subTest(standing=standing):
                rows = self.walk(standing)
                self.assertEqual(len(rows), n, "every run of the bucket, once")
                by, first = orders[standing]["by"], orders[standing]["first"]
                times = [r[by] for r in rows]
                timed = [t for t in times if t is not None]
                self.assertEqual(timed, sorted(timed, reverse=first == "newest"), f"{by}, the {first} first")
                self.assertEqual([t is None for t in times], sorted(t is None for t in times), "untimed last")
                for a, b in zip(rows, rows[1:]):
                    if a[by] == b[by]:
                        self.assertLess(a["key"], b["key"], "ties by key")
                checked += len(set(timed)) > 2
        self.assertGreaterEqual(checked, 3, "the store holds several buckets with distinct times")

    def test_the_most_recent_activity_leads_every_bucket_but_needs(self):
        # the Inbox's preview of "Changed since audit" (and every bucket but needs) is what moved last, never project order
        rows = self.observer.rows()
        for standing in {r["standing"] for r in rows} - {"needs_human"}:
            with self.subTest(standing=standing):
                held = [r for r in rows if r["standing"] == standing]
                newest = max((r["updated"] for r in held if r["updated"] is not None), default=None)
                first = self.observer.runs({"standing": standing}, None, 5)["rows"]
                self.assertEqual(first[0]["updated"], newest, "the newest first")

    def test_a_cursor_in_a_newest_first_bucket_resumes_after_its_row(self):
        row = {"standing": "proven", "asked_at": None, "updated": 12.5, "key": "p/r/1"}
        cursor = index.cursor_of(row, "sha256:" + "a" * 64)
        self.assertIn(":12.5:", cursor, "the cursor names the time the bucket reads, as recorded")
        self.assertEqual(index.cursor_after(cursor)[1], index.order(row))
        self.assertLess(index.order({**row, "updated": 20.0, "key": "z"}), index.order(row), "newer first, whatever the key")
        self.assertLess(index.order(row), index.order({**row, "updated": None}), "an untimed run after every timed one")
        untimed = {**row, "updated": None}
        self.assertEqual(index.cursor_after(index.cursor_of(untimed, "sha256:" + "a" * 64))[1], index.order(untimed))


class CalendarAgreementTests(unittest.TestCase):
    """The inbox calendar is the observer's (SOL-01): each cell is a project's runs last updated on one civil day in the
    viewer's zone, counted, and the most urgent standing among them, exactly what the rows the core answered say. The
    oracle reads each row's day straight from the zone database; the UI only draws the cells."""

    def test_every_cell_is_its_projects_runs_of_that_civil_day(self):
        from collections import Counter
        from datetime import datetime
        from zoneinfo import ZoneInfo
        observer = Observer(DemoSource(build_demo_store(24), 160, now=T0 + 60 * 86400), zones=civil.zone)
        rows = observer.rows()
        for tz in ("UTC", "America/New_York", "Asia/Kathmandu", "Pacific/Chatham"):
            with self.subTest(tz=tz):
                summary = observer.summary(None, tz)
                ids, cal = [p["id"] for p in summary["projects"]], summary["calendar"]
                runs, urgent = Counter(), {}
                for r in rows:
                    if r["updated"] is None:
                        continue
                    key = (ids.index(r["project"]), datetime.fromtimestamp(r["updated"], ZoneInfo(tz)).date().isoformat())
                    runs[key] += 1
                    urgent[key] = min(urgent.get(key, len(STANDINGS)), STANDINGS.index(r["standing"]))
                served = list(zip(cal["project"], cal["day"]))
                self.assertEqual(served, sorted(runs), "every cell once, by project then day")
                self.assertEqual(dict(zip(served, cal["runs"])), dict(runs))
                self.assertEqual(dict(zip(served, cal["standing"])), urgent)
                self.assertEqual(sum(cal["runs"]), sum(r["updated"] is not None for r in rows), "an untimed run on no day")
                for (_, day), start in zip(served, cal["start"]):
                    local = datetime.fromtimestamp(start, ZoneInfo(tz))
                    self.assertEqual((local.date().isoformat(), local.hour, local.minute), (day, 0, 0), "its first moment")


if __name__ == "__main__":
    unittest.main()
