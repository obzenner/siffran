#!/usr/bin/env python3
"""Run-scoped qualification owner regressions (behaviour only; fake Docker, fake clock).

Workers never run Docker: every Docker answer comes from the existing fake runner and
FakeDocker patterns. Contract values are read from the owner module under test — never
restated as literals here; the shape checks (closed record fields, bounds, reason
strings) stay for the contract conformance harness (E2c, not on this branch) and are
listed in the worker report.
"""
from __future__ import annotations

import copy
import json
import threading
import time
import unittest
from pathlib import Path

from adapters.sandbox import common
from adapters.sandbox import generic_authority as authority
from adapters.sandbox import generic_journal
from adapters.sandbox import process
from adapters.sandbox import probe_image
from adapters.sandbox import run_qualification as run_owner
from test_docker_planner import D1
from test_generic_authority import D0, runtime
from test_generic_execution import FakeDocker

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


class OwnerHarness:
    """One owner call wired for fakes: identical kwargs across the regressions."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.clock = MutableClock()
        self.spike_images = ["python:3.12-slim"]
        self.profile_id = "docker-isolated-v1"
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
            "implementation_inspector": self.implementation_inspector,
            "image_runner": self.images, "effects_factory": self.effects_factory,
            "kernel_factory": self.kernel_factory,
            "sandbox_root": self.root, "clock": self.clock,
        }
        arguments.update(overrides)
        return run_owner.qualify_run_environment(**arguments)


class OwnerTests(unittest.TestCase):
    def setUp(self):
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
        # exact digest and durability: safe read-back, owner-only mode 0600
        published = self.store.records / (digest.removeprefix("sha256:") + ".json")
        self.assertEqual(oct(published.stat().st_mode & 0o777), "0o600")
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
        # every file in the run-scoped namespace is a published record: no pointer,
        # no side file, and the record is durably content-addressed
        published = sorted(str(item.relative_to(self.root / run_owner.NAMESPACE))
                            for item in (self.root / run_owner.NAMESPACE).rglob("*")
                            if item.is_file())
        self.assertEqual(published, ["records/" + digest.removeprefix("sha256:") + ".json"])

    # --- closed probe-image admission (4.3) ---------------------------------

    def test_probe_image_admission_failure_is_probe_image_untrusted_with_the_reference(self):
        cases = (
            {"Id": D1, "Os": "linux", "Architecture": "x86_64",
             "RepoDigests": [PIN.repository + "@sha256:" + "9" * 64], "Volumes": None},
            {"Id": D1, "Os": "linux", "Architecture": "x86_64", "Volumes": None},
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
        harness.images.probe_document = {"Id": D1, "Os": "linux",
                                         "Architecture": "aarch64",
                                         "RepoDigests": [PIN.repo_digest], "Volumes": None}
        outcome = harness.call()
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)

        harness = OwnerHarness(self.root)
        harness.images.probe_document = {"Id": D1, "Os": "linux", "Architecture": "x86_64",
                                         "RepoDigests": [PIN.repo_digest],
                                         "Volumes": {"/data": {}}}
        outcome = harness.call()
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)

        harness = OwnerHarness(self.root)
        harness.images = ImageInspects()
        harness.images.spike_documents["python:3.12-slim"] = spike_document(image_id=D1)
        outcome = harness.call(image_runner=_FailingRunner())
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)

    # --- stale daemon, version change, implementation drift ------------------

    def test_stale_daemon_and_version_change_map_to_the_typed_stale_reason(self):
        drifts = (
            ("endpoint", {}, {"endpoint": "unix:///var/run/other.sock"}),
            ("daemon-id", {"daemon_id": "the-first-daemon-id"},
             {"daemon_id": "a-restarted-daemon-id"}),
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

                def inspector(recorded, *, deadline, **kwargs):
                    calls.append(1)
                    # the pinned facts come from the first observation; every later
                    # re-observation answers with the drifted daemon
                    selected = (copy.deepcopy(pinned) if len(calls) <= 1
                                else copy.deepcopy(drifted))
                    return authority.GenericRuntimeInspection(selected)

                harness.runtime_inspector = inspector
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

        def inspector(recorded, *, deadline, **kwargs):
            calls.append(1)
            facts = copy.deepcopy(versioned)
            # the five kernel postflights observe the pinned version; only the
            # owner's own post-probe re-observation sees it change
            if len(calls) > PROBE_COUNT + 1:
                facts["server_version"] = facts["server_version"] + "-post"
            return authority.GenericRuntimeInspection(facts)

        harness.runtime_inspector = inspector
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.STALE_DAEMON)
        self.assertIn("version", outcome.detail)
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
        harness.effects_options["natural-125"] = {"exit_code": 42}
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.PROBE_FAILED)
        self.assertEqual(outcome.probe, run_owner.qualification.PROBE_ORDER[1])
        self.assertNoRecord(harness)

    # --- cleanup failure keeps the kept reason, no probe member --------------

    def test_cleanup_failure_is_never_collapsed_and_keeps_residue_recoverable(self):
        harness = OwnerHarness(self.root)
        harness.effects_options["realization"] = {"cleanup_failure": "container"}
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.CLEANUP_UNAVAILABLE)
        self.assertIsNone(outcome.probe)
        self.assertNoRecord(harness)
        self.assertFalse(outcome.reason is run_owner.RunQualificationReason.PROBE_FAILED)

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
        harness.runtime_inspector = _ExpiringInspector()
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertNoRecord(harness)

        harness = OwnerHarness(self.root)
        harness.images.on_probe = lambda runner: harness.clock.advance(1000.0)
        outcome = harness.call()
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertIn("spike image", outcome.detail)
        self.assertNoRecord(harness)

    def test_deadline_before_the_probes_and_at_publication(self):
        harness = OwnerHarness(self.root)
        harness.spike_images.append("busybox:latest")
        harness.images.spike_documents["busybox:latest"] = spike_document(
            image_id="sha256:" + "b" * 64)
        harness.images.on_reference = (
            lambda runner, reference, harness=harness:
            harness.clock.advance(1000.0) if reference == "busybox:latest" else None)
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertIn("probe", outcome.detail)
        self.assertNoRecord(harness)

        harness = OwnerHarness(self.root)
        calls = []

        def inspector(recorded, *, deadline, **kwargs):
            calls.append(1)
            if len(calls) > PROBE_COUNT + 1:
                # expire only after the probes and the post-probe re-observation
                # passed, so the next bounded read is the publication itself
                harness.clock.advance(1000.0)
            return authority.GenericRuntimeInspection(copy.deepcopy(runtime()))

        harness.runtime_inspector = inspector
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertIn("publication", outcome.detail)
        self.assertNoRecord(harness)

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

    # --- legacy /2 namespaces and lineage --------------------------------------

    def test_legacy_records_and_pointers_are_ignored_and_no_pointer_is_written(self):
        legacy_records = self.root / "generic-qualification-v2" / "records"
        legacy_records.mkdir(parents=True, mode=0o700, exist_ok=True)
        legacy_pointer = legacy_records.parent / "pointers"
        legacy_pointer.mkdir(parents=True, mode=0o700, exist_ok=True)
        (legacy_records / ("legacy.json")).write_text("{}")
        (legacy_pointer / "docker-isolated-v1.json").write_text("{}")
        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        self.assertEqual((legacy_records / "legacy.json").read_text(), "{}")
        self.assertEqual((legacy_pointer / "docker-isolated-v1.json").read_text(), "{}")
        self.assertFalse((self.root / run_owner.NAMESPACE / "pointers").exists())
        self.assertTrue(all(item.name.endswith(".json")
                            for item in self.store.records.iterdir()))
        self.assertEqual(len(list(self.store.records.iterdir())), 1)

    def test_a_crash_after_the_record_write_leaves_an_inert_orphan(self):
        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        first = harness.call()
        self.assertIs(type(first), run_owner.RunQualificationBound)
        digest = first.record_digest
        # the caller's CAS happens after the return; a crash at that boundary leaves the
        # published record inert: nothing points to it, and only the caller's explicit
        # digest plus its exact run lineage can ever load it.
        second = OwnerHarness(self.root)
        second.manifests = [{"implementation_digest": D0}] * 32
        second.attempt_id = "a-2"
        outcome = second.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        self.assertNotEqual(outcome.record_digest, digest)
        self.assertEqual(len(list(self.store.records.iterdir())), 2)
        self.assertEqual(common.safe_file(
            self.store.records / (digest.removeprefix("sha256:") + ".json")),
            run_owner.fsio.dump_json(first.record))
        # lineage refusal: the orphan binds another run only through its own CAS caller
        with self.assertRaises(Exception):
            self.store.load_record(digest, project_id="other-project", run_id="run-1",
                                   generation=1)
        with self.assertRaises(Exception):
            self.store.load_record(digest, project_id="project-1", run_id="run-1",
                                   generation=2)

    def test_invalid_ingress_is_refused_before_any_effect_or_record(self):
        cases = (
            {"project_id": ""}, {"project_id": "a/b"},
            {"attempt_id": ""}, {"generation": 0}, {"revision": 0},
            {"proposal_digest": "nope"},
            {"profile_id": "docker-armoire-v1"},
            {"spike_images": []},
            {"spike_images": ["ref-" + str(index) for index in range(
                run_owner.MAX_SPIKE_IMAGE_REFERENCES + 1)]},
        )
        for index, override in enumerate(cases):
            with self.subTest(index=index):
                harness = OwnerHarness(self.root)
                outcome = harness.call(**override)
                self.assertIs(type(outcome), run_owner.RunQualificationFailed)
                self.assertIs(outcome.reason, run_owner.RunQualificationReason.INVALID_INPUT)
                self.assertIsNone(outcome.probe)
        self.assertFalse(self.store.records.exists() and any(self.store.records.iterdir()))

    def test_spike_image_resolution_failures_keep_their_own_values(self):
        harness = OwnerHarness(self.root)
        harness.images = ImageInspects()   # spike reference resolves to "No such image"
        outcome = harness.call()
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.IMAGE_NOT_LOCAL)
        self.assertIsNone(outcome.probe)

        harness = OwnerHarness(self.root)
        harness.images.spike_documents["python:3.12-slim"] = spike_document(
            volumes={"/data": {}})
        outcome = harness.call()
        self.assertIs(outcome.reason,
                      run_owner.RunQualificationReason.IMAGE_DECLARES_VOLUMES)


class _FailingRunner:
    def run(self, argv, **kwargs):
        raise OSError("docker engine is unreachable")


class _ExpiringInspector:  # pragma: no cover - direct message carrier
    """A passive inspector that reports the real internal deadline-expired message."""

    def __call__(self, recorded, *, deadline, **kwargs):
        raise authority.GenericQualificationError("passive verification deadline expired")




if __name__ == "__main__":
    unittest.main()


class OutcomeAndStoreGuardTests(unittest.TestCase):
    """Typed-outcome and store guards the owner depends on: behaviour, not shape."""

    def test_a_failure_may_carry_a_probe_name_only_with_the_probe_failure_reason(self):
        from adapters.sandbox.run_qualification import (RunQualificationFailed,
                                                        RunQualificationReason)
        legal = RunQualificationFailed(
            RunQualificationReason.PROBE_FAILED, "d",
            run_owner.qualification.PROBE_ORDER[0])
        self.assertEqual(legal.probe, run_owner.qualification.PROBE_ORDER[0])
        for reason in (member for member in RunQualificationReason
                       if member is not RunQualificationReason.PROBE_FAILED):
            with self.subTest(reason=reason.value), self.assertRaises(ValueError):
                RunQualificationFailed(reason, "d", run_owner.qualification.PROBE_ORDER[0])
        with self.assertRaises(ValueError):
            RunQualificationFailed(RunQualificationReason.PROBE_FAILED, "d", "not-a-probe")

    def test_publishing_the_same_record_twice_is_an_inert_idempotent_write(self):
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
        second = store.write_record(outcome.record)
        self.assertEqual(first, second)
        self.assertEqual(len(list(store.records.iterdir())), 1)
        with self.assertRaises(Exception):
            store.write_record({**dict(outcome.record), "probe_image_id": D0})

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

    def test_the_injected_budget_must_be_positive_and_within_the_provisional_default(self):
        for budget in (0, -1.0, run_owner.DEFAULT_QUALIFICATION_SECONDS + 0.1, True):
            with self.subTest(budget=budget):
                import tempfile
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                root = (Path(temporary.name) / "sandbox").resolve()
                root.mkdir(mode=0o700)
                common._loaded_release = None
                common._loaded_release_failed = None
                harness = OwnerHarness(root)
                outcome = harness.call(qualification_seconds=budget)
                self.assertIs(type(outcome), run_owner.RunQualificationFailed)
                self.assertIs(outcome.reason, run_owner.RunQualificationReason.INVALID_INPUT)

    def test_the_record_elapsed_milliseconds_come_from_the_injected_clock(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = (Path(temporary.name) / "sandbox").resolve()
        root.mkdir(mode=0o700)
        common._loaded_release = None
        common._loaded_release_failed = None
        harness = OwnerHarness(root)
        march = [1.5]
        original_factory = harness.effects_factory

        def factory(resolved, operation, name):
            if name == "recovery":
                march[0] += 0.25
                harness.clock.now = harness.clock.now
                harness.clock.advance(march[0])
            return original_factory(resolved, operation, name)

        harness.effects_factory = factory
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        self.assertGreaterEqual(outcome.record["qualification_elapsed_ms"], 800)
