#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins" / "empirica"))

from adapters.sandbox import docker, environment, record  # noqa: E402
from adapters.sandbox import cli as qualification  # noqa: E402


def operation_id(label: str) -> str:
    return "o-" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:32]


class DiscoveryRunner:
    def __init__(self, *, context="desktop-linux", endpoint="unix:///tmp/docker.sock",
                 repo_digest=None, image_arch="arm64",
                 server_os="linux", server_arch="arm64", malformed=None):
        self.context = context
        self.endpoint = endpoint
        self.repo_digest = repo_digest or docker.IMAGE_REPO_DIGEST
        self.image_arch = image_arch
        self.server_os = server_os
        self.server_arch = server_arch
        self.malformed = malformed
        self.calls = []

    def run(self, argv, *, timeout_seconds, output_limit_bytes, env=None):
        self.calls.append((tuple(argv), timeout_seconds, output_limit_bytes, dict(env or {})))
        args = tuple(argv[1:])
        if args == ("context", "show"):
            return docker.CommandResult.fake(stdout=self.context + "\n")
        if args[:2] == ("context", "inspect") and args[2] == self.context:
            return docker.CommandResult.fake(stdout="{}" if self.malformed == "context" else json.dumps(self.endpoint))
        if args[:2] == ("version", "--format"):
            if self.malformed == "version":
                return docker.CommandResult.fake(stdout="{}")
            return docker.CommandResult.fake(stdout=json.dumps({
                "Client": {"Version": "29.1.2"},
                "Server": {"Version": "29.1.2", "Os": self.server_os, "Arch": self.server_arch},
            }))
        if args[:2] == ("info", "--format"):
            return docker.CommandResult.fake(stdout=json.dumps({
                "SecurityOptions": ["name=seccomp,profile=builtin", "name=cgroupns"],
                "CgroupVersion": "2", "CgroupDriver": "cgroupfs",
                "KernelVersion": "6.10.0-linuxkit", "OperatingSystem": "Docker Desktop",
            }))
        if args[:3] == ("image", "inspect", docker.IMAGE_REPO_DIGEST):
            if self.malformed == "image":
                return docker.CommandResult.fake(stdout="{}")
            return docker.CommandResult.fake(stdout=json.dumps({
                "RepoDigests": [self.repo_digest],
                "Id": "sha256:" + "1" * 64,
                "Os": "linux",
                "Architecture": self.image_arch,
            }))
        raise AssertionError(f"unexpected discovery call: {argv}")


def fake_cli(root: Path) -> Path:
    path = root / "docker"
    path.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    path.chmod(0o755)
    return path.resolve(strict=True)


def healthy_environment(root: Path, *, version="3.11.9") -> environment.EnvironmentIdentity:
    executable = fake_cli(root)
    module_file = root / "module.py"
    module_file.write_text("# module\n", encoding="utf-8")
    module_file = module_file.resolve(strict=True)
    version_parts = [int(item) for item in version.split(".")]
    return environment.EnvironmentIdentity(
        environment.ENVIRONMENT_SCHEMA, True, (),
        {
            "implementation": "CPython", "version": version,
            "version_info": version_parts,
            "cache_tag": f"cpython-{version_parts[0]}{version_parts[1]}",
            "executable": str(executable),
            "executable_sha256": docker._file_digest(executable),
            "os_name": "posix", "host_os": "Darwin", "host_arch": "arm64",
        },
        {key: True for key in environment.CAPABILITY_KEYS},
        {"jsonschema": {"version": "4.20.0", "module_origin": str(module_file),
                        "draft202012": True, "validator_for": True}},
        tuple({"module": name, "origin": environment.EXPECTED_IMPORT_ORIGINS[name]}
              for name in environment.INTERNAL_IMPORTS),
        {"git": {"required_for_sandbox": False, "checked": False,
                 "reason": "Git identity is owned by Empirica snapshot capture, not Docker isolation"}},
        dict(environment.PROBE_POLICY),
    )


