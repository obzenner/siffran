"""The write-once HITL document sidecar beside each run generation (ADR-0072, the Write step).

    <home>/projects/<project_id>/runs/<run_id>/gen-<generation>/hitl/<sha256-hex>.json

Every document a human decision binds to (``empirica.hitl/1``, built by ``core/hitl.py``) is
written here **exactly once**, at the name of its own content digest (the ``document_digest`` of
ADR-0072, without the ``sha256:`` prefix):

* **write-once, atomic** — the bytes go to a fresh unique ``O_CREAT|O_EXCL`` temp file (``mkstemp``,
  never a predictable name), are flushed with ``fsync``, set to mode ``0444``, and are then
  published with :func:`os.link`, the no-replace primitive: ``link`` fails with ``FileExistsError``
  when the name exists, so no path here can ever overwrite or replace an existing document (the
  general state writes use temp + atomic rename, ``fsio.atomic_write``, which *may* overwrite — a
  different tool for a different contract). The temp is unlinked and the directory fsynced after the
  link. A crash can leave only a whole temp file, never a partial or torn final document.
* **idempotent on identical bytes** — a digest names one content, so writing the same bytes again
  returns the same ``(path, document_digest)`` without touching the file. A file at a digest name
  holding anything else fails closed (:class:`DocumentMismatch`); a symlink at any writable path is
  refused, never followed (:class:`fsio.SymlinkRefused`, ADR-31's rule — hostile targets are loud,
  not values).
* **read verifies** — :meth:`HitlDocuments.read` re-reads no-follow, requires a regular file, and
  returns the bytes only when their digest is the requested one; absence is ``None``, tampering is
  :class:`DocumentMismatch`.

Nothing here is wired into a runtime path yet: this module is imported by its regression suite only,
so the implementation manifest stays unchanged until the bridge presents documents (ADR-0072's
Present/Show steps, a later PR).
"""
from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

from core import hitl as core_hitl
from core.records import RunKey

from . import fsio
from .repository import FilesystemRunRepository, empirica_home

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_PREFIX = len("sha256:")
#: The mode of every published document: readable by all, writable by none (ADR-0072).
_MODE = 0o444


class DocumentMismatch(OSError):
    """The bytes at a document's digest name are not the bytes that name claims.

    A digest names one content: anything else at that path — different bytes, a non-regular file —
    is corruption of the write-once contract, refused loudly, never repaired or replaced. A symlink
    stays the IO layer's distinct hostile condition (:class:`fsio.SymlinkRefused`).
    """


def _name_of(document_digest: str) -> str:
    """The filename for ``document_digest``: its 64 lowercase hex characters."""
    if isinstance(document_digest, str) and _HEX64.match(document_digest[_PREFIX:]):
        return document_digest[_PREFIX:]
    raise ValueError(f"not a document_digest: {document_digest!r}")


