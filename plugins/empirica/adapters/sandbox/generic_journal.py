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
from application.generic_spike import GenericQualifiedBackend
from core import spike_plan
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
_FIELDS = {"schema", "operation_id", "compiler", "operation_digest", "operation",
           "state", "events", "resource_ids", "diagnostics"}


class GenericJournalError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GenericJournal:
    directory: Path
    document: dict[str, Any]
    operation: planner.ResolvedDockerOperation

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


def _backend(value: object) -> GenericQualifiedBackend:
    fields = {"kind", "backend", "profile_id", "qualification_record_digest",
              "execution_binding_digest"}
    if type(value) is not dict or set(value) != fields or value.get("kind") != "qualified":
        raise GenericJournalError("journal backend is malformed")
    try:
        return GenericQualifiedBackend(
            value["backend"], value["profile_id"], value["qualification_record_digest"],
            value["execution_binding_digest"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GenericJournalError("journal backend is malformed") from exc


def _reconstruct(document: Mapping[str, Any]) -> planner.ResolvedDockerOperation:
    compiler = document.get("compiler")
    fields = {"request", "backend", "run_id", "staging_token"}
    if type(compiler) is not dict or set(compiler) != fields:
        raise GenericJournalError("journal compiler input is not closed")
    try:
        request = spike_plan.sealed_request_from_data(compiler["request"])
        backend = _backend(compiler["backend"])
        operation = planner.compile_docker_operation(
            request, backend, compiler["run_id"], document["operation_id"],
            compiler["staging_token"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GenericJournalError("journal compiler input cannot reconstruct") from exc
    if (operation.digest != document.get("operation_digest")
            or operation.to_data() != document.get("operation")):
        raise GenericJournalError("journal operation differs from reconstruction")
    return operation


def validate_document(value: object, directory_name: str) -> tuple[dict[str, Any], planner.ResolvedDockerOperation]:
    if type(value) is not dict or set(value) != _FIELDS or value.get("schema") != JOURNAL_SCHEMA \
            or value.get("operation_id") != directory_name \
            or common.OPERATION_ID.fullmatch(directory_name) is None:
        raise GenericJournalError("journal shape or identity is invalid")
    operation = _reconstruct(value)
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
                or set(diagnostic) != {"kind", "returncode", "stderr_bytes", "stderr_sha256",
                                      "stderr_tail"}
                or type(diagnostic["kind"]) is not str
                or type(diagnostic["returncode"]) is not int
                or isinstance(diagnostic["returncode"], bool)
                or type(diagnostic["stderr_bytes"]) is not int
                or type(diagnostic["stderr_tail"]) is not str
                or len(diagnostic["stderr_tail"].encode("utf-8")) > 4096):
            raise GenericJournalError("journal diagnostic is malformed")
        validate_digest256(diagnostic["stderr_sha256"])
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

    def begin(self, operation: planner.ResolvedDockerOperation) -> GenericJournal:
        self.check()
        if type(operation) is not planner.ResolvedDockerOperation:
            raise GenericJournalError("journal requires exact operation")
        directory = self._directory(operation.operation_id)
        try:
            directory.mkdir(mode=0o700)
            fsio._fsync_dir(directory)
        except OSError as exc:
            raise GenericJournalError("journal allocation failed or already exists") from exc
        document = {
            "schema": JOURNAL_SCHEMA, "operation_id": operation.operation_id,
            "compiler": {"request": operation.request.to_data(),
                         "backend": operation.backend.to_data(), "run_id": operation.run_id,
                         "staging_token": operation.staging_token},
            "operation_digest": operation.digest, "operation": operation.to_data(),
            "state": "intent", "events": [],
            "resource_ids": {"container": None, "network": None}, "diagnostics": [],
        }
        validate_document(document, operation.operation_id)
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
            _read(directory / "container.cid", allow_empty=True, maximum=64)
        return GenericJournal(directory, document, operation)

    def scan(self) -> tuple[GenericJournal, ...]:
        self.check()
        rows = tuple(sorted(self.root.iterdir()))
        if len(rows) > MAX_JOURNALS:
            raise GenericJournalError("journal count exceeds bound")
        result = []
        total = 0
        for path in rows:
            if common.OPERATION_ID.fullmatch(path.name) is None:
                raise GenericJournalError("journal root contains foreign residue")
            row = self.load(path.name)
            total += sum(item.lstat().st_size for item in path.iterdir())
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
        raw = _read(self._directory(operation_id) / "container.cid", allow_empty=True,
                    allow_missing=True, maximum=64)
        if not raw:
            return None
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise GenericJournalError("cidfile is not ASCII") from exc
        if _FULL_ID.fullmatch(text) is None:
            raise GenericJournalError("cidfile does not contain one exact full ID")
        return text

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


def diagnostic(kind: str, result: Any) -> dict[str, Any]:
    tail = result.stderr.tail
    encoded = tail.encode("utf-8")
    if len(encoded) > 4096:
        tail = encoded[-4096:].decode("utf-8", errors="replace")
    return {"kind": kind, "returncode": result.returncode,
            "stderr_bytes": result.stderr.bytes_seen,
            "stderr_sha256": result.stderr.sha256, "stderr_tail": tail}


def _pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in rows:
        if key in value:
            raise GenericJournalError("journal JSON contains duplicate key")
        value[key] = item
    return value
