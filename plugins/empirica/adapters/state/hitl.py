"""The write-once HITL document sidecar beside each run generation (ADR-0072, the Write step).

    <home>/projects/<project_id>/runs/<run_id>/gen-<generation>/hitl/<sha256-hex>.json

Every document a human decision binds to (``empirica.hitl/1``, built by ``core/hitl.py``) is
written here **exactly once**, at the name of its own content digest (the ``document_digest`` of
ADR-0072 — the ``sha256:`` prefix followed by 64 lowercase hex characters; nothing else names a
document:)

* **write-once, atomic** — the bytes go to a fresh unique ``O_CREAT|O_EXCL`` temp file (``mkstemp``,
  never a predictable name), are flushed, set to mode ``0444`` *before* the ``fsync`` — so the
  read-only mode is durable together with the bytes — and are then published with
  :func:`os.link`, the no-replace primitive: ``link`` fails with ``FileExistsError`` when the name
  exists, so no path here can ever overwrite or replace an existing document (the general state
  writes use temp + atomic rename, ``fsio.atomic_write``, which *may* overwrite — a different tool
  for a different contract). Where the filesystem cannot hard-link at all (``EPERM``, ``EXDEV``,
  ``EMLINK``, ``ENOTSUP``, ``ENOSYS``) the failure is the named :class:`HardLinkUnsupported`, so a
  host can tell "this storage cannot hold the sidecar" from a document condition. The temp is
  unlinked and the directory fsynced after the link; a first write that created the ``hitl``
  directory fsyncs the generation directory too, so the new directory entry survives a crash.
  A crash can leave only a whole temp file (orphaned ``.tmp.*`` files, inert and ignored: reads
  never look at them and the next write of the same digest publishes regardless), never a partial
  or torn final document.
* **idempotent on identical bytes** — a digest names one content, so writing the same bytes again
  returns the same ``(path, document_digest)`` without touching the file. A file at a digest name
  holding anything else fails closed (:class:`DocumentMismatch`); a symlink at the document path is
  refused, never followed (:class:`fsio.SymlinkRefused`, ADR-31's rule — hostile targets are loud,
  not values).
* **read verifies** — :meth:`HitlDocuments.read` opens the document no-follow (``O_NOFOLLOW``) and
  without blocking (``O_NONBLOCK``: a FIFO swapped at the path is refused promptly, never waited
  on), requires a regular file by the open descriptor's own ``fstat``, and returns the bytes only
  when their digest is the requested one; absence is ``None``, tampering is
  :class:`DocumentMismatch`.

The symlink scope is honest, not absolute: the run's own directories (``gen-<n>`` and its
``hitl``) are checked with ``lstat`` before every write, and the document path is opened
``O_NOFOLLOW``. Ancestors above them are the repository's existing rules — ``fsio.atomic_write``
creates parents as needed and refuses its own symlinked targets — and a same-uid attacker racing
the ``mkdir`` between the check and the creation is outside the threat model (nothing here defends
against the user running the process; see ADR-31's scope). This module deliberately does not use
dir_fd-relative operations; the write-first flow's guarantees are about the file, its mode and its
name.

Nothing here is wired into the evidence or residual surfaces yet: this module's writer is
reached only from the MCP host governance mediation (``adapters.governance``, which publishes
each configuration document before its form is shown, ADR-0072's Write/Present steps), and
its readers remain the regression suite and the run's own record.
"""
from __future__ import annotations

import errno
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
#: The digest prefix of every document identity — part of the name's rule, never decoration.
_PREFIX = "sha256:"
#: The mode of every published document: readable by all, writable by none (ADR-0072).
_MODE = 0o444
#: Reading a document never follows a symlink, never blocks on a swapped special file, never
#: leaks the descriptor into a child: a regular file only, known from its own descriptor.
_READ_FLAGS = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
               | getattr(os, "O_CLOEXEC", 0))
#: The ``os.link`` failures that mean "this filesystem cannot hard-link" — a storage condition
#: with its own name, not a document condition (review-1 finding 18).
_LINK_UNSUPPORTED = frozenset(
    number for number in (errno.EPERM, errno.EXDEV, errno.EMLINK, errno.ENOSYS,
                          getattr(errno, "ENOTSUP", errno.EOPNOTSUPP),
                          getattr(errno, "EOPNOTSUPP", None))
    if number is not None)


class DocumentMismatch(OSError):
    """The bytes at a document's digest name are not the bytes that name claims.

    A digest names one content: anything else at that path — different bytes, a non-regular file —
    is corruption of the write-once contract, refused loudly, never repaired or replaced. A symlink
    stays the IO layer's distinct hostile condition (:class:`fsio.SymlinkRefused`).
    """


class HardLinkUnsupported(OSError):
    """This filesystem cannot hard-link a temp file to the document's name.

    :func:`os.link` fails with ``EPERM`` (some network filesystems), ``EXDEV`` (across volumes),
    ``EMLINK`` (too many links), ``ENOTSUP``/``ENOSYS`` (no support): the no-replace publish is
    impossible here, and the difference from a document condition matters to a host deciding what
    to tell a human. Nothing was written, published or replaced when this is raised.
    """


