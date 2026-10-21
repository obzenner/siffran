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
from test_generic_execution import AUTHORITY, FakeDocker, fixture


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

    def test_each_unavailable_path_carries_the_reason_its_hint_describes(self):
        import importlib.util
        from unittest import mock
        from adapters.sandbox import generic_staging
        script = Path(__file__).resolve().parents[3] / "scripts" / "validate_docker_spike_contract.py"
        spec = importlib.util.spec_from_file_location("validate_docker_spike_contract", script)
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        hints = validator.REASON_HINTS
        reason = generic_spike.GenericUnavailableReason

        class FlakyPostflight(Resolver):
            def resolve(self, selected, *, deadline):
                if self.calls == 1:
                    self.calls += 1
                    raise RuntimeError("daemon stopped after the spike")
                return super().resolve(selected, deadline=deadline)

        def stuck():
            journals = journal.GenericJournalStore(self.store)
            journals.begin(self.operation, authority={**AUTHORITY, "endpoint": "unix:///o.sock"})
            journals.transition(self.operation.operation_id, "container_intent", "container_intent")

        staging = mock.patch.object(
            generic_staging, "materialize_generic_staging",
            side_effect=generic_staging.GenericStagingError("staging refused"))
        cases = (  # (path, fake options, resolver, setup, patch, reason, the hint's subject)
            ("daemon stopped", {}, Resolver(self.backend, fail=True), None, None,
             reason.AUTHORITY_INVALID, "start Docker"),
            ("natural 137", {"exit_code": 137}, None, None, None,
             reason.LIFECYCLE_UNAVAILABLE, "exit 137"),
            ("workload timeout", {"timeout": True}, None, None, None,
             reason.LIFECYCLE_UNAVAILABLE, "timeout"),
            ("staging refused", {}, None, None, staging,
             reason.LIFECYCLE_UNAVAILABLE, "staging"),
            ("killed create", {"kill_create": True}, None, None, None,
             reason.CLEANUP_UNAVAILABLE, "next spike retries recovery"),
            ("natural create rejection", {"create_failure": True}, None, None, None,
             reason.CLEANUP_UNAVAILABLE, "if it persists, ask the operator"),
            ("remove failed", {"cleanup_failure": "container"}, None, None, None,
             reason.CLEANUP_UNAVAILABLE, "next spike retries recovery"),
            ("stuck journal", {}, None, stuck, None,
             reason.CLEANUP_UNAVAILABLE, "if it persists, ask the operator"),
            ("authority drifted", {}, Resolver(self.backend, drift=True), None, None,
             reason.POSTFLIGHT_CHANGED, "changed during the spike"),
            ("daemon lost at postflight", {}, FlakyPostflight(self.backend), None, None,
             reason.POSTFLIGHT_CHANGED, "changed during the spike"),
        )
        for name, options, resolver, setup, patch, expected, subject in cases:
            with self.subTest(path=name):
                temporary, self.store, _, _ = fixture()
                self.addCleanup(temporary.cleanup)
                if setup is not None:
                    setup()
                service, _ = self.build(FakeDocker(self.operation, **options), resolver)
                if patch is None:
                    result = service.run(self.request)
                else:
                    with patch:
                        result = service.run(self.request)
                self.assertIs(type(result), generic_spike.GenericHarnessUnavailable)
                self.assertIs(result.reason, expected)
                self.assertIn(subject, hints[expected.value])
                if name == "natural create rejection":
                    # The deferred create keeps its bounded private diagnostic for the operator.
                    rows = journal.GenericJournalStore(self.store).scan()
                    self.assertEqual(len(rows), 1)
                    created = [item for item in rows[0].document["diagnostics"]
                               if item["kind"] == "container_create"]
                    self.assertEqual([item["returncode"] for item in created], [125])
                    self.assertIn("daemon rejected create", created[0]["stderr_tail"])
                    self.assertTrue(rows[0].has_event("container_create_unresolved"))
        guard = harness.GenericDockerHarness(self.store, Resolver(self.backend)).run(object())
        self.assertIs(guard.reason, reason.LIFECYCLE_UNAVAILABLE)

        # Another spike holds the runtime lock until the deadline: no Docker call is made.
        import fcntl
        import os
        temporary, self.store, _, _ = fixture()
        self.addCleanup(temporary.cleanup)
        holder = os.open(self.store.runtime_lock, os.O_RDWR)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX)
        self.addCleanup(fcntl.flock, holder, fcntl.LOCK_UN)
        ticks = iter(range(0, 10**6, 50))
        fake = FakeDocker(self.operation)
        held = harness.GenericDockerHarness(
            self.store, Resolver(self.backend), effects_factory=lambda selected: fake,
            operation_id_factory=lambda: self.operation_id,
            clock=lambda: float(next(ticks))).run(self.request)
        self.assertIs(held.reason, reason.AUTHORITY_INVALID)
        self.assertIn("another spike holding it", hints[held.reason.value])
        self.assertEqual(fake.calls, [])

        # Networked profile: the daemon rejects the private bridge create (host-side).
        class NetworkRejected(FakeDocker):
            def run(self, argv, **kw):
                if tuple(argv[:2]) == ("network", "create"):
                    self.calls.append(tuple(argv))
                    return self.raw(argv, 1, stderr="Error response from daemon: pool overlaps")
                return super().run(argv, **kw)

        temporary, self.store, networked, capture = fixture("docker-networked-v1")
        self.addCleanup(temporary.cleanup)
        token = harness._staging_token(self.operation_id, capture.snapshot_digest)
        operation = planner.compile_docker_operation(
            spike_plan.SealedSpikeRequest(networked.plan, spike_plan.FRAMEWORK_SPIKE_POLICY),
            networked.backend, "run-1", self.operation_id, token)
        fake = NetworkRejected(operation)
        rejected = harness.GenericDockerHarness(
            self.store, Resolver(networked.backend), effects_factory=lambda selected: fake,
            operation_id_factory=lambda: self.operation_id).run(
                generic_spike.GenericHarnessRequest(
                    "run-1", networked.backend, networked.plan, networked.plan.digest,
                    spike_plan.FRAMEWORK_SPIKE_POLICY.digest, capture))
        self.assertIs(rejected.reason, reason.LIFECYCLE_UNAVAILABLE)
        self.assertIn("network-create", hints[rejected.reason.value])
        self.assertIn("host-side (staging, network create, or static policy)",
                      hints[rejected.reason.value])
        self.assertFalse(any(argv[:2] == ("container", "create") for argv in fake.calls))
        self.assertEqual(journal.GenericJournalStore(self.store).scan(), ())

    def test_invalid_utf8_stderr_diagnostics_stay_within_the_journal_bound(self):
        class NoisyRejected(FakeDocker):
            def run(self, argv, **kw):
                if tuple(argv[:2]) == ("container", "create"):
                    self.calls.append(tuple(argv))
                    return self.raw(argv, 125, stderr=b"\xff" * 2000)
                return super().run(argv, **kw)

        service, _ = self.build(NoisyRejected(self.operation))
        result = service.run(self.request)
        self.assertIs(result.reason, generic_spike.GenericUnavailableReason.CLEANUP_UNAVAILABLE)
        rows = journal.GenericJournalStore(self.store).scan()
        created = [item for row in rows for item in row.document["diagnostics"]
                   if item["kind"] == "container_create"]
        self.assertEqual(len(created), 1)
        self.assertLessEqual(len(created[0]["stderr_tail"].encode("utf-8")), 4096)
        self.assertEqual(created[0]["stderr_bytes"], 2000)
        # The next spike recovers that journal; a successful create's invalid-UTF-8 warning is
        # bounded the same way, so the spike still passes.
        fake = FakeDocker(self.operation)
        fake.warning = b"\xff" * 2000
        service, _ = self.build(fake)
        result = service.run(self.request)
        self.assertIs(type(result), generic_spike.GenericHarnessResult)
        self.assertIs(result.verdict, generic_spike.GenericVerdict.PASS)

    def test_unfinished_journal_is_recovered_before_new_mutation(self):
        from adapters.sandbox import generic_staging
        journals = journal.GenericJournalStore(self.store)
        row = journals.begin(self.operation, authority=AUTHORITY)
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
        # Recovery reuses the current authority and effects: one resolve plus one postflight.
        self.assertEqual(resolver.calls, 2)

    def test_stuck_recovery_surfaces_private_operation_id_and_closed_public_reason(self):
        journals = journal.GenericJournalStore(self.store)
        journals.begin(self.operation, authority={**AUTHORITY, "endpoint": "unix:///other.sock"})
        journals.transition(self.operation.operation_id, "container_intent", "container_intent")
        fake = FakeDocker(self.operation)
        service, _ = self.build(fake)
        result = service.run(self.request)
        self.assertIs(type(result), generic_spike.GenericHarnessUnavailable)
        self.assertIs(result.reason, generic_spike.GenericUnavailableReason.CLEANUP_UNAVAILABLE)
        self.assertIn(self.operation.operation_id, result.private_detail)
        self.assertIn("different Docker endpoint", result.private_detail)
        self.assertFalse(any(argv[:2] == ("container", "create") for argv in fake.calls))

    def test_lost_create_intent_is_resolved_by_exact_name_before_new_mutation(self):
        journals = journal.GenericJournalStore(self.store)
        journals.begin(self.operation, authority=AUTHORITY)
        journals.transition(self.operation.operation_id, "container_intent", "container_intent")
        fake = FakeDocker(self.operation)
        service, _ = self.build(fake)
        result = service.run(self.request)
        self.assertIs(type(result), generic_spike.GenericHarnessResult)
        self.assertEqual(fake.calls[0][:2], ("container", "ls"))

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
        row = self.journals.begin(self.operation, authority=AUTHORITY)
        self.assertFalse(row.cidfile.exists())
        self.assertIsNone(self.journals.read_cidfile(self.operation.operation_id))
        self.assertEqual(row.operation.operation_id, self.operation.operation_id)
        self.assertEqual(row.operation.container.name, self.operation.container.name)
        self.assertEqual(row.authority, journal.daemon_identity(AUTHORITY))
        with self.assertRaises(journal.GenericJournalError):
            self.journals.record_full_id(self.operation.operation_id, "container", "a" * 64)
        self.journals.transition(self.operation.operation_id, "container_intent", "container_intent")
        row = self.journals.record_full_id(
            self.operation.operation_id, "container", "a" * 64)
        self.assertEqual(row.container_id, "a" * 64)

    def test_recovery_discards_only_an_owned_bounded_atomic_transition_residue(self):
        self.journals.begin(self.operation, authority=AUTHORITY)
        directory = self.journals._directory(self.operation.operation_id)
        temporary = directory / ".journal.json.tmp"
        temporary.write_bytes(b"partial")
        temporary.chmod(0o600)
        self.assertEqual(self.journals.load(self.operation.operation_id).operation.operation_id,
                         self.operation.operation_id)
        self.assertFalse(temporary.exists())
        target = directory / "target"
        target.write_bytes(b"foreign")
        temporary.symlink_to(target)
        with self.assertRaises(journal.GenericJournalError):
            self.journals.load(self.operation.operation_id)
        self.assertTrue(target.exists())

    def test_request_operation_backend_and_cidfile_drift_fail_closed(self):
        self.journals.begin(self.operation, authority=AUTHORITY)
        path = self.journals._directory(self.operation.operation_id) / "journal.json"
        document = __import__("json").loads(path.read_text())
        for mutate in (
            lambda d: d["operation"].__setitem__("operation_id", "o-" + "9" * 32),
            lambda d: d.__setitem__("operation_digest", "not-a-digest"),
            lambda d: d["operation"]["container"].__setitem__("name", "../escape"),
            lambda d: d["operation"]["container"]["labels"].__setitem__(
                "io.siffran.empirica.operation", "o-" + "9" * 32),
            lambda d: d["operation"].__setitem__("network", {"name": "x", "labels": {}}),
            lambda d: d.__setitem__("authority", {}),
            lambda d: d["authority"].__setitem__("memory_limit", True),
            lambda d: d.__setitem__("compiler", {}),
            lambda d: d["resource_ids"].__setitem__("network", "b" * 64),
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
        for content in ("short", "a" * 64 + "\n", ""):
            cid.write_text(content)
            cid.chmod(0o600)
            # Malformed content is not authority; the exact-name candidate set decides instead.
            self.assertIsNone(self.journals.read_cidfile(self.operation.operation_id))
        cid.write_text("a" * 64)
        self.assertEqual(self.journals.read_cidfile(self.operation.operation_id), "a" * 64)
        cid.chmod(0o644)
        with self.assertRaises(journal.GenericJournalError):
            self.journals.read_cidfile(self.operation.operation_id)


if __name__ == "__main__":
    unittest.main()
