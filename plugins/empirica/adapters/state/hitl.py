"""The write-once HITL document sidecar beside each run generation (ADR-0072, the Write step).

    <home>/projects/<project_id>/runs/<run_id>/gen-<generation>/hitl/<sha256-hex>.json

Every document a human decision binds to (``empirica.hitl/1``, built by ``core/hitl.py``) is
written here **exactly once**, at the name of its own content digest (the ``document_digest`` of
ADR-0072 — the ``sha256:`` prefix followed by 64 lowercase hex characters; nothing else names a
document:)

* **write-once, atomic** — the bytes go to a fresh unique ``O_CREAT|O_EXCL`` temp file (``mkstemp``,
  never a predictable name), are flushed, set to mode ``0400`` (owner read-only, review-2 #43:
  documents hold goals, rationale and quoted material) *before* the ``fsync`` — so the
  read-only mode is durable together with the bytes — and are then published with
  :func:`os.link`, the no-replace primitive: ``link`` fails with ``FileExistsError`` when the name
  exists, so no path here can ever overwrite or replace an existing document (the general state
  writes use temp + atomic rename, ``fsio.atomic_write``, which *may* overwrite — a different tool
  for a different contract). Where the filesystem cannot hard-link at all (``EPERM``, ``EXDEV``,
  ``EMLINK``, ``ENOTSUP``, ``ENOSYS``) the failure is the named :class:`HardLinkUnsupported`, so a
  host can tell "this storage cannot hold the sidecar" from a document condition. The temp is
  unlinked and the directory fsynced after the link (every successful outcome, review-2 #46); a
  first write that created the ``hitl`` directory fsyncs the generation directory too, so the new
  directory entry survives a crash.
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

The writer reaches both hosts through one bound helper: :func:`publish` verifies, parses and
object-checks a core-rendered document pair before its one write, and every binding call site
binds through it — the MCP host's governance mediation and the evidence/consent flows on the
Claude side (``adapters.governance``, ``adapters.confirmation``), and the Pi bridge's
document-binding leg (``adapters.pi.private_bridge``) — so no host ever re-implements the pair's
verification or the publish order (review-2 m1). The readers remain the regression suite and the
run's own record.
"""
from __future__ import annotations

import errno
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core import hitl as core_hitl
from core.records import RunKey

from . import fsio
from .repository import FilesystemRunRepository, empirica_home

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
#: The digest prefix of every document identity — part of the name's rule, never decoration.
_PREFIX = "sha256:"
#: The mode of every published document: readable by the owner, writable by none (ADR-0072;
#: review-2 #43 — documents hold goals, rationale and quoted material, so owner read-only
#: matches the owner-only state metadata beside them — the generic spike staging's own seal
#: is a different surface with its own mode).
_MODE = 0o400
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


class DocumentTooLarge(OSError):
    """A document file whose own ``fstat`` size exceeds the caller's read cap (PR 15, A6).

    The bounded read refuses it before reading a byte: an over-cap document is named ``too_large``
    to the reader, never truncated and never read whole. The bytes are not trusted for the digest
    that is asked about until they fit the cap, so the status is honest without reading them.
    """


class HardLinkUnsupported(OSError):
    """This filesystem cannot hard-link a temp file to the document's name.

    :func:`os.link` fails with ``EPERM`` (some network filesystems), ``EXDEV`` (across volumes),
    ``EMLINK`` (too many links), ``ENOTSUP``/``ENOSYS`` (no support): the no-replace publish is
    impossible here, and the difference from a document condition matters to a host deciding what
    to tell a human. Nothing was written, published or replaced when this is raised.
    """


@dataclass(frozen=True)
class Published:
    """One core-rendered document pair verified, parsed and published exactly once (review-2 m1,
    the one bound helper every binding call site shares): ``digest`` is the pair's own document
    identity, ``path`` the published file's path beside the run's own record, ``parsed`` the
    verified document itself — the one object every caller reads its members from. Nothing but
    construction is gated in the helper: it owns no interpretation, and every checkable refusal
    answers ``None`` with nothing written."""

    digest: str
    path: Path
    parsed: Mapping[str, Any]


