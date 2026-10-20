#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import time
import unittest
from pathlib import Path

from adapters.sandbox import generic_authority as authority
from adapters.sandbox import generic_qualification as qualification
from adapters.sandbox import planner, process
from application import generic_spike

D0 = "sha256:" + "0" * 64
D1 = "sha256:" + "1" * 64
CLI = str(Path(sys.executable).resolve())
FIXTURE = Path(__file__).parent / "fixtures" / "docker" / "generic-linux-v27.json"


def runtime(*, host_os="Linux", server_arch="x86_64", host_arch="amd64",
            platform_name="Docker Engine - Community", operating_system="Ubuntu 24.04",
            seccomp="name=seccomp,profile=default"):
    return {
        "docker_cli": CLI, "endpoint": "unix:///var/run/docker.sock", "context": "default",
        "client_version": "27.5.1", "server_version": "27.5.1",
        "server_raw_os": "linux", "server_os": "linux",
        "server_raw_architecture": server_arch, "server_architecture": "amd64",
        "platform_name": platform_name, "operating_system": operating_system,
        "security_options": ["name=cgroupns", seccomp],
        "memory_limit": True, "swap_limit": True,
        "cpu_cfs_quota": True, "pids_limit": True,
        "ncpu": 8, "mem_total": 16 * 1024**3, "cgroup_version": "2",
        "cgroup_driver": "systemd", "host_os": host_os,
        "host_raw_architecture": host_arch, "host_architecture": "amd64",
        "discovery_reads": 5,
    }


def desktop_runtime():
    return runtime(host_os="Darwin", server_arch="aarch64", host_arch="arm64",
                   platform_name="Docker Desktop 4.38.0 (181591)",
                   operating_system="Docker Desktop",
                   seccomp="name=seccomp,profile=builtin")


def qualification_record(profile="docker-isolated-v1"):
    image = qualification.QualificationImage(
        "busybox:local", D1, "linux", "linux", "x86_64", "amd64", ())
    probes = tuple(qualification.ProbeObservation(
        name, expected, expected, D0, True, index + 1,
        name == "network-profile" and profile == "docker-networked-v1")
        for index, (name, expected) in enumerate(zip(
            qualification.PROBE_ORDER, qualification.PROBE_EXPECTED)))
    return qualification.build_candidate_record(
        profile, runtime(), D0, image, probes,
        {"container_ids_absent": True, "network_ids_absent": True,
         "staging_empty": True, "journals_empty": True}, 50)


def backend():
    return generic_spike.GenericQualifiedBackend("docker", "docker-isolated-v1", D0, D1)


class QueueRunner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        if not self.outputs:
            raise AssertionError("unexpected runner call")
        return self.outputs.pop(0)


class FakeStore:
    def __init__(self, record):
        self.record = record
        self.execution_store = object()
        self.calls = 0

    def check_runtime(self, profile):
        self.calls += 1
        if profile != "docker-isolated-v1":
            raise AssertionError(profile)
        return Path("/config"), Path("/work")

    def load_record(self, digest):
        if digest != self.record["qualification_record_digest"]:
            raise AssertionError(digest)
        return copy.deepcopy(self.record)


