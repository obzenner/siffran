#!/usr/bin/env python3
"""Fake-Docker tests for the mechanically unreachable 3C-b exact-record harness."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from adapters.sandbox import docker, execution, record, selection
from application.observation import HarnessContractError, execute_run_bound_spike
from application.ports import (
    ExecutionSnapshot, HarnessResult, QualifiedSpikeBackend, SpikeExecutionUnavailable,
)
from core.freshness import FileBinding, canonical_digest

import test_sandbox_adapter as support


def operation_id(label: str) -> str:
    return "o-" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:32]


class HarnessRunner(support.LifecycleRunner):
    """One fake runner for both exact five-read discovery and lifecycle effects."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.discovery = support.DiscoveryRunner(
            server_arch=platform.machine(), image_arch=platform.machine())
        self.trace = []

    def run(self, argv, *, timeout_seconds, output_limit_bytes, env=None):
        self.trace.append((tuple(argv), timeout_seconds, output_limit_bytes, dict(env or {})))
        if tuple(argv[1:3]) == ("container", "create") and self.removed:
            self.removed = False
            self.started = False
            self.created = False
            self.create_doc = None
        head = tuple(argv[1:2])
        if head in {("context",), ("version",), ("info",), ("image",)}:
            return self.discovery.run(
                argv, timeout_seconds=timeout_seconds,
                output_limit_bytes=output_limit_bytes, env=env)
        return super().run(
            argv, timeout_seconds=timeout_seconds,
            output_limit_bytes=output_limit_bytes, env=env)


class MutableClock:
    def __init__(self):
        self.now = time.monotonic()

    def __call__(self):
        return self.now


class DockerHarnessCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve(strict=True)
        self.current_environment = support.healthy_environment(self.root)
        result = support.RecordTests().result(
            self.root, environment_identity=self.current_environment)
        self.authority = record.build_record(result)
        self.store = record.QualificationStore(
            self.root / "sandbox", environment_inspector=lambda: self.current_environment)
        self.store.publish(self.authority)
        self.binding = QualifiedSpikeBackend(
            self.authority["qualification_record_digest"],
            self.authority["execution_binding_digest"])
        self.runner = HarnessRunner()
        self.operation = operation_id("production")
        self.harness = self.make_harness()
        content = b"fn main() {}\n"
        binding = FileBinding(
            "src/main.rs", "sha256:" + hashlib.sha256(content).hexdigest())
        self.snapshot = ExecutionSnapshot(
            (binding,), (content,), canonical_digest([(binding.path, binding.sha256)]))

    def make_harness(self, **kwargs):
        return execution.DockerSpikeHarness(
            self.store, runner=kwargs.pop("runner", self.runner),
            operation_id_source=kwargs.pop("operation_id_source", lambda: self.operation),
            **kwargs)

    def run_harness(self, *, command="true", binding=None):
        return self.harness.run(
            run_id="er2.opaque-run-handle/never-a-label",
            binding=self.binding if binding is None else binding,
            command=command, snapshot=self.snapshot)

    def assert_clean(self):
        self.assertEqual(self.store.read_journals(), [])
        self.assertEqual(list(self.store.staging.iterdir()), [])


