"""Durable one-container `/2` crash journal and exact destructive-ID authority."""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from adapters.state import fsio
from core.freshness import validate_digest256

from . import common, planner

JOURNAL_SCHEMA = "siffran.empirica.generic-docker-operation/2"
ROOT_NAME = "generic-runtime-v2"
MAX_JOURNALS = 32
MAX_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 128
_FULL_ID = re.compile(r"^[0-9a-f]{64}$")
_STATES = (
    "intent", "staged", "network_intent", "network_created", "container_intent",
    "container_created", "static_verified", "attaching", "terminal_observed",
    "cleanup", "removed",
)
_RANK = {name: index for index, name in enumerate(_STATES)}
_FIELDS = {"schema", "operation_id", "authority", "operation_digest", "operation",
           "state", "events", "resource_ids", "diagnostics"}


class GenericJournalError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GenericJournal:
    directory: Path
    document: dict[str, Any]
    operation: "JournalOperation"

    @property
    def authority(self) -> dict[str, Any]:
        return self.document["authority"]

    @property
    def container_id(self) -> str | None:
        return self.document["resource_ids"]["container"]

    @property
    def network_id(self) -> str | None:
        return self.document["resource_ids"]["network"]

    @property
    def cidfile(self) -> Path:
        return self.directory / "container.cid"

    def has_event(self, kind: str) -> bool:
        return any(event["kind"] == kind for event in self.document["events"])


def _safe_dir(path: Path, *, create: bool = False) -> None:
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise GenericJournalError("journal directory cannot be created") from exc
    try:
        info = path.lstat()
    except OSError as exc:
        raise GenericJournalError("journal directory is unavailable") from exc
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise GenericJournalError("journal directory identity is unsafe")


def _read(path: Path, *, allow_empty: bool = False, allow_missing: bool = False,
          maximum: int = MAX_BYTES) -> bytes | None:
    try:
        before = path.lstat()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        if allow_missing:
            return None
        raise GenericJournalError("journal control file cannot be opened safely")
    except OSError as exc:
        raise GenericJournalError("journal control file cannot be opened safely") from exc
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid()
                or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_nlink != 1
                or opened.st_size > maximum
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise GenericJournalError("journal control file identity is unsafe")
        data = b""
        while len(data) <= maximum:
            chunk = os.read(fd, min(64 * 1024, maximum + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        after = os.fstat(fd)
        if (after.st_dev, after.st_ino, after.st_size) != (
                opened.st_dev, opened.st_ino, opened.st_size):
            raise GenericJournalError("journal control file changed while read")
    finally:
        os.close(fd)
    if len(data) > maximum or (not data and not allow_empty):
        raise GenericJournalError("journal control content is absent or over bound")
    return data


def _discard_atomic_temp(directory: Path) -> None:
    temporary = directory / ".journal.json.tmp"
    try:
        before = temporary.lstat()
    except FileNotFoundError:
        return
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
            or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size > MAX_BYTES):
        raise GenericJournalError("journal atomic temporary identity is unsafe")
    fd = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        opened = os.fstat(fd)
    finally:
        os.close(fd)
    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
        raise GenericJournalError("journal atomic temporary was replaced")
    temporary.unlink()
    fsio._fsync_dir(directory)


def _atomic(path: Path, data: bytes) -> None:
    if len(data) > MAX_BYTES:
        raise GenericJournalError("journal exceeds its byte bound")
    temporary = path.parent / ("." + path.name + ".tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                     | getattr(os, "O_CLOEXEC", 0), 0o600)
        try:
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:offset + 64 * 1024])
                if written <= 0:
                    raise GenericJournalError("journal write made no progress")
                offset += written
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, path)
        fsio._fsync_dir(path.parent)
        if _read(path) != data:
            raise GenericJournalError("journal publication differs")
    except GenericJournalError:
        raise
    except OSError as exc:
        raise GenericJournalError("journal cannot be published atomically") from exc


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_LABEL_BYTES = 64 * 1024
_DIAGNOSTIC_FIELDS = frozenset({"kind", "returncode", "stderr_bytes", "stderr_sha256",
                                "stderr_tail"})


