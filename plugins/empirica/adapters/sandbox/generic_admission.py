"""Bounded semantic admission for the single-container Docker MVP.

Only security-relevant projected fields are interpreted. Unknown non-authoritative fields and
benign missing/null/default representations are ignored or normalized; authority and limits remain
exact.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core import spike_plan
from core.freshness import is_digest256
from . import planner

MAX_CONTROL_BYTES = 256 * 1024
_FULL_ID = re.compile(r"^[0-9a-f]{64}$")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class DockerObservationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RawStreamObservation:
    data: bytes
    bytes_seen: int
    sha256: str


@dataclass(frozen=True, slots=True)
class RawProcessObservation:
    argv: tuple[str, ...]
    process_group_owned: bool
    returncode: int | None
    timed_out: bool
    output_limited: bool
    process_ambiguous: bool
    stdout: RawStreamObservation
    stderr: RawStreamObservation


@dataclass(frozen=True, slots=True)
class RawAttachObservation:
    process: RawProcessObservation
    natural_exit: bool
    forced_exit: bool
    exit_code: int | None


@dataclass(frozen=True, slots=True)
class StaticContainerObservation:
    full_id: str
    realization_digest: str
    network_mode: str


@dataclass(frozen=True, slots=True)
class TerminalContainerObservation:
    status: str
    running: bool
    exit_code: int
    oom_killed: bool
    error: str
    networks: tuple[tuple[str, str, str], ...]

    @property
    def natural_verdict(self):
        if self.status != "exited" or self.running or self.oom_killed or self.error:
            return None
        return spike_plan.natural_verdict(self.exit_code)


@dataclass(frozen=True, slots=True)
class NetworkMembershipObservation:
    full_id: str
    attached_container_ids: tuple[str, ...]


ABSENCE_STDOUT = frozenset({b"", b"[]", b"[]\n"})


def admit_created_full_id(value: RawProcessObservation, argv: tuple[str, ...]) -> str:
    raw = admit_process(value, argv, stdout_ceiling=MAX_CONTROL_BYTES,
                        stderr_ceiling=MAX_CONTROL_BYTES)
    try:
        text = raw.stdout.data.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise DockerObservationError("created resource ID is not ASCII") from exc
    return full_id(text, "created resource ID")


def admit_exact_name_candidates(value: RawProcessObservation, argv: tuple[str, ...]) -> tuple[str, ...]:
    raw = admit_process(value, argv, stdout_ceiling=MAX_CONTROL_BYTES,
                        stderr_ceiling=MAX_CONTROL_BYTES)
    try:
        lines = tuple(item for item in raw.stdout.data.decode("ascii").splitlines() if item)
    except UnicodeDecodeError as exc:
        raise DockerObservationError("candidate IDs are not ASCII") from exc
    if len(lines) > 2:
        raise DockerObservationError("candidate discovery is unbounded or ambiguous")
    return tuple(full_id(item, "candidate full ID") for item in lines)


def admit_exact_absence(value: RawProcessObservation, argv: tuple[str, ...], resource: str) -> None:
    if resource not in {"container", "network"} or argv[:2] != (resource, "inspect") \
            or len(argv) != 3:
        raise DockerObservationError("absence command authority is malformed")
    recorded_id = full_id(argv[2], "absence command full ID")
    raw = admit_process(value, argv, stdout_ceiling=MAX_CONTROL_BYTES,
                        stderr_ceiling=MAX_CONTROL_BYTES, require_success=False)
    # docker/cli `inspect.Inspect` flushes an empty JSON list (`[]\n`) before exiting 1.
    if raw.returncode == 0 or raw.stdout.data not in ABSENCE_STDOUT:
        raise DockerObservationError("recorded resource remains present")
    text = raw.stderr.data.decode("utf-8", errors="replace").strip()
    escaped = re.escape(recorded_id)
    prefix = r"(?:(?:Error: )|(?:Error response from daemon: ))?"
    forms = (
        rf"{prefix}No such {resource}: {escaped}",
        rf"{prefix}{resource} {escaped} not found",
    )
    if not any(re.fullmatch(pattern, text, flags=re.IGNORECASE) for pattern in forms):
        raise DockerObservationError("absence command failed without exact-ID absence proof")


def admit_owned_container(
    document: object,
    *, container_full_id: str,
    name: str,
    labels: tuple[tuple[str, str], ...],
) -> None:
    """Ownership only: exact `Id`, exact compiled `Name`, and every Empirica ownership label.

    This predicate grants nothing by itself; it decides whether a cidfile or exact-name candidate
    may be durably recorded. Security policy is a separate gate whose refusal removes the
    recorded ID instead of stranding it.
    """
    row = _mapping(document, "owned container inspect")
    if full_id(row.get("Id"), "owned container Id") != full_id(container_full_id):
        raise DockerObservationError("owned container Id differs")
    if row.get("Name") not in ("/" + name, name):
        raise DockerObservationError("owned container name differs")
    config = _mapping(row.get("Config"), "owned container Config")
    observed = _labels(config.get("Labels"))
    if not labels or any(observed.get(key) != value for key, value in labels):
        raise DockerObservationError("owned container labels differ")


def admit_owned_network(
    operation: object,
    document: object,
    *, network_full_id: str,
    container_full_id: str | None,
) -> NetworkMembershipObservation:
    """An exact owned bridge, attached to nothing or only to `container_full_id`.

    `operation` is compiled or journal-persisted data: only `operation.network.labels` is read,
    so a journal written by an older planner is judged by the labels it persisted.
    """
    if getattr(operation, "network", None) is None:
        raise DockerObservationError("isolated operation has no owned network")
    row = _mapping(document, "network inspect")
    full = full_id(network_full_id, "network Id")
    if (full_id(row.get("Id"), "network inspect Id") != full
            or row.get("Driver") != "bridge" or row.get("Internal") is not False):
        raise DockerObservationError("owned bridge identity differs")
    labels = _labels(row.get("Labels"))
    if any(labels.get(key) != value for key, value in operation.network.labels):
        raise DockerObservationError("network ownership labels differ")
    containers = row.get("Containers")
    if containers is None:
        containers = {}
    if not isinstance(containers, Mapping):
        raise DockerObservationError("network membership is malformed")
    observed = tuple(sorted(containers))
    for item in observed:
        full_id(item, "attached container Id")
    permitted = () if container_full_id is None else (full_id(container_full_id),)
    if observed not in ((), permitted):
        raise DockerObservationError("network has foreign attachments")
    return NetworkMembershipObservation(full, observed)


def stream(data: bytes) -> RawStreamObservation:
    if type(data) is not bytes:
        raise DockerObservationError("stream data must be bytes")
    return RawStreamObservation(data, len(data), "sha256:" + hashlib.sha256(data).hexdigest())


def _stream(value: object, ceiling: int, path: str) -> bytes:
    if type(value) is not RawStreamObservation or type(value.data) is not bytes:
        raise DockerObservationError(path + " is malformed")
    if (type(value.bytes_seen) is not int or isinstance(value.bytes_seen, bool)
            or value.bytes_seen != len(value.data) or value.bytes_seen > ceiling
            or value.sha256 != "sha256:" + hashlib.sha256(value.data).hexdigest()):
        raise DockerObservationError(path + " exceeds or differs from its bounded capture")
    return value.data


def admit_process(value: object, argv: tuple[str, ...], *, stdout_ceiling: int,
                  stderr_ceiling: int, require_success: bool = True) -> RawProcessObservation:
    if type(value) is not RawProcessObservation or value.argv != argv:
        raise DockerObservationError("process argv ownership differs")
    if (value.process_group_owned is not True or value.timed_out is not False
            or value.output_limited is not False or value.process_ambiguous is not False
            or type(value.returncode) is not int or isinstance(value.returncode, bool)):
        raise DockerObservationError("bounded process completion is unavailable")
    if require_success and value.returncode != 0:
        raise DockerObservationError("Docker control command failed")
    _stream(value.stdout, stdout_ceiling, "stdout")
    _stream(value.stderr, stderr_ceiling, "stderr")
    return value


def _pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in rows:
        if key in value:
            raise DockerObservationError("Docker JSON contains a duplicate key")
        value[key] = item
    return value


def json_object(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or len(raw) > MAX_CONTROL_BYTES:
        raise DockerObservationError("Docker JSON exceeds its bound")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda item: (_ for _ in ()).throw(
                               DockerObservationError("non-finite Docker JSON")))
    except DockerObservationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DockerObservationError("Docker output is not one JSON object") from exc
    if type(value) is not dict:
        raise DockerObservationError("Docker output is not one JSON object")
    return value


def full_id(value: object, path: str = "Docker full ID") -> str:
    if type(value) is not str or _FULL_ID.fullmatch(value) is None:
        raise DockerObservationError(path + " must be 64 lowercase hex")
    return value


def digest(value: object, path: str = "Docker digest") -> str:
    if not is_digest256(value):
        raise DockerObservationError(path + " must be lowercase sha256")
    return value


def decode_image_document(document: object) -> tuple[str, str, str, tuple[str, ...]]:
    """Decode only Id/Os/Architecture/Config.Volumes; optional volumes normalize empty."""
    if type(document) is not dict:
        raise DockerObservationError("image inspect is not an object")
    image_id = digest(document.get("Id"), "image Id")
    raw_os = document.get("Os")
    raw_arch = document.get("Architecture")
    if (type(raw_os) is not str or not raw_os or type(raw_arch) is not str or not raw_arch):
        raise DockerObservationError("image platform is malformed")
    volumes = document.get("Volumes")
    if volumes is None and isinstance(document.get("Config"), Mapping):
        volumes = document["Config"].get("Volumes")
    if volumes in (None, {}):
        declared: tuple[str, ...] = ()
    elif type(volumes) is dict and all(type(key) is str and key.startswith("/")
                                      and item in ({}, None) for key, item in volumes.items()):
        declared = tuple(sorted(volumes))
    else:
        raise DockerObservationError("image declared volumes are malformed")
    return image_id, raw_os, raw_arch, declared


def _empty(value: object) -> bool:
    return value in (None, [], {})


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DockerObservationError(path + " is malformed")
    return value


def _environment(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if type(value) is not list or len(value) > 512:
        raise DockerObservationError("container environment is malformed")
    result: dict[str, str] = {}
    total = 0
    for row in value:
        if type(row) is not str or "=" not in row or "\x00" in row:
            raise DockerObservationError("container environment row is malformed")
        key, item = row.split("=", 1)
        total += len(row.encode("utf-8"))
        if _ENV.fullmatch(key) is None or key in result or total > 256 * 1024:
            raise DockerObservationError("container environment is ambiguous or unbounded")
        result[key] = item
    return result


def _labels(value: object) -> Mapping[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > 256 \
            or any(type(key) is not str or type(item) is not str
                   for key, item in value.items()):
        raise DockerObservationError("container labels are malformed")
    return value


def _string_set(value: object, path: str) -> set[str]:
    if value is None:
        return set()
    if type(value) is not list or any(type(item) is not str for item in value):
        raise DockerObservationError(path + " is malformed")
    return set(value)


def _ulimits(value: object) -> dict[str, tuple[int, int]]:
    if type(value) is not list:
        raise DockerObservationError("container ulimits are malformed")
    rows: dict[str, tuple[int, int]] = {}
    for item in value:
        if not isinstance(item, Mapping) or not {"Name", "Soft", "Hard"}.issubset(item):
            raise DockerObservationError("container ulimit row is malformed")
        name, soft, hard = item["Name"], item["Soft"], item["Hard"]
        if (type(name) is not str or name in rows or type(soft) is not int
                or isinstance(soft, bool) or type(hard) is not int or isinstance(hard, bool)):
            raise DockerObservationError("container ulimit row is ambiguous")
        rows[name] = (soft, hard)
    return rows


def _tmpfs_options(value: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise DockerObservationError("container tmpfs map is malformed")
    result = {}
    for target, raw in value.items():
        if type(target) is not str or type(raw) is not str:
            raise DockerObservationError("container tmpfs row is malformed")
        options = tuple(sorted(raw.split(",")))
        if any(not item for item in options) or len(set(options)) != len(options):
            raise DockerObservationError("container tmpfs options are ambiguous")
        result[target] = options
    return result


def _expected_tmpfs(operation: planner.ResolvedDockerOperation) -> dict[str, set[str]]:
    policy = operation.policy
    return {
        spike_plan.FIXED_WORKDIR: {"rw", "nodev", "nosuid", "exec",
                                  f"size={policy.work_tmpfs_bytes}", "mode=1777"},
        spike_plan.FIXED_TMPDIR: {"rw", "nodev", "nosuid", "noexec",
                                f"size={policy.tmp_tmpfs_bytes}", "mode=1777"},
    }


def admit_static_container(
    operation: planner.ResolvedDockerOperation,
    document: object,
    *, container_full_id: str,
    network_full_id: str | None,
    staging_path: Path,
) -> StaticContainerObservation:
    if type(operation) is not planner.ResolvedDockerOperation:
        raise DockerObservationError("operation type is not exact")
    row = _mapping(document, "container inspect")
    if full_id(row.get("Id"), "container Id") != full_id(container_full_id):
        raise DockerObservationError("container Id differs")
    realization = digest(row.get("Image"), "container realization")
    config = _mapping(row.get("Config"), "container Config")
    host = _mapping(row.get("HostConfig"), "container HostConfig")
    plan, policy = operation.plan, operation.policy
    entrypoint = config.get("Entrypoint")
    cmd = config.get("Cmd")
    if entrypoint == plan.argv[0]:
        entrypoint = [entrypoint]
    if cmd is None:
        cmd = []
    if (config.get("Image") != plan.image.image_id or entrypoint != [plan.argv[0]]
            or cmd != list(plan.argv[1:]) or config.get("WorkingDir") != spike_plan.FIXED_WORKDIR
            or config.get("User") != f"{spike_plan.FIXED_UID}:{spike_plan.FIXED_GID}"):
        raise DockerObservationError("container image or process differs")
    expected_environment = dict(operation.container.environment)
    environment = _environment(config.get("Env"))
    if any(environment.get(key) != value for key, value in expected_environment.items()):
        raise DockerObservationError("framework or authored environment differs")
    labels = _labels(config.get("Labels"))
    if any(labels.get(key) != value for key, value in operation.container.labels):
        raise DockerObservationError("container ownership labels differ")
    # `Config.ExposedPorts` is image `EXPOSE` metadata merged by Docker; it publishes nothing.
    # Publication is `HostConfig.PortBindings`/`PublishAllPorts`, which stay refused below.
    if not _empty(config.get("Volumes")):
        raise DockerObservationError("container image volumes appeared")
    for field in ("PortBindings", "Devices", "DeviceRequests", "DeviceCgroupRules",
                  "VolumesFrom", "CapAdd"):
        if not _empty(host.get(field)):
            raise DockerObservationError("unapproved host authority appeared")
    if (host.get("Privileged", False) is not False
            or host.get("PublishAllPorts", False) is not False
            or host.get("ReadonlyRootfs") is not True):
        raise DockerObservationError("container privilege, publication, or rootfs differs")
    if host.get("IpcMode", "") not in ("", "private") \
            or host.get("PidMode", "") not in ("", "private") \
            or host.get("UTSMode", "") != "":
        raise DockerObservationError("container host namespace appeared")
    if (host.get("CgroupnsMode", "private") not in ("", "private")
            or host.get("UsernsMode", "") not in ("", "private")):
        raise DockerObservationError("container host namespace appeared")
    security = {item.split(":", 1)[0] for item in _string_set(host.get("SecurityOpt"), "security options")}
    if "no-new-privileges" not in security:
        raise DockerObservationError("no-new-privileges is absent")
    if {item.upper() for item in _string_set(host.get("CapDrop"), "cap drop")} != {"ALL"}:
        raise DockerObservationError("cap-drop ALL differs")
    expected_resources = {
        "Memory": policy.memory_bytes, "MemorySwap": policy.memory_swap_bytes,
        "NanoCpus": plan.cpu_count * 1_000_000_000, "PidsLimit": policy.pids,
        "ShmSize": policy.shm_bytes,
    }
    if any(host.get(key) != value for key, value in expected_resources.items()):
        raise DockerObservationError("container resource limit differs")
    expected_ulimits = {
        "nofile": (policy.open_files, policy.open_files),
        "fsize": (policy.max_file_size_bytes, policy.max_file_size_bytes),
        "core": (policy.core_bytes, policy.core_bytes),
    }
    if _ulimits(host.get("Ulimits")) != expected_ulimits:
        raise DockerObservationError("container ulimits differ")
    actual_tmpfs = {key: set(value) for key, value in _tmpfs_options(host.get("Tmpfs")).items()}
    if actual_tmpfs != _expected_tmpfs(operation):
        raise DockerObservationError("container tmpfs differs")
    mounts = row.get("Mounts")
    if type(mounts) is not list or len(mounts) != 1 or not isinstance(mounts[0], Mapping):
        raise DockerObservationError("container input mount membership differs")
    mount = mounts[0]
    if (mount.get("Type") != "bind" or mount.get("Source") != str(staging_path)
            or mount.get("Destination") != spike_plan.FIXED_INPUTS
            or mount.get("RW") is not False
            or mount.get("Propagation", "rprivate") not in ("", "rprivate")
            or mount.get("Mode", "") not in ("", "ro")):
        raise DockerObservationError("container input bind differs")
    mode = host.get("NetworkMode")
    if operation.backend.profile_id == spike_plan.ISOLATED_PROFILE:
        if mode != "none" or network_full_id is not None:
            raise DockerObservationError("isolated network mode differs")
        normalized_mode = "none"
    else:
        expected_id = full_id(network_full_id, "network Id")
        allowed = {expected_id}
        if operation.network is not None:
            allowed.add(operation.network.name)
        if mode not in allowed:
            raise DockerObservationError("networked mode differs")
        normalized_mode = expected_id
    return StaticContainerObservation(container_full_id, realization, normalized_mode)


def _bounded_generated(value: object, path: str, *, maximum: int = 4096) -> str:
    if type(value) is not str or "\x00" in value or len(value.encode("utf-8")) > maximum:
        raise DockerObservationError(path + " is malformed")
    return value


def _networks(value: object) -> tuple[tuple[str, str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping) or len(value) > 4:
        raise DockerObservationError("container network shapes are unbounded")
    rows = []
    for name, endpoint in value.items():
        if type(name) is not str or not isinstance(endpoint, Mapping):
            raise DockerObservationError("container network endpoint is malformed")
        network_id = _bounded_generated(endpoint.get("NetworkID", ""), "network ID", maximum=64)
        endpoint_id = _bounded_generated(endpoint.get("EndpointID", ""), "endpoint ID", maximum=64)
        if network_id and _FULL_ID.fullmatch(network_id) is None:
            raise DockerObservationError("generated network ID is malformed")
        if endpoint_id and _FULL_ID.fullmatch(endpoint_id) is None:
            raise DockerObservationError("generated endpoint ID is malformed")
        rows.append((name, network_id, endpoint_id))
    return tuple(sorted(rows))


def decode_terminal_container(document: object) -> TerminalContainerObservation:
    row = _mapping(document, "terminal container inspect")
    state = _mapping(row.get("State"), "container State")
    status = state.get("Status")
    running = state.get("Running")
    exit_code = state.get("ExitCode")
    oom = state.get("OOMKilled", False)
    error = state.get("Error", "")
    if (status not in {"created", "running", "exited", "dead"}
            or type(running) is not bool or type(exit_code) is not int
            or isinstance(exit_code, bool) or not -(1 << 31) <= exit_code < (1 << 31)
            or type(oom) is not bool):
        raise DockerObservationError("terminal state is malformed")
    error = _bounded_generated(error, "container state error", maximum=16 * 1024)
    networks = _networks(row.get("Networks"))
    return TerminalContainerObservation(status, running, exit_code, oom, error, networks)


def admit_terminal_container(
    operation: planner.ResolvedDockerOperation,
    document: object,
    *,
    container_full_id: str,
    network_full_id: str | None,
) -> TerminalContainerObservation:
    row = _mapping(document, "terminal container inspect")
    if full_id(row.get("Id"), "terminal container Id") != full_id(container_full_id):
        raise DockerObservationError("terminal container Id differs")
    terminal = decode_terminal_container(row)
    if operation.backend.profile_id == spike_plan.ISOLATED_PROFILE:
        if network_full_id is not None or any(name != "none" for name, _, _ in terminal.networks):
            raise DockerObservationError("terminal isolated network membership differs")
    else:
        if operation.network is None:
            raise DockerObservationError("terminal network authority is absent")
        expected = full_id(network_full_id, "terminal network Id")
        if (len(terminal.networks) != 1
                or terminal.networks[0][0] != operation.network.name
                or terminal.networks[0][1] != expected):
            raise DockerObservationError("terminal private bridge membership differs")
    return terminal


def admit_natural_verdict(
    terminal: TerminalContainerObservation, attach: RawAttachObservation,
):
    if type(terminal) is not TerminalContainerObservation or type(attach) is not RawAttachObservation:
        raise DockerObservationError("natural verdict inputs are malformed")
    if (attach.natural_exit is not True or attach.forced_exit is not False
            or terminal.status != "exited" or terminal.running
            or attach.exit_code != terminal.exit_code or terminal.oom_killed
            or terminal.error or terminal.exit_code == 137):
        raise DockerObservationError("natural verdict is unavailable")
    verdict = spike_plan.natural_verdict(terminal.exit_code)
    if verdict is None:
        raise DockerObservationError("natural verdict is unavailable")
    return verdict


def admit_prestart_network(
    operation: planner.ResolvedDockerOperation,
    document: object,
    *, network_full_id: str,
) -> NetworkMembershipObservation:
    """Admit the exact owned bridge before start; real Docker has no live endpoints yet."""
    observed = admit_owned_network(
        operation, document, network_full_id=network_full_id, container_full_id=None)
    if observed.attached_container_ids:
        raise DockerObservationError("prestart private bridge has a foreign attachment")
    return observed


def admit_network_membership(
    operation: planner.ResolvedDockerOperation,
    document: object,
    *, network_full_id: str,
    container_full_id: str,
) -> NetworkMembershipObservation:
    """Qualification only: the exact owned bridge has exactly the verdict container attached."""
    if operation.backend.profile_id != spike_plan.NETWORKED_PROFILE:
        raise DockerObservationError("operation has no private network membership invariant")
    observed = admit_owned_network(operation, document, network_full_id=network_full_id,
                                   container_full_id=container_full_id)
    if observed.attached_container_ids != (full_id(container_full_id),):
        raise DockerObservationError("private bridge membership differs")
    return observed
