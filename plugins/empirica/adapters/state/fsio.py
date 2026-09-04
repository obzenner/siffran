"""Hardened, machine-local file IO for the global-state adapter (ADR-31).

One place owns the security- and durability-critical mechanics so the repository logic above it
reads as plain read-modify-write. Every property here is load-bearing for ADR-31's confirmation
tests:

* **0600 lock/temp files** — control metadata is machine-local and single-user; nothing here is
  world- or group-readable.
* **``O_NOFOLLOW`` on the lock and the target read; ``islink`` refusal on the write** — a symlink
  planted at a predictable path is refused, never followed, so an attacker cannot redirect a read
  or a write to a file of their choosing.
* **``mkstemp`` + ``fsync`` + atomic ``rename``** — a fresh unique 0600 temp file (never a
  predictable ``.tmp`` an attacker could pre-plant), flushed to disk, then renamed onto the target.
  On POSIX the rename is atomic, so a crash leaves either the old file or the new one, never a torn
  write. The parent directory is fsynced after the rename so the new dirent survives a crash too.
* **``flock`` serialises writers across processes** — the read-modify-write in a compare-and-set is
  guarded by an exclusive OS lock on a per-target ``.lock`` file, so two processes racing a CAS
  serialise rather than lose an update.

This is a sibling of ``hooks/atomicio.py`` by design, not by omission: that module serves the legacy
``.claude`` scratch layout and is loaded by path (no package imports); the adapter is a separate
package with its own dependency boundary, so it carries its own hardened IO rather than reaching
across into the hooks tree.
"""
from __future__ import annotations

import errno
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl  # POSIX only
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    _HAVE_FCNTL = False

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_MODE = 0o600


class SymlinkRefused(OSError):
    """A read or write target was a symlink. Refused, never followed (ADR-31 symlink refusal).

    Distinct from absence and from corruption: absence is "nothing is here" and corruption is
    "something undecodable is here", both ordinary values a caller handles. A symlink at a control
    path is a hostile condition, so it is raised rather than folded into a value.
    """


@contextmanager
def lock(path: Path):
    """Hold an exclusive OS lock for the duration of the block, keyed on a per-target ``.lock`` file.

    Opened ``O_CREAT|O_RDWR|O_NOFOLLOW`` at mode 0600, so a symlink pre-planted at the lock path is
    refused. Best-effort where ``fcntl`` is absent (Windows) — a degraded no-op lock, not a crash.
    Every writer takes this lock, so the read-modify-write cycles of concurrent compare-and-sets
    serialise against each other instead of racing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | _O_NOFOLLOW | _O_CLOEXEC, _MODE)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise SymlinkRefused(f"refusing symlinked lock path: {lock_path}") from exc
        raise
    try:
        if _HAVE_FCNTL:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if _HAVE_FCNTL:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read_bytes(path: Path) -> bytes | None:
    """The file's bytes, ``None`` if it does not exist, refusing a symlinked target.

    ``O_NOFOLLOW`` makes the kernel reject the open with ``ELOOP`` if the final component is a
    symlink; that becomes :class:`SymlinkRefused`. A genuinely missing file returns ``None`` so the
    caller can report absence as a value (never an exception) — the distinction ADR-31 requires
    between absent and corrupt is drawn one layer up, on the returned bytes.
    """
    try:
        fd = os.open(str(path), os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise SymlinkRefused(f"refusing symlinked read target: {path}") from exc
        raise
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a fresh 0600 temp file, fsync, then atomic rename.

    Refuses to write when ``path`` is itself a symlink: the rename would otherwise replace the link
    (harmless) but callers rely on the pre-write check to fail closed on a hostile target. The temp
    file is created ``O_CREAT|O_EXCL`` by ``mkstemp`` with a unique name, so uncoordinated writers
    never share a temp path and no pre-planted ``.tmp`` symlink is followed.
    """
    if path.is_symlink():
        raise SymlinkRefused(f"refusing symlinked write target: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp.", suffix=".json")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, _MODE)
        tmp.replace(path)  # atomic on POSIX
        _fsync_dir(path.parent)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _fsync_dir(directory: Path) -> None:
    """fsync a directory so a freshly renamed entry survives a crash. Best-effort: some platforms
    disallow opening a directory for fsync, and there the rename's own durability is what remains."""
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY | _O_CLOEXEC)
    except OSError:  # pragma: no cover - platform dependent
        return
    try:
        os.fsync(dir_fd)
    except OSError:  # pragma: no cover - platform dependent
        pass
    finally:
        os.close(dir_fd)


def dump_json(value: object) -> bytes:
    """Canonical UTF-8 JSON bytes for an operational document.

    ``sort_keys`` makes the byte serialisation a deterministic function of the value, which is what
    lets the content-hash revision be stable and comparable across processes. ``allow_nan=False``
    refuses non-finite constants so a document can never carry ``Infinity``/``NaN``.
    """
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    return text.encode("utf-8")
