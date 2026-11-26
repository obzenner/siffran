#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import time
import unittest
from pathlib import Path

from adapters.sandbox import common
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
        name == "network-profile" and profile == "docker-networked-v1",
        D0 if expected in {"PASS", "FAIL"} else None)
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


class DockerCli:
    """The five passive reads answered as docker/cli does, from the independent fixture.

    `info --format` executes on the Go struct `dockerInfo`: `{{json .}}` yields the Engine-API
    JSON, while a map-style `index .` template fails exactly as the CLI does.
    """

    def __init__(self):
        self.info = json.loads(FIXTURE.read_text(encoding="utf-8"))["docker_info"]
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        args, fake = tuple(argv[1:]), process.CommandResult.fake
        if args == ("context", "show"):
            return fake(stdout="desktop-linux\n")
        if args[:2] == ("context", "inspect"):
            return fake(stdout='"unix:///Users/me/.docker/run/docker.sock"\n')
        if args == ("version", "--format", "{{json .Client.Version}}"):
            return fake(stdout='"29.7.2"\n')
        if args[:2] == ("version", "--format") and ".Server.Platform.Name" in args[2]:
            return fake(stdout=json.dumps(self.info["version_server_projection"]) + "\n")
        if args == ("info", "--format", "{{json .}}"):
            return fake(stdout=json.dumps(self.info["json_form"]) + "\n")
        if args[:2] == ("info", "--format") and "index ." in args[2]:
            return fake(self.info["struct_template_returncode"],
                        stderr="template: :1:12: executing \"\" at <index . \"OSType\">: "
                               + self.info["struct_template_error"] + "\n")
        raise AssertionError(f"unexpected docker/cli call {args}")


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
        self.assertEqual(runner.calls[-1][0][1:], ("info", "--format", "{{json .}}"))
        self.assertEqual([call[0][1:3] for call in runner.calls],
                         [("context", "show"), ("context", "inspect"),
                          ("version", "--format"), ("version", "--format"),
                          ("info", "--format")])

    def test_pin_of_version_and_info_reads_to_the_context_endpoint(self):
        server = {"Version": "27", "Os": "linux", "Arch": "x86_64",
                  "PlatformName": "Docker Engine - Community"}
        info = {"OSType": "linux", "Architecture": "x86_64",
                "OperatingSystem": "Ubuntu 24.04",
                "SecurityOptions": ["name=cgroupns", "name=seccomp,profile=default"],
                "MemoryLimit": True, "SwapLimit": True, "CpuCfsQuota": True,
                "PidsLimit": True, "NCPU": 8, "MemTotal": 123,
                "CgroupVersion": "2", "CgroupDriver": "systemd",
                "ID": "engine-daemon-identity-1"}
        runner = QueueRunner([
            process.CommandResult.fake(stdout="default"),
            process.CommandResult.fake(stdout='"unix:///var/run/docker.sock"'),
            process.CommandResult.fake(stdout='"27"'),
            process.CommandResult.fake(stdout=json.dumps(server)),
            process.CommandResult.fake(stdout=json.dumps(info)),
        ])
        authority.inspect_current_runtime(
            runtime(), deadline=time.monotonic() + 10, runner=runner,
            host_facts=("Linux", "x86_64"))
        self.assertEqual(len(runner.calls), 5)
        self.assertEqual(runner.calls[1][0][1:],
                         ("context", "inspect", "default", "--format",
                          "{{json .Endpoints.docker.Host}}"))
        for header in (runner.calls[0][1]["env"], runner.calls[1][1]["env"]):
            self.assertEqual(header, {"LANG": "C", "LC_ALL": "C"})
            self.assertNotIn("DOCKER_HOST", header)
        for pinned in [call[1]["env"] for call in runner.calls[2:]]:
            self.assertEqual(pinned, {"LANG": "C", "LC_ALL": "C",
                                      "DOCKER_HOST": "unix:///var/run/docker.sock"})

    def test_daemon_id_is_projected_from_engine_info_or_stays_absent(self):
        observed = authority.inspect_current_runtime(
            desktop_runtime(), deadline=time.monotonic() + 10, runner=DockerCli(),
            host_facts=("Darwin", "arm64")).runtime
        self.assertEqual(observed["daemon_id"], "pinned-sample")
        self.assertEqual(authority.authority_facts(observed)["daemon_id"], "pinned-sample")
        self.assertNotIn("daemon_id", authority.authority_facts(runtime()))
        server = {"Version": "27", "Os": "linux", "Arch": "x86_64",
                  "PlatformName": "Docker Engine - Community"}
        info = {"OSType": "linux", "Architecture": "x86_64",
                "OperatingSystem": "Ubuntu 24.04",
                "SecurityOptions": ["name=cgroupns", "name=seccomp,profile=default"],
                "MemoryLimit": True, "SwapLimit": True, "CpuCfsQuota": True,
                "PidsLimit": True, "NCPU": 8, "MemTotal": 123,
                "CgroupVersion": "2", "CgroupDriver": "systemd"}
        runner = QueueRunner([
            process.CommandResult.fake(stdout="default"),
            process.CommandResult.fake(stdout='"unix:///var/run/docker.sock"'),
            process.CommandResult.fake(stdout='"27"'),
            process.CommandResult.fake(stdout=json.dumps(server)),
            process.CommandResult.fake(stdout=json.dumps(info)),
        ])
        observed = authority.inspect_current_runtime(
            runtime(), deadline=time.monotonic() + 10, runner=runner,
            host_facts=("Linux", "x86_64")).runtime
        self.assertNotIn("daemon_id", observed)

    def test_daemon_id_must_be_nonblank_bounded_and_control_free(self):
        for index, malformed in enumerate(("", "   ", "x" * 129, "bad\nidentity", "bad\x1fidentity",
                                          "bad\x7fidentity",
                                          "bad\x85identity", "bad\x9fidentity", 123, True, None)):
            with self.subTest(index=index), self.assertRaises(
                    authority.GenericQualificationError) as caught:
                authority.validate_runtime({**runtime(), "daemon_id": malformed})
            self.assertEqual(str(caught.exception), "Docker daemon identity is malformed")
        for index, boundary in enumerate(("x" * 128, "a b x", "a\u00a0b")):
            with self.subTest(boundary=index):
                self.assertEqual(authority.validate_runtime(
                    {**runtime(), "daemon_id": boundary})["daemon_id"], boundary)

    def test_shrinking_per_read_timeouts_reach_the_runner_and_stop_after_the_deadline(self):
        class SteadyClock:
            def __init__(self, now):
                self.now = now

            def __call__(self):
                return self.now

        class OccupiedRunner:
            """Each read consumes exactly the timeout it was granted, like a busy daemon read."""

            def __init__(self, clock, *results):
                self.clock, self.results, self.calls = clock, list(results), []

            def run(self, argv, **kwargs):
                self.calls.append((tuple(argv), kwargs))
                self.clock.now += kwargs["timeout_seconds"]
                return self.results.pop(0)

        clock = SteadyClock(1000.0)
        runner = OccupiedRunner(
            clock, *[process.CommandResult.fake(stdout=output) for output in (
                "default", '"unix:///var/run/docker.sock"', '"27"', 'not', 'reached')])
        with self.assertRaisesRegex(authority.GenericQualificationError, "deadline expired"):
            authority.inspect_current_runtime(
                runtime(), deadline=1007.0, runner=runner, clock=clock,
                host_facts=("Linux", "x86_64"))
        self.assertEqual([call[1]["timeout_seconds"] for call in runner.calls], [5.0, 2.0])
        self.assertEqual(len(runner.calls), 2)

    def test_docker_29_info_json_is_read_struct_safely_and_projected(self):
        cli = DockerCli()
        observed = authority.inspect_current_runtime(
            desktop_runtime(), deadline=time.monotonic() + 10, runner=cli,
            host_facts=("Darwin", "arm64")).runtime
        self.assertEqual(len(cli.calls), 5)
        self.assertEqual(cli.calls[-1][0][1:], ("info", "--format", "{{json .}}"))
        self.assertEqual(
            {key: observed[key] for key in (
                "server_version", "server_architecture", "platform_name", "operating_system",
                "security_options", "memory_limit", "swap_limit", "ncpu")},
            {"server_version": "29.7.2", "server_architecture": "arm64",
             "platform_name": "Docker Desktop 4.90.0 (238679)",
             "operating_system": "Docker Desktop",
             "security_options": ["name=cgroupns", "name=seccomp,profile=builtin"],
             "memory_limit": True, "swap_limit": True, "ncpu": 8})
        # Only the named JSON keys are projected; the rest of the document is not retained.
        self.assertNotIn("docker-desktop", json.dumps(observed))
        self.assertNotIn("ClientInfo", json.dumps(observed))

    def test_map_style_info_template_fails_as_docker_cli_does(self):
        cli = DockerCli()
        refused = cli.run((CLI, "info", "--format", '{"OSType":{{json (index . "OSType")}}}'))
        self.assertEqual(refused.returncode, 1)
        self.assertIn(b"can't index item of type system.dockerInfo", refused.stderr.data)

        class StructUnsafe(DockerCli):
            def run(self, argv, **kwargs):
                if tuple(argv[1:3]) == ("info", "--format"):
                    argv = (*argv[:3], '{"OSType":{{json (index . "OSType")}}}')
                return super().run(argv, **kwargs)

        with self.assertRaisesRegex(authority.GenericQualificationError, "Docker info read failed"):
            authority.inspect_current_runtime(
                desktop_runtime(), deadline=time.monotonic() + 10, runner=StructUnsafe(),
                host_facts=("Darwin", "arm64"))

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