def publish(store: "HitlDocuments", run_id: object, pair: object) -> Published | None:
    """Verify one core-rendered ``{text, digest}`` pair, publish its bytes exactly once through
    the write-once store, and give the bound document back (review-2 m1, 7d — the one helper
    Claude's governance and evidence flows and Pi's bridge leg all bind through).

    The order is the point: everything is checked BEFORE the write — the pair is a mapping whose
    members are typed, ``digest`` is a full document identity (the naming rule
    :func:`_name_of` enforces) that names the digested bytes themselves (:func:`core.hitl.digest`,
    the one definition), ``run_id`` decodes to the run whose sidecar this is, and the text parses
    as a JSON **object** of a known kind whose configuration kind names one of the two
    core-derived ``question`` values (X3, Copilot #49) — so a malformed, digest-inconsistent,
    kindless or question-less configuration pair never touches the store.
    The write is the one write (idempotent on the same bytes; different bytes at a digest's name
    is :class:`DocumentMismatch`), and its storage failures — :class:`DocumentMismatch`,
    :class:`fsio.SymlinkRefused`, :class:`HardLinkUnsupported`, any :class:`OSError` — are the
    caller's to translate: the flows differ in what their host must answer (``None``, an
    ``unavailable`` result, a typed fault). The published digest equals the pair's own identity,
    answered and never assumed."""
    from application.location import decode_handle  # lazy: the caller keeps its import-chain cost
    if not isinstance(pair, Mapping):
        return None
    text, digest = pair.get("text"), pair.get("digest")
    if not isinstance(text, str) or not isinstance(digest, str):
        return None
    try:
        name = _name_of(digest)  # the identity rule, before any bytes exist under it
        data = text.encode("utf-8")
        if core_hitl.digest(data) != digest:
            return None
        key = decode_handle(run_id)
        if key is None:
            return None
        parsed = json.loads(text)
    except ValueError:
        return None
    if not isinstance(parsed, Mapping):
        return None
    # X3 (Copilot #49, the one check both governance callers get through this shared helper):
    # a published document has a known kind, and a configuration kind already carries one of the
    # two core-derived questions. The bytes below exist in no flow a form can be shown from —
    # `{}`, a questionless or unknown question, an unknown kind — so refusing them BEFORE the
    # write leaves no orphan at a digest's name; both governance binding legs additionally check
    # ``question`` themselves (the Pi one since PR 13a, the Claude one beside it), so a caller
    # never presents a form chosen from an unverifiable discrimination: the mediation answers
    # ``governance.approval_unavailable`` before any present, nothing spent, nothing written.
    # An evidence document carries no ``question`` and keeps publishing unchanged.
    kind = parsed.get("kind")
    if kind not in (core_hitl.CONFIGURATION, core_hitl.EVIDENCE):
        return None
    if (kind == core_hitl.CONFIGURATION
            and parsed.get("question") not in (core_hitl.APPROVE, core_hitl.CONFIRM_AMENDMENT)):
        return None
    path, written = store.write(key, data)
    if written != digest or path.name != f"{name}.json":  # defensive: the write answers its name
        return None
    return Published(digest=digest, path=path, parsed=parsed)


def _name_of(document_digest: str) -> str:
    """The filename for ``document_digest``: the 64 lowercase hex characters after the required
    ``sha256:`` prefix (review-1 finding 14) — the prefix is part of the identity, so the same
    64 hex under any other label, or in any other case, is not a document's name."""
    if (isinstance(document_digest, str) and document_digest.startswith(_PREFIX)
            and _HEX64.match(document_digest[len(_PREFIX):])):
        return document_digest[len(_PREFIX):]
    raise ValueError(f"not a document_digest: {document_digest!r}")


