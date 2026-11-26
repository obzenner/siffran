#!/usr/bin/env python3
"""Run-scoped qualification owner regressions (behaviour only; fake Docker, fake clock).

Workers never run Docker: every Docker answer comes from the existing fake runner and
FakeDocker patterns. Contract values are read from the owner module under test — never
restated as literals here; the shape checks (closed record fields, bounds, reason
strings) belong to the contract conformance harness, which registers the closed record
definition at the restack through its own discovery and annotation conventions; the
semantic rules are carried by named owner tests and listed in the worker report. Every
test here covers a transition, a race, a recovery or a classification, not a shape
matrix.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from adapters.sandbox import common
from adapters.sandbox import generic_authority as authority
from adapters.sandbox import generic_journal
from adapters.sandbox import planner
from adapters.sandbox import process
from adapters.sandbox import probe_image
from adapters.sandbox import run_qualification as run_owner
from core import spike_plan
from core.records import RunKey
from test_docker_planner import D0, D1
from test_generic_authority import CLI, runtime
from test_generic_execution import FakeDocker, StubRunner
from test_generic_qualification import FakeKernelFactory

PIN = probe_image.pinned_probe_image()
PROBE_COUNT = len(run_owner.qualification.PROBE_ORDER)
#: The schema of the machine-wide `/2` pointer an Empirica 7.1 install may have left on disk.
LEGACY_POINTER_SCHEMA = "siffran.empirica.docker-spike-qualification-pointer/2"


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
    and every approved spike reference, and the bounded `docker image list --digests`
    fallback when the pinned reference finds no image locally."""

    def __init__(self, *, probe_document=None, spike_documents=None, on_probe=None,
                 on_reference=None, probe_absent=False, list_rows=None,
                 on_list=None, list_output_limited=False, list_exits_nonzero=False,
                 probe_timed_out=False):
        self.probe_document = probe_document if probe_document is not None else {
            "Id": D1, "Os": "linux", "Architecture": "x86_64",
            "RepoDigests": [PIN.repo_digest], "Volumes": None,
        }
        self.spike_documents = dict(spike_documents or {})
        self.on_probe = on_probe
        self.on_reference = on_reference
        self.probe_absent = probe_absent
        self.list_rows = list_rows
        self.on_list = on_list
        self.list_output_limited = list_output_limited
        self.list_exits_nonzero = list_exits_nonzero
        self.probe_timed_out = probe_timed_out
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        argv = tuple(argv)
        if argv[1:5] == ("image", "list", "--digests", "--no-trunc"):
            assert argv[5:7] == ("--format", run_owner.PROBE_IMAGE_LIST_FORMAT), argv
            self._check_env(kwargs)
            # the bounded read the owner itself declares: the listing is fetched with
            # the owner's own byte cap, never an unbounded one
            assert kwargs["output_limit_bytes"] == run_owner.MAX_IMAGE_LIST_BYTES, kwargs
            if self.on_list is not None:
                self.on_list(self)
            rows = [json.dumps(row) for row in (self.list_rows if self.list_rows is not None else ())]
            if self.list_output_limited:
                return process.CommandResult.fake(stdout="\n".join(rows), output_limited=True)
            if self.list_exits_nonzero:
                return process.CommandResult.fake(
                    125, stdout="\n".join(rows), stderr="Error listing images")
            return process.CommandResult.fake(stdout="\n".join(rows))
        assert argv[1:4] == ("image", "inspect", "--format"), argv
        reference = argv[-1]
        if reference == PIN.reference:
            if self.on_probe is not None:
                self.on_probe(self)
            if self.probe_absent:
                return process.CommandResult.fake(
                    1, stderr="Error: No such image: " + PIN.reference)
            if self.probe_timed_out:
                # a killed inspection may still hold a prefix that parses; the
                # timeout flag alone ends the read
                return process.CommandResult.fake(
                    stdout=json.dumps(self.probe_document), timed_out=True)
            # the projection is verified at the command boundary: the daemon is asked by the owner's exact constant, so a
            # mutant that drops the digest membership (or any other member the
            # admission reads) fails here before any fake answer can mask it
            assert argv[4] == run_owner.PROBE_IMAGE_INSPECT_FORMAT, argv
            self._check_env(kwargs)
            return process.CommandResult.fake(stdout=json.dumps(self.probe_document))
        if self.on_reference is not None:
            self.on_reference(self, reference)
        document = self.spike_documents.get(reference)
        if document is None:
            return process.CommandResult.fake(1, stderr="Error: No such image: " + reference)
        if isinstance(document, Exception):
            return process.CommandResult.fake(1, stderr=str(document))
        return process.CommandResult.fake(stdout=json.dumps(document))

    def _check_env(self, kwargs):
        assert kwargs["env"] == {
            "LANG": "C", "LC_ALL": "C", "DOCKER_HOST": runtime()["endpoint"],
            "DOCKER_CONFIG": kwargs["env"]["DOCKER_CONFIG"]}, kwargs["env"]


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
        self.on_inspect = None
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
        if self.on_inspect is not None:
            self.on_inspect(self.inspector_calls)
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

    def options(self):
        """The owner's own injection seams — every Docker answer a fake — as the keyword set a
        host port passes through unchanged (the bridge's ``_RunQualificationPort``)."""
        return {
            "runtime_inspector": self.runtime_inspector,
            "pinned_reobserver": self.pinned_reobserver or self._runtime_inspector,
            "implementation_inspector": self.implementation_inspector,
            "image_runner": self.images, "effects_factory": self.effects_factory,
            "kernel_factory": self.kernel_factory,
            "sandbox_root": self.root,
            "clock": self.clock,
        }

    def call(self, **overrides):
        arguments = {
            "project_id": "project-1", "run_id": "run-1", "generation": 1,
            "attempt_id": self.attempt_id, "revision": 1, "proposal_digest": D0,
            "spike_images": list(self.spike_images), "profile_id": self.profile_id,
            "docker_cli": self.docker_cli,
            **self.options(),
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

    def test_sealed_spike_rows_carry_no_capability_list_while_the_probe_image_keeps_its_own(self):
        # (operator decision, drift 4.5) spike images are inspected, never probed: a
        # sealed /3 spike row carries the requested reference, the full daemon image ID
        # observed at qualification (a diagnostic), the canonical platform and its
        # volume facts — and no capability list. The one trusted probe image keeps the
        # shared qualification helper's own list. The closed row shape itself is the
        # contract conformance harness's (the registered /3 schema's shapes own it);
        # this keeps the behaviour.
        harness = OwnerHarness(self.root)
        harness.spike_images = ["python:3.12-slim", "busybox:latest"]
        harness.images.spike_documents["busybox:latest"] = spike_document(
            image_id="sha256:" + "b" * 64, raw_architecture="x86_64")
        harness.manifests = [{"implementation_digest": D0}] * 32
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        for row in outcome.record["spike_images"]:
            self.assertNotIn("capabilities", row,
                             "a sealed spike row never lists capabilities")
        self.assertEqual(outcome.record["qualification_image"]["capabilities"],
                         list(run_owner.qualification.IMAGE_CAPABILITIES),
                         "the trusted probe image keeps the shared helper's list")
        self.assertEqual(len(list(self.store.records.iterdir())), 1)

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

    def test_probe_image_admission_matches_the_pinned_digest_under_any_repository_name(self):
        # (operator decision, drift 4.3) the pinned index digest is the image's content
        # identity; the repository name is not: a mirror or proxy pull reports the
        # pinned digest under its own repository name and is admitted, while a locally
        # built, loaded or retagged image — one whose RepoDigests carry some other
        # digest, under the pinned name or any other — is still refused.
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        mirror = "registry.mirror.example.com/proxy/alpine@" + PIN.index_digest
        harness.images.probe_document = probe_document([mirror])
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        # The digest proof is the entry the daemon
        # itself reported — the mirror's own name pinned to the observed digest,
        # never the canonical synthetic form it never observed — while the sealed
        # reference stays the pinned canonical one, the contract's requested identity
        self.assertEqual(outcome.record["qualification_image"]["repo_digest"], mirror)
        self.assertEqual(outcome.record["qualification_image"]["reference"], PIN.repo_digest)

        for repo_digests in (
            [PIN.repository + "@" + D1],
            [mirror.rsplit("@", 1)[0] + "@sha256:" + "c" * 64],
        ):
            with self.subTest(repo_digests=repo_digests):
                harness = OwnerHarness(self._fresh_cell())
                harness.manifests = [{"implementation_digest": D0}] * 32
                harness.images.probe_document = probe_document(repo_digests)
                outcome = harness.call()
                self.assertIs(type(outcome), run_owner.RunQualificationFailed)
                self.assertIs(outcome.reason,
                              run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)
                self.assertIsNone(outcome.probe)

    def test_a_mirror_named_local_image_is_admitted_by_its_repository_digest_when_the_pinned_name_is_absent(
            self):
        # The pinned name may find no image in the
        # local store while the digest exists under a proxy's own name: the digest
        # is the content identity, the repository name is not (the coordinator's
        # real mirror pull witnessed it end to end). One bounded
        # `docker image list --digests` locates any row whose digest is the pin —
        # the same admission rule as RepoDigests, repository-agnostic — and the
        # admission re-inspects the located row's digest-sealed reference
        # (`<repository>@<pin>`, answered by both image stores), NEVER the row's
        # reported image ID: the classic store's ID is the config digest, not the
        # index digest, so a by-ID lookup is store-dependent. The sealed reference
        # stays the pinned canonical one; the mirror's own daemon ID is sealed as
        # the diagnostic the record stores.
        mirror_id = "sha256:" + "d" * 64
        mirror_reference = "mirror.example/alpine@" + PIN.index_digest
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.images.probe_absent = True
        harness.images.list_rows = [
            {"Repository": "mirror.example/other", "Tag": "latest",
             "Digest": "sha256:" + "e" * 64, "ID": "sha256:" + "c" * 64},
            {"Repository": "mirror.example/alpine", "Tag": "3.22",
             "Digest": PIN.index_digest, "ID": mirror_id},
        ]
        harness.images.spike_documents[mirror_reference] = probe_document(
            ["mirror.example/alpine@" + PIN.index_digest], image_id=mirror_id)
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        commands = [call[0] for call in harness.images.calls]
        # The fallback admission itself is exactly three bounded image commands
        # The pinned-reference inspect, the one
        # bounded digest listing, and the re-inspection of the located row's
        # digest-sealed reference through the ordinary content rules — the same
        # projection the pinned inspect uses, so the third command reads the same
        # members the first one would.
        self.assertEqual(commands[0][1:], ("image", "inspect", "--format",
                                           run_owner.PROBE_IMAGE_INSPECT_FORMAT,
                                           PIN.reference))
        self.assertEqual(commands[1][1:], ("image", "list", "--digests", "--no-trunc",
                                           "--format", run_owner.PROBE_IMAGE_LIST_FORMAT))
        self.assertEqual(commands[2][1:], ("image", "inspect", "--format",
                                           run_owner.PROBE_IMAGE_INSPECT_FORMAT,
                                           mirror_reference))
        # A fourth image command exists, and it is a different stage, never a
        # re-inspection of the probe image: the per-spike image inspection of the
        # declared spike reference, through the planner's own projection (no
        # RepoDigests — spike images are inspected, never admitted by digest).
        # It is present in every successful qualification with at least one spike
        # image, so the whole attempt's image commands are the three admission
        # ones plus exactly one inspection per spike reference.
        self.assertEqual(commands[3][1:], ("image", "inspect", "--format",
                                           planner.IMAGE_INSPECT_FORMAT,
                                           harness.spike_images[0]))
        self.assertEqual(len(commands), 3 + len(harness.spike_images))
        # the record seals the pinned canonical reference and the exact inspected ID;
        # the digest proof is the entry the daemon reported for the located mirror,
        # so both names are observations
        self.assertEqual(outcome.record["qualification_image"]["repo_digest"],
                         mirror_reference)
        self.assertEqual(outcome.record["qualification_image"]["reference"], PIN.repo_digest)
        self.assertEqual(outcome.record["probe_image_id"], mirror_id)
        self.assertEqual(outcome.record["qualification_image"]["id"], mirror_id)

    def test_an_absent_probe_image_without_a_matching_local_digest_keeps_the_pull_hint(self):
        # the bounded digest list finds no row carrying the pinned digest (a locally
        # built image answers its own digest; a proxy pull kept the docker.io name and
        # was found by the pinned reference itself): the refusal is the untrusted
        # one, and the detail keeps the operator's pull hint — and a malformed or
        # oversized list is refused the same way, never parsed further.
        for label, rows in (
            ("a local image with its own digest",
             [{"Repository": "mirror.example/other", "Tag": "latest",
               "Digest": "sha256:" + "e" * 64, "ID": "sha256:" + "c" * 64}]),
            ("an unparsable row", ["not json"]),
        ):
            with self.subTest(rows=label):
                harness = OwnerHarness(self._fresh_cell())
                harness.manifests = [{"implementation_digest": D0}] * 32
                harness.images.probe_absent = True
                harness.images.list_rows = rows
                outcome = harness.call()
                self.assertIs(type(outcome), run_owner.RunQualificationFailed)
                self.assertIs(outcome.reason,
                              run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)
                self.assertIn("pull " + PIN.reference, outcome.detail)
                self.assertIsNone(outcome.probe)
                self.assertNoRecord(harness)

    def test_the_digest_listing_itself_is_bounded_at_its_declared_rows_and_bytes(self):
        # The fallback's one listing is a bounded
        # read: the owner's own row cap is read from the module, never restated here,
        # and the admission keeps both of its sides — a list with a row count past the
        # cap is refused the untrusted way, never parsed further (feeding a partial
        # read could admit a present image by luck of an alphabet that survives
        # truncation), while a list exactly at the cap still admits when the pinned
        # digest appears in it. An output-limited answer refuses the same way; the
        # byte cap is the formatter-level bound the runner enforces, asserted at the
        # boundary in the fake (ADR-0075: no bound restated as a literal here).
        cap = run_owner.MAX_IMAGE_LIST_ROWS
        pinned = {"Repository": "mirror.example/alpine", "Tag": "3.22",
                  "Digest": PIN.index_digest, "ID": "sha256:" + "d" * 64}
        other = {"Repository": "mirror.example/other", "Tag": "latest",
                 "Digest": "sha256:" + "e" * 64, "ID": "sha256:" + "c" * 64}
        oversized = [other] * cap + [pinned]
        with self.subTest(rows="one row past the cap"):
            harness = OwnerHarness(self._fresh_cell())
            harness.manifests = [{"implementation_digest": D0}] * 32
            harness.images.probe_absent = True
            harness.images.list_rows = oversized
            outcome = harness.call()
            self.assertIs(type(outcome), run_owner.RunQualificationFailed)
            self.assertIs(outcome.reason,
                          run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)
            self.assertIn("pull " + PIN.reference, outcome.detail)
            self.assertIsNone(outcome.probe)
            self.assertNoRecord(harness)
        with self.subTest(rows="exactly the cap"):
            harness = OwnerHarness(self._fresh_cell())
            harness.manifests = [{"implementation_digest": D0}] * 32
            harness.images.probe_absent = True
            harness.images.list_rows = [other] * (cap - 1) + [pinned]
            harness.images.spike_documents["mirror.example/alpine@" + PIN.index_digest] = \
                probe_document(["mirror.example/alpine@" + PIN.index_digest],
                                image_id=pinned["ID"])
            outcome = harness.call()
            self.assertIs(type(outcome), run_owner.RunQualificationBound)
            self.assertEqual(outcome.record["qualification_image"]["repo_digest"],
                             "mirror.example/alpine@" + PIN.index_digest)
        with self.subTest(answer="output-limited"):
            harness = OwnerHarness(self._fresh_cell())
            harness.manifests = [{"implementation_digest": D0}] * 32
            harness.images.probe_absent = True
            harness.images.list_rows = [pinned]
            harness.images.list_output_limited = True
            outcome = harness.call()
            self.assertIs(type(outcome), run_owner.RunQualificationFailed)
            self.assertIs(outcome.reason,
                          run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)
            self.assertIsNone(outcome.probe)
            self.assertNoRecord(harness)

    def test_a_listing_row_with_no_usable_repository_name_is_skipped_never_fatal(self):
        # a row whose Repository member is not a usable name carries no reference to
        # inspect even when its digest is the pin, so it is skipped — never fatal to a
        # list whose other rows name the pinned content (an unparsable row, by
        # contrast, ends the listing: a partially parsable answer is never trusted)
        mirror_id = "sha256:" + "d" * 64
        mirror_reference = "mirror.example/alpine@" + PIN.index_digest
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.images.probe_absent = True
        harness.images.list_rows = [
            {"Repository": None, "Tag": "latest", "Digest": PIN.index_digest,
             "ID": "sha256:" + "c" * 64},
            {"Repository": "mirror.example/alpine", "Tag": "3.22",
             "Digest": PIN.index_digest, "ID": mirror_id},
        ]
        harness.images.spike_documents[mirror_reference] = probe_document(
            ["mirror.example/alpine@" + PIN.index_digest], image_id=mirror_id)
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        self.assertEqual(outcome.record["qualification_image"]["repo_digest"],
                         mirror_reference)
        self.assertEqual(outcome.record["qualification_image"]["reference"], PIN.repo_digest)

    def test_the_sealed_digest_proof_is_the_sorted_first_entry_the_daemon_reported(self):
        # The digest proof member is the sorted-first
        # matching RepoDigests entry — one observation, chosen deterministically —
        # whatever order the daemon reported the entries in, while the reference
        # member stays the canonical pinned form
        later = "mirror-b.example/proxy/alpine@" + PIN.index_digest
        first = "mirror-a.example/proxy/alpine@" + PIN.index_digest
        for entries in ((later, first), (first, later)):
            with self.subTest(entries=list(entries)):
                harness = OwnerHarness(self._fresh_cell())
                harness.manifests = [{"implementation_digest": D0}] * 32
                harness.images.probe_document = probe_document(list(entries))
                outcome = harness.call()
                self.assertIs(type(outcome), run_owner.RunQualificationBound)
                self.assertEqual(
                    outcome.record["qualification_image"]["repo_digest"], first,
                    "the proof is the sorted-first reported entry, never the first listed")
                self.assertEqual(outcome.record["qualification_image"]["reference"],
                                 PIN.repo_digest)

    def test_a_timed_out_probe_inspection_is_never_admitted_even_when_its_output_parses(self):
        # the read ends the attempt on its timeout flag alone: a killed inspection
        # may hold a prefix that still parses (or a document the daemon completed
        # writing before the kill), and it must be refused — never admitted — because
        # the bounded read is over, so the inspection's own answer cannot be trusted
        # to be the whole one
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.images.probe_document = probe_document(
            ["mirror.example/alpine@" + PIN.index_digest])
        harness.images.probe_timed_out = True
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason,
                      run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)
        self.assertIsNone(outcome.probe)
        self.assertNoRecord(harness)

    def test_a_digest_listing_that_failed_is_never_trusted_even_when_its_rows_parse(self):
        # The one bounded listing is trusted for the
        # located digest-sealed reference alone and only from a successful answer:
        # a listing that exited nonzero must end the attempt the untrusted way even
        # though its stdout rows carry the pinned digest and would otherwise
        # admit — never a partial or failed answer's leftover rows
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        mirror_id = "sha256:" + "d" * 64
        mirror_reference = "mirror.example/alpine@" + PIN.index_digest
        harness.images.probe_absent = True
        harness.images.list_rows = [{"Repository": "mirror.example/alpine",
                                     "Tag": "3.22", "Digest": PIN.index_digest,
                                     "ID": mirror_id}]
        harness.images.list_exits_nonzero = True
        harness.images.spike_documents[mirror_reference] = probe_document(
            ["mirror.example/alpine@" + PIN.index_digest], image_id=mirror_id)
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason,
                      run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)
        self.assertIn("pull " + PIN.reference, outcome.detail)
        self.assertIsNone(outcome.probe)
        self.assertNoRecord(harness)

    def test_several_mirror_named_copies_of_the_pinned_digest_admit_through_the_sorted_first_reference(
            self):
        # more than one repository may carry the pinned digest — the same content pulled
        # through different proxies — and any of them admits; the choice must be
        # deterministic, the sorted-first repository, so the same store answers the
        # same re-inspection on every attempt: the third command names the
        # sorted-first reference, never the later-named one that happened to be
        # listed first
        mirror_id = "sha256:" + "d" * 64
        chosen = "mirror-a.example/alpine@" + PIN.index_digest
        listed_later = "mirror-b.example/alpine@" + PIN.index_digest
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.images.probe_absent = True
        harness.images.list_rows = [
            {"Repository": "mirror-b.example/alpine", "Tag": "3.22",
             "Digest": PIN.index_digest, "ID": "sha256:" + "b" * 64},
            {"Repository": "mirror-a.example/alpine", "Tag": "3.22",
             "Digest": PIN.index_digest, "ID": mirror_id},
        ]
        harness.images.spike_documents[chosen] = probe_document(
            ["mirror-a.example/alpine@" + PIN.index_digest], image_id=mirror_id)
        harness.images.spike_documents[listed_later] = probe_document(
            ["mirror-b.example/alpine@" + PIN.index_digest],
            image_id="sha256:" + "e" * 64)
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        commands = [call[0] for call in harness.images.calls]
        self.assertEqual(commands[2][-1], chosen,
                         "the sorted-first repository is re-inspected")
        self.assertEqual(outcome.record["qualification_image"]["id"], mirror_id,
                         "the re-inspected mirror's own daemon ID is the sealed one")
        self.assertEqual(outcome.record["qualification_image"]["repo_digest"],
                         "mirror-a.example/alpine@" + PIN.index_digest,
                         "the proof is the entry the re-inspected mirror reported")

    def test_a_listing_row_with_a_none_repository_is_screened_out_by_the_reference_lexeme(self):
        # A `<none>` repository sorts before
        # every real name, so a dangling row carrying the pin would otherwise end the
        # attempt on a reference no daemon can answer; the repo-digest lexeme on the
        # combined reference screens unusable names out before the sorted-first
        # choice — while a list whose only matching row is `<none>` keeps the pull
        # hint (a `<none>` digest can never match the pin by equality)
        mirror_id = "sha256:" + "d" * 64
        mirror_reference = "mirror.example/alpine@" + PIN.index_digest
        for rows, admitted in (
            ([{"Repository": "<none>", "Tag": "3.22", "Digest": PIN.index_digest,
               "ID": "sha256:" + "c" * 64},
              {"Repository": "mirror.example/alpine", "Tag": "3.22",
               "Digest": PIN.index_digest, "ID": mirror_id}], True),
            ([{"Repository": "<none>", "Tag": "3.22", "Digest": PIN.index_digest,
               "ID": "sha256:" + "c" * 64}], False),
        ):
            with self.subTest(rows=rows, admitted=admitted):
                harness = OwnerHarness(self._fresh_cell())
                harness.manifests = [{"implementation_digest": D0}] * 32
                harness.images.probe_absent = True
                harness.images.list_rows = rows
                harness.images.spike_documents[mirror_reference] = probe_document(
                    ["mirror.example/alpine@" + PIN.index_digest], image_id=mirror_id)
                outcome = harness.call()
                if admitted:
                    self.assertIs(type(outcome), run_owner.RunQualificationBound)
                    self.assertEqual(
                        outcome.record["qualification_image"]["repo_digest"],
                        "mirror.example/alpine@" + PIN.index_digest)
                else:
                    self.assertIs(type(outcome), run_owner.RunQualificationFailed)
                    self.assertIs(outcome.reason,
                                  run_owner.RunQualificationReason.PROBE_IMAGE_UNTRUSTED)
                    self.assertIn("pull " + PIN.reference, outcome.detail)
                    self.assertNoRecord(harness)

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

    def test_both_image_row_shapes_name_their_own_row_in_each_refusal(self):
        """The spike row and the probe image row share one platform check, and each refusal names
        the row it refused: the field set, the platform facts and the declared volumes."""
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        record = harness.call().record
        for validate, row, label in (
                (run_owner.validate_spike_image_row_shape, record["spike_images"][0], "spike row"),
                (run_owner.validate_qualification_image_shape, record["qualification_image"],
                 "probe image row")):
            self.assertEqual(validate(copy.deepcopy(row)), row)
            for name, changed, refusal in (
                    ("field set", {**row, "extra": 1}, "shape is unknown"),
                    ("operating system", {**row, "os": "windows"}, "platform is unknown"),
                    ("blank architecture", {**row, "architecture": ""}, "platform is unknown"),
                    ("raw architecture", {**row, "raw_architecture": 7}, "platform is unknown"),
                    ("raw operating system", {**row, "raw_os": ""}, "platform is unknown"),
                    ("volumes", {**row, "declared_volumes": ["/data"]}, "declares volumes"),
                    ("volume shape", {**row, "declared_volumes": "none"}, "declares volumes")):
                with self.subTest(row=label, change=name), self.assertRaises(ValueError) as caught:
                    validate(changed)
                self.assertEqual(str(caught.exception), f"run qualification {label} {refusal}")

    def test_sealed_spike_rows_refuse_every_non_conforming_form_at_load(self):
        # (the registered /3 schema owns the closed row shape — the field set, the
        # identity digest, the os enum and the no-volumes constant are the contract
        # conformance harness's — so the owner keeps only the row behaviour it owns:
        # the cross-field identity with the observed runtime, at the loader itself)
        # a sealed record whose row names a platform that is not the bound runtime's,
        # or one whose image declares volumes, is refused by load_record from its
        # own published state, with the closed record error, never a leaked exception
        # (through load_record, never a private validator).
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        store = run_owner.RunQualificationStore(harness.root)
        digest = outcome.record["qualification_record_digest"]
        path = store.records / (digest.removeprefix("sha256:") + ".json")
        payload = run_owner.fsio.dump_json(outcome.record)
        rows = list(outcome.record["spike_images"])
        variants = (
            ("a foreign canonical platform",
             [{**rows[0], "raw_architecture": "aarch64", "architecture": "arm64"}]),
            ("declared volumes", [{**rows[0], "declared_volumes": ["/data"]}]),
        )
        for label, mutated in variants:
            with self.subTest(row=label):
                body = dict(outcome.record)
                body["spike_images"] = mutated
                path.write_bytes(run_owner.fsio.dump_json(body))
                path.chmod(common.MODE_FILE)
                with self.assertRaises(authority.GenericQualificationError):
                    store.load_record(digest, project_id="project-1", run_id="run-1",
                                      generation=1)
                path.write_bytes(payload)
                path.chmod(common.MODE_FILE)
                self.assertEqual(store.load_record(digest, project_id="project-1",
                                                  run_id="run-1", generation=1),
                                 outcome.record)

    def test_a_record_whose_digest_proof_names_another_digest_refuses_at_load(self):
        # The proof member may name any repository
        # the daemon observed carrying the pinned content, but its digest part must
        # be the pinned index digest. Two refusals must hold: a plainly tampered
        # record is refused from its own published state with the closed record
        # error (the untouched record still loads), and a *recomputed* record —
        # one rebuilt through the production builder so every content digest
        # agrees with the forged proof — is refused by validate_run_record itself,
        # so the check is the proof's own rule, never only tamper detection
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        store = run_owner.RunQualificationStore(harness.root)
        digest = outcome.record["qualification_record_digest"]
        path = store.records / (digest.removeprefix("sha256:") + ".json")
        payload = run_owner.fsio.dump_json(outcome.record)
        model = dict(outcome.record)
        tampered = dict(model["qualification_image"])
        tampered["repo_digest"] = "mirror.example/alpine@" + "sha256:" + "e" * 64
        body = dict(model)
        body["qualification_image"] = tampered
        path.write_bytes(run_owner.fsio.dump_json(body))
        path.chmod(common.MODE_FILE)
        with self.assertRaises(authority.GenericQualificationError):
            store.load_record(digest, project_id="project-1", run_id="run-1",
                              generation=1)
        path.write_bytes(payload)
        path.chmod(common.MODE_FILE)
        self.assertEqual(store.load_record(digest, project_id="project-1",
                                            run_id="run-1", generation=1),
                         outcome.record)
        # the recomputed forgery: every digest agrees with the wrong proof, and the
        # record is still refused by its own rule
        binding = run_owner.RunBinding(**model["run_binding"])
        probes = tuple(run_owner.qualification.ProbeObservation(**probe)
                       for probe in model["probes"])

        def rebuilt(proof):
            row = dict(model["qualification_image"])
            row["repo_digest"] = proof
            return run_owner.validate_run_record(run_owner.build_run_record(
                binding, model["profile_id"], model["runtime"],
                model["implementation_digest"], row, model["spike_images"], probes,
                model["qualification_elapsed_ms"]))

        for label, proof in (
            ("another digest under the same name",
             "mirror.example/alpine@" + "sha256:" + "e" * 64),
            ("a digest of another length",
             "mirror.example/alpine@" + "e" * 64),
        ):
            with self.subTest(proof=label):
                with self.assertRaises(authority.GenericQualificationError):
                    rebuilt(proof)
        # the pinned proof loads: the observation the builder itself makes
        self.assertEqual(rebuilt(model["qualification_image"]["repo_digest"])[
            "qualification_record_digest"], model["qualification_record_digest"])

    def test_a_daemon_identity_the_authority_admits_is_still_sealed_and_bound(self):
        # The record's daemon-identifier lexeme
        # is the /2 authority's own rule — no C0/C1 control, at most 128 characters,
        # not all whitespace, interior, leading and trailing spaces included — so a
        # daemon the authority admits is never refused at sealing after five probes;
        # the supervisor cases rest at the authority's own boundary tests (its owner)
        for daemon_id in ("abc def", " leading", "trailing ", "a\u00a0b",
                          "one\ufefftwo", "x" * 128):
            with self.subTest(daemon_id=daemon_id):
                harness = OwnerHarness(self._fresh_cell())
                harness.manifests = [{"implementation_digest": D0}] * 32
                harness.inspector_facts["daemon_id"] = daemon_id
                outcome = harness.call()
                self.assertIs(type(outcome), run_owner.RunQualificationBound,
                              "the authority's admission is the record's admission")
                self.assertEqual(outcome.record["daemon_id"], daemon_id)
                loaded = run_owner.RunQualificationStore(harness.root).load_record(
                    outcome.record["qualification_record_digest"],
                    project_id="project-1", run_id="run-1", generation=1)
                self.assertEqual(loaded["daemon_id"], daemon_id)

    def test_the_record_daemon_lexeme_admits_exactly_the_authoritys_daemon_identity(self):
        # The two owners admit one rule on one
        # member, so a corpus covers their agreement — every boundary class either
        # names — and no restatement of the rule itself: both owners answer for
        # themselves, and the test holds only the equality of their verdicts
        corpus = ("abc def", " leading", "trailing ", "a\u00a0b", "one\ufefftwo",
                  "\ufeff", "\ufeff ", "x" * 128, "", " ", "  \u00a0  ", "x" * 129,
                  "bad\nidentity", "bad\x1fidentity", "bad\x7fidentity",
                  "bad\x85identity", "bad\x9fidentity", 123, True, None, ["a"])
        for identity in corpus:
            with self.subTest(identity=identity):
                admission_failed = False
                try:
                    authority.validate_runtime({**runtime(), "daemon_id": identity})
                except authority.GenericQualificationError:
                    admission_failed = True
                shape_failed = False
                try:
                    run_owner.validate_daemon_identity_shape(identity)
                except ValueError:
                    shape_failed = True
                self.assertEqual(admission_failed, shape_failed,
                                 "the record's lexeme is the authority's own rule")

    # --- stale daemon, version change, implementation drift ------------------

    def test_stale_daemon_and_version_change_map_to_the_typed_stale_reason(self):
        version = runtime()["server_version"]
        drifts = (
            ("endpoint", {}, {"endpoint": "unix:///var/run/other.sock"}),
            ("daemon-id", {"daemon_id": "the-first-daemon-id"},
             {"daemon_id": "a-restarted-daemon-id"}),
            ("capability", {}, {"swap_limit": False}),
            ("version", {}, {"server_version": "99.0.0"}),
            # within one attempt the exact version must still match: a change that
            # keeps the major part alone (operator decision, drift "Server version")
            # is the same daemon drift, never an admitted binding
            ("same-major version", {},
             {"server_version": run_owner.server_version_major(version) + ".9"}),
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

    def test_across_the_binding_only_the_server_major_version_is_bounded(self):
        # (operator decision, drift "Server version") the execution binding carries
        # only the Docker server version's major part; the full version stays a
        # diagnostic of the runtime facts, and within one attempt the exact version
        # must still match (the re-observation rule). The witness holds every other
        # fact fixed around one real record — the production builder recomputes the
        # binding from only the varied runtime — so the minor part is observed to
        # leave the bound facts and the major part to change them, without restating
        # the binding composition.
        version = runtime()["server_version"]
        major = run_owner.server_version_major(version)
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.inspector_facts["server_version"] = major + ".5.1"
        model = dict(harness.call().record)
        self.assertEqual(model["runtime"]["server_version"],
                         harness.inspector_facts["server_version"])

        binding = run_owner.RunBinding(**model["run_binding"])
        probes = tuple(run_owner.qualification.ProbeObservation(**probe)
                       for probe in model["probes"])

        def rebuilt(server_version):
            facts = copy.deepcopy(harness.inspector_facts)
            facts["server_version"] = server_version
            return run_owner.build_run_record(
                binding, model["profile_id"], facts, model["implementation_digest"],
                model["qualification_image"], model["spike_images"], probes,
                model["qualification_elapsed_ms"])

        base = rebuilt(major + ".5.1")
        minor_update = rebuilt(major + ".9")
        self.assertEqual(base["execution_binding_digest"],
                         minor_update["execution_binding_digest"],
                         "a minor daemon update keeps the bound facts")
        self.assertNotEqual(base["qualification_record_digest"],
                            minor_update["qualification_record_digest"],
                            "a changed diagnostic changes the record")
        self.assertEqual(minor_update["runtime"]["server_version"], major + ".9",
                         "the full version stays a diagnostic of the runtime")
        for record in (base, minor_update):
            self.assertEqual(run_owner.server_version_major(record["runtime"]["server_version"]),
                             major)
        # the real-world daemon spellings stay literal here: a vendor-suffixed and a pre-release version keep the same
        # major binding, and a same-major pair that differs only in the suffix never
        # rebinds — the derived forms alone would let a mutant that requires the whole
        # string numeric-dotted pass. A leading zero in the major is normalized away
        # before the binding ever sees it: the invariant is numeric,
        # never textual.
        self.assertEqual(run_owner.server_version_major("0" + major + ".7"), major,
                         "the major is the normalized integer, never the literal text")
        for spelling in (major + ".5.1-desktop", major + ".0.0-rc.1",
                         "0" + major + ".5.1"):
            with self.subTest(spelling=spelling):
                update = rebuilt(spelling)
                self.assertEqual(base["execution_binding_digest"],
                                 update["execution_binding_digest"],
                                 "a same-major spelling keeps the bound facts")
                self.assertNotEqual(base["qualification_record_digest"],
                                    update["qualification_record_digest"],
                                    "a changed diagnostic changes the record")
                self.assertEqual(run_owner.server_version_major(spelling), major,
                                 "the real spelling still names its major")
        for one, other in ((major + ".0.0-rc.1", major + ".0.0"),
                           (major + ".5.1-desktop", major + ".5.1")):
            with self.subTest(suffix_pair=(one, other)):
                self.assertEqual(rebuilt(one)["execution_binding_digest"],
                                 rebuilt(other)["execution_binding_digest"],
                                 "a suffix change alone never rebinds")

        major_update = rebuilt(str(int(major) + 1) + ".0")
        self.assertNotEqual(major_update["execution_binding_digest"],
                            base["execution_binding_digest"],
                            "a major daemon change is a different execution binding")

    def test_a_server_version_without_a_derivable_major_binds_nothing(self):
        # fail closed: a daemon whose version carries no numeric major part — a private
        # build, a garbage answer — can seal no execution binding, and the attempt
        # ends in a capability refusal with nothing published
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.inspector_facts["server_version"] = "emu-not-a-number"
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.CAPABILITY_UNAVAILABLE)
        self.assertIsNone(outcome.probe)
        self.assertNoRecord(harness)

    def test_a_version_without_a_derivable_major_is_refused_at_the_initial_admission(self):
        # The major is derived once at the facts'
        # admission, before the trusted probe image is ever inspected: a `dev` daemon
        # ends the attempt with the capability reason at the admission itself — never
        # after a whole probe run — and no Docker image command has run at all
        harness = OwnerHarness(self._fresh_cell())
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.inspector_facts["server_version"] = "dev"
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.CAPABILITY_UNAVAILABLE)
        self.assertIsNone(outcome.probe)
        self.assertEqual(harness.images.calls, [],
                         "the refusal precedes every Docker image command")
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


    def test_a_now_refusing_pinned_daemon_recovers_before_it_refuses_production(self):
        # Through the production inspector: the first attempt
        # retains a journal; the same pinned daemon then stops admitting (SwapLimit
        # false); the owner's non-admitting observation still yields facts, recovery
        # runs at the pinned endpoint, and only then the capability refusal is reported.
        cell = self._fresh_cell()
        facts = runtime()
        runner = DaemonFixtureRunner(facts)
        arrivals = []

        def discovery(seed, *, deadline, clock=None, **kwargs):
            return authority.inspect_observed_runtime(
                seed, deadline=deadline, runner=runner,
                clock=clock if clock is not None else harness.clock,
                host_facts=(facts["host_os"], facts["host_raw_architecture"]))

        harness = OwnerHarness(cell)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.effects_options["realization"] = {"crash_after": 5}
        original_effects = harness.effects_factory

        def recording_effects(resolved, operation, name):
            arrivals.append((name, dict(resolved.environment),
                             resolved.runtime.get("endpoint")))
            return original_effects(resolved, operation, name)

        harness.effects_factory = recording_effects
        harness.runtime_inspector = discovery
        crashed = harness.call()
        self.assertIs(type(crashed), run_owner.RunQualificationFailed)
        self.assertIs(crashed.reason, run_owner.RunQualificationReason.CLEANUP_UNAVAILABLE)
        self.assertEqual(len(generic_journal.GenericJournalStore(
            common.ExecutionStore(cell)).scan()), 1)

        # the same pinned daemon now refuses the resource-limit capability
        runner.facts["swap_limit"] = False

        fresh = OwnerHarness(cell)
        fresh.manifests = [{"implementation_digest": D0}] * 32
        fresh.runtime_inspector = discovery
        fresh_effects = fresh.effects_factory

        def recovery_recording(resolved, operation, name):
            arrivals.append((name, dict(resolved.environment),
                             resolved.runtime.get("endpoint")))
            return fresh_effects(resolved, operation, name)

        fresh.effects_factory = recovery_recording
        outcome = fresh.call()
        # the refusal keeps its own kept reason... after recovery ran first
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason,
                      run_owner.RunQualificationReason.RESOURCE_LIMITS_UNAVAILABLE)
        self.assertEqual(generic_journal.GenericJournalStore(
            common.ExecutionStore(cell)).scan(),
            (), "the retained journal was recovered to the initialized state first")
        self.assertNoRecord(fresh)
        # the initialized-state and command-destination assertions: recovery ran at the
        # endpoint the refusing daemon was observed at, pinned, under the config
        recovery = [row for row in arrivals if row[0] == "recovery"]
        self.assertEqual(len(recovery), 2)   # one recovery per attempt, pinned each time
        for name, environment, endpoint in recovery:
            self.assertEqual(endpoint, facts["endpoint"])
            self.assertEqual(environment["DOCKER_HOST"], facts["endpoint"])
        self.assertEqual({row[1]["DOCKER_CONFIG"] for row in recovery},
                         {str(cell / run_owner.NAMESPACE / "configs"
                              / spike_plan.ISOLATED_PROFILE)})
        # and every daemon read of both observations that resolved an endpoint was
        # pinned to it in the launched commands
        for argv, environment in runner.calls:
            if argv[1:2] == ("version",) or argv[1:2] == ("info",):
                self.assertEqual(environment["DOCKER_HOST"], facts["endpoint"])
        self.assertEqual([argv for argv, _ in runner.calls[:2]
                          if argv[1] == "version"], [])


    def test_the_final_reobservation_separates_drift_result_failure_and_launch_failure(self):
        # At the owner boundary: a changed capability observed by
        # the final pinned re-observation is stale-daemon; a failed daemon command
        # result is unreachable; a command-launch OSError — the hole — is normalized at
        # the observation boundary and arrives unreachable, never capability.
        for mode, expected in (
            ("drift", run_owner.RunQualificationReason.STALE_DAEMON),
            ("result", run_owner.RunQualificationReason.DOCKER_UNREACHABLE),
            ("launch", run_owner.RunQualificationReason.DOCKER_UNREACHABLE),
        ):
            with self.subTest(final_failure=mode):
                cell = self._fresh_cell()
                facts = runtime()
                # the failure lands on the final re-observation only: the five kernel
                # postflights pass untouched (PROBE_COUNT observations), then the final
                # observation changes
                runner = DaemonFixtureRunner(
                    facts, fail_mode=mode, fail_observation=PROBE_COUNT + 1)

                def reobserver(recorded, *, deadline, **kwargs):
                    return authority.inspect_pinned_runtime(
                        recorded, deadline=deadline, runner=runner, clock=fresh.clock,
                        host_facts=(facts["host_os"], facts["host_raw_architecture"]))

                fresh = OwnerHarness(cell)
                fresh.manifests = [{"implementation_digest": D0}] * 32
                fresh.pinned_reobserver = reobserver
                outcome = fresh.call()
                self.assertIs(type(outcome), run_owner.RunQualificationFailed)
                self.assertIs(outcome.reason, expected)
                self.assertIsNot(outcome.reason,
                                 run_owner.RunQualificationReason.CAPABILITY_UNAVAILABLE)
                self.assertIsNone(outcome.probe)
                self.assertNoRecord(fresh)
                self.assertEqual(runner.observations_started, PROBE_COUNT + 1)

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

    def test_the_second_first_use_attempt_never_replaces_the_inode_the_first_holds(self):
        # Both attempts' lock lookups are the initializer's own real `lstat` calls, and
        # both genuinely miss — the barrier runs inside the actual FileNotFoundError
        # handler, before either initializer creates. The second attempt's *missed*
        # lookup is released only after the first attempt holds the published inode, so
        # its publication happens against a held lock. The exclusive create then loses
        # (FileExistsError) and adopts the held inode; a lookup-then-replace writer
        # unlinks it instead, and the assertions fail.
        cell = self._fresh_cell()
        store = common.ExecutionStore(cell)
        for path in (store.home, store.runtime, store.staging):
            common.safe_directory(path, create=True)
        lock_path = store.runtime_lock
        self.assertFalse(lock_path.exists())   # first use: both lookups can miss
        barrier = threading.Barrier(2)
        first_locked = threading.Event()
        first_done = threading.Event()
        state = threading.local()
        entries: list[int] = []
        errors: list[BaseException] = []
        original = type(lock_path).lstat

        def observed_lstat(path):
            # the hook runs the real lookup and observes its result; only a genuine
            # miss on the lock name is synchronized — inside the actual
            # FileNotFoundError handler — and the second's continuation is delivered
            # once the first attempt holds the published inode (a real miss, never a
            # fabricated one)
            try:
                return original(path)
            except FileNotFoundError:
                if Path(path) == lock_path and getattr(state, "first_lookup", False):
                    state.first_lookup = False
                    barrier.wait(timeout=10.0)   # both genuine misses are synchronized
                    if state.index == 1 and not first_locked.wait(timeout=10.0):
                        raise AssertionError("the first attempt never held the lock")
                raise

        def attempt(index):
            state.index = index
            state.first_lookup = True
            try:
                # the real production initializer, lookup included
                store.prepare_execution_scaffolding()
                if index == 1:
                    # the lost race adopted the winner: the held inode is the name's inode
                    self.assertEqual(lock_path.stat().st_ino, entries[0])
                    return
                with common.runtime_lock(store, time.monotonic() + 30.0):
                    entries.append(lock_path.stat().st_ino)
                    descriptor = os.open(lock_path, os.O_RDONLY)
                    try:
                        first_locked.set()
                        held = os.fstat(descriptor)
                        # while the second attempt published against this held lock, the
                        # held inode was neither replaced nor unlinked away
                        self.assertEqual(held.st_nlink, 1,
                                         "the second attempt replaced the held lock inode")
                    finally:
                        os.close(descriptor)
                    if not first_done.wait(timeout=10.0):
                        raise AssertionError("the second attempt never finished its initialization")
            except BaseException as exc:
                errors.append(exc)
            finally:
                if index == 1:
                    first_done.set()

        with mock.patch.object(type(lock_path), "lstat", observed_lstat):
            workers = [threading.Thread(target=attempt, args=(index,)) for index in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=30.0)
        self.assertFalse(any(worker.is_alive() for worker in workers),
                         "a first-use attempt failed but never ended")
        self.assertEqual(errors, [])
        self.assertEqual(lock_path.stat().st_ino, entries[0],
                         "the name still addresses the inode the first attempt locked")
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
        # The check is injected below the concrete effects
        # factory - at the real command-construction boundary - instead of replacing it,
        # so the environment the produced commands actually launch with is verified.
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

        def recording_construction(cli, environment, work):
            seen.append((dict(environment), Path(work), str(cli)))
            return QuietEffects()

        with mock.patch.object(run_owner.execution, "concrete_effects",
                               recording_construction):
            outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        self.assertEqual(len(seen), 1)   # the recovery stage went through the default
        facts = runtime()
        environment, work, cli = seen[0]
        self.assertEqual(environment, {
            "LANG": "C", "LC_ALL": "C", "DOCKER_HOST": facts["endpoint"],
            "DOCKER_CONFIG": str(harness.root / run_owner.NAMESPACE / "configs"
                                 / spike_plan.ISOLATED_PROFILE)})
        self.assertEqual(work, harness.root / run_owner.NAMESPACE / "work")
        self.assertEqual(cli, facts["docker_cli"])

    def test_the_default_recovery_effects_launch_commands_at_the_pinned_destination(self):
        # The destination witness below the concrete
        # effects implementation: with the production default effects factory, the
        # commands the real ConcreteGenericEffects launches — not the objects it is
        # constructed with — carry the pinned Docker CLI, the pinned endpoint and the
        # private config directory. The runner is injected under the effects (the
        # ConcreteGenericEffects process boundary); the recording construction test above
        # watches the constructor, and this one watches the actual launched commands.
        cell = self._fresh_cell()
        harness = OwnerHarness(cell)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.effects_options["realization"] = {"crash_after": 5}
        crashed = harness.call()
        self.assertIs(type(crashed), run_owner.RunQualificationFailed)
        self.assertIs(crashed.reason, run_owner.RunQualificationReason.CLEANUP_UNAVAILABLE)
        rows = generic_journal.GenericJournalStore(common.ExecutionStore(cell)).scan()
        self.assertEqual(len(rows), 1, "a retained journal gives the recovery real work")

        facts = runtime()
        runner = DestinationRecordingRunner(rows[0].operation)
        fresh = OwnerHarness(cell)
        fresh.manifests = [{"implementation_digest": D0}] * 32
        fresh.attempt_id = "a-2"
        fresh.kernel_factory = FakeKernelFactory()   # the probes own no docker effects
        fresh.effects_factory = None                  # the production default: _concrete_effects
        with mock.patch.object(process, "BoundedRunner", mock.Mock(return_value=runner)):
            outcome = fresh.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        self.assertEqual(generic_journal.GenericJournalStore(
            common.ExecutionStore(cell)).scan(), (),
            "the real recovery finished the retained journal and emptied the store")
        self.assertTrue(runner.destinations,
                        "the recovery launched real docker commands through the concrete effects")
        config = cell / run_owner.NAMESPACE / "configs" / spike_plan.ISOLATED_PROFILE
        for argv, environment in runner.destinations:
            self.assertEqual(argv[0], facts["docker_cli"],
                             "the pinned CLI prefixes every launched command")
            self.assertEqual(environment, {
                "LANG": "C", "LC_ALL": "C", "DOCKER_HOST": facts["endpoint"],
                "DOCKER_CONFIG": str(config)})
            self.assertIn(argv[1], {"container", "network"},
                          "recovery only reads and removes the journal's own resources")
        inspected = {argv[-1] for argv, _ in runner.destinations
                     if argv[1:3] == ("container", "inspect")}
        self.assertEqual(inspected, {rows[0].container_id},
                         "the recovery proved the journal's exact container name")

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
            "schema": LEGACY_POINTER_SCHEMA,
            "profile_id": spike_plan.ISOLATED_PROFILE,
            "qualification_record_digest": legacy["qualification_record_digest"],
            "execution_binding_digest": legacy["execution_binding_digest"],
        }
        pointer_path = legacy_pointers / (spike_plan.ISOLATED_PROFILE + ".json")
        pointer_path.write_bytes(run_owner.fsio.dump_json(pointer))
        pointer_path.chmod(common.MODE_FILE)
        # the planted record is genuinely readable by the /2 store
        self.assertEqual(
            authority.GenericQualificationStore(self.root).load_record(
                legacy["qualification_record_digest"]), legacy)

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
            authority.GenericQualificationStore(self.root).load_record(
                legacy["qualification_record_digest"])
        fresh = OwnerHarness(self.root)
        fresh.manifests = [{"implementation_digest": D0}] * 32
        fresh.attempt_id = "a-2"
        second = fresh.call()
        self.assertIs(type(second), run_owner.RunQualificationBound)
        self.assertEqual(len(list(self.store.records.iterdir())), 2)

    def test_changed_bodies_and_wrong_digest_paths_in_the_v3_tree_are_refused(self):
        # The same load verification witnesses now planted
        # in the version-three tree: a body changed at its own digest name, and a
        # valid body planted at a wrong digest path, are both refused by the /3 loader.
        cell = self._fresh_cell()
        harness = OwnerHarness(cell)
        harness.manifests = [{"implementation_digest": D0}] * 32
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        store = run_owner.RunQualificationStore(cell)
        digest = outcome.record["qualification_record_digest"]
        payload = run_owner.fsio.dump_json(outcome.record)
        path = store.records / (digest.removeprefix("sha256:") + ".json")
        self.assertEqual(common.safe_file(path, cap=run_owner.MAX_RECORD_BYTES), payload)

        # a changed body at its own digest name: the content no longer names the bytes
        changed = dict(outcome.record)
        changed["qualification_elapsed_ms"] = changed["qualification_elapsed_ms"] + 1
        path.write_bytes(run_owner.fsio.dump_json(changed))
        path.chmod(common.MODE_FILE)
        with self.assertRaises(authority.GenericQualificationError):
            store.load_record(digest, project_id="project-1", run_id="run-1",
                              generation=1)
        # restore the original bytes
        path.write_bytes(payload)
        path.chmod(common.MODE_FILE)
        self.assertEqual(store.load_record(digest, project_id="project-1",
                                          run_id="run-1", generation=1),
                         outcome.record)

        # a valid body planted at a wrong digest path: the path differs from the content
        wrong = D1.replace("sha256:", "sha256:")
        self.assertNotEqual(wrong, digest)
        wrong_path = store.records / (wrong.removeprefix("sha256:") + ".json")
        wrong_path.write_bytes(payload)
        wrong_path.chmod(common.MODE_FILE)
        with self.assertRaises(authority.GenericQualificationError):
            store.load_record(wrong, project_id="project-1", run_id="run-1",
                              generation=1)
        self.assertEqual(len(list(store.records.iterdir())), 2)

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
        # write: the directory is validated on load exactly as on write. The redirected
        # directory holds the exact published record, so only the directory guard can
        # refuse it - a removed guard would load it.
        foreign = self._fresh_cell() / "foreign-records"
        foreign.mkdir(mode=0o700)
        foreign.chmod(common.MODE_DIR)
        exact = run_owner.fsio.dump_json(first.record)
        (foreign / (first.record_digest.removeprefix("sha256:") + ".json")).write_bytes(exact)
        (foreign / (first.record_digest.removeprefix("sha256:") + ".json")).chmod(
            common.MODE_FILE)
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


