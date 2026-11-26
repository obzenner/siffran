"""Passive capability authority and bounded local-image resolution for Docker MVP `/2`.

Installed records are read-only during ordinary runtime; absent, legacy, malformed, or drifted
records freeze spikes unavailable. Qualification and installation are explicit separate commands.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from application import generic_spike as application
from core import spike_plan
from core.freshness import is_digest256

from . import common, generic_admission, planner, process

RECORD_SCHEMA = "siffran.empirica.docker-spike-qualification-record/2"
POINTER_SCHEMA = "siffran.empirica.docker-spike-qualification-pointer/2"
NAMESPACE = "docker-spike-qualification-v2"
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_POINTER_BYTES = 64 * 1024
_CONTEXT = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
#: `docker version --format` projects exactly the server facts both observation paths use.
SERVER_VERSION_FORMAT = (
    '{"Version":{{json .Server.Version}},"Os":{{json .Server.Os}},'
    '"Arch":{{json .Server.Arch}},"PlatformName":{{json .Server.Platform.Name}}}'
)

RUNTIME_REQUIRED = frozenset({
    "docker_cli", "endpoint", "context", "client_version", "server_version",
    "server_raw_os", "server_os", "server_raw_architecture", "server_architecture",
    "platform_name", "operating_system", "security_options", "memory_limit",
    "swap_limit", "cpu_cfs_quota", "pids_limit", "ncpu", "mem_total", "cgroup_version",
    "cgroup_driver", "host_os", "host_raw_architecture", "host_architecture",
    "discovery_reads",
})
RECORD_FIELDS = frozenset({
    "schema", "profile_id", "runtime", "semantic_authority", "authority_digest",
    "policy_digest", "implementation_digest", "qualification_image", "probes", "cleanup",
    "qualification_elapsed_ms", "execution_binding_digest", "qualification_record_digest",
})


class GenericQualificationError(RuntimeError):
    pass


class DaemonDrift(GenericQualificationError):
    """The observed Docker daemon or capability differs semantically from the bound record."""


class CapabilityRefused(GenericQualificationError):
    """The endpoint resolved and the daemon answered, but its observed facts do not
    admit: an unsupported platform or capability (for example a macOS engine that is
    not Docker Desktop), never an unreachability and never a read failure."""


class ImplementationDrift(GenericQualificationError):
    """The loaded implementation release differs from disk, or from the bound record."""


MAX_DAEMON_ID_CHARS = 128


def _daemon_id(value: object) -> str:
    if (type(value) is not str or not value.strip() or len(value) > MAX_DAEMON_ID_CHARS
            or any(ord(character) <= 0x1F or 0x7F <= ord(character) <= 0x9F
                   for character in value)):
        raise GenericQualificationError("Docker daemon identity is malformed")
    return value


@dataclass(frozen=True, slots=True)
class GenericRuntimeInspection:
    runtime: dict[str, Any]


class PassiveGenericRuntimeInspector(Protocol):
    def __call__(self, recorded_runtime: Mapping[str, Any], *, deadline: float) \
            -> GenericRuntimeInspection: ...


@dataclass(frozen=True, slots=True)
class GenericSpikesUnavailable:
    state: str = "spikes_unavailable"


@dataclass(frozen=True, slots=True)
class ResolvedGenericAuthority:
    backend: application.GenericQualifiedBackend
    docker_cli: Path
    environment: tuple[tuple[str, str], ...]
    work_directory: Path
    os: str
    architecture: str
    engine_ncpu: int
    mem_total: int
    runtime: dict[str, Any]


def _digest(value: object, where: str) -> str:
    if not is_digest256(value):
        raise GenericQualificationError(where + " must be lowercase sha256")
    return value


def _profile(value: object) -> str:
    if type(value) is not str or value not in spike_plan.NETWORK_PROFILES:
        raise GenericQualificationError("generic Docker profile is unknown")
    return value


def _endpoint(value: object) -> str:
    if type(value) is not str or not value.startswith("unix:///") \
            or any(character.isspace() for character in value) \
            or len(value.encode("utf-8")) > 4096:
        raise GenericQualificationError("Docker endpoint must be a local Unix socket")
    raw = value.removeprefix("unix://")
    path = Path(raw)
    if not path.is_absolute() or str(path) != raw or path == Path("/") or ".." in path.parts:
        raise GenericQualificationError("Docker endpoint is not canonical")
    return value


def _canonical_cli(value: object) -> str:
    if type(value) is not str:
        raise GenericQualificationError("Docker CLI path is malformed")
    supplied = Path(value)
    try:
        canonical = supplied.resolve(strict=True)
        info = canonical.stat()
    except OSError as exc:
        raise GenericQualificationError("Docker CLI is unavailable") from exc
    if (not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) & 0o002 or not os.access(canonical, os.X_OK)):
        raise GenericQualificationError(
            "Docker CLI target must be regular, executable, and not world-writable")
    return str(canonical)


def _security_options(value: object) -> tuple[str, ...]:
    if type(value) is not list or len(value) > 64 \
            or any(type(item) is not str or not item for item in value):
        raise GenericQualificationError("Docker security options are malformed")
    options = tuple(sorted(set(value)))
    seccomp = any(item in {"name=seccomp,profile=default", "name=seccomp,profile=builtin"}
                  for item in options)
    if "name=cgroupns" not in options or not seccomp:
        raise GenericQualificationError("Docker cgroupns or default seccomp is unavailable")
    return options


def validate_runtime(value: object) -> dict[str, Any]:
    if type(value) is not dict or not RUNTIME_REQUIRED.issubset(value):
        raise GenericQualificationError("Docker runtime facts are incomplete")
    runtime = {key: value[key] for key in RUNTIME_REQUIRED}
    runtime["docker_cli"] = _canonical_cli(runtime["docker_cli"])
    runtime["endpoint"] = _endpoint(runtime["endpoint"])
    if type(runtime["context"]) is not str or _CONTEXT.fullmatch(runtime["context"]) is None:
        raise GenericQualificationError("Docker context is malformed")
    for key in ("client_version", "server_version", "server_raw_os",
                "server_raw_architecture", "platform_name", "operating_system",
                "cgroup_version", "cgroup_driver", "host_os", "host_raw_architecture"):
        if type(runtime[key]) is not str:
            raise GenericQualificationError("Docker diagnostic text is malformed")
    try:
        runtime["server_os"] = spike_plan.canonical_os(runtime["server_raw_os"])
        runtime["server_architecture"] = spike_plan.canonical_architecture(
            runtime["server_raw_architecture"])
        runtime["host_architecture"] = spike_plan.canonical_architecture(
            runtime["host_raw_architecture"])
    except (TypeError, ValueError) as exc:
        raise GenericQualificationError("Docker platform facts are malformed") from exc
    runtime["security_options"] = list(_security_options(runtime["security_options"]))
    for key in ("memory_limit", "cpu_cfs_quota", "pids_limit"):
        if runtime[key] is not True:
            raise GenericQualificationError(
                "resource-limits-unavailable: Docker memory, CPU, and PID enforcement is required")
    if runtime["swap_limit"] is not True:
        raise GenericQualificationError(
            "resource-limits-unavailable: Docker SwapLimit must be enabled so memory-swap equals memory")
    for key in ("ncpu", "mem_total"):
        if type(runtime[key]) is not int or isinstance(runtime[key], bool) or runtime[key] <= 0:
            raise GenericQualificationError("Docker capacity fact is unavailable")
    if runtime["server_os"] != "linux" or runtime["host_os"] not in {"Darwin", "Linux"}:
        raise GenericQualificationError("Docker native Linux platform is unavailable")
    identity = (runtime["platform_name"] + " " + runtime["operating_system"]).lower()
    if "docker" not in identity:
        raise GenericQualificationError("server is not a genuine Docker Engine or Desktop")
    if runtime["host_os"] == "Darwin" and "docker desktop" not in identity:
        raise GenericQualificationError("macOS support requires Docker Desktop")
    if runtime["discovery_reads"] != 5:
        raise GenericQualificationError("Docker passive discovery read count differs")
    if "daemon_id" in value:
        runtime["daemon_id"] = _daemon_id(value["daemon_id"])
    return json.loads(json.dumps(runtime))


def authority_facts(runtime: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_runtime(dict(runtime))
    seccomp = ("default" if "name=seccomp,profile=default" in checked["security_options"]
               else "builtin")
    facts = {
        "endpoint": checked["endpoint"],
        "server_os": checked["server_os"],
        "server_architecture": checked["server_architecture"],
        "required_security": {"cgroupns": True, "seccomp_class": seccomp},
        "memory_limit": True, "swap_limit": True,
        "cpu_cfs_quota": True, "pids_limit": True,
    }
    if "daemon_id" in checked:
        facts["daemon_id"] = checked["daemon_id"]
    return facts


def authorities_match(bound: Mapping[str, Any], observed: Mapping[str, Any]) -> bool:
    """Semantic daemon equality: `daemon_id` joins only when the bound record's runtime carries
    it, so legacy `/2` records without one stay valid against a daemon that now reports it."""
    expected = authority_facts(bound)
    actual = authority_facts(observed)
    if "daemon_id" not in expected:
        actual.pop("daemon_id", None)
    return actual == expected


def implementation_release_digest(manifest: object) -> str:
    if (not isinstance(manifest, Mapping)
            or type(manifest.get("implementation_digest")) is not str):
        raise ImplementationDrift("implementation identity drifted")
    return manifest["implementation_digest"]


def validate_record(value: object) -> dict[str, Any]:
    from .generic_qualification import GenericQualificationFailure, validate_candidate_record
    try:
        return validate_candidate_record(value)
    except GenericQualificationFailure as exc:
        raise GenericQualificationError(str(exc)) from exc


def _json_document(raw: bytes, where: str) -> object:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenericQualificationError(where + " is malformed") from exc
    return value


class GenericQualificationStore:
    """Read only current qualification records and pointers; never publishes or repairs."""
    def __init__(self, sandbox_root: Path | None = None, **_: Any) -> None:
        self.sandbox_root = common.sandbox_home() if sandbox_root is None else Path(sandbox_root)
        self.root = self.sandbox_root / NAMESPACE
        self.records = self.root / "records"
        self.pointers = self.root / "pointers"
        self.configs = self.sandbox_root / "docker-spike-config-v2"
        self.work = self.sandbox_root / "docker-spike-work-v2"
        self.execution_store = common.ExecutionStore(self.sandbox_root)

    def check_runtime(self, profile_id: str) -> tuple[Path, Path]:
        profile = _profile(profile_id)
        config = self.configs / profile
        for path in (self.sandbox_root, self.root, self.records, self.pointers,
                     self.configs, config, self.work):
            common.safe_directory(path, create=False)
        try:
            if tuple(config.iterdir()):
                raise GenericQualificationError(
                    "Docker config authority contains foreign files; remove them from the private "
                    f"{profile} config directory and requalify")
        except OSError as exc:
            raise GenericQualificationError("Docker config authority is unreadable") from exc
        return config, self.work

    def load_record(self, record_digest: str) -> dict[str, Any]:
        digest_value = _digest(record_digest, "qualification record digest")
        raw = common.safe_file(
            self.records / (digest_value.removeprefix("sha256:") + ".json"),
            cap=MAX_RECORD_BYTES)
        record = validate_record(_json_document(raw, "qualification record"))
        if record["qualification_record_digest"] != digest_value:
            raise GenericQualificationError("qualification record path differs")
        return record

    def load_current(self, profile_id: str) -> dict[str, Any]:
        profile = _profile(profile_id)
        raw = common.safe_file(self.pointers / (profile + ".json"), cap=MAX_POINTER_BYTES)
        pointer = _json_document(raw, "qualification pointer")
        fields = {"schema", "profile_id", "qualification_record_digest",
                  "execution_binding_digest"}
        if type(pointer) is not dict or set(pointer) != fields \
                or pointer.get("schema") != POINTER_SCHEMA or pointer.get("profile_id") != profile:
            raise GenericQualificationError("qualification pointer is malformed")
        record = self.load_record(pointer["qualification_record_digest"])
        if record["execution_binding_digest"] != pointer["execution_binding_digest"]:
            raise GenericQualificationError("qualification pointer binding differs")
        return record


class GenericBackendSelector:
    def __init__(self, store: GenericQualificationStore) -> None:
        self.store = store

    def select(self, requested_profile: str | None = None):
        profile = spike_plan.ISOLATED_PROFILE if requested_profile is None else requested_profile
        try:
            self.store.check_runtime(profile)
            record = self.store.load_current(profile)
            return application.GenericQualifiedBackend(
                "docker", profile, record["qualification_record_digest"],
                record["execution_binding_digest"])
        except Exception:
            return GenericSpikesUnavailable()


def _strict_json(text: str, where: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GenericQualificationError(where + " is malformed JSON") from exc


def _verified_cli(recorded: Mapping[str, Any], *, deadline: float,
                  clock: Callable[[], float]) -> str:
    if deadline <= clock():
        raise GenericQualificationError("passive verification deadline expired")
    return _canonical_cli(recorded.get("docker_cli"))


def _admit_command_result(value: object, ceiling: int, where: str) -> process.CommandResult:
    if (type(value) is not process.CommandResult or type(value.returncode) is not int
            or isinstance(value.returncode, bool) or type(value.timed_out) is not bool
            or type(value.output_limited) is not bool):
        raise GenericQualificationError(where + " returned a malformed process result")
    for name in ("stdout", "stderr"):
        capture = getattr(value, name)
        if (type(capture) is not process.StreamCapture or type(capture.data) is not bytes
                or capture.bytes_seen != len(capture.data) or capture.bytes_seen > ceiling
                or capture.sha256 != "sha256:" + hashlib.sha256(capture.data).hexdigest()):
            raise GenericQualificationError(where + " returned malformed bounded output")
    return value


def _inspect_runtime(
    recorded_runtime: Mapping[str, Any], *, pinned: bool, admit: bool, deadline: float,
    runner: process.Runner | None, clock: Callable[[], float],
    host_facts: tuple[str, str] | None,
) -> GenericRuntimeInspection:
    """The one bounded passive observation of the Docker authority.

    The endpoint source is either one discovery (``pinned`` false: a context read and an
    endpoint read, then every remaining read is pinned to that endpoint, so an ambient
    ``DOCKER_HOST`` change between reads never reaches a second daemon) or the endpoint
    a valid recorded runtime already pinned (``pinned`` true: no context discovery, and
    the facts inherit the recorded context and discovery read count). A command that
    cannot be launched or read is one closed read failure of the daemon. With ``admit``
    the facts are admitted, and facts that do not admit after every read succeeded are a
    capability refusal of a reachable daemon; without it the raw facts are returned for
    the caller's decision.
    """
    if pinned:
        try:
            recorded = validate_runtime(dict(recorded_runtime))
        except GenericQualificationError as exc:
            raise GenericQualificationError("the recorded pinned runtime is malformed") from exc
        cli = recorded["docker_cli"]
    else:
        cli = _verified_cli(recorded_runtime, deadline=deadline, clock=clock)
    selected = process.BoundedRunner() if runner is None else runner
    environment = {"LANG": "C", "LC_ALL": "C"}
    daemon = "pinned" if pinned else "observed"

    def read(args: tuple[str, ...], where: str) -> str:
        remaining = deadline - clock()
        if remaining <= 0:
            raise GenericQualificationError("passive verification deadline expired")
        try:
            launched = selected.run(
                (cli, *args), timeout_seconds=min(5.0, remaining),
                output_limit_bytes=MAX_POINTER_BYTES, env=environment)
        except OSError as exc:
            raise GenericQualificationError(
                where + " could not be launched at the " + daemon + " daemon") from exc
        result = _admit_command_result(launched, MAX_POINTER_BYTES, where)
        if (result.timed_out
                or result.output_limited or result.returncode != 0):
            raise GenericQualificationError(where + " failed")
        try:
            text = result.stdout.data.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise GenericQualificationError(where + " is not UTF-8") from exc
        if not text:
            raise GenericQualificationError(where + " is empty")
        return text

    if pinned:
        endpoint, context = recorded["endpoint"], recorded["context"]
        discovery_reads = recorded["discovery_reads"]
    else:
        context = read(("context", "show"), "Docker context read")
        if _CONTEXT.fullmatch(context) is None:
            raise GenericQualificationError("Docker context is malformed")
        endpoint = _endpoint(_strict_json(
            read(("context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"),
                 "Docker endpoint read"), "Docker endpoint"))
        discovery_reads = 5
    environment = {**environment, "DOCKER_HOST": endpoint}
    client = _strict_json(read(("version", "--format", "{{json .Client.Version}}"),
                               "Docker client version read"), "Docker client version")
    server = _strict_json(read(("version", "--format", SERVER_VERSION_FORMAT),
                               "Docker server version read"), "Docker server version")
    # docker/cli formats `info` over a Go struct (no `index`, field names differ from JSON keys),
    # so read the Engine-API JSON whole and project only the keys used below by their JSON names.
    info = _strict_json(read(("info", "--format", "{{json .}}"), "Docker info read"),
                        "Docker info")
    if type(client) is not str or type(server) is not dict or type(info) is not dict:
        raise GenericQualificationError("Docker version/info projection is malformed")
    raw_os = server.get("Os") or info.get("OSType")
    raw_arch = server.get("Arch") or info.get("Architecture")
    host_os, host_arch = ((platform.system(), platform.machine())
                          if host_facts is None else host_facts)
    runtime = {
        "docker_cli": cli, "endpoint": endpoint, "context": context,
        "client_version": client, "server_version": server.get("Version", ""),
        "server_raw_os": raw_os, "server_os": raw_os,
        "server_raw_architecture": raw_arch, "server_architecture": raw_arch,
        "platform_name": server.get("PlatformName", ""),
        "operating_system": info.get("OperatingSystem", ""),
        "security_options": info.get("SecurityOptions"),
        "memory_limit": info.get("MemoryLimit"), "swap_limit": info.get("SwapLimit"),
        "cpu_cfs_quota": info.get("CpuCfsQuota"),
        "pids_limit": info.get("PidsLimit"), "ncpu": info.get("NCPU"),
        "mem_total": info.get("MemTotal"), "cgroup_version": info.get("CgroupVersion") or "",
        "cgroup_driver": info.get("CgroupDriver") or "", "host_os": host_os,
        "host_raw_architecture": host_arch, "host_architecture": host_arch,
        "discovery_reads": discovery_reads,
    }
    if "ID" in info:
        runtime["daemon_id"] = info["ID"]
    if not admit:
        return GenericRuntimeInspection(runtime)
    try:
        return GenericRuntimeInspection(validate_runtime(runtime))
    except GenericQualificationError as exc:
        raise CapabilityRefused(str(exc)) from exc


def inspect_observed_runtime(
    recorded_runtime: Mapping[str, Any], *, deadline: float,
    runner: process.Runner | None = None,
    clock: Callable[[], float] = time.monotonic,
    host_facts: tuple[str, str] | None = None,
    **_: Any,
) -> GenericRuntimeInspection:
    """Discover the endpoint once and return the raw facts without admission.

    Journal recovery runs on these facts before the new attempt's admission decides the
    capability, so a capability refusal seen here is an observation to recover on, never
    a boundary error that would skip recovery.
    """
    return _inspect_runtime(recorded_runtime, pinned=False, admit=False, deadline=deadline,
                            runner=runner, clock=clock, host_facts=host_facts)


def inspect_current_runtime(
    recorded_runtime: Mapping[str, Any], *, deadline: float,
    runner: process.Runner | None = None,
    clock: Callable[[], float] = time.monotonic,
    host_facts: tuple[str, str] | None = None,
    **_: Any,
) -> GenericRuntimeInspection:
    """Discover the endpoint once and admit the facts; a refusal is :class:`CapabilityRefused`."""
    return _inspect_runtime(recorded_runtime, pinned=False, admit=True, deadline=deadline,
                            runner=runner, clock=clock, host_facts=host_facts)


def inspect_pinned_runtime(
    recorded_runtime: Mapping[str, Any], *, deadline: float,
    runner: process.Runner | None = None,
    clock: Callable[[], float] = time.monotonic,
    host_facts: tuple[str, str] | None = None,
    **_: Any,
) -> GenericRuntimeInspection:
    """Re-observe an already-discovered daemon at its recorded endpoint, without admission.

    Fresh facts that no longer admit are a changed observation of the same pinned daemon
    (:func:`validate_reobserved_runtime`), never a fresh capability refusal and never a
    collapsed read failure.
    """
    return _inspect_runtime(recorded_runtime, pinned=True, admit=False, deadline=deadline,
                            runner=runner, clock=clock, host_facts=host_facts)


def validate_reobserved_runtime(pinned: Mapping[str, Any], observed: object) -> dict[str, Any]:
    """Admit the fresh facts of a pinned re-observation (postflight, E5).

    The pinned runtime was admitted when the pin was made, so fresh facts that no longer
    admit are a changed observation of that same daemon — the caller reports daemon
    drift or staleness — never a fresh capability refusal (which belongs to the initial
    observation) and never a collapsed read failure (which fails before this point).
    """
    try:
        return validate_runtime(observed)
    except GenericQualificationError as exc:
        raise DaemonDrift("Docker daemon authority drifted during re-observation: " + str(exc)) from exc


def pinned_daemon_identity(observed_runtime: object) -> dict[str, str]:
    """The minimum daemon identity journal recovery needs, without capability admission.

    Recovery precedes new-attempt admission: this projects only what journal
    identity keys on — the canonical endpoint and the canonical server platform, never
    capability facts and never the daemon identifier — from the raw discovery facts, so
    a daemon that now refuses capability still gets its prior journals recovered.
    """
    try:
        facts = observed_runtime if isinstance(observed_runtime, Mapping) else {}
        return {
            "endpoint": _endpoint(facts.get("endpoint")),
            "server_os": spike_plan.canonical_os(facts.get("server_raw_os")),
            "server_architecture": spike_plan.canonical_architecture(
                facts.get("server_raw_architecture")),
        }
    except (GenericQualificationError, TypeError, ValueError) as exc:
        raise GenericQualificationError(
            "the Docker authority observation cannot identify a recovery daemon") from exc


class GenericAuthorityResolver:
    def __init__(self, store: GenericQualificationStore, *,
                 runtime_inspector: PassiveGenericRuntimeInspector = inspect_current_runtime,
                 implementation_inspector: Callable[[], Mapping[str, Any]] = common.implementation_manifest,
                 loaded_release: str | None = None,
                 clock: Callable[[], float] = time.monotonic, **_: Any) -> None:
        self.store = store
        self.runtime_inspector = runtime_inspector
        self.implementation_inspector = implementation_inspector
        # The loaded release is frozen once per process at the loaded-release boundary and
        # injected here; when absent (direct construction), the first resolver seeds the
        # per-process freeze from its implementation inspector, never its own per-instance
        # snapshot of a manifest that may already differ from the executing code.
        if loaded_release is not None:
            if type(loaded_release) is not str or not loaded_release:
                raise GenericQualificationError("loaded release digest is malformed")
            self.loaded_implementation_digest = loaded_release
        else:
            self.loaded_implementation_digest = common.freeze_loaded_release(
                self.implementation_inspector)
        self.clock = clock

    def resolve(self, backend: application.GenericQualifiedBackend, *, deadline: float) \
            -> ResolvedGenericAuthority:
        if type(backend) is not application.GenericQualifiedBackend or deadline <= self.clock():
            raise GenericQualificationError("historical authority input is malformed")
        config, work = self.store.check_runtime(backend.profile_id)
        first = self.store.load_record(backend.qualification_record_digest)
        if (first["profile_id"] != backend.profile_id
                or first["execution_binding_digest"] != backend.execution_binding_digest):
            raise GenericQualificationError("historical backend binding differs")
        observed = self.runtime_inspector(first["runtime"], deadline=deadline)
        if type(observed) is not GenericRuntimeInspection:
            raise GenericQualificationError("runtime inspector returned an unknown value")
        current = validate_runtime(observed.runtime)
        if not authorities_match(first["runtime"], current):
            raise DaemonDrift("Docker daemon authority drifted")
        if (self.loaded_implementation_digest != first["implementation_digest"]
                or implementation_release_digest(self.implementation_inspector())
                != self.loaded_implementation_digest):
            raise ImplementationDrift("implementation identity drifted")
        if self.store.load_record(backend.qualification_record_digest) != first:
            raise GenericQualificationError("qualification record changed while resolving")
        return ResolvedGenericAuthority(
            backend, Path(current["docker_cli"]), tuple(sorted({
                "LANG": "C", "LC_ALL": "C", "DOCKER_HOST": current["endpoint"],
                "DOCKER_CONFIG": str(config),
            }.items())), work, current["server_os"], current["server_architecture"],
            current["ncpu"], current["mem_total"], current)


class DockerLocalImageResolver(application.LocalImageResolver):
    """One bounded read-only `docker image inspect`; never acquires an image."""
    def __init__(self, authority: GenericAuthorityResolver, *, runner: process.Runner | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.authority = authority
        self.runner = process.BoundedRunner() if runner is None else runner
        self.clock = clock

    @staticmethod
    def _failure(stderr: bytes):
        text = stderr.decode("utf-8", errors="replace").lower()
        if "ambiguous" in text or "multiple ids" in text or "multiple images" in text:
            return application.GenericPreparationReason.IMAGE_REFERENCE_AMBIGUOUS
        if "no such image" in text or "not found" in text or "does not exist" in text:
            return application.GenericPreparationReason.IMAGE_NOT_LOCAL
        return application.GenericPreparationReason.MALFORMED_REQUIRED_RESULT

    def resolve(self, requested_reference: str, backend: application.GenericQualifiedBackend):
        try:
            spike_plan.validate_local_image_reference(requested_reference)
        except Exception:
            return application.LocalImageResolutionUnavailable(
                application.GenericPreparationReason.MALFORMED_REQUIRED_RESULT)
        try:
            deadline = self.clock() + 15.0
            authority = self.authority.resolve(backend, deadline=deadline)
        except Exception:
            return application.LocalImageResolutionUnavailable(
                application.GenericPreparationReason.AUTHORITY_DRIFT)
        try:
            argv = (str(authority.docker_cli), "image", "inspect", "--format",
                    planner.IMAGE_INSPECT_FORMAT, requested_reference)
            result = _admit_command_result(self.runner.run(
                argv, timeout_seconds=10.0, output_limit_bytes=MAX_POINTER_BYTES,
                env=dict(authority.environment)), MAX_POINTER_BYTES, "Docker image inspect")
            if result.timed_out or result.output_limited:
                return application.LocalImageResolutionUnavailable(
                    application.GenericPreparationReason.MALFORMED_REQUIRED_RESULT)
            if result.returncode != 0:
                return application.LocalImageResolutionUnavailable(self._failure(result.stderr.data))
            document = generic_admission.json_object(result.stdout.data)
            image_id, raw_os, raw_arch, volumes = generic_admission.decode_image_document(document)
            return application.LocalImageResolution(
                requested_reference, image_id, raw_os, raw_arch,
                authority.runtime["server_raw_os"],
                authority.runtime["server_raw_architecture"], volumes,
                authority.engine_ncpu, backend)
        except application.GenericPreparationUnavailable as exc:
            return application.LocalImageResolutionUnavailable(exc.reason)
        except Exception:
            return application.LocalImageResolutionUnavailable(
                application.GenericPreparationReason.MALFORMED_REQUIRED_RESULT)