class PortAndPlanTests(DockerHarnessCase):
    def test_closed_exception_and_pure_plan(self):
        for reason in sorted(SpikeExecutionUnavailable.REASONS):
            failure = SpikeExecutionUnavailable(reason, "diagnostic\nonly")
            self.assertEqual((failure.reason, failure.detail), (reason, "diagnostic only"))
        with self.assertRaises(ValueError):
            SpikeExecutionUnavailable("invented", "detail")
        plan = execution._execution_plan(
            run_id="raw/run:handle", binding=self.binding, command="true",
            snapshot=self.snapshot, operation_id=self.operation,
            authority=self.authority,
            policy=docker.validate_policy({
                **self.authority["profile"]["policy"],
                "runtime_identity": self.authority["runtime"]}))
        self.assertNotIn("raw/run:handle", plan.labels.values())
        self.assertRegex(plan.labels[docker.RUN_LABEL], r"^r-[0-9a-f]{40}$")
        self.assertEqual(plan.intent["operation_id"], self.operation)
        self.assertEqual(plan.intent["qualification_record_digest"],
                         self.binding.qualification_record_digest)

    def test_mechanically_unreachable_run_bound_observation_helper(self):
        class Workspace:
            def observe(inner, paths):
                from application.ports import CapturedFile, WorkspaceCapture
                from core.freshness import FileObservation, ObservationState
                content = self.snapshot.captured_bytes[0]
                observation = FileObservation(
                    paths[0], ObservationState.PRESENT,
                    "sha256:" + hashlib.sha256(content).hexdigest())
                return WorkspaceCapture("basis", (CapturedFile(observation, content),))
        with patch.object(self.harness, "run", wraps=self.harness.run) as called:
            facts, snapshot, _ = execute_run_bound_spike(
                run_id="opaque", binding=self.binding, command="true",
                dependent_paths=("src/main.rs",), workspace=Workspace(),
                harness=self.harness)
        called.assert_called_once()
        self.assertEqual(called.call_args.kwargs["binding"], self.binding)
        self.assertRegex(facts.result_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(snapshot.snapshot_digest, called.call_args.kwargs["snapshot"].snapshot_digest)

    def test_run_bound_helper_rejects_non_result_and_snapshot_mismatch(self):
        class Workspace:
            def observe(inner, paths):
                from application.ports import CapturedFile, WorkspaceCapture
                from core.freshness import FileObservation, ObservationState
                content = self.snapshot.captured_bytes[0]
                observation = FileObservation(
                    paths[0], ObservationState.PRESENT,
                    "sha256:" + hashlib.sha256(content).hexdigest())
                return WorkspaceCapture("basis", (CapturedFile(observation, content),))

        class BadHarness:
            def __init__(inner, mode):
                inner.mode = mode
            def run(inner, **kwargs):
                if inner.mode == "type":
                    return object()
                return HarnessResult(
                    0, "sha256:" + "1" * 64, "sha256:" + "2" * 64)

        for mode in ("type", "echo"):
            with self.subTest(mode=mode), self.assertRaises(HarnessContractError):
                execute_run_bound_spike(
                    run_id="opaque", binding=self.binding, command="true",
                    dependent_paths=("src/main.rs",), workspace=Workspace(),
                    harness=BadHarness(mode))


class SuccessAndAuthorityTests(DockerHarnessCase):
    def test_normal_exit_codes_and_canonical_output_binding(self):
        for code in (0, 1, 124, 125):
            with self.subTest(code=code):
                if code != 0:
                    self.setUp()
                self.runner.workload_exit = code
                self.runner.workload_stdout = b"stdout-bytes"
                self.runner.workload_stderr = b"stderr-bytes"
                result = self.run_harness(command="exit requested")
                self.assertEqual((result.exit_code, result.snapshot_digest),
                                 (code, self.snapshot.snapshot_digest))
                expected = canonical_digest({
                    "command_digest": canonical_digest("exit requested"),
                    "exit_code": code,
                    "stdout_sha256": "sha256:" + hashlib.sha256(b"stdout-bytes").hexdigest(),
                    "stdout_bytes": len(b"stdout-bytes"),
                    "stderr_sha256": "sha256:" + hashlib.sha256(b"stderr-bytes").hexdigest(),
                    "stderr_bytes": len(b"stderr-bytes"),
                    "output_limit_bytes": docker.WORKLOAD_OUTPUT_LIMIT_BYTES,
                    "snapshot_digest": self.snapshot.snapshot_digest,
                    "qualification_record_digest": self.binding.qualification_record_digest,
                    "execution_binding_digest": self.binding.execution_binding_digest,
                    "policy_digest": docker.policy_digest(docker.validate_policy({
                        **self.authority["profile"]["policy"],
                        "runtime_identity": self.authority["runtime"]})),
                })
                self.assertEqual(result.result_digest, expected)
                self.assert_clean()

    def test_never_reads_current_and_pointer_movement_is_irrelevant(self):
        self.store.current.write_text("{}", encoding="utf-8")
        self.store.current.chmod(0o600)
        with patch.object(self.store, "load_current",
                          side_effect=AssertionError("current pointer was read")):
            result = self.run_harness()
        self.assertEqual(result.exit_code, 0)
        self.assert_clean()
        self.assertEqual(len(self.runner.discovery.calls), 10)

    def test_exact_argv_environment_deadlines_and_no_prepare_or_fallback(self):
        ambient = {"DOCKER_CONTEXT": "desktop-linux", "DOCKER_HOST": "tcp://ignored:2375"}
        with patch.dict(os.environ, ambient, clear=False), patch.object(
                self.store, "prepare",
                side_effect=AssertionError("runtime repaired authority")):
            self.run_harness(command="printf exact")
        closed = {"LANG": "C", "LC_ALL": "C",
                  "DOCKER_HOST": "unix:///tmp/docker.sock"}
        allowed = {
            ("context", "show"), ("context", "inspect"), ("version", "--format"),
            ("info", "--format"), ("image", "inspect"), ("container", "create"),
            ("container", "inspect"), ("container", "start"), ("container", "ls"),
            ("container", "rm"),
        }
        for argv, timeout, _, env in self.runner.trace:
            self.assertEqual(argv[0], self.authority["runtime"]["docker_cli"])
            self.assertIn(tuple(argv[1:3]), allowed)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, docker.OPERATION_TIMEOUT_SECONDS)
            if argv[1] == "container":
                self.assertEqual(env, closed)
            else:
                self.assertEqual(env.get("DOCKER_CONTEXT"), "desktop-linux")
                self.assertEqual(env.get("DOCKER_HOST"), "tcp://ignored:2375")
        create = next(row for row in self.runner.trace if row[0][1:3] == ("container", "create"))
        argv, timeout, _, env = create
        self.assertIn("--pull=never", argv)
        self.assertIn("--network=none", argv)
        self.assertIn("--read-only", argv)
        self.assertLessEqual(timeout, docker.CREATE_INSPECT_TIMEOUT_SECONDS)
        self.assertEqual(env, closed)
        self.assertFalse(any(row[0][0] in {"sh", "/bin/sh", "bash", "/bin/bash"}
                             for row in self.runner.trace))

    def test_qualification_selection_and_harness_share_ambient_discovery_precedence(self):
        ambient = {"DOCKER_CONTEXT": "desktop-linux", "DOCKER_HOST": "tcp://ignored:2375"}
        qualified_runner = support.DiscoveryRunner(
            server_arch=platform.machine(), image_arch=platform.machine())
        selected_runner = support.DiscoveryRunner(
            server_arch=platform.machine(), image_arch=platform.machine())
        with patch.dict(os.environ, ambient, clear=False), patch.object(
                selection, "inspect_runtime", wraps=selection.inspect_runtime) as shared:
            qualified = docker.qualify_docker(
                "preflight", runner=qualified_runner,
                cli_path=self.authority["runtime"]["docker_cli"],
                environment_inspector=lambda: self.current_environment)
            selected = shared(
                self.current_environment, runner=selected_runner,
                cli_path=self.authority["runtime"]["docker_cli"])
            result = self.run_harness()
        self.assertEqual(shared.call_count, 3)
        self.assertEqual(qualified["preflight"], asdict(selected))
        self.assertEqual(result.exit_code, 0)
        for runner in (qualified_runner, selected_runner):
            self.assertEqual(len(runner.calls), 5)
            self.assertTrue(all(call[3].get("DOCKER_CONTEXT") == "desktop-linux"
                                and call[3].get("DOCKER_HOST") == "tcp://ignored:2375"
                                for call in runner.calls))
        lifecycle = [row for row in self.runner.trace if row[0][1] == "container"]
        self.assertTrue(all(row[3] == {"LANG": "C", "LC_ALL": "C",
                                      "DOCKER_HOST": "unix:///tmp/docker.sock"}
                            for row in lifecycle))

    def test_supplied_store_is_single_environment_owner(self):
        harness = execution.DockerSpikeHarness(
            self.store,
            environment_inspector=lambda: (_ for _ in ()).throw(
                AssertionError("parallel environment inspector used")),
            runner=self.runner, operation_id_source=lambda: self.operation)
        result = harness.run(
            run_id="opaque", binding=self.binding,
            command="true", snapshot=self.snapshot)
        self.assertEqual(result.exit_code, 0)
        self.assert_clean()

    def test_restart_recovers_exact_created_journal_before_new_execution(self):
        policy = docker.validate_policy({
            **self.authority["profile"]["policy"],
            "runtime_identity": self.authority["runtime"]})
        old_id = operation_id("old-created")
        old_plan = execution._execution_plan(
            run_id="old/raw-handle", binding=self.binding, command="old command",
            snapshot=self.snapshot, operation_id=old_id,
            authority=self.authority, policy=policy)
        self.store.begin_operation(old_plan.intent)
        staging = execution.materialize_snapshot(
            self.store, old_id, self.snapshot, policy)
        identity = docker.DockerRuntimeIdentity(
            schema_version=docker.SCHEMA_VERSION, backend=docker.BACKEND,
            available=True, reasons=(), **self.authority["runtime"])
        client = docker.DockerClient(identity, policy, self.runner)
        container_id, _ = client.create_container_with_identity(
            name=old_plan.name, labels=old_plan.labels, input_dir=staging,
            command=old_plan.command, network=policy["isolation"]["network"])
        self.store.write_cid(old_id, container_id)
        self.store.mark_created(old_id, container_id)

        result = self.run_harness()

        self.assertEqual(result.exit_code, 0)
        remove_calls = [row for row in self.runner.trace
                        if row[0][1:4] == ("container", "rm", "--force")]
        self.assertEqual(len(remove_calls), 2)
        self.assert_clean()

    def test_runtime_and_implementation_postflight_drift_never_admit(self):
        class RuntimeDriftRunner(HarnessRunner):
            def run(inner, argv, *, timeout_seconds, output_limit_bytes, env=None):
                contexts = sum(
                    row[0][1:3] == ("context", "show") for row in inner.trace)
                if tuple(argv[1:3]) == ("version", "--format") and contexts >= 2:
                    inner.trace.append((
                        tuple(argv), timeout_seconds, output_limit_bytes, dict(env or {})))
                    return docker.CommandResult.fake(stdout=json.dumps({
                        "Client": {"Version": "30.0.0"},
                        "Server": {"Version": "30.0.0", "Os": "linux",
                                   "Arch": platform.machine()},
                    }))
                return super().run(
                    argv, timeout_seconds=timeout_seconds,
                    output_limit_bytes=output_limit_bytes, env=env)

        for kind in ("runtime", "implementation"):
            with self.subTest(kind=kind):
                self.setUp()
                if kind == "runtime":
                    self.runner = RuntimeDriftRunner()
                    self.harness = self.make_harness(runner=self.runner)
                    context = patch.object(
                        record, "implementation_manifest", wraps=record.implementation_manifest)
                else:
                    postflight = False
                    def manifest():
                        if not postflight:
                            return self.authority["implementation"]
                        return {"implementation_digest": "sha256:" + "0" * 64}
                    real_finish = self.store.finish_operation
                    def finish(*args, **kwargs):
                        nonlocal postflight
                        result = real_finish(*args, **kwargs)
                        postflight = True
                        return result
                    self.harness = self.make_harness()
                    context = patch.object(record, "implementation_manifest", side_effect=manifest)
                    finish_context = patch.object(self.store, "finish_operation", side_effect=finish)
                if kind == "runtime":
                    finish_context = patch.object(
                        self.store, "finish_operation", wraps=self.store.finish_operation)
                with context, finish_context, patch.object(
                        execution, "_admit_result", wraps=execution._admit_result) as admit:
                    with self.assertRaises(SpikeExecutionUnavailable) as caught:
                        self.run_harness()
                self.assertEqual(caught.exception.reason, "postflight_changed")
                admit.assert_not_called()
                self.assert_clean()