class SettleInterruptedAttemptTests(IsolatedSandboxHomeTestCase):
    """Settling the open row of a dead attempt (spec §5): the shared runtime lock
    is taken without waiting, prior journals are recovered by the attempt's own recovery owner
    under it, and the transaction's closing runs still under it."""

    def setUp(self):
        super().setUp()
        import tempfile
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = (Path(self.temporary.name) / "sandbox").resolve()
        self.root.mkdir(mode=0o700, parents=True)
        self.root.chmod(0o700)
        self.harness = OwnerHarness(self.root)
        self.recoveries = []
        original = self.harness.effects_factory

        def recording(resolved, operation, name):
            self.recoveries.append(name)
            return original(resolved, operation, name)

        self.harness.effects_factory = recording

    def settle(self, on_settled, **overrides):
        """Settle under the lease the caller holds, as the transaction does."""
        options = self.harness.options()
        arguments = {
            "project_id": "project-1", "run_id": "run-1", "generation": 1,
            "attempt_id": self.harness.attempt_id, "profile_id": self.harness.profile_id,
            "docker_cli": self.harness.docker_cli, "on_settled": on_settled,
            "runtime_inspector": options["runtime_inspector"],
            "effects_factory": options["effects_factory"], "clock": options["clock"],
        }
        arguments.update(overrides)
        if "lease" in arguments:
            return run_owner.settle_interrupted_attempt(**arguments)
        with run_owner.hold_runtime_lock(sandbox_root=self.root, clock=options["clock"]) as lease:
            return run_owner.settle_interrupted_attempt(lease=lease, **arguments)

    def test_a_free_lock_recovers_journals_and_runs_the_closing_under_the_lock(self):
        held_during = []

        def closing():
            store = common.ExecutionStore(self.root)
            try:
                with common.runtime_lock(store, time.monotonic() + 1.0, blocking=False):
                    held_during.append(False)
            except common.RuntimeLockBusy:
                held_during.append(True)

        outcome = self.settle(closing)
        self.assertIs(outcome, True)
        self.assertEqual(held_during, [True], "the closing runs while the settle holds the lock")
        self.assertEqual(self.recoveries, ["recovery"], "prior journals are recovered first")
        self.assertEqual(self.harness.inspector_calls, 1)
        self.assertNoRecordPublished()

    def test_a_lock_another_holder_has_is_busy_at_once(self):
        store = common.ExecutionStore(self.root)
        store.prepare_execution_scaffolding()
        with common.runtime_lock(store, time.monotonic() + 5.0):
            with self.assertRaises(common.RuntimeLockBusy):
                with run_owner.hold_runtime_lock(sandbox_root=self.root):
                    self.fail("the lease is never granted over a held lock")
        with run_owner.hold_runtime_lock(sandbox_root=self.root):
            pass

    def test_a_lease_that_is_not_held_is_refused_as_invalid_input(self):
        with run_owner.hold_runtime_lock(sandbox_root=self.root) as lease:
            pass
        closed = []
        for stale in (lease, None):
            outcome = self.settle(lambda: closed.append(True), lease=stale)
            self.assertIs(type(outcome), run_owner.RunQualificationFailed)
            self.assertIs(outcome.reason, run_owner.RunQualificationReason.INVALID_INPUT)
        self.assertEqual((closed, self.recoveries), ([], []))

    def test_an_attempt_under_a_held_lease_runs_without_taking_the_lock_again(self):
        """The lease is the caller's hold on the one lock: the attempt runs under it (a second
        acquisition on another descriptor would block against the holder itself), and a lease
        that is no longer held is refused before any effect."""
        with run_owner.hold_runtime_lock(sandbox_root=self.root) as lease:
            outcome = self.harness.call(lease=lease)
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        stale = self.harness.call(lease=lease)
        self.assertIs(type(stale), run_owner.RunQualificationFailed)
        self.assertIs(stale.reason, run_owner.RunQualificationReason.INVALID_INPUT)

    def test_a_settle_spends_only_what_is_left_of_the_lease_deadline(self):
        """The lease carries the one bound of a qualify: time already spent under it is not given
        back to the settle, so a settle that starts past the bound ends as the deadline failure
        without recovering or closing the row (the settle's own budget alone would still have had room)."""
        bound = run_owner.DEFAULT_QUALIFICATION_SECONDS
        clock = self.harness.clock
        closed = []
        with run_owner.hold_runtime_lock(sandbox_root=self.root, clock=clock) as lease:
            clock.advance(bound * 1.1)
            outcome = self.settle(lambda: closed.append(True), lease=lease)
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertEqual((closed, self.recoveries), ([], []))

    def test_a_settle_whose_closing_lands_past_the_bound_answers_the_deadline_failure(self):
        """The closing is durable and runs, but a settle that leaves no time must not let the
        caller reserve another attempt: it answers the deadline failure, not success."""
        bound = run_owner.DEFAULT_QUALIFICATION_SECONDS
        clock = self.harness.clock
        closed = []
        outcome = self.settle(lambda: (clock.advance(bound * 1.1), closed.append(True)))
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.DEADLINE_EXCEEDED)
        self.assertEqual(closed, [True])

    def test_the_budget_has_one_ingress_bound_for_the_attempt_and_the_settle(self):
        """A budget is a positive number of seconds no larger than the qualify bound, and never a
        boolean or text; the attempt and the settle refuse the same values before any effect."""
        bound = run_owner.DEFAULT_QUALIFICATION_SECONDS
        refused = (0, -1, bound + 0.001, True, "5", float("nan"))
        closed = []
        for value in refused:
            with self.subTest(value=value):
                settled = self.settle(lambda: closed.append(True), qualification_seconds=value)
                attempted = self.harness.call(qualification_seconds=value)
                for outcome in (settled, attempted):
                    self.assertIs(type(outcome), run_owner.RunQualificationFailed)
                    self.assertIs(outcome.reason, run_owner.RunQualificationReason.INVALID_INPUT)
        self.assertEqual((closed, self.recoveries, self.harness.inspector_calls), ([], [], 0))
        for value in (None, bound, 0.5):
            with self.subTest(accepted=value):
                self.assertIs(self.settle(lambda: None, qualification_seconds=value), True)

    def test_a_recovery_that_cannot_run_returns_its_closed_failure_and_never_closes(self):
        closed = []

        def unreachable(recorded, *, deadline, **kwargs):
            raise authority.GenericQualificationError("the daemon cannot be reached")

        outcome = self.settle(lambda: closed.append(True), runtime_inspector=unreachable)
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIsNot(outcome.reason, run_owner.RunQualificationReason.INVALID_INPUT)
        self.assertEqual(closed, [])

    def test_an_unclosed_binding_is_refused_as_invalid_input_before_any_lock(self):
        closed = []
        outcome = self.settle(lambda: closed.append(True), attempt_id="")
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.INVALID_INPUT)
        self.assertEqual((closed, self.recoveries), ([], []))

    def assertNoRecordPublished(self):
        records = run_owner.RunQualificationStore(self.root).records
        self.assertFalse(records.exists() and any(records.iterdir()),
                         "settling a dead attempt publishes no record")


