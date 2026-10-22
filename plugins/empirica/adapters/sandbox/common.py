"""Shared generic Docker authority scaffolding, runtime lock, and implementation identity.

Installed pre-generic qualification files are outside the generic namespace and are never read,
migrated, translated, repaired, or deleted here.
"""
from __future__ import annotations

import fcntl
import hashlib
import math
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Mapping

from adapters.state import fsio
from core.freshness import canonical_digest

SANDBOX_HOME_ENV = "EMPIRICA_SANDBOX_HOME"
MODE_FILE = 0o600
MODE_DIR = 0o700
MAX_CONTROL_FILE_BYTES = 2 * 1024 * 1024
OPERATION_ID = re.compile(r"^o-[0-9a-f]{32}$")
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
EMPIRICA_ROOT = Path(__file__).resolve().parents[2]

IMPLEMENTATION_SOURCE_PATHS = tuple(sorted({
    ".mcp.json",
    "adapters/author_view.py",
    "adapters/bridge.py",
    "adapters/claude/transport.py",
    "adapters/codex/transport.py",
    "adapters/execution.py",
    "adapters/invocation.py",
    "adapters/mcp_server.py",
    "adapters/pi/bridge.py",
    "adapters/pi/src/index.ts",
    "adapters/pi/src/process-transport.ts",
    "adapters/pi/src/stdio-transport.ts",
    "adapters/pi/src/translate.ts",
    "adapters/public_tools.py",
    "adapters/sandbox/common.py",
    "adapters/sandbox/environment.py",
    "adapters/sandbox/generic_admission.py",
    "adapters/sandbox/generic_authority.py",
    "adapters/sandbox/generic_effects.py",
    "adapters/sandbox/generic_execution.py",
    "adapters/sandbox/generic_harness.py",
    "adapters/sandbox/generic_journal.py",
    "adapters/sandbox/generic_qualification.py",
    "adapters/sandbox/generic_staging.py",
    "adapters/sandbox/planner.py",
    "adapters/sandbox/process.py",
    "adapters/state/fsio.py",
    "adapters/state/located.py",
    "adapters/state/repository.py",
    "application/generic_spike.py",
    "application/host_runtime.py",
    "application/history_records.py",
    "application/observation.py",
    "application/ports.py",
    "application/protocol.py",
    "application/run_state.py",
    "application/snapshot.py",
    "application/transaction.py",
    "application/v2.py",
    "core/canonical.py",
    "core/evaluation.py",
    "core/freshness.py",
    "core/observer.py",
    "core/projection.py",
    "core/run.py",
    "core/spike_observer.py",
    "core/spike_plan.py",
    "core/spike_plan_draft.py",
    "vendor/contracts/empirica/v2/host-profiles.json",
    "vendor/contracts/empirica/v2/public-contract.json",
    "vendor/contracts/empirica/v2/public-contract.schema.json",
    "vendor/contracts/empirica/v2/public-tools.json",
    "vendor/contracts/empirica/v2/request.schema.json",
    "vendor/contracts/empirica/v2/response.schema.json",
    "vendor/contracts/empirica/v2/state.schema.json",
}))


class GenericScaffoldingError(RuntimeError):
    """Generic authority scaffolding, implementation identity, or runtime lock is unsafe."""


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def canonical_authority_path(value: Path) -> Path:
    supplied = value.expanduser()
    if any(character in str(supplied) for character in (",", '"')):
        raise GenericScaffoldingError(
            "sandbox path cannot contain comma or quote characters used by Docker mount syntax")
    if (not supplied.is_absolute() or ".." in supplied.parts
            or os.path.normpath(str(supplied)) != str(supplied)):
        raise GenericScaffoldingError("sandbox home must be a canonical absolute path")
    current = Path(supplied.anchor)
    for part in supplied.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise GenericScaffoldingError("sandbox home cannot be inspected safely") from exc
        if stat.S_ISLNK(info.st_mode):
            raise GenericScaffoldingError("sandbox home contains a symlink component")
    if supplied.resolve(strict=False) != supplied:
        raise GenericScaffoldingError("sandbox home is not canonical")
    return supplied


