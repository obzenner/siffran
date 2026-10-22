"""Pure host-sealed single-container spike plan and framework policy.

The author-facing draft lives in :mod:`core.spike_plan_draft`. This module contains only
host-owned immutable facts; it performs no Docker or filesystem effects.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .canonical import canonical_digest
from .freshness import validate_digest256, validate_relative_posix_path

PLAN_SCHEMA = "empirica.spike-plan/2"
POLICY_SCHEMA = "empirica.spike-plan-policy/2"
REQUEST_SCHEMA = "empirica.spike-request/2"

MAX_ENVIRONMENT = 128
MAX_ARGV = 256
MAX_TEXT_BYTES = 4096
MAX_PATH_BYTES = 1024
MAX_IMAGE_REFERENCE_BYTES = 255
MAX_STAGING_FILES = 256
MAX_STAGING_BYTES = 64 * 1024 * 1024
MAX_FILE_SIZE_BYTES = 64 * 1024 * 1024
WORKLOAD_TIMEOUT_SECONDS = 120
BACKEND_OPERATION_TIMEOUT_SECONDS = 210

FIXED_UID = 65534
FIXED_GID = 65534
FIXED_WORKDIR = "/work"
FIXED_INPUTS = "/inputs"
FIXED_TMPDIR = "/tmp"
WORK_TMPFS_BYTES = 512 * 1024 * 1024
TMP_TMPFS_BYTES = 64 * 1024 * 1024
SHM_BYTES = 64 * 1024 * 1024
MEMORY_BYTES = 2 * 1024 * 1024 * 1024
PIDS = 256
OPEN_FILES = 1024
STREAM_BYTES = 1024 * 1024
NETWORK_PROFILES = frozenset({"docker-isolated-v1", "docker-networked-v1"})

_ARCHITECTURE_ALIASES = {"aarch64": "arm64", "x86_64": "amd64"}
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_PREFIX = re.compile(r"^(?:sha256:)?[0-9a-f]{12,64}$")
_REPO_DIGEST = re.compile(r"^(.+)@sha256:[0-9a-f]{64}$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_NAME_COMPONENT = re.compile(r"^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*$")
_DOMAIN = re.compile(
    r"^(?:localhost|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::[0-9]{1,5})?$"
)
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class SpikePlanError(ValueError):
    """A pure author or host-sealed plan fact is malformed."""


class NaturalVerdict(Enum):
    PASS = "PASS"
    FAIL = "FAIL"


def natural_verdict(exit_code: object) -> NaturalVerdict | None:
    """Map one supervised natural exit to evidence; 137 is always unavailable."""
    if type(exit_code) is not int or isinstance(exit_code, bool) or not 0 <= exit_code <= 255:
        raise SpikePlanError("natural exit must be an unsigned byte")
    if exit_code == 137:
        return None
    return NaturalVerdict.PASS if exit_code == 0 else NaturalVerdict.FAIL


def _bounded_text(value: object, path: str, *, nonempty: bool = False,
                  maximum: int = MAX_TEXT_BYTES) -> str:
    if type(value) is not str or "\x00" in value:
        raise SpikePlanError(f"{path} must be bounded text")
    if nonempty and not value:
        raise SpikePlanError(f"{path} must be nonempty")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise SpikePlanError(f"{path} must be UTF-8") from exc
    if size > maximum:
        raise SpikePlanError(f"{path} exceeds its byte bound")
    return value


def _valid_name(name: str) -> bool:
    parts = name.split("/")
    if not parts or any(not part for part in parts):
        return False
    start = 0
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        if _DOMAIN.fullmatch(parts[0]) is None:
            return False
        start = 1
    return start < len(parts) and all(_NAME_COMPONENT.fullmatch(part) for part in parts[start:])


def validate_local_image_reference(value: object) -> str:
    """Validate syntax only; locality and prefix ambiguity are host-owned effects."""
    text = _bounded_text(
        value, "image reference", nonempty=True, maximum=MAX_IMAGE_REFERENCE_BYTES)
    if _IMAGE_ID.fullmatch(text) or _IMAGE_PREFIX.fullmatch(text):
        return text
    digest = _REPO_DIGEST.fullmatch(text)
    if digest is not None:
        if _valid_name(digest.group(1)):
            return text
        raise SpikePlanError("repository digest name is malformed")
    if "@" in text:
        raise SpikePlanError("image digest is malformed")
    slash = text.rfind("/")
    colon = text.rfind(":")
    if colon > slash:
        name, tag = text[:colon], text[colon + 1:]
        if not tag or _TAG.fullmatch(tag) is None:
            raise SpikePlanError("image tag is malformed")
    else:
        name = text
    if not _valid_name(name):
        raise SpikePlanError("image name is malformed")
    return text


def canonical_architecture(value: object) -> str:
    raw = _bounded_text(value, "architecture", nonempty=True, maximum=128)
    return _ARCHITECTURE_ALIASES.get(raw, raw)


def canonical_os(value: object) -> str:
    return _bounded_text(value, "operating system", nonempty=True, maximum=128)


@dataclass(frozen=True, slots=True)
class SpikePlanPolicy:
    max_cpu_count: int = 2
    memory_bytes: int = MEMORY_BYTES
    memory_swap_bytes: int = MEMORY_BYTES
    pids: int = PIDS
    open_files: int = OPEN_FILES
    max_file_size_bytes: int = MAX_FILE_SIZE_BYTES
    core_bytes: int = 0
    workload_timeout_seconds: int = WORKLOAD_TIMEOUT_SECONDS
    stdout_bytes: int = STREAM_BYTES
    stderr_bytes: int = STREAM_BYTES
    staging_files: int = MAX_STAGING_FILES
    staging_bytes: int = MAX_STAGING_BYTES
    staging_file_bytes: int = MAX_FILE_SIZE_BYTES
    work_tmpfs_bytes: int = WORK_TMPFS_BYTES
    tmp_tmpfs_bytes: int = TMP_TMPFS_BYTES
    shm_bytes: int = SHM_BYTES

    def __post_init__(self) -> None:
        defaults = {
            field: type(self).__dataclass_fields__[field].default
            for field in type(self).__dataclass_fields__
        }
        if any(type(getattr(self, field)) is not type(value)
               or getattr(self, field) != value for field, value in defaults.items()):
            raise SpikePlanError("framework policy differs from the Empirica 6 MVP")

    def to_data(self) -> dict[str, Any]:
        return {
            "schema": POLICY_SCHEMA,
            "max_cpu_count": self.max_cpu_count,
            "memory_bytes": self.memory_bytes,
            "memory_swap_bytes": self.memory_swap_bytes,
            "pids": self.pids,
            "open_files": self.open_files,
            "max_file_size_bytes": self.max_file_size_bytes,
            "core_bytes": self.core_bytes,
            "workload_timeout_seconds": self.workload_timeout_seconds,
            "stdout_bytes": self.stdout_bytes,
            "stderr_bytes": self.stderr_bytes,
            "staging_files": self.staging_files,
            "staging_bytes": self.staging_bytes,
            "staging_file_bytes": self.staging_file_bytes,
            "work_tmpfs_bytes": self.work_tmpfs_bytes,
            "tmp_tmpfs_bytes": self.tmp_tmpfs_bytes,
            "shm_bytes": self.shm_bytes,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_data())


FRAMEWORK_SPIKE_POLICY = SpikePlanPolicy()


@dataclass(frozen=True, slots=True)
class ResolvedImage:
    requested_reference: str
    image_id: str
    raw_os: str
    os: str
    raw_architecture: str
    architecture: str
    declared_volumes: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_local_image_reference(self.requested_reference)
        try:
            validate_digest256(self.image_id)
        except ValueError as exc:
            raise SpikePlanError("resolved image ID must be a full lowercase digest") from exc
        if self.os != canonical_os(self.raw_os):
            raise SpikePlanError("resolved image OS is not canonical")
        if self.architecture != canonical_architecture(self.raw_architecture):
            raise SpikePlanError("resolved image architecture is not canonical")
        if type(self.declared_volumes) is not tuple or self.declared_volumes:
            raise SpikePlanError("resolved image must declare no volumes")

    def to_data(self) -> dict[str, str]:
        return {
            "requested_reference": self.requested_reference,
            "id": self.image_id,
            "raw_os": self.raw_os,
            "os": self.os,
            "raw_architecture": self.raw_architecture,
            "architecture": self.architecture,
            "declared_volumes": [],
        }


@dataclass(frozen=True, slots=True)
class CapturedFile:
    path: str
    sha256: str
    bytes: int

    def __post_init__(self) -> None:
        try:
            validate_relative_posix_path(self.path)
            validate_digest256(self.sha256)
        except ValueError as exc:
            raise SpikePlanError("captured file identity is malformed") from exc
        if (type(self.bytes) is not int or isinstance(self.bytes, bool)
                or not 0 <= self.bytes <= MAX_FILE_SIZE_BYTES):
            raise SpikePlanError("captured file size is out of bounds")

    def to_data(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "bytes": self.bytes}


def _validate_declared_inputs(value: tuple[str, ...]) -> None:
    if type(value) is not tuple:
        raise SpikePlanError("declared inputs are not canonical")
    try:
        canonical = tuple(sorted(value))
        unique = len(set(value)) == len(value)
    except TypeError as exc:
        raise SpikePlanError("declared inputs are malformed") from exc
    if (canonical != value
            or not unique or len(value) > MAX_STAGING_FILES):
        raise SpikePlanError("declared inputs are not canonical")
    bases = []
    for item in value:
        if type(item) is not str or not item:
            raise SpikePlanError("declared input is malformed")
        if len(item.encode("utf-8")) > MAX_PATH_BYTES:
            raise SpikePlanError("declared input exceeds its byte bound")
        candidate = item[:-1] if item.endswith("/") else item
        try:
            validate_relative_posix_path(candidate)
        except ValueError as exc:
            raise SpikePlanError("declared input is not repository-relative") from exc
        if ".git" in candidate.split("/"):
            raise SpikePlanError(".git cannot be captured")
        bases.append(candidate)
    for index, base in enumerate(bases):
        if any(other.startswith(base + "/") for other in bases[index + 1:]):
            raise SpikePlanError("declared inputs overlap")


def _captured_by_declarations(declarations: tuple[str, ...], path: str) -> bool:
    return any(path == item.rstrip("/") or path.startswith(item.rstrip("/") + "/")
               for item in declarations)


@dataclass(frozen=True, slots=True)
class SealedSpikePlan:
    draft_digest: str
    image: ResolvedImage
    argv: tuple[str, ...]
    declared_inputs: tuple[str, ...]
    captured_files: tuple[CapturedFile, ...]
    capture_digest: str
    environment: tuple[tuple[str, str], ...]
    engine_ncpu: int
    cpu_count: int
    network_profile: str
    policy_digest: str

    def __post_init__(self) -> None:
        try:
            validate_digest256(self.draft_digest)
            validate_digest256(self.capture_digest)
            validate_digest256(self.policy_digest)
        except ValueError as exc:
            raise SpikePlanError("sealed plan digest is malformed") from exc
        if type(self.image) is not ResolvedImage:
            raise SpikePlanError("sealed plan image is not resolved")
        if (type(self.argv) is not tuple or not self.argv or len(self.argv) > MAX_ARGV
                or any(type(item) is not str for item in self.argv)
                or not self.argv[0]):
            raise SpikePlanError("sealed argv is malformed")
        for index, item in enumerate(self.argv):
            _bounded_text(item, f"argv[{index}]", nonempty=index == 0)
        _validate_declared_inputs(self.declared_inputs)
        if type(self.captured_files) is not tuple or any(
                type(item) is not CapturedFile for item in self.captured_files):
            raise SpikePlanError("sealed capture is malformed")
        if tuple(sorted(self.captured_files, key=lambda item: item.path)) != self.captured_files:
            raise SpikePlanError("captured files are not canonical")
        if len({item.path for item in self.captured_files}) != len(self.captured_files):
            raise SpikePlanError("captured files are duplicated")
        if any(not _captured_by_declarations(self.declared_inputs, item.path)
               for item in self.captured_files):
            raise SpikePlanError("captured file falls outside declared inputs")
        if not self.declared_inputs and self.captured_files:
            raise SpikePlanError("empty declarations cannot carry captured files")
        if (len(self.captured_files) > MAX_STAGING_FILES
                or sum(item.bytes for item in self.captured_files) > MAX_STAGING_BYTES):
            raise SpikePlanError("captured files exceed framework policy")
        expected_capture = canonical_digest([item.to_data() for item in self.captured_files])
        if self.capture_digest != expected_capture:
            raise SpikePlanError("capture digest differs from captured files")
        if type(self.environment) is not tuple:
            raise SpikePlanError("sealed environment is not canonical")
        try:
            canonical_environment = tuple(sorted(self.environment))
            environment = dict(self.environment)
        except (TypeError, ValueError) as exc:
            raise SpikePlanError("sealed environment is malformed") from exc
        if canonical_environment != self.environment:
            raise SpikePlanError("sealed environment is not canonical")
        if len(environment) != len(self.environment):
            raise SpikePlanError("sealed environment names are duplicated")
        _validate_environment(environment)
        if (type(self.engine_ncpu) is not int or isinstance(self.engine_ncpu, bool)
                or self.engine_ncpu <= 0
                or self.cpu_count != min(2, self.engine_ncpu)):
            raise SpikePlanError("sealed CPU count must be min(2, engine NCPU)")
        if self.network_profile not in NETWORK_PROFILES:
            raise SpikePlanError("sealed network profile is unknown")
        if self.policy_digest != FRAMEWORK_SPIKE_POLICY.digest:
            raise SpikePlanError("sealed policy identity differs")

    @property
    def schema(self) -> str:
        return PLAN_SCHEMA

    def to_data(self) -> dict[str, Any]:
        return {
            "schema": PLAN_SCHEMA,
            "draft_digest": self.draft_digest,
            "image": self.image.to_data(),
            "argv": list(self.argv),
            "inputs": list(self.declared_inputs),
            "captured_files": [item.to_data() for item in self.captured_files],
            "capture_digest": self.capture_digest,
            "environment": dict(self.environment),
            "engine_ncpu": self.engine_ncpu,
            "cpu_count": self.cpu_count,
            "uid": FIXED_UID,
            "gid": FIXED_GID,
            "workdir": FIXED_WORKDIR,
            "inputs_mount": FIXED_INPUTS,
            "tmpdir": FIXED_TMPDIR,
            "network_profile": self.network_profile,
            "policy_digest": self.policy_digest,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_data())


def sealed_plan_from_data(value: object) -> SealedSpikePlan:
    """Decode the exact closed host-only plan for pure request admission."""
    fields = {
        "schema", "draft_digest", "image", "argv", "inputs", "captured_files",
        "capture_digest", "environment", "engine_ncpu", "cpu_count", "uid", "gid", "workdir",
        "inputs_mount", "tmpdir", "network_profile", "policy_digest",
    }
    if type(value) is not dict or set(value) != fields or value.get("schema") != PLAN_SCHEMA:
        raise SpikePlanError("sealed plan shape is not closed")
    if (value.get("uid") != FIXED_UID or value.get("gid") != FIXED_GID
            or value.get("workdir") != FIXED_WORKDIR
            or value.get("inputs_mount") != FIXED_INPUTS
            or value.get("tmpdir") != FIXED_TMPDIR):
        raise SpikePlanError("sealed fixed runtime facts differ")
    image_data = value.get("image")
    image_fields = {"requested_reference", "id", "raw_os", "os",
                    "raw_architecture", "architecture", "declared_volumes"}
    if type(image_data) is not dict or set(image_data) != image_fields:
        raise SpikePlanError("sealed image shape is not closed")
    files_data = value.get("captured_files")
    if type(files_data) is not list:
        raise SpikePlanError("sealed captured files are malformed")
    try:
        image = ResolvedImage(
            image_data["requested_reference"], image_data["id"], image_data["raw_os"],
            image_data["os"], image_data["raw_architecture"], image_data["architecture"],
            tuple(image_data["declared_volumes"]))
        files = tuple(CapturedFile(item["path"], item["sha256"], item["bytes"])
                      for item in files_data
                      if type(item) is dict and set(item) == {"path", "sha256", "bytes"})
    except (KeyError, TypeError) as exc:
        raise SpikePlanError("sealed plan facts are malformed") from exc
    if len(files) != len(files_data):
        raise SpikePlanError("captured file shape is not closed")
    if type(value.get("argv")) is not list or type(value.get("inputs")) is not list:
        raise SpikePlanError("sealed vectors are malformed")
    environment = environment_from_data(value.get("environment"))
    plan = SealedSpikePlan(
        value["draft_digest"], image, tuple(value["argv"]), tuple(value["inputs"]), files,
        value["capture_digest"], environment, value["engine_ncpu"], value["cpu_count"],
        value["network_profile"], value["policy_digest"])
    if plan.to_data() != value:
        raise SpikePlanError("sealed plan is not canonical")
    return plan


# Public name for the one host-sealed plan. There is no multi-container alternative.
SpikePlan = SealedSpikePlan


@dataclass(frozen=True, slots=True)
class SealedSpikeRequest:
    plan: SealedSpikePlan
    policy: SpikePlanPolicy
    operation_timeout_seconds: int = BACKEND_OPERATION_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if type(self.plan) is not SealedSpikePlan or type(self.policy) is not SpikePlanPolicy:
            raise SpikePlanError("sealed request requires exact plan and policy values")
        if self.operation_timeout_seconds != BACKEND_OPERATION_TIMEOUT_SECONDS:
            raise SpikePlanError("sealed request operation timeout differs")

    def to_data(self) -> dict[str, Any]:
        return {
            "schema": REQUEST_SCHEMA,
            "plan": self.plan.to_data(),
            "plan_digest": self.plan.digest,
            "policy": self.policy.to_data(),
            "policy_digest": self.policy.digest,
            "operation_timeout_seconds": self.operation_timeout_seconds,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_data())


def sealed_request_from_data(value: object) -> SealedSpikeRequest:
    fields = {"schema", "plan", "plan_digest", "policy", "policy_digest",
              "operation_timeout_seconds"}
    if type(value) is not dict or set(value) != fields or value.get("schema") != REQUEST_SCHEMA:
        raise SpikePlanError("sealed request shape is not closed")
    plan = sealed_plan_from_data(value["plan"])
    if value.get("plan_digest") != plan.digest:
        raise SpikePlanError("sealed request plan digest differs")
    if value.get("policy") != FRAMEWORK_SPIKE_POLICY.to_data() \
            or value.get("policy_digest") != FRAMEWORK_SPIKE_POLICY.digest:
        raise SpikePlanError("sealed request policy differs")
    request = SealedSpikeRequest(
        plan, FRAMEWORK_SPIKE_POLICY, value["operation_timeout_seconds"])
    if request.to_data() != value:
        raise SpikePlanError("sealed request is not canonical")
    return request


def _validate_environment(value: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    if type(value) is not dict or len(value) > MAX_ENVIRONMENT:
        raise SpikePlanError("environment must be a bounded map")
    rows: list[tuple[str, str]] = []
    for name, item in value.items():
        if type(name) is not str or _ENVIRONMENT_NAME.fullmatch(name) is None:
            raise SpikePlanError("environment name is malformed")
        if name in {"HOME", "TMPDIR"}:
            raise SpikePlanError("HOME and TMPDIR are framework-owned")
        rows.append((name, _bounded_text(item, f"environment[{name}]")))
    return tuple(sorted(rows))


def environment_from_data(value: object) -> tuple[tuple[str, str], ...]:
    if type(value) is not dict:
        raise SpikePlanError("environment must be a map")
    return _validate_environment(value)