class OutcomeAndStoreGuardTests(IsolatedSandboxHomeTestCase):
    """Typed-outcome and store guards the owner depends on: behaviour, not shape."""

    def setUp(self) -> None:
        super().setUp()

    def test_a_kernel_failure_before_a_probe_keeps_its_own_reason_and_no_probe_name(self):
        # The boundary never relabels a kernel failure that
        # did not own a probe name as an unnamed probe failure: the owning reason
        # survives the outcome with no probe identity. The construction-time invariant
        # (a probe-failed outcome requires its owner-supplied name) is what makes the
        # relabelling impossible in the first place.
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = (Path(temporary.name) / "sandbox").resolve()
        root.mkdir(mode=0o700)
        common._loaded_release = None
        common._loaded_release_failed = None
        harness = OwnerHarness(root)
        harness.manifests = [{"implementation_digest": D0}] * 32

        def wrapper(kernel):
            def refusing(name, expected, *, deadline):
                raise run_owner.qualification.GenericQualificationFailure(
                    run_owner.qualification.QualificationReason.CAPABILITY_UNAVAILABLE,
                    "the kernel refused before any probe owned a name")
            kernel.run = refusing
            return kernel

        harness.kernel_wrapper = wrapper
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.CAPABILITY_UNAVAILABLE)
        self.assertIsNot(outcome.reason, run_owner.RunQualificationReason.PROBE_FAILED)
        self.assertIsNone(outcome.probe)
        # a rejected attempt publishes nothing: the run binds no record
        self.assertFalse(any(run_owner.RunQualificationStore(root).records.iterdir()))

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
        with mock.patch.object(run_owner.fsio, "sync_directory",
                               wraps=run_owner.fsio.sync_directory) as sync:
            second = store.write_record(outcome.record)
        self.assertEqual(first, second)
        self.assertEqual(second.stat().st_ino, inode)   # no replacement on the retry
        self.assertEqual(len(list(store.records.iterdir())), 1)
        # the retry re-proves the whole naming chain: the
        # records directory naming the file, the namespace naming records, and the
        # sandbox directory naming the namespace - all strictly synced again
        synced = sorted(Path(call.args[0]) for call in sync.call_args_list)
        self.assertEqual(synced, sorted([store.records, store.root, store.sandbox_root]))
        self.assertEqual(len(synced), len(run_owner._naming_levels(store)),
                         "every naming level was synced exactly once")

        # existing bytes that differ are refused without being overwritten
        tampered = b"{\"tampered\": true}"
        first.write_bytes(tampered)
        first.chmod(common.MODE_FILE)
        with self.assertRaises(Exception):
            store.write_record(outcome.record)
        self.assertEqual(common.safe_file(first, cap=run_owner.MAX_RECORD_BYTES), tampered)

    def test_a_sync_failure_at_any_naming_level_withholds_publication(self):
        # The durable barrier covers the required parent chain
        # pre-created by the scaffold: the sandbox directory naming the namespace, the
        # namespace naming records, and records naming the record file. An injected
        # strict-sync failure at any of the three levels fails publication on the retry
        # and on a first publication alike.
        for label in ("sandbox_root", "namespace", "records"):
            with self.subTest(naming_level=label):
                import tempfile
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                root = (Path(temporary.name) / "sandbox").resolve()
                root.mkdir(mode=0o700)
                common._loaded_release = None
                common._loaded_release_failed = None
                publisher = OwnerHarness(root)
                publisher.manifests = [{"implementation_digest": D0}] * 32
                published = publisher.call()   # one record with strict syncs intact
                self.assertIs(type(published), run_owner.RunQualificationBound)
                store = run_owner.RunQualificationStore(root)
                level = {"sandbox_root": store.sandbox_root,
                         "namespace": store.root,
                         "records": store.records}[label]

                def refused(directory, level=level, label=label):
                    if Path(directory) == level:
                        raise OSError("directory sync refused at " + label)
                    return None

                # the identical-bytes retry also re-proves the whole chain: a failing
                # sync at any level withholds the publication even for bytes on disk
                with mock.patch.object(run_owner.fsio, "sync_directory",
                                       side_effect=refused):
                    with self.assertRaises(OSError):
                        store.write_record(dict(published.record))
                # and a first publication whose chain cannot be proven durable is
                # never reported bound, whatever wrote the bytes first: the intact
                # record stays, and the unproven one stays an inert orphan
                second = OwnerHarness(root)
                second.manifests = [{"implementation_digest": D0}] * 32
                second.attempt_id = "a-" + label
                with mock.patch.object(run_owner.fsio, "sync_directory",
                                       side_effect=refused):
                    failed = second.call()
                self.assertIs(type(failed), run_owner.RunQualificationFailed)
                self.assertIs(failed.reason, run_owner.RunQualificationReason.SANDBOX_UNSAFE)
                remaining = {item.stem for item in store.records.iterdir()}
                self.assertIn(published.record_digest.removeprefix("sha256:"), remaining)
                orphans = remaining - {published.record_digest.removeprefix("sha256:")}
                self.assertEqual(len(orphans), 1,
                                 "the unproven publication stays an inert orphan")
                # the intact record still loads; the orphan is loadable only through its
                # own exact digest and lineage, so nothing binds on its behalf
                intact = store.load_record(published.record_digest, project_id="project-1",
                                          run_id="run-1", generation=1)
                self.assertEqual(intact["run_binding"]["attempt_id"], "a-1")
                orphan_digest = "sha256:" + orphans.pop()
                orphan_record = store.load_record(orphan_digest, project_id="project-1",
                                                  run_id="run-1", generation=1)
                self.assertEqual(orphan_record["run_binding"]["attempt_id"], "a-" + label)

    def test_the_store_creates_missing_naming_levels_to_publish(self):
        # (mutation survivor: `create=True` on the write path's level pre-check) an
        # unscaffolded store creates and strictly syncs every missing level of its own
        # naming chain for a publication; a create=False mutant only worked because the
        # owner's scaffolding had pre-created the levels for every other test.
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cell = (Path(temporary.name) / "sandbox").resolve()
        cell.mkdir(mode=0o700)
        harness = OwnerHarness(cell)
        harness.manifests = [{"implementation_digest": D0}] * 32
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)

        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        bare = (Path(temporary.name) / "sandbox").resolve()
        store = run_owner.RunQualificationStore(bare)
        for level in run_owner._naming_levels(store):
            self.assertFalse(level.exists(),
                             "the bare store starts without any naming level")
        store.write_record(dict(outcome.record))
        for level in run_owner._naming_levels(store):
            self.assertTrue(level.exists(), "publication created " + level.name)
        self.assertEqual(store.load_record(outcome.record_digest, project_id="project-1",
                                           run_id="run-1", generation=1),
                         outcome.record)

    def test_the_publication_seal_never_follows_a_replaced_symbolic_link(self):
        # (mutation survivors: the publication validation's `path.exists` `or` islink, and
        # the read-back itself) the freshly renamed publication is validated without
        # following a symbolic link swapped in after the durable write: it is refused,
        # and the swapped link's target keeps its own mode — no permission is ever
        # changed through the name (the closed mode was already fixed on the exclusive
        # temporary descriptor before the rename).
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cell = (Path(temporary.name) / "sandbox").resolve()
        cell.mkdir(mode=0o700)
        harness = OwnerHarness(cell)
        harness.manifests = [{"implementation_digest": D0}] * 32
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        store = run_owner.RunQualificationStore(harness.root)

        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        victim = (Path(temporary.name) / "victim").resolve()
        victim.mkdir(mode=0o755)
        victim.chmod(0o755)
        different = dict(outcome.record)
        different["qualification_elapsed_ms"] = different["qualification_elapsed_ms"] + 7
        body = {key: item for key, item in different.items()
                if key != "qualification_record_digest"}
        different["qualification_record_digest"] = run_owner.canonical_digest(body)
        path = store.records / (different["qualification_record_digest"]
                                .removeprefix("sha256:") + ".json")
        self.assertFalse(path.exists())
        original_write = run_owner.fsio.atomic_write

        def swapped_write(target, data, *, durable=False):
            original_write(target, data, durable=durable)
            # the attacker wins the race window after the durable write
            target.unlink()
            target.symlink_to(victim, target_is_directory=True)

        with mock.patch.object(run_owner.fsio, "atomic_write", swapped_write):
            # an unsafe publication is the scaffolding refusal, never a capability one
            with self.assertRaises(common.GenericScaffoldingError):
                store.write_record(different)
        import stat as stat_module
        self.assertEqual(
            stat_module.S_IMODE(victim.lstat().st_mode), 0o755,
            "the link target was never chmod'ed through the swapped name")
        self.assertTrue(path.is_symlink(), "the attacker's swap is still in place")
        with self.assertRaises((authority.GenericQualificationError,
                                common.GenericScaffoldingError)):
            store.load_record(different["qualification_record_digest"],
                              project_id="project-1", run_id="run-1", generation=1)

    def test_a_hardlinked_impostor_on_the_fresh_publication_is_refused_unmutated(self):
        # There is no permission change after the rename at
        # all: the closed mode was fixed on the exclusive temporary descriptor before
        # the sync and rename (fsio.atomic_write), and the published entry is validated
        # without ever being mutated. A hard link to an unrelated 0644 file swapped onto
        # the fresh publication is refused by the identity admission — O_NOFOLLOW
        # cannot stop a hard link; only the absent chmod can keep it harmless.
        import stat as stat_module
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cell = (Path(temporary.name) / "sandbox").resolve()
        cell.mkdir(mode=0o700)
        harness = OwnerHarness(cell)
        harness.manifests = [{"implementation_digest": D0}] * 32
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        store = run_owner.RunQualificationStore(cell)

        neighbor = tempfile.TemporaryDirectory()
        self.addCleanup(neighbor.cleanup)
        unrelated = (Path(neighbor.name) / "unrelated-0644").resolve()
        unrelated.write_bytes(b"someone else's data")
        unrelated.chmod(0o644)
        different = dict(outcome.record)
        different["qualification_elapsed_ms"] = different["qualification_elapsed_ms"] + 7
        body = {key: item for key, item in different.items()
                if key != "qualification_record_digest"}
        different["qualification_record_digest"] = run_owner.canonical_digest(body)
        path = store.records / (different["qualification_record_digest"]
                                .removeprefix("sha256:") + ".json")
        self.assertFalse(path.exists())
        original_write = run_owner.fsio.atomic_write

        def swapped_write(target, data, *, durable=False):
            original_write(target, data, durable=durable)
            # the attacker wins the race window after the durable write, with a hard
            # link — the one swap a no-follow open cannot refuse by itself
            target.unlink()
            os.link(unrelated, target)

        with mock.patch.object(run_owner.fsio, "atomic_write", swapped_write):
            # an unsafe publication is the scaffolding refusal, never a capability one
            with self.assertRaises(common.GenericScaffoldingError):
                store.write_record(different)
        self.assertEqual(stat_module.S_IMODE(unrelated.stat().st_mode), 0o644,
                         "the publication validates without mutating: an unrelated "
                         "hard-linked file keeps its own mode")
        self.assertEqual(path.stat().st_ino, unrelated.stat().st_ino)
        with self.assertRaises((authority.GenericQualificationError,
                                common.GenericScaffoldingError)):
            store.load_record(different["qualification_record_digest"],
                              project_id="project-1", run_id="run-1", generation=1)

    def test_an_unsafe_publication_is_reported_as_sandbox_scaffolding_unsafe(self):
        # A substituted publication is a scaffolding
        # refusal, never a capability one: the content-addressed name was never
        # proven to hold this attempt's record, so nothing binds, the attempt's only
        # outcome is the unsafe-publication reason, and the impostor is left exactly
        # as the attacker placed it.
        import shutil
        import stat as stat_module
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cell = (Path(temporary.name) / "sandbox").resolve()
        cell.mkdir(mode=0o700)
        common._loaded_release = None
        common._loaded_release_failed = None
        harness = OwnerHarness(cell)
        harness.manifests = [{"implementation_digest": D0}] * 32
        records = run_owner.RunQualificationStore(cell).records
        neighbor = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, neighbor, ignore_errors=True)
        unrelated = neighbor / "impostor-0644"
        unrelated.write_bytes(b"someone else's publication")
        unrelated.chmod(0o644)
        original_write = run_owner.fsio.atomic_write

        def swapped_write(target, data, *, durable=False):
            original_write(target, data, durable=durable)
            if Path(target).parent == records:
                # the attacker wins the race after the durable write, with a hard
                # link to an unrelated file: the publication name holds other bytes
                Path(target).unlink()
                os.link(unrelated, target)

        with mock.patch.object(run_owner.fsio, "atomic_write", swapped_write):
            outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationFailed)
        self.assertIs(outcome.reason, run_owner.RunQualificationReason.SANDBOX_UNSAFE)
        self.assertIsNot(outcome.reason,
                         run_owner.RunQualificationReason.CAPABILITY_UNAVAILABLE)
        self.assertIsNone(outcome.probe)
        # nothing was published for this attempt: every entry at a record name is the
        # attacker's own hard link, unmutated, and no record resolves to it
        published = list(records.iterdir())
        self.assertTrue(published)
        for entry in published:
            self.assertEqual(entry.stat().st_ino, unrelated.stat().st_ino)
        self.assertEqual(stat_module.S_IMODE(unrelated.stat().st_mode), 0o644,
                         "the impostor keeps its own mode: nothing is ever written "
                         "through the unsafe name")
        for entry in published:
            with self.assertRaises((authority.GenericQualificationError,
                                    common.GenericScaffoldingError)):
                run_owner.RunQualificationStore(cell).load_record(
                    "sha256:" + entry.stem, project_id="project-1", run_id="run-1",
                    generation=1)

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

