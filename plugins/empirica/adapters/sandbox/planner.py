"""Pure single-container compiler for the Empirica 6 Docker MVP.

No subprocess, Docker, filesystem, clock, random, or environment access is permitted here. Names,
labels, paths, flags, resources, and network topology are framework-derived from sealed inputs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from application.generic_spike import GenericQualifiedBackend
from core.canonical import canonical_digest
from core.spike_plan import (
    BACKEND_OPERATION_TIMEOUT_SECONDS, FIXED_GID, FIXED_INPUTS, FIXED_TMPDIR, FIXED_UID,
    FIXED_WORKDIR, FRAMEWORK_SPIKE_POLICY, SealedSpikeRequest, SpikePlanError,
    WORKLOAD_TIMEOUT_SECONDS,
)

OPERATION_SCHEMA = "empirica.docker-operation-plan/2"
NETWORK_PROFILE_IDS = ("docker-isolated-v1", "docker-networked-v1")
IMAGE_INSPECT_FORMAT = (
    '{"Id":{{json .Id}},"Os":{{json .Os}},"Architecture":{{json .Architecture}},'
    '"Volumes":{{json (index .Config "Volumes")}}}'
)
CONTAINER_INSPECT_FORMAT = (
    '{"Id":{{json .Id}},"Name":{{json .Name}},"Image":{{json .Image}},"Config":{'
    '"Image":{{json (index .Config "Image")}},'
    '"Entrypoint":{{json (index .Config "Entrypoint")}},'
    '"Cmd":{{json (index .Config "Cmd")}},'
    '"Env":{{json (index .Config "Env")}},'
    '"WorkingDir":{{json (index .Config "WorkingDir")}},'
    '"User":{{json (index .Config "User")}},'
    '"Labels":{{json (index .Config "Labels")}},'
    '"Volumes":{{json (index .Config "Volumes")}}},"HostConfig":{'
    '"NetworkMode":{{json (index .HostConfig "NetworkMode")}},'
    '"IpcMode":{{json (index .HostConfig "IpcMode")}},'
    '"PidMode":{{json (index .HostConfig "PidMode")}},'
    '"UTSMode":{{json (index .HostConfig "UTSMode")}},'
    '"CgroupnsMode":{{json (index .HostConfig "CgroupnsMode")}},'
    '"UsernsMode":{{json (index .HostConfig "UsernsMode")}},'
    '"Privileged":{{json (index .HostConfig "Privileged")}},'
    '"PortBindings":{{json (index .HostConfig "PortBindings")}},'
    '"PublishAllPorts":{{json (index .HostConfig "PublishAllPorts")}},'
    '"Devices":{{json (index .HostConfig "Devices")}},'
    '"DeviceRequests":{{json (index .HostConfig "DeviceRequests")}},'
    '"DeviceCgroupRules":{{json (index .HostConfig "DeviceCgroupRules")}},'
    '"VolumesFrom":{{json (index .HostConfig "VolumesFrom")}},'
    '"ReadonlyRootfs":{{json (index .HostConfig "ReadonlyRootfs")}},'
    '"SecurityOpt":{{json (index .HostConfig "SecurityOpt")}},'
    '"CapAdd":{{json (index .HostConfig "CapAdd")}},'
    '"CapDrop":{{json (index .HostConfig "CapDrop")}},'
    '"Memory":{{json (index .HostConfig "Memory")}},'
    '"MemorySwap":{{json (index .HostConfig "MemorySwap")}},'
    '"NanoCpus":{{json (index .HostConfig "NanoCpus")}},'
    '"PidsLimit":{{json (index .HostConfig "PidsLimit")}},'
    '"Ulimits":{{json (index .HostConfig "Ulimits")}},'
    '"ShmSize":{{json (index .HostConfig "ShmSize")}},'
    '"Tmpfs":{{json (index .HostConfig "Tmpfs")}}},'
    '"State":{"Status":{{json (index .State "Status")}},'
    '"Running":{{json (index .State "Running")}},'
    '"ExitCode":{{json (index .State "ExitCode")}},'
    '"OOMKilled":{{json (index .State "OOMKilled")}},'
    '"Error":{{json (index .State "Error")}}},'
    '"Mounts":{{json (index . "Mounts")}},'
    '"Networks":{{json (index .NetworkSettings "Networks")}}}'
)
NETWORK_INSPECT_FORMAT = (
    '{"Id":{{json .Id}},"Driver":{{json .Driver}},"Internal":{{json .Internal}},'
    '"Labels":{{json (index . "Labels")}},'
    '"Containers":{{json (index . "Containers")}}}'
)

_OPERATION = re.compile(r"^o-[0-9a-f]{32}$")
_RUN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TOKEN = re.compile(r"^stg-[0-9a-f]{32}$")

CONTAINER_CREATE_VALUE_FLAGS = frozenset({
    "--name", "--cidfile", "--platform", "--pull", "--network", "--ipc", "--cgroupns",
    "--cap-drop", "--security-opt", "--user", "--workdir", "--cpus", "--memory",
    "--memory-swap", "--pids-limit", "--ulimit", "--shm-size", "--tmpfs", "--mount",
    "--entrypoint", "--label", "--env",
})
CONTAINER_CREATE_BOOLEAN_FLAGS = frozenset({"--read-only"})
CONTAINER_CREATE_FLAGS = CONTAINER_CREATE_VALUE_FLAGS | CONTAINER_CREATE_BOOLEAN_FLAGS


def _admit_compiled_create_flags(argv: tuple[str, ...], image_id: str) -> None:
    """Assert the emitted pre-image option grammar; Docker-default PID/UTS are never flags.

    Only the pre-image region is Docker option syntax: `--pid` and `--uts` are not in the closed
    allowlist, so the loop refuses them there, while guest argv after the image is never scanned.
    """
    if argv[:2] != ("container", "create"):
        raise DockerPlanCompileError("container create command prefix differs")
    index = 2
    while index < len(argv) and argv[index] != image_id:
        flag = argv[index]
        if flag in CONTAINER_CREATE_BOOLEAN_FLAGS:
            index += 1
        elif flag in CONTAINER_CREATE_VALUE_FLAGS and index + 1 < len(argv):
            index += 2
        else:
            raise DockerPlanCompileError("container create contains an unsupported option")
    if index >= len(argv):
        raise DockerPlanCompileError("container create image boundary is absent")


class DockerPlanCompileError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DockerCommand:
    kind: str
    argv: tuple[str, ...]

    def to_data(self) -> dict[str, Any]:
        return {"kind": self.kind, "argv": list(self.argv)}


@dataclass(frozen=True, slots=True)
class ResolvedNetwork:
    name: str
    full_id_token: str
    labels: tuple[tuple[str, str], ...]

    def to_data(self) -> dict[str, Any]:
        return {"name": self.name, "full_id_token": self.full_id_token,
                "labels": dict(self.labels), "driver": "bridge", "internal": False}


@dataclass(frozen=True, slots=True)
class ResolvedContainer:
    name: str
    full_id_token: str
    cidfile_token: str
    environment: tuple[tuple[str, str], ...]
    labels: tuple[tuple[str, str], ...]
    network_mode_token: str

    def to_data(self) -> dict[str, Any]:
        return {
            "name": self.name, "full_id_token": self.full_id_token,
            "cidfile_token": self.cidfile_token, "environment": dict(self.environment),
            "labels": dict(self.labels), "network_mode_token": self.network_mode_token,
        }


@dataclass(frozen=True, slots=True)
class ResolvedDockerOperation:
    operation_id: str
    run_id: str
    request: SealedSpikeRequest
    backend: GenericQualifiedBackend
    staging_token: str
    container: ResolvedContainer
    network: ResolvedNetwork | None
    commands: tuple[DockerCommand, ...]

    def __post_init__(self) -> None:
        if (type(self.request) is not SealedSpikeRequest
                or type(self.backend) is not GenericQualifiedBackend
                or self.request.plan.network_profile != self.backend.profile_id):
            raise DockerPlanCompileError("operation request and backend differ")
        if self.request.operation_timeout_seconds != BACKEND_OPERATION_TIMEOUT_SECONDS:
            raise DockerPlanCompileError("operation timeout differs")

    @property
    def plan(self):
        return self.request.plan

    @property
    def policy(self):
        return self.request.policy

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_data())

    def command(self, kind: str) -> DockerCommand:
        rows = [item for item in self.commands if item.kind == kind]
        if len(rows) != 1:
            raise DockerPlanCompileError(f"operation has no unique {kind} command")
        return rows[0]

    def to_data(self) -> dict[str, Any]:
        return {
            "schema": OPERATION_SCHEMA, "operation_id": self.operation_id,
            "run_id": self.run_id, "request_digest": self.request.digest,
            "plan_digest": self.plan.digest, "policy_digest": self.policy.digest,
            "backend": self.backend.to_data(), "staging_token": self.staging_token,
            "container": self.container.to_data(),
            "network": None if self.network is None else self.network.to_data(),
            "commands": [item.to_data() for item in self.commands],
            "timing": {"workload_seconds": WORKLOAD_TIMEOUT_SECONDS,
                       "operation_seconds": BACKEND_OPERATION_TIMEOUT_SECONDS},
            "destructive_authority": "durably-recorded-full-ids-only",
        }


def _resource_name(operation_id: str, kind: str) -> str:
    suffix = canonical_digest({"operation": operation_id, "kind": kind})[7:23]
    return f"emp-{operation_id[2:18]}-{kind[0]}-{suffix}"


def _full_id_token(operation_id: str, kind: str) -> str:
    return f"{kind}-full-id:" + canonical_digest({"operation": operation_id, "kind": kind})[7:39]


def _labels(request: SealedSpikeRequest, backend: GenericQualifiedBackend,
            run_id: str, operation_id: str, resource: str) -> tuple[tuple[str, str], ...]:
    values = {
        "io.siffran.empirica.backend": backend.execution_binding_digest,
        "io.siffran.empirica.operation": operation_id,
        "io.siffran.empirica.plan": request.plan.digest,
        "io.siffran.empirica.policy": request.policy.digest,
        "io.siffran.empirica.qualification": backend.qualification_record_digest,
        "io.siffran.empirica.request": request.digest,
        "io.siffran.empirica.resource": resource,
        "io.siffran.empirica.run": run_id,
    }
    return tuple(sorted(values.items()))


def _label_argv(labels: tuple[tuple[str, str], ...]) -> list[str]:
    return [part for key, value in labels for part in ("--label", f"{key}={value}")]


def compile_docker_operation(
    request: SealedSpikeRequest,
    backend: GenericQualifiedBackend,
    run_id: str,
    operation_id: str,
    staging_token: str,
) -> ResolvedDockerOperation:
    """Compile exactly one sealed verdict container and optional private bridge."""
    if type(request) is not SealedSpikeRequest or type(backend) is not GenericQualifiedBackend:
        raise DockerPlanCompileError("compiler requires exact sealed request and backend")
    if _RUN.fullmatch(run_id) is None or _OPERATION.fullmatch(operation_id) is None:
        raise DockerPlanCompileError("run or operation identity is malformed")
    if _TOKEN.fullmatch(staging_token) is None:
        raise DockerPlanCompileError("staging token is malformed")
    plan = request.plan
    policy = request.policy
    if (plan.network_profile != backend.profile_id
            or policy != FRAMEWORK_SPIKE_POLICY
            or plan.image.os != "linux"):
        raise DockerPlanCompileError("sealed request differs from selected Docker authority")

    container_name = _resource_name(operation_id, "container")
    container_token = _full_id_token(operation_id, "container")
    cidfile_token = "cidfile:" + canonical_digest({"operation": operation_id})[7:39]
    container_labels = _labels(request, backend, run_id, operation_id, "container")
    network = None
    network_mode = "none"
    commands: list[DockerCommand] = []
    if backend.profile_id == "docker-networked-v1":
        network_name = _resource_name(operation_id, "network")
        network_token = _full_id_token(operation_id, "network")
        network_labels = _labels(request, backend, run_id, operation_id, "network")
        network = ResolvedNetwork(network_name, network_token, network_labels)
        commands.append(DockerCommand("network_create", tuple(
            ["network", "create", "--driver", "bridge", *_label_argv(network_labels),
             network_name])))
        network_mode = network_token

    environment = tuple(sorted({"HOME": FIXED_WORKDIR, "TMPDIR": FIXED_TMPDIR,
                                **dict(plan.environment)}.items()))
    container = ResolvedContainer(
        container_name, container_token, cidfile_token, environment,
        container_labels, network_mode)
    create: list[str] = [
        "container", "create", "--name", container_name, "--cidfile", cidfile_token,
        "--platform", f"{plan.image.os}/{plan.image.architecture}", "--pull", "never",
        "--network", network_mode, "--ipc", "private", "--cgroupns", "private",
        "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", f"{FIXED_UID}:{FIXED_GID}", "--workdir", FIXED_WORKDIR,
        "--cpus", str(plan.cpu_count), "--memory", f"{policy.memory_bytes}b",
        "--memory-swap", f"{policy.memory_swap_bytes}b",
        "--pids-limit", str(policy.pids),
        "--ulimit", f"nofile={policy.open_files}:{policy.open_files}",
        "--ulimit", f"fsize={policy.max_file_size_bytes}:{policy.max_file_size_bytes}",
        "--ulimit", f"core={policy.core_bytes}:{policy.core_bytes}",
        "--shm-size", f"{policy.shm_bytes}b",
        "--tmpfs", f"{FIXED_WORKDIR}:rw,nodev,nosuid,exec,size={policy.work_tmpfs_bytes},mode=1777",
        "--tmpfs", f"{FIXED_TMPDIR}:rw,nodev,nosuid,noexec,size={policy.tmp_tmpfs_bytes},mode=1777",
        "--mount", f"type=bind,source=staging-token:{staging_token},destination={FIXED_INPUTS},readonly,bind-propagation=rprivate",
        "--entrypoint", plan.argv[0], *_label_argv(container_labels),
    ]
    for key, value in environment:
        create.extend(("--env", f"{key}={value}"))
    create.append(plan.image.image_id)
    create.extend(plan.argv[1:])
    create_tuple = tuple(create)
    _admit_compiled_create_flags(create_tuple, plan.image.image_id)
    # The static inspect targets the cidfile/exact-name candidate before its ID is recorded, so it
    # is issued by the lifecycle with that candidate rather than compiled against the ID token.
    commands.append(DockerCommand("container_create", create_tuple))
    if network is not None:
        commands.append(DockerCommand("network_prestart_inspect", (
            "network", "inspect", "--format", NETWORK_INSPECT_FORMAT,
            network.full_id_token)))
    commands.append(DockerCommand("container_start_attach", (
        "container", "start", "--attach", container_token)))
    # Removal and exact absence are built by the lifecycle from durably recorded full IDs only.
    commands.append(DockerCommand("container_terminal_inspect", (
        "container", "inspect", "--format", CONTAINER_INSPECT_FORMAT, container_token)))
    try:
        return ResolvedDockerOperation(
            operation_id, run_id, request, backend, staging_token, container, network,
            tuple(commands))
    except (SpikePlanError, ValueError, TypeError) as exc:
        raise DockerPlanCompileError("sealed Docker operation is invalid") from exc
