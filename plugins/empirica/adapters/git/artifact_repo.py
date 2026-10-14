"""Git shadow-ref adapter for :class:`empirica.core.ports.ArtifactRepository` (ADR-31).

A run's claims and evidence are stored as immutable, content-addressed Git objects under
``refs/empirica/artifacts/*``. The store is the *set* of artifacts appended under a key, realised as
a flat Git tree keyed by artifact id; the ref is the head of an append-only commit chain over that
set. This gives the port's semantics for free:

* **Content-addressed & immutable** — each artifact is a blob at a tree path derived from its id;
  identical bodies produce identical blobs and, with fixed commit metadata, identical commits.
* **Idempotent** — appending an artifact already present rewrites the identical tree, so the ref
  never moves.
* **Commutative** — the resulting set is a Git tree, whose contents do not depend on append order.
* **Concurrent-safe without locking** — writes land via a compare-and-swap ``update-ref``; a losing
  writer re-reads the winner's tree, unions its own artifact in, and retries, so two concurrent
  *distinct* appends both survive (ADR-31 "append retries are set unions by artifact identity").
* **Collision = corruption** — a content address binds to exactly one body. An append that would
  bind an existing id to a different body is refused (:class:`ArtifactCollision`); a stored entry
  whose path disagrees with its recorded id reads back as ``Corrupt``, never silently.

Isolation guarantees (ADR-31): the adapter uses only Git plumbing — ``hash-object``, ``mktree``,
``commit-tree``, ``update-ref`` — against the resolved ``--git-common-dir``. It never runs
``add``/``commit``/``checkout``, never sets ``GIT_INDEX_FILE`` or a work tree, and never
pushes or fetches. The user's HEAD, index, and worktree are left byte-identical, and linked
worktrees share one artifact namespace because ``refs/empirica/*`` lives in the common directory.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# Import the domain records by shape. This module is loaded both as a package
# (`empirica.adapters.git.artifact_repo`) and by direct path in the tests, so support both.
try:  # package import
    from ...core.canonical import canonical_json
    from ...core.records import ABSENT, Artifact, Corrupt, Present, Read, Revision, RunKey
except ImportError:  # pragma: no cover - direct path-load fallback (see tests)
    from core.canonical import canonical_json  # type: ignore
    from core.records import ABSENT, Artifact, Corrupt, Present, Read, Revision, RunKey  # type: ignore

# CAS create sentinel: `update-ref <ref> <new> ""` (empty old-value) succeeds only if the ref does
# not exist. The empty string is object-format agnostic — a literal zero-oid would have to be 40 or
# 64 hex chars depending on whether the repo is SHA-1 or SHA-256, and the wrong width is rejected.
_MUST_NOT_EXIST = ""

# Bound on the compare-and-swap retry loop. Each iteration re-reads the ref and re-attempts the
# swap; contention resolves in one extra pass per concurrent winner, so this only trips on a
# pathological live-lock (or a bug) rather than normal concurrency.
_MAX_APPEND_RETRIES = 64

# A tree-path component that is safe to use verbatim: no separators, no leading dot, no refname
# hazards. Anything else is escaped (see :func:`tree_path`).
_SAFE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Fixed commit identity and timestamps. Determinism is a requirement (ADR-31 "deterministic tree
# layout and commit metadata"): the same set of artifacts must yield the same commit object on any
# machine at any time, so nothing here may read the wall clock or the user's git identity.
_COMMIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "empirica",
    "GIT_AUTHOR_EMAIL": "empirica@localhost",
    "GIT_COMMITTER_NAME": "empirica",
    "GIT_COMMITTER_EMAIL": "empirica@localhost",
    "GIT_AUTHOR_DATE": "@0 +0000",
    "GIT_COMMITTER_DATE": "@0 +0000",
}


def toplevel(path: Path) -> Path | None:
    """The working tree's top-level directory holding ``path``, or ``None`` outside a repository.

    Plain ``git`` in the user's environment, like the plugin's own project identity
    (``identity._git_common_dir``): a repository must resolve here exactly as StartRun resolved it,
    so this deliberately does not use the artifact repository's isolated Git environment."""
    proc = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, check=False)
    return Path(proc.stdout.strip()).resolve() if proc.returncode == 0 else None