@dataclass(frozen=True, slots=True)
class JournalResource:
    """The persisted correlation facts of one owned resource; never destructive authority."""
    name: str
    labels: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class JournalOperation:
    """The closed cleanup view recovery needs, read from the persisted operation data only.

    It never asks the current planner to reproduce the operation, so an unfinished journal stays
    recoverable after a plugin, planner, or implementation change.
    """
    operation_id: str
    container: JournalResource
    network: JournalResource | None


def _resource(value: object, operation_id: str, kind: str) -> JournalResource:
    if type(value) is not dict:
        raise GenericJournalError(f"journal {kind} correlation is malformed")
    name, labels = value.get("name"), value.get("labels")
    if (type(name) is not str or _NAME.fullmatch(name) is None or type(labels) is not dict
            or not labels or len(labels) > 64
            or any(type(key) is not str or type(item) is not str or not key
                   for key, item in labels.items())
            or len(json.dumps(labels).encode("utf-8")) > _LABEL_BYTES
            or labels.get("io.siffran.empirica.operation") != operation_id
            or labels.get("io.siffran.empirica.resource") != kind):
        raise GenericJournalError(f"journal {kind} correlation is malformed")
    return JournalResource(name, tuple(sorted(labels.items())))


def _operation_view(document: Mapping[str, Any]) -> JournalOperation:
    operation = document.get("operation")
    if type(operation) is not dict or operation.get("operation_id") != document.get("operation_id"):
        raise GenericJournalError("journal operation data is not self-consistent")
    try:
        # A diagnostic identity only: recovery never recompiles or re-hashes the operation, so a
        # journal written by an older planner remains recoverable.
        validate_digest256(document.get("operation_digest"))
    except (TypeError, ValueError) as exc:
        raise GenericJournalError("journal operation digest is malformed") from exc
    network = operation.get("network")
    return JournalOperation(
        document["operation_id"],
        _resource(operation.get("container"), document["operation_id"], "container"),
        None if network is None else _resource(network, document["operation_id"], "network"))


# A Docker-written cidfile is one 64-hex ID; a longer readable file is malformed, not authority.
CIDFILE_BYTES = 4096
DAEMON_IDENTITY = ("endpoint", "server_os", "server_architecture")


def daemon_identity(value: object) -> dict[str, str]:
    """Which daemon owns the recorded IDs. Capability facts are not identity: passive
    verification and postflight enforce them for new execution, never for recovery."""
    if not isinstance(value, Mapping) or any(
            type(value.get(key)) is not str or not value[key] for key in DAEMON_IDENTITY):
        raise GenericJournalError("journal Docker daemon identity is malformed")
    return {key: value[key] for key in DAEMON_IDENTITY}


