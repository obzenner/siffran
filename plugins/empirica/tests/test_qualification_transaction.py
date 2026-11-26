#!/usr/bin/env python3
"""Empirica 7.2 E9: the ``qualify`` attempt transaction — settle, reserve, effect, binding CAS (spec §5).

Behaviour only, over the real composed service with a fake port and no Docker: the reservation is
durable before the host attempt starts (charged even when the attempt then fails or is
interrupted), the outcome is recorded with the closed reason of its own family (and the failed
probe exactly with the probe-failure reason), a bound outcome installs the qualified binding by
compare-and-set against the exact facts the reservation captured, and a lost compare-and-set — a
stop, a superseding revision — orphans the already-published record, closes the open row with the
conflict cause and binds nothing.

The delivery is the request: an exact replay of a delivery whose attempt resolved returns its
recorded outcome with no charge and no effect. A qualify that finds an open row refuses with the
closed in-progress reason while another attempt holds the shared runtime lock — taken before
anything is reserved or settled and held to the end of the outcome, so an open row seen under the
lock is dead — and closes it as interrupted — charged, binding nothing, any record it published an
orphan — before evaluating the request as a fresh attempt. The qualification passes the current
configuration approval before any reservation or effect, and a host that composed no port (a
degraded composition, an unsupported profile) refuses an admitted qualify as the unsupported host
fact before reserving anything (the admission family itself stays with
``test_environment_actions.py`` and the pure decisions with ``test_environment_governance.py``).

No contract value is restated as a literal: every reason's spelling, the reserved outcome and the
probe name are read from the vocabulary owners (``application.generic_spike``'s registry members,
``core.run``'s attempt outcomes, the generated bound for the ceiling), and the qualifying
binding's shape is built by its own constructor.
"""
import copy
import sys
import threading
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application import generic_spike, protocol
from application.location import decode_handle
from application.ports import (QualificationAttemptBound, QualificationAttemptFailed,
                               QualificationRuntimeBusy)
from application import run_state
from application.run_state import decode_state
from application.protocol import host_profile_id
from application.observation import ObservationUnavailable
from application.snapshot import GraphInvalid, HistoryCorrupt
from application.transaction import RETRY, Assembled
from application.transaction import Coordinator as ProductionCoordinator
from core import environment
from core import run as core_run
from core.canonical import canonical_digest
from core.records import Corrupt, Present, Revision
from governance_setup import TEST_INVOCATION, approve_current
from test_environment_actions import CONFIGURE_RUN, ENVIRONMENT_ACTION, QUALIFY_ACTION
from test_d7_transactions import Artifacts, Harness, QualifiedSelector, Runs, Workspace
from dataclasses import replace
from unittest.mock import patch

PROFILE = host_profile_id("pi")
GRAPH = {"root": "C0", "claims": [{"id": "C0", "text": "one claim of supplied context",
                                   "gating": True, "kind": "needs-experiment"}], "edges": []}


def _record_digest(request) -> str:
    return canonical_digest({"qualification record": request.attempt_id, "kind": "record"})


def _binding_digest(request) -> str:
    return canonical_digest({"qualification record": request.attempt_id, "kind": "binding"})


class ProcessDied(BaseException):
    """The host process died mid-attempt: unlike an ``Exception`` it is not an answer the
    transaction can catch, so nothing after the raise point runs — exactly a crash."""


class FakeQualificationPort:
    """The composed port's fake: every outcome is closed and deterministic. ``before_result``
    runs once per attempt, inside the host effect exactly where the transaction calls it (its
    nested observations see the run's committed state mid-attempt). A bound outcome is derived
    from the delivered request, so two deliveries read two distinct publishings of their own
    attempt; ``overrides`` pins specific attempts' outcomes for the race tests. ``published``
    records every record the fake published, ``publish_then`` makes the attempt die only after
    publishing (the crash between record publication and the outcome commit). The runtime lease is
    the shared lock: one ``lock`` (shared between fakes to model one runtime), taken without waiting
    and busy while any holder has it; the settle half recovers and closes the row under the lease."""

    def __init__(self, *, reason=None, probe=None, raiser=None, before_result=None, overrides=None,
                 publish_then=None, lock=None, settle_failure=None,
                 before_close=None, settle_raises=None, settle_skips_close=False):
        self.requests = []
        self.calls = 0
        self.reason = reason
        self.probe = probe
        self.raiser = raiser
        self.before_result = before_result
        self.overrides = dict(overrides or {})
        self.publish_then = publish_then
        self.published = []
        self.lock = lock if lock is not None else threading.Lock()
        self.settle_failure = settle_failure
        self.settles = []
        self.before_close = before_close
        self.settle_raises = settle_raises
        self.settle_skips_close = settle_skips_close

    @contextmanager
    def runtime_lease(self):
        if not self.lock.acquire(blocking=False):
            raise QualificationRuntimeBusy("the runtime lock is held")
        try:
            yield self.lock
        finally:
            self.lock.release()

    def run_qualification_attempt(self, request, lease):
        assert lease is self.lock and self.lock.locked(), "the attempt runs under the lease"
        self.calls += 1
        self.requests.append(request)
        if self.before_result is not None:
            self.before_result(request)
        if self.publish_then is not None:
            self.published.append(_record_digest(request))
            raise self.publish_then
        if self.raiser is not None:
            raise self.raiser
        if request.attempt_id in self.overrides:
            return self.overrides[request.attempt_id]
        if self.reason is not None:
            return QualificationAttemptFailed(
                self.reason.value if hasattr(self.reason, "value") else self.reason, self.probe)
        self.published.append(_record_digest(request))
        return QualificationAttemptBound(_record_digest(request), _binding_digest(request))

    def settle_interrupted_attempt(self, request, close, lease):
        assert lease is self.lock and self.lock.locked(), "the settle runs under the lease"
        self.settles.append(request)
        if self.settle_raises is not None:
            raise self.settle_raises
        if self.settle_failure is not None:
            return QualificationAttemptFailed(self.settle_failure)
        if self.before_close is not None:
            self.before_close(request)
        if not self.settle_skips_close:
            close()
        return True