class ArtifactCollision(Exception):
    """An append tried to bind an existing artifact id to a different body.

    A content address binds to exactly one body (see ``ArtifactRepository.append``); a second body
    under the same id is a producer bug, not an update. The append is refused rather than
    overwriting, so the already-stored artifact is preserved and the conflict is surfaced.
    """

    def __init__(self, key: RunKey, artifact_id: str) -> None:
        self.key = key
        self.artifact_id = artifact_id
        super().__init__(
            f"artifact id collision on {key}: id {artifact_id!r} is already bound to a different body"
        )


class GitError(RuntimeError):
    """A Git plumbing command failed unexpectedly (not a normal absent/corrupt outcome)."""


@dataclass(frozen=True)
class _Entry:
    """One decoded tree entry: the on-disk record for a single artifact."""

    path: str
    artifact_id: str
    body: str
    blob_oid: str


class GitArtifactRepository:
    """A Git-backed :class:`~empirica.core.ports.ArtifactRepository`.

    Construct with any path inside the target repository (a linked worktree is fine); the adapter
    resolves the shared common directory once and issues every command against it.
    """

    def __init__(self, repo_dir: str | Path) -> None:
        self._common_dir = self._resolve_common_dir(Path(repo_dir))

    # --- port surface --------------------------------------------------------

    def append(self, key: RunKey, artifact: Artifact) -> None:
        """Add ``artifact`` to the set under ``key`` (commutative, idempotent, CAS-retried)."""
        if not artifact.artifact_id:
            raise ValueError("artifact_id must be non-empty")
        ref = self.ref_for(key)
        path = tree_path(artifact.artifact_id)
        new_blob = self._write_blob(_encode_body(artifact))

        for _ in range(_MAX_APPEND_RETRIES):
            head = self._resolve_commit(ref)
            try:
                entries = self._read_entries(head) if head is not None else {}
            except _CorruptTree as exc:
                # Refuse to append onto a corrupt store rather than paper over it — fail loudly
                # with a public exception instead of leaking the internal signal.
                raise GitError(f"cannot append onto corrupt store at {ref}: {exc}") from exc

            existing = entries.get(path)
            if existing is not None:
                if existing.blob_oid == new_blob:
                    return  # already present verbatim — idempotent no-op, ref untouched
                # Same path (== same id) but a different body: a content-address collision.
                raise ArtifactCollision(key, artifact.artifact_id)

            new_tree = self._make_tree({**{e.path: e.blob_oid for e in entries.values()}, path: new_blob})
            if head is not None and new_tree == self._tree_of(head):
                return  # defensive: nothing changed
            commit = self._commit_tree(new_tree, parent=head)
            old = head if head is not None else _MUST_NOT_EXIST
            if self._cas_update_ref(ref, commit, old):
                return
            # Lost the race: the ref moved. Re-read and union our artifact into the new head.

        raise GitError(f"append did not converge after {_MAX_APPEND_RETRIES} retries on {ref}")

    def read(self, key: RunKey) -> Read[frozenset[Artifact]]:
        """Return the union of artifacts under ``key``, ``ABSENT``, or ``Corrupt`` — never raising
        for absence or corruption (both are values, per ADR-31)."""
        ref = self.ref_for(key)
        if not self._ref_exists(ref):
            return ABSENT  # never written under this (project, run, generation)

        commit = self._peel_commit(ref)
        if commit is None:
            return Corrupt(f"{ref} exists but does not resolve to a commit")

        try:
            entries = self._read_entries(commit)
        except _CorruptTree as exc:
            return Corrupt(str(exc))

        artifacts = frozenset(Artifact(e.artifact_id, e.body) for e in entries.values())
        return Present(artifacts, Revision(self._tree_of(commit)))

    # --- ref naming ----------------------------------------------------------

    def ref_for(self, key: RunKey) -> str:
        """The shadow ref for a run key. Components are injective (a content hash disambiguates)
        yet readable (a slug leads), and always refname-safe."""
        return "refs/empirica/artifacts/{gen}/{proj}/{run}".format(
            gen=int(key.generation),
            proj=ref_component(key.project_id),
            run=ref_component(key.run_id),
        )

    def ref_heads(self) -> dict[str, str]:
        """Every artifact ref and the object it names, in one read-only ``for-each-ref`` that opens no object: what a
        reader polls to learn which runs' histories moved without reading any of them."""
        proc = self._git("for-each-ref", "--format=%(refname) %(objectname)", "refs/empirica/artifacts/")
        if proc.returncode != 0:
            raise GitError(f"for-each-ref failed: {proc.stderr.strip()}")
        return dict(line.split(" ", 1) for line in proc.stdout.splitlines() if " " in line)

    #: What a refs signature holds besides the artifact directories: a change to any of these may move any ref.
    REFS_GLOBAL = ("packed-refs", "reftable", "reftable/tables.list")

    def refs_signature(self) -> dict[str, tuple[int, int, int, int] | None] | None:
        """What changes whenever an artifact ref could have moved, read without running git, by path under the common
        directory: the ``lstat`` of ``packed-refs`` and ``reftable/`` (``REFS_GLOBAL``), and of every directory under
        ``refs/empirica/artifacts`` down to each project's (git moves a loose ref, packs refs and adds a table by
        writing a lock file and renaming it, so the directory that holds it changes; a run's ref is a file in its
        project's directory). Compared for equality only, never read as a time. ``None`` when it cannot be read: the
        caller then lists everything."""
        base = str(self._common_dir) + "/"
        try:
            signature = {name: _lstat(base + name) for name in self.REFS_GLOBAL}
            root = "refs/empirica/artifacts"
            signature[root] = _lstat(base + root)
            for level in _subdirs(base, root):  # generations, then each one's projects
                signature[level] = _lstat(base + level)
                for project in _subdirs(base, level):
                    signature[project] = _lstat(base + project)
        except OSError:
            return None
        return signature

    def ref_heads_under(self, directories: Iterable[str]) -> dict[str, str]:
        """``ref_heads`` for the refs in these project directories only (``refs_signature`` paths), in one
        ``for-each-ref``."""
        patterns = sorted({d.rstrip("/") + "/" for d in directories})
        if not patterns:
            return {}
        proc = self._git("for-each-ref", "--format=%(refname) %(objectname)", *patterns)
        if proc.returncode != 0:
            raise GitError(f"for-each-ref failed: {proc.stderr.strip()}")
        return dict(line.split(" ", 1) for line in proc.stdout.splitlines() if " " in line)

    def read_listed(self, commits: Iterable[str]) -> dict[str, Read[frozenset[Artifact]]]:
        """The histories at many listed commits (``ref_heads`` values), by commit, through one ``cat-file --batch``
        process in all (three rounds: the commits, their trees, every blob they name) rather than five processes per
        history. Each is exactly what ``read`` returns for a ref at that commit; one that is not plainly a commit whose
        flat tree of artifact blobs reads and decodes whole is left out, for the caller's single ``read`` to answer
        (including as ``Corrupt``)."""
        wanted = {c for c in commits if _OID.fullmatch(c)}
        if not wanted:
            return {}
        with self._cat_file() as batch:
            trees_of = {}
            for commit, raw in batch(wanted, "commit").items():
                first = raw.split(b"\n", 1)[0].split(b" ")
                if len(first) == 2 and first[0] == b"tree" and _OID.fullmatch(first[1].decode("ascii", "replace")):
                    trees_of[commit] = first[1].decode("ascii")
            trees = batch(set(trees_of.values()), "tree")
            descriptors: dict[str, list[tuple[str, str]]] = {}
            for commit, tree in trees_of.items():
                parsed = _tree_entries(trees.get(tree), len(tree) // 2)
                if parsed is not None:
                    descriptors[commit] = parsed
            blobs = batch({oid for entries in descriptors.values() for _, oid in entries}, "blob")
        out: dict[str, Read[frozenset[Artifact]]] = {}
        for commit, entries in descriptors.items():
            try:
                artifacts = frozenset(Artifact(e.artifact_id, e.body) for e in
                                      (_entry(path, oid, blobs.get(oid)) for path, oid in entries))
            except _CorruptTree:
                continue
            out[commit] = Present(artifacts, Revision(trees_of[commit]))
        return out

    @contextlib.contextmanager
    def _cat_file(self):
        """One ``cat-file --batch`` process for several rounds of reads: ``batch(oids, kind)`` returns the bodies of those
        of type ``kind``; any that is missing or of another type is left out, and a frame error ends the session
        (nothing read after it is trusted)."""
        proc = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=self._git_env())
        broken: list[object] = []

        def batch(oids: set[str], kind: str) -> dict[str, bytes]:
            found: dict[str, bytes] = {}
            if not oids or broken:
                return found
            order = sorted(oids)
            try:
                proc.stdin.write(b"".join(o.encode("ascii") + b"\n" for o in order))
                proc.stdin.flush()
                for oid in order:
                    fields = proc.stdout.readline().split()
                    if len(fields) == 2 and fields[1] == b"missing":
                        continue
                    if len(fields) != 3 or not fields[2].isdigit():
                        broken.append(oid)
                        return found
                    size = int(fields[2])
                    body = proc.stdout.read(size)
                    if len(body) != size or proc.stdout.read(1) != b"\n":
                        broken.append(oid)
                        return found
                    if fields[0] == oid.encode("ascii") and fields[1] == kind.encode("ascii"):
                        found[oid] = body
            except OSError:
                broken.append(None)
            return found
        try:
            yield batch
        finally:
            for stream in (proc.stdin, proc.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
            proc.wait()

    # --- git plumbing --------------------------------------------------------

    def _git_env(self) -> dict[str, str]:
        """Environment shared by text and byte-exact Git plumbing transports."""
        return {
            **_COMMIT_IDENTITY,
            "GIT_DIR": str(self._common_dir),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            # Pin the locale so Git's diagnostics are stable, untranslated English — the CAS
            # fallback below inspects stderr wording, and a translated catalog would break it.
            "LC_ALL": "C",
            "LANG": "C",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }

    def _git(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        """Run text Git plumbing against the common dir, isolated from user configuration."""
        # Encoding is pinned to UTF-8 (not the ambient locale) so artifact bodies round-trip
        # byte-for-byte regardless of the host's LANG — a determinism requirement (ADR-31).
        return subprocess.run(
            ["git", *args],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self._git_env(),
        )

    def _git_bytes(
        self, *args: str, stdin: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        """Run byte-exact Git plumbing for protocols whose frames are sized in bytes."""
        return subprocess.run(
            ["git", *args], input=stdin, capture_output=True, env=self._git_env()
        )

    def _resolve_common_dir(self, repo_dir: Path) -> Path:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if proc.returncode != 0:
            raise GitError(
                f"{repo_dir} is not a git repository: {proc.stderr.strip()} "
                "(the empirica knowledge store needs the workspace git repository as the working "
                "directory; invoke the bridge with cwd set to the repo)")
        return Path(proc.stdout.strip())

    def _write_blob(self, content: str) -> str:
        proc = self._git("hash-object", "-w", "--stdin", stdin=content)
        if proc.returncode != 0:
            raise GitError(f"hash-object failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def _make_tree(self, path_to_blob: dict[str, str]) -> str:
        # Flat tree: every artifact is a single-level entry, so `mktree` (one level) is sufficient
        # and no index is ever involved. Feed entries in sorted order for a deterministic layout.
        spec = "".join(
            f"100644 blob {oid}\t{path}\n" for path, oid in sorted(path_to_blob.items())
        )
        proc = self._git("mktree", stdin=spec)
        if proc.returncode != 0:
            raise GitError(f"mktree failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def _commit_tree(self, tree: str, parent: str | None) -> str:
        args = ["commit-tree", tree]
        if parent is not None:
            args += ["-p", parent]
        proc = self._git(*args, stdin="empirica artifacts")
        if proc.returncode != 0:
            raise GitError(f"commit-tree failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def _cas_update_ref(self, ref: str, new: str, old: str) -> bool:
        """Atomically set ``ref`` to ``new`` iff it currently equals ``old`` (``_MUST_NOT_EXIST`` =
        must not exist). Returns False if the swap should be retried (the ref moved underneath us,
        or a transient lock was held), True on success; raises on a genuine failure.
        """
        proc = self._git("update-ref", ref, new, old)
        if proc.returncode == 0:
            return True
        # Classify the failure by re-reading actual state rather than trusting stderr wording: if
        # the ref no longer matches what we required, another writer moved it — an ordinary lost
        # CAS race under concurrency, so signal a retry. This is locale- and ref-backend-independent.
        current = self._peel_commit(ref)  # commit oid, or None if absent
        moved = current is not None if old == _MUST_NOT_EXIST else current != old
        if moved:
            return False
        # The ref did not move: retry only on transient lock contention (another writer holds the
        # ref lock this instant); anything else is a real failure we must surface.
        stderr = proc.stderr
        if "cannot lock ref" in stderr or "unable to create" in stderr or "File exists" in stderr:
            return False
        raise GitError(f"update-ref failed: {stderr.strip()}")

    def _ref_exists(self, ref: str) -> bool:
        # Existence without peeling: distinguishes a truly absent ref (return False) from one that
        # exists but points at a non-commit (exists, but will fail to peel -> corrupt).
        proc = self._git("show-ref", "--verify", "--quiet", ref)
        return proc.returncode == 0

    def _resolve_commit(self, ref: str) -> str | None:
        """The commit oid at ``ref`` for the append path, or None if the ref is absent."""
        if not self._ref_exists(ref):
            return None
        commit = self._peel_commit(ref)
        if commit is None:
            # A corrupt head on the append path is unrecoverable without clobbering — fail loudly
            # rather than silently overwrite unreadable history.
            raise GitError(f"{ref} exists but does not resolve to a commit")
        return commit

    def _peel_commit(self, ref: str) -> str | None:
        proc = self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    def _tree_of(self, commit: str) -> str:
        proc = self._git("rev-parse", "--verify", "--quiet", f"{commit}^{{tree}}")
        if proc.returncode != 0:
            raise GitError(f"cannot resolve tree of {commit}")
        return proc.stdout.strip()

    def _read_entries(self, commit: str) -> dict[str, _Entry]:
        """Decode the flat artifact tree at ``commit`` into ``path -> _Entry``.

        Raises :class:`_CorruptTree` on any structural violation (non-blob entry, undecodable body,
        or a path that disagrees with the id it stores — the read-side collision check).
        """
        proc = self._git("ls-tree", commit)
        if proc.returncode != 0:
            raise _CorruptTree(f"cannot list tree of {commit}: {proc.stderr.strip()}")

        descriptors: list[tuple[str, str]] = []
        for line in proc.stdout.splitlines():
            if not line:
                continue
            meta, separator, path = line.partition("\t")
            fields = meta.split()
            if not separator or len(fields) != 3:
                raise _CorruptTree(f"malformed tree entry: {line!r}")
            _mode, obj_type, oid = fields
            if obj_type != "blob":
                raise _CorruptTree(f"unexpected {obj_type} entry at {path!r}; artifacts are blobs")
            descriptors.append((path, oid))
        return self._batch_read_entries(descriptors)

    def _batch_read_entries(self, descriptors: list[tuple[str, str]]) -> dict[str, _Entry]:
        """Read blob bodies in one ``cat-file --batch`` process and validate exact byte frames."""
        if not descriptors:
            return {}
        request = b"".join(oid.encode("ascii") + b"\n" for _, oid in descriptors)
        proc = self._git_bytes("cat-file", "--batch", stdin=request)
        if proc.returncode != 0:
            raise _CorruptTree("cannot batch-read artifact blobs")

        stream = io.BytesIO(proc.stdout)
        entries: dict[str, _Entry] = {}
        for path, oid in descriptors:
            header = stream.readline()
            fields = header.split()
            if (
                not header.endswith(b"\n")
                or len(fields) != 3
                or fields[0] != oid.encode("ascii")
                or fields[1] != b"blob"
                or not fields[2].isdigit()
            ):
                raise _CorruptTree(f"invalid cat-file batch header for blob {oid} at {path!r}")
            try:
                size = int(fields[2])
            except ValueError as exc:
                raise _CorruptTree(
                    f"invalid cat-file batch size for blob {oid} at {path!r}"
                ) from exc
            if size > len(proc.stdout) - stream.tell() - 1:
                raise _CorruptTree(f"truncated cat-file batch body for blob {oid} at {path!r}")
            encoded = stream.read(size)
            if len(encoded) != size or stream.read(1) != b"\n":
                raise _CorruptTree(f"invalid cat-file batch delimiter for blob {oid} at {path!r}")
            try:
                blob = encoded.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _CorruptTree(f"blob {oid} at {path!r} is not valid UTF-8") from exc
            artifact_id, body = _decode_body(blob, path)
            if tree_path(artifact_id) != path:
                raise _CorruptTree(
                    f"artifact id/path mismatch at {path!r}: blob records id {artifact_id!r}"
                )
            entries[path] = _Entry(path=path, artifact_id=artifact_id, body=body, blob_oid=oid)
        if stream.read(1):
            raise _CorruptTree("unexpected trailing output from cat-file batch")
        return entries


class _CorruptTree(Exception):
    """Internal signal: the stored tree violates a structural invariant (read returns Corrupt)."""


def _lstat(path: str) -> tuple[int, int, int, int] | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    return st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _subdirs(base: str, relative: str) -> list[str]:
    """The real subdirectories of ``base + relative``, as paths relative to ``base``."""
    try:
        with os.scandir(base + relative) as entries:
            return sorted(f"{relative}/{e.name}" for e in entries if e.is_dir(follow_symlinks=False))
    except FileNotFoundError:
        return []


_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
#: Tree modes ``ls-tree`` reports as blobs; anything else makes ``read`` answer ``Corrupt``.
_BLOB_MODES = (b"100644", b"100755", b"120000")
#: Names ``ls-tree`` prints unquoted, so a tree whose names are all of these lists the same either way.
_PLAIN_NAME = re.compile(rb"[\x21-\x7e]+")


def _tree_entries(raw: bytes | None, width: int) -> list[tuple[str, str]] | None:
    """A raw tree object's ``(path, blob oid)`` entries, in tree order, exactly as ``ls-tree`` would list them; ``None``
    when that is not certain (an entry that is not a blob, a name ``ls-tree`` would quote, a malformed frame)."""
    if raw is None:
        return None
    entries, at = [], 0
    while at < len(raw):
        space, nul = raw.find(b" ", at), raw.find(b"\0", at)
        if space < 0 or nul < space or nul + 1 + width > len(raw):
            return None
        mode, name = raw[at:space], raw[space + 1:nul]
        if mode not in _BLOB_MODES or not _PLAIN_NAME.fullmatch(name) or b'"' in name or b"\\" in name:
            return None
        entries.append((name.decode("ascii"), raw[nul + 1:nul + 1 + width].hex()))
        at = nul + 1 + width
    return entries


def _entry(path: str, oid: str, encoded: bytes | None) -> _Entry:
    """One artifact blob decoded and checked as ``_batch_read_entries`` checks it."""
    if encoded is None:
        raise _CorruptTree(f"blob {oid} at {path!r} was not read")
    try:
        blob = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _CorruptTree(f"blob {oid} at {path!r} is not valid UTF-8") from exc
    artifact_id, body = _decode_body(blob, path)
    if tree_path(artifact_id) != path:
        raise _CorruptTree(f"artifact id/path mismatch at {path!r}: blob records id {artifact_id!r}")
    return _Entry(path=path, artifact_id=artifact_id, body=body, blob_oid=oid)


# --- encoding helpers --------------------------------------------------------


def _encode_body(artifact: Artifact) -> str:
    """Serialise an artifact to its on-disk blob: a deterministic JSON record.

    Both the id and the body are stored so a read can cross-check the tree path against the
    recorded id (the read-side collision guard). ``sort_keys`` + fixed separators make the encoding
    canonical, which is what lets identical artifacts hash to identical blobs (idempotency).
    """
    return canonical_json({"id": artifact.artifact_id, "body": artifact.body})


def _decode_body(blob: str, path: str) -> tuple[str, str]:
    try:
        record = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise _CorruptTree(f"blob at {path!r} is not valid JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise _CorruptTree(f"blob at {path!r} is not a JSON object")
    artifact_id, body = record.get("id"), record.get("body")
    if not isinstance(artifact_id, str) or not isinstance(body, str):
        raise _CorruptTree(f"blob at {path!r} is missing string id/body fields")
    return artifact_id, body


def tree_path(artifact_id: str) -> str:
    """An injective, ``/``-free, refname-safe tree path for an artifact id.

    Content addresses (hex digests) pass through verbatim for a reviewable layout. Anything else is
    escaped as ``=<hex>``; the ``=`` marker is disjoint from the verbatim namespace (a safe id
    cannot begin with ``=``), so the mapping stays injective — distinct ids never share a path.
    """
    if _SAFE_PATH.match(artifact_id) and artifact_id not in (".", ".."):
        return artifact_id
    return "=" + artifact_id.encode("utf-8").hex()


def ref_component(value: str) -> str:
    """A refname-safe component: a readable slug plus a full SHA-256 content digest.

    The digest disambiguates distinct inputs even when their slugs collapse to the same slug (e.g.
    ``a/b`` and ``a-b``); the slug keeps refs human-legible for review. The full (untruncated)
    digest is used deliberately — a truncated prefix would reintroduce a birthday-bound collision
    risk between two runs, which would silently merge their artifact sets into one namespace."""
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", value).strip("-.")
    slug = re.sub(r"-{2,}", "-", slug) or "x"
    # Git rejects ``..`` anywhere in a ref even when the full component is not
    # itself ``..``. Keep the readable prefix valid; the full digest below
    # preserves injectivity.
    while ".." in slug:
        slug = slug.replace("..", "-")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{slug}-{digest}"