def validate_document(value: object, directory_name: str) -> tuple[dict[str, Any], JournalOperation]:
    if type(value) is not dict or set(value) != _FIELDS or value.get("schema") != JOURNAL_SCHEMA \
            or value.get("operation_id") != directory_name \
            or common.OPERATION_ID.fullmatch(directory_name) is None:
        raise GenericJournalError("journal shape or identity is invalid")
    operation = _operation_view(value)
    if value.get("authority") != daemon_identity(value.get("authority")):
        raise GenericJournalError("journal Docker daemon identity is malformed")
    state = value.get("state")
    events = value.get("events")
    ids = value.get("resource_ids")
    diagnostics = value.get("diagnostics")
    if state not in _RANK or type(events) is not list or len(events) > MAX_EVENTS \
            or type(ids) is not dict or set(ids) != {"container", "network"} \
            or type(diagnostics) is not list or len(diagnostics) > MAX_EVENTS:
        raise GenericJournalError("journal state, IDs, or diagnostics are malformed")
    if operation.network is None and ids["network"] is not None:
        raise GenericJournalError("isolated journal carries a network ID")
    for name, full in ids.items():
        if full is not None and (type(full) is not str or _FULL_ID.fullmatch(full) is None):
            raise GenericJournalError("journal carries a malformed destructive ID")
    prior = 0
    for index, event in enumerate(events):
        if (type(event) is not dict or set(event) != {"seq", "state", "kind"}
                or event["seq"] != index or event["state"] not in _RANK
                or _RANK[event["state"]] < prior or type(event["kind"]) is not str
                or not event["kind"] or len(event["kind"]) > 128):
            raise GenericJournalError("journal event is malformed or moves backward")
        prior = _RANK[event["state"]]
    if events and events[-1]["state"] != state or not events and state != "intent":
        raise GenericJournalError("journal state differs from events")
    for diagnostic in diagnostics:
        if (type(diagnostic) is not dict
                or set(diagnostic) not in (_DIAGNOSTIC_FIELDS,
                                           _DIAGNOSTIC_FIELDS | {"realization_digest"})
                or type(diagnostic["kind"]) is not str
                or type(diagnostic["returncode"]) is not int
                or isinstance(diagnostic["returncode"], bool)
                or type(diagnostic["stderr_bytes"]) is not int
                or type(diagnostic["stderr_tail"]) is not str
                or len(diagnostic["stderr_tail"].encode("utf-8")) > 4096):
            raise GenericJournalError("journal diagnostic is malformed")
        validate_digest256(diagnostic["stderr_sha256"])
        if "realization_digest" in diagnostic:
            validate_digest256(diagnostic["realization_digest"])
    checked = json.loads(json.dumps(value))
    return checked, operation