class HitlDocuments:
    """The sidecar store under ``EMPIRICA_HOME``, one ``hitl`` directory per run generation,
    beside the ``run.json`` and the timeline (ADR-0070's beside-the-record location)."""

    def __init__(self, home: Path | None = None) -> None:
        self._runs = FilesystemRunRepository(Path(home) if home is not None else empirica_home())

    def dir(self, key: RunKey) -> Path:
        """The generation's ``hitl`` directory (built as a path; a read creates nothing)."""
        return self._runs.generation_dir(key) / "hitl"

    def path(self, key: RunKey, document_digest: str) -> Path:
        """The document's final path: ``dir(key)/<64 hex>.json``."""
        return self.dir(key) / f"{_name_of(document_digest)}.json"

    # --- write -----------------------------------------------------------------

    def write(self, key: RunKey, data: bytes | bytearray | memoryview) -> tuple[Path, str]:
        """Publish ``data`` once at the name of its own digest; return ``(path, document_digest)``.

        The digest is computed here with :func:`core.hitl.digest` — one definition. Same bytes when
        the document exists: the same result, the file untouched. Different bytes at that name, a
        symlink at any writable path, an unsafe key: refused, storage untouched.
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(f"document bytes, not {type(data).__name__}")
        payload = bytes(data)
        document_digest = core_hitl.digest(payload)
        directory = self._prepare_dir(key)
        final = directory / f"{_name_of(document_digest)}.json"
        published = self._published(final, payload, document_digest)
        if published is not None:
            return published
        fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".tmp.", suffix=".json")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:  # fresh + unique: O_CREAT|O_EXCL, mode 0600
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())  # the bytes reach the disk before the name exists
                os.fchmod(handle.fileno(), _MODE)  # born 0444: published once, never writable
            try:
                os.link(tmp, final)  # the no-replace publish: FileExistsError if the name is taken
            except FileExistsError:
                # Another writer took the name. It is the same digest's content or a hostile plant:
                # the published check re-reads and decides — identical (ours won the race as theirs)
                # is idempotent, anything else fails closed. Either way our temp goes.
                published = self._published(final, payload, document_digest)
                if published is None:
                    raise  # vanished between link and read: absurd, refuse the whole write
                return published
        finally:
            tmp.unlink(missing_ok=True)  # the temp's lifetime ends here, success or failure
        fsio._fsync_dir(directory)  # the new dirent survives a crash (best-effort, as everywhere)
        return (final, document_digest)

    # --- read ------------------------------------------------------------------

    def read(self, key: RunKey, document_digest: str) -> bytes | None:
        """The document's bytes when they exist and hash to ``document_digest``; ``None`` when it
        does not; :class:`DocumentMismatch` when a file is there but is not those bytes; a symlink
        is refused (``SymlinkRefused``, the IO layer's hostile-target condition)."""
        final = self.path(key, document_digest)
        try:
            info = os.lstat(final)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode):  # never followed, never repaired
            raise fsio.SymlinkRefused(f"refusing symlinked document path: {final}")
        if not stat.S_ISREG(info.st_mode):  # a directory, a fifo — nothing a run decided on
            raise DocumentMismatch(f"not a regular document file: {final}")
        existing = fsio.read_bytes(final)  # the no-follow read (O_NOFOLLOW)
        if existing is None:
            return None
        if core_hitl.digest(existing) != document_digest:
            raise DocumentMismatch(f"the bytes at {final} do not hash to their own digest")
        return existing

    # --- shared internals ------------------------------------------------------

    def _prepare_dir(self, key: RunKey) -> Path:
        """The generation's ``hitl`` directory, created with the repository's own directory rules
        (parents as needed, the process's umask — what ``fsio.atomic_write`` uses), after checking
        that neither the generation directory nor ``hitl`` is a planted symlink (ADR-31: a symlink
        at a predictable path is refused, never followed)."""
        generation = self._runs.generation_dir(key)
        for step in (generation, generation / "hitl"):
            if os.path.lexists(step) and stat.S_ISLNK(os.lstat(step).st_mode):
                raise fsio.SymlinkRefused(f"refusing symlinked directory: {step}")
        directory = generation / "hitl"
        directory.mkdir(parents=True, exist_ok=True)
        if stat.S_ISLNK(os.lstat(directory).st_mode):
            raise fsio.SymlinkRefused(f"refusing symlinked directory: {directory}")
        return directory

    def _published(self, final: Path, payload: bytes,
                   document_digest: str) -> tuple[Path, str] | None:
        """The existing-document decision: identical bytes are idempotent, anything else is
        refused; ``None`` when nothing (or nothing readable) exists at the path."""
        try:
            info = os.lstat(final)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode):
            raise fsio.SymlinkRefused(f"refusing symlinked document path: {final}")
        if not stat.S_ISREG(info.st_mode):
            raise DocumentMismatch(f"not a regular document file: {final}")
        existing = fsio.read_bytes(final)
        if existing is None:
            return None
        if existing == payload:
            return (final, document_digest)
        raise DocumentMismatch(
            f"{final} already holds different bytes: a document is written exactly once")
