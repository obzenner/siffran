#!/usr/bin/env python3
"""Empirica 7.2 E9: the ``qualify`` attempt transaction — reserve, effect, binding CAS (spec §5).

Behaviour only, over the real composed service with a fake port and no Docker: the reservation is
durable before the host attempt starts (charged even when the attempt then fails or is
interrupted), the outcome is recorded with the closed reason of its own family (and the failed
probe exactly with the probe-failure reason), a bound outcome installs the qualified binding by
compare-and-set against the exact run generation, approved revision and prior binding, and a lost
compare-and-set — a stop, a superseding revision, a concurrent delivery resolving the open
attempt — orphans the already-published record, closes the open row with the conflict cause and
binds nothing. An exact replay (the same payload-free action re-entering its still-open attempt of
the same approved revision) charges nothing and re-enters that attempt's identity. A host that
composed no port refuses an admitted qualify as the unsupported host fact before reserving
anything (the admission family itself stays with ``test_environment_actions.py`` and the pure
decisions with ``test_environment_governance.py``).

No contract value is restated as a literal: every reason's spelling, the reserved outcome and the
probe name are read from the vocabulary owners (``application.generic_spike``'s registry members,
``core.run``'s attempt outcomes, the generated bound for the ceiling), and the qualifying
binding's shape is built by its own constructor.
"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application import generic_spike, protocol
from application.location import decode_handle
from application.ports import QualificationAttemptBound, QualificationAttemptFailed
from application.run_state import decode_state
from application.protocol import host_profile_id
from application.transaction import Coordinator as ProductionCoordinator
from core import environment
from core import run as core_run
from core.canonical import canonical_digest
from governance_setup import TEST_INVOCATION, approve_current
from test_environment_actions import ENVIRONMENT_ACTION, QUALIFY_ACTION
from test_d7_transactions import Artifacts, Harness, QualifiedSelector, Runs, Workspace
from dataclasses import replace

PROFILE = host_profile_id("pi")
GRAPH = {"root": "C0", "claims": [{"id": "C0", "text": "one claim of supplied context",
                                   "gating": True, "kind": "needs-experiment"}], "edges": []}


def _record_digest(request) -> str:
    return canonical_digest({"qualification record": request.attempt_id, "kind": "record"})


def _binding_digest(request) -> str:
    return canonical_digest({"qualification record": request.attempt_id, "kind": "binding"})


class FakeQualificationPort:
    """The composed port's fake: every outcome is closed and deterministic. ``before_result``
    runs once per attempt, inside the host effect exactly where the E9 transaction called it (its
    nested observations see the run's committed state mid-attempt). A bound outcome is derived
    from the delivered request, so two deliveries read two distinct publishings of their own
    attempt; ``overrides`` pins specific attempts' outcomes for the race tests."""

    def __init__(self, *, reason=None, probe=None, raiser=None, before_result=None, overrides=None):
        self.requests = []
        self.calls = 0
        self.reason = reason
        self.probe = probe
        self.raiser = raiser
        self.before_result = before_result
        self.overrides = dict(overrides or {})

    def run_qualification_attempt(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.before_result is not None:
            self.before_result(request)
        if self.raiser is not None:
            raise self.raiser
        if request.attempt_id in self.overrides:
            return self.overrides[request.attempt_id]
        if self.reason is not None:
            return QualificationAttemptFailed(
                self.reason.value if hasattr(self.reason, "value") else self.reason, self.probe)
        return QualificationAttemptBound(_record_digest(request), _binding_digest(request))


class QualificationEnv:
    """One auto run — the environment approval derives itself (Empirica 7.2 §5) — with one
    approved revision and a composed fake port: the state the transaction's own tests start from."""

    def __init__(self, port, **start):
        self.runs, self.artifacts_repo = Runs(), Artifacts()
        self.port = port
        self.coordinator = ProductionCoordinator(
            Workspace(), Harness(), self.runs, self.artifacts_repo, PROFILE, {},
            QualifiedSelector(), None, port)
        command = {"type": "StartRun", "control_mode": "auto",
                   "goal": "Qualify the run's environment from supplied context.",
                   "invocation": dict(TEST_INVOCATION),
                   "selector": {"project": "p", "session": "q"}, **start}
        result = self.coordinator.handle(command, "start")
        assert result["result"]["type"] == "Allow", result
        self.run_id = result["result"]["run"]["id"]
        self.key = decode_handle(self.run_id)
        self.observe({"kind": "graph", "payload": copy.deepcopy(GRAPH)})
        approve_current(self.coordinator, self.run_id)
        self.observe({"kind": "route", "reason": "primary environment qualification route"})
        self.observe({"kind": "investigate"})
        submitted = self.observe(copy.deepcopy(ENVIRONMENT_ACTION))
        assert submitted["type"] == "Allow", submitted
        member = self.state().environment
        assert member["state"] == environment.APPROVED, member

    def observe(self, action: dict):
        return self.coordinator.handle(
            {"type": "ObserveAction", "run_id": self.run_id, "action": action},
            "transaction-observe")["result"]

    def state(self):
        return self.coordinator.last_state

    def persisted(self):
        return decode_state(self.runs.data[self.key].value)

    def rows(self):
        return [dict(row) for row in self.persisted().qualification_attempts]


class QualificationTransactionTests(unittest.TestCase):
    """The transaction's behaviour cases (spec §5 steps 1 and 6): charged reservations, closed
    outcomes, the binding compare-and-set, orphaned records."""

    def _qualify(self, env: QualificationEnv):
        return env.observe(copy.deepcopy(QUALIFY_ACTION))

    def test_the_reservation_is_durable_before_the_host_attempt_starts(self):
        """The port runs only on its own attempt's committed reserved row (spec §5 step 1): the
        fake port reads the persisted state it is handed — the row is already durable ``reserved``
        with its own reserved stamp against the unqualified StartRun binding — proving the
        reservation precedes every effect of the attempt."""
        seen = {}

        env = None

        def inside_the_effect(request):
            state = decode_state(env.runs.data[env.key].value)
            row = next(item for item in state.qualification_attempts
                       if item["attempt_id"] == request.attempt_id)
            seen["row"] = dict(row)
            seen["stamp_seq"] = state.stamp_seq
            seen["binding"] = dict(state.spike_backend)

        env = QualificationEnv(FakeQualificationPort(before_result=inside_the_effect))
        result = self._qualify(env)
        self.assertEqual(result["type"], "Allow", result)
        self.assertEqual(env.port.calls, 1)
        self.assertEqual(seen["row"]["outcome"], core_run.ATTEMPT_OUTCOMES[0])
        self.assertEqual(seen["row"]["started_stamp"], seen["stamp_seq"])
        self.assertEqual(seen["row"]["attempt_id"], env.port.requests[0].attempt_id)
        self.assertEqual(seen["binding"], {"kind": "unqualified"},
                         "the attempt starts against the unqualified StartRun binding")

    def test_a_bound_outcome_installs_the_binding_by_compare_and_set(self):
        env = QualificationEnv(FakeQualificationPort())
        result = self._qualify(env)
        self.assertTrue(protocol.validate_public_result(result), result)
        self.assertEqual(result["type"], "Allow", result)
        request = env.port.requests[0]
        expected = generic_spike.GenericQualifiedBackend(
            "docker", request.profile_id, _record_digest(request),
            _binding_digest(request)).to_data()
        state = env.persisted()
        self.assertEqual(dict(state.spike_backend), expected)
        row = env.rows()[0]
        self.assertEqual(row["outcome"], "succeeded")
        self.assertEqual(row["record_digest"], _record_digest(request))
        self.assertIsNone(row["reason"], "a succeeded attempt carries no cause")
        self.assertIsNone(row["probe"])
        view = env.coordinator.handle({"type": "GetRun", "run_id": env.run_id}, "read")["result"]
        self.assertEqual(view["run"]["spike_backend"], expected)

    def test_a_failed_attempt_records_its_reason_and_never_changes_the_binding(self):
        reason = generic_spike.GenericUnavailableReason.DOCKER_UNREACHABLE
        env = QualificationEnv(FakeQualificationPort(reason=reason))
        result = self._qualify(env)
        self.assertTrue(protocol.validate_public_result(result), result)
        self.assertEqual(result["type"], "Fault", result)
        self.assertEqual(result["code"], "unavailable", result)
        self.assertEqual(result["reason"], reason.value, result)
        self.assertNotIn("probe", result)
        row = env.rows()[0]
        self.assertEqual(row["outcome"], "failed")
        self.assertIsNone(row["record_digest"])
        self.assertEqual(row["reason"], reason.value)
        self.assertEqual(dict(env.persisted().spike_backend), {"kind": "unqualified"})

    def test_a_probe_failure_records_its_probe_and_the_fault_names_it(self):
        reason = generic_spike.GenericUnavailableReason.QUALIFICATION_PROBE_FAILED
        probe = core_run.QUALIFICATION_PROBE_NAMES[0]
        env = QualificationEnv(FakeQualificationPort(reason=reason, probe=probe))
        result = self._qualify(env)
        self.assertEqual(result["type"], "Fault", result)
        self.assertEqual(result["reason"], reason.value, result)
        self.assertEqual(result["probe"], probe, result)
        row = env.rows()[0]
        self.assertEqual(row["outcome"], "failed")
        self.assertEqual(row["reason"], reason.value)
        self.assertEqual(row["probe"], probe)
        self.assertEqual(dict(env.persisted().spike_backend), {"kind": "unqualified"})

    def test_an_interrupted_attempt_leaves_an_open_charged_reservation_and_no_binding(self):
        env = QualificationEnv(
            FakeQualificationPort(raiser=RuntimeError("the host died mid-attempt")))
        result = self._qualify(env)
        self.assertTrue(protocol.validate_public_result(result), result)
        self.assertEqual(result, {"type": "Fault", "code": "unavailable",
                                 "fail_direction": "closed"},
                         "an internal host failure carries no closed reason: retry once")
        rows = env.rows()
        self.assertEqual(len(rows), 1, "the interrupted attempt is charged by its open row")
        self.assertEqual(rows[0]["outcome"], core_run.ATTEMPT_OUTCOMES[0])
        self.assertEqual(rows[0]["attempt_id"], env.port.requests[0].attempt_id)
        self.assertEqual(dict(env.persisted().spike_backend), {"kind": "unqualified"})

    def test_an_exact_replay_reenters_the_open_attempt_without_a_new_charge(self):
        """The same payload-free action, re-issued while its own attempt of this same approved
        revision is still open (the interrupted attempt above): the replay reserves nothing,
        re-enters the open row's identity, and its outcome resolves that one row."""
        port = FakeQualificationPort(raiser=RuntimeError("first delivery died"))
        env = QualificationEnv(port)
        self._qualify(env)
        port.raiser = None
        result = self._qualify(env)
        self.assertEqual(result["type"], "Allow", result)
        rows = env.rows()
        self.assertEqual(len(rows), 1, "an exact replay adds no row: the open one is resolved")
        self.assertEqual(rows[0]["outcome"], "succeeded")
        self.assertEqual(rows[0]["attempt_id"], env.port.requests[0].attempt_id)
        self.assertLessEqual(len(env.persisted().qualification_attempts),
                             environment.MAX_QUALIFICATION_ATTEMPTS)

    def test_a_new_request_after_a_resolved_attempt_is_charged_afresh(self):
        """A failed attempt is resolved, so a further qualify on the same approved revision is a
        new charged attempt (the recovery 'request qualification again'), never a replay."""
        reason = generic_spike.GenericUnavailableReason.DOCKER_UNREACHABLE
        env = QualificationEnv(FakeQualificationPort(reason=reason))
        self._qualify(env)
        self._qualify(env)
        self.assertEqual(len(env.rows()), 2,
                         "a resolved row never absorbs the next request; the run pays again")
        self.assertEqual([row["outcome"] for row in env.rows()], ["failed", "failed"])
        self.assertEqual({row["attempt_id"] for row in env.rows()},
                         {request.attempt_id for request in env.port.requests})

    def test_a_lost_cas_on_a_superseded_revision_orphans_the_record(self):
        """A newer approved revision committed mid-attempt is a compare-and-set loser (spec §5
        step 6): the loser's already-published record is an inert orphan, the row closes with the
        conflict cause, nothing binds (the unqualified run stays unqualified)."""
        seen = {}

        def supersede_mid_attempt(request):
            revised = copy.deepcopy(ENVIRONMENT_ACTION)
            revised["payload"]["rationale"] = "a second revision the agent needs mid-attempt"
            outcome = env.observe(revised)
            assert outcome["type"] == "Allow", outcome
            seen["revision"] = env.state().environment["revision"]

        env = QualificationEnv(FakeQualificationPort(before_result=supersede_mid_attempt))
        result = self._qualify(env)
        self.assertEqual(seen["revision"], 2)
        self.assertEqual(result["type"], "Fault", result)
        self.assertEqual(
            result["reason"],
            generic_spike.GenericUnavailableReason.QUALIFICATION_CONFLICT.value, result)
        rows = env.rows()
        self.assertEqual(len(rows), 1, "the superseding revision never reserved: it is approved")
        self.assertEqual(rows[0]["outcome"], "superseded")
        self.assertIsNone(rows[0]["record_digest"], "the published record is orphaned")
        self.assertEqual(rows[0]["reason"],
                         generic_spike.GenericUnavailableReason.QUALIFICATION_CONFLICT.value)
        self.assertEqual(dict(env.persisted().spike_backend), {"kind": "unqualified"},
                         "the orphaned record binds nothing")

    def test_a_stop_during_the_attempt_is_a_cas_loser_that_records_its_charge(self):
        def stop_mid_attempt(request):
            stopped = env.coordinator.handle(
                {"type": "EvaluateRun", "run_id": env.run_id, "intent": "stop"}, "stop")
            assert stopped["result"]["type"] == "Allow", stopped

        env = QualificationEnv(FakeQualificationPort(before_result=stop_mid_attempt))
        result = self._qualify(env)
        self.assertEqual(result["type"], "Fault", result)
        self.assertEqual(
            result["reason"],
            generic_spike.GenericUnavailableReason.QUALIFICATION_CONFLICT.value, result)
        rows = env.rows()
        self.assertEqual(rows[0]["outcome"], "superseded")
        self.assertIsNone(rows[0]["record_digest"], "a stopped run never adopts the record")
        self.assertEqual(env.persisted().status, "stopped_residual")
        self.assertEqual(dict(env.persisted().spike_backend), {"kind": "unqualified"},
                         "a stop during the attempt changes no binding")

    def test_concurrent_deliveries_bind_once_and_orphan_the_loser(self):
        """Two coordinators, one run, both mid-attempt: both deliver the run's single open attempt
        (the reservation absorbs the concurrent delivery; one charge is ever paid per open
        attempt), the nested delivery binds, and the outer one's already-published record loses
        the compare-and-set and stays an inert orphan — one binding, one resolved row."""
        nested_port = FakeQualificationPort()
        nested_result = {}

        def run_the_second_coordinator(request):
            second = ProductionCoordinator(
                Workspace(), Harness(), env.runs, env.artifacts_repo, PROFILE, {},
                QualifiedSelector(), None, nested_port)
            response = second.handle(
                {"type": "ObserveAction", "run_id": env.run_id,
                 "action": copy.deepcopy(QUALIFY_ACTION)}, "concurrent")
            nested_result.clear()
            nested_result.update(response)

        env = QualificationEnv(FakeQualificationPort(before_result=run_the_second_coordinator))
        result = self._qualify(env)
        self.assertEqual(result["type"], "Fault", result)
        self.assertEqual(
            result["reason"],
            generic_spike.GenericUnavailableReason.QUALIFICATION_CONFLICT.value, result)
        self.assertEqual(nested_result["result"]["type"], "Allow", nested_result)
        rows = env.rows()
        self.assertEqual(len(rows), 1, "the in-flight attempt absorbs the concurrent delivery")
        self.assertEqual(rows[0]["outcome"], "succeeded")
        installed = dict(env.persisted().spike_backend)
        self.assertEqual(installed["kind"], "qualified")
        self.assertEqual(installed["qualification_record_digest"],
                         _record_digest(nested_port.requests[0]))
        self.assertEqual(env.port.calls, 1)
        self.assertEqual(nested_port.calls, 1)

    def test_at_the_attempt_ceiling_the_admission_refuses_before_any_effect(self):
        """Once the run's attempt ceiling is spent the admission refuses with its own closed
        reason (now against live rows, where ``test_environment_governance`` derives it from the
        pure decision): nothing reserved, the port never called."""
        env = QualificationEnv(FakeQualificationPort())

        def seed_attempts():
            read = env.runs.data[env.key]
            state = decode_state(read.value)
            revision = state.environment["revision"]
            rows = tuple(
                {"attempt_id": f"qualification-{index}", "revision": revision,
                 "started_stamp": index, "outcome": core_run.ATTEMPT_OUTCOMES[3],
                 "record_digest": None, "reason": None, "probe": None}
                for index in range(1, environment.MAX_QUALIFICATION_ATTEMPTS + 1))
            snapshot = env.coordinator._assemble(
                env.key, state, {"type": "GetRun", "run_id": env.run_id}, require_graph=False)
            env.coordinator._commit(env.key, read.revision, snapshot,
                                    replace(state, qualification_attempts=rows), ())

        seed_attempts()
        before = env.rows()
        result = self._qualify(env)
        self.assertEqual(result["type"], "Block", result)
        self.assertEqual([reason["code"] for reason in result["reasons"]],
                         [environment.QUALIFICATION_ATTEMPTS_EXHAUSTED])
        self.assertEqual(env.port.calls, 0)
        self.assertEqual(env.rows(), before)


if __name__ == "__main__":
    unittest.main()