class EnvironmentPreflightTests(unittest.TestCase):
    def facts(self, root):
        return healthy_environment(root).python

    def importer(self, root, *, fail=None, jsonschema=True):
        module_file = root / "imported.py"
        module_file.write_text("# imported\n", encoding="utf-8")
        draft = type("Draft202012Validator", (), {})
        modules = {name: SimpleNamespace(__file__=environment.EXPECTED_IMPORT_ORIGINS[name])
                   for name in environment.INTERNAL_IMPORTS}
        modules["jsonschema"] = SimpleNamespace(
            __file__=str(module_file), Draft202012Validator=draft,
            validators=SimpleNamespace(validator_for=lambda _schema: draft))
        def load(name):
            if name == fail or (name == "jsonschema" and not jsonschema):
                raise ModuleNotFoundError(name)
            return modules[name]
        return load

    def test_healthy_environment_is_structured_and_available(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            identity = environment.inspect_environment(
                python_facts=self.facts(root),
                capability_facts={key: True for key in environment.CAPABILITY_KEYS},
                importer=self.importer(root), distribution_version=lambda _name: "4.20.0")
        self.assertTrue(identity.available, identity.reasons)
        self.assertEqual([row["module"] for row in identity.import_smoke],
                         list(environment.INTERNAL_IMPORTS))

    def test_missing_jsonschema_and_bridge_import_are_structured(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            missing_dependency = environment.inspect_environment(
                python_facts=self.facts(root),
                capability_facts={key: True for key in environment.CAPABILITY_KEYS},
                importer=self.importer(root, jsonschema=False),
                distribution_version=lambda _name: "4.20.0")
            missing_bridge = environment.inspect_environment(
                python_facts=self.facts(root),
                capability_facts={key: True for key in environment.CAPABILITY_KEYS},
                importer=self.importer(root, fail="adapters.bridge"),
                distribution_version=lambda _name: "4.20.0")
        self.assertFalse(missing_dependency.available)
        self.assertIn("bounded dependency/import preflight failed: jsonschema",
                      missing_dependency.reasons)
        self.assertFalse(missing_bridge.available)
        self.assertIn("bounded dependency/import preflight failed: adapters.bridge",
                      missing_bridge.reasons)

    def test_bounded_child_timeout_and_malformed_output_are_structured(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            def timeout(_executable):
                raise TimeoutError("bounded child")
            timed_out = environment.inspect_environment(
                python_facts=self.facts(root),
                capability_facts={key: True for key in environment.CAPABILITY_KEYS},
                child_probe=timeout)
            malformed = environment.inspect_environment(
                python_facts=self.facts(root),
                capability_facts={key: True for key in environment.CAPABILITY_KEYS},
                child_probe=lambda _executable: {"unexpected": True})
        self.assertFalse(timed_out.available)
        self.assertIn("bounded dependency/import preflight failed: bounded child", timed_out.reasons)
        self.assertFalse(malformed.available)
        self.assertIn("bounded dependency/import preflight failed: import probe document is not closed",
                      malformed.reasons)

    def test_shadowed_internal_origin_is_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            identity = asdict(healthy_environment(root))
            identity["import_smoke"][0]["origin"] = identity["dependencies"]["jsonschema"]["module_origin"]
            with self.assertRaises(environment.EnvironmentPreflightError):
                environment.validate_environment_identity(identity)

    def test_python_floor_implementation_and_capabilities_fail_independently(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            for key, value in (("version_info", [3, 9, 9]), ("implementation", "PyPy")):
                facts = dict(self.facts(root))
                facts[key] = value
                identity = environment.inspect_environment(
                    python_facts=facts,
                    capability_facts={name: True for name in environment.CAPABILITY_KEYS},
                    importer=self.importer(root), distribution_version=lambda _name: "4.20.0")
                self.assertFalse(identity.available)
            capabilities = {name: True for name in environment.CAPABILITY_KEYS}
            capabilities["o_nofollow"] = False
            identity = environment.inspect_environment(
                python_facts=self.facts(root), capability_facts=capabilities,
                importer=self.importer(root), distribution_version=lambda _name: "4.20.0")
        self.assertFalse(identity.available)
        self.assertIn("required POSIX sandbox capabilities are unavailable", identity.reasons)


class DiscoveryTests(unittest.TestCase):
    def test_passive_discovery_binds_absolute_cli_endpoint_and_both_image_identities(self):
        with tempfile.TemporaryDirectory() as raw:
            cli = fake_cli(Path(raw))
            runner = DiscoveryRunner()
            expected_digest = "sha256:" + hashlib.sha256(cli.read_bytes()).hexdigest()
            identity = docker.discover_docker(
                runner, cli_path=str(cli), env={"PATH": str(cli.parent)})
        self.assertTrue(identity.available, identity.reasons)
        self.assertEqual(identity.docker_cli, str(cli))
        self.assertEqual(identity.docker_cli_sha256, expected_digest)
        self.assertEqual(identity.endpoint, "unix:///tmp/docker.sock")
        self.assertEqual(identity.image_repo_digest, docker.IMAGE_REPO_DIGEST)
        self.assertEqual(identity.image_config_id, "sha256:" + "1" * 64)
        self.assertEqual(len(runner.calls), 5)
        self.assertTrue(all(call[1] == docker.CONTROL_TIMEOUT_SECONDS for call in runner.calls))
        self.assertTrue(all(call[2] == docker.CONTROL_OUTPUT_LIMIT_BYTES for call in runner.calls))

    def test_preflight_is_exactly_five_discovery_reads_and_never_mutates(self):
        with tempfile.TemporaryDirectory() as raw:
            cli = fake_cli(Path(raw))
            runner = DiscoveryRunner()
            result = docker.qualify_docker(
                "preflight", runner=runner, cli_path=str(cli),
                env={"PATH": str(cli.parent), "SECRET_TOKEN": "must-not-be-forwarded"},
                environment_inspector=lambda: healthy_environment(Path(raw)))
        verbs = [call[0][1:] for call in runner.calls]
        self.assertEqual(len(verbs), 5)
        self.assertEqual(verbs[0], ("context", "show"))
        self.assertEqual([args[0] for args in verbs],
                         ["context", "context", "version", "info", "image"])
        self.assertFalse(any(word in args for args in verbs
                             for word in ("create", "start", "rm", "remove", "network")))
        self.assertTrue(result["passed"])
        self.assertTrue(result["selected_probe_passed"])
        self.assertFalse(result["cleanup_verified"])
        self.assertIsNone(result["census_before"])
        self.assertIsNone(result["census_after"])
        self.assertFalse(result["complete_qualification"])
        self.assertFalse(result["docker_backend_eligible"])
        self.assertTrue(result["environment_preflight_available"])

    def test_endpoint_precedence_covers_context_only_host_only_and_both_conflict(self):
        with tempfile.TemporaryDirectory() as raw:
            cli = fake_cli(Path(raw))
            context_only = docker.discover_docker(
                DiscoveryRunner(context="desktop-linux", endpoint="ssh://remote-context"),
                cli_path=str(cli), env={"PATH": str(cli.parent), "DOCKER_CONTEXT": "desktop-linux"})
            host_only = docker.discover_docker(
                DiscoveryRunner(context="default", endpoint="unix:///default.sock"),
                cli_path=str(cli), env={"PATH": str(cli.parent), "DOCKER_HOST": "ssh://remote-host"})
            sticky_context_host_override = docker.discover_docker(
                DiscoveryRunner(context="desktop-linux", endpoint="unix:///sticky-context.sock"),
                cli_path=str(cli), env={"PATH": str(cli.parent), "DOCKER_HOST": "ssh://remote-host"})
            both_local_context = docker.discover_docker(
                DiscoveryRunner(context="desktop-linux", endpoint="unix:///context.sock"),
                cli_path=str(cli), env={"PATH": str(cli.parent), "DOCKER_CONTEXT": "desktop-linux",
                                             "DOCKER_HOST": "ssh://ignored-host"})
            both_remote_context = docker.discover_docker(
                DiscoveryRunner(context="desktop-linux", endpoint="ssh://effective-context"),
                cli_path=str(cli), env={"PATH": str(cli.parent), "DOCKER_CONTEXT": "desktop-linux",
                                             "DOCKER_HOST": "unix:///ignored-host.sock"})
        self.assertFalse(context_only.available)
        self.assertEqual(context_only.endpoint, "ssh://remote-context")
        self.assertFalse(host_only.available)
        self.assertEqual(host_only.endpoint, "ssh://remote-host")
        self.assertFalse(sticky_context_host_override.available)
        self.assertEqual(sticky_context_host_override.endpoint, "ssh://remote-host")
        self.assertTrue(both_local_context.available, both_local_context.reasons)
        self.assertEqual(both_local_context.endpoint, "unix:///context.sock")
        self.assertFalse(both_remote_context.available)
        self.assertEqual(both_remote_context.endpoint, "ssh://effective-context")

    def test_remote_effective_docker_host_is_rejected_even_when_context_is_local(self):
        with tempfile.TemporaryDirectory() as raw:
            cli = fake_cli(Path(raw))
            identity = docker.discover_docker(
                DiscoveryRunner(context="default"), cli_path=str(cli),
                env={"PATH": str(cli.parent), "DOCKER_HOST": "ssh://remote.example"})
        self.assertFalse(identity.available)
        self.assertIn("effective Docker endpoint is not a local Unix socket", identity.reasons)

    def test_non_linux_server_and_image_arch_mismatch_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            cli = fake_cli(Path(raw))
            identity = docker.discover_docker(
                DiscoveryRunner(server_os="windows", image_arch="amd64"),
                cli_path=str(cli), env={"PATH": str(cli.parent)})
        self.assertFalse(identity.available)
        self.assertIn("Docker engine is not running Linux containers", identity.reasons)
        self.assertIn("pinned Docker image repo digest/config/platform identity mismatches", identity.reasons)

    def test_repo_digest_is_not_confused_with_config_id(self):
        with tempfile.TemporaryDirectory() as raw:
            cli = fake_cli(Path(raw))
            identity = docker.discover_docker(
                DiscoveryRunner(repo_digest="rust@sha256:" + "2" * 64),
                cli_path=str(cli), env={"PATH": str(cli.parent)})
        self.assertFalse(identity.available)
        self.assertIn("pinned Docker image repo digest/config/platform identity mismatches", identity.reasons)

    def test_malformed_context_version_and_image_json_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            cli = fake_cli(Path(raw))
            for malformed, expected in (
                ("context", "selected Docker context/endpoint is unavailable or malformed"),
                ("version", "Docker client/server identity is unavailable or malformed"),
                ("image", "pinned Docker image repo digest/config/platform identity mismatches"),
            ):
                with self.subTest(malformed=malformed):
                    identity = docker.discover_docker(
                        DiscoveryRunner(malformed=malformed), cli_path=str(cli),
                        env={"PATH": str(cli.parent)})
                    self.assertFalse(identity.available)
                    self.assertIn(expected, identity.reasons)

    def test_missing_cli_fails_without_runner_calls(self):
        runner = DiscoveryRunner()
        identity = docker.discover_docker(runner, cli_path=None, env={"PATH": ""})
        self.assertFalse(identity.available)
        self.assertEqual(identity.reasons, ("docker CLI is unavailable",))
        self.assertEqual(runner.calls, [])


class PolicyTests(unittest.TestCase):
    def identity(self, cli: Path) -> docker.DockerRuntimeIdentity:
        return docker.DockerRuntimeIdentity(
            docker.SCHEMA_VERSION, docker.BACKEND, True, "Darwin", "x", "arm64",
            str(cli), "sha256:" + "a" * 64, "desktop-linux", "unix:///tmp/docker.sock",
            "29.1.2", "29.1.2", "linux", "arm64",
            ("name=cgroupns", "name=seccomp,profile=builtin"), "2", "cgroupfs",
            "6.10.0-linuxkit", "Docker Desktop", True,
            docker.IMAGE_REPO_DIGEST,
            "sha256:" + "1" * 64, "linux", "arm64", (),
        )

    def test_effective_policy_binds_runtime_identity_and_every_limit(self):
        with tempfile.TemporaryDirectory() as raw:
            cli = fake_cli(Path(raw))
            policy = docker.effective_policy(self.identity(cli))
        self.assertEqual(policy["runtime_identity"]["docker_cli"], str(cli))
        self.assertEqual(policy["runtime_identity"]["image_config_id"], "sha256:" + "1" * 64)
        self.assertEqual(policy["isolation"]["work_tmpfs"]["size_bytes"], 64 * 1024 * 1024)
        self.assertEqual(policy["isolation"]["tmp_tmpfs"]["size_bytes"], 16 * 1024 * 1024)
        self.assertEqual(policy["resources"]["ulimits"]["fsize"],
                         [docker.FSIZE_LIMIT, docker.FSIZE_LIMIT])
        self.assertEqual(policy["bounds"]["workload_output_limit_bytes"],
                         docker.WORKLOAD_OUTPUT_LIMIT_BYTES)
        self.assertEqual(policy["bounds"]["control_timeout_seconds"], 5.0)
        self.assertEqual(policy["bounds"]["create_inspect_timeout_seconds"], 10.0)
        self.assertEqual(policy["bounds"]["postflight_timeout_seconds"], 15.0)
        self.assertEqual(policy["bounds"]["operation_timeout_seconds"], 210.0)
        self.assertEqual(policy["container_lifecycle"]["remove_volumes"], False)
        self.assertRegex(docker.policy_digest(policy), r"^sha256:[0-9a-f]{64}$")

    def test_post_discovery_client_uses_only_closed_endpoint_bound_environment(self):
        with tempfile.TemporaryDirectory() as raw:
            cli = fake_cli(Path(raw))
            identity = self.identity(cli)
            runner = DiscoveryRunner()
            client = docker.DockerClient(
                identity, docker.effective_policy(identity), runner,
                env={"PATH": "/host/bin", "DOCKER_HOST": "ssh://wrong",
                     "DOCKER_CONTEXT": "wrong", "DOCKER_CONFIG": "/secret",
                     "HTTPS_PROXY": "https://secret", "SECRET_TOKEN": "secret"})
            client.call(("version", "--format", "{{json .}}"))
        self.assertEqual(runner.calls[-1][3], {
            "LANG": "C", "LC_ALL": "C", "DOCKER_HOST": "unix:///tmp/docker.sock"})
        self.assertNotIn("SECRET_TOKEN", runner.calls[-1][3])
        self.assertEqual(client.policy["host_cli_environment"],
                         docker.POLICY_TEMPLATE["host_cli_environment"])

    def test_create_argv_is_absolute_pull_disabled_and_has_no_auto_remove_or_volume(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            cli = fake_cli(root)
            input_dir = root / "input"
            input_dir.mkdir()
            policy = docker.effective_policy(self.identity(cli))
            digest = docker.policy_digest(policy)
            labels = docker.qualification_labels("q123", "unit", digest)
            argv = docker.docker_create_argv(
                policy, input_dir=input_dir, name="siffran-qual-q123-001",
                labels=labels, command="exit 125")
        self.assertEqual(argv[0], str(cli))
        self.assertEqual(argv[1:3], ["container", "create"])
        self.assertIn("--pull=never", argv)
        self.assertIn("--network=none", argv)
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop=ALL", argv)
        self.assertIn("--security-opt=no-new-privileges=true", argv)
        self.assertIn("--user=65534:65534", argv)
        self.assertIn(f"--memory={docker.MEMORY_BYTES}", argv)
        self.assertIn(f"--memory-swap={docker.MEMORY_BYTES}", argv)
        self.assertIn(f"--pids-limit={docker.PIDS_LIMIT}", argv)
        self.assertNotIn("--rm", argv)
        self.assertNotIn("-v", argv)
        self.assertFalse(any("docker.sock" in arg for arg in argv))
        work_tmpfs = argv[argv.index("--tmpfs") + 1]
        self.assertIn("/work:rw,exec,nosuid,nodev", work_tmpfs)
        self.assertIn("/usr/bin/env", argv)
        self.assertIn("-i", argv)
        self.assertEqual(argv[-3:], ["/bin/sh", "-c", "exit 125"])

    def test_policy_mutation_changes_argv_and_inspect_expectations_without_digest_divergence(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            cli = fake_cli(root)
            input_dir = root / "input"
            input_dir.mkdir()
            identity = self.identity(cli)
            policy = docker.effective_policy(identity)
            policy["resources"]["memory_bytes"] //= 2
            policy["resources"]["memory_swap_bytes"] //= 2
            policy["process"]["environment"].append("QUALIFICATION_POLICY_TEST=yes")
            digest = docker.policy_digest(policy)
            labels = docker.qualification_labels("q123", "unit", digest)
            argv = docker.docker_create_argv(
                policy, input_dir=input_dir, name="siffran-qual-q123-001",
                labels=labels, command="true")
            self.assertIn(f"--memory={docker.MEMORY_BYTES // 2}", argv)
            self.assertIn("QUALIFICATION_POLICY_TEST=yes", argv)
            fresh = docker.effective_policy(identity)
            self.assertEqual(fresh["resources"]["memory_bytes"], docker.MEMORY_BYTES)
            self.assertNotIn("QUALIFICATION_POLICY_TEST=yes", fresh["process"]["environment"])
            runner = LifecycleRunner()
            client = docker.DockerClient(identity, policy, runner, env={})
            result = client.execute(
                run_id="q123", role="unit", input_dir=input_dir, command="true")
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(runner.removed)

    def test_policy_rejects_missing_extra_and_unsafe_fields(self):
        with tempfile.TemporaryDirectory() as raw:
            cli = fake_cli(Path(raw))
            original = docker.effective_policy(self.identity(cli))
            variants = []
            missing = json.loads(json.dumps(original))
            del missing["resources"]["pids_limit"]
            variants.append(missing)
            extra = json.loads(json.dumps(original))
            extra["isolation"]["unexpected"] = True
            variants.append(extra)
            unsafe = json.loads(json.dumps(original))
            unsafe["image"]["pull"] = "always"
            variants.append(unsafe)
            for variant in variants:
                with self.subTest(variant=variant):
                    with self.assertRaises(docker.DockerQualificationError):
                        docker.validate_policy(variant)

    def test_mount_rejects_symlink_spelling(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            cli = fake_cli(root)
            real = root / "real"
            real.mkdir()
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            policy = docker.effective_policy(self.identity(cli))
            labels = docker.qualification_labels("q123", "unit", docker.policy_digest(policy))
            with self.assertRaises(docker.DockerQualificationError):
                docker.docker_create_argv(
                    policy, input_dir=alias, name="siffran-qual-q123-001",
                    labels=labels, command="true")


class BoundedRunnerTests(unittest.TestCase):
    def test_runner_limits_streaming_output_without_buffering_the_whole_stream(self):
        result = docker.BoundedRunner().run(
            (sys.executable, "-c", "import sys; sys.stdout.write('x'*1000000)"),
            timeout_seconds=5, output_limit_bytes=4096)
        self.assertTrue(result.output_limited)
        self.assertGreater(result.stdout.bytes_seen, 4096)
        self.assertLessEqual(len(result.stdout.data), 4096)
        self.assertRegex(result.stdout.sha256, r"^sha256:[0-9a-f]{64}$")

    def _assert_process_gone(self, pid: int) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        self.fail(f"descendant process {pid} survived bounded runner cleanup")

    def test_runner_timeout_kills_descendant_process_group(self):
        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "child.pid"
            script = (
                "import pathlib,subprocess,time; "
                f"p=subprocess.Popen([{sys.executable!r},'-c','import time; time.sleep(60)']); "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid)); time.sleep(60)")
            result = docker.BoundedRunner().run(
                (sys.executable, "-c", script), timeout_seconds=0.1, output_limit_bytes=4096)
            child_pid = int(pid_path.read_text())
        self.assertTrue(result.timed_out)
        self._assert_process_gone(child_pid)

    def test_runner_output_limit_kills_descendant_process_group(self):
        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "child.pid"
            child = "import os,time; [(os.write(1,b\"x\"*4096),time.sleep(.001)) for _ in range(10000)]"
            script = (
                "import pathlib,subprocess,time; "
                f"p=subprocess.Popen([{sys.executable!r},'-c',{child!r}]); "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid)); time.sleep(60)")
            result = docker.BoundedRunner().run(
                (sys.executable, "-c", script), timeout_seconds=5, output_limit_bytes=4096)
            child_pid = int(pid_path.read_text())
        self.assertTrue(result.output_limited)
        self._assert_process_gone(child_pid)

    def test_runner_owns_timeout_and_process_group_kill(self):
        result = docker.BoundedRunner().run(
            (sys.executable, "-c", "import time; time.sleep(60)"),
            timeout_seconds=0.05, output_limit_bytes=4096)
        self.assertTrue(result.timed_out)
        self.assertLess(result.elapsed_ms, 3000)


class LifecycleRunner:
    ID = "a" * 64

    def __init__(self, *, workload_exit=0, workload_stdout=b"", workload_stderr=b"",
                 timed_out=False, output_limited=False, create_exit=0, remove_exit=0):
        self.workload_exit = workload_exit
        self.workload_stdout = workload_stdout
        self.workload_stderr = workload_stderr
        self.timed_out = timed_out
        self.output_limited = output_limited
        self.create_exit = create_exit
        self.remove_exit = remove_exit
        self.removed = False
        self.created = False
        self.started = False
        self.create_doc = None
        self.calls = []
        self.environments = []
        self.timeouts = []
        self.output_limits = []

    @staticmethod
    def _value(argv, prefix):
        return next(arg.removeprefix(prefix) for arg in argv if arg.startswith(prefix))

    def _build_doc(self, argv):
        name = argv[argv.index("--name") + 1]
        labels = {}
        for index, arg in enumerate(argv):
            if arg == "--label":
                key, value = argv[index + 1].split("=", 1)
                labels[key] = value
        mount = argv[argv.index("--mount") + 1]
        source = dict(part.split("=", 1) for part in mount.split(",") if "=" in part)["src"]
        image_index = argv.index(docker.IMAGE_REPO_DIGEST)
        command = argv[image_index + 1:]
        tmpfs = [argv[index + 1] for index, item in enumerate(argv) if item == "--tmpfs"]
        memory = int(self._value(argv, "--memory="))
        memory_swap = int(self._value(argv, "--memory-swap="))
        pids = int(self._value(argv, "--pids-limit="))
        nano_cpus = int(float(self._value(argv, "--cpus=")) * 1_000_000_000)
        ulimits = []
        for arg in argv:
            if arg.startswith("--ulimit="):
                ulimit_name, values = arg.removeprefix("--ulimit=").split("=", 1)
                soft, hard = values.split(":", 1)
                ulimits.append({"Name": ulimit_name, "Soft": int(soft), "Hard": int(hard)})
        return {
            "Id": self.ID, "Image": "sha256:" + "1" * 64, "Name": "/" + name,
            "Config": {"Labels": labels, "Image": docker.IMAGE_REPO_DIGEST,
                       "User": "65534:65534", "WorkingDir": "/input", "Cmd": command},
            "HostConfig": {
                "NetworkMode": self._value(argv, "--network="), "ReadonlyRootfs": True,
                "AutoRemove": False, "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges=true"], "NanoCpus": nano_cpus,
                "Memory": memory, "MemorySwap": memory_swap,
                "PidsLimit": pids, "ShmSize": docker.SHM_BYTES,
                "Ulimits": ulimits,
                "Init": True,
                "LogConfig": {"Type": "none"},
                "Tmpfs": {"/work": tmpfs[0].split(":", 1)[1],
                          "/tmp": tmpfs[1].split(":", 1)[1]},
            },
            "Mounts": [{"Destination": "/input", "Type": "bind", "RW": False, "Source": source}],
            "State": {"Status": "created", "Running": False, "ExitCode": 0,
                      "Error": "", "OOMKilled": False},
        }

    def run(self, argv, *, timeout_seconds, output_limit_bytes, env=None):
        argv = tuple(argv)
        self.calls.append(argv)
        self.environments.append(dict(env or {}))
        self.timeouts.append(timeout_seconds)
        self.output_limits.append(output_limit_bytes)
        args = argv[1:]
        if args[:2] == ("container", "create"):
            if self.create_exit:
                return docker.CommandResult.fake(self.create_exit, stderr="daemon create failure")
            self.created = True
            self.create_doc = self._build_doc(list(argv))
            return docker.CommandResult.fake(stdout=self.ID + "\n")
        if args[:3] == ("container", "ls", "--all"):
            visible = self.created and not self.removed
            filters = [args[index + 1].removeprefix("label=")
                       for index, value in enumerate(args) if value == "--filter"]
            labels = self.create_doc["Config"]["Labels"] if self.create_doc else {}
            visible = visible and all(
                "=" in item and labels.get(item.split("=", 1)[0]) == item.split("=", 1)[1]
                for item in filters)
            return docker.CommandResult.fake(stdout=(self.ID + "\n") if visible else "")
        if args[:2] == ("container", "inspect"):
            if not self.created or self.removed:
                return docker.CommandResult.fake(1, stderr="No such container")
            doc = json.loads(json.dumps(self.create_doc))
            if self.started and not self.timed_out and not self.output_limited:
                doc["State"] = {"Status": "exited", "Running": False,
                                "ExitCode": self.workload_exit, "Error": "", "OOMKilled": False}
            return docker.CommandResult.fake(stdout=json.dumps(doc))
        if args[:3] == ("container", "start", "--attach"):
            self.started = True
            return docker.CommandResult.fake(
                self.workload_exit, stdout=self.workload_stdout, stderr=self.workload_stderr,
                timed_out=self.timed_out, output_limited=self.output_limited)
        if args[:3] == ("container", "rm", "--force"):
            if self.remove_exit:
                return docker.CommandResult.fake(self.remove_exit, stderr="remove failed")
            self.removed = True
            return docker.CommandResult.fake(stdout=self.ID + "\n")
        raise AssertionError(f"unexpected lifecycle call: {argv}")


def lifecycle_client(root: Path, runner: LifecycleRunner) -> docker.DockerClient:
    cli = fake_cli(root)
    identity = PolicyTests().identity(cli)
    return docker.DockerClient(identity, docker.effective_policy(identity), runner, env={})


class LifecycleTests(unittest.TestCase):
    def execute(self, runner: LifecycleRunner):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve(strict=True)
        input_dir = root / "input"
        input_dir.mkdir()
        (input_dir / "fixture").write_text("x", encoding="utf-8")
        return lifecycle_client(root, runner).execute(
            run_id="q123", role="unit", input_dir=input_dir, command="exit 125")

    def test_inspected_workload_exit_125_is_not_misclassified_as_docker_failure(self):
        runner = LifecycleRunner(workload_exit=125, workload_stdout=b"workload")
        result = self.execute(runner)
        self.assertEqual(result.exit_code, 125)
        self.assertFalse(result.output_limited)
        self.assertTrue(runner.removed)
        self.assertEqual([call[1:3] for call in runner.calls], [
            ("container", "create"), ("container", "inspect"),
            ("container", "start"), ("container", "inspect"),
            ("container", "inspect"), ("container", "rm"),
            ("container", "inspect"), ("container", "ls"), ("container", "ls"),
        ])
        self.assertTrue(runner.environments)
        self.assertTrue(all(value == {
            "LANG": "C", "LC_ALL": "C", "DOCKER_HOST": "unix:///tmp/docker.sock"}
                            for value in runner.environments))

    def test_create_exit_125_is_infrastructure_and_never_a_workload_verdict(self):
        runner = LifecycleRunner(create_exit=125)
        with self.assertRaises(docker.DockerInfrastructureError):
            self.execute(runner)
        self.assertFalse(runner.started)

    def test_parent_timeout_gets_synthetic_124_and_removes_exact_owned_container(self):
        runner = LifecycleRunner(timed_out=True)
        result = self.execute(runner)
        self.assertEqual(result.exit_code, docker.TIMEOUT_EXIT_CODE)
        self.assertTrue(result.timed_out)
        self.assertTrue(runner.removed)

    def test_stream_limit_gets_synthetic_125_and_removes_exact_owned_container(self):
        runner = LifecycleRunner(output_limited=True, workload_stdout=b"x" * 5000)
        result = self.execute(runner)
        self.assertEqual(result.exit_code, docker.OUTPUT_LIMIT_EXIT_CODE)
        self.assertTrue(result.output_limited)
        self.assertTrue(runner.removed)

    def test_cleanup_failure_overrides_apparent_workload_success(self):
        runner = LifecycleRunner(remove_exit=1)
        with self.assertRaises(docker.DockerCleanupError):
            self.execute(runner)

    def test_timeout_and_output_overrides_may_only_narrow_policy(self):
        runner = LifecycleRunner()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve(strict=True)
        input_dir = root / "input"
        input_dir.mkdir()
        client = lifecycle_client(root, runner)
        with self.assertRaises(docker.DockerQualificationError):
            client.execute(run_id="q123", role="unit", input_dir=input_dir, command="true",
                           timeout_seconds=docker.WORKLOAD_TIMEOUT_SECONDS + 1)
        with self.assertRaises(docker.DockerQualificationError):
            client.execute(run_id="q123", role="unit", input_dir=input_dir, command="true",
                           output_limit_bytes=docker.WORKLOAD_OUTPUT_LIMIT_BYTES + 1)
        self.assertEqual(runner.calls, [])


    def test_orphan_recovery_skips_mismatched_identity_and_cleanup_fails_closed(self):
        class OrphanRunner(LifecycleRunner):
            def run(inner, argv, *, timeout_seconds, output_limit_bytes, env=None):
                args = tuple(argv[1:])
                if args[:3] == ("container", "ls", "--all"):
                    inner.calls.append(tuple(argv))
                    return docker.CommandResult.fake(stdout=inner.ID + "\n")
                return super(OrphanRunner, inner).run(
                    argv, timeout_seconds=timeout_seconds,
                    output_limit_bytes=output_limit_bytes, env=env)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            input_dir = root / "input"
            input_dir.mkdir()
            runner = OrphanRunner()
            client = lifecycle_client(root, runner)
            client.create_container(
                run_id="q123", role="interrupted", input_dir=input_dir, command="true")
            runner.create_doc["Config"]["Labels"][docker.SCHEMA_LABEL] = "wrong"
            recovered = client.recover_orphans()
        self.assertEqual(recovered, ())
        self.assertFalse(runner.removed)
        residue = docker.Census((runner.ID,), (runner.ID,), (), ())
        self.assertFalse(docker.cleanup_matches(residue, residue, ()))


class RecordTests(unittest.TestCase):
    def setUp(self):
        self._environment_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._environment_temp.cleanup)
        self.expected_environment = healthy_environment(
            Path(self._environment_temp.name).resolve(strict=True))
        self._environment_patch = patch.object(
            environment, "inspect_environment", return_value=self.expected_environment)
        self._environment_patch.start()
        self.addCleanup(self._environment_patch.stop)

    def test_sandbox_home_is_independent_absolute_canonical_and_explicitly_overridable(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            override = root / "sandbox-authority"
            with patch.dict(os.environ, {
                "EMPIRICA_HOME": str(root / "evidence-home"),
                record.SANDBOX_HOME_ENV: str(override),
            }, clear=False):
                self.assertEqual(record.sandbox_home(), override)
            with patch.dict(os.environ, {record.SANDBOX_HOME_ENV: "relative/home"}, clear=False):
                with self.assertRaises(record.QualificationRecordError):
                    record.sandbox_home()
            real = root / "real-home"
            real.mkdir(mode=0o700)
            alias = root / "alias-home"
            alias.symlink_to(real, target_is_directory=True)
            with patch.dict(os.environ, {record.SANDBOX_HOME_ENV: str(alias)}, clear=False):
                with self.assertRaises(record.QualificationRecordError):
                    record.sandbox_home()
            with self.assertRaises(record.QualificationRecordError):
                record.QualificationStore(Path("relative/home"))
            with self.assertRaises(record.QualificationRecordError):
                record.QualificationStore(root / "part" / ".." / "home")
        with patch.dict(os.environ, {"EMPIRICA_HOME": "/tmp/evidence-home"}, clear=False):
            os.environ.pop(record.SANDBOX_HOME_ENV, None)
            self.assertEqual(record.sandbox_home(), Path.home() / ".empirica-plugin" / "sandbox")

    def result(self, root: Path, *, generated_at="now", complete=True,
               environment_identity=None):
        cli = fake_cli(root)
        identity = docker.DockerRuntimeIdentity(
            docker.SCHEMA_VERSION, docker.BACKEND, True,
            platform.system(), platform.release(), platform.machine(),
            str(cli), docker._file_digest(cli), "desktop-linux", "unix:///tmp/docker.sock",
            "29.1.2", "29.1.2", "linux", platform.machine(),
            ("name=cgroupns", "name=seccomp,profile=builtin"), "2", "cgroupfs",
            "6.10.0-linuxkit", "Docker Desktop", True,
            docker.IMAGE_REPO_DIGEST, "sha256:" + "1" * 64,
            "linux", platform.machine(), (),
        )
        policy = docker.effective_policy(identity)
        probes = [
            {"name": name, "passed": True, "elapsed_ms": 1, "details": {"probe": name}}
            for name in docker.MANDATORY_PROBES
        ]
        return {
            "requested_probe": "all", "complete_qualification": complete,
            "passed": complete, "cleanup_verified": complete,
            "docker_backend_eligible": complete, "qualified_for_untrusted_code": complete,
            "empirica_contract_compatible": complete, "fallback_eligible": False,
            "preflight": asdict(identity),
            "environment_preflight": asdict(
                environment_identity or getattr(self, "expected_environment", healthy_environment(root))),
            "environment_preflight_available": True,
            "policy": policy, "policy_sha256": docker.policy_digest(policy),
            "probes": probes, "generated_at": generated_at,
        }

    @staticmethod
    def reports(root: Path):
        json_report, markdown = root / "report.json", root / "report.md"
        json_report.write_text("{}\n", encoding="utf-8")
        markdown.write_text("# report\n", encoding="utf-8")
        return json_report, markdown

    def test_record_digests_are_canonical_and_report_deletion_is_non_authoritative(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            reports = self.reports(root)
            authority = record.build_record(self.result(root), report_paths=reports)
            checked = record.validate_record(authority)
            for path in reports:
                path.unlink()
            checked_without_reports = record.validate_record(authority)
        self.assertEqual(checked, checked_without_reports)
        self.assertRegex(authority["profile"]["profile_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(authority["runtime_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(authority["implementation"]["implementation_digest"],
                         r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(authority["execution_binding_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(authority["qualification_record_digest"], r"^sha256:[0-9a-f]{64}$")

    def test_identical_requalification_and_current_pointer_change_do_not_stale_old_record(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            home = root / "sandbox"
            first = record.build_record(self.result(root, generated_at="one"))
            second = record.build_record(self.result(root, generated_at="two"))
            store = record.QualificationStore(home)
            store.publish(first)
            old_binding = store.load_current()["execution_binding_digest"]
            store.publish(second)
            current = store.load_current()
            old = store.load_record(first["qualification_record_digest"])
        self.assertEqual(first["execution_binding_digest"], second["execution_binding_digest"])
        self.assertNotEqual(first["qualification_record_digest"], second["qualification_record_digest"])
        self.assertEqual(old_binding, old["execution_binding_digest"])
        self.assertEqual(current["qualification_record_digest"], second["qualification_record_digest"])

    def test_current_pointer_publication_is_atomic_under_competing_complete_records(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            store = record.QualificationStore(root / "sandbox")
            store.prepare()
            first = record.build_record(self.result(root, generated_at="one"))
            second = record.build_record(self.result(root, generated_at="two"))
            errors = []
            def publish(value):
                try:
                    store.publish(value)
                except Exception as exc:  # pragma: no cover - asserted below
                    errors.append(exc)
            threads = [threading.Thread(target=publish, args=(value,), daemon=True)
                       for value in (first, second)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            try:
                current = store.load_current()
            except Exception as exc:  # pragma: no cover - assertion captures any torn publication
                errors.append(exc)
        self.assertEqual(errors, [])
        self.assertIn(current["qualification_record_digest"], {
            first["qualification_record_digest"], second["qualification_record_digest"]})

    def test_read_only_directory_checks_use_lstat_and_never_path_exists(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            authority = record.build_record(self.result(root))
            store = record.QualificationStore(root / "sandbox")
            store.publish(authority)
            with patch.object(Path, "exists",
                              side_effect=AssertionError("following exists predicate invoked")):
                self.assertEqual(store.load_current()["qualification_record_digest"],
                                 authority["qualification_record_digest"])
                self.assertEqual(store.read_journals(), [])

    @staticmethod
    def _redigest(authority):
        authority["runtime_digest"] = record._digest(authority["runtime"])
        binding = {
            "schema": authority["schema"],
            "qualification_protocol_revision": authority["qualification_protocol_revision"],
            "profile_digest": authority["profile"]["profile_digest"],
            "runtime_digest": authority["runtime_digest"],
            "environment_digest": authority["environment_digest"],
            "implementation_digest": authority["implementation"]["implementation_digest"],
        }
        authority["execution_binding_digest"] = record._digest(binding)
        body = {key: authority[key]
                for key in record.RECORD_KEYS - {"qualification_record_digest"}}
        authority["qualification_record_digest"] = record._digest(body)
        return authority

    def test_static_record_validation_compares_current_environment_identity(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            environment_a = healthy_environment(root, version="3.11.9")
            environment_b = healthy_environment(root, version="3.12.1")
            authority = record.build_record(self.result(root, environment_identity=environment_a))
            record.validate_record(authority, expected_environment=environment_a)
            with self.assertRaises(record.QualificationRecordError):
                record.validate_record(authority, expected_environment=environment_b)

    def test_environment_identity_drift_changes_binding_and_invalid_semantics_refuse(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            first = record.build_record(self.result(
                root, environment_identity=healthy_environment(root, version="3.11.9")))
            second = record.build_record(self.result(
                root, environment_identity=healthy_environment(root, version="3.12.1")))
            self.assertNotEqual(first["environment_digest"], second["environment_digest"])
            self.assertNotEqual(first["execution_binding_digest"], second["execution_binding_digest"])
            first["environment"]["capabilities"]["o_nofollow"] = False
            first["environment_digest"] = record._digest(first["environment"])
            self._redigest(first)
            with self.assertRaises(record.QualificationRecordError):
                record.validate_record(first, verify_static=False)

    def test_persisted_runtime_semantics_cannot_be_self_asserted_with_new_digests(self):
        mutations = (
            lambda runtime: runtime.__setitem__("engine_security_options", ["name=cgroupns"]),
            lambda runtime: runtime.__setitem__("engine_security_options", ["name=seccomp,profile=builtin"]),
            lambda runtime: runtime.__setitem__("cgroup_version", "1"),
            lambda runtime: runtime.__setitem__("engine_operating_system", "native Linux"),
            lambda runtime: runtime.__setitem__("untrusted_boundary_established", False),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as raw:
                root = Path(raw).resolve(strict=True)
                authority = record.build_record(self.result(root))
                mutate(authority["runtime"])
                self._redigest(authority)
                with self.assertRaises(record.QualificationRecordError):
                    record.validate_record(authority, verify_static=False)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            for field, value in (("schema_version", 9), ("backend", "other")):
                result = self.result(root)
                result["preflight"][field] = value
                with self.assertRaises(record.QualificationRecordError):
                    record.build_record(result)

    def test_implementation_manifest_is_closed_and_every_dependency_affects_binding(self):
        expected = {
            "adapters/execution.py", "adapters/sandbox/cli.py", "adapters/sandbox/docker.py",
            "adapters/sandbox/environment.py", "adapters/sandbox/execution.py",
            "adapters/sandbox/record.py",
            "adapters/sandbox/selection.py", "adapters/state/fsio.py",
            "application/observation.py", "application/ports.py", "application/protocol.py",
            "application/run_state.py", "application/transaction.py",
            "application/v2.py", "core/evaluation.py", "core/freshness.py", "core/run.py",
            "vendor/contracts/empirica/v2/state.schema.json",
        }
        baseline = record.implementation_manifest()
        self.assertEqual({row["path"] for row in baseline["files"]}, expected)
        base_binding = record._digest({"schema": record.RECORD_SCHEMA,
                                      "qualification_protocol_revision": record.PROTOCOL_REVISION,
                                      "profile_digest": "sha256:" + "1" * 64,
                                      "runtime_digest": "sha256:" + "2" * 64,
                                      "environment_digest": "sha256:" + "3" * 64,
                                      "implementation_digest": baseline["implementation_digest"]})
        original_digest = record._file_digest
        for relative in sorted(expected):
            with self.subTest(relative=relative):
                def changed_digest(path, relative=relative):
                    if str(path).endswith(relative):
                        return "sha256:" + "f" * 64
                    return original_digest(path)
                with patch.object(record, "_file_digest", side_effect=changed_digest):
                    changed = record.implementation_manifest()
                changed_binding = record._digest({
                    "schema": record.RECORD_SCHEMA,
                    "qualification_protocol_revision": record.PROTOCOL_REVISION,
                    "profile_digest": "sha256:" + "1" * 64,
                    "runtime_digest": "sha256:" + "2" * 64,
                    "environment_digest": "sha256:" + "3" * 64,
                    "implementation_digest": changed["implementation_digest"],
                })
                self.assertNotEqual(changed["implementation_digest"],
                                    baseline["implementation_digest"])
                self.assertNotEqual(changed_binding, base_binding)

    def test_profile_implementation_and_runtime_changes_invalidate(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            authority = record.build_record(self.result(root))
            wrong = "sha256:" + "f" * 64
            with self.assertRaises(record.QualificationRecordError):
                record.validate_record(authority, expected_profile={"profile_digest": wrong})
            with self.assertRaises(record.QualificationRecordError):
                record.validate_record(authority, expected_implementation={"implementation_digest": wrong})
            changed_runtime = json.loads(json.dumps(authority["runtime"]))
            changed_runtime["server_version"] = "changed"
            with self.assertRaises(record.QualificationRecordError):
                record.validate_record(authority, expected_runtime=changed_runtime)

    def test_incomplete_or_selected_evidence_and_unknown_record_schema_are_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            incomplete = self.result(root, complete=False)
            with self.assertRaises(record.QualificationRecordError):
                record.build_record(incomplete)
            selected = self.result(root)
            selected["requested_probe"] = "filesystem"
            with self.assertRaises(record.QualificationRecordError):
                record.build_record(selected)
            contract_false = self.result(root)
            contract_false["empirica_contract_compatible"] = False
            with self.assertRaises(record.QualificationRecordError):
                record.build_record(contract_false)
            fallback_true = self.result(root)
            fallback_true["fallback_eligible"] = True
            with self.assertRaises(record.QualificationRecordError):
                record.build_record(fallback_true)
            divergent = self.result(root)
            divergent["preflight"]["server_version"] = "different"
            with self.assertRaises(record.QualificationRecordError):
                record.build_record(divergent)
            authority = record.build_record(self.result(root))
            authority["schema"] = "unknown/9"
            with self.assertRaises(record.QualificationRecordError):
                record.validate_record(authority)

    def test_unsafe_directory_file_modes_and_symlink_pointer_are_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            unsafe = root / "unsafe"
            unsafe.mkdir(mode=0o755)
            with self.assertRaises(record.QualificationRecordError):
                record.QualificationStore(unsafe).prepare()
            real = root / "real"
            real.mkdir(mode=0o700)
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaises(record.QualificationRecordError):
                record.QualificationStore(alias).prepare()
            authority = record.build_record(self.result(root))
            store = record.QualificationStore(root / "safe")
            store.publish(authority)
            with patch.object(record.os, "getuid", return_value=os.getuid() + 1):
                with self.assertRaises(record.QualificationRecordError):
                    store.load_current()
            os.chmod(store.current, 0o644)
            with self.assertRaises(record.QualificationRecordError):
                store.load_current()
            store.current.unlink()
            store.current.symlink_to(store.records / "missing.json")
            with self.assertRaises(record.QualificationRecordError):
                store.load_current()

    def test_publish_converts_current_and_lock_symlink_failures(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            authority = record.build_record(self.result(root))
            store = record.QualificationStore(root / "current-link")
            store.prepare()
            store.current.symlink_to(root / "outside")
            with self.assertRaises(record.QualificationRecordError):
                store.publish(authority)
            locked = record.QualificationStore(root / "lock-link")
            locked.prepare()
            digest = authority["qualification_record_digest"].removeprefix("sha256:")
            target = locked.records / f"{digest}.json"
            target.with_name(target.name + ".lock").symlink_to(root / "outside-lock")
            with self.assertRaises(record.QualificationRecordError):
                locked.publish(authority)

    def test_size_caps_are_checked_before_any_file_read(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            authority = record.build_record(self.result(root))
            store = record.QualificationStore(root / "sandbox")
            store.publish(authority)
            with store.current.open("wb") as target:
                target.truncate(record.MAX_POINTER_BYTES + 1)
            os.chmod(store.current, 0o600)
            with patch.object(record.fsio, "read_bytes",
                              side_effect=AssertionError("oversized pointer was read")):
                with self.assertRaises(record.QualificationRecordError):
                    store.load_current()
            record_path = store.records / (
                authority["qualification_record_digest"].removeprefix("sha256:") + ".json")
            with record_path.open("wb") as target:
                target.truncate(record.MAX_CONTROL_FILE_BYTES + 1)
            os.chmod(record_path, 0o600)
            with patch.object(record.fsio, "read_bytes",
                              side_effect=AssertionError("oversized record was read")):
                with self.assertRaises(record.QualificationRecordError):
                    store.load_record(authority["qualification_record_digest"])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            authority = record.build_record(self.result(root))
            store = record.QualificationStore(root / "cidfile")
            store.publish(authority)
            cid_op = operation_id("cid")
            self._write_journal(store, cid_op, self.valid_journal(cid_op, authority=authority))
            cidfile = store.runtime / cid_op / "container.cid"
            with cidfile.open("wb") as target:
                target.truncate(record.MAX_CIDFILE_BYTES + 1)
            os.chmod(cidfile, 0o600)
            original_read = record.fsio.read_bytes
            def refuse_cidfile_read(path):
                if path.name == "container.cid":
                    raise AssertionError("oversized cidfile was read")
                return original_read(path)
            with patch.object(record.fsio, "read_bytes", side_effect=refuse_cidfile_read):
                with self.assertRaises(record.QualificationRecordError):
                    store.read_journals()

    def _write_journal(self, store, name, value):
        operation = store.runtime / name
        operation.mkdir(mode=0o700)
        path = operation / "intent.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(path, 0o600)

    def _write_cidfile(self, store, name, container_id):
        path = store.runtime / name / "container.cid"
        path.write_text(container_id + "\n", encoding="ascii")
        os.chmod(path, 0o600)

    @staticmethod
    def valid_journal(operation_id=operation_id("default"), lifecycle_state="intent", authority=None):
        if authority is None:
            policy_digest = "sha256:" + "a" * 64
            binding_digest = "sha256:" + "b" * 64
            record_digest = "sha256:" + "c" * 64
            config_id = "sha256:" + "d" * 64
        else:
            policy_digest = docker.policy_digest(docker.validate_policy({
                **authority["profile"]["policy"], "runtime_identity": authority["runtime"]}))
            binding_digest = authority["execution_binding_digest"]
            record_digest = authority["qualification_record_digest"]
            config_id = authority["runtime"]["image_config_id"]
        run_id, role = "q-run", "probe"
        return {
            "schema": record.JOURNAL_SCHEMA,
            "operation_id": operation_id,
            "lifecycle_state": lifecycle_state,
            "execution_binding_digest": binding_digest,
            "qualification_record_digest": record_digest,
            "policy_digest": policy_digest,
            "run_id": run_id,
            "role": role,
            "name": docker.NAME_PREFIX + "journal",
            "labels": {
                docker.OWNER_LABEL: docker.OWNER_VALUE,
                docker.SCHEMA_LABEL: str(docker.SCHEMA_VERSION),
                docker.POLICY_LABEL: policy_digest,
                docker.RUN_LABEL: run_id,
                docker.ROLE_LABEL: role,
            },
            "expected_image": {
                "repo_digest": docker.IMAGE_REPO_DIGEST,
                "config_id": config_id,
            },
            "full_id": None if lifecycle_state == "intent" else "e" * 64,
            "cidfile_name": "container.cid",
        }

    def test_journal_v1_positive_and_every_authority_field_is_validated(self):
        operation = operation_id("default")
        valid = self.valid_journal()
        self.assertEqual(record.validate_journal(valid, directory_name=operation), valid)
        mutations = {
            "directory": ("operation_id", "different"),
            "state": ("lifecycle_state", "unknown"),
            "binding": ("execution_binding_digest", "bad"),
            "record": ("qualification_record_digest", "bad"),
            "policy": ("policy_digest", "bad"),
            "run": ("run_id", "bad/value"),
            "role": ("role", "bad/value"),
            "name": ("name", "unowned"),
            "cidfile": ("cidfile_name", "../container.cid"),
            "image": ("expected_image", {"repo_digest": "wrong", "config_id": "bad"}),
            "full_id": ("full_id", "e" * 64),
        }
        for name, (key, value) in mutations.items():
            with self.subTest(name=name):
                changed = json.loads(json.dumps(valid))
                changed[key] = value
                with self.assertRaises(record.QualificationRecordError):
                    record.validate_journal(changed, directory_name=operation)
        changed = json.loads(json.dumps(valid))
        changed["labels"][docker.RUN_LABEL] = "other"
        with self.assertRaises(record.QualificationRecordError):
            record.validate_journal(changed, directory_name=operation)
        created = self.valid_journal(lifecycle_state="created")
        created["full_id"] = None
        with self.assertRaises(record.QualificationRecordError):
            record.validate_journal(created, directory_name=operation)

    def test_journal_reader_binds_record_policy_image_and_cidfile_crash_window(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            authority = record.build_record(self.result(root))
            store = record.QualificationStore(root / "sandbox")
            store.publish(authority)
            intent_op = operation_id("intent")
            intent = self.valid_journal(intent_op, authority=authority)
            self._write_journal(store, intent_op, intent)
            crash_id = "f" * 64
            self._write_cidfile(store, intent_op, crash_id)
            rows = store.read_journals()
            self.assertEqual(rows[0]["cidfile_full_id"], crash_id)
            (store.runtime / intent_op / "container.cid").unlink()
            (store.runtime / intent_op / "intent.json").unlink()
            (store.runtime / intent_op).rmdir()
            created_op = operation_id("created")
            created = self.valid_journal(created_op, "created", authority)
            self._write_journal(store, created_op, created)
            with self.assertRaises(record.QualificationRecordError):
                store.read_journals()
            self._write_cidfile(store, created_op, created["full_id"])
            self.assertEqual(store.read_journals()[0]["full_id"], created["full_id"])

    def test_journal_reader_refuses_record_binding_policy_image_and_cid_mismatch(self):
        fields = ("execution_binding_digest", "policy_digest", "expected_image", "full_id")
        for field in fields:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as raw:
                root = Path(raw).resolve(strict=True)
                authority = record.build_record(self.result(root))
                store = record.QualificationStore(root / "sandbox")
                store.publish(authority)
                created_op = operation_id("created")
                journal = self.valid_journal(created_op, "created", authority)
                if field == "expected_image":
                    journal[field]["config_id"] = "sha256:" + "9" * 64
                elif field == "full_id":
                    journal[field] = "8" * 64
                else:
                    journal[field] = "sha256:" + "9" * 64
                    if field == "policy_digest":
                        journal["labels"][docker.POLICY_LABEL] = journal[field]
                self._write_journal(store, created_op, journal)
                self._write_cidfile(store, created_op, "e" * 64)
                with self.assertRaises(record.QualificationRecordError):
                    store.read_journals()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            authority = record.build_record(self.result(root))
            store = record.QualificationStore(root / "sandbox")
            store.publish(authority)
            missing_record = operation_id("missing-record")
            journal = self.valid_journal(missing_record, authority=authority)
            journal["qualification_record_digest"] = "sha256:" + "9" * 64
            self._write_journal(store, missing_record, journal)
            with self.assertRaises(record.QualificationRecordError):
                store.read_journals()

    def test_runtime_entries_and_journal_file_set_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            store = record.QualificationStore(root / "regular")
            store.prepare()
            bad = store.runtime / "bad"
            bad.write_text("x", encoding="utf-8")
            os.chmod(bad, 0o600)
            with self.assertRaises(record.QualificationRecordError):
                store.read_journals()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            store = record.QualificationStore(root / "symlink")
            store.prepare()
            target = root / "target"
            target.write_text("x", encoding="utf-8")
            (store.runtime / "bad").symlink_to(target)
            with self.assertRaises(record.QualificationRecordError):
                store.read_journals()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            store = record.QualificationStore(root / "extra")
            store.prepare()
            operation = operation_id("extra")
            self._write_journal(store, operation, self.valid_journal(operation))
            extra = store.runtime / operation / "extra"
            extra.write_text("x", encoding="utf-8")
            os.chmod(extra, 0o600)
            with self.assertRaises(record.QualificationRecordError):
                store.read_journals()

    def test_unknown_historical_journal_and_count_or_byte_caps_fail_closed(self):
        one = operation_id("one")
        base = self.valid_journal(one)
        base["schema"] = "unknown/9"
        with tempfile.TemporaryDirectory() as raw:
            store = record.QualificationStore(Path(raw).resolve(strict=True) / "sandbox")
            store.prepare()
            self._write_journal(store, one, base)
            with self.assertRaises(record.QualificationRecordError):
                store.read_journals()
        with tempfile.TemporaryDirectory() as raw:
            store = record.QualificationStore(Path(raw).resolve(strict=True) / "sandbox")
            store.prepare()
            for index in range(record.MAX_JOURNALS + 1):
                (store.runtime / operation_id(str(index))).mkdir(mode=0o700)
            with self.assertRaises(record.QualificationRecordError):
                store.read_journals()
        with tempfile.TemporaryDirectory() as raw:
            store = record.QualificationStore(Path(raw).resolve(strict=True) / "sandbox")
            store.prepare()
            huge_id = operation_id("huge")
            huge = self.valid_journal(huge_id)
            huge["labels"][docker.ROLE_LABEL] = "y" * record.MAX_JOURNAL_BYTES
            self._write_journal(store, huge_id, huge)
            with patch.object(record.fsio, "read_bytes",
                              side_effect=AssertionError("oversized journal was read")):
                with self.assertRaises(record.QualificationRecordError):
                    store.read_journals()


class ReportAndInterfaceTests(unittest.TestCase):
    @staticmethod
    def result(*, requested="all", selected=True, complete=True):
        return {
            "passed": selected, "requested_probe": requested,
            "preflight_available": True, "selected_probe_passed": selected,
            "environment_preflight_available": True,
            "environment_preflight": {
                "available": True, "reasons": [],
                "python": {"implementation": "CPython", "version": "3.11.9",
                           "cache_tag": "cpython-311", "executable_sha256": "sha256:" + "1" * 64},
            },
            "complete_qualification": complete,
            "docker_backend_eligible": complete,
            "empirica_contract_compatible": complete, "fallback_eligible": False,
            "qualified_for_untrusted_code": complete, "cleanup_verified": True,
            "qualification_run_id": "q1", "generated_at": "now", "policy_sha256": "sha256:p",
            "preflight": {
                "host_os": "Darwin", "host_release": "x", "host_arch": "arm64",
                "docker_cli": "/usr/bin/docker", "docker_cli_sha256": "sha256:c",
                "context": "desktop-linux", "endpoint": "unix:///tmp/docker.sock",
                "client_version": "29", "server_version": "29", "server_os": "linux",
                "server_arch": "arm64", "image_repo_digest": docker.IMAGE_REPO_DIGEST,
                "image_config_id": "sha256:i", "reasons": [],
            },
            "probes": ([] if requested == "preflight" else [
                {"name": requested if requested != "all" else "filesystem",
                 "passed": selected, "elapsed_ms": 7, "details": {}}]),
            "census_before": {key: {"count": 0} for key in (
                "all_containers", "qualification_containers", "all_networks", "qualification_networks")},
            "census_after": {key: {"count": 0} for key in (
                "all_containers", "qualification_containers", "all_networks", "qualification_networks")},
            "recovered_orphan_container_ids": [], "limitations": ["local only"],
        }

    def test_make_interface_accepts_established_docker_all_shape(self):
        args = qualification.parse_args(("qualify", "--backend", "docker", "--probe", "all"))
        self.assertEqual(args.backend, "docker")
        self.assertEqual(args.probe, "all")
        self.assertEqual(qualification.DEFAULT_OUTPUT_DIR,
                         Path(tempfile.gettempdir()) / "siffran-sandbox-qualification")

    def test_report_keeps_backend_contract_fallback_and_cleanup_separate(self):
        report = qualification.render_report(self.result())
        self.assertIn("Complete qualification: **PASS**", report)
        self.assertIn("Docker backend eligible: **true**", report)
        self.assertIn("Empirica contract compatible: **true**", report)
        self.assertIn("Host subprocess fallback eligible: **false**", report)
        self.assertIn("Cleanup verified: **true**", report)
        self.assertIn("Host environment ready: **true**", report)
        self.assertIn("| `filesystem` | PASS | 7 ms |", report)

    def test_operator_install_never_publishes_incomplete_evidence(self):
        result = self.result(requested="all", selected=False, complete=False)
        published = []
        class Store:
            def prepare(self):
                return None
            def publish(self, _authority):
                published.append(True)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            with patch.object(qualification, "DEFAULT_OUTPUT_DIR", root):
                exit_code = qualification.main(
                    ("install", "--sandbox-home", str(root / "authority")),
                    qualifier=lambda _probe: result, store_factory=lambda _home: Store())
        self.assertEqual(exit_code, 1)
        self.assertEqual(published, [])

    def test_operator_install_rejects_noncanonical_or_unsafe_home_before_qualification(self):
        qualified = []
        exit_code = qualification.main(
            ("install", "--sandbox-home", "relative/authority"),
            qualifier=lambda _probe: qualified.append(True))
        self.assertEqual(exit_code, 1)
        self.assertEqual(qualified, [])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            unsafe = root / "unsafe"
            unsafe.mkdir(mode=0o755)
            exit_code = qualification.main(
                ("install", "--sandbox-home", str(unsafe)),
                qualifier=lambda _probe: qualified.append(True))
        self.assertEqual(exit_code, 1)
        self.assertEqual(qualified, [])

    def test_operator_install_converts_preplanted_current_symlink_to_refusal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            store = record.QualificationStore(
                root / "authority", environment_inspector=lambda: healthy_environment(root))
            store.prepare()
            store.current.symlink_to(root / "outside")
            result = self.result()
            result.update(RecordTests().result(root))
            with patch.object(qualification, "DEFAULT_OUTPUT_DIR", root / "reports"):
                exit_code = qualification.main(
                    ("install", "--sandbox-home", str(store.home)),
                    qualifier=lambda _probe: result, store_factory=lambda _home: store)
        self.assertEqual(exit_code, 1)

    def test_operator_install_normalizes_malformed_policy_to_refusal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(strict=True)
            result = self.result()
            result.update(RecordTests().result(root))
            result["policy"]["schema_version"] = 9
            result["policy_sha256"] = docker.policy_digest(result["policy"])
            class Store:
                def prepare(self):
                    return None
                def publish(self, _authority):
                    raise AssertionError("malformed authority reached publish")
            with patch.object(qualification, "DEFAULT_OUTPUT_DIR", root / "reports"):
                exit_code = qualification.main(
                    ("install", "--sandbox-home", str(root / "authority")),
                    qualifier=lambda _probe: result, store_factory=lambda _home: Store())
        self.assertEqual(exit_code, 1)

    def test_preflight_success_is_available_but_never_complete_or_eligible(self):
        result = self.result(requested="preflight", selected=True, complete=False)
        report = qualification.render_report(result)
        self.assertIn("Requested operation: **preflight — AVAILABLE**", report)
        self.assertIn("Complete qualification: **INCOMPLETE**", report)
        self.assertIn("Docker backend eligible: **false**", report)
        self.assertIn("Qualified for untrusted code: **false**", report)
        self.assertNotIn("Complete qualification: **PASS**", report)

    def test_single_probe_success_is_selected_pass_but_never_complete_or_eligible(self):
        result = self.result(requested="filesystem", selected=True, complete=False)
        report = qualification.render_report(result)
        self.assertIn("Requested operation: **filesystem — PASS**", report)
        self.assertIn("Complete qualification: **INCOMPLETE**", report)
        self.assertFalse(result["docker_backend_eligible"])
        self.assertFalse(result["qualified_for_untrusted_code"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