def sandbox_home(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    override = values.get(SANDBOX_HOME_ENV)
    selected = Path(override) if override else Path.home() / ".empirica-plugin" / "sandbox"
    return canonical_authority_path(selected)


def safe_directory(path: Path, *, create: bool) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise GenericScaffoldingError(f"sandbox directory is unavailable: {path.name}")
        try:
            path.mkdir(parents=True, mode=MODE_DIR)
            os.chmod(path, MODE_DIR)
            info = path.lstat()
        except OSError as exc:
            raise GenericScaffoldingError(
                f"sandbox directory cannot be created safely: {path.name}") from exc
    except OSError as exc:
        raise GenericScaffoldingError(f"sandbox directory is unavailable: {path.name}") from exc
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != MODE_DIR):
        raise GenericScaffoldingError(f"sandbox directory has unsafe ownership/mode: {path.name}")


def safe_file(path: Path, *, cap: int = MAX_CONTROL_FILE_BYTES) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise GenericScaffoldingError(f"sandbox file is unavailable: {path.name}") from exc
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != MODE_FILE or info.st_nlink != 1):
        raise GenericScaffoldingError(f"sandbox file has unsafe ownership/mode: {path.name}")
    if info.st_size > cap:
        raise GenericScaffoldingError("sandbox file exceeds its byte cap")
    try:
        raw = fsio.read_bytes(path)
    except (OSError, fsio.SymlinkRefused) as exc:
        raise GenericScaffoldingError("sandbox file cannot be read safely") from exc
    if raw is None or len(raw) > cap:
        raise GenericScaffoldingError("sandbox file is absent or over its byte cap")
    return raw


def implementation_manifest() -> dict[str, object]:
    files = []
    for relative in IMPLEMENTATION_SOURCE_PATHS:
        path = EMPIRICA_ROOT / relative
        try:
            canonical = path.resolve(strict=True)
            if canonical.relative_to(EMPIRICA_ROOT) != Path(relative):
                raise GenericScaffoldingError("implementation source path is not canonical")
            file_digest = _file_digest(canonical)
        except GenericScaffoldingError:
            raise
        except (OSError, ValueError) as exc:
            raise GenericScaffoldingError("implementation source cannot be read safely") from exc
        files.append({"path": relative, "sha256": file_digest})
    body = {"files": files}
    return {**body, "implementation_digest": canonical_digest(body)}


class ExecutionStore:
    """Only generic staging, journal, and process-lock roots; no qualification record loader."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = sandbox_home() if home is None else canonical_authority_path(Path(home))
        self.runtime = self.home / "runtime"
        self.staging = self.home / "staging"
        self.runtime_lock = self.home / "runtime.lock"

    def _check_runtime_lock(self) -> None:
        if safe_file(self.runtime_lock, cap=1) != b"":
            raise GenericScaffoldingError("sandbox runtime lock is not an empty control file")

    def prepare_execution_scaffolding(self) -> None:
        for path in (self.home, self.runtime, self.staging):
            safe_directory(path, create=True)
        try:
            self.runtime_lock.lstat()
        except FileNotFoundError:
            fsio.atomic_write(self.runtime_lock, b"")
        self._check_runtime_lock()

    def check_execution_scaffolding(self) -> None:
        for path in (self.home, self.runtime, self.staging):
            safe_directory(path, create=False)
        self._check_runtime_lock()


def _remaining(deadline: float, *, clock: Callable[[], float]) -> float:
    if (type(deadline) not in {int, float} or isinstance(deadline, bool)
            or not math.isfinite(deadline)):
        raise GenericScaffoldingError("runtime lock deadline is malformed")
    value = deadline - clock()
    if value <= 0:
        raise GenericScaffoldingError("runtime lock deadline expired")
    return value


@contextmanager
def runtime_lock(
    store: ExecutionStore, deadline: float, *, clock: Callable[[], float] = time.monotonic,
) -> Iterator[None]:
    """Acquire the operator-created generic process lock within one monotonic deadline."""
    if type(store) is not ExecutionStore:
        raise GenericScaffoldingError("runtime lock requires an exact generic execution store")
    store.check_execution_scaffolding()
    _remaining(deadline, clock=clock)
    try:
        fd = os.open(store.runtime_lock, os.O_RDWR | NOFOLLOW | CLOEXEC)
    except OSError as exc:
        raise GenericScaffoldingError("runtime lock cannot be opened safely") from exc
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != MODE_FILE or info.st_size != 0
                or info.st_nlink != 1):
            raise GenericScaffoldingError("runtime lock identity is unsafe")
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.01, _remaining(deadline, clock=clock)))
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
