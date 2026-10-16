#!/usr/bin/env python3
"""Strict run/3 backend binding and major-version run/2 rejection regressions."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from adapters.sandbox import record, selection
from adapters.state.repository import FilesystemRunRepository
from application import protocol, run_state, v2
from application.location import encode_handle, storage_id
from application.ports import QualifiedSpikeBackend, SpikesUnavailable
from application.transaction import Coordinator
from core.records import Conflict, Present, Revision, RunKey

from governance_setup import TEST_INVOCATION
from test_d7_transactions import Artifacts, Harness, Runs, Workspace, chain, initial

PROFILE = protocol.host_profile_id("pi")
ROOT = Path(__file__).resolve().parents[3]
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def start_command(project="p", session="s"):
    return {
        "type": "StartRun",
        "control_mode": "deliberative",
        "invocation": dict(TEST_INVOCATION),
        "selector": {"project": project, "session": session},
        "goal": "g",
    }


class CountingSelector:
    def __init__(self, value=None):
        self.value = value or SpikesUnavailable()
        self.calls = 0

    def __bool__(self):
        return False

    def select(self):
        self.calls += 1
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


class Run3SchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = protocol.state_schema()
        self.fixture = json.loads((
            ROOT / "contracts/empirica/v2/state-fixtures/valid-active.json"
        ).read_text())

    def test_exact_two_closed_binding_branches(self):
        unavailable = copy.deepcopy(self.fixture)
        unavailable["spike_backend"] = {"kind": "spikes_unavailable"}
        protocol.schema_validator("state").validate(unavailable)
        qualified = copy.deepcopy(self.fixture)
        qualified["spike_backend"] = {
            "kind": "qualified", "backend": "docker", "profile_id": "docker-rust-v1",
            "qualification_record_digest": DIGEST_A,
            "execution_binding_digest": DIGEST_B,
        }
        protocol.schema_validator("state").validate(qualified)
        bad = []
        for binding in (
            {},
            {"kind": "spikes_unavailable", "reason": "absent"},
            {"kind": "qualified", "backend": "host", "profile_id": "docker-rust-v1",
             "qualification_record_digest": DIGEST_A, "execution_binding_digest": DIGEST_B},
            {"kind": "qualified", "backend": "docker", "profile_id": "other",
             "qualification_record_digest": DIGEST_A, "execution_binding_digest": DIGEST_B},
            {"kind": "qualified", "backend": "docker", "profile_id": "docker-rust-v1",
             "qualification_record_digest": "bad", "execution_binding_digest": DIGEST_B},
        ):
            candidate = copy.deepcopy(self.fixture)
            candidate["spike_backend"] = binding
            bad.append(candidate)
        missing = copy.deepcopy(self.fixture)
        missing.pop("spike_backend")
        bad.append(missing)
        for candidate in bad:
            with self.subTest(binding=candidate.get("spike_backend")):
                self.assertFalse(protocol.schema_validator("state").is_valid(candidate))
                self.assertEqual(run_state.classify_and_decode(candidate).kind, "current_corrupt")

    def test_roundtrip_is_deeply_immutable_and_encoder_is_run3_only(self):
        classified = run_state.classify_and_decode(self.fixture)
        self.assertEqual(classified.kind, "valid")
        with self.assertRaises(TypeError):
            classified.state.spike_backend["kind"] = "changed"
        self.assertEqual(run_state.encode_state(classified.state), self.fixture)
        run2 = copy.deepcopy(self.fixture)
        run2["state_schema"] = "empirica.run/2"
        run2.pop("spike_backend")
        self.assertEqual(run_state.classify_and_decode(run2).kind, "current_corrupt")
        self.assertIsNone(run_state.classify_and_decode(run2).state)
        with self.assertRaises(ValueError):
            run_state.encode_state(replace(classified.state, state_schema="empirica.run/2"))
        run2["spike_backend"] = {"kind": "spikes_unavailable"}
        self.assertEqual(run_state.classify_and_decode(run2).kind, "current_corrupt")

        class StringImpostor:
            def __str__(self):
                return DIGEST_A

        for key in ("qualification_record_digest", "execution_binding_digest"):
            for impostor in (1, True, StringImpostor()):
                with self.subTest(key=key, impostor=type(impostor).__name__):
                    binding = QualifiedSpikeBackend(DIGEST_A, DIGEST_B).persisted()
                    binding[key] = impostor
                    with self.assertRaises(ValueError):
                        replace(classified.state, spike_backend=binding)


class StartSelectionTests(unittest.TestCase):
    def coordinator(self, selector, *, workspace=None, harness=None, runs=None, artifacts=None):
        values = (workspace or Workspace(), harness or Harness(), runs or Runs(),
                  artifacts or Artifacts())
        return Coordinator(*values, PROFILE, {}, selector), values

    def test_selects_once_persists_exact_binding_and_active_resume_does_no_work(self):
        selector = CountingSelector(QualifiedSpikeBackend(DIGEST_A, DIGEST_B))
        coordinator, (workspace, _harness, runs, artifacts) = self.coordinator(selector)
        first = coordinator.handle(start_command(), "one")
        self.assertEqual(first["result"]["type"], "Allow")
        key = next(iter(runs.data))
        self.assertEqual(runs.data[key].value["spike_backend"], {
            "kind": "qualified", "backend": "docker", "profile_id": "docker-rust-v1",
            "qualification_record_digest": DIGEST_A,
            "execution_binding_digest": DIGEST_B,
        })
        self.assertEqual(selector.calls, 1)
        before = (len(workspace.calls), artifacts.append_calls, runs.seq)
        selector.value = QualifiedSpikeBackend(DIGEST_B, DIGEST_A)
        second = coordinator.handle(start_command(), "two")
        self.assertEqual(second["result"]["run"]["id"], first["result"]["run"]["id"])
        self.assertEqual(selector.calls, 1)
        self.assertEqual((len(workspace.calls), artifacts.append_calls, runs.seq), before)
        self.assertEqual(runs.data[key].value["spike_backend"]["qualification_record_digest"],
                         DIGEST_A)

    def test_selector_exception_and_unknown_result_cannot_block_reasoning(self):
        for value in (RuntimeError("broken"), object(), SpikesUnavailable("absent")):
            with self.subTest(value=type(value).__name__):
                selector = CountingSelector(value)
                coordinator, (_workspace, _harness, runs, _artifacts) = self.coordinator(selector)
                result = coordinator.handle(start_command(), "start")
                self.assertEqual(result["result"]["type"], "Allow")
                self.assertEqual(next(iter(runs.data.values())).value["spike_backend"],
                                 {"kind": "spikes_unavailable"})
                routed = coordinator.handle({
                    "type": "ObserveAction", "run_id": result["result"]["run"]["id"],
                    "action": {"kind": "route", "reason": "ordinary reasoning"},
                }, "route")
                self.assertEqual(routed["result"]["type"], "Allow")

    def test_create_race_returns_winners_binding_without_rewrite(self):
        winner_selector = CountingSelector(QualifiedSpikeBackend(DIGEST_B, DIGEST_A))
        winner, (_workspace, _harness, winner_runs, winner_artifacts) = self.coordinator(
            winner_selector)
        winner.handle(start_command(), "winner")
        key = next(iter(winner_runs.data))
        winner_read = winner_runs.data[key]

        class RaceRuns(Runs):
            def create(self, create_key, value):
                self.data[create_key] = Present(winner_read.value, Revision("winner"))
                raise Conflict(create_key, None, "lost create race")

        race_runs = RaceRuns()
        race_artifacts = Artifacts()
        race_artifacts.values[key] = dict(winner_artifacts.values[key])
        loser_selector = CountingSelector(QualifiedSpikeBackend(DIGEST_A, DIGEST_B))
        loser, _ = self.coordinator(
            loser_selector, runs=race_runs, artifacts=race_artifacts)
        response = loser.handle(start_command(), "loser")
        self.assertEqual(response["result"]["type"], "Allow")
        self.assertEqual(race_runs.data[key].value["spike_backend"],
                         winner_read.value["spike_backend"])
        self.assertEqual(loser_selector.calls, 1)

    def test_falsey_selector_identity_survives_service_composition(self):
        selector = CountingSelector(QualifiedSpikeBackend(DIGEST_A, DIGEST_B))
        workspace, harness, runs, artifacts = Workspace(), Harness(), Runs(), Artifacts()
        service = v2.compose(
            workspace, harness, runs, artifacts, None, PROFILE, {}, None,
            spike_authority_selector=selector)
        result = service._dispatch_validated({"request_id": "start", "command": start_command()})
        self.assertEqual(result["result"]["type"], "Allow")
        self.assertIs(service._coordinator.spike_authority_selector, selector)
        self.assertEqual(selector.calls, 1)
        self.assertEqual(next(iter(runs.data.values())).value["spike_backend"]["kind"],
                         "qualified")

    def test_base_exception_from_injected_selector_propagates(self):
        coordinator, _ = self.coordinator(CountingSelector(KeyboardInterrupt()))
        with self.assertRaises(KeyboardInterrupt):
            coordinator.handle(start_command(), "start")

    def test_explicit_start_after_latest_run2_creates_fresh_run3_without_mutating_old(self):
        selector = CountingSelector()
        coordinator, (_workspace, _harness, runs, artifacts) = self.coordinator(selector)
        historical_state, historical_artifacts = chain(initial())
        old_key = RunKey(storage_id("p"), storage_id("s"), 1)
        old_document = legacy_document(historical_state)
        runs.create(old_key, old_document)
        for item in historical_artifacts:
            artifacts.append(old_key, item)
        before_read = copy.deepcopy(runs.data[old_key])
        before_artifacts = copy.deepcopy(artifacts.values[old_key])
        result = coordinator.handle(start_command(), "start")
        new_key = RunKey(old_key.project_id, old_key.run_id, 2)
        self.assertEqual(result["result"]["type"], "Allow")
        self.assertIn(new_key, runs.data)
        self.assertEqual(selector.calls, 1)
        self.assertEqual(runs.data[old_key], before_read)
        self.assertEqual(artifacts.values[old_key], before_artifacts)
        self.assertEqual(runs.data[new_key].value["state_schema"], "empirica.run/3")

    def test_unavailable_run3_spike_is_guarded_before_workspace_artifact_and_harness(self):
        coordinator, (workspace, harness, runs, artifacts) = self.coordinator(
            CountingSelector(SpikesUnavailable()))
        started = coordinator.handle(start_command(), "start")
        before = (len(workspace.calls), artifacts.append_calls, runs.seq, harness.calls)
        result = coordinator.handle({
            "type": "ObserveAction", "run_id": started["result"]["run"]["id"],
            "action": {"kind": "spike_request", "claim_id": "C0",
                       "command": "true", "dependent_files": ["x"]},
        }, "spike")
        self.assertEqual(result["result"], {
            "type": "Fault", "code": "unavailable", "fail_direction": "closed"})
        self.assertEqual((len(workspace.calls), artifacts.append_calls, runs.seq, harness.calls),
                         before)
    def test_terminal_run3_spike_is_inert_before_every_side_effect(self):
        bindings = (
            {"kind": "spikes_unavailable"},
            QualifiedSpikeBackend(DIGEST_A, DIGEST_B).persisted(),
        )
        for binding in bindings:
            with self.subTest(kind=binding["kind"]):
                workspace, harness, runs, artifacts = Workspace(), Harness(), Runs(), Artifacts()
                state = replace(initial(), status="stopped_residual", spike_backend=binding)
                state, values = chain(state)
                key = RunKey(storage_id("p"), storage_id("s"), 1)
                runs.create(key, run_state.encode_state(state))
                for item in values:
                    artifacts.append(key, item)
                coordinator = Coordinator(
                    workspace, harness, runs, artifacts, PROFILE, {}, CountingSelector())
                before = (copy.deepcopy(runs.data[key]), copy.deepcopy(artifacts.values[key]),
                          len(workspace.calls), harness.calls, artifacts.append_calls)
                result = coordinator.handle({
                    "type": "ObserveAction", "run_id": encode_handle(key),
                    "action": {"kind": "spike_request", "claim_id": "C0",
                               "command": "true", "dependent_files": ["x"]},
                }, "terminal-spike")
                self.assertEqual(result["result"]["type"], "Inert")
                self.assertEqual(
                    (runs.data[key], artifacts.values[key], len(workspace.calls), harness.calls,
                     artifacts.append_calls), before)


class Store:
    def __init__(self, outcome):
        self.outcome = outcome
        self.kwargs = None

    def __bool__(self):
        return False

    def load_current(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class PassiveSelectorTests(unittest.TestCase):
    def select(self, outcome):
        store = Store(outcome)
        selector = selection.QualificationAuthoritySelector(
            store,
            environment_inspector=lambda: object(),
            runtime_inspector=lambda _environment: {},
        )
        with (patch.object(selection.environment, "validate_environment_identity", return_value={}),
              patch.object(selection.docker, "validate_runtime_identity", return_value={})):
            return selector.select(), store

    def test_absent_unsafe_drift_and_unexpected_errors_all_close_without_persisted_reason(self):
        for failure in (FileNotFoundError("absent"), PermissionError("unsafe"),
                        ValueError("drift"), RuntimeError("unexpected")):
            with self.subTest(failure=type(failure).__name__):
                selected, _store = self.select(failure)
                self.assertIsInstance(selected, SpikesUnavailable)
                self.assertEqual(selected.persisted(), {"kind": "spikes_unavailable"})
                self.assertIn(type(failure).__name__, selected.diagnostic)

    def test_qualified_authority_and_base_exception_behavior(self):
        selected, store = self.select({
            "qualification_record_digest": DIGEST_A,
            "execution_binding_digest": DIGEST_B,
        })
        self.assertEqual(selected, QualifiedSpikeBackend(DIGEST_A, DIGEST_B))
        self.assertEqual(set(store.kwargs), {"expected_environment", "expected_runtime"})
        with self.assertRaises(KeyboardInterrupt):
            self.select(KeyboardInterrupt())

    def test_falsey_store_and_environment_inspector_are_preserved_by_identity(self):
        authority = {"qualification_record_digest": DIGEST_A,
                     "execution_binding_digest": DIGEST_B}
        store = Store(authority)

        class Inspector:
            def __bool__(self):
                return False

            def __call__(self):
                return object()

        inspector = Inspector()
        selector = selection.QualificationAuthoritySelector(
            store, environment_inspector=inspector, runtime_inspector=lambda _value: {})
        self.assertIs(selector._store, store)
        with (patch.object(selection.environment, "validate_environment_identity", return_value={}),
              patch.object(selection.docker, "validate_runtime_identity", return_value={})):
            self.assertIsInstance(selector.select(), QualifiedSpikeBackend)
        with tempfile.TemporaryDirectory() as temporary:
            qualification_store = record.QualificationStore(
                Path(temporary).resolve(), environment_inspector=inspector)
            self.assertIs(qualification_store.environment_inspector, inspector)


def legacy_document(state):
    current = replace(state, state_schema="empirica.run/3",
                      spike_backend={"kind": "spikes_unavailable"})
    document = run_state.encode_state(current)
    document["state_schema"] = "empirica.run/2"
    document.pop("spike_backend")
    return document


class Run2RejectionTests(unittest.TestCase):
    def test_old_handle_is_corrupt_and_cannot_mutate_state_or_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            runs = FilesystemRunRepository(Path(temporary))
            artifacts = Artifacts()
            state, values = chain(initial())
            key = RunKey(storage_id("p"), storage_id("s"), 1)
            runs.create(key, legacy_document(state))
            for item in values:
                artifacts.append(key, item)
            workspace, harness = Workspace(), Harness()
            service = v2.compose(workspace, harness, runs, artifacts, None, PROFILE, {}, None)
            run_id = encode_handle(key)
            path = runs._doc_path(key)
            before_bytes = path.read_bytes()
            before_read = runs.read(key)
            before_artifacts = copy.deepcopy(artifacts.values[key])
            for command in (
                {"type": "ResolveRun", "selector": {"project": "p", "session": "s"}},
                {"type": "GetRun", "run_id": run_id},
                {"type": "RestoreRun", "run_id": run_id},
                {"type": "GetArgument", "run_id": run_id},
                {"type": "EvaluateRun", "run_id": run_id, "intent": "continue"},
                {"type": "ObserveAction", "run_id": run_id,
                 "action": {"kind": "route", "reason": "must not write"}},
                {"type": "ObserveAction", "run_id": run_id,
                 "action": {"kind": "spike_request", "claim_id": "C0",
                            "command": "true", "dependent_files": ["x"]}},
            ):
                with self.subTest(command=command["type"]):
                    result = service._dispatch_validated(
                        {"request_id": command["type"], "command": command})
                    self.assertEqual(result["result"]["type"], "Block")
                    self.assertEqual(result["result"]["reasons"][0]["code"], "run.corrupt")
            self.assertIsNone(service.trusted_host_runtime(run_id=run_id))
            trusted = service.trusted_evidence_leaf(run_id=run_id, payload={})
            self.assertEqual(trusted["result"]["type"], "Inert")
            after_read = runs.read(key)
            self.assertEqual(path.read_bytes(), before_bytes)
            self.assertEqual(after_read.revision, before_read.revision)
            self.assertEqual(artifacts.values[key], before_artifacts)
            self.assertEqual(workspace.calls, [])
            self.assertEqual(harness.calls, 0)


if __name__ == "__main__":
    unittest.main()
