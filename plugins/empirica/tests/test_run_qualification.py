#!/usr/bin/env python3
"""Run-scoped qualification owner regressions (behaviour only; fake Docker, fake clock).

Workers never run Docker: every Docker answer comes from the existing fake runner and
FakeDocker patterns. Contract values are read from the owner module under test — never
restated as literals here; the shape checks (closed record fields, bounds, reason
strings) belong to the contract conformance harness (E2c, not yet accepted) through the
closed record definition ``contracts/empirica-docker-spike/v1/qualification-record.schema.json``
and are listed in the worker report. Every test here covers a transition, a race, a
recovery or a classification, not a shape matrix.
"""
from __future__ import annotations

import copy
import json
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from adapters.sandbox import common
from adapters.sandbox import generic_authority as authority
from adapters.sandbox import generic_journal
from adapters.sandbox import process
from adapters.sandbox import probe_image
from adapters.sandbox import run_qualification as run_owner
from core import spike_plan
from test_docker_planner import D0, D1
from test_generic_authority import CLI, runtime
from test_generic_execution import FakeDocker
from test_generic_qualification import FakeKernelFactory

PIN = probe_image.pinned_probe_image()
PROBE_COUNT = len(run_owner.qualification.PROBE_ORDER)


class MutableClock:
    """A frozen clock the tests advance deterministically (the E4 fake-clock pattern)."""

    def __init__(self, started=1000.0) -> None:
        self.now = float(started)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds=0.05) -> None:
        self.now += seconds


class ImageInspects:
    """The one trust boundary runner: `docker image inspect` for the pinned probe image
    and every approved spike reference."""

    def __init__(self, *, probe_document=None, spike_documents=None, on_probe=None,
                 on_reference=None):
        self.probe_document = probe_document if probe_document is not None else {
            "Id": D1, "Os": "linux", "Architecture": "x86_64",
            "RepoDigests": [PIN.repo_digest], "Volumes": None,
        }
        self.spike_documents = dict(spike_documents or {})
        self.on_probe = on_probe
        self.on_reference = on_reference
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        argv = tuple(argv)
        assert argv[1:4] == ("image", "inspect", "--format"), argv
        reference = argv[-1]
        if reference == PIN.reference:
            if self.on_probe is not None:
                self.on_probe(self)
            return process.CommandResult.fake(stdout=json.dumps(self.probe_document))
        if self.on_reference is not None:
            self.on_reference(self, reference)
        document = self.spike_documents.get(reference)
        if document is None:
            return process.CommandResult.fake(1, stderr="Error: No such image: " + reference)
        if isinstance(document, Exception):
            return process.CommandResult.fake(1, stderr=str(document))
        return process.CommandResult.fake(stdout=json.dumps(document))


def spike_document(image_id=D1, raw_architecture="x86_64", volumes=None):
    return {"Id": image_id, "Os": "linux", "Architecture": raw_architecture, "Volumes": volumes}


def probe_document(repo_digests, *, image_id=D1, raw_architecture="x86_64", volumes=None):
    return {"Id": image_id, "Os": "linux", "Architecture": raw_architecture,
            "RepoDigests": list(repo_digests), "Volumes": volumes}