class GenericJournalStore:
    def __init__(self, execution_store: common.ExecutionStore) -> None:
        self.execution_store = execution_store
        self.root = execution_store.home / ROOT_NAME

    def prepare(self) -> None:
        _safe_dir(self.execution_store.home)
        _safe_dir(self.root, create=True)
        fsio._fsync_dir(self.execution_store.home)

    def check(self) -> None:
        self.execution_store.check_execution_scaffolding()
        _safe_dir(self.root)

    def _directory(self, operation_id: str) -> Path:
        if type(operation_id) is not str or common.OPERATION_ID.fullmatch(operation_id) is None:
            raise GenericJournalError("journal operation ID is malformed")
        return self.root / operation_id

    def begin(self, operation: planner.ResolvedDockerOperation, *,
              authority: Mapping[str, Any]) -> GenericJournal:
        """Persist the closed operation data and the identity of the daemon it runs on."""
        self.check()
        if type(operation) is not planner.ResolvedDockerOperation:
            raise GenericJournalError("journal requires exact operation")
        directory = self._directory(operation.operation_id)
        document = {
            "schema": JOURNAL_SCHEMA, "operation_id": operation.operation_id,
            "authority": daemon_identity(authority),
            "operation_digest": operation.digest, "operation": operation.to_data(),
            "state": "intent", "events": [],
            "resource_ids": {"container": None, "network": None}, "diagnostics": [],
        }
        validate_document(document, operation.operation_id)
        try:
            directory.mkdir(mode=0o700)
            fsio._fsync_dir(directory)
        except OSError as exc:
            raise GenericJournalError("journal allocation failed or already exists") from exc
        _atomic(directory / "journal.json", fsio.dump_json(document))
        return self.load(operation.operation_id)

    def load(self, operation_id: str) -> GenericJournal:
        directory = self._directory(operation_id)
        _safe_dir(directory)
        if (directory / "journal.json").exists():
            _discard_atomic_temp(directory)
        try:
            value = json.loads(_read(directory / "journal.json").decode("utf-8"),
                               object_pairs_hook=_pairs,
                               parse_constant=lambda item: (_ for _ in ()).throw(
                                   GenericJournalError("journal non-finite JSON")))
        except GenericJournalError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GenericJournalError("journal JSON is malformed") from exc
        document, operation = validate_document(value, operation_id)
        children = tuple(sorted(item.name for item in directory.iterdir()))
        if children not in (("journal.json",), ("container.cid", "journal.json")):
            raise GenericJournalError("journal contains foreign residue")
        if "container.cid" in children:
            _read(directory / "container.cid", allow_empty=True, maximum=CIDFILE_BYTES)
        return GenericJournal(directory, document, operation)

    def operation_ids(self) -> tuple[str, ...]:
        """Bounded names of unfinished journals; loading each is separate so a failure keeps its ID."""
        self.check()
        rows = tuple(sorted(item.name for item in self.root.iterdir()))
        if len(rows) > MAX_JOURNALS:
            raise GenericJournalError("journal count exceeds bound")
        for name in rows:
            if common.OPERATION_ID.fullmatch(name) is None:
                raise GenericJournalError("journal root contains foreign residue")
        return rows

    def scan(self) -> tuple[GenericJournal, ...]:
        result = []
        total = 0
        for name in self.operation_ids():
            row = self.load(name)
            total += sum(item.lstat().st_size for item in row.directory.iterdir())
            if total > MAX_TOTAL_BYTES:
                raise GenericJournalError("journal aggregate bytes exceed bound")
            result.append(row)
        return tuple(result)

    def transition(self, operation_id: str, state: str, kind: str,
                   diagnostic: Mapping[str, Any] | None = None) -> GenericJournal:
        row = self.load(operation_id)
        if state not in _RANK or _RANK[state] < _RANK[row.document["state"]]:
            raise GenericJournalError("journal transition moves backward")
        document = row.document
        document["events"].append({"seq": len(document["events"]), "state": state, "kind": kind})
        document["state"] = state
        if diagnostic is not None:
            document["diagnostics"].append(dict(diagnostic))
        validate_document(document, operation_id)
        _atomic(row.directory / "journal.json", fsio.dump_json(document))
        return self.load(operation_id)

    def record_full_id(self, operation_id: str, resource: str, full_id: str) -> GenericJournal:
        if resource not in {"container", "network"} or _FULL_ID.fullmatch(full_id or "") is None:
            raise GenericJournalError("destructive authority ID is malformed")
        row = self.load(operation_id)
        expected_intent = resource + "_intent"
        if not row.has_event(expected_intent) or row.document["resource_ids"][resource] is not None:
            raise GenericJournalError("destructive authority lacks one durable create intent")
        row.document["resource_ids"][resource] = full_id
        _atomic(row.directory / "journal.json", fsio.dump_json(row.document))
        return self.load(operation_id)

    def read_cidfile(self, operation_id: str) -> str | None:
        """Return the Docker-written full ID, or None when the cidfile is missing, empty, or not
        exactly one full ID (the exact-name candidate set then decides).

        None is never an absence proof: docker/cli creates the file before `ContainerCreate` and
        writes the ID only afterwards, so an empty file can coexist with a created container. An
        unsafe file identity still fails closed.
        """
        raw = _read(self._directory(operation_id) / "container.cid", allow_empty=True,
                    allow_missing=True, maximum=CIDFILE_BYTES)
        text = (raw or b"").decode("ascii", errors="replace")
        return text if _FULL_ID.fullmatch(text) else None

    def finish(self, operation_id: str) -> None:
        row = self.load(operation_id)
        if row.document["state"] != "removed":
            raise GenericJournalError("journal cannot finish before proved removal")
        for path in (row.directory / "container.cid", row.directory / "journal.json"):
            try:
                info = path.lstat()
            except FileNotFoundError:
                if path.name == "container.cid":
                    continue
                raise GenericJournalError("journal disappeared before finish")
            if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or info.st_uid != os.getuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise GenericJournalError("journal changed before finish")
            path.unlink()
        row.directory.rmdir()
        fsio._fsync_dir(self.root)


def _pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in rows:
        if key in value:
            raise GenericJournalError("journal JSON contains duplicate key")
        value[key] = item
    return value