class DaemonFixtureRunner:
    """A fake Docker CLI mirroring one admitted runtime fixture: two context reads for
    discovery, then three pinned `version`/`info` reads per re-observation. A designated
    observation (counted from one) fails by drift, failed command result, or launch
    OSError."""

    def __init__(self, facts, *, fail_mode=None, fail_observation=None):
        self.facts = dict(facts)
        self.fail_mode = fail_mode          # None | "drift" | "result" | "launch"
        self.fail_observation = fail_observation
        self.observations_started = 0
        self.calls = []

    def _answers(self, args):
        f = self.facts
        info = {"OSType": f["server_raw_os"], "Architecture": f["server_raw_architecture"],
                "OperatingSystem": f["operating_system"],
                "SecurityOptions": f["security_options"],
                "MemoryLimit": f["memory_limit"], "SwapLimit": f["swap_limit"],
                "CpuCfsQuota": f["cpu_cfs_quota"], "PidsLimit": f["pids_limit"],
                "NCPU": f["ncpu"], "MemTotal": f["mem_total"],
                "CgroupVersion": f["cgroup_version"], "CgroupDriver": f["cgroup_driver"]}
        if "daemon_id" in f:
            info["ID"] = f["daemon_id"]
        server = {"Version": f["server_version"], "Os": f["server_raw_os"],
                  "Arch": f["server_raw_architecture"], "PlatformName": f["platform_name"]}
        if args == ("context", "show"):
            return f["context"]
        if tuple(args[:2]) == ("context", "inspect"):
            return json.dumps(f["endpoint"])
        if args == ("version", "--format", "{{json .Client.Version}}"):
            return json.dumps(f["client_version"])
        if tuple(args[:2]) == ("version", "--format"):
            return json.dumps(server)
        if args == ("info", "--format", "{{json .}}"):
            return json.dumps(info)
        raise AssertionError(f"unexpected docker/cli call {args}")

    def run(self, argv, **kwargs):
        argv = tuple(argv)
        args = argv[1:]
        environment = kwargs.get("env") or {}
        self.calls.append((argv, environment))
        if args == ("version", "--format", "{{json .Client.Version}}"):
            self.observations_started += 1
            if self.fail_observation == self.observations_started:
                if self.fail_mode == "drift":
                    self.facts["swap_limit"] = False
                elif self.fail_mode == "result":
                    return process.CommandResult.fake(1, stderr="docker: refused")
                elif self.fail_mode == "launch":
                    raise OSError("the Docker CLI could not be launched")
        answer = self._answers(args)
        return process.CommandResult.fake(stdout=answer + "\n")