_SERVER = {"Version": "27", "Os": "linux", "Arch": "x86_64", "PlatformName": "Docker Engine - Community"}
_INFO = {"OSType": "linux", "Architecture": "x86_64", "OperatingSystem": "Ubuntu 24.04",
         "SecurityOptions": ["name=cgroupns", "name=seccomp,profile=default"],
         "MemoryLimit": True, "SwapLimit": True, "CpuCfsQuota": True, "PidsLimit": True,
         "NCPU": 8, "MemTotal": 123, "CgroupVersion": "2", "CgroupDriver": "systemd"}
#: The bounded reads of each endpoint source, in order: discovery resolves the endpoint itself,
#: a pinned re-observation reads only at the endpoint the recorded runtime carries.
_DISCOVERY_READS = ("Docker context read", "Docker endpoint read", "Docker client version read",
                    "Docker server version read", "Docker info read")
_PINNED_READS = _DISCOVERY_READS[2:]


def _answers(*, pinned, server=_SERVER, info=_INFO, context="default"):
    reads = [process.CommandResult.fake(stdout='"27.5.1"'),
             process.CommandResult.fake(stdout=json.dumps(server)),
             process.CommandResult.fake(stdout=json.dumps(info))]
    if pinned:
        return reads
    return [process.CommandResult.fake(stdout=context),
            process.CommandResult.fake(stdout='"unix:///var/run/docker.sock"'), *reads]