class QualificationEnv:
    """One auto run — the environment approval derives itself (Empirica 7.2 §5) — with one
    approved revision and a composed fake port: the state the transaction's own tests start from.
    Every observation is its own delivery (a distinct request identity) unless a test names one."""

    def __init__(self, port, profile_id=PROFILE, **start):
        self.runs, self.artifacts_repo = Runs(), Artifacts()
        self.port = port
        self.deliveries = 0
        self.coordinator = ProductionCoordinator(
            Workspace(), Harness(), self.runs, self.artifacts_repo, profile_id, {},
            QualifiedSelector(), None, port)
        self.start = start
        self._start_and_approve()

    def _start_and_approve(self):
        command = {"type": "StartRun", "control_mode": "auto",
                   "goal": "Qualify the run's environment from supplied context.",
                   "invocation": dict(TEST_INVOCATION),
                   "selector": {"project": "p", "session": "q"}, **self.start}
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

    def restart(self):
        """Stop the run and start the same selector again: the next generation of the same run,
        taken through the same environment cycle to its approved revision."""
        stopped = self.coordinator.handle(
            {"type": "EvaluateRun", "run_id": self.run_id, "intent": "stop"}, "restart-stop")
        assert stopped["result"]["type"] == "Allow", stopped
        self._start_and_approve()

    def observe(self, action: dict, request_id: str | None = None):
        self.deliveries += 1
        return self.coordinator.handle(
            {"type": "ObserveAction", "run_id": self.run_id, "action": action},
            request_id or f"transaction-observe-{self.deliveries}")["result"]

    def qualify(self, request_id: str | None = None):
        return self.observe(copy.deepcopy(QUALIFY_ACTION), request_id)

    def state(self):
        return self.coordinator.last_state

    def persisted_document(self):
        return self.runs.data[self.key].value

    def persisted(self):
        return decode_state(self.persisted_document())

    def rows(self):
        return [dict(row) for row in self.persisted().qualification_attempts]

    def binding(self):
        return dict(self.persisted().spike_backend)


CONFLICT = generic_spike.GenericUnavailableReason.QUALIFICATION_CONFLICT.value
IN_PROGRESS = generic_spike.GenericUnavailableReason.QUALIFICATION_IN_PROGRESS.value
INTERRUPTED = generic_spike.GenericUnavailableReason.QUALIFICATION_INTERRUPTED.value
RESERVED, SUCCEEDED, FAILED, SUPERSEDED = core_run.ATTEMPT_OUTCOMES