class DestinationRecordingRunner(StubRunner):
    """A process runner *below* the concrete effects implementation: it records every
    command the real ConcreteGenericEffects launches — its full argv and environment —
    and answers from the in-memory FakeDocker, so the retry/recovery path of the owner
    can be witnessed at the actual command destination, not at the constructors."""

    def __init__(self, operation):
        super().__init__(FakeDocker(operation))
        self.destinations: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def run(self, argv, *, env=None, **kwargs):
        self.destinations.append((tuple(argv), dict(env or {})))
        return super().run(argv, env=env, **kwargs)


class RunBindingSpikeAuthorityTests(IsolatedSandboxHomeTestCase):
    """A spike resolves its run's binding through the run-scoped record the qualification
    published (spec sections 4.5 and 5 "Spikes"): loaded for its own run generation only, every
    observation at the record's pinned endpoint, the server major version bound across it."""

    def setUp(self):
        super().setUp()
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = (Path(temporary.name) / "sandbox").resolve()
        self.root.mkdir(mode=0o700)
        common._loaded_release = None
        common._loaded_release_failed = None
        self.addCleanup(lambda: setattr(common, "_loaded_release", None))
        harness = OwnerHarness(self.root)
        harness.manifests = [{"implementation_digest": D0}] * 32
        harness.inspector_facts["daemon_id"] = "the-qualified-daemon"
        outcome = harness.call()
        self.assertIs(type(outcome), run_owner.RunQualificationBound)
        self.record = outcome.record
        self.backend = run_owner.application.GenericQualifiedBackend(
            "docker", self.record["profile_id"], self.record["qualification_record_digest"],
            self.record["execution_binding_digest"])
        self.run = RunKey("project-1", "run-1", 1)

    def resolve(self, run=None, observed=None, release=D0):
        facts = copy.deepcopy(self.record["runtime"]) if observed is None else observed
        seen = []

        def pinned(recorded, *, deadline):
            seen.append(recorded["endpoint"])
            return authority.GenericRuntimeInspection(copy.deepcopy(facts))

        resolved = authority.GenericAuthorityResolver(
            run_owner.RunQualificationStore(self.root), runtime_inspector=pinned,
            implementation_inspector=lambda: {"implementation_digest": release},
            loaded_release=release).resolve(
                self.backend, deadline=time.monotonic() + 10, run=self.run if run is None else run)
        self.assertEqual(seen, [self.record["runtime"]["endpoint"]])
        return resolved

    def changed(self, **facts):
        return {**copy.deepcopy(self.record["runtime"]), **facts}

    def test_the_run_resolves_its_binding_in_the_run_scoped_private_runtime(self):
        resolved = self.resolve()
        store = run_owner.RunQualificationStore(self.root)
        self.assertEqual(resolved.work_directory, store.work)
        self.assertEqual(dict(resolved.environment)["DOCKER_CONFIG"],
                         str(store.configs / self.backend.profile_id))
        self.assertEqual(dict(resolved.environment)["DOCKER_HOST"],
                         self.record["runtime"]["endpoint"])

    def test_another_run_generation_never_resolves_the_record(self):
        for other in (RunKey("project-1", "run-1", 2), RunKey("project-1", "run-2", 1),
                      RunKey("project-2", "run-1", 1)):
            with self.subTest(other=other), self.assertRaises(
                    authority.GenericQualificationError) as caught:
                self.resolve(run=other)
            self.assertNotIsInstance(caught.exception, authority.DaemonDrift)

    def test_only_the_server_major_version_is_bound_across_the_binding(self):
        major, _, rest = self.record["runtime"]["server_version"].partition(".")
        self.assertEqual(self.resolve(observed=self.changed(
            server_version=major + ".99.0")).runtime["server_version"], major + ".99.0")
        with self.assertRaises(authority.DaemonDrift):
            self.resolve(observed=self.changed(server_version=str(int(major) + 1) + "." + rest))

    def test_a_changed_daemon_or_release_is_its_own_drift(self):
        with self.assertRaises(authority.DaemonDrift):
            self.resolve(observed=self.changed(daemon_id="another-daemon"))
        with self.assertRaises(authority.DaemonDrift):
            self.resolve(observed=self.changed(memory_limit=False))
        with self.assertRaises(authority.ImplementationDrift):
            self.resolve(release=D1)

    def test_a_foreign_file_in_the_private_config_refuses_the_binding(self):
        store = run_owner.RunQualificationStore(self.root)
        foreign = store.configs / self.backend.profile_id / "config.json"
        foreign.write_text("{}")
        with self.assertRaises(authority.GenericQualificationError) as caught:
            self.resolve()
        self.assertIn("foreign files", str(caught.exception))
        foreign.unlink()
        with self.assertRaises(authority.GenericQualificationError):
            store.check_runtime("docker-unknown-v1")


