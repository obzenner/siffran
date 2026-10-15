#!/usr/bin/env python3
"""Pull-disabled qualification for Empirica's local Docker spike policy.

This is an operator-run diagnostic, not the production adapter.  It uses the
already-running selected Docker engine, writes reports outside the repository,
and never installs, pulls, switches contexts, repairs, or changes daemon state.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

EMPIRICA = Path(__file__).resolve().parents[2]
ROOT = EMPIRICA.parents[1]
if str(EMPIRICA) not in sys.path:
    sys.path.insert(0, str(EMPIRICA))

from adapters.execution import FilesystemWorkspace  # noqa: E402
from adapters.sandbox.environment import EnvironmentIdentity, inspect_environment  # noqa: E402
from application.observation import execute_spike_bound  # noqa: E402
from application.ports import ExecutionSnapshot, HarnessResult  # noqa: E402
from core.freshness import Gate, canonical_digest  # noqa: E402

SCHEMA_VERSION = 1
BACKEND = "docker"
IMAGE_REPO_DIGEST = "rust@sha256:93ce27a88655056a51dbdd8f5f2d7ddc071c7b0070fb288a37b5a285fc83971e"
OWNER_LABEL = "io.siffran.empirica.qualification.owner"
SCHEMA_LABEL = "io.siffran.empirica.qualification.schema"
POLICY_LABEL = "io.siffran.empirica.qualification.policy"
RUN_LABEL = "io.siffran.empirica.qualification.run"
ROLE_LABEL = "io.siffran.empirica.qualification.role"
OWNER_VALUE = "docker-spike-v1"
NAME_PREFIX = "siffran-qual-"
CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
SAFE_LABEL_VALUE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

WORK_TMPFS_BYTES = 64 * 1024 * 1024
TMP_TMPFS_BYTES = 16 * 1024 * 1024
SHM_BYTES = 1024 * 1024
MEMORY_BYTES = 512 * 1024 * 1024
PIDS_LIMIT = 128
NOFILE_LIMIT = 256
FSIZE_LIMIT = 64 * 1024 * 1024
CPU_NANO = 1_000_000_000
CONTROL_TIMEOUT_SECONDS = 5.0
WORKLOAD_TIMEOUT_SECONDS = 120.0
CLEANUP_TIMEOUT_SECONDS = 15.0
CREATE_INSPECT_TIMEOUT_SECONDS = 10.0
POSTFLIGHT_TIMEOUT_SECONDS = 15.0
OPERATION_TIMEOUT_SECONDS = 210.0
CONTROL_OUTPUT_LIMIT_BYTES = 256 * 1024
WORKLOAD_OUTPUT_LIMIT_BYTES = 1024 * 1024
DIAGNOSTIC_TAIL_BYTES = 4096
TIMEOUT_EXIT_CODE = 124
OUTPUT_LIMIT_EXIT_CODE = 125

EXPLICIT_ENVIRONMENT = (
    "HOME=/work/home",
    "TMPDIR=/tmp",
    "CARGO_HOME=/work/cargo-home",
    "CARGO_TARGET_DIR=/work/target",
    "CARGO_NET_OFFLINE=true",
    "RUSTUP_HOME=/usr/local/rustup",
    "PATH=/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "PWD=/input",
)

HOST_CLI_FIXED_ENVIRONMENT = {"LANG": "C", "LC_ALL": "C"}

POLICY_TEMPLATE: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "backend": BACKEND,
    "host_cli_environment": {
        "endpoint_variable": "DOCKER_HOST",
        "fixed": HOST_CLI_FIXED_ENVIRONMENT,
    },
    "container_lifecycle": {
        "strategy": "create-start-inspect-remove-v1",
        "auto_remove": False,
        "force_remove": True,
        "remove_volumes": False,
        "inspect_identity_before_remove": True,
        "recover_orphans_by_exact_labels": True,
        "name_prefix": NAME_PREFIX,
        "label_keys": [OWNER_LABEL, SCHEMA_LABEL, POLICY_LABEL, RUN_LABEL, ROLE_LABEL],
    },
    "image": {"repo_digest": IMAGE_REPO_DIGEST, "pull": "never"},
    "isolation": {
        "network": "none",
        "read_only_root": True,
        "input_mount": {"destination": "/input", "readonly": True, "type": "bind"},
        "work_tmpfs": {
            "destination": "/work", "size_bytes": WORK_TMPFS_BYTES,
            "mode": "1777", "nosuid": True, "nodev": True, "exec": True,
        },
        "tmp_tmpfs": {
            "destination": "/tmp", "size_bytes": TMP_TMPFS_BYTES,
            "mode": "1777", "nosuid": True, "nodev": True, "exec": False,
        },
        "shm_bytes": SHM_BYTES,
        "docker_socket_mount": False,
        "cap_drop": ["ALL"],
        "no_new_privileges": True,
        "user": "65534:65534",
        "init": True,
        "log_driver": "none",
        "required_engine_security_options": ["name=cgroupns", "name=seccomp,profile=builtin"],
        "required_cgroup_version": "2",
        "required_container_seccomp_mode": 2,
        "qualified_host_boundary": "Darwin host -> local Unix endpoint -> Docker Desktop Linux VM -> container",
    },
    "process": {
        "entrypoint": ["/usr/bin/env", "-i"],
        "environment": list(EXPLICIT_ENVIRONMENT),
        "shell": ["/bin/sh", "-c"],
        "working_directory": "/input",
    },
    "resources": {
        "nano_cpus": CPU_NANO,
        "memory_bytes": MEMORY_BYTES,
        "memory_swap_bytes": MEMORY_BYTES,
        "pids_limit": PIDS_LIMIT,
        "ulimits": {
            "nofile": [NOFILE_LIMIT, NOFILE_LIMIT],
            "core": [0, 0],
            "fsize": [FSIZE_LIMIT, FSIZE_LIMIT],
        },
    },
    "bounds": {
        "control_timeout_seconds": CONTROL_TIMEOUT_SECONDS,
        "create_inspect_timeout_seconds": CREATE_INSPECT_TIMEOUT_SECONDS,
        "workload_timeout_seconds": WORKLOAD_TIMEOUT_SECONDS,
        "cleanup_timeout_seconds": CLEANUP_TIMEOUT_SECONDS,
        "postflight_timeout_seconds": POSTFLIGHT_TIMEOUT_SECONDS,
        "operation_timeout_seconds": OPERATION_TIMEOUT_SECONDS,
        "control_output_limit_bytes": CONTROL_OUTPUT_LIMIT_BYTES,
        "workload_output_limit_bytes": WORKLOAD_OUTPUT_LIMIT_BYTES,
        "diagnostic_tail_bytes": DIAGNOSTIC_TAIL_BYTES,
        "staging_max_files": NOFILE_LIMIT,
        "staging_max_bytes": FSIZE_LIMIT,
    },
}

MANDATORY_SECURITY_PROBES = (
    "filesystem", "environment", "writable_storage", "docker_socket", "network",
    "privilege", "engine_isolation", "resources", "memory_limit", "pids_limit",
    "timeout", "output_limit", "process_cleanup", "orphan_recovery",
)
MANDATORY_PERFORMANCE_PROBES = ("rust", "rust_large_artifact", "empirica_contract")
MANDATORY_PROBES = MANDATORY_SECURITY_PROBES + MANDATORY_PERFORMANCE_PROBES


class DockerQualificationError(RuntimeError):
    """A required Docker fact or operation is absent, malformed, or unsafe."""


class DockerInfrastructureError(DockerQualificationError):
    """The CLI, daemon, create/start/inspect path, or image failed."""


class DockerCleanupError(DockerQualificationError):
    """A qualification-owned resource could not be safely proven removed."""


@dataclass(frozen=True)
class StreamCapture:
    data: bytes
    bytes_seen: int
    sha256: str
    tail: str

    def report(self) -> dict[str, Any]:
        return {"bytes": self.bytes_seen, "sha256": self.sha256, "tail": self.tail}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    timed_out: bool
    output_limited: bool
    stdout: StreamCapture
    stderr: StreamCapture
    elapsed_ms: int

    @classmethod
    def fake(
        cls, returncode: int = 0, stdout: str | bytes = b"", stderr: str | bytes = b"", *,
        timed_out: bool = False, output_limited: bool = False,
    ) -> "CommandResult":
        return cls(
            returncode, timed_out, output_limited,
            _capture_bytes(stdout.encode() if isinstance(stdout, str) else stdout),
            _capture_bytes(stderr.encode() if isinstance(stderr, str) else stderr), 0,
        )

    def report(self) -> dict[str, Any]:
        return {
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "output_limited": self.output_limited,
            "stdout": self.stdout.report(),
            "stderr": self.stderr.report(),
            "elapsed_ms": self.elapsed_ms,
        }


class Runner(Protocol):
    def run(
        self, argv: Sequence[str], *, timeout_seconds: float, output_limit_bytes: int,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult: ...


def _capture_bytes(value: bytes) -> StreamCapture:
    return StreamCapture(
        value, len(value), "sha256:" + hashlib.sha256(value).hexdigest(),
        value[-DIAGNOSTIC_TAIL_BYTES:].decode("utf-8", errors="replace").strip(),
    )


class BoundedRunner:
    """Drain both pipes incrementally, hash/count them, and kill the process group at a bound."""

    def run(
        self, argv: Sequence[str], *, timeout_seconds: float, output_limit_bytes: int,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        if (not argv or isinstance(timeout_seconds, bool)
                or type(timeout_seconds) not in {int, float}
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
                or isinstance(output_limit_bytes, bool) or type(output_limit_bytes) is not int
                or output_limit_bytes <= 0):
            raise ValueError("runner requires argv, a finite positive timeout, and a positive byte limit")
        began = time.monotonic_ns()
        process = subprocess.Popen(
            tuple(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=None if env is None else dict(env), start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        retained = {"stdout": bytearray(), "stderr": bytearray()}
        tails = {"stdout": bytearray(), "stderr": bytearray()}
        totals = {"stdout": 0, "stderr": 0}
        digests = {"stdout": hashlib.sha256(), "stderr": hashlib.sha256()}
        deadline = time.monotonic() + timeout_seconds
        timed_out = output_limited = False
        killed = False

        def kill_group() -> None:
            nonlocal killed
            if killed:
                return
            killed = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # A just-exited leader can race process-group teardown on macOS.  If it is
                # still live, kill the owned leader and surface no false success: the caller's
                # Docker container cleanup remains independently identity-bound.
                if process.poll() is None:
                    process.kill()

        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    kill_group()
                    remaining = 0.05
                events = selector.select(max(0.0, min(0.05, remaining if remaining > 0 else 0.05)))
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 65_536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    stream = key.data
                    totals[stream] += len(chunk)
                    digests[stream].update(chunk)
                    room = max(0, output_limit_bytes - len(retained[stream]))
                    retained[stream].extend(chunk[:room])
                    tails[stream].extend(chunk)
                    if len(tails[stream]) > DIAGNOSTIC_TAIL_BYTES:
                        del tails[stream][:-DIAGNOSTIC_TAIL_BYTES]
                    if totals[stream] > output_limit_bytes:
                        output_limited = True
                        kill_group()
                if output_limited:
                    # Drain only what was already pipe-buffered after the kill; memory stays capped.
                    deadline = min(deadline, time.monotonic() + 0.5)
            if process.poll() is None:
                remaining = max(0.01, deadline - time.monotonic())
                try:
                    process.wait(timeout=min(2.0, remaining))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    kill_group()
                    process.wait(timeout=2)
            else:
                process.wait()
        except BaseException:
            kill_group()
            try:
                process.wait(timeout=2)
            except BaseException:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except BaseException:
                    pass
            raise
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
        captures = {}
        for stream in ("stdout", "stderr"):
            captures[stream] = StreamCapture(
                bytes(retained[stream]), totals[stream], "sha256:" + digests[stream].hexdigest(),
                tails[stream].decode("utf-8", errors="replace").strip(),
            )
        return CommandResult(
            process.returncode, timed_out, output_limited,
            captures["stdout"], captures["stderr"],
            (time.monotonic_ns() - began) // 1_000_000,
        )


@dataclass(frozen=True)
class DockerRuntimeIdentity:
    schema_version: int
    backend: str
    available: bool
    host_os: str
    host_release: str
    host_arch: str
    docker_cli: str | None
    docker_cli_sha256: str | None
    context: str | None
    endpoint: str | None
    client_version: str | None
    server_version: str | None
    server_os: str | None
    server_arch: str | None
    engine_security_options: tuple[str, ...]
    cgroup_version: str | None
    cgroup_driver: str | None
    engine_kernel_version: str | None
    engine_operating_system: str | None
    untrusted_boundary_established: bool
    image_repo_digest: str
    image_config_id: str | None
    image_os: str | None
    image_arch: str | None
    reasons: tuple[str, ...]


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _text(result: CommandResult, operation: str) -> str:
    if result.timed_out or result.output_limited or result.returncode != 0:
        raise DockerInfrastructureError(
            f"{operation} failed (exit={result.returncode}, timeout={result.timed_out}, "
            f"output_limit={result.output_limited}): {result.stderr.tail}")
    return result.stdout.data.decode("utf-8", errors="strict").strip()


def _json_result(result: CommandResult, operation: str) -> Any:
    text = _text(result, operation)
    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise DockerInfrastructureError(f"{operation} returned malformed JSON") from exc


def _call_discovery(
    runner: Runner, cli: str, args: Sequence[str], operation: str, env: Mapping[str, str],
) -> str:
    return _text(runner.run(
        (cli, *args), timeout_seconds=CONTROL_TIMEOUT_SECONDS,
        output_limit_bytes=CONTROL_OUTPUT_LIMIT_BYTES, env=env), operation)


def discover_docker(
    runner: Runner, *, cli_path: str | None = None,
    env: Mapping[str, str] | None = None,
) -> DockerRuntimeIdentity:
    """Passive discovery only: no create, pull, context mutation, or repair."""
    current_env = dict(os.environ if env is None else env)
    reasons: list[str] = []
    selected = cli_path if cli_path is not None else shutil.which("docker", path=current_env.get("PATH"))
    cli = cli_digest = context = endpoint = client = server = server_os = server_arch = None
    cgroup_version = cgroup_driver = engine_kernel = engine_operating_system = None
    security_options: tuple[str, ...] = ()
    image_config = image_os = image_arch = None
    if selected is None:
        reasons.append("docker CLI is unavailable")
    else:
        try:
            candidate = Path(selected).expanduser().resolve(strict=True)
            mode = candidate.stat().st_mode
            if not stat.S_ISREG(mode) or not os.access(candidate, os.X_OK):
                raise OSError("not an executable regular file")
            cli = str(candidate)
            cli_digest = _file_digest(candidate)
        except OSError:
            reasons.append("docker CLI is not a hashable canonical executable")
        if cli is not None:
            try:
                context = _call_discovery(runner, cli, ("context", "show"), "docker context show", current_env)
                endpoint = json.loads(_call_discovery(
                    runner, cli,
                    ("context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"),
                    "docker context inspect", current_env))
                if not isinstance(endpoint, str) or not endpoint:
                    raise ValueError("empty endpoint")
            except (DockerQualificationError, TypeError, ValueError):
                reasons.append("selected Docker context/endpoint is unavailable or malformed")
            context_override = bool(current_env.get("DOCKER_CONTEXT"))
            effective_endpoint = (endpoint if context_override
                                  else current_env.get("DOCKER_HOST") or endpoint)
            if not isinstance(effective_endpoint, str) or not effective_endpoint.startswith("unix://"):
                reasons.append("effective Docker endpoint is not a local Unix socket")
            else:
                socket_spelling = effective_endpoint.removeprefix("unix://")
                if not socket_spelling.startswith("/") or "\x00" in socket_spelling or "\n" in socket_spelling:
                    reasons.append("effective Docker Unix socket path is not absolute and canonical")
            endpoint = effective_endpoint if isinstance(effective_endpoint, str) else endpoint
            try:
                version = json.loads(_call_discovery(
                    runner, cli, ("version", "--format", "{{json .}}"),
                    "docker version", current_env))
                client_doc, server_doc = version["Client"], version["Server"]
                client, server = client_doc["Version"], server_doc["Version"]
                server_os, server_arch = server_doc["Os"], server_doc["Arch"]
                if not all(isinstance(item, str) and item for item in
                           (client, server, server_os, server_arch)):
                    raise ValueError("empty version identity")
            except (DockerQualificationError, KeyError, TypeError, ValueError):
                reasons.append("Docker client/server identity is unavailable or malformed")
            if server_os is not None and server_os != "linux":
                reasons.append("Docker engine is not running Linux containers")
            if (server_arch is not None
                    and (server_arch not in {"amd64", "arm64"} or server_arch != platform.machine())):
                reasons.append("Docker engine architecture is unsupported or differs from the host")
            try:
                info = json.loads(_call_discovery(
                    runner, cli, ("info", "--format", "{{json .}}"),
                    "docker info", current_env))
                raw_security = info["SecurityOptions"]
                cgroup_version = info["CgroupVersion"]
                cgroup_driver = info["CgroupDriver"]
                engine_kernel = info["KernelVersion"]
                engine_operating_system = info["OperatingSystem"]
                if (not isinstance(raw_security, list)
                        or not all(isinstance(item, str) and item for item in raw_security)
                        or not all(isinstance(item, str) and item for item in (
                            cgroup_version, cgroup_driver, engine_kernel, engine_operating_system))):
                    raise ValueError("malformed engine isolation identity")
                security_options = tuple(sorted(raw_security))
            except (DockerQualificationError, KeyError, TypeError, ValueError):
                reasons.append("Docker engine security/cgroup identity is unavailable or malformed")
            try:
                image = json.loads(_call_discovery(
                    runner, cli,
                    ("image", "inspect", IMAGE_REPO_DIGEST, "--format", "{{json .}}"),
                    "docker image inspect", current_env))
                repo_digests = image["RepoDigests"]
                image_config, image_os, image_arch = image["Id"], image["Os"], image["Architecture"]
                if (not isinstance(repo_digests, list) or IMAGE_REPO_DIGEST not in repo_digests
                        or not isinstance(image_config, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_config)
                        or image_os != "linux" or image_arch != server_arch):
                    raise ValueError("image identity mismatch")
            except DockerQualificationError:
                reasons.append("pinned Docker image is not present locally")
            except (KeyError, TypeError, ValueError):
                reasons.append("pinned Docker image repo digest/config/platform identity mismatches")
    boundary_established = (
        platform.system() == "Darwin"
        and isinstance(endpoint, str) and endpoint.startswith("unix://")
        and server_os == "linux"
        and isinstance(engine_operating_system, str) and "Docker Desktop" in engine_operating_system
        and cgroup_version == "2"
        and "name=cgroupns" in security_options
        and "name=seccomp,profile=builtin" in security_options
    )
    return DockerRuntimeIdentity(
        SCHEMA_VERSION, BACKEND, not reasons,
        platform.system(), platform.release(), platform.machine(),
        cli, cli_digest, context, endpoint, client, server, server_os, server_arch,
        security_options, cgroup_version, cgroup_driver, engine_kernel,
        engine_operating_system, boundary_established,
        IMAGE_REPO_DIGEST, image_config, image_os, image_arch, tuple(reasons),
    )


def docker_client_environment(policy: Mapping[str, Any]) -> dict[str, str]:
    """Derive the entire post-discovery host CLI environment from qualified facts."""
    runtime = policy["runtime_identity"]
    endpoint = runtime["endpoint"]
    host_environment = policy["host_cli_environment"]
    if (host_environment != POLICY_TEMPLATE["host_cli_environment"]
            or not isinstance(endpoint, str) or not endpoint.startswith("unix:///")):
        raise DockerQualificationError("qualified Docker host CLI environment is malformed")
    return {
        **host_environment["fixed"],
        host_environment["endpoint_variable"]: endpoint,
    }


def effective_policy(identity: DockerRuntimeIdentity) -> dict[str, Any]:
    runtime_identity = validate_runtime_identity(identity)
    policy = json.loads(json.dumps(POLICY_TEMPLATE))
    policy["runtime_identity"] = runtime_identity
    return policy


RUNTIME_IDENTITY_KEYS = {
    "host_os", "host_release", "host_arch", "docker_cli", "docker_cli_sha256",
    "context", "endpoint", "client_version", "server_version", "server_os", "server_arch",
    "engine_security_options", "cgroup_version", "cgroup_driver", "engine_kernel_version",
    "engine_operating_system", "untrusted_boundary_established", "image_repo_digest",
    "image_config_id", "image_os", "image_arch",
}


def validate_runtime_identity(value: DockerRuntimeIdentity | Mapping[str, Any]) -> dict[str, Any]:
    """Validate persisted/runtime identity semantically; never trust its boundary boolean alone."""
    source = asdict(value) if isinstance(value, DockerRuntimeIdentity) else dict(value)
    full_keys = set(DockerRuntimeIdentity.__dataclass_fields__)
    if set(source) == full_keys:
        if (source["schema_version"] != SCHEMA_VERSION or source["backend"] != BACKEND
                or source["available"] is not True or source["reasons"] not in ((), [])):
            raise DockerQualificationError("Docker preflight identity is not an available current profile")
    elif set(source) != RUNTIME_IDENTITY_KEYS:
        raise DockerQualificationError("Docker runtime identity has missing or extra fields")
    runtime = {key: source[key] for key in RUNTIME_IDENTITY_KEYS}
    required_strings = RUNTIME_IDENTITY_KEYS - {
        "engine_security_options", "untrusted_boundary_established",
    }
    if (not all(isinstance(runtime[key], str) and runtime[key] for key in required_strings)
            or not isinstance(runtime["engine_security_options"], (list, tuple))
            or not all(isinstance(item, str) and item for item in runtime["engine_security_options"])):
        raise DockerQualificationError("Docker runtime identity fields are malformed")
    cli = Path(runtime["docker_cli"])
    try:
        canonical_cli = cli.resolve(strict=True)
        cli_mode = canonical_cli.stat().st_mode
    except OSError as exc:
        raise DockerQualificationError("Docker runtime CLI is unavailable") from exc
    endpoint_path = Path(runtime["endpoint"].removeprefix("unix://"))
    required_security = set(POLICY_TEMPLATE["isolation"]["required_engine_security_options"])
    architectures = {runtime["host_arch"], runtime["server_arch"], runtime["image_arch"]}
    if (runtime["host_os"] != "Darwin" or runtime["server_os"] != "linux"
            or runtime["image_os"] != "linux" or architectures not in ({"arm64"}, {"amd64"})
            or not runtime["endpoint"].startswith("unix:///") or not endpoint_path.is_absolute()
            or os.path.normpath(str(endpoint_path)) != str(endpoint_path)
            or runtime["context"].strip() != runtime["context"]
            or "Docker Desktop" not in runtime["engine_operating_system"]
            or runtime["cgroup_version"] != "2"
            or not required_security.issubset(set(runtime["engine_security_options"]))
            or runtime["image_repo_digest"] != IMAGE_REPO_DIGEST
            or re.fullmatch(r"sha256:[0-9a-f]{64}", runtime["image_config_id"]) is None
            or cli != canonical_cli or not stat.S_ISREG(cli_mode) or not os.access(cli, os.X_OK)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", runtime["docker_cli_sha256"]) is None
            or runtime["untrusted_boundary_established"] is not True):
        raise DockerQualificationError("Docker runtime identity is outside the qualified boundary")
    runtime["engine_security_options"] = list(runtime["engine_security_options"])
    return json.loads(json.dumps(runtime))


def _closed_shape(value: Any, template: Any, path: str) -> None:
    if isinstance(template, dict):
        if not isinstance(value, Mapping) or set(value) != set(template):
            raise DockerQualificationError(f"effective policy has missing/extra fields at {path}")
        for key in template:
            _closed_shape(value[key], template[key], f"{path}.{key}")
    elif isinstance(template, list):
        if not isinstance(value, list):
            raise DockerQualificationError(f"effective policy field {path} must be a list")
    elif template is None:
        return
    elif type(value) is not type(template):
        raise DockerQualificationError(f"effective policy field {path} has the wrong type")


def validate_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy the closed executable policy before any Docker mutation."""
    runtime_template = {key: None for key in RUNTIME_IDENTITY_KEYS}
    template = {**POLICY_TEMPLATE, "runtime_identity": runtime_template}
    _closed_shape(policy, template, "policy")
    candidate = json.loads(json.dumps(policy))
    if candidate["schema_version"] != SCHEMA_VERSION or candidate["backend"] != BACKEND:
        raise DockerQualificationError("effective policy schema/backend mismatch")
    if candidate["host_cli_environment"] != POLICY_TEMPLATE["host_cli_environment"]:
        raise DockerQualificationError("effective policy host CLI environment is not closed")
    docker_client_environment(candidate)
    if candidate["container_lifecycle"] != POLICY_TEMPLATE["container_lifecycle"]:
        raise DockerQualificationError("effective policy lifecycle must use the reviewed canonical strategy")
    runtime = candidate["runtime_identity"]
    required_strings = RUNTIME_IDENTITY_KEYS - {
        "engine_security_options", "untrusted_boundary_established",
    }
    if (not all(isinstance(runtime[key], str) and runtime[key] for key in required_strings)
            or not isinstance(runtime["engine_security_options"], list)
            or not all(isinstance(item, str) and item for item in runtime["engine_security_options"])
            or type(runtime["untrusted_boundary_established"]) is not bool):
        raise DockerQualificationError("effective policy runtime identity is incomplete or malformed")
    runtime = validate_runtime_identity(runtime)
    candidate["runtime_identity"] = runtime
    if candidate["image"] != {"repo_digest": runtime["image_repo_digest"], "pull": "never"}:
        raise DockerQualificationError("effective policy image must match the qualified pull-disabled identity")
    isolation = candidate["isolation"]
    isolation_template = json.loads(json.dumps(POLICY_TEMPLATE["isolation"]))
    for mount in ("work_tmpfs", "tmp_tmpfs"):
        isolation_template[mount]["size_bytes"] = isolation[mount]["size_bytes"]
    if isolation != isolation_template:
        raise DockerQualificationError("effective policy isolation controls differ from the reviewed policy")
    for mount, maximum in (("work_tmpfs", WORK_TMPFS_BYTES), ("tmp_tmpfs", TMP_TMPFS_BYTES)):
        size = isolation[mount]["size_bytes"]
        if type(size) is not int or not 0 < size <= maximum:
            raise DockerQualificationError(f"effective policy {mount} size is outside its maximum")
    process = candidate["process"]
    if (process["entrypoint"] != ["/usr/bin/env", "-i"]
            or process["shell"] != ["/bin/sh", "-c"] or process["working_directory"] != "/input"
            or not process["environment"]
            or not all(isinstance(item, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*=[^\x00\n]*", item)
                       for item in process["environment"])
            or len({item.split("=", 1)[0] for item in process["environment"]})
            != len(process["environment"])):
        raise DockerQualificationError("effective policy process is not a closed sanitized environment")
    resources = candidate["resources"]
    integer_limits = {
        "nano_cpus": CPU_NANO, "memory_bytes": MEMORY_BYTES,
        "memory_swap_bytes": MEMORY_BYTES, "pids_limit": PIDS_LIMIT,
    }
    for key, maximum in integer_limits.items():
        value = resources[key]
        if type(value) is not int or not 0 < value <= maximum:
            raise DockerQualificationError(f"effective policy resource {key} is outside its maximum")
    if resources["memory_swap_bytes"] != resources["memory_bytes"]:
        raise DockerQualificationError("effective policy must disable additional swap")
    expected_ulimit_names = {"nofile", "core", "fsize"}
    if set(resources["ulimits"]) != expected_ulimit_names:
        raise DockerQualificationError("effective policy ulimits are not closed")
    for name, pair in resources["ulimits"].items():
        if (not isinstance(pair, list) or len(pair) != 2
                or any(type(value) is not int or value < 0 for value in pair)
                or pair[0] != pair[1]):
            raise DockerQualificationError(f"effective policy ulimit {name} is malformed")
    if resources["ulimits"]["core"] != [0, 0]:
        raise DockerQualificationError("effective policy must disable core dumps")
    bounds = candidate["bounds"]
    for key, maximum in POLICY_TEMPLATE["bounds"].items():
        value = bounds[key]
        if (type(value) not in {int, float} or isinstance(value, bool)
                or not 0 < value <= maximum
                or key in {"staging_max_files", "staging_max_bytes"} and type(value) is not int):
            raise DockerQualificationError(f"effective policy bound {key} is outside its maximum")
    return candidate


def policy_digest(policy: Mapping[str, Any]) -> str:
    return canonical_digest(policy)


def canonical_directory(value: str | os.PathLike[str]) -> Path:
    supplied = Path(value).expanduser()
    resolved = supplied.resolve(strict=True)
    if supplied != resolved or not supplied.is_absolute() or not resolved.is_dir():
        raise DockerQualificationError("Docker mounts require an existing canonical absolute directory")
    if "\n" in str(resolved) or "\x00" in str(resolved):
        raise DockerQualificationError("Docker mount path contains a forbidden character")
    return resolved


def qualification_labels(run_id: str, role: str, digest: str) -> dict[str, str]:
    if not SAFE_LABEL_VALUE.fullmatch(run_id) or not SAFE_LABEL_VALUE.fullmatch(role):
        raise DockerQualificationError("qualification run and role labels must be closed safe values")
    return {
        OWNER_LABEL: OWNER_VALUE,
        SCHEMA_LABEL: str(SCHEMA_VERSION),
        POLICY_LABEL: digest,
        RUN_LABEL: run_id,
        ROLE_LABEL: role,
    }


def docker_create_argv(
    policy: Mapping[str, Any], *, input_dir: Path, name: str, labels: Mapping[str, str],
    command: str, network: str | None = None,
) -> list[str]:
    """Generate the exact create argv from the validated policy SSOT."""
    policy = validate_policy(policy)
    runtime = policy["runtime_identity"]
    lifecycle = policy["container_lifecycle"]
    isolation = policy["isolation"]
    process = policy["process"]
    resources = policy["resources"]
    cli = runtime["docker_cli"]
    if not isinstance(cli, str) or cli != str(Path(cli).resolve(strict=True)):
        raise DockerQualificationError("effective policy lacks the qualified absolute Docker CLI")
    source = canonical_directory(input_dir)
    if not isinstance(command, str) or not command:
        raise DockerQualificationError("workload command must be a nonempty string")
    if not name.startswith(lifecycle["name_prefix"]) or not SAFE_LABEL_VALUE.fullmatch(name):
        raise DockerQualificationError("container name is outside the qualification namespace")
    expected = set(lifecycle["label_keys"])
    if set(labels) != expected or any(type(v) is not str for v in labels.values()):
        raise DockerQualificationError("container labels do not match the closed policy")
    run_label = labels[RUN_LABEL]
    role_label = labels[ROLE_LABEL]
    if labels != qualification_labels(run_label, role_label, policy_digest(policy)):
        raise DockerQualificationError("container labels do not match the closed policy")
    network_mode = network or isolation["network"]
    uid, gid = isolation["user"].split(":", 1)

    def tmpfs(spec: Mapping[str, Any]) -> str:
        flags = ["rw", "exec" if spec["exec"] else "noexec"]
        flags.extend(name for name in ("nosuid", "nodev") if spec[name])
        flags.extend((f"size={spec['size_bytes']}", f"mode={spec['mode']}", f"uid={uid}", f"gid={gid}"))
        return f"{spec['destination']}:" + ",".join(flags)

    argv = [
        cli, "container", "create", f"--pull={policy['image']['pull']}", "--name", name,
        f"--network={network_mode}", "--read-only", f"--cap-drop={isolation['cap_drop'][0]}",
        f"--security-opt=no-new-privileges={str(isolation['no_new_privileges']).lower()}",
        f"--user={isolation['user']}", "--init", f"--log-driver={isolation['log_driver']}",
        f"--cpus={resources['nano_cpus'] / 1_000_000_000:g}",
        f"--memory={resources['memory_bytes']}", f"--memory-swap={resources['memory_swap_bytes']}",
        f"--pids-limit={resources['pids_limit']}", f"--shm-size={isolation['shm_bytes']}",
    ]
    for ulimit_name in sorted(resources["ulimits"]):
        soft, hard = resources["ulimits"][ulimit_name]
        argv.append(f"--ulimit={ulimit_name}={soft}:{hard}")
    mount = isolation["input_mount"]
    argv.extend((
        "--mount", f"type={mount['type']},src={source},dst={mount['destination']},readonly",
        "--tmpfs", tmpfs(isolation["work_tmpfs"]), "--tmpfs", tmpfs(isolation["tmp_tmpfs"]),
        f"--workdir={process['working_directory']}",
    ))
    for key in sorted(labels):
        argv.extend(("--label", f"{key}={labels[key]}"))
    argv.extend((policy["image"]["repo_digest"], *process["entrypoint"],
                 *process["environment"], *process["shell"], command))
    return argv


@dataclass(frozen=True)
class WorkloadResult:
    exit_code: int
    timed_out: bool
    output_limited: bool
    oom_killed: bool
    stdout: StreamCapture
    stderr: StreamCapture
    elapsed_ms: int
    container_id: str
    container_inspect: dict[str, Any]

    def report(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "output_limited": self.output_limited,
            "oom_killed": self.oom_killed,
            "stdout": self.stdout.report(),
            "stderr": self.stderr.report(),
            "elapsed_ms": self.elapsed_ms,
            "container_id": self.container_id,
        }


@dataclass(frozen=True)
class Census:
    all_containers: tuple[str, ...]
    qualification_containers: tuple[str, ...]
    all_networks: tuple[str, ...]
    qualification_networks: tuple[str, ...]

    def report(self) -> dict[str, Any]:
        def fact(values: tuple[str, ...]) -> dict[str, Any]:
            return {"count": len(values), "digest": canonical_digest(list(values)), "ids": list(values)}
        return {
            "all_containers": fact(self.all_containers),
            "qualification_containers": fact(self.qualification_containers),
            "all_networks": fact(self.all_networks),
            "qualification_networks": fact(self.qualification_networks),
        }


class DockerClient:
    def __init__(
        self, identity: DockerRuntimeIdentity, policy: Mapping[str, Any], runner: Runner,
        *, env: Mapping[str, str] | None = None,
    ) -> None:
        if not identity.available or identity.docker_cli is None:
            raise DockerQualificationError("Docker client requires an available runtime identity")
        self.policy = validate_policy(policy)
        if self.policy["runtime_identity"] != effective_policy(identity)["runtime_identity"]:
            raise DockerQualificationError("effective policy runtime identity does not match discovery")
        self.identity = identity
        self.digest = policy_digest(self.policy)
        self.runner = runner
        self.env = docker_client_environment(self.policy)
        self.cli = self.policy["runtime_identity"]["docker_cli"]
        self._counter = 0

    def call(
        self, args: Sequence[str], *, timeout_seconds: float | None = None,
        output_limit_bytes: int | None = None,
    ) -> CommandResult:
        bounds = self.policy["bounds"]
        timeout = bounds["control_timeout_seconds"] if timeout_seconds is None else timeout_seconds
        output_limit = (bounds["control_output_limit_bytes"]
                        if output_limit_bytes is None else output_limit_bytes)
        return self.runner.run(
            (self.cli, *args), timeout_seconds=timeout,
            output_limit_bytes=output_limit, env=self.env)

    def json_call(self, args: Sequence[str], operation: str) -> Any:
        return _json_result(self.call(args), operation)

    def json_call_with_timeout(
        self, args: Sequence[str], operation: str, timeout_seconds: float,
    ) -> Any:
        return _json_result(self.call(args, timeout_seconds=timeout_seconds), operation)

    def inspect_container(self, container_id: str) -> dict[str, Any]:
        value = self.json_call(
            ("container", "inspect", container_id, "--format", "{{json .}}"),
            "docker container inspect")
        if not isinstance(value, dict):
            raise DockerInfrastructureError("Docker container inspect did not return an object")
        return value

    def _new_identity(self, run_id: str, role: str) -> tuple[str, dict[str, str]]:
        self._counter += 1
        name = f"{self.policy['container_lifecycle']['name_prefix']}{run_id[:12]}-{self._counter:03d}"
        return name, qualification_labels(run_id, role, self.digest)

    def _owned_container(
        self, doc: Mapping[str, Any], expected_labels: Mapping[str, str] | None = None,
    ) -> bool:
        try:
            identifier = doc["Id"]
            name = doc["Name"].removeprefix("/")
            labels = doc["Config"]["Labels"]
            requested_image = doc["Config"]["Image"]
            effective_image = doc["Image"]
        except (KeyError, TypeError, AttributeError):
            return False
        baseline = {
            OWNER_LABEL: OWNER_VALUE,
            SCHEMA_LABEL: str(self.policy["schema_version"]),
            POLICY_LABEL: self.digest,
        }
        runtime = self.policy["runtime_identity"]
        return (
            isinstance(identifier, str) and CONTAINER_ID.fullmatch(identifier) is not None
            and isinstance(name, str) and name.startswith(
                self.policy["container_lifecycle"]["name_prefix"])
            and isinstance(labels, dict) and all(labels.get(k) == v for k, v in baseline.items())
            and SAFE_LABEL_VALUE.fullmatch(str(labels.get(RUN_LABEL, ""))) is not None
            and SAFE_LABEL_VALUE.fullmatch(str(labels.get(ROLE_LABEL, ""))) is not None
            and (expected_labels is None
                 or all(labels.get(k) == v for k, v in expected_labels.items()))
            and requested_image == self.policy["image"]["repo_digest"]
            and effective_image == runtime["image_config_id"]
        )

    def _assert_policy_inspect(
        self, doc: Mapping[str, Any], *, input_dir: Path, name: str,
        labels: Mapping[str, str], network: str,
    ) -> None:
        if not self._owned_container(doc, labels):
            raise DockerInfrastructureError("created container identity/labels/image do not match policy")
        config = doc.get("Config", {})
        host = doc.get("HostConfig", {})
        policy = self.policy
        isolation = policy["isolation"]
        process = policy["process"]
        resources = policy["resources"]
        if (doc.get("Name") != f"/{name}" or config.get("User") != isolation["user"]
                or config.get("WorkingDir") != process["working_directory"]):
            raise DockerInfrastructureError("created container user/name/workdir do not match policy")
        expected_cmd = [*process["entrypoint"], *process["environment"], *process["shell"]]
        cmd = config.get("Cmd")
        if not isinstance(cmd, list) or cmd[:-1] != expected_cmd:
            raise DockerInfrastructureError("created container command/environment do not match policy")
        ulimits = {row.get("Name"): (row.get("Soft"), row.get("Hard"))
                   for row in host.get("Ulimits", []) if isinstance(row, dict)}
        expected_ulimits = {key: tuple(value) for key, value in resources["ulimits"].items()}
        security = host.get("SecurityOpt") or []
        cap_drop = isolation["cap_drop"]
        if (host.get("NetworkMode") != network or host.get("ReadonlyRootfs") is not True
                or host.get("AutoRemove") is not policy["container_lifecycle"]["auto_remove"]
                or host.get("CapDrop") != cap_drop
                or not any(str(item).startswith("no-new-privileges") for item in security)
                or host.get("NanoCpus") != resources["nano_cpus"]
                or host.get("Memory") != resources["memory_bytes"]
                or host.get("MemorySwap") != resources["memory_swap_bytes"]
                or host.get("PidsLimit") != resources["pids_limit"]
                or host.get("ShmSize") != isolation["shm_bytes"] or ulimits != expected_ulimits
                or host.get("LogConfig", {}).get("Type") != isolation["log_driver"]
                or host.get("Init") is not isolation["init"]):
            raise DockerInfrastructureError("created container host controls do not match policy")
        mounts = doc.get("Mounts") or []
        input_mount = isolation["input_mount"]
        inputs = [row for row in mounts if row.get("Destination") == input_mount["destination"]]
        if (len(inputs) != 1 or inputs[0].get("Type") != input_mount["type"]
                or inputs[0].get("RW") is input_mount["readonly"]
                or inputs[0].get("Source") != str(input_dir)):
            raise DockerInfrastructureError("created container input mount does not match policy")
        tmpfs = host.get("Tmpfs") or {}
        expected_destinations = {
            isolation["work_tmpfs"]["destination"], isolation["tmp_tmpfs"]["destination"]}
        if not isinstance(tmpfs, dict) or set(tmpfs) != expected_destinations:
            raise DockerInfrastructureError("created container tmpfs mount set does not match policy")
        for mount_name in ("work_tmpfs", "tmp_tmpfs"):
            spec = isolation[mount_name]
            options = tmpfs[spec["destination"]].split(",")
            required = {
                "rw", "exec" if spec["exec"] else "noexec",
                f"size={spec['size_bytes']}", f"mode={spec['mode']}",
            }
            required.update(flag for flag in ("nosuid", "nodev") if spec[flag])
            if not required.issubset(set(options)):
                raise DockerInfrastructureError("created container tmpfs mounts do not match policy")

    def assert_policy_inspect(
        self, doc: Mapping[str, Any], *, container_id: str, input_dir: Path,
        name: str, labels: Mapping[str, str], network: str,
    ) -> None:
        if doc.get("Id") != container_id or CONTAINER_ID.fullmatch(container_id) is None:
            raise DockerInfrastructureError("container inspect full ID does not match")
        self._assert_policy_inspect(
            doc, input_dir=input_dir, name=name, labels=labels, network=network)

    def create_container_with_identity(
        self, *, name: str, labels: Mapping[str, str], input_dir: Path, command: str,
        network: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Create and inspect using a caller-persisted exact identity."""
        network_mode = network or self.policy["isolation"]["network"]
        argv = docker_create_argv(
            self.policy, input_dir=input_dir, name=name, labels=labels,
            command=command, network=network)
        bounds = self.policy["bounds"]
        began = time.monotonic()

        def remaining() -> float:
            left = bounds["create_inspect_timeout_seconds"] - (time.monotonic() - began)
            if left <= 0:
                raise DockerInfrastructureError("create/inspect deadline expired")
            return min(bounds["control_timeout_seconds"], left)

        created = self.runner.run(
            argv, timeout_seconds=remaining(),
            output_limit_bytes=bounds["control_output_limit_bytes"], env=self.env)
        identifier = _text(created, "docker container create").splitlines()
        if len(identifier) != 1 or CONTAINER_ID.fullmatch(identifier[0]) is None:
            raise DockerInfrastructureError("docker create did not return one full container ID")
        container_id = identifier[0]
        doc = self.json_call_with_timeout(
            ("container", "inspect", container_id, "--format", "{{json .}}"),
            "docker container inspect", remaining())
        if not isinstance(doc, dict):
            raise DockerInfrastructureError("Docker container inspect did not return an object")
        try:
            self.assert_policy_inspect(
                doc, container_id=container_id, input_dir=input_dir,
                name=name, labels=labels, network=network_mode)
        except BaseException as failure:
            if self._owned_container(doc, labels):
                try:
                    self.remove_owned(container_id, labels, doc)
                except BaseException as cleanup_failure:
                    raise failure from cleanup_failure
            raise
        return container_id, doc

    def create_container(
        self, *, run_id: str, role: str, input_dir: Path, command: str,
        network: str | None = None,
    ) -> tuple[str, str, dict[str, str], dict[str, Any]]:
        name, labels = self._new_identity(run_id, role)
        container_id, doc = self.create_container_with_identity(
            name=name, labels=labels, input_dir=input_dir, command=command, network=network)
        return container_id, name, labels, doc

    def full_container_ids(self, *, timeout_seconds: float) -> tuple[str, ...]:
        """Return one bounded, successful, full-ID census without treating CLI failure as absence."""
        text = _text(self.call(
            ("container", "ls", "--all", "--quiet", "--no-trunc"),
            timeout_seconds=timeout_seconds), "docker full container census")
        identifiers = tuple(filter(None, text.splitlines()))
        if (len(identifiers) != len(set(identifiers))
                or any(CONTAINER_ID.fullmatch(value) is None for value in identifiers)):
            raise DockerCleanupError("full container census returned malformed IDs")
        return identifiers

    def known_container(
        self, *, container_id: str, name: str, labels: Mapping[str, str],
        input_dir: Path, network: str, timeout_seconds: float,
    ) -> dict[str, Any] | None:
        """Inspect a recorded full ID first; prove its absence only by successful full census."""
        if CONTAINER_ID.fullmatch(container_id) is None:
            raise DockerCleanupError("known container ID is malformed")
        began = time.monotonic()

        def remaining() -> float:
            left = timeout_seconds - (time.monotonic() - began)
            if left <= 0:
                raise DockerCleanupError("known container inspection deadline expired")
            return left

        inspected = self.call(
            ("container", "inspect", container_id, "--format", "{{json .}}"),
            timeout_seconds=remaining())
        if not inspected.timed_out and not inspected.output_limited and inspected.returncode == 0:
            doc = _json_result(inspected, "docker known container inspect")
            if not isinstance(doc, dict):
                raise DockerCleanupError("known container inspect is malformed")
            self.assert_policy_inspect(
                doc, container_id=container_id, input_dir=input_dir,
                name=name, labels=labels, network=network)
            return doc
        if inspected.timed_out or inspected.output_limited:
            raise DockerCleanupError("known container inspect was not bounded successfully")
        identifiers = self.full_container_ids(timeout_seconds=remaining())
        if container_id in identifiers:
            raise DockerCleanupError("known full ID still exists after failed inspect")
        if self.exact_candidates(
                name=name, labels=labels, input_dir=input_dir, network=network,
                timeout_seconds=remaining()):
            raise DockerCleanupError("replacement container collides with journal identity")
        return None

    def exact_candidates(
        self, *, name: str, labels: Mapping[str, str], input_dir: Path, network: str,
        timeout_seconds: float | None = None,
    ) -> tuple[tuple[str, dict[str, Any]], ...]:
        """List by all labels, then establish exact identity only through inspect."""
        expected = set(self.policy["container_lifecycle"]["label_keys"])
        if set(labels) != expected:
            raise DockerCleanupError("exact candidate labels are incomplete")
        timeout = (self.policy["bounds"]["cleanup_timeout_seconds"]
                   if timeout_seconds is None else timeout_seconds)
        began = time.monotonic()

        def remaining() -> float:
            left = timeout - (time.monotonic() - began)
            if left <= 0:
                raise DockerCleanupError("exact container census deadline expired")
            return left

        args = ["container", "ls", "--all", "--quiet", "--no-trunc"]
        for key in sorted(labels):
            args.extend(("--filter", f"label={key}={labels[key]}"))
        listed = _text(self.call(tuple(args), timeout_seconds=remaining()),
                       "docker exact container census")
        identifiers = tuple(filter(None, listed.splitlines()))
        if (len(identifiers) != len(set(identifiers)) or len(identifiers) > 1
                or any(CONTAINER_ID.fullmatch(value) is None for value in identifiers)):
            raise DockerCleanupError("exact container census is malformed or ambiguous")
        rows = []
        for container_id in identifiers:
            doc = self.json_call_with_timeout(
                ("container", "inspect", container_id, "--format", "{{json .}}"),
                "docker exact container inspect", remaining())
            if not isinstance(doc, dict):
                raise DockerCleanupError("exact container inspect is malformed")
            self.assert_policy_inspect(
                doc, container_id=container_id, input_dir=input_dir,
                name=name, labels=labels, network=network)
            rows.append((container_id, doc))
        return tuple(rows)

    def remove_exact(
        self, *, container_id: str, name: str, labels: Mapping[str, str],
        input_dir: Path, network: str, deadline: float | None = None,
    ) -> None:
        """Re-inspect, remove one full ID, then prove absence by successful census."""
        timeout = self.policy["bounds"]["cleanup_timeout_seconds"]
        phase_deadline = time.monotonic() + timeout
        if deadline is not None:
            phase_deadline = min(phase_deadline, deadline)

        def remaining() -> float:
            left = phase_deadline - time.monotonic()
            if left <= 0:
                raise DockerCleanupError("container cleanup deadline expired")
            return left

        doc = self.known_container(
            container_id=container_id, name=name, labels=labels, input_dir=input_dir,
            network=network, timeout_seconds=remaining())
        if doc is None:
            raise DockerCleanupError("exact container disappeared before removal")
        removed = self.call(
            ("container", "rm", "--force", container_id),
            timeout_seconds=remaining())
        removed_ids = _text(removed, "docker container rm").splitlines()
        if removed_ids != [container_id]:
            raise DockerCleanupError("docker rm did not confirm the exact full ID")
        if self.known_container(
                container_id=container_id, name=name, labels=labels, input_dir=input_dir,
                network=network, timeout_seconds=remaining()) is not None:
            raise DockerCleanupError("container removal was not verified by full-ID census")

    def remove_owned(
        self, container_id: str, labels: Mapping[str, str], doc: Mapping[str, Any],
    ) -> None:
        if not self._owned_container(doc, labels) or doc.get("Id") != container_id:
            raise DockerCleanupError("refusing to remove a container without exact owned identity")
        try:
            name = doc["Name"].removeprefix("/")
            network = doc["HostConfig"]["NetworkMode"]
            mounts = [row for row in doc["Mounts"] if row.get("Destination") == "/input"]
            input_dir = Path(mounts[0]["Source"])
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise DockerCleanupError("owned container cleanup facts are malformed") from exc
        self.remove_exact(
            container_id=container_id, name=name, labels=labels,
            input_dir=input_dir, network=network)

    def execute(
        self, *, run_id: str, role: str, input_dir: Path, command: str,
        timeout_seconds: float | None = None, output_limit_bytes: int | None = None,
        network: str | None = None,
    ) -> WorkloadResult:
        bounds = self.policy["bounds"]
        timeout = bounds["workload_timeout_seconds"] if timeout_seconds is None else timeout_seconds
        output_limit = (bounds["workload_output_limit_bytes"]
                        if output_limit_bytes is None else output_limit_bytes)
        if timeout <= 0 or timeout > bounds["workload_timeout_seconds"]:
            raise DockerQualificationError("workload timeout must narrow the qualified maximum")
        if output_limit <= 0 or output_limit > bounds["workload_output_limit_bytes"]:
            raise DockerQualificationError("workload output limit must narrow the qualified maximum")
        container_id = name = ""
        labels: dict[str, str] | None = None
        initial: dict[str, Any] | None = None
        failure: BaseException | None = None
        result: WorkloadResult | None = None
        try:
            container_id, name, labels, initial = self.create_container(
                run_id=run_id, role=role, input_dir=input_dir, command=command, network=network)
            started = self.call(
                ("container", "start", "--attach", container_id),
                timeout_seconds=timeout, output_limit_bytes=output_limit)
            if started.timed_out:
                result = WorkloadResult(
                    TIMEOUT_EXIT_CODE, True, False, False, started.stdout, started.stderr,
                    started.elapsed_ms, container_id, initial)
            elif started.output_limited:
                result = WorkloadResult(
                    OUTPUT_LIMIT_EXIT_CODE, False, True, False, started.stdout, started.stderr,
                    started.elapsed_ms, container_id, initial)
            else:
                after = self.inspect_container(container_id)
                state = after.get("State")
                if (not isinstance(state, dict) or state.get("Status") != "exited"
                        or state.get("Running") is not False
                        or isinstance(state.get("ExitCode"), bool)
                        or not isinstance(state.get("ExitCode"), int)
                        or not isinstance(state.get("Error", ""), str)):
                    raise DockerInfrastructureError("container did not reach an inspectable exited state")
                if state.get("Error"):
                    raise DockerInfrastructureError(f"container runtime error: {state['Error']}")
                # Docker start commonly returns the workload code, including 125.  State.ExitCode,
                # never the CLI code, is the workload verdict.
                result = WorkloadResult(
                    state["ExitCode"], False, False, state.get("OOMKilled") is True,
                    started.stdout, started.stderr, started.elapsed_ms, container_id, after)
        except BaseException as exc:  # cleanup must still run, then the original failure is preserved
            failure = exc
        cleanup_failure: BaseException | None = None
        if container_id and labels is not None and initial is not None:
            try:
                self.remove_owned(container_id, labels, initial)
            except BaseException as exc:
                cleanup_failure = exc
        if failure is not None and not isinstance(failure, Exception):
            if cleanup_failure is not None:
                raise failure from cleanup_failure
            raise failure
        if cleanup_failure is not None:
            raise DockerCleanupError(str(cleanup_failure)) from cleanup_failure
        if failure is not None:
            raise failure
        assert result is not None
        return result

    def create_detached(
        self, *, run_id: str, role: str, input_dir: Path, command: str, network: str,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        container_id, _name, labels, doc = self.create_container(
            run_id=run_id, role=role, input_dir=input_dir, command=command, network=network)
        started = self.call(("container", "start", container_id))
        try:
            _text(started, "docker container start")
        except BaseException:
            self.remove_owned(container_id, labels, doc)
            raise
        return container_id, labels, doc

    def recover_orphans(self) -> tuple[str, ...]:
        listed = self.call((
            "container", "ls", "--all", "--quiet", "--no-trunc",
            "--filter", f"label={OWNER_LABEL}={OWNER_VALUE}",
            "--filter", f"label={SCHEMA_LABEL}={SCHEMA_VERSION}",
            "--filter", f"label={POLICY_LABEL}={self.digest}",
        ))
        text = _text(listed, "docker qualification orphan census")
        recovered: list[str] = []
        for container_id in sorted(filter(None, text.splitlines())):
            if CONTAINER_ID.fullmatch(container_id) is None:
                raise DockerInfrastructureError("orphan census returned a malformed container ID")
            doc = self.inspect_container(container_id)
            if not self._owned_container(doc):
                continue
            labels = doc["Config"]["Labels"]
            self.remove_owned(container_id, labels, doc)
            recovered.append(container_id)
        return tuple(recovered)

    def census(self) -> Census:
        def ids(args: Sequence[str], operation: str) -> tuple[str, ...]:
            text = _text(self.call(args), operation)
            values = tuple(sorted(filter(None, text.splitlines())))
            if any(re.fullmatch(r"[0-9a-f]{12,64}", value) is None for value in values):
                raise DockerInfrastructureError(f"{operation} returned a malformed ID")
            return values
        return Census(
            ids(("container", "ls", "--all", "--quiet", "--no-trunc"), "container census"),
            ids(("container", "ls", "--all", "--quiet", "--no-trunc", "--filter",
                 f"label={OWNER_LABEL}={OWNER_VALUE}"), "qualification container census"),
            ids(("network", "ls", "--quiet", "--no-trunc"), "network census"),
            ids(("network", "ls", "--quiet", "--no-trunc", "--filter",
                 f"label={OWNER_LABEL}={OWNER_VALUE}"), "qualification network census"),
        )


@dataclass(frozen=True)
class ProbeResult:
    name: str
    passed: bool
    elapsed_ms: int
    details: dict[str, Any]


def _probe(name: str, function) -> ProbeResult:
    began = time.monotonic_ns()
    try:
        passed, details = function()
    except Exception as exc:  # a probe failure must still reach the qualification-wide cleanup census
        passed, details = False, {"error": str(exc), "error_class": type(exc).__name__}
    return ProbeResult(name, bool(passed), (time.monotonic_ns() - began) // 1_000_000, details)


def _input_tree(prefix: str):
    return tempfile.TemporaryDirectory(prefix=prefix)


def filesystem_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        with _input_tree("siffran-docker-files-") as raw, tempfile.TemporaryDirectory(
                prefix="siffran-docker-host-canary-") as outside:
            root = Path(raw).resolve(strict=True)
            fixture = root / "fixture.txt"
            fixture.write_text("expected\n", encoding="utf-8")
            canary = Path(outside).resolve(strict=True) / "secret.txt"
            canary.write_text("host-secret\n", encoding="utf-8")
            command = (
                "set -eu; test \"$(cat /input/fixture.txt)\" = expected; "
                "if chmod u+w /input/fixture.txt 2>/dev/null || printf changed >> /input/fixture.txt 2>/dev/null; "
                "then exit 31; fi; "
                f"if cat {shlex.quote(str(canary))} >/dev/null 2>&1; then exit 32; fi")
            result = client.execute(
                run_id=run_id, role="filesystem", input_dir=root, command=command)
            unchanged = fixture.read_text(encoding="utf-8") == "expected\n" \
                and canary.read_text(encoding="utf-8") == "host-secret\n"
            return result.exit_code == 0 and unchanged, {**result.report(), "host_canary_unchanged": unchanged}
    return _probe("filesystem", run)


def environment_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        with _input_tree("siffran-docker-env-") as raw:
            root = Path(raw).resolve(strict=True)
            (root / "fixture").write_text("x", encoding="utf-8")
            result = client.execute(
                run_id=run_id, role="environment", input_dir=root,
                command="test -z \"${QUALIFICATION_FAKE_SECRET+x}\"; /usr/bin/env")
            observed = set(result.stdout.data.decode("utf-8", errors="replace").splitlines())
            expected = set(EXPLICIT_ENVIRONMENT)
            return result.exit_code == 0 and observed == expected, {
                **result.report(), "observed_environment": sorted(observed),
                "expected_environment": sorted(expected),
            }
    return _probe("environment", run)


def writable_storage_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        with _input_tree("siffran-docker-storage-") as raw:
            root = Path(raw).resolve(strict=True)
            (root / "fixture").write_text("x", encoding="utf-8")
            command = (
                "set -eu; touch /work/ok /tmp/ok; "
                "if touch /etc/qualification-forbidden 2>/dev/null; then exit 41; fi; "
                "set +e; dd if=/dev/zero of=/work/fill bs=1048576 count=80 2>/dev/null; ws=$?; "
                "rm -f /work/fill; dd if=/dev/zero of=/tmp/fill bs=1048576 count=24 2>/dev/null; ts=$?; "
                "rm -f /tmp/fill; set -e; test $ws -ne 0; test $ts -ne 0; "
                "df -B1 /work /tmp | tail -n +2")
            result = client.execute(
                run_id=run_id, role="storage", input_dir=root, command=command)
            return result.exit_code == 0, {
                **result.report(), "configured_work_bytes": WORK_TMPFS_BYTES,
                "configured_tmp_bytes": TMP_TMPFS_BYTES,
                "configured_user_writable_total_bytes": WORK_TMPFS_BYTES + TMP_TMPFS_BYTES + SHM_BYTES,
            }
    return _probe("writable_storage", run)


def docker_socket_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        with _input_tree("siffran-docker-socket-") as raw:
            root = Path(raw).resolve(strict=True)
            (root / "fixture").write_text("x", encoding="utf-8")
            result = client.execute(
                run_id=run_id, role="socket", input_dir=root,
                command="test ! -e /var/run/docker.sock; test ! -S /var/run/docker.sock")
            return result.exit_code == 0, result.report()
    return _probe("docker_socket", run)


SERVER_RS = r"""use std::io::{Read, Write};
use std::net::TcpListener;
fn main() {
    let listener = TcpListener::bind("0.0.0.0:43123").unwrap();
    for stream in listener.incoming() {
        let mut stream = stream.unwrap();
        let mut byte = [0u8; 1];
        stream.read_exact(&mut byte).unwrap();
        stream.write_all(b"q").unwrap();
    }
}
"""
def _network_labels(run_id: str, digest: str) -> dict[str, str]:
    return qualification_labels(run_id, "network", digest)


def network_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        name = f"{NAME_PREFIX}{run_id[:12]}-net"
        labels = _network_labels(run_id, client.digest)
        argv = ["network", "create", "--driver=bridge", "--internal"]
        for key in sorted(labels):
            argv.extend(("--label", f"{key}={labels[key]}"))
        argv.append(name)
        network_id = ""
        sentinel_id = ""
        sentinel_labels: dict[str, str] | None = None
        sentinel_doc: dict[str, Any] | None = None
        cleanup_errors: list[str] = []
        positive: WorkloadResult | None = None
        denied: WorkloadResult | None = None
        try:
            network_id = _text(client.call(tuple(argv)), "docker internal network create")
            if CONTAINER_ID.fullmatch(network_id) is None:
                raise DockerInfrastructureError("network create did not return one full ID")
            network_doc = client.json_call(
                ("network", "inspect", network_id, "--format", "{{json .}}"),
                "docker network inspect")
            if (not isinstance(network_doc, dict) or network_doc.get("Name") != name
                    or network_doc.get("Internal") is not True
                    or network_doc.get("Labels") != labels):
                raise DockerInfrastructureError("temporary internal network identity mismatches")
            with _input_tree("siffran-docker-net-server-") as server_raw, \
                    _input_tree("siffran-docker-net-client-") as client_raw:
                server_root = Path(server_raw).resolve(strict=True)
                client_root = Path(client_raw).resolve(strict=True)
                (server_root / "server.rs").write_text(SERVER_RS, encoding="utf-8")
                (client_root / "fixture").write_text("network client\n", encoding="utf-8")
                sentinel_id, sentinel_labels, sentinel_doc = client.create_detached(
                    run_id=run_id, role="net-sentinel", input_dir=server_root, network=name,
                    command="rustc /input/server.rs -o /work/server && exec /work/server")
                endpoint_host = sentinel_doc["Name"].removeprefix("/")
                endpoint = f"{endpoint_host}:43123"
                client_command = (
                    "/bin/bash -c 'exec 3<>/dev/tcp/$1/43123; printf q >&3; "
                    "IFS= read -r -n 1 reply <&3; test \"$reply\" = q' qualification-net "
                    + shlex.quote(endpoint_host))
                last_result: WorkloadResult | None = None
                last_error = None
                for attempt in range(8):
                    try:
                        positive = client.execute(
                            run_id=run_id, role=f"net-control-{attempt}", input_dir=client_root,
                            command=client_command, network=name, timeout_seconds=5)
                    except DockerQualificationError as exc:
                        last_error = exc
                        time.sleep(0.25)
                        continue
                    last_result = positive
                    if positive.exit_code == 0:
                        break
                    time.sleep(0.25)
                else:
                    sentinel_state = client.inspect_container(sentinel_id).get("State")
                    detail = last_result.report() if last_result is not None else str(last_error)
                    raise DockerInfrastructureError(
                        f"network positive control failed: client={detail!r}; sentinel={sentinel_state!r}")
                denied = client.execute(
                    run_id=run_id, role="net-denied", input_dir=client_root,
                    command=client_command, timeout_seconds=5)
                passed = positive.exit_code == 0 and denied.exit_code != 0
                details = {
                    "endpoint": endpoint, "network_id": network_id,
                    "positive_control": positive.report(), "network_none": denied.report(),
                    "traffic_scope": "Docker --internal temporary bridge only",
                }
        finally:
            if sentinel_id and sentinel_labels is not None and sentinel_doc is not None:
                try:
                    client.remove_owned(sentinel_id, sentinel_labels, sentinel_doc)
                except DockerQualificationError as exc:
                    cleanup_errors.append(str(exc))
            if network_id:
                try:
                    current = client.json_call(
                        ("network", "inspect", network_id, "--format", "{{json .}}"),
                        "network cleanup identity inspect")
                    if (not isinstance(current, dict) or current.get("Name") != name
                            or current.get("Labels") != labels):
                        raise DockerCleanupError("refusing to remove network without exact identity")
                    _text(client.call(("network", "rm", network_id)), "docker network rm")
                    verify = client.call(("network", "inspect", network_id, "--format", "{{json .Id}}"))
                    if verify.returncode == 0 or verify.timed_out or verify.output_limited:
                        raise DockerCleanupError("network removal was not verified")
                except DockerQualificationError as exc:
                    cleanup_errors.append(str(exc))
            if cleanup_errors:
                raise DockerCleanupError("; ".join(cleanup_errors))
        return passed, details
    return _probe("network", run)


def privilege_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        with _input_tree("siffran-docker-privilege-") as raw:
            root = Path(raw).resolve(strict=True)
            (root / "fixture").write_text("x", encoding="utf-8")
            command = (
                "set -eu; test \"$(id -u)\" = 65534; "
                "test \"$(awk '/CapEff/ {print $2}' /proc/self/status)\" = 0000000000000000; "
                "test \"$(awk '/NoNewPrivs/ {print $2}' /proc/self/status)\" = 1; "
                "if touch /root/forbidden 2>/dev/null; then exit 51; fi")
            result = client.execute(
                run_id=run_id, role="privilege", input_dir=root, command=command)
            return result.exit_code == 0, result.report()
    return _probe("privilege", run)


def engine_isolation_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        with _input_tree("siffran-docker-isolation-") as raw:
            root = Path(raw).resolve(strict=True)
            (root / "fixture").write_text("x", encoding="utf-8")
            command = (
                "set -eu; test \"$(awk '/Seccomp:/ {print $2}' /proc/self/status)\" = 2; "
                "test \"$(awk '/NoNewPrivs:/ {print $2}' /proc/self/status)\" = 1; "
                "ns=$(readlink /proc/self/ns/cgroup); case \"$ns\" in cgroup:\\[*\\]) ;; *) exit 52;; esac; "
                "printf 'seccomp=2\\nno_new_privileges=1\\ncgroup_namespace=%s\\n' \"$ns\"")
            result = client.execute(
                run_id=run_id, role="engine-isolation", input_dir=root, command=command)
            runtime = client.policy["runtime_identity"]
            isolation = client.policy["isolation"]
            expected_security = set(isolation["required_engine_security_options"])
            passed = (
                result.exit_code == 0 and runtime["untrusted_boundary_established"] is True
                and runtime["cgroup_version"] == isolation["required_cgroup_version"]
                and expected_security.issubset(set(runtime["engine_security_options"])))
            return passed, {
                **result.report(), "host_boundary": isolation["qualified_host_boundary"],
                "engine_security_options": runtime["engine_security_options"],
                "cgroup_version": runtime["cgroup_version"],
                "boundary_established": runtime["untrusted_boundary_established"],
            }
    return _probe("engine_isolation", run)


def resources_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        with _input_tree("siffran-docker-resources-") as raw:
            root = Path(raw).resolve(strict=True)
            (root / "fixture").write_text("x", encoding="utf-8")
            resources = client.policy["resources"]
            nofile = resources["ulimits"]["nofile"][0]
            memory = resources["memory_bytes"]
            pids = resources["pids_limit"]
            command = (
                "set -eu; printf 'nofile=%s\\n' \"$(ulimit -n)\"; "
                "printf 'core=%s\\n' \"$(ulimit -c)\"; printf 'fsize=%s\\n' \"$(ulimit -f)\"; "
                "printf 'cpu=%s\\n' \"$(cat /sys/fs/cgroup/cpu.max)\"; "
                "printf 'memory=%s\\n' \"$(cat /sys/fs/cgroup/memory.max)\"; "
                "printf 'swap=%s\\n' \"$(cat /sys/fs/cgroup/memory.swap.max)\"; "
                "printf 'pids=%s\\n' \"$(cat /sys/fs/cgroup/pids.max)\"; "
                f"test \"$(ulimit -n)\" = {nofile}; test \"$(ulimit -c)\" = 0; "
                f"test \"$(cat /sys/fs/cgroup/memory.max)\" = {memory}; "
                "test \"$(cat /sys/fs/cgroup/memory.swap.max)\" = 0; "
                f"test \"$(cat /sys/fs/cgroup/pids.max)\" = {pids}; "
                f"test \"$(cat /sys/fs/cgroup/cpu.max)\" = "
                f"'{resources['nano_cpus'] // 10_000} 100000'")
            result = client.execute(
                run_id=run_id, role="resources", input_dir=root, command=command)
            return result.exit_code == 0, {
                **result.report(), **resources,
                "cpu_enforcement_evidence": "cgroup-v2 cpu.max inspect only; no flaky timing canary",
            }
    return _probe("resources", run)


def memory_limit_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        memory_limit = client.policy["resources"]["memory_bytes"]
        allocation = memory_limit + 64 * 1024 * 1024
        source = f"""use std::hint::black_box;
fn main() {{
    let mut bytes = vec![0_u8; {allocation}];
    for offset in (0..bytes.len()).step_by(4096) {{
        unsafe {{ std::ptr::write_volatile(bytes.as_mut_ptr().add(offset), 1); }}
    }}
    black_box(&bytes);
    std::thread::sleep(std::time::Duration::from_secs(30));
}}
"""
        with _input_tree("siffran-docker-memory-") as raw:
            root = Path(raw).resolve(strict=True)
            (root / "memory.rs").write_text(source, encoding="utf-8")
            result = client.execute(
                run_id=run_id, role="memory-limit", input_dir=root,
                command="rustc /input/memory.rs -o /work/memory && /work/memory",
                timeout_seconds=30)
            passed = result.oom_killed and result.exit_code != 0 and not result.timed_out
            return passed, {
                **result.report(), "configured_memory_bytes": memory_limit,
                "attempted_allocation_bytes": allocation,
            }
    return _probe("memory_limit", run)


def pids_limit_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        pids_limit = client.policy["resources"]["pids_limit"]
        attempts = pids_limit + 32
        source = f"""use std::process::{{Child, Command}};
fn main() {{
    let mut children: Vec<Child> = Vec::new();
    let mut denied = false;
    for _ in 0..{attempts} {{
        match Command::new(\"/bin/sleep\").arg(\"10\").spawn() {{
            Ok(child) => children.push(child),
            Err(_) => {{ denied = true; break; }}
        }}
    }}
    println!(\"spawned={{}} denied={{}}\", children.len(), denied);
    for child in &mut children {{ let _ = child.kill(); let _ = child.wait(); }}
    if !denied || children.len() >= {attempts} {{ std::process::exit(61); }}
}}
"""
        with _input_tree("siffran-docker-pids-") as raw:
            root = Path(raw).resolve(strict=True)
            (root / "pids.rs").write_text(source, encoding="utf-8")
            result = client.execute(
                run_id=run_id, role="pids-limit", input_dir=root,
                command="rustc /input/pids.rs -o /work/pids && /work/pids",
                timeout_seconds=30)
            return result.exit_code == 0 and "denied=true" in result.stdout.tail, {
                **result.report(), "configured_pids_limit": pids_limit,
                "spawn_attempts": attempts,
            }
    return _probe("pids_limit", run)


def timeout_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        with _input_tree("siffran-docker-timeout-") as raw:
            root = Path(raw).resolve(strict=True)
            (root / "fixture").write_text("x", encoding="utf-8")
            result = client.execute(
                run_id=run_id, role="timeout", input_dir=root, command="sleep 60",
                timeout_seconds=0.5)
            return result.exit_code == TIMEOUT_EXIT_CODE and result.timed_out, result.report()
    return _probe("timeout", run)


def output_limit_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        with _input_tree("siffran-docker-output-") as raw:
            root = Path(raw).resolve(strict=True)
            (root / "fixture").write_text("x", encoding="utf-8")
            result = client.execute(
                run_id=run_id, role="output", input_dir=root,
                command="yes qualification-output", output_limit_bytes=4096, timeout_seconds=10)
            return (
                result.exit_code == OUTPUT_LIMIT_EXIT_CODE and result.output_limited
                and result.stdout.bytes_seen > 4096
            ), result.report()
    return _probe("output_limit", run)


def process_cleanup_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        with _input_tree("siffran-docker-cleanup-") as raw:
            root = Path(raw).resolve(strict=True)
            (root / "fixture").write_text("x", encoding="utf-8")
            result = client.execute(
                run_id=run_id, role="cleanup", input_dir=root,
                command="sleep 60 & echo $!; exit 0", timeout_seconds=10)
            return result.exit_code == 0, result.report()
    return _probe("process_cleanup", run)


def orphan_recovery_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        with _input_tree("siffran-docker-orphan-") as raw:
            root = Path(raw).resolve(strict=True)
            (root / "fixture").write_text("x", encoding="utf-8")
            container_id, _name, _labels, _doc = client.create_container(
                run_id=run_id, role="interrupted", input_dir=root, command="true")
            recovered = client.recover_orphans()
            return container_id in recovered, {"created_id": container_id, "recovered_ids": list(recovered)}
    return _probe("orphan_recovery", run)


def _write_rust_project(project: Path, *, large: bool) -> None:
    (project / "src").mkdir(parents=True)
    name = "qualification_large" if large else "qualification_normal"
    (project / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n', encoding="utf-8")
    (project / "Cargo.lock").write_text(
        "# This file is automatically @generated by Cargo.\n"
        "# It is not intended for manual editing.\n"
        f'version = 4\n\n[[package]]\nname = "{name}"\nversion = "0.1.0"\n', encoding="utf-8")
    source = (
        "pub static PAYLOAD: [u8; 4_000_000] = [7; 4_000_000];\n"
        "pub fn answer() -> u8 { PAYLOAD[3_999_999] }\n"
        "#[cfg(test)] mod tests { #[test] fn runs() { assert_eq!(super::answer(), 7); } }\n"
        if large else
        "pub fn answer() -> usize { 42 }\n"
        "#[cfg(test)] mod tests { #[test] fn runs() { assert_eq!(super::answer(), 42); } }\n")
    (project / "src" / "lib.rs").write_text(source, encoding="utf-8")


def rust_probe(client: DockerClient, run_id: str, *, large: bool) -> ProbeResult:
    name = "rust_large_artifact" if large else "rust"
    def run():
        with _input_tree(f"siffran-docker-{name}-") as raw:
            root = Path(raw).resolve(strict=True)
            project = root / "project"
            _write_rust_project(project, large=large)
            command = (
                "set -eu; cargo test --offline --locked --manifest-path /input/project/Cargo.toml; "
                "size=$(find /work/target/debug/deps -type f -perm -111 -printf '%s\\n' | sort -nr | head -1); "
                "used=$(du -sb /work | cut -f1); printf 'artifact_bytes=%s\\nwork_bytes=%s\\n' \"$size\" \"$used\"; "
                + ("test \"$size\" -gt 3145728" if large else "test \"$size\" -gt 0"))
            result = client.execute(
                run_id=run_id, role=name, input_dir=root, command=command,
                timeout_seconds=WORKLOAD_TIMEOUT_SECONDS)
            measurements = {}
            for line in result.stdout.data.decode("utf-8", errors="replace").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    if key in {"artifact_bytes", "work_bytes"} and value.isdigit():
                        measurements[key] = int(value)
            passed = result.exit_code == 0 and (
                not large or measurements.get("artifact_bytes", 0) > 3 * 1024 * 1024)
            return passed, {**result.report(), **measurements, "large": large,
                            "work_tmpfs_limit_bytes": WORK_TMPFS_BYTES}
    return _probe(name, run)


class QualificationDockerHarness:
    """Qualification-only implementation of the unchanged SpikeHarness port."""

    def __init__(self, client: DockerClient, run_id: str):
        self.client, self.run_id, self.counter = client, run_id, 0

    def run(self, command: str, snapshot: ExecutionSnapshot) -> HarnessResult:
        self.counter += 1
        with _input_tree("siffran-docker-contract-") as raw:
            root = Path(raw).resolve(strict=True)
            for binding, content in zip(snapshot.file_bindings, snapshot.captured_bytes):
                target = root.joinpath(*binding.path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            for path in sorted(root.rglob("*"), reverse=True):
                path.chmod(0o555 if path.is_dir() else 0o444)
            result = self.client.execute(
                run_id=self.run_id, role=f"contract-{self.counter}", input_dir=root,
                command=command)
            digest = canonical_digest({
                "backend": BACKEND, "command": command, "exit_code": result.exit_code,
                "output_limited": result.output_limited, "policy_sha256": self.client.digest,
                "snapshot_digest": snapshot.snapshot_digest,
                "stderr": result.stderr.report(), "stdout": result.stdout.report(),
                "timed_out": result.timed_out,
            })
            return HarnessResult(result.exit_code, digest, snapshot.snapshot_digest)


def empirica_contract_probe(client: DockerClient, run_id: str) -> ProbeResult:
    def run():
        with _input_tree("siffran-docker-contract-workspace-") as raw:
            root = Path(raw).resolve(strict=True)
            (root / "fixture.txt").write_text("expected\n", encoding="utf-8")
            workspace = FilesystemWorkspace(root)
            harness = QualificationDockerHarness(client, run_id)
            passing, pass_snapshot, _ = execute_spike_bound(
                'test "$(cat fixture.txt)" = expected', ("fixture.txt",), workspace, harness)
            failing, fail_snapshot, _ = execute_spike_bound(
                'test "$(cat fixture.txt)" = wrong', ("fixture.txt",), workspace, harness)
            passed = (
                passing.gate is Gate.PASS and passing.exit_code == 0
                and failing.gate is Gate.FAIL and failing.exit_code != 0
                and passing.file_bindings == pass_snapshot.file_bindings
                and failing.file_bindings == fail_snapshot.file_bindings
                and pass_snapshot.snapshot_digest == fail_snapshot.snapshot_digest
                and passing.result_digest != failing.result_digest
            )
            return passed, {
                "pass_exit_code": passing.exit_code, "pass_gate": passing.gate.value,
                "pass_result_digest": passing.result_digest,
                "fail_exit_code": failing.exit_code, "fail_gate": failing.gate.value,
                "fail_result_digest": failing.result_digest,
                "snapshot_digest": pass_snapshot.snapshot_digest,
            }
    return _probe("empirica_contract", run)


PROBE_METHODS = {
    "filesystem": filesystem_probe,
    "environment": environment_probe,
    "writable_storage": writable_storage_probe,
    "docker_socket": docker_socket_probe,
    "network": network_probe,
    "privilege": privilege_probe,
    "engine_isolation": engine_isolation_probe,
    "resources": resources_probe,
    "memory_limit": memory_limit_probe,
    "pids_limit": pids_limit_probe,
    "timeout": timeout_probe,
    "output_limit": output_limit_probe,
    "process_cleanup": process_cleanup_probe,
    "orphan_recovery": orphan_recovery_probe,
    "rust": lambda client, run: rust_probe(client, run, large=False),
    "rust_large_artifact": lambda client, run: rust_probe(client, run, large=True),
    "empirica_contract": empirica_contract_probe,
}


def cleanup_matches(before: Census | None, after: Census, recovered: Sequence[str]) -> bool:
    expected_containers = set(before.all_containers if before else ()) - set(recovered)
    expected_networks = set(before.all_networks if before else ())
    return (
        not after.qualification_containers and not after.qualification_networks
        and set(after.all_containers) == expected_containers
        and set(after.all_networks) == expected_networks)


def qualify_docker(
    probe_name: str, *, runner: Runner | None = None, cli_path: str | None = None,
    env: Mapping[str, str] | None = None,
    environment_inspector: Any = inspect_environment,
) -> dict[str, Any]:
    runner = runner or BoundedRunner()
    run_id = "q" + uuid.uuid4().hex
    generated = datetime.now(timezone.utc).isoformat()
    identity = discover_docker(runner, cli_path=cli_path, env=env)
    environment_identity: EnvironmentIdentity = environment_inspector()
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "backend": BACKEND,
        "qualification_run_id": run_id, "generated_at": generated,
        "preflight": asdict(identity),
        "environment_preflight": asdict(environment_identity),
        "environment_preflight_available": environment_identity.available,
        "policy": None, "policy_sha256": None,
        "probes": [], "passed": False, "requested_probe": probe_name,
        "preflight_available": identity.available, "selected_probe_passed": False,
        "complete_qualification": False, "docker_backend_eligible": False,
        "empirica_contract_compatible": False, "fallback_eligible": False,
        "bounded_repository_diagnostic_eligible": False,
        "qualified_for_untrusted_code": False,
        "recovered_orphan_container_ids": [], "census_before": None, "census_after": None,
        "cleanup_verified": False,
        "limitations": [
            "qualification is local to this exact CLI/context/engine/image/policy identity",
            "the operator report is not yet a production durable qualification record",
            "qualified boundary: Darwin host -> local Unix endpoint -> Docker Desktop Linux VM -> container",
            "this Darwin qualification does not qualify native Linux or any other engine class",
            "fallback_eligible remains false: no host-subprocess fallback is authorized",
        ],
    }
    if not identity.available or not environment_identity.available:
        return base
    if probe_name == "preflight":
        policy = effective_policy(identity)
        base.update(
            policy=policy,
            policy_sha256=policy_digest(policy),
            passed=True,
            selected_probe_passed=True,
        )
        return base
    policy = effective_policy(identity)
    client = DockerClient(identity, policy, runner, env=env)
    base["policy"] = policy
    base["policy_sha256"] = client.digest
    before: Census | None = None
    recovered: tuple[str, ...] = ()
    try:
        before = client.census()
        base["census_before"] = before.report()
        recovered = client.recover_orphans()
        base["recovered_orphan_container_ids"] = list(recovered)
        requested = MANDATORY_PROBES if probe_name == "all" else (() if probe_name == "preflight" else (probe_name,))
        probes: list[ProbeResult] = []
        security_failed = False
        for name in requested:
            if security_failed and name in MANDATORY_PERFORMANCE_PROBES:
                probes.append(ProbeResult(name, False, 0, {
                    "skipped": True, "reason": "a mandatory security probe failed"}))
                continue
            probe = PROBE_METHODS[name](client, run_id)
            probes.append(probe)
            if name in MANDATORY_SECURITY_PROBES and not probe.passed:
                security_failed = True
        base["probes"] = [asdict(probe) for probe in probes]
    except DockerQualificationError as exc:
        base["fatal_error"] = {"class": type(exc).__name__, "message": str(exc)}
    finally:
        try:
            after = client.census()
            base["census_after"] = after.report()
            base["cleanup_verified"] = cleanup_matches(before, after, recovered)
        except DockerQualificationError as exc:
            base["cleanup_error"] = {"class": type(exc).__name__, "message": str(exc)}
    probes_by_name = {row["name"]: row for row in base["probes"]}
    requested_complete = probe_name == "all" and set(probes_by_name) == set(MANDATORY_PROBES)
    complete_passed = (requested_complete and all(
        probes_by_name[name]["passed"] for name in MANDATORY_PROBES)
        and identity.untrusted_boundary_established)
    selected_passed = (
        probe_name == "preflight" or
        (bool(probes_by_name) and all(row["passed"] for row in probes_by_name.values())))
    contract = probes_by_name.get("empirica_contract", {}).get("passed") is True
    passed = (identity.available and selected_passed and base["cleanup_verified"]
              and "fatal_error" not in base and "cleanup_error" not in base)
    full_eligible = (complete_passed and base["cleanup_verified"]
                     and "fatal_error" not in base and "cleanup_error" not in base)
    base.update(
        passed=passed,
        selected_probe_passed=passed,
        complete_qualification=full_eligible,
        docker_backend_eligible=full_eligible,
        empirica_contract_compatible=contract,
        bounded_repository_diagnostic_eligible=full_eligible,
        qualified_for_untrusted_code=full_eligible,
    )
    return base