def _read_regular_bytes(path: Path, cap: int | None = None) -> bytes | None:
    """The bytes of a regular file at ``path``, opened no-follow and without blocking, ``None``
    when nothing is there. The regular-file requirement is answered by the open descriptor's own
    ``fstat`` — not by a path that a racer could swap — so a symlink is refused
    (:class:`fsio.SymlinkRefused`), and a FIFO or other special file is refused promptly
    (:class:`DocumentMismatch`), never blocked on (review-1 finding 16).

    ``cap`` (PR 15, A6) bounds the read itself: a file whose own ``fstat`` size is over the cap
    is refused as too large before one byte is read (:class:`DocumentTooLarge`), and the loop
    then reads at most ``cap + 1`` bytes — the ``+1`` so a file that grew between ``fstat`` and
    the loop is still bounded, while a file that shrank reads whole with no error."""
    try:
        fd = os.open(str(path), _READ_FLAGS)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:  # O_NOFOLLOW: the final component was a symlink
            raise fsio.SymlinkRefused(f"refusing symlinked document path: {path}") from exc
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise DocumentMismatch(f"not a regular document file: {path}")
        if cap is not None and info.st_size > cap:
            raise DocumentTooLarge(f"document file over the read cap: {path}")
        limit = None if cap is None else cap + 1
        chunks: list[bytes] = []
        while True:  # a regular file: the loop ends at end of file, never a wait
            chunk = os.read(fd, 1 << 20 if limit is None else min(1 << 20, limit - sum(len(c) for c in chunks)))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            if limit is not None and sum(len(c) for c in chunks) > limit:
                raise DocumentTooLarge(f"document file grew over the read cap: {path}")
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
        #: The machine home this sidecar store writes under, absolute as the writes see it
        #: (PR 16: the same home a host form's discovery probe reads an advertise file from —
        #: one probe per presentation, never a guess at another home's observer).
        self.home = base.absolute()
        #: Generation directories this store has itself proven synced (review-2 #46): a
        #: completed write's flush is the proof, so a fresh store — or one whose earlier write
        #: was interrupted before its barriers — flushes the generation directory again instead
        #: of trusting a directory entry it has never seen flushed.
        self._proven_generations: set[Path] = set()

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
        directory = self._prepare_dir(key)
        final = directory / f"{_name_of(document_digest)}.json"
        published = self._published(final, payload, document_digest)
        if published is not None:
            self._flush_directories(directory)  # the retry and idempotent paths flush too (#46)
            return published
        fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".tmp.", suffix=".json")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:  # fresh + unique: O_CREAT|O_EXCL, mode 0600
                handle.write(payload)
                handle.flush()
                os.fchmod(handle.fileno(), _MODE)  # born 0400: owner-readable, never writable
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
                self._flush_directories(directory)  # the racing idempotent path flushes too (#46)
                return published
            except OSError as exc:
                if exc.errno in _LINK_UNSUPPORTED:
                    raise HardLinkUnsupported(
                        f"hard links to {final} are unsupported on this filesystem: {exc}") from exc
                raise
        finally:
            tmp.unlink(missing_ok=True)  # the temp's lifetime ends here, success or failure
        # every successful outcome ends here too: the new dirent survives a crash by the same
        # barrier the idempotent paths use — best-effort as everywhere, and the first creation
        # of the hitl directory must survive with it, so the generation directory is flushed
        # alongside until this store has seen it flushed by a completed write (review-2 #46).
        # fsio owns the one best-effort directory fsync; this module shares it rather than
        # forking a second fsync path (fsio.py is inside the manifest closure, its rules
        # already apply here unchanged).
        self._flush_directories(directory)
        return (final, document_digest)

    def _flush_directories(self, directory: Path) -> None:
        """The one exit barrier every successful ``write`` passes through (review-2 #46): the
        document's directory always — an interrupted earlier publication may have left its
        dirent unflushed, and an idempotent success is the retry that must complete it — and the
        generation directory onto which the ``hitl`` entry's own creation may still be riding,
        proven only once a completed call of this store has flushed it."""
        fsio._fsync_dir(directory)
        if directory.parent not in self._proven_generations:
            fsio._fsync_dir(directory.parent)
            self._proven_generations.add(directory.parent)

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

    def read_bounded(self, key: RunKey, document_digest: str, cap: int) -> tuple[str, bytes | None]:
        """The observer's bounded read of one decision's document (PR 15, operator finding F2;
        A6): ``(status, bytes)`` — ``missing`` when nothing is there, ``mismatch`` when bytes are
        there but are not that digest's content, ``too_large`` when the file's own ``fstat``
        size is over ``cap`` (refused before reading), else ``verified`` with the whole bytes.

        The hostile-input rules are the write path's: no-follow and non-blocking reads only,
        a regular file by its own descriptor's ``fstat`` (a FIFO or other special file is refused
        promptly loudly, never blocked on), a symlink at the document path refused, and — the
        read path's own addition — a symlink planted at the run's ``gen-<n>`` or ``hitl``
        directory refused with ``lstat`` exactly as :meth:`_prepare_dir` refuses it when writing.
        A read creates nothing. Malformed input raises as ``read`` does."""
        for step in (self._runs.generation_dir(key), self.dir(key)):
            if os.path.lexists(step) and stat.S_ISLNK(os.lstat(step).st_mode):
                raise fsio.SymlinkRefused(f"refusing symlinked directory: {step}")
        final = self.path(key, document_digest)  # validates the digest before anything is opened
        try:
            existing = _read_regular_bytes(final, cap)  # no-follow, no-block, regular, capped
        except DocumentTooLarge:
            return "too_large", None
        if existing is None:
            return "missing", None
        if core_hitl.digest(existing) != document_digest:
            return "mismatch", None
        return "verified", existing

    # --- shared internals ------------------------------------------------------

    def _prepare_dir(self, key: RunKey) -> Path:
        """The generation's ``hitl`` directory, created **owner-only** (mode ``0700``) when this
        call creates it (review-2 #43): its children are owner-read-only documents and the host's
        state beside them is owner-only metadata, so the directory names and entries are the
        owner's too. Ancestors keep the repository's own directory rules (parents as needed by
        the repository's writer, the process's umask — what ``fsio.atomic_write`` uses), after
        checking that neither the generation directory nor ``hitl`` is a planted symlink
        (ADR-31: a symlink at a predictable path is refused, never followed). The scope is the
        run's own two directories: ancestors follow ``fsio``'s existing rules, and a same-uid
        racer landing between the check and the ``mkdir`` is outside the threat model (see the
        module docstring) — deliberately not dir_fd-relative operations."""
        generation = self._runs.generation_dir(key)
        for step in (generation, generation / "hitl"):
            if os.path.lexists(step) and stat.S_ISLNK(os.lstat(step).st_mode):
                raise fsio.SymlinkRefused(f"refusing symlinked directory: {step}")
        directory = generation / "hitl"
        if not os.path.lexists(directory):
            directory.parent.mkdir(parents=True, exist_ok=True)  # ancestors: the repository's rules
            try:
                os.mkdir(directory, 0o700)  # the store's own directory, owner-only
            except FileExistsError:
                pass  # a sharing thread of the same store won the creation; its entry is ours too
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
        existing = _read_regular_bytes(final)  # the no-follow, no-block read (review-1 finding 16)
        if existing is None:
            return None
        if existing == payload:
            return (final, document_digest)
        raise DocumentMismatch(
            f"{final} already holds different bytes: a document is written exactly once")