class RuntimeLockWaitTests(IsolatedSandboxHomeTestCase):
    """A spike's lease waits for the shared runtime lock within its own bound."""

    def setUp(self):
        super().setUp()
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = (Path(temporary.name) / "sandbox").resolve()
        self.root.mkdir(mode=0o700)

    def test_a_waiting_lease_is_granted_once_the_holder_releases(self):
        granted, released = threading.Event(), threading.Event()

        def waiter():
            with run_owner.hold_runtime_lock(sandbox_root=self.root, wait_seconds=10) as lease:
                granted.set()
                self.assertTrue(released.is_set(), "granted while another holder had it")
                self.assertLessEqual(lease.deadline, time.monotonic() + 10)

        with run_owner.hold_runtime_lock(sandbox_root=self.root):
            worker = threading.Thread(target=waiter, daemon=True)
            worker.start()
            self.assertFalse(granted.wait(timeout=0.2), "a held lock was granted again")
            released.set()
        self.assertTrue(granted.wait(timeout=10), "the waiting lease was never granted")
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive())

    def test_a_wait_past_its_bound_fails_and_a_malformed_bound_is_refused(self):
        clock = MutableClock()
        with run_owner.hold_runtime_lock(sandbox_root=self.root):
            def advance(*_):
                clock.advance(1.0)
            with mock.patch.object(common.time, "sleep", advance), \
                    self.assertRaises(common.GenericScaffoldingError):
                with run_owner.hold_runtime_lock(sandbox_root=self.root, clock=clock,
                                                 wait_seconds=0.5):
                    self.fail("a lease past its wait bound is never granted")
        for malformed in (0, -1, True, "1"):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                with run_owner.hold_runtime_lock(sandbox_root=self.root, wait_seconds=malformed):
                    self.fail("a malformed wait bound takes no lock")


if __name__ == "__main__":
    unittest.main()