class OwnerHarness:
    """One owner call wired for fakes: identical kwargs across the regressions."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.clock = MutableClock()
        self.spike_images = ["python:3.12-slim"]
        self.profile_id = spike_plan.ISOLATED_PROFILE
        self.attempt_id = "a-1"
        self.docker_cli = Path(runtime()["docker_cli"])
        self.inspector_facts = copy.deepcopy(runtime())
        self.inspector_calls = 0
        self.manifests = [{"implementation_digest": D0}]
        self.kernel_wrapper = None
        self.effects_options = {
            "natural-125": {"exit_code": 125},
            "output-overflow": {"overflow": "stdout"},
            "timeout": {"timeout": True},
        }
        self.images = ImageInspects(spike_documents={
            "python:3.12-slim": spike_document(image_id=D1.replace("1", "a")),
        })
        self.runtime_inspector = self._runtime_inspector
        self.pinned_reobserver = None
        self.implementation_inspector = self._implementation_inspector
        self.recovery_effects = None

    def _runtime_inspector(self, recorded, *, deadline, **kwargs):
        self.inspector_calls += 1
        return authority.GenericRuntimeInspection(copy.deepcopy(self.inspector_facts))

    def _implementation_inspector(self):
        index = min(self.inspector_calls, len(self.manifests) - 1)
        return copy.deepcopy(self.manifests[index]) if self.manifests else None

    def effects_factory(self, resolved, operation, name):
        if name == "recovery":
            if self.recovery_effects is not None:
                return self.recovery_effects
            return FakeDocker(operation)
        return FakeDocker(operation, **self.effects_options.get(name, {}))

    def kernel_factory(self, *args, **kwargs):
        kernel = run_owner.qualification.ProductionProbeKernel(*args, **kwargs)
        if self.kernel_wrapper is not None:
            return self.kernel_wrapper(kernel)
        return kernel

    def call(self, **overrides):
        arguments = {
            "project_id": "project-1", "run_id": "run-1", "generation": 1,
            "attempt_id": self.attempt_id, "revision": 1, "proposal_digest": D0,
            "spike_images": list(self.spike_images), "profile_id": self.profile_id,
            "docker_cli": self.docker_cli,
            "runtime_inspector": self.runtime_inspector,
            "pinned_reobserver": self.pinned_reobserver or self._runtime_inspector,
            "implementation_inspector": self.implementation_inspector,
            "image_runner": self.images, "effects_factory": self.effects_factory,
            "kernel_factory": self.kernel_factory,
            "sandbox_root": self.root,
            "clock": self.clock,
        }
        arguments.update(overrides)
        return run_owner.qualify_run_environment(**arguments)


class IsolatedSandboxHomeTestCase(unittest.TestCase):
    """Every test in this module is wired to a temporary sandbox root; resolving the real
    operator home would leak test fixtures into it (live finding), so the default
    resolver is trapped for the duration of each test."""

    def setUp(self) -> None:
        trap = mock.patch.object(common, "sandbox_home",
                                 side_effect=AssertionError(
                                     "a test resolved the real sandbox home"))
        trap.start()
        self.addCleanup(trap.stop)


class OwnerTests(IsolatedSandboxHomeTestCase):
    def setUp(self):
        super().setUp()
        import tempfile
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = (Path(self.temporary.name) / "sandbox").resolve()
        self.root.mkdir(mode=0o700, parents=True)
        self.root.chmod(0o700)
        # freeze_loaded_release latches per process; tests reset it at its only seam.
        common._loaded_release = None
        common._loaded_release_failed = None
        self.store = run_owner.RunQualificationStore(self.root)

    def _fresh_cell(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cell = (Path(temporary.name) / "sandbox").resolve()
        cell.mkdir(mode=0o700, parents=True)
        cell.chmod(0o700)
        return cell

    def assertNoRecord(self, harness):
        records = run_owner.RunQualificationStore(harness.root).records
        self.assertFalse(records.exists() and any(records.iterdir()),
                         "a failed attempt must not publish a record")

    # --- success ------------------------------------------------------------

    def test_success_publishes_the_durable_record_write_first_and_seals_spike_images(self):
        harness = OwnerHarness(self.root)
        harness.spike_images = ["python:3.12-slim", "busybox:latest"]
        harness.images.spike_documents["busybox:latest"] = spike_document(
            image_id="sha256:" + "b" * 64, raw_architecture="x86_64")
        harness.inspector_facts["daemon_id"] = "the-observed-daemon-id"
        harness.manifests = [{"implementation_digest": D0}] * 32
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        record = dict(outcome.record)
        self.assertEqual(len(list(self.store.records.iterdir())), 1)
        digest = record["qualification_record_digest"]
        self.assertEqual(outcome, run_owner.RunQualificationBound(digest, record))
        loaded = self.store.load_record(digest, project_id="project-1", run_id="run-1",
                                        generation=1)
        self.assertEqual(loaded, record)
        # exact digest and durability: safe read-back, owner-only mode
        published = self.store.records / (digest.removeprefix("sha256:") + ".json")
        self.assertEqual(oct(published.stat().st_mode & 0o777), oct(common.MODE_FILE))
        self.assertEqual(common.safe_file(published), run_owner.fsio.dump_json(loaded))
        # the sealed facts and the run binding
        self.assertEqual(loaded["run_binding"], {
            "project_id": "project-1", "run_id": "run-1", "generation": 1,
            "attempt_id": "a-1", "revision": 1, "proposal_digest": D0})
        self.assertEqual(loaded["daemon_id"], "the-observed-daemon-id")
        self.assertEqual(loaded["probe_image_id"], D1)
        self.assertEqual(loaded["qualification_image"]["reference"], PIN.repo_digest)
        self.assertEqual(loaded["qualification_image"]["repo_digest"], PIN.repo_digest)
        self.assertEqual([row["reference"] for row in loaded["spike_images"]],
                         ["python:3.12-slim", "busybox:latest"])
        sealed = [row["id"] for row in loaded["spike_images"]]
        self.assertEqual(sealed, ["sha256:" + "a" * 64, "sha256:" + "b" * 64])
        for row in loaded["spike_images"]:
            self.assertEqual(row["os"], "linux")
            self.assertEqual(row["declared_volumes"], [])
        self.assertEqual([row["name"] for row in loaded["probes"]],
                         list(run_owner.qualification.PROBE_ORDER))
        self.assertEqual(loaded["cleanup"], {"container_ids_absent": True,
                                             "network_ids_absent": True,
                                             "staging_empty": True, "journals_empty": True})
        # write-first: publication happened before the return; the namespace holds the
        # record under records/ and nothing else — no pointer, no side files.
        published_files = sorted(str(item.relative_to(self.root / run_owner.NAMESPACE))
                                 for item in (self.root / run_owner.NAMESPACE).rglob("*")
                                 if item.is_file())
        self.assertEqual(published_files, ["records/" + digest.removeprefix("sha256:") + ".json"])
        # command destinations: the one probe-image inspection carries the pinned
        # endpoint, the private config, the exact projection and the exact pin —
        # every daemon read after discovery goes to the pinned endpoint.
        facts = runtime()
        config = self.root / run_owner.NAMESPACE / "configs" / spike_plan.ISOLATED_PROFILE
        self.assertEqual(harness.images.calls[0][0],
                         (facts["docker_cli"], "image", "inspect", "--format",
                          run_owner.PROBE_IMAGE_INSPECT_FORMAT, PIN.reference))
        self.assertEqual(harness.images.calls[0][1]["env"],
                         {"LANG": "C", "LC_ALL": "C", "DOCKER_HOST": facts["endpoint"],
                          "DOCKER_CONFIG": str(config)})

    # --- closed probe-image admission (4.3) ---------------------------------

    def test_probe_image_admission_failure_is_probe_image_untrusted_with_the_reference(self):
        cases = (
            probe_document([PIN.repository + "@" + D1]),
            probe_document([]),
        )
        for index, document in enumerate(cases):
            with self.subTest(index=index):
                harness = OwnerHarness(self.root)
                harness.images.probe_document = document
                outcome = harness.call()
                self.assertIs(type(outcome), run_owner.RunQualificationFailed)
                self.assertIs(outcome.reason,
                              run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)
                self.assertIn(PIN.reference, outcome.detail)
                self.assertIn("pull " + PIN.reference, outcome.detail)
                self.assertIsNone(outcome.probe)
                self.assertNoRecord(harness)

        harness = OwnerHarness(self.root)
        harness.images.probe_document = probe_document(
            [PIN.repo_digest], raw_architecture="aarch64")
        outcome = harness.call()
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)

        harness = OwnerHarness(self.root)
        harness.images.probe_document = probe_document(
            [PIN.repo_digest], volumes={"/data": {}})
        outcome = harness.call()
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)

        harness = OwnerHarness(self.root)
        harness.images = ImageInspects()
        harness.images.spike_documents["python:3.12-slim"] = spike_document(image_id=D1)
        outcome = harness.call(image_runner=_FailingRunner())
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)

    def test_its_probe_image_admission_keys_on_membership_never_on_the_local_id(self):
        # membership: a legitimate additional digest never disqualifies the pin
        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.images.probe_document = probe_document(
            [PIN.repository + "@" + D1, PIN.repo_digest])
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        self.assertEqual(outcome.record["qualification_image"]["repo_digest"], PIN.repo_digest)

        # ... while the containerd store's Id-equals-index-digest never admits alone
        harness = OwnerHarness(self.root)
        harness.images.probe_document = probe_document(
            [PIN.repository + "@" + D1], image_id=PIN.index_digest.replace("sha256:", "h"))
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)

    def test_the_probe_image_inspection_timeout_is_the_remaining_budget(self):
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        budget = 10.0

        def inspector(recorded, *, deadline, **kwargs):
            harness.clock.advance(budget - 0.5)
            return authority.GenericRuntimeInspection(copy.deepcopy(runtime()))

        harness.runtime_inspector = inspector
        harness.call(qualification_seconds=budget)  # expires later at the probe phase
        call = harness.images.calls[0]
        self.assertEqual(call[1]["timeout_seconds"], 0.5)

    # --- stale daemon, version change, implementation drift ------------------

    def test_stale_daemon_and_version_change_map_to_the_typed_stale_reason(self):
        drifts = (
            ("endpoint", {}, {"endpoint": "unix:///var/run/other.sock"}),
            ("daemon-id", {"daemon_id": "the-first-daemon-id"},
             {"daemon_id": "a-restarted-daemon-id"}),
            ("capability", {}, {"swap_limit": False}),
            ("version", {}, {"server_version": "99.0.0"}),
        )
        for label, pinned_overrides, drifted_overrides in drifts:
            with self.subTest(label=label):
                harness = OwnerHarness(self._fresh_cell())
                harness.manifests = [{"implementation_digest": D0}] * 32
                pinned = copy.deepcopy(runtime())
                pinned.update(pinned_overrides)
                drifted = copy.deepcopy(pinned)
                drifted.update(drifted_overrides)
                calls = []

                def reobserver(recorded, *, deadline, **kwargs):
                    calls.append(1)
                    # the pinned facts come from the first observation; every later
                    # re-observation answers with the drifted daemon
                    selected = (copy.deepcopy(pinned) if len(calls) <= 1
                                else copy.deepcopy(drifted))
                    return authority.GenericRuntimeInspection(selected)

                harness.runtime_inspector = reobserver
                harness.pinned_reobserver = reobserver
                outcome = harness.call()
                self.assertIs(type(outcome), run_owner.RunQualificationFailed)
                self.assertIs(outcome.reason, run_owner.RunQualificationReason.STALE_DAEMON)
                self.assertIsNone(outcome.probe)
                self.assertNoRecord(harness)

    def test_post_probe_version_change_is_stale_daemon_and_not_a_probe_failure(self):
        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        versioned = copy.deepcopy(runtime())
        calls = []

        def reobserver(recorded, *, deadline, **kwargs):
            calls.append(1)
            facts = copy.deepcopy(versioned)
            # the five kernel postflights observe the pinned version; only the
            # owner's own post-probe re-observation sees it change
            if len(calls) > PROBE_COUNT + 1:
                facts["server_version"] = facts["server_version"] + "-post"
            return authority.GenericRuntimeInspection(facts)

        harness.runtime_inspector = reobserver
        harness.pinned_reobserver = reobserver
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.STALE_DAEMON)
        self.assertIsNone(outcome.probe)
        self.assertNoRecord(harness)

    def test_implementation_drift_maps_to_stale_implementation_before_and_after_probes(self):
        harness = OwnerHarness(self._fresh_cell())
        # the freeze, the start check and the observation see the loading release; the
        # first kernel postflight sees a different one
        harness.manifests = ([{"implementation_digest": D0}] * 2
                            + [{"implementation_digest": D1}] * 40)
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.STALE_IMPLEMENTATION)
        self.assertIsNone(outcome.probe)
        self.assertNoRecord(harness)

        harness = OwnerHarness(self._fresh_cell())
        # every kernel postflight sees the loaded release; only the owner's own
        # post-probe re-observation finds the changed one
        harness.manifests = ([{"implementation_digest": D0}] * (PROBE_COUNT + 2)
                            + [{"implementation_digest": D1}] * 8)
        outcome = harness.call()
        self.assertIs(outcome.reason,
                      run_owner.RunQualificationReason.STALE_IMPLEMENTATION)
        self.assertNoRecord(harness)

    # --- probe failure carries its probe name -------------------------------

    def test_probe_failure_carries_exactly_its_probe_name(self):
        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.effects_options["natural-125"] = {"exit_code": 42}
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.PROBE_FAILED)
        self.assertEqual(outcome.probe, run_owner.qualification.PROBE_ORDER[1])
        self.assertNoRecord(harness)

    # --- cleanup failure keeps the kept reason, no probe member --------------

    def test_cleanup_failure_is_never_collapsed_and_keeps_residue_recoverable(self):
        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.effects_options["realization"] = {"cleanup_failure": "container"}
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.CLEANUP_UNAVAILABLE)
        self.assertIsNone(outcome.probe)
        self.assertNoRecord(harness)
        self.assertFalse(outcome.reason is run_owner.RunQualificationReason.PROBE_FAILED)

    def test_a_probe_cleanup_failure_at_the_deadline_reports_the_deadline_reason(self):
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        holder = harness

        class ExpiringCleanup(FakeDocker):
            def run(self, argv, *args, **kwargs):
                if tuple(argv)[:2] == ("container", "rm"):
                    holder.clock.advance(1000.0)
                return super().run(argv, *args, **kwargs)

        original = harness.effects_factory

        def factory(resolved, operation, name):
            if name == "realization":
                return ExpiringCleanup(operation, cleanup_failure="container")
            return original(resolved, operation, name)

        harness.effects_factory = factory
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertIsNone(outcome.probe)
        self.assertNoRecord(harness)

    # --- networked egress keeps its separate reason ----------------------------

    def test_a_networked_probe_egress_failure_preserves_its_separate_reason(self):
        harness = OwnerHarness(self._fresh_cell())
        harness.profile_id = spike_plan.NETWORKED_PROFILE
        harness.manifests = [{"implementation_digest": D0}] * 32

        class FastEgressFailure(FakeDocker):
            def attach(self, argv, *, timeout_seconds, stdout_limit, stderr_limit):
                self.attached = True
                self.exit_code = 46
                return run_owner.admission.RawAttachObservation(
                    self.raw(argv, 46, stderr="wget: bad address"), True, False, 46)

        original = harness.effects_factory

        def factory(resolved, operation, name):
            if name == "network-profile":
                return FastEgressFailure(operation)
            return original(resolved, operation, name)

        harness.effects_factory = factory
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.EGRESS_UNAVAILABLE)
        self.assertIsNone(outcome.probe)
        self.assertNoRecord(harness)

    # --- deadline at each stage ------------------------------------------------

    def test_deadline_at_the_lock_wait_binds_nothing(self):
        harness = OwnerHarness(self.root)
        store = common.ExecutionStore(self.root)
        store.prepare_execution_scaffolding()
        with common.runtime_lock(store, time.monotonic() + 5.0):
            outcome = harness.call(qualification_seconds=0.05, clock=time.monotonic)
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertNoRecord(harness)

    def test_deadline_during_authority_observation_and_spike_sealing(self):
        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.runtime_inspector = _ExpiringInspector()
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertNoRecord(harness)

        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.images.on_probe = lambda runner: harness.clock.advance(1000.0)
        outcome = harness.call()
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertNoRecord(harness)

    def test_deadline_before_the_probes_and_at_publication(self):
        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.spike_images.append("busybox:latest")
        harness.images.spike_documents["busybox:latest"] = spike_document(
            image_id="sha256:" + "b" * 64)
        harness.images.on_reference = (
            lambda runner, reference, harness=harness:
            harness.clock.advance(1000.0) if reference == "busybox:latest" else None)
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertNoRecord(harness)

        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        calls = []

        def reobserver(recorded, *, deadline, **kwargs):
            calls.append(1)
            if len(calls) > PROBE_COUNT + 1:
                # expire only after the probes and the post-probe re-observation
                # passed, so the next bounded read is the publication itself
                harness.clock.advance(1000.0)
            return authority.GenericRuntimeInspection(copy.deepcopy(runtime()))

        harness.runtime_inspector = reobserver
        harness.pinned_reobserver = reobserver
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertNoRecord(harness)

    def test_a_spike_image_inspection_that_exhausts_the_attempt_reports_the_deadline(self):
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.spike_images.append("busybox:latest")
        harness.images.spike_documents["busybox:latest"] = process.CommandResult.fake(
            stdout="{}", timed_out=True)
        harness.images.on_reference = (
            lambda runner, reference, harness=harness:
            harness.clock.advance(1000.0) if reference == "busybox:latest" else None)
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        # the inspection's own ceiling expired exactly as the attempt's budget did, so
        # the deadline reason is reported, never the command's own timeout reason
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertIsNot(outcome.reason,
                         run_owner.RunQualificationReason.IMAGE_INSPECT_TIMEOUT)
        self.assertNoRecord(harness)

    def test_deadline_during_recovery_is_the_deadline_reason(self):
        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.effects_options["realization"] = {"crash_after": 5}
        crashed = harness.call()
        self.assertIs(crashed.reason, run_owner.RunQualificationReason.CLEANUP_UNAVAILABLE)
        self.assertEqual(len(generic_journal.GenericJournalStore(
            common.ExecutionStore(self.root)).scan()), 1)
        fresh = OwnerHarness(self.root)
        fresh.manifests = [{"implementation_digest": D0}] * 32

        class ExpiringRecovery(FakeDocker):
            def run(self, argv, *args, **kwargs):
                fresh.clock.advance(1000.0)
                raise OSError("the daemon vanished mid-recovery")

        fresh.recovery_effects = ExpiringRecovery(None)
        outcome = fresh.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertNoRecord(fresh)
        # the journal is retained: a later attempt recovers it
        self.assertEqual(len(generic_journal.GenericJournalStore(
            common.ExecutionStore(self.root)).scan()), 1)

    def test_expiry_crossing_publication_fails_the_attempt_and_leaves_an_inert_orphan(self):
        cell = self._fresh_cell()
        harness = OwnerHarness(cell)
        harness.manifests = [{"implementation_digest": D0}] * 32
        records = run_owner.RunQualificationStore(cell).records
        mutable = MutableClock()

        class ExpiryOncePublished:
            """Expires the attempt the moment the record first appears on disk."""

            def __call__(self):
                try:
                    if any(records.iterdir()):
                        mutable.now = mutable.now + 1000.0
                except FileNotFoundError:
                    pass
                return mutable()

        harness.clock = ExpiryOncePublished()
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        # record written after expiry stays an inert orphan: it is on disk, nothing
        # binds it, and only its exact digest plus lineage can ever load it
        [published] = list(records.iterdir())
        digest = "sha256:" + published.stem
        store = run_owner.RunQualificationStore(cell)
        loaded = store.load_record(digest, project_id="project-1", run_id="run-1",
                                   generation=1)
        with self.assertRaises(Exception):
            store.load_record(digest, project_id="other-project", run_id="run-1",
                              generation=1)
        self.assertEqual(loaded["run_binding"]["attempt_id"], "a-1")

    # --- journals: recovery and serialization --------------------------------

    def test_crashed_attempt_leaves_a_recoverable_journal_and_the_next_attempt_recovers_it(self):
        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.effects_options["realization"] = {"crash_after": 5}
        crashed = harness.call()
        self.assertIs(type(crashed), run_owner.RunQualificationFailed)
        self.assertIs(crashed.reason, run_owner.RunQualificationReason.CLEANUP_UNAVAILABLE)
        journals = generic_journal.GenericJournalStore(
            common.ExecutionStore(self.root)).scan()
        self.assertEqual(len(journals), 1)

        fresh = OwnerHarness(self.root)
        fresh.manifests = [{"implementation_digest": D0}] * 32
        outcome = fresh.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        self.assertEqual(generic_journal.GenericJournalStore(
            common.ExecutionStore(self.root)).scan(), ())
        self.assertEqual(outcome.record["cleanup"]["journals_empty"], True)
        self.assertEqual(len(list(self.store.records.iterdir())), 1)

    def test_a_capability_refusal_still_recovers_prior_journals_first(self):
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.effects_options["realization"] = {"crash_after": 5}
        harness.call()

        fresh = OwnerHarness(harness.root)
        fresh.manifests = [{"implementation_digest": D0}] * 32
        fresh.inspector_facts["swap_limit"] = False
        outcome = fresh.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason,
                      run_owner.RunQualificationReason.RESOURCE_LIMITS_UNAVAILABLE)
        # recovery ran before the capability admission: the journal is gone
        self.assertEqual(generic_journal.GenericJournalStore(
            common.ExecutionStore(harness.root)).scan(), ())
        self.assertNoRecord(fresh)

    def test_an_implementation_refusal_still_recovers_prior_journals_first(self):
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.effects_options["realization"] = {"crash_after": 5}
        harness.call()

        fresh = OwnerHarness(harness.root)
        # the freeze sees the loaded release; the very next snapshot already drifted
        fresh.manifests = [{"implementation_digest": D0}] + [{"implementation_digest": D1}] * 8
        outcome = fresh.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason,
                      run_owner.RunQualificationReason.STALE_IMPLEMENTATION)
        self.assertEqual(generic_journal.GenericJournalStore(
            common.ExecutionStore(harness.root)).scan(), ())
        self.assertNoRecord(fresh)

    def test_a_foreign_qualification_config_still_recovers_prior_journals_first(self):
        cell = self._fresh_cell()
        harness = OwnerHarness(cell)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.effects_options["realization"] = {"crash_after": 5}
        harness.call()

        fresh = OwnerHarness(cell)
        fresh.manifests = [{"implementation_digest": D0}] * 32
        config = cell / run_owner.NAMESPACE / "configs" / spike_plan.ISOLATED_PROFILE
        (config / "foreign.json").write_text("{}")
        (config / "foreign.json").chmod(common.MODE_FILE)
        outcome = fresh.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.CAPABILITY_UNAVAILABLE)
        self.assertEqual(generic_journal.GenericJournalStore(
            common.ExecutionStore(cell)).scan(), ())
        self.assertNoRecord(fresh)

    def test_two_concurrent_attempts_serialize_on_the_shared_runtime_lock(self):
        for budget, expected in (
            (30.0, run_owner.RunQualificationBound),
            (0.05, run_owner.RunQualificationFailed),
        ):
            with self.subTest(budget=budget):
                cell = self._fresh_cell()
                harness = OwnerHarness(cell)
                harness.clock = time.monotonic
                harness.attempt_id = "a-" + str(int(budget * 100))
                harness.manifests = [{"implementation_digest": D0}] * 32
                store = common.ExecutionStore(cell)
                store.prepare_execution_scaffolding()
                outcomes = {}

                def attempt():
                    outcomes["first"] = harness.call(qualification_seconds=budget,
                                                     clock=time.monotonic)

                worker = threading.Thread(target=attempt)
                with common.runtime_lock(store, time.monotonic() + 5.0):
                    worker.start()
                    time.sleep(0.3)
                    outcomes["held"] = worker.is_alive()
                worker.join(timeout=10.0)
                if expected is run_owner.RunQualificationBound:
                    self.assertTrue(outcomes["held"],
                                    "the second attempt must wait for the lock")
                    self.assertIs(type(outcomes["first"]), expected)
                    self.assertTrue(any(
                        run_owner.RunQualificationStore(cell).records.iterdir()))
                else:
                    self.assertIs(type(outcomes["first"]), expected)
                    self.assertIs(outcomes["first"].reason,
                                  run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
                    records = run_owner.RunQualificationStore(cell).records
                    self.assertFalse(records.exists() and any(records.iterdir()))

    def test_concurrent_first_use_initializes_one_lock_and_never_both_enter(self):
        # Two attempts race the lock's first publication with barriers around the
        # initialization and the locked work: the exclusive create keeps a single
        # inode, so flock serializes them and the locked work never overlaps.
        for attempt_index in range(4):
            cell = self._fresh_cell()
            store = common.ExecutionStore(cell)
            barrier = threading.Barrier(2)
            active: list[int] = []
            overlaps: list[int] = []
            guard = threading.Lock()

            errors = []

            def attempt():
                try:
                    barrier.wait(timeout=10.0)   # both observe the absent lock together
                    store.prepare_execution_scaffolding()
                    barrier.wait(timeout=10.0)   # both contend the single lock together
                    with common.runtime_lock(store, time.monotonic() + 30.0):
                        with guard:
                            active.append(1)
                            overlaps.append(len(active))
                        time.sleep(0.05)
                        with guard:
                            active.pop()
                except BaseException as exc:   # a broken attempt must fail, never hang
                    errors.append(exc)
                    barrier.abort()

            workers = [threading.Thread(target=attempt) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=30.0)
            self.assertFalse(any(worker.is_alive() for worker in workers),
                             "a first-use attempt failed but never ended")
            self.assertEqual(errors, [])
            self.assertEqual(overlaps, [1, 1],
                             "the two first attempts must never both hold the lock")
            store._check_runtime_lock()

    # --- effects and resolved-authority wiring -------------------------------

    def test_effects_stages_receive_the_pinned_endpoint_and_the_private_config(self):
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        facts = runtime()
        config = harness.root / run_owner.NAMESPACE / "configs" / spike_plan.ISOLATED_PROFILE
        arrivals = []
        original = harness.effects_factory

        def factory(resolved, operation, name):
            arrivals.append((name, dict(resolved.environment), resolved.runtime["endpoint"],
                             resolved.runtime["docker_cli"], resolved.work_directory,
                             resolved.backend.profile_id))
            return original(resolved, operation, name)

        harness.effects_factory = factory
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        self.assertEqual([row[0] for row in arrivals][0], "recovery")
        self.assertEqual({row[5] for row in arrivals}, {spike_plan.ISOLATED_PROFILE})
        for name, environment, endpoint, cli, work, _profile in arrivals:
            self.assertEqual(environment, {
                "LANG": "C", "LC_ALL": "C", "DOCKER_HOST": facts["endpoint"],
                "DOCKER_CONFIG": str(config)})
            self.assertEqual(endpoint, facts["endpoint"])
            self.assertEqual(cli, facts["docker_cli"])
            self.assertEqual(work, harness.root / run_owner.NAMESPACE / "work")

    def test_the_default_effects_wiring_serves_the_pinned_endpoint(self):
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.kernel_factory = FakeKernelFactory()
        harness.effects_factory = None
        seen = []

        class QuietEffects:
            def run(self, *args, **kwargs):
                raise AssertionError("the fake kernel performs no docker effects")

            def attach(self, *args, **kwargs):
                raise AssertionError("the fake kernel performs no docker effects")

            def close(self) -> None:
                pass

        def concrete(resolved):
            seen.append(dict(resolved.environment))
            return QuietEffects()

        with mock.patch.object(run_owner, "_concrete_effects", concrete):
            outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        self.assertEqual(len(seen), 1)   # the recovery stage went through the default
        facts = runtime()
        self.assertEqual(seen[0]["DOCKER_HOST"], facts["endpoint"])

    # --- legacy /2 namespaces and lineage --------------------------------------

    def test_legacy_v2_records_and_pointers_at_their_real_paths_are_ignored(self):
        from test_generic_qualification import record as legacy_record

        legacy = legacy_record()
        payload = run_owner.fsio.dump_json(legacy)
        legacy_root = self.root / authority.NAMESPACE
        legacy_records = legacy_root / "records"
        legacy_pointers = legacy_root / "pointers"
        legacy_records.mkdir(parents=True, mode=0o700)
        legacy_pointers.mkdir(parents=True, mode=0o700)
        record_path = legacy_records / (legacy["qualification_record_digest"]
                                        .removeprefix("sha256:") + ".json")
        record_path.write_bytes(payload)
        record_path.chmod(common.MODE_FILE)
        # a second valid legacy record planted at a wrong digest path
        other = legacy_record(profile=spike_plan.NETWORKED_PROFILE)
        other_path = legacy_records / (D1.removeprefix("sha256:") + ".json")
        other_path.write_bytes(run_owner.fsio.dump_json(other))
        other_path.chmod(common.MODE_FILE)
        pointer = {
            "schema": run_owner.qualification.POINTER_SCHEMA,
            "profile_id": spike_plan.ISOLATED_PROFILE,
            "qualification_record_digest": legacy["qualification_record_digest"],
            "execution_binding_digest": legacy["execution_binding_digest"],
        }
        pointer_path = legacy_pointers / (spike_plan.ISOLATED_PROFILE + ".json")
        pointer_path.write_bytes(run_owner.fsio.dump_json(pointer))
        pointer_path.chmod(common.MODE_FILE)
        # the planted pointer and record are genuinely usable by the /2 store
        self.assertEqual(
            authority.GenericQualificationStore(self.root).load_current(
                spike_plan.ISOLATED_PROFILE), legacy)

        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        for planted, expected in ((record_path, payload),
                                  (pointer_path, run_owner.fsio.dump_json(pointer)),
                                  (other_path, run_owner.fsio.dump_json(other))):
            self.assertEqual(common.safe_file(planted), expected)
        self.assertFalse((self.root / run_owner.NAMESPACE / "pointers").exists())
        self.assertEqual(len(list(self.store.records.iterdir())), 1)

        # a changed body at the legacy digest path still never reaches the /3 owner
        record_path.write_bytes(run_owner.fsio.dump_json({**legacy, "schema": D1}))
        record_path.chmod(common.MODE_FILE)
        with self.assertRaises(authority.GenericQualificationError):
            authority.GenericQualificationStore(self.root).load_current(
                spike_plan.ISOLATED_PROFILE)
        fresh = OwnerHarness(self.root)
        fresh.manifests = [{"implementation_digest": D0}] * 32
        fresh.attempt_id = "a-2"
        second = fresh.call()
        self.assertIs(type(second), run_owner.RunQualificationBound)
        self.assertEqual(len(list(self.store.records.iterdir())), 2)

    def test_an_interruption_after_the_record_write_leaves_an_inert_orphan(self):
        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        original = run_owner.RunQualificationStore.write_record

        def interrupted(self, record):
            original(self, record)
            raise KeyboardInterrupt("interrupted between the durable write and the return")

        with mock.patch.object(run_owner.RunQualificationStore, "write_record", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                harness.call()
        records = list(self.store.records.iterdir())
        self.assertEqual(len(records), 1)
        digest = "sha256:" + records[0].stem
        orphan = self.store.load_record(digest, project_id="project-1", run_id="run-1",
                                        generation=1)
        self.assertEqual(orphan["run_binding"]["attempt_id"], "a-1")

        # the next attempt publishes its own record; the orphan stays inert at its digest
        second = OwnerHarness(self.root)
        second.manifests = [{"implementation_digest": D0}] * 32
        second.attempt_id = "a-2"
        outcome = second.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        self.assertNotEqual(outcome.record_digest, digest)
        self.assertEqual(len(list(self.store.records.iterdir())), 2)
        with self.assertRaises(Exception):
            self.store.load_record(digest, project_id="project-1", run_id="run-1",
                                   generation=2)

    def test_loading_refuses_a_redirected_records_directory(self):
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        first = harness.call()
        store = run_owner.RunQualificationStore(harness.root)
        # a symbolic link planted at the records directory redirects neither a load nor a
        # write: the directory is validated on load exactly as on write
        foreign = self._fresh_cell() / "foreign-records"
        foreign.mkdir(mode=0o700)
        foreign.chmod(common.MODE_DIR)
        for published in store.records.iterdir():
            published.unlink()
        store.records.rmdir()
        store.records.symlink_to(foreign)
        with self.assertRaises(common.GenericScaffoldingError):
            store.load_record(first.record_digest, project_id="project-1",
                              run_id="run-1", generation=1)
        with self.assertRaises(common.GenericScaffoldingError):
            store.write_record(dict(first.record))

    # --- ingress ---------------------------------------------------------------

    def test_invalid_ingress_is_refused_before_any_effect_or_record(self):
        # one derived-out-of-bound input per closed bound class: the rest of the shape
        # matrix is the contract conformance harness's (E2c); what this test keeps is
        # the invariant the matrix cannot see — the refusal precedes every effect
        overlong = "x" * (run_owner.MAX_IDENTIFIER_CHARS + 1)
        too_many = ["ref-" + str(index) for index in range(
            run_owner.MAX_SPIKE_IMAGE_REFERENCES + 1)]
        for override in ({"attempt_id": overlong}, {"spike_images": too_many}):
            with self.subTest(bound=sorted(override)):
                harness = OwnerHarness(self.root)
                outcome = harness.call(**override)
                self.assertIs(type(outcome), run_owner.RunQualificationFailed)
                self.assertIs(outcome.reason, run_owner.RunQualificationReason.INVALID_INPUT)
                self.assertIsNone(outcome.probe)
                self.assertEqual(harness.inspector_calls, 0,
                                 "ingress refusal precedes the authority observation")
        self.assertFalse(self.store.records.exists() and any(self.store.records.iterdir()))

    def test_an_unsupported_platform_is_a_capability_refusal_not_unreachability(self):
        # live finding: a Lima daemon on macOS is reachable but unsupported; the
        # production observation raises it as a platform refusal, never as
        # docker-unreachable
        harness = OwnerHarness(self._fresh_cell())
        runner = _UnsupportedPlatformRunner()

        def discovery(seed, *, deadline, **kwargs):
            return authority.inspect_current_runtime(
                seed, deadline=deadline, runner=runner, clock=harness.clock,
                host_facts=("Darwin", "arm64"))

        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.runtime_inspector = discovery
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.CAPABILITY_UNAVAILABLE)
        self.assertIsNot(outcome.reason, run_owner.RunQualificationReason.DOCKER_UNREACHABLE)
        self.assertIsNone(outcome.probe)
        self.assertNoRecord(harness)

    def test_spike_image_resolution_failures_keep_their_own_values(self):
        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.images = ImageInspects()   # spike reference resolves to "No such image"
        outcome = harness.call()
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.IMAGE_NOT_LOCAL)
        self.assertIsNone(outcome.probe)

        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.images.spike_documents["python:3.12-slim"] = spike_document(
            volumes={"/data": {}})
        outcome = harness.call()
        self.assertIs(outcome.reason,
                      run_owner.RunQualificationReason.IMAGE_DECLARES_VOLUMES)


class OutcomeAndStoreGuardTests(IsolatedSandboxHomeTestCase):
    """Typed-outcome and store guards the owner depends on: behaviour, not shape."""

    def setUp(self) -> None:
        super().setUp()

    def test_a_probe_failure_outcome_requires_and_forbids_its_probe_identity(self):
        from adapters.sandbox.run_qualification import (RunQualificationFailed,
                                                        RunQualificationReason)
        legal = RunQualificationFailed(
            RunQualificationReason.PROBE_FAILED, "d",
            run_owner.qualification.PROBE_ORDER[0])
        self.assertEqual(legal.probe, run_owner.qualification.PROBE_ORDER[0])
        # the name is required with its reason…
        with self.assertRaises(ValueError):
            RunQualificationFailed(RunQualificationReason.PROBE_FAILED, "d")
        # …forbidden with any other, and unknown names are refused
        with self.assertRaises(ValueError):
            RunQualificationFailed(RunQualificationReason.CLEANUP_UNAVAILABLE, "d",
                                   run_owner.qualification.PROBE_ORDER[0])
        with self.assertRaises(ValueError):
            RunQualificationFailed(RunQualificationReason.PROBE_FAILED, "d",
                                   run_owner.qualification.PROBE_ORDER[0] + "-x")

    def test_publishing_the_same_record_twice_re_proves_durability_without_rewriting(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = (Path(temporary.name) / "sandbox").resolve()
        root.mkdir(mode=0o700)
        common._loaded_release = None
        common._loaded_release_failed = None
        harness = OwnerHarness(root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        outcome = harness.call()
        store = run_owner.RunQualificationStore(root)
        first = store.write_record(outcome.record)
        inode = first.stat().st_ino
        with mock.patch.object(run_owner.fsio, "sync_directory") as sync:
            second = store.write_record(outcome.record)
        self.assertEqual(first, second)
        self.assertEqual(second.stat().st_ino, inode)   # no replacement on the retry
        self.assertEqual(len(list(store.records.iterdir())), 1)
        sync.assert_called()   # the identical-bytes retry also proves durability

        # existing bytes that differ are refused without being overwritten
        tampered = b"{\"tampered\": true}"
        first.write_bytes(tampered)
        first.chmod(common.MODE_FILE)
        with self.assertRaises(Exception):
            store.write_record(outcome.record)
        self.assertEqual(common.safe_file(first, cap=run_owner.MAX_RECORD_BYTES), tampered)

    def test_a_directory_sync_failure_withholds_publication(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = (Path(temporary.name) / "sandbox").resolve()
        root.mkdir(mode=0o700)
        common._loaded_release = None
        common._loaded_release_failed = None
        harness = OwnerHarness(root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        outcome = harness.call()   # publishes one record with strict syncs intact
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        store = run_owner.RunQualificationStore(root)
        # the identical-bytes retry re-proves durability: a failing directory sync
        # withholds the publication even for bytes already on disk
        with mock.patch.object(run_owner.fsio, "sync_directory",
                               side_effect=OSError("directory sync refused")):
            with self.assertRaises(OSError):
                store.write_record(dict(outcome.record))
        # and a first publication whose directory sync fails is never reported bound
        second = OwnerHarness(root)
        second.manifests = [{"implementation_digest": D0}] * 32
        second.attempt_id = "a-2"
        with mock.patch.object(run_owner.fsio, "sync_directory",
                               side_effect=OSError("directory sync refused")):
            failed = second.call()
        self.assertIs(type(failed), run_owner.RunQualificationFailed)
        self.assertIs(failed.reason, run_owner.RunQualificationReason.SANDBOX_UNSAFE)

    def test_authority_read_failure_is_docker_unreachable_and_never_capability(self):
        for message, reason in (
            ("Docker context read failed", run_owner.RunQualificationReason.DOCKER_UNREACHABLE),
            (run_owner.RunQualificationReason.RESOURCE_LIMITS_UNAVAILABLE.value
             + ": Docker SwapLimit must be enabled",
             run_owner.RunQualificationReason.RESOURCE_LIMITS_UNAVAILABLE),
        ):
            with self.subTest(message=message):
                import tempfile
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                root = (Path(temporary.name) / "sandbox").resolve()
                root.mkdir(mode=0o700)
                common._loaded_release = None
                common._loaded_release_failed = None

                def inspector(seed, *, deadline, message=message):
                    raise authority.GenericQualificationError(message)

                harness = OwnerHarness(root)
                harness.runtime_inspector = inspector
                outcome = harness.call()
                self.assertIs(type(outcome), run_owner.RunQualificationFailed)
                self.assertIs(outcome.reason, reason)
                self.assertIsNone(outcome.probe)

    def test_the_record_elapsed_milliseconds_are_the_injected_clocks_delta(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = (Path(temporary.name) / "sandbox").resolve()
        root.mkdir(mode=0o700)
        common._loaded_release = None
        common._loaded_release_failed = None
        harness = OwnerHarness(root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        original = harness.effects_factory

        def factory(resolved, operation, name):
            if name == "realization":
                harness.clock.advance(1.25)   # exactly 1250 elapsed milliseconds later
            return original(resolved, operation, name)

        harness.effects_factory = factory
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        self.assertEqual(outcome.record["qualification_elapsed_ms"], 1250)


class ProductionPinnedReobservationTests(IsolatedSandboxHomeTestCase):
    """B1: the default postflight path reads only the pinned endpoint, with destination
    witnesses — the production inspector, a fake command runner whose context changes
    after the initial read, and the command destinations recorded, not just facts."""

    def _runner(self):
        server = {"Version": "27.5.1", "Os": "linux", "Arch": "x86_64",
                  "PlatformName": "Docker Engine - Community"}
        remote_server = {"Version": "28.0.0", "Os": "linux", "Arch": "x86_64",
                         "PlatformName": "Docker Engine - Community"}
        info = {"OSType": "linux", "Architecture": "x86_64",
                "OperatingSystem": "Ubuntu 24.04",
                "SecurityOptions": ["name=cgroupns", "name=seccomp,profile=default"],
                "MemoryLimit": True, "SwapLimit": True,
                "CpuCfsQuota": True, "PidsLimit": True,
                "NCPU": 8, "MemTotal": 123, "CgroupVersion": "2",
                "CgroupDriver": "systemd", "ID": "engine-a"}
        remote_info = {**info, "ID": "engine-b"}

        class ContextSwitchingRunner:
            def __init__(self):
                self.calls = []
                self.switched = False

            def run(self, argv, *, timeout_seconds, output_limit_bytes, env):
                argv = tuple(argv)
                self.calls.append((argv, env))
                args = argv[1:]
                if args == ("context", "show"):
                    return process.CommandResult.fake(
                        stdout="remote\n" if self.switched else "default\n")
                if args[:2] == ("context", "inspect"):
                    endpoint = ("unix:///var/run/other.sock" if self.switched
                                else "unix:///var/run/docker.sock")
                    return process.CommandResult.fake(stdout=json.dumps(endpoint) + "\n")
                if env.get("DOCKER_HOST") == "unix:///var/run/other.sock":
                    selected_server, selected_info = remote_server, remote_info
                else:
                    selected_server, selected_info = server, info
                if args == ("version", "--format", "{{json .Client.Version}}"):
                    return process.CommandResult.fake(stdout='"27.5.1"\n')
                if args[:2] == ("version", "--format"):
                    return process.CommandResult.fake(
                        stdout=json.dumps(selected_server) + "\n")
                if args == ("info", "--format", "{{json .}}"):
                    return process.CommandResult.fake(
                        stdout=json.dumps(selected_info) + "\n")
                raise AssertionError(f"unexpected docker/cli call {args}")

        return ContextSwitchingRunner()

    def test_the_pinned_reobservation_reads_only_the_thrice_pinned_endpoint(self):
        runner = self._runner()
        discovered = authority.inspect_current_runtime(
            {"docker_cli": CLI}, deadline=time.monotonic() + 10, runner=runner,
            host_facts=("Linux", "x86_64"))
        pinned_endpoint = discovered.runtime["endpoint"]

        runner.switched = True     # the ambient context now names a second daemon
        reobserved = authority.inspect_pinned_runtime(
            discovered.runtime, deadline=time.monotonic() + 10, runner=runner,
            host_facts=("Linux", "x86_64"))

        # the re-observation ran exactly three reads, every one pinned to the discovered
        # endpoint, and not one context read: the second daemon was never asked
        reobservation_reads = runner.calls[-3:]
        self.assertEqual([call[0][1] for call in reobservation_reads],
                         ["version", "version", "info"])
        for call in reobservation_reads:
            self.assertEqual(call[1]["DOCKER_HOST"], pinned_endpoint)
        # the re-observed facts are the first daemon's: the second daemon's answers were
        # never adopted
        current = authority.validate_reobserved_runtime(
            discovered.runtime, reobserved.runtime)
        self.assertEqual(current, discovered.runtime)

        # the unpinned discovery path on the same recorded runtime WOULD have been
        # redirected to the second daemon, which is exactly what pinning prevents
        redirected = authority.inspect_current_runtime(
            discovered.runtime, deadline=time.monotonic() + 10, runner=runner,
            host_facts=("Linux", "x86_64"))
        self.assertEqual(redirected.runtime["endpoint"],
                         "unix:///var/run/other.sock")
        self.assertEqual(redirected.runtime["daemon_id"], "engine-b")


class _UnsupportedPlatformRunner:
    """A genuine Docker Engine daemon on a Lima VM over macOS: reachable, unsupported."""

    def run(self, argv, **kwargs):
        argv = tuple(argv)
        args = argv[1:]
        if args == ("context", "show"):
            return process.CommandResult.fake(stdout="lima-default\n")
        if args[:2] == ("context", "inspect"):
            return process.CommandResult.fake(
                stdout=json.dumps("unix:///Users/me/.lima/moby.sock") + "\n")
        if args == ("version", "--format", "{{json .Client.Version}}"):
            return process.CommandResult.fake(stdout='"28.0.0"\n')
        if args[:2] == ("version", "--format"):
            return process.CommandResult.fake(stdout=json.dumps({
                "Version": "28.0.0", "Os": "linux", "Arch": "aarch64",
                "PlatformName": "Docker Engine - Community"}) + "\n")
        if args == ("info", "--format", "{{json .}}"):
            return process.CommandResult.fake(stdout=json.dumps({
                "OSType": "linux", "Architecture": "arm64",
                "OperatingSystem": "Docker Engine (Lima)",
                "SecurityOptions": ["name=cgroupns", "name=seccomp,profile=default"],
                "MemoryLimit": True, "SwapLimit": True, "CpuCfsQuota": True,
                "PidsLimit": True, "NCPU": 8, "MemTotal": 123,
                "CgroupVersion": "2", "CgroupDriver": "systemd"}) + "\n")
        raise AssertionError(f"unexpected docker/cli call {args}")


class _FailingRunner:
    def run(self, argv, **kwargs):
        raise OSError("docker engine is unreachable")


class _ExpiringInspector:  # pragma: no cover - direct message carrier
    """A passive inspector that reports the real internal deadline-expired message."""

    def __call__(self, recorded, *, deadline, **kwargs):
        raise authority.GenericQualificationError("passive verification deadline expired")


if __name__ == "__main__":
    unittest.main()
