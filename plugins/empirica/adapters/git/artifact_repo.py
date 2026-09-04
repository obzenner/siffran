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

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Import the domain records by shape. This module is loaded both as a package
# (`empirica.adapters.git.artifact_repo`) and by direct path in the tests, so support both.
try:  # package import
    from ...core.records import ABSENT, Artifact, Corrupt, Present, Read, Revision, RunKey
except ImportError:  # pragma: no cover - direct path-load fallback (see tests)
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
# hazards. Anything else is escaped (see :func:`_tree_path`).
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
        path = _tree_path(artifact.artifact_id)
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
            proj=_ref_component(key.project_id),
            run=_ref_component(key.run_id),
        )

    # --- git plumbing --------------------------------------------------------

    def _git(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        """Run a Git plumbing command against the common dir, isolated from user config.

        ``GIT_DIR`` points at the resolved common directory and no work tree or index file is set,
        so nothing here can read or write the user's checkout. System/global config is disabled to
        keep commit objects deterministic regardless of the user's git settings (e.g. gpg signing).
        """
        env = {
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
        # encoding is pinned to UTF-8 (not the ambient locale) so artifact bodies round-trip
        # byte-for-byte regardless of the host's LANG — a determinism requirement (ADR-31).
        return subprocess.run(
            ["git", *args],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )

    def _resolve_common_dir(self, repo_dir: Path) -> Path:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if proc.returncode != 0:
            raise GitError(f"{repo_dir} is not a git repository: {proc.stderr.strip()}")
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

        entries: dict[str, _Entry] = {}
        for line in proc.stdout.splitlines():
            if not line:
                continue
            meta, _, path = line.partition("\t")
            fields = meta.split()
            if len(fields) != 3:
                raise _CorruptTree(f"malformed tree entry: {line!r}")
            _mode, obj_type, oid = fields
            if obj_type != "blob":
                raise _CorruptTree(f"unexpected {obj_type} entry at {path!r}; artifacts are blobs")

            try:
                body_proc = self._git("cat-file", "blob", oid)
            except UnicodeDecodeError as exc:
                # A well-formed artifact body is always valid UTF-8 (we wrote it as such); a blob
                # that is not is tampered/corrupt. read() must surface this as Corrupt, not raise.
                raise _CorruptTree(f"blob {oid} at {path!r} is not valid UTF-8") from exc
            if body_proc.returncode != 0:
                raise _CorruptTree(f"cannot read blob {oid} at {path!r}")
            artifact_id, body = _decode_body(body_proc.stdout, path)
            if _tree_path(artifact_id) != path:
                raise _CorruptTree(
                    f"artifact id/path mismatch at {path!r}: blob records id {artifact_id!r}"
                )
            entries[path] = _Entry(path=path, artifact_id=artifact_id, body=body, blob_oid=oid)
        return entries


class _CorruptTree(Exception):
    """Internal signal: the stored tree violates a structural invariant (read returns Corrupt)."""


# --- encoding helpers --------------------------------------------------------


def _encode_body(artifact: Artifact) -> str:
    """Serialise an artifact to its on-disk blob: a deterministic JSON record.

    Both the id and the body are stored so a read can cross-check the tree path against the
    recorded id (the read-side collision guard). ``sort_keys`` + fixed separators make the encoding
    canonical, which is what lets identical artifacts hash to identical blobs (idempotency).
    """
    return json.dumps(
        {"id": artifact.artifact_id, "body": artifact.body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


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


def _tree_path(artifact_id: str) -> str:
    """An injective, ``/``-free, refname-safe tree path for an artifact id.

    Content addresses (hex digests) pass through verbatim for a reviewable layout. Anything else is
    escaped as ``=<hex>``; the ``=`` marker is disjoint from the verbatim namespace (a safe id
    cannot begin with ``=``), so the mapping stays injective — distinct ids never share a path.
    """
    if _SAFE_PATH.match(artifact_id) and artifact_id not in (".", ".."):
        return artifact_id
    return "=" + artifact_id.encode("utf-8").hex()


def _ref_component(value: str) -> str:
    """A refname-safe component: a readable slug plus a full SHA-256 content digest.

    The digest disambiguates distinct inputs even when their slugs collapse to the same slug (e.g.
    ``a/b`` and ``a-b``); the slug keeps refs human-legible for review. The full (untruncated)
    digest is used deliberately — a truncated prefix would reintroduce a birthday-bound collision
    risk between two runs, which would silently merge their artifact sets into one namespace."""
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", value).strip("-.")
    slug = re.sub(r"-{2,}", "-", slug) or "x"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{slug}-{digest}"