class QualificationTransactionTests(unittest.TestCase):
    """The transaction's behaviour cases (spec §5 steps 1 and 6): charged reservations, closed
    outcomes, the binding compare-and-set, orphaned records."""

    def _qualify(self, env: QualificationEnv, request_id: str | None = None):
        return env.qualify(request_id)

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
        self.assertEqual(seen["row"]["outcome"], RESERVED)
        self.assertEqual(seen["row"]["started_stamp"], seen["stamp_seq"])
        self.assertEqual(seen["row"]["attempt_id"], env.port.requests[0].attempt_id)
        self.assertEqual(seen["binding"], {"kind": "unqualified"},
                         "the attempt starts against the unqualified StartRun binding")

    def test_the_attempt_binds_the_storage_run_identity_never_the_public_handle(self):
        """The host attempt is handed the bounded storage run identifier beside the separate
        project and generation fields (the owner admits an identifier of at most the bound the
        record contract owns); the encoded public handle is a different, longer string."""
        env = QualificationEnv(FakeQualificationPort())
        self._qualify(env)
        request = env.port.requests[0]
        self.assertEqual((request.project_id, request.run_id, request.generation),
                         (env.key.project_id, env.key.run_id, env.key.generation))
        self.assertNotEqual(request.run_id, env.run_id)

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
        self.assertEqual(row["outcome"], SUCCEEDED)
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
        self.assertEqual(row["outcome"], FAILED)
        self.assertIsNone(row["record_digest"])
        self.assertEqual(row["reason"], reason.value)
        self.assertEqual(env.binding(), {"kind": "unqualified"})

    def test_a_failed_requalification_keeps_the_existing_qualified_binding(self):
        """Failure never changes the active binding (spec §5 step 6): a run qualified once, asked
        to qualify again, keeps its first binding when the second attempt fails — the outcome
        compare-and-set holds the binding the reservation captured."""
        port = FakeQualificationPort()
        env = QualificationEnv(port)
        self.assertEqual(self._qualify(env)["type"], "Allow")
        first = env.binding()
        self.assertEqual(first["kind"], "qualified")
        port.reason = generic_spike.GenericUnavailableReason.DOCKER_UNREACHABLE
        result = self._qualify(env)
        self.assertEqual(result["type"], "Fault", result)
        self.assertEqual(env.binding(), first)
        self.assertEqual([row["outcome"] for row in env.rows()], [SUCCEEDED, FAILED])

    def test_a_probe_failure_records_its_probe_and_the_fault_names_it(self):
        reason = generic_spike.GenericUnavailableReason.QUALIFICATION_PROBE_FAILED
        probe = core_run.QUALIFICATION_PROBE_NAMES[0]
        env = QualificationEnv(FakeQualificationPort(reason=reason, probe=probe))
        result = self._qualify(env)
        self.assertEqual(result["type"], "Fault", result)
        self.assertEqual(result["reason"], reason.value, result)
        self.assertEqual(result["probe"], probe, result)
        row = env.rows()[0]
        self.assertEqual(row["outcome"], FAILED)
        self.assertEqual(row["reason"], reason.value)
        self.assertEqual(row["probe"], probe)
        self.assertEqual(env.binding(), {"kind": "unqualified"})

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
        self.assertEqual(rows[0]["outcome"], RESERVED)
        self.assertEqual(rows[0]["attempt_id"], env.port.requests[0].attempt_id)
        self.assertEqual(env.binding(), {"kind": "unqualified"})

    def test_a_malformed_port_outcome_is_an_unreasoned_failure_that_records_and_binds_nothing(self):
        good = "sha256:" + "a" * 64
        probe_failed = core_run.QUALIFICATION_PROBE_FAILED_REASON
        registered = generic_spike.GenericUnavailableReason.DOCKER_UNREACHABLE.value
        malformed = {
            "a malformed record digest": QualificationAttemptBound("sha256:xyz", good),
            "a malformed binding digest": QualificationAttemptBound(good, "sha256:xyz"),
            "an unregistered reason": QualificationAttemptFailed("not-a-registered-reason"),
            "an admission reason": QualificationAttemptFailed(environment.ENVIRONMENT_REQUIRED),
            "a probe without the probe reason": QualificationAttemptFailed(
                registered, core_run.QUALIFICATION_PROBE_NAMES[0]),
            "the probe reason without a probe": QualificationAttemptFailed(probe_failed),
            "an unknown probe name": QualificationAttemptFailed(probe_failed, "not-a-probe"),
            "a stray object": object(),
        }
        for label, outcome in malformed.items():
            with self.subTest(label):
                port = FakeQualificationPort()
                env = QualificationEnv(port)
                port.overrides = {
                    request_id: outcome for request_id in [
                        environment.qualification_attempt_id(1, "transaction-observe-9")]}
                result = self._qualify(env, "transaction-observe-9")
                self.assertEqual(result, {"type": "Fault", "code": "unavailable",
                                         "fail_direction": "closed"}, result)
                self.assertEqual([row["outcome"] for row in env.rows()], [RESERVED])
                self.assertEqual(env.binding(), {"kind": "unqualified"})

    # --- exact replay of a delivery --------------------------------------------------------

    def test_an_exact_replay_of_a_completed_delivery_returns_its_outcome_without_a_charge(self):
        """The recorded outcome of a bound attempt returns to the same delivery again: no new row,
        no effect — also the recovery of a response lost after the outcome commit."""
        env = QualificationEnv(FakeQualificationPort())
        first = self._qualify(env, "delivery-1")
        again = self._qualify(env, "delivery-1")
        self.assertEqual(first["type"], "Allow", first)
        self.assertEqual(again["type"], "Allow", again)
        self.assertEqual(again["run"]["spike_backend"], first["run"]["spike_backend"])
        self.assertEqual(len(env.rows()), 1, "a replay never charges")
        self.assertEqual(env.port.calls, 1, "a replay never repeats the effect")

    def test_an_exact_replay_of_a_failed_delivery_returns_the_same_closed_fault(self):
        reason = generic_spike.GenericUnavailableReason.QUALIFICATION_PROBE_FAILED
        probe = core_run.QUALIFICATION_PROBE_NAMES[0]
        env = QualificationEnv(FakeQualificationPort(reason=reason, probe=probe))
        first = self._qualify(env, "delivery-1")
        again = self._qualify(env, "delivery-1")
        self.assertEqual(again, first)
        self.assertEqual((len(env.rows()), env.port.calls), (1, 1))

    def test_an_exact_replay_of_a_lost_compare_and_set_returns_the_conflict(self):
        env = None

        def supersede(request):
            revised = copy.deepcopy(ENVIRONMENT_ACTION)
            revised["payload"]["rationale"] = "a second revision the agent needs mid-attempt"
            assert env.observe(revised)["type"] == "Allow"

        env = QualificationEnv(FakeQualificationPort(before_result=supersede))
        first = self._qualify(env, "delivery-1")
        again = self._qualify(env, "delivery-1")
        self.assertEqual(first["reason"], CONFLICT)
        self.assertEqual(again, first)
        self.assertEqual((len(env.rows()), env.port.calls), (1, 1))

    def test_a_new_delivery_after_a_resolved_attempt_is_charged_afresh(self):
        """A failed attempt is resolved, so a further qualify — a different delivery — on the same
        approved revision is a new charged attempt (the recovery 'request qualification again')."""
        reason = generic_spike.GenericUnavailableReason.DOCKER_UNREACHABLE
        env = QualificationEnv(FakeQualificationPort(reason=reason))
        self._qualify(env, "delivery-1")
        self._qualify(env, "delivery-2")
        self.assertEqual(len(env.rows()), 2,
                         "a resolved row never absorbs the next delivery; the run pays again")
        self.assertEqual([row["outcome"] for row in env.rows()], [FAILED, FAILED])
        self.assertEqual({row["attempt_id"] for row in env.rows()},
                         {request.attempt_id for request in env.port.requests})

    # --- live and interrupted attempts -----------------------------------------------------

    def test_a_live_attempt_refuses_a_concurrent_delivery_without_a_charge_or_an_effect(self):
        """While one attempt is mid-effect, another delivery on the run — here a second
        coordinator of the same process, a different request — refuses with the closed
        in-progress reason: nothing reserved, charged or attempted, the live attempt unharmed and
        later binding once."""
        runtime = threading.Lock()
        nested_port = FakeQualificationPort(lock=runtime)
        nested_result = {}

        def run_the_second_coordinator(request):
            second = ProductionCoordinator(
                Workspace(), Harness(), env.runs, env.artifacts_repo, PROFILE, {},
                QualifiedSelector(), None, nested_port)
            nested_result.update(second.handle(
                {"type": "ObserveAction", "run_id": env.run_id,
                 "action": copy.deepcopy(QUALIFY_ACTION)}, "concurrent"))

        env = QualificationEnv(FakeQualificationPort(
            before_result=run_the_second_coordinator, lock=runtime))
        result = self._qualify(env)
        self.assertEqual(result["type"], "Allow", result)
        refusal = nested_result["result"]
        self.assertTrue(protocol.validate_public_result(refusal), refusal)
        self.assertEqual((refusal["type"], refusal["code"]), ("Fault", "unavailable"), refusal)
        self.assertEqual(refusal["reason"], IN_PROGRESS, refusal)
        self.assertEqual((nested_port.calls, nested_port.settles), (0, []),
                         "a live attempt is never settled and never repeated")
        rows = env.rows()
        self.assertEqual([row["outcome"] for row in rows], [SUCCEEDED],
                         "the refusal reserved nothing: one charge for one attempt")
        self.assertEqual(env.binding()["kind"], "qualified")

    def test_a_held_runtime_lock_refuses_in_progress_before_anything_is_reserved_or_closed(self):
        """Another attempt of the runtime holds the shared lock: the qualify refuses as in progress
        whether the run has no open row (nothing is reserved or charged) or a dead one (nothing is
        closed) — the lock is taken before either — and no effect runs; released, it proceeds."""
        for label, dead_attempt in (("no open row", False), ("a dead open row", True)):
            with self.subTest(label):
                port = FakeQualificationPort(
                    raiser=RuntimeError("the host died mid-attempt") if dead_attempt else None)
                env = QualificationEnv(port)
                if dead_attempt:
                    self._qualify(env)
                    port.raiser = None
                before, calls = env.rows(), port.calls
                with env.port.runtime_lease():
                    result = self._qualify(env)
                self.assertEqual((result["type"], result["code"]), ("Fault", "unavailable"), result)
                self.assertEqual(result["reason"], IN_PROGRESS, result)
                self.assertEqual((env.rows(), port.calls, port.settles), (before, calls, []))
                self.assertEqual(self._qualify(env)["type"], "Allow")

    def test_the_runtime_lock_is_held_from_before_the_reservation_to_after_the_outcome(self):
        """The order the transaction keeps: lock, (close an interrupted row), reserve, effect,
        outcome, release — every durable commit of the call happens under the lock, and the lock
        is free again once it returns, whether the attempt bound, failed or was interrupted."""
        for label, port in (
                ("bound", FakeQualificationPort()),
                ("failed", FakeQualificationPort(
                    reason=generic_spike.GenericUnavailableReason.DOCKER_UNREACHABLE)),
                ("interrupted", FakeQualificationPort(raiser=RuntimeError("died")))):
            with self.subTest(label):
                env = QualificationEnv(port)
                held_at_commit = []
                commit = env.coordinator._commit

                def observing_commit(*args, **kwargs):
                    held_at_commit.append(port.lock.locked())
                    return commit(*args, **kwargs)

                with patch.object(env.coordinator, "_commit", observing_commit):
                    self._qualify(env)
                    if label == "interrupted":
                        port.raiser = None
                        self._qualify(env)
                self.assertEqual(held_at_commit, [True] * len(held_at_commit))
                self.assertEqual(len(held_at_commit), 4 if label == "interrupted" else 2,
                                 "reservation and outcome (and, after a death, the closing again)")
                self.assertFalse(port.lock.locked())

    def test_an_interrupted_attempt_is_closed_by_the_next_qualify_and_a_fresh_one_runs(self):
        """The attempt died before publishing anything (a plain host failure): the next delivery
        finds an open row under the lock it took, settles it — recovery under the
        lock, then the row closes failed-interrupted (charged, no record, no binding) — and only
        then is its own request a fresh charged attempt that binds."""
        port = FakeQualificationPort(raiser=RuntimeError("the host died mid-attempt"))
        env = QualificationEnv(port)
        self._qualify(env)
        dead = env.rows()[0]["attempt_id"]
        port.raiser = None
        result = self._qualify(env)
        self.assertEqual(result["type"], "Allow", result)
        rows = env.rows()
        self.assertEqual([row["outcome"] for row in rows], [FAILED, SUCCEEDED])
        self.assertEqual(rows[0]["attempt_id"], dead)
        self.assertEqual(rows[0]["reason"], INTERRUPTED)
        self.assertIsNone(rows[0]["record_digest"])
        self.assertIsNone(rows[0]["probe"])
        self.assertNotEqual(rows[1]["attempt_id"], dead, "a dead attempt is never reused or resumed")
        self.assertEqual([request.attempt_id for request in port.requests], [dead, rows[1]["attempt_id"]])
        settle = port.settles[0]
        self.assertEqual((settle.project_id, settle.run_id, settle.generation, settle.attempt_id),
                         (env.key.project_id, env.key.run_id, env.key.generation, dead))
        self.assertEqual(settle.profile_id, port.requests[0].profile_id,
                         "recovery runs on the profile the dead attempt's revision proposed")
        self.assertEqual(env.binding()["qualification_record_digest"],
                         _record_digest(port.requests[1]))

    def test_an_unreadable_run_answers_as_every_other_command_does_before_and_after_the_effect(self):
        """A run that is corrupt, a legacy document or gone — when the qualify reads it and again
        when the attempt's outcome is about to be recorded — answers exactly what a read of the
        same stored run answers (one shared classification), and records and binds nothing."""
        stored = {
            "corrupt": lambda env: Corrupt("bad bytes"),
            "legacy": lambda env: Present(
                {"protocol": env.persisted_document()["protocol"],
                 "state_schema": run_state.LEGACY_STATE_SCHEMA_ID}, Revision("legacy")),
            "absent": lambda env: None,
        }
        for label, make in stored.items():
            for phase in ("before the effect", "after the effect"):
                with self.subTest(label, phase=phase):
                    def damage(env):
                        replacement = make(env)
                        if replacement is None:
                            del env.runs.data[env.key]
                        else:
                            env.runs.data[env.key] = replacement

                    env = QualificationEnv(FakeQualificationPort())
                    if phase == "before the effect":
                        damage(env)
                    else:
                        env.port.before_result = lambda request: damage(env)
                    result = self._qualify(env)
                    expected = env.coordinator.handle(
                        {"type": "GetRun", "run_id": env.run_id}, "read")["result"]
                    self.assertEqual(result, expected)
                    self.assertEqual(env.port.calls, 0 if phase == "before the effect" else 1)

    def test_the_shared_read_closes_each_assembly_failure_and_gates_on_an_active_run(self):
        """The one author-surface read both qualify phases use: a failed assembly asks for a re-read
        when the run moved and otherwise closes as the sibling transactions do; an inactive run is
        Inert unless the caller reads it as it stands."""
        env = QualificationEnv(FakeQualificationPort())
        coordinator, key = env.coordinator, env.key
        command = {"type": "GetRun", "run_id": env.run_id}
        state = env.persisted()
        run_corrupt, graph_invalid = "run.corrupt", "graph.invalid"
        self.assertLessEqual({run_corrupt, graph_invalid}, set(protocol.public_contract()["reasons"]))
        failures = (
            (HistoryCorrupt(), lambda: coordinator._safe_block(key, "read", run_corrupt)),
            (GraphInvalid(), lambda: coordinator._block_from_state(
                key, state, command, "read", graph_invalid)),
            (ObservationUnavailable(), lambda: coordinator._fault("read", "unavailable")))
        for exception, expected in failures:
            with self.subTest(type(exception).__name__):
                with patch.object(coordinator, "_assemble", side_effect=exception):
                    self.assertEqual(coordinator._load_assembled(key, command, "read"), expected())
                    if not isinstance(exception, ObservationUnavailable):
                        with patch.object(coordinator, "_revision_unchanged", return_value=False):
                            self.assertIs(coordinator._load_assembled(key, command, "read"), RETRY)
        stopped = coordinator.handle(
            {"type": "EvaluateRun", "run_id": env.run_id, "intent": "stop"}, "stop")["result"]
        self.assertEqual(stopped["type"], "Allow", stopped)
        self.assertEqual(coordinator._load_assembled(key, command, "read"),
                         coordinator._inert("read"))
        read = coordinator._load_assembled(key, command, "read", inactive_ok=True)
        self.assertIsInstance(read, Assembled)
        self.assertEqual(read.state.status, env.persisted().status)

    def test_an_exception_escaping_the_call_never_leaks_the_runtime_lock(self):
        """In a long-lived bridge process nothing frees a lock on a crash but the call's own
        unwinding: whatever escapes the transaction — the attempt, the settle, a commit — the lock
        is free again, so the next qualify takes it instead of refusing in progress forever."""
        for label in ("attempt", "settle", "commit"):
            with self.subTest(label):
                port = FakeQualificationPort(
                    raiser=RuntimeError("the host died mid-attempt") if label == "settle" else None)
                env = QualificationEnv(port)
                if label == "settle":
                    self._qualify(env)
                    port.raiser, port.settle_raises = None, ProcessDied()
                elif label == "attempt":
                    port.raiser = ProcessDied()
                commit = (patch.object(env.coordinator, "_commit", side_effect=ProcessDied())
                          if label == "commit" else nullcontext())
                with commit, self.assertRaises(ProcessDied):
                    self._qualify(env)
                self.assertFalse(port.lock.locked(), "the unwinding released the lock")
                port.raiser = port.settle_raises = None
                self.assertEqual(self._qualify(env)["type"], "Allow")

    def test_a_crash_after_the_record_was_published_orphans_it(self):
        """The process died after its record was durably published and before the outcome commit:
        nothing was recorded, so the next qualify closes the row interrupted with no digest, and
        the published record names no row and no binding."""
        port = FakeQualificationPort(publish_then=ProcessDied())
        env = QualificationEnv(port)
        with self.assertRaises(ProcessDied):
            self._qualify(env)
        orphan = port.published[0]
        self.assertEqual([row["outcome"] for row in env.rows()], [RESERVED])
        self.assertEqual(env.binding(), {"kind": "unqualified"})
        port.publish_then = None
        result = self._qualify(env)
        self.assertEqual(result["type"], "Allow", result)
        rows = env.rows()
        self.assertEqual([(row["outcome"], row["reason"]) for row in rows],
                         [(FAILED, INTERRUPTED), (SUCCEEDED, None)])
        self.assertIsNone(rows[0]["record_digest"])
        installed = env.binding()["qualification_record_digest"]
        self.assertNotEqual(installed, orphan)
        self.assertNotIn(orphan, [row["record_digest"] for row in rows])

    def test_a_crash_after_the_outcome_commit_is_settled_by_replay_not_by_a_second_attempt(self):
        """The other side of the outcome commit: the outcome was committed and the process died
        before answering, so the delivery's retry finds its resolved row and gets the recorded
        outcome — the dead-attempt path never runs."""
        env = QualificationEnv(FakeQualificationPort())
        committed = self._qualify(env, "delivery-1")
        self.assertEqual(committed["type"], "Allow")
        again = self._qualify(env, "delivery-1")
        self.assertEqual(again["type"], "Allow", again)
        self.assertEqual(env.port.settles, [])
        self.assertEqual((len(env.rows()), env.port.calls), (1, 1))

    def test_a_retried_delivery_of_the_dead_attempt_runs_fresh_and_its_replay_finds_the_new_row(self):
        """A delivery whose own attempt died, retried with the same request identity: the open row
        is closed interrupted, the request runs as a fresh attempt (a later ordinal, so a new
        row), and a further replay of that delivery returns the fresh attempt's recorded outcome."""
        port = FakeQualificationPort(raiser=RuntimeError("the host died mid-attempt"))
        env = QualificationEnv(port)
        self._qualify(env, "delivery-1")
        port.raiser = None
        retried = self._qualify(env, "delivery-1")
        self.assertEqual(retried["type"], "Allow", retried)
        replay = self._qualify(env, "delivery-1")
        self.assertEqual(replay["type"], "Allow", replay)
        self.assertEqual([row["outcome"] for row in env.rows()], [FAILED, SUCCEEDED])
        self.assertEqual(port.calls, 2)

    def test_an_interrupted_final_reservation_closes_and_the_ceiling_refuses_honestly(self):
        """The ceiling's last attempt died: the next qualify closes that row interrupted first,
        then the ceiling refuses it with the exhausted reason — no row stays reserved forever."""
        env = QualificationEnv(FakeQualificationPort())
        self._seed_attempts(env, resolved=environment.MAX_QUALIFICATION_ATTEMPTS - 1, open_last=True)
        result = self._qualify(env)
        self.assertEqual(result["type"], "Block", result)
        self.assertEqual([reason["code"] for reason in result["reasons"]],
                         [environment.QUALIFICATION_ATTEMPTS_EXHAUSTED])
        rows = env.rows()
        self.assertEqual(len(rows), environment.MAX_QUALIFICATION_ATTEMPTS)
        self.assertNotIn(RESERVED, [row["outcome"] for row in rows])
        self.assertEqual((rows[-1]["outcome"], rows[-1]["reason"]), (FAILED, INTERRUPTED))
        self.assertEqual(env.port.calls, 0)

    def test_a_settle_that_cannot_recover_leaves_the_row_open_with_its_closed_reason(self):
        port = FakeQualificationPort(raiser=RuntimeError("the host died mid-attempt"))
        env = QualificationEnv(port)
        self._qualify(env)
        reason = generic_spike.GenericUnavailableReason.DOCKER_UNREACHABLE.value
        port.settle_failure = reason
        result = self._qualify(env)
        self.assertEqual((result["type"], result["code"]), ("Fault", "unavailable"), result)
        self.assertEqual(result["reason"], reason, result)
        self.assertEqual([row["outcome"] for row in env.rows()], [RESERVED])

    def test_a_settle_that_raises_or_never_closes_is_an_unreasoned_failure_with_the_row_open(self):
        for label, options in (("raises", {"settle_raises": RuntimeError("recovery blew up")}),
                               ("never closes", {"settle_skips_close": True})):
            with self.subTest(label):
                port = FakeQualificationPort(raiser=RuntimeError("the host died mid-attempt"))
                env = QualificationEnv(port)
                self._qualify(env)
                for name, value in options.items():
                    setattr(port, name, value)
                result = self._qualify(env)
                self.assertEqual(result, {"type": "Fault", "code": "unavailable",
                                         "fail_direction": "closed"})
                self.assertEqual([row["outcome"] for row in env.rows()], [RESERVED])
                self.assertEqual(port.calls, 1, "no fresh attempt runs on an unsettled row")

    def test_a_closing_that_cannot_commit_leaves_the_row_open(self):
        port = FakeQualificationPort(raiser=RuntimeError("the host died mid-attempt"))
        env = QualificationEnv(port)
        self._qualify(env)
        with patch.object(env.coordinator, "_commit", side_effect=RuntimeError("store down")):
            result = self._qualify(env)
        self.assertEqual(result, {"type": "Fault", "code": "unavailable",
                                 "fail_direction": "closed"})
        self.assertEqual([row["outcome"] for row in env.rows()], [RESERVED])

    def test_a_closing_that_loses_its_compare_and_set_is_reread_not_forced(self):
        """The run moved between the settle's read and its closing (here a newer approved
        revision committed under the lock): the closing's compare-and-set loses, nothing is
        forced, and the request re-reads and settles again on what is now committed."""
        def revise_the_run(request):
            revised = copy.deepcopy(ENVIRONMENT_ACTION)
            revised["payload"]["rationale"] = "a newer revision committed while the row was settled"
            assert env.observe(revised)["type"] == "Allow"

        port = FakeQualificationPort(raiser=RuntimeError("the host died mid-attempt"))
        env = QualificationEnv(port)
        self._qualify(env)
        port.raiser = None
        port.before_close = lambda request: (revise_the_run(request),
                                             setattr(port, "before_close", None))
        result = self._qualify(env)
        self.assertEqual(result["type"], "Allow", result)
        self.assertEqual(len(port.settles), 2, "the lost closing is settled again, never forced")
        self.assertEqual([(row["outcome"], row["reason"]) for row in env.rows()],
                         [(FAILED, INTERRUPTED), (SUCCEEDED, None)])
        self.assertEqual(env.rows()[1]["revision"], 2)

    # --- compare-and-set losers ------------------------------------------------------------

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
        self.assertEqual(result["reason"], CONFLICT, result)
        rows = env.rows()
        self.assertEqual(len(rows), 1, "the superseding revision never reserved: it is approved")
        self.assertEqual(rows[0]["outcome"], SUPERSEDED)
        self.assertIsNone(rows[0]["record_digest"], "the published record is orphaned")
        self.assertEqual(rows[0]["reason"], CONFLICT)
        self.assertEqual(env.binding(), {"kind": "unqualified"},
                         "the orphaned record binds nothing")

    def test_a_stop_during_the_attempt_is_a_cas_loser_that_records_its_charge(self):
        def stop_mid_attempt(request):
            stopped = env.coordinator.handle(
                {"type": "EvaluateRun", "run_id": env.run_id, "intent": "stop"}, "stop")
            assert stopped["result"]["type"] == "Allow", stopped

        env = QualificationEnv(FakeQualificationPort(before_result=stop_mid_attempt))
        result = self._qualify(env)
        self.assertEqual(result["type"], "Fault", result)
        self.assertEqual(result["reason"], CONFLICT, result)
        rows = env.rows()
        self.assertEqual(rows[0]["outcome"], SUPERSEDED)
        self.assertIsNone(rows[0]["record_digest"], "a stopped run never adopts the record")
        self.assertEqual(env.persisted().status, "stopped_residual")
        self.assertEqual(env.binding(), {"kind": "unqualified"},
                         "a stop during the attempt changes no binding")

    # --- admission -------------------------------------------------------------------------

    def test_a_pending_configuration_revision_refuses_before_any_reservation_or_effect(self):
        """The qualification passes the run's current configuration approval first (spec §5): a
        raised configuration waiting for consent refuses with the existing governance reason,
        reserving, charging and attempting nothing, until the configuration is approved again."""
        env = QualificationEnv(FakeQualificationPort())
        revised = copy.deepcopy(CONFIGURE_RUN)
        revised["rationale"] = "a revised task size the run asks consent for after its approval"
        self.assertEqual(env.observe(revised)["type"], "Allow")
        before = env.rows()
        result = self._qualify(env)
        self.assertEqual(result["type"], "Block", result)
        self.assertEqual([reason["code"] for reason in result["reasons"]],
                         ["governance.revision_required"])
        self.assertEqual((env.rows(), env.port.calls), (before, 0))
        approve_current(env.coordinator, env.run_id)
        self.assertEqual(self._qualify(env)["type"], "Allow")

    def test_at_the_attempt_ceiling_the_admission_refuses_before_any_effect(self):
        """Once the run's attempt ceiling is spent the admission refuses with its own closed
        reason (now against live rows, where ``test_environment_governance`` derives it from the
        pure decision): nothing reserved, the port never called."""
        env = QualificationEnv(FakeQualificationPort())
        self._seed_attempts(env, resolved=environment.MAX_QUALIFICATION_ATTEMPTS, open_last=False)
        before = env.rows()
        result = self._qualify(env)
        self.assertEqual(result["type"], "Block", result)
        self.assertEqual([reason["code"] for reason in result["reasons"]],
                         [environment.QUALIFICATION_ATTEMPTS_EXHAUSTED])
        self.assertEqual(env.port.calls, 0)
        self.assertEqual(env.rows(), before)

    def _seed_attempts(self, env: QualificationEnv, *, resolved: int, open_last: bool):
        read = env.runs.data[env.key]
        state = decode_state(read.value)
        revision = state.environment["revision"]
        total = resolved + (1 if open_last else 0)
        rows = tuple(
            {"attempt_id": environment.qualification_attempt_id(index, f"seeded-{index}"),
             "revision": revision, "started_stamp": index,
             "outcome": RESERVED if open_last and index == total else SUCCEEDED,
             "record_digest": None, "reason": None, "probe": None}
            for index in range(1, total + 1))
        # a resolved seeded row stands for an earlier attempt that carried its own record
        rows = tuple(
            {**row, "record_digest": canonical_digest({"seeded": row["attempt_id"]})}
            if row["outcome"] == SUCCEEDED else row for row in rows)
        snapshot = env.coordinator._assemble(
            env.key, state, {"type": "GetRun", "run_id": env.run_id}, require_graph=False)
        env.coordinator._commit(env.key, read.revision, snapshot,
                                replace(state, qualification_attempts=rows), ())


if __name__ == "__main__":
    unittest.main()
