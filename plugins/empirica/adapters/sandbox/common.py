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
from typing import Any, Callable, Iterator, Mapping

from adapters.state import fsio
from core.freshness import canonical_digest

SANDBOX_HOME_ENV = "EMPIRICA_SANDBOX_HOME"
MODE_FILE = 0o600
MODE_DIR = 0o700
MAX_CONTROL_FILE_BYTES = 2 * 1024 * 1024
OPERATION_ID = re.compile(r"^o-[0-9a-f]{32}$")
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
OPENDIR = getattr(os, "O_DIRECTORY", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
# Opening a scaffold name asks the kernel for a directory directly (O_DIRECTORY): a
# planted non-directory — a regular file, a device, or a named pipe — fails the open
# with ENOTDIR immediately instead of parking the caller in the kernel's blocked-open
# wait (re-review 2, blocking 3); O_NONBLOCK is never needed for that refusal.
DIRECTORY_OPEN = os.O_RDONLY | NOFOLLOW | OPENDIR | CLOEXEC
EMPIRICA_ROOT = Path(__file__).resolve().parents[2]

# The implementation manifest is exactly the closure of these roots, by the rule in doc/testing.md ("The generic
# authority implementation manifest"); `make empirica-manifest-check` derives it and requires the list below to equal it.
IMPLEMENTATION_ROOTS = (
    ".mcp.json",
    "adapters/bridge.py",
    "adapters/claude/transport.py",
    "adapters/codex/transport.py",
    "adapters/mcp_server.py",
    "adapters/pi/bridge.py",
    "adapters/pi/src/index.ts",
    "adapters/sandbox/generic_qualification.py",
    "adapters/sandbox/probe_image.py",
    "adapters/sandbox/run_qualification.py",
    "vendor/contracts/empirica/v2",
    "vendor/contracts/empirica-docker-spike/v1",
)

IMPLEMENTATION_SOURCE_PATHS = tuple(sorted({
    ".mcp.json",
    "adapters/__init__.py",
    "adapters/audit.py",
    "adapters/audit_protocol.py",
    "adapters/author_view.py",
    "adapters/bridge.py",
    "adapters/claude/__init__.py",
    "adapters/claude/completion.py",
    "adapters/claude/correlation.py",
    "adapters/claude/fail_direction.py",
    "adapters/claude/invocation.py",
    "adapters/claude/preflight.py",
    "adapters/claude/restore.py",
    "adapters/claude/route.py",
    "adapters/claude/run_start.py",
    "adapters/claude/selector.py",
    "adapters/claude/spawn.py",
    "adapters/claude/transport.py",
    "adapters/codex/__init__.py",
    "adapters/codex/audit.py",
    "adapters/codex/correlation.py",
    "adapters/codex/lifecycle.py",
    "adapters/codex/transport.py",
    "adapters/confirmation.py",
    "adapters/execution.py",
    "adapters/git/__init__.py",
    "adapters/git/artifact_repo.py",
    "adapters/governance.py",
    "adapters/identity.py",
    "adapters/invocation.py",
    "adapters/mcp_server.py",
    "adapters/pi/bridge.py",
    "adapters/pi/src/audit-identity.ts",
    "adapters/pi/src/audit-launch.ts",
    "adapters/pi/src/audit-retrieval.ts",
    "adapters/pi/src/audit.ts",
    "adapters/pi/src/author-view.ts",
    "adapters/pi/src/contract.ts",
    "adapters/pi/src/dialog-view.ts",
    "adapters/pi/src/evidence-ui.ts",
    "adapters/pi/src/governance-ui.ts",
    "adapters/pi/src/guard.ts",
    "adapters/pi/src/host-profile.ts",
    "adapters/pi/src/host-runtime.ts",
    "adapters/pi/src/index.ts",
    "adapters/pi/src/observer-ui.ts",
    "adapters/pi/src/owner-refusal.ts",
    "adapters/pi/src/pi-types.ts",
    "adapters/pi/src/preflight-seam.ts",
    "adapters/pi/src/private-transport.ts",
    "adapters/pi/src/process-transport.ts",
    "adapters/pi/src/public-tools.ts",
    "adapters/pi/src/runtime-owner.ts",
    "adapters/pi/src/stdio-transport.ts",
    "adapters/pi/src/subagent-inventory.ts",
    "adapters/pi/src/translate.ts",
    "adapters/pi/src/turn-end.ts",
    "adapters/observer/__init__.py",
    "adapters/observer/discovery.py",
    "adapters/public_tools.py",
    "adapters/sandbox/__init__.py",
    "adapters/sandbox/common.py",
    "adapters/sandbox/generic_admission.py",
    "adapters/sandbox/generic_authority.py",
    "adapters/sandbox/generic_effects.py",
    "adapters/sandbox/generic_execution.py",
    "adapters/sandbox/generic_harness.py",
    "adapters/sandbox/generic_journal.py",
    "adapters/sandbox/generic_qualification.py",
    "adapters/sandbox/probe_image.py",
    "adapters/sandbox/run_qualification.py",
    "adapters/sandbox/generic_staging.py",
    "adapters/sandbox/planner.py",
    "adapters/sandbox/process.py",
    "adapters/state/__init__.py",
    "adapters/state/fsio.py",
    "adapters/state/hitl.py",
    "adapters/state/identity.py",
    "adapters/state/located.py",
    "adapters/state/locations.py",
    "adapters/state/repository.py",
    "adapters/state/timeline.py",
    "application/__init__.py",
    "application/generic_spike.py",
    "application/governance.py",
    "application/history_records.py",
    "application/host_runtime.py",
    "application/location.py",
    "application/observation.py",
    "application/ports.py",
    "application/protocol.py",
    "application/run_state.py",
    "application/snapshot.py",
    "application/transaction.py",
    "application/v2.py",
    "core/__init__.py",
    "core/canonical.py",
    "core/citations.py",
    "core/confirmation.py",
    "core/contract_bounds.py",
    "core/context_selector.py",
    "core/environment.py",
    "core/evaluation.py",
    "core/freshness.py",
    "core/governance.py",
    "core/hitl.py",
    "core/ports.py",
    "core/projection.py",
    "core/records.py",
    "core/run.py",
    "core/spike_plan.py",
    "core/spike_plan_draft.py",
    "vendor/contracts/empirica/v2/host-profiles.json",
    "vendor/contracts/empirica/v2/public-contract.json",
    "vendor/contracts/empirica/v2/public-contract.schema.json",
    "vendor/contracts/empirica/v2/public-tools.json",
    "vendor/contracts/empirica/v2/request.schema.json",
    "vendor/contracts/empirica/v2/response.schema.json",
    "vendor/contracts/empirica/v2/state.schema.json",
    "vendor/contracts/empirica-docker-spike/v1/qualification-record.schema.json",
    "vendor/contracts/empirica-docker-spike/v1/support-contract.json",
}))


class GenericScaffoldingError(RuntimeError):
    """Generic authority scaffolding, implementation identity, or runtime lock is unsafe."""


# The digest of the release this process is actually executing, frozen once per process at the
# real loaded-release boundary (service composition). A disk snapshot taken later is not the
# loaded release: the still-running process keeps this value even after the tree changes under
# it. A failed first snapshot latches the loaded release unavailable for the rest of the
# process (``_loaded_release_failed``): the executing modules can never be proved to match a
# later disk digest, so no later composition retries and adopts one. Unfreezing is never an
# operation; tests reset these globals directly as the only seam.
_loaded_release: str | None = None
_loaded_release_failed: str | None = None


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


def _verified_identity(descriptor: int, name: str, *, final: bool, created: bool) -> None:
    """Verify one opened scaffold component's identity from ``fstat`` alone.

    No permission is ever changed on a scaffold: a component is required to *be*
    exactly the closed owner-only directory, and anything else — including a
    same-owner replacement swapped in after this call's own ``mkdir`` — is refused as
    it stands. The ``created`` mark comes from an earlier ``mkdir`` by name and does
    not establish that the opened inode is the one created, so a created component is
    validated exactly like a found one; the only difference is that a component this
    walk had to create may not stand at a permissive mode even mid-path (the attacker's
    replacement lands on that branch), while a pre-existing intermediate is only
    required to be a directory (re-review 3, blocking 1).
    """
    try:
        info = os.fstat(descriptor)
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
                or (final or created) and info.st_uid != os.getuid()):
            raise GenericScaffoldingError(
                f"sandbox directory has unsafe identity: {name}")
        if (final or created) and stat.S_IMODE(info.st_mode) != MODE_DIR:
            # the closed mode can only be refused here, never fixed: this code neither
            # chmods the entry nor removes it, whatever created it or when
            raise GenericScaffoldingError(
                f"sandbox directory has unsafe ownership/mode: {name}")
    except OSError as exc:
        raise GenericScaffoldingError(
            f"sandbox directory cannot be initialized safely: {name}") from exc