class RaisingQueueRunner(QueueRunner):
    def run(self, argv, **kwargs):
        answer = super().run(argv, **kwargs)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class RuntimeInspectionTests(unittest.TestCase):
    """The one bounded observation owner behind the discovering, the admitting and the pinned
    inspectors: one closed error per read failure, the endpoint source and the admission."""

    SOURCES = (("observed", authority.inspect_observed_runtime, False, _DISCOVERY_READS),
               ("pinned", authority.inspect_pinned_runtime, True, _PINNED_READS))

    def inspect(self, inspector, answers, *, recorded=None, clock=time.monotonic, deadline=None):
        runner = RaisingQueueRunner(answers)
        observed = inspector(runtime() if recorded is None else recorded,
                             deadline=clock() + 10 if deadline is None else deadline,
                             runner=runner, clock=clock, host_facts=("Linux", "x86_64"))
        return observed, runner

    def test_each_read_failure_is_one_closed_error_naming_its_read_and_daemon(self):
        failures = (
            ("timed out", process.CommandResult.fake(stdout="x", timed_out=True), " failed"),
            ("output limited", process.CommandResult.fake(stdout="x", output_limited=True), " failed"),
            ("nonzero", process.CommandResult.fake(returncode=1, stdout="x"), " failed"),
            ("not UTF-8", process.CommandResult.fake(stdout=b"\xff"), " is not UTF-8"),
            ("empty", process.CommandResult.fake(stdout="  "), " is empty"),
            ("unlaunchable", OSError("spawn refused"), None),
        )
        for daemon, inspector, pinned, reads in self.SOURCES:
            for index, label in enumerate(reads):
                for name, failed, suffix in failures:
                    answers = _answers(pinned=pinned)
                    answers[index] = failed
                    expected = (label + suffix if suffix is not None
                                else f"{label} could not be launched at the {daemon} daemon")
                    with self.subTest(daemon=daemon, read=label, failure=name):
                        with self.assertRaises(authority.GenericQualificationError) as caught:
                            self.inspect(inspector, answers)
                        self.assertEqual(str(caught.exception), expected)

    def test_an_expired_deadline_reads_nothing_and_any_time_left_is_the_read_bound(self):
        for daemon, inspector, pinned, reads in self.SOURCES:
            with self.subTest(daemon=daemon):
                runner = RaisingQueueRunner(_answers(pinned=pinned))
                with self.assertRaises(authority.GenericQualificationError) as caught:
                    inspector(runtime(), deadline=100.0, runner=runner, clock=lambda: 100.0)
                self.assertEqual(str(caught.exception), "passive verification deadline expired")
                self.assertEqual(runner.calls, [])
                _, runner = self.inspect(inspector, _answers(pinned=pinned),
                                         clock=lambda: 100.0, deadline=100.5)
                self.assertEqual([call[1]["timeout_seconds"] for call in runner.calls],
                                 [0.5] * len(reads))

    def test_the_pinned_source_reads_at_the_recorded_endpoint_and_inherits_its_discovery(self):
        recorded = {**runtime(), "endpoint": "unix:///run/pinned.sock", "context": "pinned"}
        observed, runner = self.inspect(authority.inspect_pinned_runtime,
                                        _answers(pinned=True), recorded=recorded)
        self.assertEqual([call[0][1:3] for call in runner.calls],
                         [("version", "--format"), ("version", "--format"), ("info", "--format")])
        self.assertTrue(all(call[1]["env"] == {"LANG": "C", "LC_ALL": "C",
                                               "DOCKER_HOST": "unix:///run/pinned.sock"}
                            for call in runner.calls))
        for member in ("endpoint", "context", "discovery_reads", "docker_cli"):
            self.assertEqual(observed.runtime[member], recorded[member], member)
        with self.assertRaises(authority.GenericQualificationError) as caught:
            self.inspect(authority.inspect_pinned_runtime, _answers(pinned=True),
                         recorded={**recorded, "discovery_reads": 4})
        self.assertEqual(str(caught.exception), "the recorded pinned runtime is malformed")

    def test_the_discovering_source_refuses_a_malformed_context_or_projection(self):
        for answers, message in (
                (_answers(pinned=False, context="bad context"), "Docker context is malformed"),
                (_answers(pinned=False, server=["not", "an", "object"]),
                 "Docker version/info projection is malformed")):
            with self.subTest(message=message), \
                    self.assertRaises(authority.GenericQualificationError) as caught:
                self.inspect(authority.inspect_observed_runtime, answers)
            self.assertEqual(str(caught.exception), message)

    def test_a_read_that_is_not_json_names_its_own_document(self):
        for index, document in ((1, "Docker endpoint"), (2, "Docker client version"),
                                (3, "Docker server version"), (4, "Docker info")):
            answers = _answers(pinned=False)
            answers[index] = process.CommandResult.fake(stdout="{not json")
            with self.subTest(document=document), \
                    self.assertRaises(authority.GenericQualificationError) as caught:
                self.inspect(authority.inspect_observed_runtime, answers)
            self.assertEqual(str(caught.exception), document + " is malformed JSON")

    def test_omitted_diagnostic_strings_project_as_empty(self):
        server = {key: value for key, value in _SERVER.items() if key not in ("Version", "PlatformName")}
        info = {key: value for key, value in _INFO.items() if key != "OperatingSystem"}
        observed, _ = self.inspect(authority.inspect_observed_runtime,
                                   _answers(pinned=False, server=server, info=info))
        self.assertEqual([observed.runtime[member] for member in
                          ("server_version", "platform_name", "operating_system")], ["", "", ""])

    def test_the_server_answer_wins_and_info_fills_only_what_the_server_omits(self):
        server = {key: value for key, value in _SERVER.items() if key not in ("Os", "Arch")}
        info = {**_INFO, "CgroupVersion": None, "CgroupDriver": None}
        observed, _ = self.inspect(authority.inspect_observed_runtime,
                                   _answers(pinned=False, server=server, info=info))
        self.assertEqual((observed.runtime["server_raw_os"], observed.runtime["server_raw_architecture"]),
                         (_INFO["OSType"], _INFO["Architecture"]))
        self.assertEqual((observed.runtime["cgroup_version"], observed.runtime["cgroup_driver"]), ("", ""))
        info = {**_INFO, "OSType": "windows", "Architecture": "arm64"}
        observed, _ = self.inspect(authority.inspect_observed_runtime,
                                   _answers(pinned=False, info=info))
        self.assertEqual((observed.runtime["server_raw_os"], observed.runtime["server_raw_architecture"]),
                         (_SERVER["Os"], _SERVER["Arch"]))
        self.assertEqual((observed.runtime["cgroup_version"], observed.runtime["cgroup_driver"]),
                         (_INFO["CgroupVersion"], _INFO["CgroupDriver"]))

    def test_only_the_admitting_inspector_refuses_capability_and_it_names_the_refusal(self):
        info = {**_INFO, "SwapLimit": False}
        observed, _ = self.inspect(authority.inspect_observed_runtime, _answers(pinned=False, info=info))
        self.assertFalse(observed.runtime["swap_limit"])
        with self.assertRaises(authority.GenericQualificationError) as expected:
            authority.validate_runtime(observed.runtime)
        with self.assertRaises(authority.CapabilityRefused) as caught:
            self.inspect(authority.inspect_current_runtime, _answers(pinned=False, info=info))
        self.assertEqual(str(caught.exception), str(expected.exception))
        admitted, _ = self.inspect(authority.inspect_current_runtime, _answers(pinned=False))
        self.assertEqual(admitted.runtime, authority.validate_runtime(admitted.runtime))
        pinned, _ = self.inspect(authority.inspect_pinned_runtime, _answers(pinned=True, info=info))
        self.assertFalse(pinned.runtime["swap_limit"])

