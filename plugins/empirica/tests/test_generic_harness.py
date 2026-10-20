#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

from adapters.sandbox import generic_authority as authority
from adapters.sandbox import generic_harness as harness
from adapters.sandbox import generic_journal as journal
from adapters.sandbox import planner
from application import generic_spike
from core import spike_plan
from test_generic_authority import runtime
from test_generic_execution import FakeDocker, fixture


class Resolver:
    def __init__(self, backend_value, *, drift=False, fail=False):
        self.backend = backend_value
        self.drift = drift
        self.fail = fail
        self.calls = 0

    def resolve(self, selected, *, deadline):
        self.calls += 1
        if self.fail:
            raise RuntimeError("authority unavailable")
        facts = runtime()
        if self.drift and self.calls > 1:
            facts["server_raw_architecture"] = "arm64"
            facts["server_architecture"] = "arm64"
        return authority.ResolvedGenericAuthority(
            selected, Path(sys.executable).resolve(),
            (("DOCKER_CONFIG", "/config"), ("DOCKER_HOST", "unix:///var/run/docker.sock"),
             ("LANG", "C"), ("LC_ALL", "C")), Path("/work"), "linux", "amd64",
            facts["ncpu"], facts["mem_total"], facts)


class HarnessTests(unittest.TestCase):
    def setUp(self):
        temporary, self.store, op, capture = fixture()
        self.temporary = temporary
        self.addCleanup(temporary.cleanup)
        self.capture = capture
        self.plan = op.plan
        self.backend = op.backend
        self.request = generic_spike.GenericHarnessRequest(
            "run-1", self.backend, self.plan, self.plan.digest,
            spike_plan.FRAMEWORK_SPIKE_POLICY.digest, capture)
        self.operation_id = "o-" + "4" * 32
        sealed = spike_plan.SealedSpikeRequest(self.plan, spike_plan.FRAMEWORK_SPIKE_POLICY)
        token = harness._staging_token(self.operation_id, capture.snapshot_digest)
        self.operation = planner.compile_docker_operation(
            sealed, self.backend, "run-1", self.operation_id, token)

    def build(self, fake, resolver=None):
        resolver = Resolver(self.backend) if resolver is None else resolver
        return harness.GenericDockerHarness(
            self.store, resolver, effects_factory=lambda selected: fake,
            operation_id_factory=lambda: self.operation_id), resolver

    def test_success_and_natural_125_convert_to_flat_application_result(self):
        for code, verdict in ((0, generic_spike.GenericVerdict.PASS),
                              (125, generic_spike.GenericVerdict.FAIL)):
            fake = FakeDocker(self.operation, exit_code=code)
            service, resolver = self.build(fake)
            result = service.run(self.request)
            with self.subTest(code=code):
                self.assertIs(type(result), generic_spike.GenericHarnessResult)
                self.assertIs(result.verdict, verdict)
                self.assertEqual(result.exit_code, code)
                self.assertEqual(result.facts.input_digest, self.capture.snapshot_digest)
                self.assertEqual(resolver.calls, 2)

    def test_137_timeout_overflow_oom_failed_start_and_malformed_are_unavailable(self):
        cases = ({"exit_code": 137}, {"timeout": True}, {"overflow": "stdout"},
                 {"overflow": "stderr"}, {"oom": True}, {"failed_start": True},
                 {"malformed": True})
        for options in cases:
            service, _ = self.build(FakeDocker(self.operation, **options))
            result = service.run(self.request)
            with self.subTest(options=options):
                self.assertIs(type(result), generic_spike.GenericHarnessUnavailable)
                self.assertIs(result.reason, generic_spike.GenericUnavailableReason.LIFECYCLE_UNAVAILABLE)

    def test_authority_and_postflight_drift_have_closed_reasons(self):
        service, _ = self.build(FakeDocker(self.operation), Resolver(self.backend, fail=True))
        result = service.run(self.request)
        self.assertIs(result.reason, generic_spike.GenericUnavailableReason.AUTHORITY_INVALID)
        service, _ = self.build(FakeDocker(self.operation), Resolver(self.backend, drift=True))
        result = service.run(self.request)
        self.assertIs(result.reason, generic_spike.GenericUnavailableReason.POSTFLIGHT_CHANGED)

    def test_unfinished_journal_is_recovered_before_new_mutation(self):
        from adapters.sandbox import generic_staging
        journals = journal.GenericJournalStore(self.store)
        row = journals.begin(self.operation)
        manifest = generic_staging.materialize_generic_staging(
            self.store, self.operation.operation_id, self.plan, self.capture,
            self.operation.staging_token)
        row = journals.transition(self.operation.operation_id, "staged", "staged")
        row = journals.transition(
            self.operation.operation_id, "container_intent", "container_intent")
        row.cidfile.write_text("a" * 64)
        row.cidfile.chmod(0o600)
        journals.record_full_id(self.operation.operation_id, "container", "a" * 64)
        journals.transition(
            self.operation.operation_id, "container_created", "container_created")
        fake = FakeDocker(self.operation)
        fake.container_present = True
        fake.staging_override = manifest.root
        service, resolver = self.build(fake)
        result = service.run(self.request)
        self.assertIs(type(result), generic_spike.GenericHarnessResult)
        inspect_index = next(i for i, argv in enumerate(fake.calls)
                             if argv[:2] == ("container", "inspect"))
        remove_index = next(i for i, argv in enumerate(fake.calls)
                            if argv[:2] == ("container", "rm"))
        create_index = next(i for i, argv in enumerate(fake.calls)
                            if argv[:2] == ("container", "create"))
        self.assertLess(inspect_index, remove_index)
        self.assertLess(remove_index, create_index)
        self.assertEqual(resolver.calls, 3)

    def test_stuck_recovery_surfaces_private_operation_id_and_closed_public_reason(self):
        journals = journal.GenericJournalStore(self.store)
        journals.begin(self.operation)
        journals.transition(self.operation.operation_id, "container_intent", "container_intent")
        service, _ = self.build(FakeDocker(self.operation))
        result = service.run(self.request)
        self.assertIs(type(result), generic_spike.GenericHarnessUnavailable)
        self.assertIs(result.reason, generic_spike.GenericUnavailableReason.CLEANUP_UNAVAILABLE)
        self.assertIn(self.operation.operation_id, result.private_detail)
        self.assertIn("container intent lacks durable full-ID authority", result.private_detail)

    def test_exact_request_backend_and_operation_identity_are_sealed(self):
        wrong = generic_spike.GenericHarnessRequest(
            "run-other", self.backend, self.plan, self.plan.digest,
            spike_plan.FRAMEWORK_SPIKE_POLICY.digest, self.capture)
        fake = FakeDocker(self.operation)
        service = harness.GenericDockerHarness(
            self.store, Resolver(self.backend), effects_factory=lambda selected: fake,
            operation_id_factory=lambda: self.operation_id)
        result = service.run(wrong)
        self.assertIs(type(result), generic_spike.GenericHarnessUnavailable)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary, self.store, self.operation, self.capture = fixture()
        self.addCleanup(self.temporary.cleanup)
        self.journals = journal.GenericJournalStore(self.store)

    def test_closed_reconstruction_and_full_ids_require_durable_intent(self):
        row = self.journals.begin(self.operation)
        self.assertFalse(row.cidfile.exists())
        self.assertIsNone(self.journals.read_cidfile(self.operation.operation_id))
        self.assertEqual(row.operation, self.operation)
        with self.assertRaises(journal.GenericJournalError):
            self.journals.record_full_id(self.operation.operation_id, "container", "a" * 64)
        self.journals.transition(self.operation.operation_id, "container_intent", "container_intent")
        row = self.journals.record_full_id(
            self.operation.operation_id, "container", "a" * 64)
        self.assertEqual(row.container_id, "a" * 64)

    def test_recovery_discards_only_an_owned_bounded_atomic_transition_residue(self):
        self.journals.begin(self.operation)
        directory = self.journals._directory(self.operation.operation_id)
        temporary = directory / ".journal.json.tmp"
        temporary.write_bytes(b"partial")
        temporary.chmod(0o600)
        self.assertEqual(self.journals.load(self.operation.operation_id).operation, self.operation)
        self.assertFalse(temporary.exists())
        target = directory / "target"
        target.write_bytes(b"foreign")
        temporary.symlink_to(target)
        with self.assertRaises(journal.GenericJournalError):
            self.journals.load(self.operation.operation_id)
        self.assertTrue(target.exists())

    def test_request_operation_backend_and_cidfile_drift_fail_closed(self):
        self.journals.begin(self.operation)
        path = self.journals._directory(self.operation.operation_id) / "journal.json"
        document = __import__("json").loads(path.read_text())
        for mutate in (
            lambda d: d["compiler"].__setitem__("run_id", "other"),
            lambda d: d.__setitem__("operation_digest", "sha256:" + "9" * 64),
            lambda d: d["compiler"]["backend"].__setitem__("profile_id", "docker-networked-v1"),
        ):
            changed = copy.deepcopy(document)
            mutate(changed)
            path.write_text(json.dumps(changed))
            path.chmod(0o600)
            with self.assertRaises(journal.GenericJournalError):
                self.journals.load(self.operation.operation_id)
            path.write_text(json.dumps(document))
            path.chmod(0o600)
        cid = self.journals._directory(self.operation.operation_id) / "container.cid"
        cid.write_text("short")
        cid.chmod(0o600)
        with self.assertRaises(journal.GenericJournalError):
            self.journals.read_cidfile(
            self.operation.operation_id)


if __name__ == "__main__":
    unittest.main()
