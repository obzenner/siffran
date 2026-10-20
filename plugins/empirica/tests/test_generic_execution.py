#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from adapters.sandbox import common, generic_admission as admission
from adapters.sandbox import generic_execution as execution
from adapters.sandbox import generic_journal as journal
from adapters.sandbox import planner
from application import generic_spike
from core import spike_plan
from core.canonical import canonical_digest
from test_docker_planner import C0, D0, N0, FIXTURE, backend, request, static_document
from test_generic_staging import snapshot


class FakeDocker:
    def __init__(self, operation, *, exit_code=0, timeout=False, overflow=None, oom=False,
                 failed_start=False, malformed=False, create_loss=False, network_loss=False,
                 candidates=None, foreign=False, cleanup_failure=None, warning="warning",
                 no_cid=False, create_failure=False):
        self.operation = operation
        self.exit_code = exit_code
        self.timeout = timeout
        self.overflow = overflow
        self.oom = oom
        self.failed_start = failed_start
        self.malformed = malformed
        self.create_loss = create_loss
        self.network_loss = network_loss
        self.candidates = candidates
        self.foreign = foreign
        self.cleanup_failure = cleanup_failure
        self.warning = warning
        self.no_cid = no_cid
        self.create_failure = create_failure
        self.container_present = False
        self.network_present = False
        self.attached = False
        self.running = False
        self.calls = []
        self.destructive = []
        self.staging_override = None

    @staticmethod
    def raw(argv, code=0, stdout=b"", stderr=b"", *, timeout=False, limited=False):
        if isinstance(stdout, str):
            stdout = stdout.encode()
        if isinstance(stderr, str):
            stderr = stderr.encode()
        return admission.RawProcessObservation(
            tuple(argv), True, code, timeout, limited, False,
            admission.stream(stdout), admission.stream(stderr))

    def network_doc(self):
        containers = {C0: {}} if self.running else {}
        if self.foreign:
            containers["c" * 64] = {}
        return {"Id": N0, "Driver": "bridge", "Internal": False,
                "Labels": dict(self.operation.network.labels), "Containers": containers}

    def container_doc(self, staging):
        document = static_document(self.operation, network_id=N0 if self.operation.network else None)
        document["Id"] = C0
        document["Mounts"][0]["Source"] = str(staging)
        if self.running:
            document["State"] = {"Status": "running", "Running": True,
                                 "ExitCode": 0, "OOMKilled": False, "Error": ""}
            if self.operation.network:
                document["Networks"] = {self.operation.network.name: {
                    "NetworkID": N0, "EndpointID": "3" * 64}}
        elif self.attached:
            if self.failed_start:
                document["State"] = {"Status": "created", "Running": False,
                                     "ExitCode": 127, "OOMKilled": False,
                                     "Error": "OCI runtime create failed"}
            else:
                document["State"] = {"Status": "exited", "Running": False,
                                     "ExitCode": self.exit_code, "OOMKilled": self.oom,
                                     "Error": ""}
            if self.operation.network:
                document["Networks"] = {self.operation.network.name: {
                    "NetworkID": N0, "EndpointID": ""}}
        return document

    def run(self, argv, *, timeout_seconds, output_limit_bytes=256 * 1024):
        argv = tuple(argv)
        self.calls.append(argv)
        if argv[:2] == ("network", "create"):
            self.network_present = True
            if self.network_loss:
                raise OSError("lost after create")
            return self.raw(argv, stdout=N0 + "\n", stderr=self.warning)
        if argv[:2] == ("network", "ls"):
            values = self.candidates
            if values is None:
                values = [N0] if self.network_present else []
            return self.raw(argv, stdout="".join(item + "\n" for item in values))
        if argv[:2] == ("container", "create"):
            cidfile = Path(argv[argv.index("--cidfile") + 1])
            if cidfile.exists():
                return self.raw(
                    argv, 125,
                    stderr=FIXTURE["docker_cli_create_contract"]["cidfile_preexisting_error"])
            self.container_present = True
            if self.create_failure:
                cidfile.write_text(C0)
                cidfile.chmod(0o600)
                cidfile.unlink()
                self.container_present = False
                return self.raw(argv, 125, stderr="daemon rejected create")
            if not self.no_cid:
                fd = os.open(cidfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as stream:
                    stream.write(C0)
            if self.create_loss:
                raise OSError("lost after cidfile")
            return self.raw(argv, stdout=C0 + "\n", stderr=self.warning)
        if argv[:2] == ("container", "inspect") and "--format" in argv:
            if not self.container_present:
                return self.raw(argv, 1, stderr="Error: No such container: " + C0)
            staging = next(Path(item.split("source=", 1)[1].split(",", 1)[0])
                           for item in self.operation.command("container_create").argv
                           if "source=staging-token:" in item)
            if self.staging_override is not None:
                staging = self.staging_override
            # The executed create argv is the authoritative concrete staging path.
            for call in self.calls:
                if call[:2] == ("container", "create"):
                    mount = next(item for item in call if item.startswith("type=bind"))
                    staging = Path(mount.split("source=", 1)[1].split(",", 1)[0])
            if self.malformed and self.attached:
                return self.raw(argv, stdout="{")
            return self.raw(argv, stdout=json.dumps(self.container_doc(staging)), stderr=self.warning)
        if argv[:2] == ("network", "inspect") and "--format" in argv:
            if not self.network_present:
                return self.raw(argv, 1, stderr="Error response from daemon: network " + N0 + " not found")
            return self.raw(argv, stdout=json.dumps(self.network_doc()), stderr=self.warning)
        if argv[:2] == ("container", "rm"):
            self.destructive.append(argv)
            if self.cleanup_failure == "container":
                return self.raw(argv, 1, stderr="daemon error")
            self.container_present = False
            return self.raw(argv, stdout=C0)
        if argv[:2] == ("network", "rm"):
            self.destructive.append(argv)
            if self.cleanup_failure == "network":
                return self.raw(argv, 1, stderr="daemon error")
            self.network_present = False
            return self.raw(argv, stdout=N0)
        if argv[:2] == ("container", "inspect"):
            if self.container_present or self.cleanup_failure == "container":
                return self.raw(argv, stdout="present")
            return self.raw(argv, 1, stderr=FIXTURE["absence_stderr"]["container_no_such"])
        if argv[:2] == ("network", "inspect"):
            if self.network_present or self.cleanup_failure == "network":
                return self.raw(argv, stdout="present")
            return self.raw(argv, 1, stderr=FIXTURE["absence_stderr"]["network_not_found"])
        raise AssertionError(argv)

    def attach(self, argv, *, timeout_seconds, stdout_limit, stderr_limit):
        argv = tuple(argv)
        self.calls.append(argv)
        self.attached = True
        self.running = True
        if self.failed_start:
            self.running = False
            return admission.RawAttachObservation(
                self.raw(argv, 125, stderr="start failed"), True, False, 125)
        stdout = b"x" * (stdout_limit + 1) if self.overflow == "stdout" else b"out"
        stderr = b"x" * (stderr_limit + 1) if self.overflow == "stderr" else b"err"
        raw = self.raw(argv, -9 if self.timeout or self.overflow else self.exit_code,
                       stdout, stderr, timeout=self.timeout, limited=bool(self.overflow))
        natural = not self.timeout and not self.overflow
        self.running = False
        return admission.RawAttachObservation(raw, natural, not natural,
                                              self.exit_code if natural else None)

    def close(self):
        pass


def fixture(profile="docker-isolated-v1"):
    capture = snapshot((("a.py", b"x"),))
    captured = (spike_plan.CapturedFile(
        "a.py", "sha256:" + hashlib.sha256(b"x").hexdigest(), 1),)
    plan = replace(request(profile).plan, captured_files=captured,
                   capture_digest=canonical_digest([item.to_data() for item in captured]))
    sealed = spike_plan.SealedSpikeRequest(plan, spike_plan.FRAMEWORK_SPIKE_POLICY)
    op = planner.compile_docker_operation(
        sealed, backend(profile), "run-1", "o-" + "1" * 32, "stg-" + "2" * 32)
    temporary = tempfile.TemporaryDirectory()
    store = common.ExecutionStore(Path(temporary.name).resolve() / "sandbox")
    store.prepare_execution_scaffolding()
    journal.GenericJournalStore(store).prepare()
    return temporary, store, op, capture


class ExecutionLifecycleTests(unittest.TestCase):
    def run_case(self, profile="docker-isolated-v1", **options):
        temporary, store, op, capture = fixture(profile)
        self.addCleanup(temporary.cleanup)
        fake = FakeDocker(op, **options)
        result = execution.execute_generic_operation(
            store, op, capture, fake, backend_deadline=time.monotonic() + 60,
            postflight=lambda deadline: op.backend.execution_binding_digest)
        return store, op, fake, result

    def test_isolated_and_networked_success_zero(self):
        for profile in ("docker-isolated-v1", "docker-networked-v1"):
            with self.subTest(profile=profile):
                store, op, fake, result = self.run_case(profile)
                self.assertIs(result.verdict, generic_spike.GenericVerdict.PASS)
                self.assertEqual(result.exit_code, 0)
                self.assertFalse((store.staging / op.operation_id).exists())
                self.assertEqual(journal.GenericJournalStore(store).scan(), ())
                for argv in fake.destructive:
                    self.assertIn(argv[-1], {C0, N0})
                    self.assertNotIn(op.container.name, argv)

    def test_docker_owned_cidfile_is_absent_before_create_and_removed_after_finish(self):
        _, _, fake, result = self.run_case()
        self.assertEqual(result.exit_code, 0)
        create = next(call for call in fake.calls if call[:2] == ("container", "create"))
        cidfile = Path(create[create.index("--cidfile") + 1])
        self.assertFalse(cidfile.exists())

    def test_failed_create_deletes_cidfile_and_finishes_without_destructive_guess(self):
        temporary, store, op, capture = fixture()
        self.addCleanup(temporary.cleanup)
        fake = FakeDocker(op, create_failure=True)
        with self.assertRaises(execution.GenericExecutionUnavailable):
            execution.execute_generic_operation(
                store, op, capture, fake, backend_deadline=time.monotonic() + 60,
                postflight=lambda deadline: op.backend.execution_binding_digest)
        self.assertEqual(journal.GenericJournalStore(store).scan(), ())
        self.assertFalse(fake.destructive)
        self.assertFalse((store.staging / op.operation_id).exists())

    def test_natural_125_is_fail_and_warnings_do_not_invalidate(self):
        _, _, _, result = self.run_case(exit_code=125, warning="harmless warning")
        self.assertIs(result.verdict, generic_spike.GenericVerdict.FAIL)
        self.assertEqual(result.exit_code, 125)

    def test_unavailable_outcomes_cleanup_without_evidence(self):
        cases = [
            {"exit_code": 137}, {"timeout": True}, {"overflow": "stdout"},
            {"overflow": "stderr"}, {"oom": True}, {"failed_start": True},
            {"malformed": True},
        ]
        for options in cases:
            temporary, store, op, capture = fixture()
            self.addCleanup(temporary.cleanup)
            fake = FakeDocker(op, **options)
            with self.subTest(options=options), self.assertRaises(execution.GenericExecutionUnavailable):
                execution.execute_generic_operation(
                    store, op, capture, fake, backend_deadline=time.monotonic() + 60,
                    postflight=lambda deadline: op.backend.execution_binding_digest)
            self.assertFalse(fake.container_present)
            self.assertFalse((store.staging / op.operation_id).exists())
            self.assertEqual(journal.GenericJournalStore(store).scan(), ())

    def test_network_not_found_variant_does_not_wedge_the_runtime(self):
        store, _, _, result = self.run_case("docker-networked-v1")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(journal.GenericJournalStore(store).scan(), ())

    def test_create_cli_loss_recovers_only_from_secure_cidfile(self):
        _, _, fake, result = self.run_case(create_loss=True)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(any(argv[:2] == ("container", "inspect") for argv in fake.calls))

    def test_network_create_loss_adopts_one_exact_owned_candidate(self):
        _, _, fake, result = self.run_case("docker-networked-v1", network_loss=True)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(any(argv[:2] == ("network", "ls") for argv in fake.calls))

    def test_ambiguous_or_foreign_network_candidate_refuses_without_network_rm(self):
        for options in ({"network_loss": True, "candidates": [N0, "d" * 64]},
                        {"network_loss": True, "foreign": True}):
            temporary, store, op, capture = fixture("docker-networked-v1")
            self.addCleanup(temporary.cleanup)
            fake = FakeDocker(op, **options)
            with self.subTest(options=options), self.assertRaises(execution.GenericExecutionUnavailable):
                execution.execute_generic_operation(
                    store, op, capture, fake, backend_deadline=time.monotonic() + 60,
                    postflight=lambda deadline: op.backend.execution_binding_digest)
            self.assertFalse(any(argv[:2] == ("network", "rm") for argv in fake.destructive))

    def test_missing_cidfile_after_create_intent_retains_recovery_authority(self):
        temporary, store, op, capture = fixture()
        self.addCleanup(temporary.cleanup)
        fake = FakeDocker(op, no_cid=True)
        with self.assertRaises(execution.GenericExecutionUnavailable):
            execution.execute_generic_operation(
                store, op, capture, fake, backend_deadline=time.monotonic() + 60,
                postflight=lambda deadline: op.backend.execution_binding_digest)
        rows = journal.GenericJournalStore(store).scan()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].has_event("container_intent"))
        self.assertIsNone(rows[0].container_id)
        self.assertTrue((store.staging / op.operation_id).exists())
        self.assertFalse(fake.destructive)

    def test_recovery_is_safe_after_each_isolated_journal_transition(self):
        transitions = (
            (), ("staged",), ("staged", "container_intent"),
            ("staged", "container_intent", "container_created"),
            ("staged", "container_intent", "container_created", "static_verified"),
            ("staged", "container_intent", "container_created", "static_verified", "attaching"),
            ("staged", "container_intent", "container_created", "static_verified", "attaching",
             "terminal_observed"),
            ("staged", "container_intent", "container_created", "static_verified", "attaching",
             "terminal_observed", "cleanup"),
            ("staged", "container_intent", "container_created", "static_verified", "attaching",
             "terminal_observed", "cleanup", "removed"),
        )
        from adapters.sandbox import generic_staging
        for states in transitions:
            temporary, store, op, capture = fixture()
            self.addCleanup(temporary.cleanup)
            journals = journal.GenericJournalStore(store)
            row = journals.begin(op)
            fake = FakeDocker(op)
            if states and states[0] == "staged":
                manifest = generic_staging.materialize_generic_staging(
                    store, op.operation_id, op.plan, capture, op.staging_token)
                fake.staging_override = manifest.root
            for state in states:
                row = journals.transition(op.operation_id, state, state)
                if state == "container_intent":
                    row.cidfile.write_text(C0)
                    row.cidfile.chmod(0o600)
                    fake.container_present = True
                if state == "container_created":
                    row = journals.record_full_id(op.operation_id, "container", C0)
                if state in {"attaching", "terminal_observed"}:
                    fake.attached = True
                if state == "removed":
                    fake.container_present = False
            execution.recover_generic_operations(
                store, lambda operation: fake, deadline=time.monotonic() + 60)
            with self.subTest(states=states):
                self.assertEqual(journals.scan(), ())
                self.assertFalse((store.staging / op.operation_id).exists())
                self.assertFalse(fake.container_present)

    def test_cleanup_and_postflight_failures_admit_no_result(self):
        for options, postflight in (({"cleanup_failure": "container"}, lambda d: D0),
                                    ({}, lambda d: D0)):
            temporary, store, op, capture = fixture()
            self.addCleanup(temporary.cleanup)
            fake = FakeDocker(op, **options)
            with self.assertRaises(execution.GenericExecutionUnavailable):
                execution.execute_generic_operation(
                    store, op, capture, fake, backend_deadline=time.monotonic() + 60,
                    postflight=postflight)


if __name__ == "__main__":
    unittest.main()