def _scaffold_component(parent: int, name: str, *, create: bool, final: bool) -> int:
    """Open one scaffold component relative to its opened, verified parent.

    An existing component is opened directory-only and never modified. A missing
    component is created by ``mkdir(name, 0o700, dir_fd=parent)`` — the process mask is
    never touched, and can only remove bits from the requested mode — then opened the
    same way and required to stand at exactly the closed identity: type, owner, and
    mode 0700 on the opened descriptor. Anything else, including a same-owner
    replacement substituted after a successful ``mkdir`` or a mask that stripped owner
    bits, is refused with the scaffolding-unsafe error: never fixed, and never removed
    by name either (re-review 3, blocking 1; re-review 2, blocking 1).
    """
    def opened() -> int:
        try:
            return os.open(name, DIRECTORY_OPEN, dir_fd=parent)
        except OSError as exc:
            # after this call's own mkdir, the entry can be unopenable because the mask
            # stripped the owner-read bit, or because a swap stands at the name: refused
            # either way, without one permission change and without removing the entry
            # by name (re-review 3, blocking 1)
            raise GenericScaffoldingError(
                "sandbox directory cannot be opened after its creation: " + name) from exc

    def adopted(descriptor: int) -> int:
        try:
            _verified_identity(descriptor, name, final=final, created=True)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    try:
        descriptor = os.open(name, DIRECTORY_OPEN, dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise GenericScaffoldingError(f"sandbox directory is unavailable: {name}") from None
    except OSError as exc:
        raise GenericScaffoldingError(
            f"sandbox directory cannot be opened safely: {name}") from exc
    else:
        try:
            _verified_identity(descriptor, name, final=final, created=False)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor
    try:
        os.mkdir(name, MODE_DIR, dir_fd=parent)
    except FileExistsError:
        # lost the create race: the winner is validated below exactly like this call's
        # own creation — and never altered (re-review 1, blocking 3)
        pass
    except OSError as exc:
        raise GenericScaffoldingError(
            f"sandbox directory cannot be created safely: {name}") from exc
    # won or lost the create after a genuinely missing lookup: the opened entry is
    # required to be the closed identity as it stands, whatever stands there
    return adopted(opened())


def safe_directory(path: Path, *, create: bool) -> None:
    """Validate one scaffolding directory's identity, component by component.

    The process umask is never read or changed, and no permission is ever changed on a
    scaffold: a fresh component is created relative to its opened, verified parent at
    mode 0700 (the mask can only remove bits), opened with ``O_DIRECTORY|O_NOFOLLOW`",
    and required to stand at exactly type, owner and mode 0700 on that descriptor — a
    component that a mask left at another mode, a replacement, or a planted impostor
    is refused exactly as it stands, never fixed and never removed by name. A name
    that is not a directory (a regular file, a device, a named pipe) is refused by the
    directory-only open without blocking (re-review 2, blocking 3).
    """
    supplied = Path(path)
    if (not supplied.is_absolute() or ".." in supplied.parts
            or os.path.normpath(str(supplied)) != str(supplied)
            or not supplied.parts[1:]):
        raise GenericScaffoldingError("sandbox directory path is not canonical and absolute")
    parent = os.open(supplied.anchor or os.sep, DIRECTORY_OPEN)
    try:
        names = supplied.parts[1:]
        for index, name in enumerate(names):
            final = index == len(names) - 1
            descriptor = _scaffold_component(parent, name, create=create, final=final)
            os.close(parent)
            parent = descriptor
    finally:
        os.close(parent)


def safe_file(path: Path, *, cap: int = MAX_CONTROL_FILE_BYTES) -> bytes:
    """Read one bounded control file through a single no-follow, non-blocking descriptor.

    ``fstat`` on that one descriptor owns the checks: type regular, owner, closed
    mode, exactly one link, size inside the cap. The bounded bytes are read from the
    same descriptor, the read is confirmed to have watched a stable inode and size,
    and only when the name still identifies that validated inode does the call
    return: anything substituted between the checks and the read — Sol's hard link
    carrying the very same bytes — is neither read nor reported, and the substitute
    keeps its own metadata untouched (re-review 3, blocking 2).
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | NOFOLLOW | NONBLOCK | CLOEXEC)
    except FileNotFoundError as exc:
        raise GenericScaffoldingError(f"sandbox file is unavailable: {path.name}") from exc
    except OSError as exc:
        raise GenericScaffoldingError("sandbox file cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (stat.S_ISLNK(opened.st_mode) or not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.getuid()
                or stat.S_IMODE(opened.st_mode) != MODE_FILE or opened.st_nlink != 1
                or opened.st_size > cap):
            raise GenericScaffoldingError(f"sandbox file has unsafe ownership/mode: {path.name}")
        data = b""
        while len(data) <= cap:
            chunk = os.read(descriptor, min(64 * 1024, cap + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        after = os.fstat(descriptor)
        if ((after.st_dev, after.st_ino, after.st_size)
                != (opened.st_dev, opened.st_ino, opened.st_size)):
            raise GenericScaffoldingError("sandbox file changed while read")
        named = path.lstat()
        if (stat.S_ISLNK(named.st_mode)
                or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)):
            # the name no longer identifies the file just validated and read: a
            # substitute stands at it, or it was moved away — refused before the bytes
            # are reported, with the substitute never opened through the name
            raise GenericScaffoldingError(
                f"sandbox file no longer names the validated file: {path.name}")
    except OSError as exc:
        raise GenericScaffoldingError("sandbox file cannot be read safely") from exc
    finally:
        os.close(descriptor)
    if len(data) > cap:
        raise GenericScaffoldingError("sandbox file is over its byte cap")
    return data


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


def freeze_loaded_release(
        implementation_inspector: Callable[[], Mapping[str, Any]] | None = None,
) -> str:
    """Bind and return this process's loaded implementation digest, assessing it exactly once.

    The first caller (service composition for the shipped adapters, or the first resolver
    constructed without an injected release) fixes the value for the whole process. A malformed
    manifest at that boundary fails closed with :class:`GenericScaffoldingError` and latches
    the loaded release unavailable for the life of the process: this process's executing
    modules can never be proved to match a later disk digest, so no retry may adopt one.
    Both latched messages are fixed and path-free constants; the original failure stays
    chained as the cause, never as the message.
    """
    global _loaded_release, _loaded_release_failed
    if _loaded_release is not None:
        return _loaded_release
    if _loaded_release_failed is not None:
        raise GenericScaffoldingError(_loaded_release_failed)
    try:
        manifest = (implementation_inspector or implementation_manifest)()
        digest = (manifest.get("implementation_digest")
                  if isinstance(manifest, Mapping) else None)
    except Exception as exc:
        # The latch is closed: an arbitrary snapshot failure may carry filesystem paths, so
        # only the fixed constant is latched and raised; the cause stays exc.
        _loaded_release_failed = "loaded implementation release is unavailable"
        raise GenericScaffoldingError(_loaded_release_failed) from exc
    if type(digest) is not str or not digest:
        _loaded_release_failed = "loaded implementation release is malformed"
        raise GenericScaffoldingError(_loaded_release_failed)
    _loaded_release = digest
    return _loaded_release


def loaded_release_digest() -> str | None:
    """The per-process frozen loaded release, or ``None`` before the first freeze."""
    return _loaded_release


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
        self._create_runtime_lock_exclusively()
        self._check_runtime_lock()

    def _create_runtime_lock_exclusively(self) -> None:
        """Initialize the shared runtime lock without ever replacing an existing inode.

        First use creates the lock with ``O_CREAT|O_EXCL`` and then every attempt opens the
        same name, so concurrent first users race only the exclusive create: the loser sees
        the winner's inode and serializes on ``flock``. Atomic replacement is forbidden here
        on purpose — its rename could unlink a lock file that is already held, letting both
        attempts enter recovery and the probes (E5 review, blocking finding 3).
        """
        try:
            self.runtime_lock.lstat()
            return  # an existing lock inode is the shared lock; never replace it
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise GenericScaffoldingError(
                "sandbox runtime lock cannot be inspected safely") from exc
        try:
            fd = os.open(self.runtime_lock,
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY | NOFOLLOW | CLOEXEC, MODE_FILE)
        except FileExistsError:
            return  # lost the exclusive-create race: the winner's inode is the lock
        except OSError as exc:
            raise GenericScaffoldingError(
                "sandbox runtime lock cannot be created safely") from exc
        try:
            os.fchmod(fd, MODE_FILE)
            os.fsync(fd)
        except OSError as exc:
            raise GenericScaffoldingError(
                "sandbox runtime lock cannot be initialized safely") from exc
        finally:
            os.close(fd)
        # Best-effort control-file durability only: a lost lock dirent is recreated on the
        # next preparation; the publication path is where strict sync is required.
        fsio._fsync_dir(self.home)

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