class HistoricalResolutionTests(unittest.TestCase):
    def setUp(self):
        common._loaded_release = None
        self.addCleanup(lambda: setattr(common, "_loaded_release", None))
        self.record = qualification_record()
        self.backend = generic_spike.GenericQualifiedBackend(
            "docker", "docker-isolated-v1", self.record["qualification_record_digest"],
            self.record["execution_binding_digest"])

    def resolver(self, observed=None, record=None):
        return authority.GenericAuthorityResolver(
            FakeStore(record or self.record),
            runtime_inspector=lambda seed, deadline: authority.GenericRuntimeInspection(
                copy.deepcopy((record or self.record)["runtime"] if observed is None else observed)),
            implementation_inspector=lambda: {"implementation_digest": D0})

    def reobserved(self, daemon_id=None, record=None):
        changed = copy.deepcopy((record or self.record)["runtime"])
        if daemon_id is not None:
            changed["daemon_id"] = daemon_id
        return changed

    def test_reobserves_exact_semantic_authority_but_tolerates_patch_diagnostics(self):
        changed = copy.deepcopy(self.record["runtime"])
        changed.update(client_version="28", server_version="28", cgroup_driver="other")
        resolved = self.resolver(changed).resolve(
            self.backend, deadline=time.monotonic() + 10)
        self.assertEqual(resolved.engine_ncpu, 8)
        self.assertEqual(resolved.architecture, "amd64")
        self.assertEqual(dict(resolved.environment)["DOCKER_HOST"],
                         "unix:///var/run/docker.sock")

    def test_platform_drift_raises_the_distinct_daemon_drift_type(self):
        changed = copy.deepcopy(self.record["runtime"])
        changed["server_raw_architecture"] = "arm64"
        with self.assertRaises(authority.DaemonDrift) as caught:
            self.resolver(changed).resolve(self.backend, deadline=time.monotonic() + 10)
        self.assertEqual(str(caught.exception), "Docker daemon authority drifted")
        self.assertIsInstance(caught.exception, authority.GenericQualificationError)

    def test_daemon_id_is_compared_only_when_the_bound_record_carries_it(self):
        # A legacy `/2` record without a daemon id stays valid although the live daemon reports one.
        resolved = self.resolver(self.reobserved(daemon_id="another-daemon-identity")).resolve(
            self.backend, deadline=time.monotonic() + 10)
        self.assertEqual(resolved.engine_ncpu, 8)
        # A record that bound a daemon id refutes a differing current one with DaemonDrift.
        bound = qualification_record()
        bound["runtime"]["daemon_id"] = "bound-daemon-identity"
        backend = generic_spike.GenericQualifiedBackend(
            "docker", "docker-isolated-v1", bound["qualification_record_digest"],
            bound["execution_binding_digest"])
        # The bound daemon id matching the observed one stays valid.
        self.resolver(
            self.reobserved(daemon_id="bound-daemon-identity", record=bound),
            record=bound).resolve(backend, deadline=time.monotonic() + 10)
        changed = {**self.reobserved(daemon_id="another-daemon-identity", record=bound),
                   "daemon_id": "a-different-daemon-identity"}
        with self.assertRaises(authority.DaemonDrift) as caught:
            self.resolver(changed, record=bound).resolve(backend, deadline=time.monotonic() + 10)
        self.assertEqual(str(caught.exception), "Docker daemon authority drifted")

    def test_loaded_release_is_frozen_per_process_not_per_resolver(self):
        class ReleasesLanded:
            def __init__(self, *values):
                self.values = list(values)

            def __call__(self):
                return {"implementation_digest": self.values.pop(0)}

        def fresh_record(implementation):
            bound = copy.deepcopy(self.record)
            bound["implementation_digest"] = implementation
            backend = generic_spike.GenericQualifiedBackend(
                "docker", "docker-isolated-v1", bound["qualification_record_digest"],
                bound["execution_binding_digest"])
            return bound, backend

        # Composition freezes the digest of the release this process is actually executing.
        authority.GenericAuthorityResolver(
            FakeStore(self.record),
            runtime_inspector=lambda seed, deadline: authority.GenericRuntimeInspection(
                copy.deepcopy(self.record["runtime"])),
            implementation_inspector=ReleasesLanded(D0, D0)).resolve(
            self.backend, deadline=time.monotonic() + 10)
        # A new release lands on disk and a record is published for those new bytes; the still
        # executing old release must refuse it, even for a resolver reconstructed now.
        record, backend = fresh_record(D1)
        with self.assertRaises(authority.ImplementationDrift) as caught:
            authority.GenericAuthorityResolver(
                FakeStore(record),
                runtime_inspector=lambda seed, deadline: authority.GenericRuntimeInspection(
                    copy.deepcopy(record["runtime"])),
                implementation_inspector=ReleasesLanded(D1, D1, D1)).resolve(
                backend, deadline=time.monotonic() + 10)
        self.assertEqual(str(caught.exception), "implementation identity drifted")

    def test_injected_loaded_release_overrides_and_validates_the_process_freeze(self):
        # An injected release that does not match string shape fails closed at construction.
        for index, malformed in enumerate((123, True, "")):
            with self.subTest(index=index), self.assertRaises(
                    authority.GenericQualificationError) as caught:
                authority.GenericAuthorityResolver(
                    FakeStore(self.record),
                    runtime_inspector=lambda seed, deadline: authority.GenericRuntimeInspection(
                        copy.deepcopy(self.record["runtime"])),
                    implementation_inspector=lambda: {"implementation_digest": D0},
                    loaded_release=malformed)
            self.assertEqual(str(caught.exception), "loaded release digest is malformed")
        # A composition-frozen other release does not override the injected one: the resolver
        # compares the injected release with the record and the disk, and accepts this record.
        common.freeze_loaded_release(lambda: {"implementation_digest": D1})
        resolved = authority.GenericAuthorityResolver(
            FakeStore(self.record),
            runtime_inspector=lambda seed, deadline: authority.GenericRuntimeInspection(
                copy.deepcopy(self.record["runtime"])),
            implementation_inspector=lambda: {"implementation_digest": D0},
            loaded_release=D0).resolve(self.backend, deadline=time.monotonic() + 10)
        self.assertEqual(resolved.engine_ncpu, 8)

    def test_implementation_release_digest_shape_is_enforced(self):
        self.assertEqual(authority.implementation_release_digest(
            {"implementation_digest": D0}), D0)
        for malformed in (None, [()], 123, {}, {"implementation_digest": 1}):
            with self.assertRaises(authority.ImplementationDrift) as caught:
                authority.implementation_release_digest(malformed)
            self.assertEqual(str(caught.exception), "implementation identity drifted")

    def test_implementation_drift_combinations_raise_the_distinct_type(self):
        self.assertTrue(issubclass(authority.DaemonDrift, authority.GenericQualificationError))
        self.assertTrue(issubclass(authority.ImplementationDrift, authority.GenericQualificationError))

        class Released:
            def __init__(self, *values):
                self.values = list(values)

            def __call__(self):
                return {"implementation_digest": self.values.pop(0)}

        # The loaded release drifted on disk after the resolver was composed.
        resolver = authority.GenericAuthorityResolver(
            FakeStore(self.record),
            runtime_inspector=lambda seed, deadline: authority.GenericRuntimeInspection(
                copy.deepcopy(self.record["runtime"])),
            implementation_inspector=Released(D0, D1))
        with self.assertRaises(authority.ImplementationDrift) as caught:
            resolver.resolve(self.backend, deadline=time.monotonic() + 10)
        self.assertEqual(str(caught.exception), "implementation identity drifted")
        # The loaded release never matched the bound record.
        resolver = authority.GenericAuthorityResolver(
            FakeStore(self.record),
            runtime_inspector=lambda seed, deadline: authority.GenericRuntimeInspection(
                copy.deepcopy(self.record["runtime"])),
            implementation_inspector=Released(D1, D1))
        with self.assertRaises(authority.ImplementationDrift) as caught:
            resolver.resolve(self.backend, deadline=time.monotonic() + 10)
        self.assertEqual(str(caught.exception), "implementation identity drifted")

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