class FixtureTests(unittest.TestCase):
    def test_generated_cross_platform_and_optional_forms_are_admissible(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(fixture["schema"],
                         "siffran.empirica.test-fixture.docker-generic-linux/2")
        for cell in fixture["capabilities"]:
            value = runtime(
                host_os=cell["host_os"], server_arch=cell["server_architecture"],
                host_arch=cell["host_architecture"],
                platform_name=cell["platform_name"],
                operating_system=cell["operating_system"],
                seccomp=next(item for item in cell["security_options"]
                             if item.startswith("name=seccomp")))
            value["security_options"] = cell["security_options"]
            for key in ("memory_limit", "swap_limit", "cpu_cfs_quota", "pids_limit",
                        "ncpu", "mem_total"):
                value[key] = cell[key]
            with self.subTest(cell=cell["name"]):
                authority.validate_runtime(value)
        for cell in fixture["image_optional_forms"]:
            image_id, raw_os, raw_arch, volumes = authority.generic_admission.decode_image_document(cell)
            self.assertEqual(image_id, D1)
            self.assertEqual(raw_os, "linux")
            self.assertEqual(volumes, ("/data",) if cell["name"] == "declared" else ())
        for cell in fixture["terminal_forms"]:
            terminal = authority.generic_admission.decode_terminal_container(
                {"State": cell["State"], "Networks": None})
            self.assertEqual(terminal.status, cell["State"]["Status"])


class RuntimeCapabilityTests(unittest.TestCase):
    def test_supports_linux_rootful_rootless_and_desktop_aliases(self):
        native = authority.validate_runtime(runtime())
        self.assertEqual(native["server_architecture"], "amd64")
        rootless = runtime()
        rootless["security_options"].append("name=rootless")
        self.assertIn("name=rootless", authority.validate_runtime(rootless)["security_options"])
        desktop = authority.validate_runtime(desktop_runtime())
        self.assertEqual(desktop["server_architecture"], "arm64")
        self.assertEqual(desktop["host_architecture"], "arm64")

    def test_rejects_remote_nondocker_or_missing_capability_but_not_host_python_arch(self):
        cases = []
        for key, value in (("endpoint", "tcp://127.0.0.1:2375"),
                           ("platform_name", "Podman Engine"),
                           ("memory_limit", False), ("swap_limit", False),
                           ("cpu_cfs_quota", False),
                           ("pids_limit", False), ("ncpu", 0)):
            changed = runtime()
            changed[key] = value
            cases.append(changed)
        changed = runtime(host_arch="arm64")
        self.assertEqual(authority.validate_runtime(changed)["server_architecture"], "amd64")
        changed = runtime()
        changed["security_options"] = ["name=cgroupns"]
        cases.append(changed)
        changed = desktop_runtime()
        changed["operating_system"] = "LinuxKit"
        changed["platform_name"] = "Docker Engine"
        cases.append(changed)
        for index, changed in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(authority.GenericQualificationError):
                authority.validate_runtime(changed)

    def test_swap_limit_failure_is_specific_and_docker_cli_symlink_resolves_canonically(self):
        changed = runtime()
        changed["swap_limit"] = False
        with self.assertRaisesRegex(authority.GenericQualificationError,
                                    "resource-limits-unavailable.*SwapLimit"):
            authority.validate_runtime(changed)
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            alias = Path(directory) / "docker"
            alias.symlink_to(Path(CLI))
            changed = runtime()
            changed["docker_cli"] = str(alias)
            self.assertEqual(authority.validate_runtime(changed)["docker_cli"], CLI)

    def test_versions_and_cgroup_strings_are_diagnostic_not_semantic_authority(self):
        first = runtime()
        changed = runtime()
        changed.update(client_version="28.0.0", server_version="28.0.0",
                       cgroup_driver="cgroupfs", cgroup_version="v2")
        self.assertEqual(authority.authority_facts(first), authority.authority_facts(changed))

    def test_five_bounded_passive_reads_project_only_required_facts(self):
        server = {"Version": "27", "Os": "linux", "Arch": "aarch64",
                  "PlatformName": "Docker Desktop"}
        info = {"OSType": "linux", "Architecture": "arm64",
                "OperatingSystem": "Docker Desktop",
                "SecurityOptions": ["name=cgroupns", "name=seccomp,profile=builtin"],
                "MemoryLimit": True, "SwapLimit": True,
                "CpuCfsQuota": True, "PidsLimit": True,
                "NCPU": 10, "MemTotal": 123, "CgroupVersion": "2",
                "CgroupDriver": "cgroupfs"}
        runner = QueueRunner([
            process.CommandResult.fake(stdout="desktop-linux"),
            process.CommandResult.fake(stdout='"unix:///Users/me/.docker/run/docker.sock"'),
            process.CommandResult.fake(stdout='"27"'),
            process.CommandResult.fake(stdout=json.dumps(server)),
            process.CommandResult.fake(stdout=json.dumps(info)),
        ])
        seed = desktop_runtime()
        observed = authority.inspect_current_runtime(
            seed, deadline=time.monotonic() + 10, runner=runner,
            host_facts=("Darwin", "arm64"))
        self.assertEqual(observed.runtime["server_architecture"], "arm64")
        self.assertEqual(observed.runtime["ncpu"], 10)
        self.assertEqual(len(runner.calls), 5)
        self.assertTrue(all(call[1]["output_limit_bytes"] == 64 * 1024
                            and call[1]["timeout_seconds"] <= 5 for call in runner.calls))
        self.assertNotIn("{{json .}}", runner.calls[-1][0][-1])
        self.assertEqual([call[0][1:3] for call in runner.calls],
                         [("context", "show"), ("context", "inspect"),
                          ("version", "--format"), ("version", "--format"),
                          ("info", "--format")])

    def test_record_binds_semantic_authority_and_implementation(self):
        record = qualification_record()
        self.assertEqual(authority.validate_record(record), record)
        for path, value in (("policy_digest", D0),
                            ("qualification_elapsed_ms", 60_000)):
            changed = copy.deepcopy(record)
            changed[path] = value
            with self.subTest(path=path), self.assertRaises(authority.GenericQualificationError):
                authority.validate_record(changed)
        changed = copy.deepcopy(record)
        changed["runtime"]["ncpu"] = 9
        with self.assertRaises(authority.GenericQualificationError):
            authority.validate_record(changed)
        changed = copy.deepcopy(record)
        changed["probes"][2]["observed"] = "PASS"
        with self.assertRaises(authority.GenericQualificationError):
            authority.validate_record(changed)


class HistoricalResolutionTests(unittest.TestCase):
    def setUp(self):
        self.record = qualification_record()
        self.backend = generic_spike.GenericQualifiedBackend(
            "docker", "docker-isolated-v1", self.record["qualification_record_digest"],
            self.record["execution_binding_digest"])

    def resolver(self, observed=None):
        return authority.GenericAuthorityResolver(
            FakeStore(self.record),
            runtime_inspector=lambda seed, deadline: authority.GenericRuntimeInspection(
                copy.deepcopy(self.record["runtime"] if observed is None else observed)),
            implementation_inspector=lambda: {"implementation_digest": D0})

    def test_reobserves_exact_semantic_authority_but_tolerates_patch_diagnostics(self):
        changed = copy.deepcopy(self.record["runtime"])
        changed.update(client_version="28", server_version="28", cgroup_driver="other")
        resolved = self.resolver(changed).resolve(
            self.backend, deadline=time.monotonic() + 10)
        self.assertEqual(resolved.engine_ncpu, 8)
        self.assertEqual(resolved.architecture, "amd64")
        self.assertEqual(dict(resolved.environment)["DOCKER_HOST"],
                         "unix:///var/run/docker.sock")

    def test_rejects_platform_drift_but_tolerates_capacity_and_optional_security_diagnostics(self):
        for key, value, rejected in (
            ("ncpu", 9, False), ("mem_total", 99, False),
            ("security_options", ["name=cgroupns", "name=seccomp,profile=default",
                                  "name=rootless", "name=userns"], False),
            ("server_raw_architecture", "arm64", True),
        ):
            changed = copy.deepcopy(self.record["runtime"])
            changed[key] = value
            if rejected:
                with self.subTest(key=key), self.assertRaises(authority.GenericQualificationError):
                    self.resolver(changed).resolve(self.backend, deadline=time.monotonic() + 10)
            else:
                resolved = self.resolver(changed).resolve(
                    self.backend, deadline=time.monotonic() + 10)
                expected = sorted(value) if key == "security_options" else value
                self.assertEqual(resolved.runtime[key], expected)


class LocalImageResolverTests(unittest.TestCase):
    def resolved_authority(self):
        return authority.ResolvedGenericAuthority(
            backend(), Path(CLI), (("DOCKER_HOST", "unix:///var/run/docker.sock"),
                                   ("LANG", "C"), ("LC_ALL", "C")),
            Path("/work"), "linux", "amd64", 8, 123, runtime())

    def image_resolver(self, result):
        class Authority:
            def __init__(self, value):
                self.value = value
                self.calls = []

            def resolve(self, selected, *, deadline):
                self.calls.append((selected, deadline))
                return self.value
        provider = Authority(self.resolved_authority())
        runner = QueueRunner([result])
        return authority.DockerLocalImageResolver(provider, runner=runner), provider, runner

    def test_one_bounded_read_only_inspect_returns_minimal_local_resolution(self):
        document = {"Id": D1, "Os": "linux", "Architecture": "x86_64",
                    "Volumes": None, "Unexpected": {"ignored": True}}
        resolver, provider, runner = self.image_resolver(
            process.CommandResult.fake(stdout=json.dumps(document), stderr="warning"))
        result = resolver.resolve("python:3.12", backend())
        self.assertIs(type(result), generic_spike.LocalImageResolution)
        self.assertEqual(result.image_id, D1)
        self.assertEqual(result.raw_architecture, "x86_64")
        self.assertEqual(result.server_raw_architecture, "x86_64")
        self.assertEqual(result.engine_ncpu, 8)
        self.assertEqual(result.backend, backend())
        argv = runner.calls[0][0]
        self.assertEqual(argv[1:5], ("image", "inspect", "--format", planner.IMAGE_INSPECT_FORMAT))
        self.assertEqual(argv[-1], "python:3.12")
        self.assertNotIn("pull", argv)
        self.assertNotIn("RepoDigests", argv)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(runner.calls[0][1]["output_limit_bytes"], 64 * 1024)

    def test_declared_volumes_are_reported_for_application_refusal(self):
        resolver, _, _ = self.image_resolver(process.CommandResult.fake(stdout=json.dumps({
            "Id": D1, "Os": "linux", "Architecture": "amd64",
            "Volumes": {"/data": {}}})))
        result = resolver.resolve("python:3.12", backend())
        self.assertEqual(result.declared_volumes, ("/data",))

    def test_authority_failure_is_distinct_from_malformed_image_output(self):
        class RefusingAuthority:
            def resolve(self, selected, *, deadline):
                raise authority.GenericQualificationError("drift")

        resolver = authority.DockerLocalImageResolver(
            RefusingAuthority(), runner=QueueRunner([]))
        result = resolver.resolve("python:3.12", backend())
        self.assertIs(result.reason, generic_spike.GenericPreparationReason.AUTHORITY_DRIFT)

    def test_missing_ambiguous_and_malformed_results_are_closed(self):
        cases = [
            (process.CommandResult.fake(1, stderr="Error: No such image: missing"),
             generic_spike.GenericPreparationReason.IMAGE_NOT_LOCAL),
            (process.CommandResult.fake(1, stderr="multiple IDs found: ambiguous"),
             generic_spike.GenericPreparationReason.IMAGE_REFERENCE_AMBIGUOUS),
            (process.CommandResult.fake(stdout="not-json"),
             generic_spike.GenericPreparationReason.MALFORMED_REQUIRED_RESULT),
            (process.CommandResult.fake(stdout="{}", output_limited=True),
             generic_spike.GenericPreparationReason.MALFORMED_REQUIRED_RESULT),
        ]
        for command, expected in cases:
            resolver, _, _ = self.image_resolver(command)
            result = resolver.resolve("python:3.12", backend())
            self.assertIs(type(result), generic_spike.LocalImageResolutionUnavailable)
            self.assertIs(result.reason, expected)


if __name__ == "__main__":
    unittest.main()
