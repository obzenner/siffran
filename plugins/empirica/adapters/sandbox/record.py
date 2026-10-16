"""User-scoped Docker qualification authority and bounded recovery journals.

The operator CLI is the only writer. Empirica run services consume this module read-only.
``EMPIRICA_SANDBOX_HOME`` is deliberately independent of ``EMPIRICA_HOME``.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence

from adapters.state import fsio
from core.freshness import canonical_digest

from . import docker, environment

RECORD_SCHEMA = "siffran.empirica.docker-qualification/1"
POINTER_SCHEMA = "siffran.empirica.docker-qualification-pointer/1"
PROFILE_SCHEMA = "siffran.empirica.docker-profile/1"
PROFILE_ID = "docker-rust-v1"
PROTOCOL_REVISION = "docker-sandbox/1"
JOURNAL_SCHEMA = "siffran.empirica.docker-operation/1"
SANDBOX_HOME_ENV = "EMPIRICA_SANDBOX_HOME"
MAX_JOURNALS = 32
MAX_JOURNAL_BYTES = 64 * 1024
MAX_CIDFILE_BYTES = 128
MAX_JOURNAL_TOTAL_BYTES = 512 * 1024
MAX_CONTROL_FILE_BYTES = 2 * 1024 * 1024
MAX_POINTER_BYTES = 64 * 1024
_MODE_FILE = 0o600
_MODE_DIR = 0o700
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
OPERATION_ID = re.compile(r"^o-[0-9a-f]{32}$")

_EMPIRICA_ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION_SOURCE_PATHS = (
    "adapters/execution.py",
    "adapters/sandbox/cli.py",
    "adapters/sandbox/docker.py",
    "adapters/sandbox/environment.py",
    "adapters/sandbox/execution.py",
    "adapters/sandbox/record.py",
    "adapters/sandbox/selection.py",
    "adapters/state/fsio.py",
    "application/observation.py",
    "application/ports.py",
    "application/protocol.py",
    "application/run_state.py",
    "application/transaction.py",
    "application/v2.py",
    "core/evaluation.py",
    "core/freshness.py",
    "core/run.py",
    "vendor/contracts/empirica/v2/state.schema.json",
)

RECORD_KEYS = {
    "schema", "qualification_protocol_revision", "subject", "profile", "implementation",
    "runtime", "runtime_digest", "environment", "environment_digest", "qualification",
    "execution_binding_digest",
    "created_at", "qualification_record_digest",
}
PROFILE_KEYS = {"schema", "id", "policy", "profile_digest"}
IMPLEMENTATION_KEYS = {"files", "implementation_digest"}
QUALIFICATION_KEYS = {"complete", "mandatory_probes", "results_digest", "report_provenance"}
POINTER_KEYS = {"schema", "execution_binding_digest", "qualification_record_digest"}
JOURNAL_KEYS = {
    "schema", "operation_id", "lifecycle_state", "execution_binding_digest",
    "qualification_record_digest", "policy_digest", "run_id", "role", "name", "labels",
    "expected_image", "full_id", "cidfile_name",
}
JOURNAL_STATES = {"intent", "created", "removing"}
JOURNAL_EVENTS = frozenset({"begin", "write_cid", "mark_created", "mark_removing", "finish"})


class QualificationRecordError(RuntimeError):
    """Qualification authority is absent, malformed, stale, or stored unsafely."""


def _digest(value: object) -> str:
    return canonical_digest(value)


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _canonical_authority_path(value: Path) -> Path:
    if not value.is_absolute():
        raise QualificationRecordError("sandbox home must be a canonical absolute path")
    supplied = value.expanduser()
    if (".." in supplied.parts
            or os.path.normpath(str(supplied)) != str(supplied)):
        raise QualificationRecordError("sandbox home must be a canonical absolute path")
    current = Path(supplied.anchor)
    for part in supplied.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise QualificationRecordError("sandbox home cannot be inspected safely") from exc
        if stat.S_ISLNK(info.st_mode):
            raise QualificationRecordError("sandbox home contains a symlink component")
    if supplied.resolve(strict=False) != supplied:
        raise QualificationRecordError("sandbox home is not canonical")
    return supplied


def sandbox_home(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    override = values.get(SANDBOX_HOME_ENV)
    selected = Path(override) if override else Path.home() / ".empirica-plugin" / "sandbox"
    return _canonical_authority_path(selected)


def _validated_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return docker.validate_policy(value)
    except (docker.DockerQualificationError, OSError, ValueError, TypeError) as exc:
        raise QualificationRecordError("sandbox policy/runtime identity is invalid") from exc


def current_profile(policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    selected = json.loads(json.dumps(docker.POLICY_TEMPLATE if policy is None else policy))
    selected.pop("runtime_identity", None)
    body = {"schema": PROFILE_SCHEMA, "id": PROFILE_ID, "policy": selected}
    return {**body, "profile_digest": _digest(body)}


def implementation_manifest() -> dict[str, Any]:
    files = []
    for relative in IMPLEMENTATION_SOURCE_PATHS:
        path = _EMPIRICA_ROOT / relative
        try:
            canonical = path.resolve(strict=True)
            if canonical.relative_to(_EMPIRICA_ROOT) != Path(relative):
                raise QualificationRecordError("implementation source path is not canonical")
            digest = _file_digest(canonical)
        except QualificationRecordError:
            raise
        except (OSError, ValueError) as exc:
            raise QualificationRecordError("implementation source cannot be read safely") from exc
        files.append({"path": relative, "sha256": digest})
    if [row["path"] for row in files] != sorted(set(IMPLEMENTATION_SOURCE_PATHS)):
        raise QualificationRecordError("implementation source manifest is not closed and sorted")
    body = {"files": files}
    return {**body, "implementation_digest": _digest(body)}


def _report_provenance(paths: Sequence[Path]) -> list[dict[str, str]]:
    rows = []
    for path in paths:
        canonical = Path(path).expanduser().resolve(strict=True)
        rows.append({"path": str(canonical), "sha256": _file_digest(canonical)})
    return rows


def build_record(
    result: Mapping[str, Any], *, report_paths: Sequence[Path] = (),
    implementation: Mapping[str, Any] | None = None,
    uid: int | None = None, user_home: Path | None = None,
) -> dict[str, Any]:
    """Build one immutable qualification instance; selected/incomplete results are refused."""
    if (result.get("requested_probe") != "all" or result.get("complete_qualification") is not True
            or result.get("passed") is not True or result.get("cleanup_verified") is not True
            or result.get("docker_backend_eligible") is not True
            or result.get("qualified_for_untrusted_code") is not True
            or result.get("environment_preflight_available") is not True
            or result.get("empirica_contract_compatible") is not True
            or result.get("fallback_eligible") is not False):
        raise QualificationRecordError("only a complete all-probe qualification can be installed")
    probes = result.get("probes")
    if (not isinstance(probes, list) or [row.get("name") for row in probes] != list(docker.MANDATORY_PROBES)
            or not all(row.get("passed") is True for row in probes)):
        raise QualificationRecordError("qualification probe coverage is incomplete or out of order")
    policy = result.get("policy")
    if not isinstance(policy, Mapping):
        raise QualificationRecordError("qualification has no effective policy")
    validated = _validated_policy(policy)
    if docker.policy_digest(validated) != result.get("policy_sha256"):
        raise QualificationRecordError("qualification policy digest mismatch")
    preflight = result.get("preflight")
    expected_preflight_keys = set(docker.DockerRuntimeIdentity.__dataclass_fields__)
    if (not isinstance(preflight, Mapping) or set(preflight) != expected_preflight_keys
            or preflight.get("available") is not True or preflight.get("reasons") not in ((), [])):
        raise QualificationRecordError("qualification preflight identity is incomplete or malformed")
    try:
        projected_runtime = docker.validate_runtime_identity(preflight)
    except (docker.DockerQualificationError, OSError, ValueError, TypeError) as exc:
        raise QualificationRecordError("qualification preflight runtime is outside the profile") from exc
    if projected_runtime != validated["runtime_identity"]:
        raise QualificationRecordError("qualification preflight and effective policy runtime differ")
    profile = current_profile(validated)
    runtime = validated["runtime_identity"]
    runtime_digest = _digest(runtime)
    try:
        environment_identity = environment.validate_environment_identity(
            result.get("environment_preflight"), require_available=True)
    except (environment.EnvironmentPreflightError, OSError, ValueError, TypeError) as exc:
        raise QualificationRecordError("qualification environment preflight is invalid") from exc
    environment_digest = _digest(environment_identity)
    impl = json.loads(json.dumps(implementation or implementation_manifest()))
    if set(impl) != IMPLEMENTATION_KEYS or impl["implementation_digest"] != _digest({"files": impl["files"]}):
        raise QualificationRecordError("implementation manifest digest mismatch")
    probe_rows = [
        {"id": row["name"], "outcome": "pass", "fact_digest": _digest(row["details"])}
        for row in probes
    ]
    qualification_body = {
        "complete": True,
        "mandatory_probes": probe_rows,
        "report_provenance": _report_provenance(report_paths),
    }
    qualification = {
        **qualification_body,
        "results_digest": _digest({"mandatory_probes": probe_rows}),
    }
    binding_material = {
        "schema": RECORD_SCHEMA,
        "qualification_protocol_revision": PROTOCOL_REVISION,
        "profile_digest": profile["profile_digest"],
        "runtime_digest": runtime_digest,
        "environment_digest": environment_digest,
        "implementation_digest": impl["implementation_digest"],
    }
    execution_binding_digest = _digest(binding_material)
    selected_uid = os.getuid() if uid is None else uid
    selected_home = (Path.home() if user_home is None else Path(user_home)).expanduser().resolve(strict=False)
    body = {
        "schema": RECORD_SCHEMA,
        "qualification_protocol_revision": PROTOCOL_REVISION,
        "subject": {"uid": selected_uid, "user_home_digest": _digest(str(selected_home))},
        "profile": profile,
        "implementation": impl,
        "runtime": runtime,
        "runtime_digest": runtime_digest,
        "environment": environment_identity,
        "environment_digest": environment_digest,
        "qualification": qualification,
        "execution_binding_digest": execution_binding_digest,
        "created_at": result.get("generated_at"),
    }
    if not isinstance(body["created_at"], str) or not body["created_at"]:
        raise QualificationRecordError("qualification generated_at is missing")
    record = {**body, "qualification_record_digest": _digest(body)}
    validate_record(record, verify_static=False)
    return record


def _closed(value: object, keys: set[str], where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise QualificationRecordError(f"{where} has missing or extra fields")
    return value


def validate_record(
    value: object, *, verify_static: bool = True,
    expected_profile: Mapping[str, Any] | None = None,
    expected_implementation: Mapping[str, Any] | None = None,
    expected_runtime: Mapping[str, Any] | None = None,
    expected_environment: Mapping[str, Any] | environment.EnvironmentIdentity | None = None,
    uid: int | None = None, user_home: Path | None = None,
) -> dict[str, Any]:
    record = dict(_closed(value, RECORD_KEYS, "qualification record"))
    if record["schema"] != RECORD_SCHEMA or record["qualification_protocol_revision"] != PROTOCOL_REVISION:
        raise QualificationRecordError("unknown qualification record schema/protocol revision")
    profile = dict(_closed(record["profile"], PROFILE_KEYS, "qualification profile"))
    profile_body = {key: profile[key] for key in ("schema", "id", "policy")}
    if (profile["schema"] != PROFILE_SCHEMA or profile["id"] != PROFILE_ID
            or profile["profile_digest"] != _digest(profile_body)):
        raise QualificationRecordError("qualification profile digest mismatch")
    _validated_policy({**profile["policy"], "runtime_identity": record["runtime"]})
    impl = dict(_closed(record["implementation"], IMPLEMENTATION_KEYS, "implementation manifest"))
    files = impl["files"]
    if (not isinstance(files, list) or not files
            or any(not isinstance(row, Mapping) or set(row) != {"path", "sha256"}
                   or not isinstance(row["path"], str) or not row["path"]
                   or Path(row["path"]).is_absolute() or ".." in Path(row["path"]).parts
                   or not _DIGEST.fullmatch(str(row["sha256"])) for row in files)
            or [row["path"] for row in files] != sorted({row["path"] for row in files})):
        raise QualificationRecordError("implementation file manifest is malformed")
    if impl["implementation_digest"] != _digest({"files": files}):
        raise QualificationRecordError("implementation digest mismatch")
    qualification = dict(_closed(record["qualification"], QUALIFICATION_KEYS, "qualification evidence"))
    probes = qualification["mandatory_probes"]
    if (qualification["complete"] is not True or not isinstance(probes, list)
            or [row.get("id") for row in probes] != list(docker.MANDATORY_PROBES)
            or any(set(row) != {"id", "outcome", "fact_digest"}
                   or row["outcome"] != "pass" or not _DIGEST.fullmatch(str(row["fact_digest"]))
                   for row in probes)
            or qualification["results_digest"] != _digest({"mandatory_probes": probes})):
        raise QualificationRecordError("qualification evidence is incomplete or malformed")
    provenance = qualification["report_provenance"]
    if (not isinstance(provenance, list)
            or any(not isinstance(row, Mapping) or set(row) != {"path", "sha256"}
                   or not isinstance(row["path"], str) or not Path(row["path"]).is_absolute()
                   or not _DIGEST.fullmatch(str(row["sha256"])) for row in provenance)):
        raise QualificationRecordError("report provenance is malformed")
    if record["runtime_digest"] != _digest(record["runtime"]):
        raise QualificationRecordError("runtime digest mismatch")
    try:
        checked_environment = environment.validate_environment_identity(
            record["environment"], require_available=True)
    except (environment.EnvironmentPreflightError, OSError, ValueError, TypeError) as exc:
        raise QualificationRecordError("qualification environment identity is invalid") from exc
    if record["environment_digest"] != _digest(checked_environment):
        raise QualificationRecordError("environment digest mismatch")
    binding_material = {
        "schema": record["schema"],
        "qualification_protocol_revision": record["qualification_protocol_revision"],
        "profile_digest": profile["profile_digest"],
        "runtime_digest": record["runtime_digest"],
        "environment_digest": record["environment_digest"],
        "implementation_digest": impl["implementation_digest"],
    }
    if record["execution_binding_digest"] != _digest(binding_material):
        raise QualificationRecordError("execution binding digest mismatch")
    body = {key: record[key] for key in RECORD_KEYS - {"qualification_record_digest"}}
    if record["qualification_record_digest"] != _digest(body):
        raise QualificationRecordError("qualification record digest mismatch")
    subject = record["subject"]
    selected_uid = os.getuid() if uid is None else uid
    selected_home = (Path.home() if user_home is None else Path(user_home)).expanduser().resolve(strict=False)
    if (not isinstance(subject, Mapping) or set(subject) != {"uid", "user_home_digest"}
            or subject["uid"] != selected_uid
            or subject["user_home_digest"] != _digest(str(selected_home))):
        raise QualificationRecordError("qualification subject does not match the current user")
    expected_profile = current_profile() if expected_profile is None else expected_profile
    expected_implementation = (implementation_manifest()
                               if expected_implementation is None else expected_implementation)
    if profile["profile_digest"] != expected_profile.get("profile_digest"):
        raise QualificationRecordError("current executable profile differs from qualification")
    if impl["implementation_digest"] != expected_implementation.get("implementation_digest"):
        raise QualificationRecordError("current sandbox implementation differs from qualification")
    if expected_runtime is not None and record["runtime_digest"] != _digest(expected_runtime):
        raise QualificationRecordError("current runtime differs from qualification")
    if verify_static:
        try:
            observed_environment = environment.validate_environment_identity(
                environment.inspect_environment() if expected_environment is None
                else expected_environment, require_available=True)
        except (environment.EnvironmentPreflightError, OSError, ValueError, TypeError) as exc:
            raise QualificationRecordError("current sandbox environment is unavailable") from exc
        if _digest(observed_environment) != record["environment_digest"]:
            raise QualificationRecordError("current sandbox environment differs from qualification")
        runtime = record["runtime"]
        if (runtime.get("host_os"), runtime.get("host_release"), runtime.get("host_arch")) != (
                platform.system(), platform.release(), platform.machine()):
            raise QualificationRecordError("current host differs from qualification")
        cli = Path(runtime.get("docker_cli", ""))
        try:
            canonical_cli = cli.expanduser().resolve(strict=True)
        except OSError as exc:
            raise QualificationRecordError("qualified Docker CLI is unavailable") from exc
        if str(canonical_cli) != runtime.get("docker_cli") or _file_digest(canonical_cli) != runtime.get("docker_cli_sha256"):
            raise QualificationRecordError("qualified Docker CLI bytes changed")
    return json.loads(json.dumps(record))


def _safe_directory(path: Path, *, create: bool) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise QualificationRecordError(f"sandbox directory is unavailable: {path.name}")
        try:
            path.mkdir(parents=True, mode=_MODE_DIR)
            os.chmod(path, _MODE_DIR)
            info = path.lstat()
        except OSError as exc:
            raise QualificationRecordError(
                f"sandbox directory cannot be created safely: {path.name}") from exc
    except OSError as exc:
        raise QualificationRecordError(f"sandbox directory is unavailable: {path.name}") from exc
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != _MODE_DIR):
        raise QualificationRecordError(f"sandbox directory has unsafe ownership/mode: {path.name}")


def _safe_file(path: Path, *, cap: int = MAX_CONTROL_FILE_BYTES) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise QualificationRecordError(f"sandbox file is unavailable: {path.name}") from exc
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != _MODE_FILE):
        raise QualificationRecordError(f"sandbox file has unsafe ownership/mode: {path.name}")
    if info.st_size > cap:
        raise QualificationRecordError("sandbox file exceeds its byte cap")
    try:
        raw = fsio.read_bytes(path)
    except (OSError, fsio.SymlinkRefused) as exc:
        raise QualificationRecordError("sandbox file cannot be read safely") from exc
    if raw is None or len(raw) > cap:
        raise QualificationRecordError("sandbox file is absent or over its byte cap")
    return raw


def validate_journal(value: object, *, directory_name: str) -> dict[str, Any]:
    journal = dict(_closed(value, JOURNAL_KEYS, "recovery journal"))
    if journal["schema"] != JOURNAL_SCHEMA:
        raise QualificationRecordError("unknown recovery journal schema")
    operation_id, run_id, role = (journal[key] for key in ("operation_id", "run_id", "role"))
    if (not isinstance(operation_id, str) or operation_id != directory_name
            or OPERATION_ID.fullmatch(operation_id) is None
            or not isinstance(run_id, str) or docker.SAFE_LABEL_VALUE.fullmatch(run_id) is None
            or not isinstance(role, str) or docker.SAFE_LABEL_VALUE.fullmatch(role) is None):
        raise QualificationRecordError("recovery journal operation/run/role identity is malformed")
    if journal["lifecycle_state"] not in JOURNAL_STATES:
        raise QualificationRecordError("unknown recovery journal lifecycle state")
    for key in ("execution_binding_digest", "qualification_record_digest", "policy_digest"):
        if not _DIGEST.fullmatch(str(journal[key])):
            raise QualificationRecordError(f"recovery journal {key} is malformed")
    if (not isinstance(journal["name"], str)
            or not journal["name"].startswith(docker.NAME_PREFIX)
            or docker.SAFE_LABEL_VALUE.fullmatch(journal["name"]) is None
            or journal["cidfile_name"] != "container.cid"):
        raise QualificationRecordError("recovery journal name/cidfile spelling is malformed")
    image = journal["expected_image"]
    if (not isinstance(image, Mapping) or set(image) != {"repo_digest", "config_id"}
            or image["repo_digest"] != docker.IMAGE_REPO_DIGEST
            or not _DIGEST.fullmatch(str(image["config_id"]))):
        raise QualificationRecordError("recovery journal image identity is malformed")
    labels = journal["labels"]
    expected_label_keys = {
        docker.OWNER_LABEL, docker.SCHEMA_LABEL, docker.POLICY_LABEL,
        docker.RUN_LABEL, docker.ROLE_LABEL,
    }
    if (not isinstance(labels, Mapping) or set(labels) != expected_label_keys
            or labels[docker.OWNER_LABEL] != docker.OWNER_VALUE
            or labels[docker.SCHEMA_LABEL] != str(docker.SCHEMA_VERSION)
            or labels[docker.POLICY_LABEL] != journal["policy_digest"]
            or labels[docker.RUN_LABEL] != run_id or labels[docker.ROLE_LABEL] != role):
        raise QualificationRecordError("recovery journal labels do not match its authority fields")
    full_id = journal["full_id"]
    if journal["lifecycle_state"] == "intent":
        if full_id is not None:
            raise QualificationRecordError("intent journal must not claim a container ID")
    elif not isinstance(full_id, str) or docker.CONTAINER_ID.fullmatch(full_id) is None:
        raise QualificationRecordError("created/removing journal requires one full container ID")
    return json.loads(json.dumps(journal))


def _bind_journal_to_record(
    journal: Mapping[str, Any], authority: Mapping[str, Any], *, cidfile_full_id: str | None,
) -> dict[str, Any]:
    effective_policy = _validated_policy({
        **authority["profile"]["policy"], "runtime_identity": authority["runtime"]})
    if (journal["qualification_record_digest"] != authority["qualification_record_digest"]
            or journal["execution_binding_digest"] != authority["execution_binding_digest"]
            or journal["policy_digest"] != docker.policy_digest(effective_policy)):
        raise QualificationRecordError("recovery journal does not match its immutable authority")
    runtime = authority["runtime"]
    if journal["expected_image"] != {
            "repo_digest": runtime["image_repo_digest"],
            "config_id": runtime["image_config_id"],
    }:
        raise QualificationRecordError("recovery journal image differs from immutable authority")
    state = journal["lifecycle_state"]
    if state in {"created", "removing"}:
        if cidfile_full_id is None or cidfile_full_id != journal["full_id"]:
            raise QualificationRecordError("recovery journal and cidfile container IDs differ")
    elif cidfile_full_id is not None and journal["full_id"] is not None:
        raise QualificationRecordError("intent crash-window journal has inconsistent IDs")
    return {**json.loads(json.dumps(journal)), "cidfile_full_id": cidfile_full_id}


def plan_journal_transition(
    row: Mapping[str, Any], event: str, full_id: str | None = None,
) -> dict[str, Any] | None:
    """Purely admit one closed lifecycle event and return its next persisted document."""
    if event not in JOURNAL_EVENTS:
        raise QualificationRecordError("unknown recovery journal transition")
    document = {key: row[key] for key in JOURNAL_KEYS}
    state = document["lifecycle_state"]
    cid = row.get("cidfile_full_id")
    if event == "begin":
        if state != "intent" or document["full_id"] is not None or cid is not None:
            raise QualificationRecordError("journal begin requires a new ID-less intent")
        return document
    if event == "write_cid":
        if state != "intent" or document["full_id"] is not None or cid is not None:
            raise QualificationRecordError("container cid can only follow ID-less intent")
        if not isinstance(full_id, str) or docker.CONTAINER_ID.fullmatch(full_id) is None:
            raise QualificationRecordError("container cid is malformed")
        return document
    if event == "mark_created":
        if (state != "intent" or document["full_id"] is not None
                or cid is None or cid != full_id):
            raise QualificationRecordError("created transition requires exact intent cid")
        document.update(lifecycle_state="created", full_id=full_id)
        return document
    if event == "mark_removing":
        if state == "removing" and document["full_id"] is not None and cid == document["full_id"]:
            return document
        if state != "created" or document["full_id"] is None or cid != document["full_id"]:
            raise QualificationRecordError("removing transition requires exact created identity")
        document["lifecycle_state"] = "removing"
        return document
    if full_id is None:
        if state != "intent" or document["full_id"] is not None or cid is not None:
            raise QualificationRecordError("no-container finish requires ID-less intent")
        return None
    if (state != "removing" or document["full_id"] != full_id or cid != full_id):
        raise QualificationRecordError("container finish requires exact removing identity")
    document.update(lifecycle_state="intent", full_id=None)
    return document


class QualificationStore:
    def __init__(
        self, home: Path | None = None, *,
        environment_inspector: Any = None,
    ) -> None:
        selected = sandbox_home() if home is None else _canonical_authority_path(Path(home))
        self.home = selected
        self.qualification = self.home / "qualification"
        self.records = self.qualification / "records"
        self.runtime = self.home / "runtime"
        self.staging = self.home / "staging"
        self.runtime_lock = self.home / "runtime.lock"
        self.current = self.qualification / "current.json"
        self.environment_inspector = (environment_inspector
                                      if environment_inspector is not None
                                      else environment.inspect_environment)

    def _check_runtime_lock(self) -> None:
        if _safe_file(self.runtime_lock, cap=1) != b"":
            raise QualificationRecordError("sandbox runtime lock is not an empty control file")
        try:
            info = self.runtime_lock.lstat()
        except OSError as exc:
            raise QualificationRecordError("sandbox runtime lock identity is unavailable") from exc
        if info.st_nlink != 1:
            raise QualificationRecordError("sandbox runtime lock must not be hard-linked")

    def prepare(self) -> None:
        for path in (self.home, self.qualification, self.records, self.runtime, self.staging):
            _safe_directory(path, create=True)
        try:
            self.runtime_lock.lstat()
        except FileNotFoundError:
            fsio.atomic_write(self.runtime_lock, b"")
        self._check_runtime_lock()

    def check_execution_scaffolding(self) -> None:
        """Validate operator-created execution roots without creating or repairing them."""
        for path in (self.home, self.runtime, self.staging):
            _safe_directory(path, create=False)
        self._check_runtime_lock()

    def _check_tree(self) -> None:
        for path in (self.home, self.qualification, self.records):
            _safe_directory(path, create=False)

    def publish(self, record: Mapping[str, Any]) -> Path:
        try:
            self.prepare()
            checked = validate_record(
                record, expected_environment=self.environment_inspector())
            digest = checked["qualification_record_digest"]
            target = self.records / f"{digest.removeprefix('sha256:')}.json"
            raw = fsio.dump_json(checked)
            with fsio.lock(target):
                try:
                    target.lstat()
                except FileNotFoundError:
                    existing = None
                else:
                    existing = _safe_file(target)
                if existing is None:
                    fsio.atomic_write(target, raw)
                elif existing != raw:
                    raise QualificationRecordError("immutable qualification record collision")
            _safe_file(target)
            pointer = {
                "schema": POINTER_SCHEMA,
                "execution_binding_digest": checked["execution_binding_digest"],
                "qualification_record_digest": digest,
            }
            fsio.atomic_write(self.current, fsio.dump_json(pointer))
            _safe_file(self.current, cap=MAX_POINTER_BYTES)
            return target
        except QualificationRecordError:
            raise
        except (OSError, fsio.SymlinkRefused) as exc:
            raise QualificationRecordError("qualification authority could not be published safely") from exc

    def load_record(
        self, qualification_record_digest: str, *,
        expected_environment: Mapping[str, Any] | environment.EnvironmentIdentity | None = None,
        expected_runtime: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._check_tree()
        if not _DIGEST.fullmatch(str(qualification_record_digest)):
            raise QualificationRecordError("qualification record digest is malformed")
        path = self.records / f"{qualification_record_digest.removeprefix('sha256:')}.json"
        try:
            value = json.loads(_safe_file(path).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QualificationRecordError("qualification record JSON is malformed") from exc
        return validate_record(
            value,
            expected_environment=(self.environment_inspector()
                                  if expected_environment is None else expected_environment),
            expected_runtime=expected_runtime,
        )

    def load_current(
        self, *,
        expected_environment: Mapping[str, Any] | environment.EnvironmentIdentity | None = None,
        expected_runtime: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._check_tree()
        try:
            pointer = json.loads(_safe_file(self.current, cap=MAX_POINTER_BYTES).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QualificationRecordError("qualification pointer JSON is malformed") from exc
        pointer = dict(_closed(pointer, POINTER_KEYS, "qualification pointer"))
        if pointer["schema"] != POINTER_SCHEMA:
            raise QualificationRecordError("unknown qualification pointer schema")
        record = self.load_record(
            pointer["qualification_record_digest"],
            expected_environment=expected_environment,
            expected_runtime=expected_runtime,
        )
        if record["execution_binding_digest"] != pointer["execution_binding_digest"]:
            raise QualificationRecordError("qualification pointer binding mismatch")
        return record

    def read_journals(self) -> list[dict[str, Any]]:
        _safe_directory(self.home, create=False)
        _safe_directory(self.runtime, create=False)
        try:
            roots = sorted(self.runtime.iterdir())
        except OSError as exc:
            raise QualificationRecordError("recovery journal directory cannot be enumerated") from exc
        if len(roots) > MAX_JOURNALS:
            raise QualificationRecordError("recovery journal count exceeds its cap")
        total = 0
        rows = []
        for root in roots:
            _safe_directory(root, create=False)
            try:
                children = list(root.iterdir())
            except OSError as exc:
                raise QualificationRecordError("recovery journal cannot be enumerated") from exc
            names = {child.name for child in children}
            if (len(children) != len(names) or "intent.json" not in names
                    or not names.issubset({"intent.json", "container.cid"})):
                raise QualificationRecordError("recovery journal contains an unknown file")
            raw = _safe_file(root / "intent.json", cap=MAX_JOURNAL_BYTES)
            total += len(raw)
            if total > MAX_JOURNAL_TOTAL_BYTES:
                raise QualificationRecordError("recovery journal bytes exceed their cap")
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise QualificationRecordError("recovery journal JSON is malformed") from exc
            journal = validate_journal(value, directory_name=root.name)
            cidfile_full_id = None
            if "container.cid" in names:
                cidfile = _safe_file(root / "container.cid", cap=MAX_CIDFILE_BYTES)
                total += len(cidfile)
                if total > MAX_JOURNAL_TOTAL_BYTES:
                    raise QualificationRecordError("recovery journal bytes exceed their cap")
                try:
                    cidfile_text = cidfile.decode("ascii")
                except UnicodeDecodeError as exc:
                    raise QualificationRecordError("recovery cidfile is not ASCII") from exc
                if re.fullmatch(r"[0-9a-f]{64}\n?", cidfile_text) is None:
                    raise QualificationRecordError("recovery cidfile does not contain one full ID")
                cidfile_full_id = cidfile_text.rstrip("\n")
            authority = self.load_record(journal["qualification_record_digest"])
            rows.append(_bind_journal_to_record(
                journal, authority, cidfile_full_id=cidfile_full_id))
        return rows

    def _operation_journal(self, operation_id: str) -> dict[str, Any]:
        rows = [row for row in self.read_journals() if row["operation_id"] == operation_id]
        if len(rows) != 1:
            raise QualificationRecordError("journal transition requires one exact operation")
        return rows[0]

    def _persist_journal_document(self, operation_id: str, document: Mapping[str, Any]) -> None:
        raw = fsio.dump_json(document)
        path = self.runtime / operation_id / "intent.json"
        previous = path.lstat().st_size
        total = sum(child.lstat().st_size
                    for root in self.runtime.iterdir() for child in root.iterdir())
        if len(raw) > MAX_JOURNAL_BYTES or total - previous + len(raw) > MAX_JOURNAL_TOTAL_BYTES:
            raise QualificationRecordError("recovery journal bytes exceed their cap")
        fsio.atomic_write(path, raw)
        _safe_file(path, cap=MAX_JOURNAL_BYTES)

    def begin_operation(self, intent: Mapping[str, Any]) -> None:
        """Durably create one exact intent journal in operator-created scaffolding."""
        self.check_execution_scaffolding()
        journal = validate_journal(intent, directory_name=str(intent.get("operation_id", "")))
        journal = plan_journal_transition(journal, "begin")
        if journal is None:
            raise QualificationRecordError("journal begin produced no document")
        authority = self.load_record(journal["qualification_record_digest"])
        _bind_journal_to_record(journal, authority, cidfile_full_id=None)
        journals = self.read_journals()
        if len(journals) >= MAX_JOURNALS:
            raise QualificationRecordError("recovery journal count exceeds its cap")
        raw = fsio.dump_json(journal)
        existing_bytes = 0
        for item in self.runtime.iterdir():
            for child in item.iterdir():
                existing_bytes += child.lstat().st_size
        if len(raw) > MAX_JOURNAL_BYTES or existing_bytes + len(raw) > MAX_JOURNAL_TOTAL_BYTES:
            raise QualificationRecordError("recovery journal bytes exceed their cap")
        root = self.runtime / journal["operation_id"]
        try:
            root.mkdir(mode=_MODE_DIR)
        except OSError as exc:
            raise QualificationRecordError("operation journal cannot be allocated exclusively") from exc
        try:
            fsio.atomic_write(root / "intent.json", raw)
            _safe_directory(root, create=False)
            _safe_file(root / "intent.json", cap=MAX_JOURNAL_BYTES)
            fsio._fsync_dir(self.runtime)
        except BaseException:
            try:
                (root / "intent.json").unlink(missing_ok=True)
                root.rmdir()
                fsio._fsync_dir(self.runtime)
            except OSError:
                pass
            raise

    def write_cid(self, operation_id: str, full_id: str) -> None:
        row = self._operation_journal(operation_id)
        plan_journal_transition(row, "write_cid", full_id)
        path = self.runtime / operation_id / "container.cid"
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise QualificationRecordError("cidfile transition refuses an existing file")
        raw_cid = (full_id + "\n").encode("ascii")
        total = sum(child.lstat().st_size
                    for root in self.runtime.iterdir() for child in root.iterdir())
        if total + len(raw_cid) > MAX_JOURNAL_TOTAL_BYTES:
            raise QualificationRecordError("recovery journal bytes exceed their cap")
        fsio.atomic_write(path, raw_cid)
        _safe_file(path, cap=MAX_CIDFILE_BYTES)

    def mark_created(self, operation_id: str, full_id: str) -> None:
        row = self._operation_journal(operation_id)
        document = plan_journal_transition(row, "mark_created", full_id)
        if document is None:
            raise QualificationRecordError("created transition produced no journal")
        self._persist_journal_document(operation_id, document)

    def mark_removing(self, operation_id: str) -> None:
        row = self._operation_journal(operation_id)
        document = plan_journal_transition(row, "mark_removing")
        if row["lifecycle_state"] == "removing":
            return
        if document is None:
            raise QualificationRecordError("removing transition produced no journal")
        self._persist_journal_document(operation_id, document)

    def finish_operation(self, operation_id: str, full_id: str | None) -> None:
        """Delete a journal last, after its caller proved container absence and staging cleanup."""
        row = self._operation_journal(operation_id)
        tombstone = plan_journal_transition(row, "finish", full_id)
        try:
            self.staging.joinpath(operation_id).lstat()
        except FileNotFoundError:
            pass
        else:
            raise QualificationRecordError("journal deletion requires prior staging cleanup")
        root = self.runtime / operation_id
        if tombstone is not None:
            self._persist_journal_document(operation_id, tombstone)
        if row["cidfile_full_id"] is not None:
            (root / "container.cid").unlink()
            fsio._fsync_dir(root)
        (root / "intent.json").unlink()
        fsio._fsync_dir(root)
        root.rmdir()
        fsio._fsync_dir(self.runtime)
