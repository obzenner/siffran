"""The observer equals the core after every step of the real service (T1 C): a Hypothesis ``RuleBasedStateMachine``.

Every step is a public action an author, a host or a human takes, through the real v2 service (``observer_store._Builder``
drives it as a host would): the bootstrap (route, graph, configure, the host's dialog: approved, shown, dismissed or
rejected), research (for and against, from the workspace and from the web), spikes that pass or fail, a user's edit of a
file a spike bound, regraphs, a freeze, audits (an independent pass, a fail, a same-model or unverified auditor, a re-audit
left pending), a configuration amendment, a report, a stop (consented, or shown and declined) and a human's answers to the
batch. Sequences are generated, so a sequence nobody wrote down is checked too.

After every step the invariant reads the run three ways, independently of the observer, on a copy of the store (reading
never changes the run): the author view (``GetRun``, ``GetArgument``), the gate (``EvaluateRun report_convergence``) and
the real host mediators (``HostGovernance``, ``HostConfirmation``: whether a human is shown a form at the agent's next
step). The observer's row and sheet must agree with all three: its ``standing`` (the precedence over the owners'
answers), its ``ask`` (what the author view presents a human, its ``governance.human_wait`` included), its ``attention``
(``needs_you`` exactly when a real mediator shows a form), every current fact (claim states, the audit, freshness),
``outstanding`` and ``next`` (the author view's obligations in the gate's order), and the contract's own validator. The
predicates are ``test_observer_agreement``'s, written once; they never reimplement the core.

Fixed seed (``derandomize``) and a bounded number of examples, so ``make check`` is deterministic; a failing sequence is
shrunk to a minimal one and printed as the steps to replay.
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parents[1]), str(HERE.parents[3] / "scripts")]

from hypothesis import HealthCheck, Phase, settings, strategies as st  # noqa: E402
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule  # noqa: E402

from application.observer import Observer  # noqa: E402
from assertions import HarnessDefect, action_graph, action_investigate, evaluate, get_argument, get_run, observe_action  # noqa: E402
from governance_setup import invocation_for, sized_configure_run  # noqa: E402
from observer_store import (PROFILE, DriverSource, _Builder, _graph, copy_store, decision_problems, observed, step_problems,  # noqa: E402
                            contract_problems)
import test_observer_agreement as agreement  # noqa: E402

#: The claims a run may hold: a root and supporters of each kind.
CLAIMS = {"C0": ("ordinary", "The root claim holds."), "C1": ("needs-experiment", "The probe passes."),
          "C2": ("needs-decision", "Ship the new default."), "C3": ("ordinary", "A supporting claim holds.")}
#: How a run starts: (its human surface, whether its host can show a form, whether nobody knows yet, start keywords).
STARTS = {
    "attended": ("human", True, False, {}),
    "unattended": ("unavailable", False, False, {}),
    "unobserved-with-dialog": (None, True, True, {"invocation": invocation_for(PROFILE, human_surface="unobserved",
                                                                                signal="host startup")}),
    "unobserved-without-dialog": (None, False, True, {"invocation": invocation_for(PROFILE, human_surface="unobserved",
                                                                                   signal="host startup")}),
    "auto-delegated": ("unavailable", False, False, {"control_mode": "auto", "invocation": invocation_for(
        PROFILE, delegation=True, human_surface="unavailable", signal="v2 harness")}),
}
FILES = ("src/probe.py", "src/other.py")


def _graph_of(claims: list[str], reworded=frozenset()) -> dict:
    words = {c: (kind, text + (" (reworded)" if c in reworded else "")) for c, (kind, text) in CLAIMS.items()}
    return _graph("C0", [(c, *words[c]) for c in claims], [("C0", c) for c in claims if c != "C0"])


class ObserverEqualsCoreMachine(RuleBasedStateMachine):
    """One run, driven through the real service by generated public actions."""

    def __init__(self):
        super().__init__()
        self.check = agreement.ObserverAgreementTests("test_INVARIANT_the_observer_says_what_the_gate_and_author_view_say")
        self.b = None
        self.edits = 0
        self.receipts = 0  # every host answer has a receipt of its own

    # --- the run ---------------------------------------------------------------------------------------------------

    @initialize(start=st.sampled_from(sorted(STARTS)), extra=st.sets(st.sampled_from(["C1", "C2", "C3"]), min_size=1),
                approve=st.booleans())
    def begin(self, start, extra, approve):
        surface, self.can_present, self.unknown, kw = STARTS[start]
        self.b = _Builder()
        self.auto = start == "auto-delegated"
        self.claims = ["C0", *sorted(extra)]
        self.reworded: set = set()
        self.run = self.b.begin("probe", "s-machine", "Generated run.", _graph_of(self.claims), surface=surface,
                                qualified=True,
                                # an auto run is approved by its host's policy, never by a waiting human
                                approve=approve or start == "auto-delegated", **copy.deepcopy(kw))

    def _core(self) -> dict:
        return self.b.a.dispatch(self.b.drv, get_run(run_id=self.run))["result"]["run"]

    def _active(self) -> bool:
        return self._core()["status"] == "active"

    def _approved(self) -> bool:
        core = self._core()
        return core["status"] == "active" and core["governance"]["state"] == "approved"

    def _act(self, act) -> None:
        """An action the harness could not drive to its prerequisite (no child admitted, no scope frozen) is the service
        refusing it: the run is as it was, and the invariant still holds."""
        try:
            act()
        except HarnessDefect:
            pass

    # --- the author's and the host's acts --------------------------------------------------------------------------

    @precondition(lambda self: self._approved())
    @rule(claim=st.sampled_from(sorted(CLAIMS)), against=st.booleans(), ref=st.sampled_from(["a.md", "b.md"]))
    def research(self, claim, against, ref):
        self.b.research(self.run, claim, result="refutes" if against else "supports", ref=ref)

    @precondition(lambda self: self._approved())
    @rule(claim=st.sampled_from(sorted(CLAIMS)))
    def cite_the_web(self, claim):
        self.b.cite(self.run, claim, "web", "https://example.invalid/spec", "External support")

    @precondition(lambda self: self._approved() and "C1" in self.claims)
    @rule(path=st.sampled_from(FILES), passes=st.booleans())
    def spike(self, path, passes):
        self.b.spike(self.run, "C1", f"probe {path}", {path: b"v1\n"}, exit_code=0 if passes else 1)

    @rule(path=st.sampled_from(FILES))
    def a_user_edits_a_file(self, path):
        self.edits += 1
        self.b.write(path, f"v{self.edits + 1}\n".encode())

    @precondition(lambda self: self._approved())
    @rule(drop=st.sampled_from(["C1", "C2", "C3", None]), add=st.sampled_from(["C1", "C2", "C3", None]),
          reword=st.sampled_from(["C0", "C3", None]))
    def regraph(self, drop, add, reword=None):
        claims = [c for c in self.claims if c != drop]
        if add is not None and add not in claims:
            claims.append(add)
        self.claims = claims
        if reword in claims:
            self.reworded.add(reword)
        self.b.a.dispatch(self.b.drv, observe_action(run_id=self.run, action=action_graph(_graph_of(claims, self.reworded))))

    @precondition(lambda self: self._approved())
    @rule()
    def freeze(self):
        self._act(lambda: self.b.freeze(self.run))

    @precondition(lambda self: self._approved())
    @rule(verdict=st.sampled_from(["pass", "fail"]), auditor=st.sampled_from(["distinct", "same_model", "unverified"]))
    def audit(self, verdict, auditor):
        self._act(lambda: self.b.audit(self.run, verdict, ["A finding."] if verdict == "fail" else None, variant=auditor))

    @precondition(lambda self: self._approved())
    @rule()
    def a_re_audit_is_left_pending(self):
        self._act(lambda: self.b.a.require_pending_audit_child(self.b.drv, self.run))

    @precondition(lambda self: self._active())
    @rule(passes=st.integers(2, 4))
    def amend_the_configuration(self, passes):
        self.b.a.dispatch(self.b.drv, observe_action(run_id=self.run, action=sized_configure_run(
            budgets={"max_passes": passes, "max_spawns": 1, "max_audit_spawns": 2}, rationale="Amended scope.")))

    # Any number of the host's configuration dialogs (a third spends the proposal's reviews). An auto run has no host
    # dialog (its host's policy approves it). An approval may or may not be followed by the agent's investigation.
    @precondition(lambda self: not self.auto and self._active() and self._core()["governance"]["state"] != "approved")
    @rule(outcome=st.sampled_from(["present", "dismiss", "reject", None, "approve", "approve_only"]))
    def the_host_mediates_the_configuration(self, outcome):
        # The core owns the interaction limit (its run view's ``interactions_remaining``): once a proposal's (or the run's)
        # host interactions are spent, every host mediation (an approval, or a dialog shown) is Blocked with
        # ``governance.interaction_limit``, the run is as it was, and the invariant, checked after every step, still
        # holds on that exhausted run. (``None``: no host mediates, so nothing is spent or refused.)
        before = self._core()["governance"]
        if outcome is not None and min(before["interactions_remaining"].values()) == 0:
            with self.check.assertRaises(AssertionError) as blocked:
                if outcome in ("approve", "approve_only"):
                    self.b.drv.approve_governance(self.run)
                else:
                    self.b.ask_configuration(self.run, f"rcpt-{self.receipts + 1}", outcome=outcome)
            self.check.assertIn("governance.interaction_limit", str(blocked.exception))
            after = self._core()["governance"]
            self.check.assertEqual([after[k] for k in ("state", "approved_digest", "plan_revision", "interactions_remaining")],
                                   [before[k] for k in ("state", "approved_digest", "plan_revision", "interactions_remaining")],
                                   "the core refused it: nothing approved, nothing spent")
            return
        if outcome in ("approve", "approve_only"):
            self.b.drv.approve_governance(self.run)
            if outcome == "approve":
                self.b.a.dispatch(self.b.drv, observe_action(run_id=self.run, action=action_investigate()))
            return
        self.receipts += 1
        self._act(lambda: self.b.ask_configuration(self.run, f"rcpt-{self.receipts}", outcome=outcome))

    @precondition(lambda self: self._approved())
    @rule()
    def report(self):
        self.b.evaluate(self.run, "report_convergence")

    @precondition(lambda self: self._approved())
    @rule(consent=st.booleans())
    def stop(self, consent):
        if consent or self.auto:  # an auto run's stop is final: nobody is asked to consent
            self.b.evaluate(self.run, "stop")
        else:
            self.receipts += 1
            self._act(lambda: self.b.ask_to_end(self.run, f"consent-{self.receipts}"))
            self.can_present, self.unknown = True, False  # the host looked, and showed its human the stop

    @precondition(lambda self: self._approved() and (self._core()["confirmation_batch"] or {}).get("terminal") is None
                  and self._core()["confirmation_batch"] is not None)
    @rule(yes=st.booleans())
    def a_human_answers_the_batch(self, yes):
        batch = self._core()["confirmation_batch"]
        argument = self.b.a.dispatch(self.b.drv, get_argument(run_id=self.run))["result"]["argument"]
        digests = {c["claim_id"]: c["wording_digest"] for c in argument["claims"]}
        decisions = [{"subject": "claim", "claim_id": c, "claim_digest": digests[c], "outcome": "approve" if yes else "reject"}
                     for c in batch["claim_ids"]]
        decisions += [{"subject": "research", "artifact_id": r, "outcome": "confirm" if yes else "reject"}
                      for r in batch["record_ids"]]
        self.receipts += 1
        self.b.present_and_answer(self.run, f"answer-{self.receipts}", decisions)

    # --- the invariant -----------------------------------------------------------------------------------------------

    @invariant()
    def the_observer_says_what_the_core_says(self):
        if self.b is None:
            return
        observer = Observer(DriverSource(self.b.store))
        view, details = observed(observer)
        problems = contract_problems(view, details)
        assert problems == [], problems
        row = view["projects"][0]["runs"][0]
        detail = details[row["key"]]
        v2_row = observer.rows()[0]
        # The owners, on a copy: a report counts a pass and a mediator records what it shows.
        reader = copy.copy(self.b)
        reader.store = copy_store(self.b.store)
        reader.drv = reader.store.driver
        core = reader.a.dispatch(reader.drv, get_run(run_id=self.run))["result"]["run"]
        argument = reader.a.dispatch(reader.drv, get_argument(run_id=self.run))["result"]["argument"]
        gate = reader.a.dispatch(reader.drv, evaluate(run_id=self.run, intent="report_convergence"))["result"]
        c = self.check
        decided, self.decisions = decision_problems(row, detail, argument)
        assert decided == [], decided
        stepped, self.steps = step_problems(detail, argument)
        assert stepped == [], stepped
        c._current(row, detail, core, argument)
        c._obligations(row, detail, core, gate)
        c._ask(v2_row, core)
        asked = v2_row["ask"] in {"approve_configuration", "approve_revision"}
        assert asked == (core["status"] == "active" and core["governance"]["human_wait"]), (v2_row["ask"], core["governance"])
        if row["state"] == "active" and core["governance"]["state"] != "approved":
            # Before its configuration is approved a run waits on a human exactly when the core says so
            # (``governance.human_wait``: a host has mediated it and can show its dialog), which the hosts' turn end reads
            # too; a configuration no host has mediated yet is the agent's to propose (P3c).
            assert (row["attention"] == "needs_you") == core["governance"]["human_wait"], (row["attention"], core["governance"])
            shown = int(core["governance"]["human_wait"])
        elif self.unknown and row["state"] == "active":
            # Before the host looks, whether a human can be asked is unknown: never a definite question. Once it looks,
            # it shows a form exactly when the owner says the batch is presentable, and only if it has a dialog.
            assert row["attention"] != "needs_you", row["attention"]
            shown, after_looking = agreement._mediate(reader, self.run, can_present=self.can_present)
            assert (shown > 0) == after_looking, (shown, after_looking)
            assert shown == 0 or self.can_present, shown
        else:
            shown = c._attention(row, detail, reader, self.run, self.can_present, False)
        c._standing(v2_row, core, shown, self.unknown and not core["governance"]["human_wait"])


#: ``check`` (``make check-observer``): about 20 seconds. ``deep`` (``make observer-deep``, in ``make verify`` only): more and
#: longer sequences, still bounded. Both under a fixed seed with no example database, so every run is the same.
#: Both bounded, under a fixed seed, with no example database (T1). ``check`` (``make check-observer``, about 15 s) never
#: shrinks, so a failure costs no more than a pass: it prints the whole sequence. ``deep`` (``make observer-deep``, in
#: ``make verify``) shrinks a failure to its minimal sequence.
PROFILES = {"check": dict(max_examples=12, stateful_step_count=6, phases=[Phase.explicit, Phase.generate]),
            "deep": dict(max_examples=40, stateful_step_count=10)}
for _name, _bounds in PROFILES.items():
    settings.register_profile(f"observer-{_name}", derandomize=True, deadline=None, database=None, print_blob=True,
                              suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much], **_bounds)
ObserverEqualsCoreMachine.TestCase.settings = settings.get_profile(
    f"observer-{os.environ.get('EMPIRICA_STATEFUL_PROFILE', 'check')}")


class ObserverEqualsCoreTests(ObserverEqualsCoreMachine.TestCase):
    """``make check-observer`` runs the machine under a fixed seed."""


#: Named sequences the generator reaches only rarely, replayed through the same invariant after every step: the store's
#: scenarios they replace (T1 C) are gone from ``observer_store.build_store``.
REPLAYS = {
    "a decision added after the freeze is deferred": [
        ("begin", dict(start="attended", extra={"C3"}, approve=True)), ("research", dict(claim="C0", against=False, ref="a.md")),
        ("freeze", {}), ("regraph", dict(drop=None, add="C2"))],
    "a spike whose file changed afterwards is stale": [
        ("begin", dict(start="attended", extra={"C1"}, approve=True)), ("research", dict(claim="C1", against=False, ref="a.md")),
        ("spike", dict(path="src/probe.py", passes=True)), ("a_user_edits_a_file", dict(path="src/probe.py"))],
    "a stop, then its files change: today's requirements beside the stop's": [
        ("begin", dict(start="attended", extra={"C1"}, approve=True)), ("research", dict(claim="C0", against=False, ref="a.md")),
        ("research", dict(claim="C1", against=False, ref="b.md")), ("spike", dict(path="src/probe.py", passes=True)),
        ("stop", dict(consent=True)), ("a_user_edits_a_file", dict(path="src/probe.py"))],
    "the pass ceiling spent": [
        ("begin", dict(start="attended", extra={"C3"}, approve=True)), ("amend_the_configuration", dict(passes=2)),
        ("the_host_mediates_the_configuration", dict(outcome="approve")), ("report", {}), ("report", {}),
        ("research", dict(claim="C0", against=False, ref="a.md"))],
    "an unattended delegated run asks nobody": [
        ("begin", dict(start="auto-delegated", extra={"C2", "C3"}, approve=True)), ("cite_the_web", dict(claim="C3"))],
    "a passing audit by the same model is not independent": [
        ("begin", dict(start="attended", extra={"C3"}, approve=True)), ("research", dict(claim="C0", against=False, ref="a.md")),
        ("research", dict(claim="C3", against=False, ref="b.md")), ("audit", dict(verdict="pass", auditor="same_model"))],
    "claims reworded and dropped in a regraph": [
        ("begin", dict(start="attended", extra={"C1", "C3"}, approve=True)),
        ("research", dict(claim="C3", against=True, ref="a.md")), ("regraph", dict(drop="C3", add=None))],
    "a claim reworded after research": [
        ("begin", dict(start="attended", extra={"C3"}, approve=True)), ("research", dict(claim="C3", against=False, ref="a.md")),
        ("regraph", dict(drop=None, add=None, reword="C3"))],
    "a human approves a decision": [
        ("begin", dict(start="attended", extra={"C2"}, approve=True)), ("a_human_answers_the_batch", dict(yes=True))],
    "a human rejects a decision": [
        ("begin", dict(start="attended", extra={"C2"}, approve=True)), ("a_human_answers_the_batch", dict(yes=False))],
    "a decision not yet asked, behind agent work": [
        ("begin", dict(start="attended", extra={"C2", "C3"}, approve=True))],
    "a decision left unanswered by a stop": [
        ("begin", dict(start="attended", extra={"C2"}, approve=True)), ("stop", dict(consent=True))],
    "a decision refuted by research is not asked": [
        ("begin", dict(start="attended", extra={"C2"}, approve=True)), ("research", dict(claim="C2", against=True, ref="a.md"))],
    "a stop shown and declined": [
        ("begin", dict(start="attended", extra={"C3"}, approve=True)), ("research", dict(claim="C0", against=False, ref="a.md")),
        ("stop", dict(consent=False))],
}


class ReplayTests(unittest.TestCase):
    def test_each_named_sequence_keeps_the_invariant_after_every_step(self):
        decisions, steps_seen = set(), set()
        for name, steps in REPLAYS.items():
            with self.subTest(name):
                machine = ObserverEqualsCoreMachine()
                for method, kwargs in steps:
                    getattr(machine, method)(**kwargs)
                    machine.the_observer_says_what_the_core_says()
                    decisions |= set(machine.decisions)
                    steps_seen |= set(machine.steps)
        # The oracles must see every answer they tell apart, or an observer that always says one would pass.
        self.assertEqual(decisions, {"approved", "rejected", "waiting", "unanswered", "deferred", "not_asked"})
        self.assertEqual(steps_seen, {"current", "replaced", "gone"})


class FoundByTheMachineTests(unittest.TestCase):
    """What the machine found (T1), each fixed at its owner (P4) and kept as its minimal sequence."""

    def test_an_audit_of_a_run_with_no_approved_claim_is_refused_and_its_argument_stays_readable(self):
        """A settled verdict reviews at least one approved claim (the response contract): the transaction refuses one with
        ``reviewed_claims: []``, so the run records no verdict and ``GetArgument`` still answers."""
        machine = ObserverEqualsCoreMachine()
        machine.begin(start="attended", extra={"C1"}, approve=True)
        machine.b.audit(machine.run, "fail", ["A finding."])
        result = machine.b.drv.request(get_argument(run_id=machine.run))["result"]
        self.assertIn("argument", result, result)
        self.assertEqual(result["argument"]["audit"]["verdict"], "none", "the verdict was not recorded")

    def test_an_approved_run_that_has_not_investigated_is_needs_a_human_only_if_a_host_shows_a_form(self):
        """Approved, not yet investigating, a decision waiting: the core refuses the batch's presentation
        (``investigation.required``), so the human's batch waits (``LATER``) and the run is not ``needs_you``."""
        machine = ObserverEqualsCoreMachine()
        machine.begin(start="attended", extra={"C2"}, approve=False)
        machine.b.drv.approve_governance(machine.run)
        self._agrees(machine)
        self.assertEqual(Observer(DriverSource(machine.b.store)).rows()[0]["standing"], "not_ended")

    def test_a_run_whose_configuration_reviews_are_spent_is_needs_a_human_only_if_a_host_shows_a_form(self):
        """Three configuration dialogs, none approved: no host shows the configuration again, and the batch's presentation
        is refused before approval, so the run is not ``needs_you``."""
        machine = ObserverEqualsCoreMachine()
        machine.begin(start="attended", extra={"C1", "C2"}, approve=False)
        for n, outcome in enumerate(("present", "dismiss", "reject")):
            machine.b.ask_configuration(machine.run, f"known-{n}", outcome=outcome)
        self._agrees(machine)
        self.assertEqual(Observer(DriverSource(machine.b.store)).rows()[0]["standing"], "not_ended")

    def _agrees(self, machine) -> None:
        row = Observer(DriverSource(machine.b.store)).rows()[0]
        reader = copy.copy(machine.b)
        reader.store = copy_store(machine.b.store)
        reader.drv = reader.store.driver
        shown, _ = agreement._mediate(reader, machine.run, can_present=True)
        self.assertEqual(row["attention"] == "needs_you", shown > 0, (row["attention"], row["ask"], shown))


if __name__ == "__main__":
    unittest.main()
