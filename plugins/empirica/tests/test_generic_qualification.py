#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from adapters.sandbox import common, generic_admission as admission
from adapters.sandbox import generic_authority as authority
from adapters.sandbox import generic_qualification as qualification
from adapters.sandbox import process
from test_docker_planner import D0, D1
from test_generic_authority import runtime
from test_generic_execution import FakeDocker


CLEAN = {"container_ids_absent": True, "network_ids_absent": True,
         "staging_empty": True, "journals_empty": True}


def image(arch="amd64", volumes=()):
    return qualification.QualificationImage(
        "busybox:local", D1, "linux", "linux", arch, arch, tuple(volumes))


def probes(profile="docker-isolated-v1"):
    return tuple(qualification.ProbeObservation(
        name, expected, expected, D0, True, index + 1,
        name == "network-profile" and profile == "docker-networked-v1",
        D0 if expected in {"PASS", "FAIL"} else None)
        for index, (name, expected) in enumerate(zip(
            qualification.PROBE_ORDER, qualification.PROBE_EXPECTED)))


def record(profile="docker-isolated-v1"):
    return qualification.build_candidate_record(
        profile, runtime(), D0, image(), probes(profile), CLEAN, 42)


class QueueRunner:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        if not self.results:
            raise AssertionError("unexpected runner call")
        return self.results.pop(0)


class FakeKernel:
    def __init__(self, *args, failure=None, cleanup=None, clock=None, **kwargs):
        self.profile = args[2]
        self.failure = failure
        self.cleanup = CLEAN if cleanup is None else cleanup
        self.calls = []

    def run(self, name, expected, *, deadline):
        self.calls.append((name, expected, deadline))
        if self.failure == name:
            raise qualification.GenericQualificationFailure(
                qualification.QualificationReason.PROBE_FAILED, "injected probe failure")
        return qualification.ProbeObservation(
            name, expected, expected, D0, True, len(self.calls),
            name == "network-profile" and self.profile == "docker-networked-v1",
            D0 if expected in {"PASS", "FAIL"} else None)

    def cleanup_proof(self):
        return dict(self.cleanup)


class FakeKernelFactory:
    def __init__(self, **options):
        self.options = options
        self.instance = None

    def __call__(self, *args, **kwargs):
        self.instance = FakeKernel(*args, **kwargs, **self.options)
        return self.instance


def runtime_inspector(value=None):
    facts = runtime() if value is None else value
    return lambda seed, deadline: authority.GenericRuntimeInspection(copy.deepcopy(facts))


def qualify_args(root, profile="docker-isolated-v1"):
    return {
        "profile_id": profile, "image_reference": "busybox:local",
        "docker_cli": Path(runtime()["docker_cli"]),
        "output": root / "candidate.json", "work_root": root / "work",
        "runtime_inspector": runtime_inspector(),
        "image_runner": QueueRunner(process.CommandResult.fake(stdout=json.dumps({
            "Id": D1, "Os": "linux", "Architecture": "amd64", "Volumes": None}))),
        "implementation_inspector": lambda: {"implementation_digest": D0},
    }


class CandidateQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)

    def test_both_profiles_run_exact_five_in_order_and_only_write_candidate(self):
        for profile in ("docker-isolated-v1", "docker-networked-v1"):
            with self.subTest(profile=profile):
                cell = self.root / profile
                cell.mkdir(mode=0o700)
                factory = FakeKernelFactory()
                args = qualify_args(cell, profile)
                args["kernel_factory"] = factory
                result = qualification.qualify_to_candidate(**args)
                self.assertEqual([row[0] for row in factory.instance.calls],
                                 list(qualification.PROBE_ORDER))
                self.assertEqual(len({row[2] for row in factory.instance.calls}), 1)
                self.assertLess(result["qualification_elapsed_ms"], 60_000)
                self.assertEqual(result["profile_id"], profile)
                self.assertTrue(args["output"].is_file())
                self.assertEqual(oct(args["output"].stat().st_mode & 0o777), "0o600")
                self.assertFalse((cell / qualification.NAMESPACE).exists())
                self.assertFalse((cell / "work" / qualification.NAMESPACE).exists())

    def test_the_probe_effects_run_under_the_authority_of_the_admitted_runtime(self):
        seen = []

        class EffectKernel(FakeKernel):
            def __init__(self, *args, effects_factory, **kwargs):
                super().__init__(*args, **kwargs)
                self.effects_factory = effects_factory

            def run(self, name, expected, *, deadline):
                self.effects_factory("probe", name)
                return super().run(name, expected, deadline=deadline)

        cell = self.root / "effects"
        cell.mkdir(mode=0o700)
        args = qualify_args(cell)
        args["kernel_factory"] = EffectKernel
        args["effects_factory"] = seen.append
        qualification.qualify_to_candidate(**args)
        self.assertEqual(len(seen), len(qualification.PROBE_ORDER))
        current = seen[0]
        self.assertIs(type(current), authority.ResolvedGenericAuthority)
        self.assertEqual((current.backend.profile_id, current.docker_cli, current.runtime),
                         ("docker-isolated-v1", Path(runtime()["docker_cli"]), runtime()))
        self.assertEqual(dict(current.environment)["DOCKER_HOST"], runtime()["endpoint"])

    def test_probe_failure_interrupt_cleanup_or_deadline_writes_no_candidate(self):
        cases = [
            FakeKernelFactory(failure="natural-125"),
            FakeKernelFactory(cleanup={**CLEAN, "journals_empty": False}),
        ]
        for index, factory in enumerate(cases):
            cell = self.root / f"case-{index}"
            cell.mkdir(mode=0o700)
            args = qualify_args(cell)
            args["kernel_factory"] = factory
            with self.subTest(index=index), self.assertRaises(
                    qualification.GenericQualificationFailure):
                qualification.qualify_to_candidate(**args)
            self.assertFalse(args["output"].exists())

        class InterruptKernel(FakeKernel):
            def run(self, name, expected, *, deadline):
                raise KeyboardInterrupt()

        cell = self.root / "interrupt"
        cell.mkdir(mode=0o700)
        args = qualify_args(cell)
        args["kernel_factory"] = InterruptKernel
        with self.assertRaises(KeyboardInterrupt):
            qualification.qualify_to_candidate(**args)
        self.assertFalse(args["output"].exists())

    def test_image_and_capability_failures_are_closed_and_effect_free(self):
        cases = []
        for document, reason in (
            ({"Id": D1, "Os": "linux", "Architecture": "arm64", "Volumes": None},
             qualification.QualificationReason.IMAGE_PLATFORM_MISMATCH),
            ({"Id": D1, "Os": "linux", "Architecture": "amd64", "Volumes": {"/data": {}}},
             qualification.QualificationReason.IMAGE_DECLARES_VOLUMES),
            ({"bad": True}, qualification.QualificationReason.IMAGE_MALFORMED),
        ):
            cases.append((process.CommandResult.fake(stdout=json.dumps(document)), reason))
        cases.extend((
            (process.CommandResult.fake(1, stderr="No such image"),
             qualification.QualificationReason.IMAGE_NOT_LOCAL),
            (process.CommandResult.fake(1, stderr="multiple images ambiguous"),
             qualification.QualificationReason.IMAGE_REFERENCE_AMBIGUOUS),
            (process.CommandResult.fake(stdout="{}", timed_out=True),
             qualification.QualificationReason.IMAGE_INSPECT_TIMEOUT),
            (process.CommandResult.fake(stdout="{}", output_limited=True),
             qualification.QualificationReason.IMAGE_INSPECT_OUTPUT_LIMIT),
        ))
        for index, (result, reason) in enumerate(cases):
            cell = self.root / f"image-{index}"
            cell.mkdir(mode=0o700)
            args = qualify_args(cell)
            args["image_runner"] = QueueRunner(result)
            args["kernel_factory"] = FakeKernelFactory()
            with self.subTest(reason=reason), self.assertRaises(
                    qualification.GenericQualificationFailure) as caught:
                qualification.qualify_to_candidate(**args)
            self.assertIs(caught.exception.reason, reason)
            self.assertFalse(args["output"].exists())

        cell = self.root / "capability"
        cell.mkdir(mode=0o700)
        args = qualify_args(cell)
        args["runtime_inspector"] = lambda seed, deadline: (_ for _ in ()).throw(
            RuntimeError("missing capability"))
        with self.assertRaises(qualification.GenericQualificationFailure) as caught:
            qualification.qualify_to_candidate(**args)
        self.assertIs(caught.exception.reason,
                      qualification.QualificationReason.CAPABILITY_UNAVAILABLE)

    def test_specific_runtime_capability_failure_detail_reaches_qualification_cli_boundary(self):
        for index, (detail, reason) in enumerate((
            ("resource-limits-unavailable: Docker SwapLimit must be enabled",
             qualification.QualificationReason.RESOURCE_LIMITS_UNAVAILABLE),
            ("macOS support requires Docker Desktop",
             qualification.QualificationReason.CAPABILITY_UNAVAILABLE),
        )):
            cell = self.root / f"specific-{index}"
            cell.mkdir(mode=0o700)
            args = qualify_args(cell)
            args["runtime_inspector"] = lambda seed, deadline, detail=detail: (_ for _ in ()).throw(
                authority.GenericQualificationError(detail))
            with self.assertRaises(qualification.GenericQualificationFailure) as caught:
                qualification.qualify_to_candidate(**args)
            self.assertIs(caught.exception.reason, reason)
            self.assertEqual(str(caught.exception), detail)

    def test_record_digest_tamper_old_schema_and_network_timing_are_rejected(self):
        self.assertEqual(authority.RECORD_SCHEMA, qualification.RECORD_SCHEMA)
        self.assertEqual(authority.NAMESPACE, qualification.NAMESPACE)
        base = record()
        mutations = []
        for mutate in (
            lambda row: row.__setitem__("schema", "siffran.empirica.generic-docker-qualification/2"),
            lambda row: row["probes"][1].__setitem__("observed", "PASS"),
            lambda row: row["cleanup"].__setitem__("staging_empty", False),
            lambda row: row.__setitem__("qualification_record_digest", D1),
            lambda row: row["probes"][-1].__setitem__("running_network_membership", True),
            lambda row: row["qualification_image"].__setitem__("declared_volumes", None),
            lambda row: row.__setitem__("probes", "not-a-list"),
        ):
            changed = copy.deepcopy(base)
            mutate(changed)
            mutations.append(changed)
        for changed in mutations:
            with self.assertRaises(qualification.GenericQualificationFailure):
                qualification.validate_candidate_record(changed)


class ProductionProbeKernelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = common.ExecutionStore(Path(self.temporary.name).resolve() / "runtime")
        self.store.prepare_execution_scaffolding()
        qualification.generic_journal.GenericJournalStore(self.store).prepare()

    def kernel(self, profile="docker-isolated-v1", options=None, fake_type=FakeDocker):
        made = []
        def factory(operation, name):
            selected = dict((options or {}).get(name, {}))
            fake = fake_type(operation, **selected)
            made.append((name, fake))
            return fake
        return qualification.ProductionProbeKernel(
            self.store, runtime(), profile, image(), D0, effects_factory=factory,
            runtime_inspector=runtime_inspector(),
            pinned_reobserver=runtime_inspector(),
            implementation_inspector=lambda: {"implementation_digest": D0}), made

    def test_exact_five_outcomes_use_production_lifecycle_and_cleanup(self):
        options = {
            "natural-125": {"exit_code": 125},
            "output-overflow": {"overflow": "stdout"},
            "timeout": {"timeout": True},
        }
        kernel, made = self.kernel(options=options)
        observations = [kernel.run(name, expected, deadline=time.monotonic() + 55)
                        for name, expected in zip(
                            qualification.PROBE_ORDER, qualification.PROBE_EXPECTED)]
        self.assertEqual([item.observed for item in observations],
                         list(qualification.PROBE_EXPECTED))
        self.assertTrue(all(item.cleanup_proven for item in observations))
        # N-M2: verdict probes record the observed realization digest; unavailable ones none.
        self.assertEqual([item.realization_digest for item in observations],
                         [D0, D0, None, None, D0])
        self.assertEqual(kernel.cleanup_proof(), CLEAN)
        natural = dict(made)["natural-125"]
        self.assertTrue(natural.attached)
        self.assertEqual(natural.exit_code, 125)

    def test_postflight_reobservation_drift_during_a_probe_fails_the_probe(self):
        drifted_endpoint = {**runtime(), "endpoint": "unix:///var/run/other.sock"}
        drifted_version = runtime()
        drifted_version["server_version"] = "28.0.5"
        cases = (
            ("daemon", {"pinned_reobserver": runtime_inspector(drifted_endpoint),
                         "implementation_inspector": lambda: {"implementation_digest": D0}},
             authority.DaemonDrift),
            ("version", {"pinned_reobserver": runtime_inspector(drifted_version),
                          "implementation_inspector": lambda: {"implementation_digest": D0}},
             authority.DaemonDrift),
            ("capability", {"pinned_reobserver": runtime_inspector(
                                 {**runtime(), "swap_limit": False}),
                             "implementation_inspector": lambda: {"implementation_digest": D0}},
             authority.DaemonDrift),
            ("implementation", {"pinned_reobserver": runtime_inspector(),
                                 "implementation_inspector": lambda: {"implementation_digest": D1}},
             authority.ImplementationDrift),
        )
        for label, inspector, drift_type in cases:
            with self.subTest(label=label):
                store = common.ExecutionStore(
                    Path(self.temporary.name).resolve() / f"drift-{label}")
                store.prepare_execution_scaffolding()
                qualification.generic_journal.GenericJournalStore(store).prepare()
                kernel = qualification.ProductionProbeKernel(
                    store, runtime(), "docker-isolated-v1", image(), D0,
                    effects_factory=lambda operation, name: FakeDocker(operation),
                    **{"clock": time.monotonic, **inspector})
                with self.assertRaises(qualification.GenericQualificationDrift) as caught:
                    kernel.run("realization", "PASS", deadline=time.monotonic() + 55)
                self.assertIs(type(caught.exception.drift), drift_type)
                self.assertIs(caught.exception.reason,
                              qualification.QualificationReason.PROBE_FAILED)
                self.assertIn("drifted", str(caught.exception))
                # Exact removals ran; the drifted operation stays journalled for recovery.
                self.assertTrue(qualification.generic_journal.GenericJournalStore(store).scan())

    def test_docker_cli_125_failed_start_is_not_natural_125(self):
        kernel, _ = self.kernel(options={"natural-125": {"failed_start": True}})
        with self.assertRaises(qualification.GenericQualificationFailure) as caught:
            kernel.run("natural-125", "FAIL", deadline=time.monotonic() + 55)
        self.assertIs(caught.exception.reason, qualification.QualificationReason.PROBE_FAILED)

    def test_overflow_timeout_cleanup_failure_and_missing_tool_fail_closed(self):
        cases = (
            ("output-overflow", "UNAVAILABLE_OUTPUT_LIMIT", {},
             qualification.QualificationReason.PROBE_FAILED),
            ("timeout", "UNAVAILABLE_TIMEOUT", {},
             qualification.QualificationReason.PROBE_FAILED),
        )
        for name, expected, options, reason in cases:
            kernel, _ = self.kernel(options={name: options})
            with self.subTest(name=name), self.assertRaises(
                    qualification.GenericQualificationFailure) as caught:
                kernel.run(name, expected, deadline=time.monotonic() + 55)
            self.assertIs(caught.exception.reason, reason)

        class MissingTool(FakeDocker):
            def attach(self, argv, *, timeout_seconds, stdout_limit, stderr_limit):
                self.attached = True
                return admission.RawAttachObservation(
                    self.raw(argv, 42, stdout="missing:wget"), True, False, 42)
        kernel, _ = self.kernel(fake_type=MissingTool)
        with self.assertRaises(qualification.GenericQualificationFailure) as caught:
            kernel.run("realization", "PASS", deadline=time.monotonic() + 55)
        self.assertIs(caught.exception.reason, qualification.QualificationReason.IMAGE_TOOL_MISSING)

        class UnusableUid(FakeDocker):
            def attach(self, argv, *, timeout_seconds, stdout_limit, stderr_limit):
                self.attached = True
                return admission.RawAttachObservation(
                    self.raw(argv, 45, stdout="uid65534-unusable"), True, False, 45)
        kernel, _ = self.kernel(fake_type=UnusableUid)
        with self.assertRaises(qualification.GenericQualificationFailure) as caught:
            kernel.run("realization", "PASS", deadline=time.monotonic() + 55)
        self.assertIs(caught.exception.reason, qualification.QualificationReason.IMAGE_UID_UNUSABLE)

        kernel, _ = self.kernel(options={"realization": {"cleanup_failure": "container"}})
        with self.assertRaises(qualification.GenericQualificationFailure) as caught:
            kernel.run("realization", "PASS", deadline=time.monotonic() + 55)
        self.assertIs(caught.exception.reason, qualification.QualificationReason.CLEANUP_UNAVAILABLE)
        self.assertTrue(qualification.generic_journal.GenericJournalStore(self.store).scan())

    def test_networked_probe_proves_membership_while_running_and_postexit_cleanup(self):
        class BlockingNetwork(FakeDocker):
            def __init__(self, operation, **options):
                super().__init__(operation, **options)
                self.release = threading.Event()
            def container_doc(self, staging):
                document = super().container_doc(staging)
                if self.attached and not self.release.is_set():
                    document["State"] = {"Status": "running", "Running": True,
                                         "ExitCode": 0, "OOMKilled": False, "Error": ""}
                return document
            def run(self, argv, **kwargs):
                result = super().run(argv, **kwargs)
                if tuple(argv)[:2] == ("network", "inspect") and self.attached:
                    self.release.set()
                return result
            def attach(self, argv, *, timeout_seconds, stdout_limit, stderr_limit):
                self.attached = True
                self.running = True
                self.release.wait(timeout=2)
                self.running = False
                return admission.RawAttachObservation(
                    self.raw(argv, 0, stdout="ok"), True, False, 0)
        kernel, made = self.kernel("docker-networked-v1", fake_type=BlockingNetwork)
        observed = kernel.run(
            "network-profile", "PASS", deadline=time.monotonic() + 55)
        self.assertTrue(observed.running_network_membership)
        fake = made[0][1]
        self.assertFalse(fake.container_present)
        self.assertFalse(fake.network_present)
        self.assertTrue(any(call[:2] == ("network", "inspect") for call in fake.calls))

        class BlockedEgress(BlockingNetwork):
            def attach(self, argv, *, timeout_seconds, stdout_limit, stderr_limit):
                self.attached = True
                self.exit_code = 46
                self.running = True
                self.release.wait(timeout=2)
                self.running = False
                return admission.RawAttachObservation(
                    self.raw(argv, 46, stderr="wget: network unreachable"), True, False, 46)

        kernel, _ = self.kernel("docker-networked-v1", fake_type=BlockedEgress)
        with self.assertRaises(qualification.GenericQualificationFailure) as caught:
            kernel.run("network-profile", "PASS", deadline=time.monotonic() + 55)
        self.assertIs(caught.exception.reason,
                      qualification.QualificationReason.NETWORK_EGRESS_UNAVAILABLE)

    def test_fast_egress_failure_before_running_membership_keeps_its_specific_reason(self):
        """N-L1: wget exits 46 before the running-membership poll observes the container."""
        class FastEgressFailure(FakeDocker):
            def attach(self, argv, *, timeout_seconds, stdout_limit, stderr_limit):
                self.attached = True
                self.exit_code = 46
                return admission.RawAttachObservation(
                    self.raw(argv, 46, stderr="wget: bad address"), True, False, 46)

        kernel, _ = self.kernel("docker-networked-v1", fake_type=FastEgressFailure)
        with self.assertRaises(qualification.GenericQualificationFailure) as caught:
            kernel.run("network-profile", "PASS", deadline=time.monotonic() + 55)
        self.assertIs(caught.exception.reason,
                      qualification.QualificationReason.NETWORK_EGRESS_UNAVAILABLE)
        self.assertEqual(qualification.generic_journal.GenericJournalStore(self.store).scan(), ())

    def test_unavailable_probe_cannot_claim_a_realization_digest(self):
        row = qualification.ProbeObservation(
            "output-overflow", "UNAVAILABLE_OUTPUT_LIMIT", "UNAVAILABLE_OUTPUT_LIMIT", D0,
            True, 1, False, D0).to_data()
        with self.assertRaisesRegex(qualification.GenericQualificationFailure, "cannot claim"):
            qualification.validate_probe(row, 2)
        qualification.validate_probe({**row, "realization_digest": None}, 2)
        row = qualification.ProbeObservation(
            "realization", "PASS", "PASS", D0, True, 1, False, None).to_data()
        with self.assertRaisesRegex(qualification.GenericQualificationFailure, "realization"):
            qualification.validate_probe(row, 0)
        qualification.validate_probe({**row, "realization_digest": D0}, 0)

    def test_source_has_no_old_probes_schema_or_image_acquisition(self):
        sources = "\n".join((
            Path(qualification.__file__).read_text(),
            (Path(__file__).parents[3] / "scripts/generic_docker_qualify.py").read_text(),
        ))
        for forbidden in ("seven-probe", "docker-rust-v1", "kill -9 $$",
                          "docker pull", "docker build", "docker load", "docker login",
                          "generic_sandbox_qualification"):
            self.assertNotIn(forbidden, sources)


if __name__ == "__main__":
    unittest.main()