class FailureTests(DockerHarnessCase):
    def assert_reason(self, expected, action=None):
        with self.assertRaises(SpikeExecutionUnavailable) as caught:
            (action or self.run_harness)()
        self.assertEqual(caught.exception.reason, expected, caught.exception.detail)
        return caught.exception

    def test_authority_scaffolding_recovery_and_staging_reasons(self):
        bad = QualifiedSpikeBackend("sha256:" + "0" * 64, "sha256:" + "1" * 64)
        self.assert_reason("authority_invalid", lambda: self.run_harness(binding=bad))

        self.store.runtime_lock.unlink()
        self.assert_reason("scaffolding_unsafe")

        self.setUp()
        bad_root = self.store.runtime / operation_id("bad-journal")
        bad_root.mkdir(mode=0o700)
        extra = bad_root / "unknown"
        extra.write_text("x", encoding="utf-8")
        extra.chmod(0o600)
        self.assert_reason("recovery_failed")

        self.setUp()
        (self.store.staging / self.operation).mkdir(mode=0o700)
        self.assert_reason("staging_failed")
        self.assert_clean()

    def test_workload_and_lifecycle_failure_table(self):
        cases = (
            ("lifecycle_failed", {"create_exit": 125}),
            ("workload_timeout", {"timed_out": True}),
            ("workload_output_limit", {"output_limited": True}),
            ("cleanup_failed", {"remove_exit": 1}),
        )
        for expected, options in cases:
            with self.subTest(expected=expected):
                self.setUp()
                self.runner = HarnessRunner(**options)
                self.harness = self.make_harness(runner=self.runner)
                self.assert_reason(expected)
                if expected == "cleanup_failed":
                    journals = self.store.read_journals()
                    self.assertEqual(len(journals), 1)
                    self.assertEqual(journals[0]["lifecycle_state"], "removing")
                    self.assertTrue((self.store.staging / self.operation).is_dir())
                else:
                    if options.get("timed_out") or options.get("output_limited"):
                        self.assertTrue(self.runner.removed)
                    self.assert_clean()

    def test_attached_and_inspected_exit_codes_must_agree(self):
        class AttachRunner(HarnessRunner):
            def __init__(inner, *, attached, inspected):
                super().__init__(workload_exit=inspected)
                inner.attached = attached
            def run(inner, argv, *, timeout_seconds, output_limit_bytes, env=None):
                result = super().run(
                    argv, timeout_seconds=timeout_seconds,
                    output_limit_bytes=output_limit_bytes, env=env)
                if tuple(argv[1:4]) == ("container", "start", "--attach"):
                    return docker.CommandResult.fake(
                        inner.attached, stderr="daemon attach failed")
                return result

        for attached, inspected in ((125, 0), (0, 125)):
            with self.subTest(attached=attached, inspected=inspected):
                self.setUp()
                self.runner = AttachRunner(attached=attached, inspected=inspected)
                self.harness = self.make_harness(runner=self.runner)
                with patch.object(
                        execution, "_admit_result", wraps=execution._admit_result) as admit:
                    self.assert_reason("workload_state_invalid")
                admit.assert_not_called()
                self.assertTrue(self.runner.removed)
                self.assert_clean()

    def test_preflight_runtime_drift_nonobject_inspect_and_state_error(self):
        class MalformedRunner(HarnessRunner):
            def __init__(inner, mode):
                super().__init__()
                inner.mode = mode
                inner.mutated = False
            def run(inner, argv, *, timeout_seconds, output_limit_bytes, env=None):
                result = super().run(
                    argv, timeout_seconds=timeout_seconds,
                    output_limit_bytes=output_limit_bytes, env=env)
                args = tuple(argv[1:3])
                if inner.mode == "runtime" and args == ("version", "--format"):
                    return docker.CommandResult.fake(stdout=json.dumps({
                        "Client": {"Version": "30.0.0"},
                        "Server": {"Version": "30.0.0", "Os": "linux",
                                   "Arch": platform.machine()},
                    }))
                if (args == ("container", "inspect") and inner.started
                        and not inner.mutated):
                    inner.mutated = True
                    if inner.mode == "nonobject":
                        return docker.CommandResult.fake(stdout="[]")
                    if inner.mode == "error":
                        doc = json.loads(result.stdout.data)
                        doc["State"]["Error"] = "daemon lost attach"
                        return docker.CommandResult.fake(stdout=json.dumps(doc))
                return result

        for mode, expected in (("runtime", "authority_invalid"),
                               ("nonobject", "workload_state_invalid"),
                               ("error", "workload_state_invalid")):
            with self.subTest(mode=mode):
                self.setUp()
                self.runner = MalformedRunner(mode)
                self.harness = self.make_harness(runner=self.runner)
                self.assert_reason(expected)
                if mode == "runtime":
                    self.assertFalse(self.runner.created)
                else:
                    self.assertTrue(self.runner.removed)
                self.assert_clean()

    def test_request_contract_errors_propagate_before_authority_or_effects(self):
        for values in (("", "true", self.snapshot),
                       ("run", "", self.snapshot),
                       ("run", "true", object())):
            with self.subTest(values=values[:2]), self.assertRaises(ValueError):
                self.harness.run(
                    run_id=values[0], binding=self.binding,
                    command=values[1], snapshot=values[2])
        self.assertEqual(self.runner.trace, [])
        self.assert_clean()

    def test_lock_deadline_is_runtime_busy_not_scaffolding_unsafe(self):
        base = time.monotonic()
        calls = 0
        def clock():
            nonlocal calls
            calls += 1
            return base if calls <= 2 else base + docker.OPERATION_TIMEOUT_SECONDS
        self.harness = self.make_harness(clock=clock)
        fd = os.open(self.store.runtime_lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_reason("runtime_busy")
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertEqual(self.runner.trace, [])
        self.assert_clean()

    def test_effect_deadline_reserves_fresh_cleanup_and_preserves_primary_reason(self):
        clock = MutableClock()
        effect_deadline = (clock.now + docker.OPERATION_TIMEOUT_SECONDS
                           - docker.CLEANUP_TIMEOUT_SECONDS
                           - docker.POSTFLIGHT_TIMEOUT_SECONDS)
        class ExpiringRunner(HarnessRunner):
            def run(inner, argv, *, timeout_seconds, output_limit_bytes, env=None):
                result = super().run(
                    argv, timeout_seconds=timeout_seconds,
                    output_limit_bytes=output_limit_bytes, env=env)
                if tuple(argv[1:4]) == ("container", "start", "--attach"):
                    clock.now = effect_deadline + 0.01
                    return docker.CommandResult.fake(
                        result.returncode, timed_out=True)
                return result
        self.runner = ExpiringRunner()
        self.harness = self.make_harness(runner=self.runner, clock=clock)
        self.assert_reason("workload_timeout")
        self.assertTrue(self.runner.removed)
        self.assert_clean()

    def test_deadlines_fail_closed_in_resolve_cleanup_and_postflight(self):
        for phase in ("resolve", "cleanup", "postflight"):
            with self.subTest(phase=phase):
                self.setUp()
                clock = MutableClock()
                hard = clock.now + docker.OPERATION_TIMEOUT_SECONDS
                effect = (hard - docker.CLEANUP_TIMEOUT_SECONDS
                          - docker.POSTFLIGHT_TIMEOUT_SECONDS)
                class PhaseRunner(HarnessRunner):
                    def run(inner, argv, *, timeout_seconds, output_limit_bytes, env=None):
                        args = tuple(argv[1:3])
                        if phase == "cleanup" and args == ("container", "rm"):
                            inner.trace.append((
                                tuple(argv), timeout_seconds, output_limit_bytes, dict(env or {})))
                            clock.now = hard
                            return docker.CommandResult.fake(1, stderr="remove deadline")
                        result = super().run(
                            argv, timeout_seconds=timeout_seconds,
                            output_limit_bytes=output_limit_bytes, env=env)
                        contexts = sum(
                            row[0][1:3] == ("context", "show") for row in inner.trace)
                        if phase == "resolve" and args == ("context", "show"):
                            clock.now = effect + 0.01
                        elif (phase == "postflight" and args == ("context", "show")
                              and contexts >= 2):
                            clock.now = hard + 0.01
                        return result
                self.runner = PhaseRunner()
                self.harness = self.make_harness(runner=self.runner, clock=clock)
                expected = {"resolve": "authority_invalid", "cleanup": "cleanup_failed",
                            "postflight": "postflight_changed"}[phase]
                self.assert_reason(expected)
                if phase == "cleanup":
                    self.assertFalse(self.runner.removed)
                    journals = self.store.read_journals()
                    self.assertEqual(len(journals), 1)
                    self.assertEqual(journals[0]["lifecycle_state"], "removing")
                else:
                    self.assert_clean()

    def test_oom_and_malformed_state_are_unavailable(self):
        class StateRunner(HarnessRunner):
            def __init__(inner, mode):
                super().__init__()
                inner.mode = mode
            def run(inner, argv, *, timeout_seconds, output_limit_bytes, env=None):
                result = super().run(
                    argv, timeout_seconds=timeout_seconds,
                    output_limit_bytes=output_limit_bytes, env=env)
                if (tuple(argv[1:3]) == ("container", "inspect") and inner.started
                        and result.returncode == 0):
                    doc = json.loads(result.stdout.data)
                    if inner.mode == "oom":
                        doc["State"]["OOMKilled"] = True
                    else:
                        doc["State"]["Status"] = "running"
                    return docker.CommandResult.fake(stdout=json.dumps(doc))
                return result
        for mode, expected in (("oom", "workload_oom"),
                               ("malformed", "workload_state_invalid")):
            with self.subTest(mode=mode):
                self.setUp()
                self.runner = StateRunner(mode)
                self.harness = self.make_harness(runner=self.runner)
                self.assert_reason(expected)
                self.assert_clean()

    def test_postflight_drift_produces_no_result_after_cleanup(self):
        changed = support.healthy_environment(self.root, version="3.12.1")
        postflight = False
        def inspect_environment():
            return changed if postflight else self.current_environment
        real_finish = self.store.finish_operation
        def finish(*args, **kwargs):
            nonlocal postflight
            result = real_finish(*args, **kwargs)
            postflight = True
            return result
        self.store.environment_inspector = inspect_environment
        self.harness = self.make_harness()
        with patch.object(self.store, "finish_operation", side_effect=finish), patch.object(
                execution, "_admit_result", wraps=execution._admit_result) as admit:
            self.assert_reason("postflight_changed")
        admit.assert_not_called()
        self.assert_clean()

    def test_base_exception_is_preserved_after_exact_recovery(self):
        class CancelRunner(HarnessRunner):
            def run(inner, argv, *, timeout_seconds, output_limit_bytes, env=None):
                if tuple(argv[1:4]) == ("container", "start", "--attach"):
                    raise KeyboardInterrupt()
                return super().run(
                    argv, timeout_seconds=timeout_seconds,
                    output_limit_bytes=output_limit_bytes, env=env)
        self.runner = CancelRunner()
        self.harness = self.make_harness(runner=self.runner)
        with self.assertRaises(KeyboardInterrupt):
            self.run_harness()
        self.assertTrue(self.runner.removed)
        self.assert_clean()

    def test_begin_crash_window_is_recovered_and_base_exception_remains_visible(self):
        begin = self.store.begin_operation
        def interrupted(intent):
            begin(intent)
            raise SystemExit("cancel after intent publication")
        with patch.object(self.store, "begin_operation", side_effect=interrupted):
            with self.assertRaisesRegex(SystemExit, "cancel after intent"):
                self.run_harness()
        self.assertFalse(self.runner.created)
        self.assert_clean()

    def test_create_before_cid_and_remove_before_staging_crash_windows_recover(self):
        real_write_cid = self.store.write_cid
        cid_calls = 0
        def interrupted_cid(*args, **kwargs):
            nonlocal cid_calls
            cid_calls += 1
            if cid_calls == 1:
                raise SystemExit("cancel before cid publication")
            return real_write_cid(*args, **kwargs)
        with patch.object(self.store, "write_cid", side_effect=interrupted_cid):
            with self.assertRaisesRegex(SystemExit, "before cid"):
                self.run_harness()
        self.assertTrue(self.runner.removed)
        self.assert_clean()

        self.setUp()
        real_cleanup = execution.cleanup_staging
        cleanup_calls = 0
        def interrupted_cleanup(*args, **kwargs):
            nonlocal cleanup_calls
            cleanup_calls += 1
            if cleanup_calls == 1:
                raise SystemExit("cancel after exact remove")
            return real_cleanup(*args, **kwargs)
        with patch.object(execution, "cleanup_staging", side_effect=interrupted_cleanup):
            with self.assertRaisesRegex(SystemExit, "after exact remove"):
                self.run_harness()
        self.assertTrue(self.runner.removed)
        self.assertEqual(cleanup_calls, 2)
        self.assert_clean()

        self.setUp()
        real_finish = self.store.finish_operation
        finish_calls = 0
        def interrupted_finish(*args, **kwargs):
            nonlocal finish_calls
            finish_calls += 1
            if finish_calls == 1:
                raise SystemExit("cancel after staging cleanup")
            return real_finish(*args, **kwargs)
        with patch.object(self.store, "finish_operation", side_effect=interrupted_finish):
            with self.assertRaisesRegex(SystemExit, "after staging cleanup"):
                self.run_harness()
        self.assertTrue(self.runner.removed)
        self.assertEqual(finish_calls, 2)
        self.assert_clean()

    def test_base_exception_from_handler_recovery_is_never_converted(self):
        self.runner = HarnessRunner(timed_out=True)
        self.harness = self.make_harness(runner=self.runner)
        real_recovery = execution.recover_operations
        recovery_calls = 0
        def interrupted_recovery(*args, **kwargs):
            nonlocal recovery_calls
            recovery_calls += 1
            if recovery_calls == 2:
                raise KeyboardInterrupt()
            return real_recovery(*args, **kwargs)
        with patch.object(execution, "recover_operations", side_effect=interrupted_recovery):
            with self.assertRaises(KeyboardInterrupt):
                self.run_harness()
        self.assertEqual(recovery_calls, 2)
        journals = self.store.read_journals()
        self.assertEqual(len(journals), 1)
        self.assertEqual(journals[0]["lifecycle_state"], "created")

    def test_injected_runner_cannot_forge_admitted_output_evidence(self):
        class ForgedRunner(HarnessRunner):
            def run(inner, argv, *, timeout_seconds, output_limit_bytes, env=None):
                result = super().run(
                    argv, timeout_seconds=timeout_seconds,
                    output_limit_bytes=output_limit_bytes, env=env)
                if tuple(argv[1:4]) == ("container", "start", "--attach"):
                    forged = docker.StreamCapture(
                        b"forged", 6, "sha256:" + "0" * 64, "forged")
                    return docker.CommandResult(
                        result.returncode, False, False, forged, result.stderr, 0)
                return result
        self.runner = ForgedRunner()
        self.harness = self.make_harness(runner=self.runner)
        with patch.object(execution, "_admit_result", wraps=execution._admit_result) as admit:
            self.assert_reason("workload_state_invalid")
        admit.assert_not_called()
        self.assertTrue(self.runner.removed)
        self.assert_clean()


class ReachabilityTests(unittest.TestCase):
    def test_candidate_has_zero_public_reachability(self):
        plugin = Path(__file__).resolve().parents[1]
        repository = plugin.parents[1]
        files = [
            plugin / "application" / "transaction.py",
            plugin / "application" / "v2.py",
            plugin / "adapters" / "bridge.py",
            plugin / "adapters" / "sandbox" / "__init__.py",
        ]
        for root in (
            plugin / "adapters" / "claude", plugin / "adapters" / "codex",
            plugin / "adapters" / "pi", plugin / "ui", repository / "scripts",
        ):
            files.extend(path for path in root.rglob("*")
                         if path.is_file() and path.suffix in {".py", ".ts", ".js", ".mjs"})
        for path in files:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("DockerSpikeHarness", source, path)
            self.assertNotIn("execute_run_bound_spike", source, path)
            self.assertNotIn("sandbox.execution", source, path)


if __name__ == "__main__":
    unittest.main()