def _name_of(document_digest: str) -> str:
    """The filename for ``document_digest``: the 64 lowercase hex characters after the required
    ``sha256:`` prefix (review-1 finding 14) — the prefix is part of the identity, so the same
    64 hex under any other label, or in any other case, is not a document's name."""
    if (isinstance(document_digest, str) and document_digest.startswith(_PREFIX)
            and _HEX64.match(document_digest[len(_PREFIX):])):
        return document_digest[len(_PREFIX):]
    raise ValueError(f"not a document_digest: {document_digest!r}")


def _read_regular_bytes(path: Path) -> bytes | None:
    """The bytes of a regular file at ``path``, opened no-follow and without blocking, ``None``
    when nothing is there. The regular-file requirement is answered by the open descriptor's own
    ``fstat`` — not by a path that a racer could swap — so a symlink is refused
    (:class:`fsio.SymlinkRefused`), and a FIFO or other special file is refused promptly
    (:class:`DocumentMismatch`), never blocked on (review-1 finding 16)."""
    try:
        fd = os.open(str(path), _READ_FLAGS)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:  # O_NOFOLLOW: the final component was a symlink
            raise fsio.SymlinkRefused(f"refusing symlinked document path: {path}") from exc
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise DocumentMismatch(f"not a regular document file: {path}")
        chunks: list[bytes] = []
        while True:  # a regular file: the loop ends at end of file, never a wait
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(fd)


class HitlDocuments:
    """The sidecar store under ``EMPIRICA_HOME``, one ``hitl`` directory per run generation,
    beside the ``run.json`` and the timeline (ADR-0070's beside-the-record location). The home is
    made absolute at construction — never resolved, so a symlinked ``EMPIRICA_HOME`` stays the
    host's own choice — because Q4 decided the form shows the document's absolute path."""

    def __init__(self, home: Path | None = None) -> None:
        base = Path(home) if home is not None else empirica_home()
        self._runs = FilesystemRunRepository(base.absolute())

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
        symlink at any writable path, an unsupported hard-link, an unsafe key: refused, storage
        untouched.
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(f"document bytes, not {type(data).__name__}")
        payload = bytes(data)
        document_digest = core_hitl.digest(payload)
        directory, hitl_created = self._prepare_dir(key)
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
                os.fchmod(handle.fileno(), _MODE)  # born 0444: published once, never writable
                os.fsync(handle.fileno())  # the read-only mode and the bytes reach the disk
                # together, before the name exists (review-1 finding 17)
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
            except OSError as exc:
                if exc.errno in _LINK_UNSUPPORTED:
                    raise HardLinkUnsupported(
                        f"hard links to {final} are unsupported on this filesystem: {exc}") from exc
                raise
        finally:
            tmp.unlink(missing_ok=True)  # the temp's lifetime ends here, success or failure
        # the new dirent survives a crash: best-effort as everywhere, and the first creation of the
        # hitl directory must survive too, so its parent — the generation directory — is flushed
        # alongside. fsio owns the one best-effort directory fsync; this module shares it rather
        # than forking a second fsync path (fsio.py is inside the manifest closure, its rules
        # already apply here unchanged).
        fsio._fsync_dir(directory)
        if hitl_created:
            fsio._fsync_dir(directory.parent)
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
        existing = _read_regular_bytes(final)  # no-follow, no-block, regular by its own fstat
        if existing is None:
            return None
        if core_hitl.digest(existing) != document_digest:
            raise DocumentMismatch(f"the bytes at {final} do not hash to their own digest")
        return existing

    # --- shared internals ------------------------------------------------------

    def _prepare_dir(self, key: RunKey) -> tuple[Path, bool]:
        """The generation's ``hitl`` directory and whether this call created it. Created with the
        repository's own directory rules (parents as needed, the process's umask — what
        ``fsio.atomic_write`` uses), after checking that neither the generation directory nor
        ``hitl`` is a planted symlink (ADR-31: a symlink at a predictable path is refused, never
        followed). The scope is the run's own two directories: ancestors follow ``fsio``'s existing
        rules, and a same-uid racer landing between the check and the ``mkdir`` is outside the
        threat model (see the module docstring) — deliberately not dir_fd-relative operations."""
        generation = self._runs.generation_dir(key)
        for step in (generation, generation / "hitl"):
            if os.path.lexists(step) and stat.S_ISLNK(os.lstat(step).st_mode):
                raise fsio.SymlinkRefused(f"refusing symlinked directory: {step}")
        directory = generation / "hitl"
        hitl_created = not os.path.lexists(directory)
        directory.mkdir(parents=True, exist_ok=True)
        if stat.S_ISLNK(os.lstat(directory).st_mode):
            raise fsio.SymlinkRefused(f"refusing symlinked directory: {directory}")
        return directory, hitl_created

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
        existing = _read_regular_bytes(final)  # the no-follow, no-block read (review-1 finding 16)
        if existing is None:
            return None
        if existing == payload:
            return (final, document_digest)
        raise DocumentMismatch(
            f"{final} already holds different bytes: a document is written exactly once")
