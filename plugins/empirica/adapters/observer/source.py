"""The machine's run store and the user's repositories as an observer ``Source``. Reads only.

* Runs are enumerated from the run repository's layout (``<home>/projects/<p>/runs/<r>/gen-<n>``):
  every canonical generation directory is a run, whether or not its state can be read (the observer
  then reports it ``unreadable``); each state is read through ``FilesystemRunRepository.read``, the same read the plugin uses.
* A project's history is in the repository the bridge recorded for it when it created a run
  (``adapters.state.locations``), read on every poll, so projects that appear while the observer runs
  are found. A ``--repo`` given explicitly also maps by the plugin's own identity,
  ``storage_id(identity.project_id(repo))`` (the key Claude and Codex derive), for runs recorded before
  the bridge kept locations. A project with neither has no readable history (``None``).
* A run's workspace is the directory recorded for it, else its project's repository.
* When a run's writes happened is the timeline the bridge recorded beside its state (``adapters.state.timeline``);
  a file's modification time is never read as a time (a copied store keeps its times).
* A run's version (``stamp``) is cheap: an ``lstat`` of its generation directory, of its ``run.json`` and of its
  ``timeline.jsonl`` (inode, size and change times, compared for equality only: the plugin writes every file there by
  renaming a new one in, which changes the directory; a file's own ``lstat`` also catches a rewrite in place) and its history ref's commit from the poll's one ``for-each-ref`` per repository, of only the project directories whose refs
  signature (``GitArtifactRepository.refs_signature``) changed, and none while nothing did. A warm poll reads no state,
  no history and runs no git.
* Histories are read in batches (``prefetch``: two ``cat-file --batch`` processes per batch, ``read_listed``), each exactly
  what the single read returns; anything the batch cannot read exactly is read singly.
* History is read through ``GitArtifactRepository.read`` and the workspace through
  ``FilesystemWorkspace``, both read paths of the adapters the plugin writes with.
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapters.execution import FilesystemWorkspace
from adapters.git.artifact_repo import GitArtifactRepository, GitError, toplevel
from adapters.state import identity
from adapters.state.locations import Locations
from adapters.state.repository import GEN_PREFIX, FilesystemRunRepository
from adapters.state.timeline import TIMELINE, Timeline
from application.location import storage_id
from application.observer import Project
from core.records import Corrupt, RunKey

@dataclass(frozen=True)
class Repository:
    top: Path
    artifacts: GitArtifactRepository


def generation_of(name: str) -> int | None:
    """The generation a run directory entry names: ``gen-N`` with N >= 1 in canonical spelling
    (``gen-01`` is not ``gen-1``), else ``None``."""
    suffix = name.removeprefix(GEN_PREFIX)
    if suffix == name or not suffix.isdigit() or str(int(suffix)) != suffix or int(suffix) < 1:
        return None
    return int(suffix)


def project_of(top: Path) -> str:
    """The storage project id StartRun derives for a session working in ``top``."""
    return storage_id(identity.project_id(top))


#: The history revision of a run whose repository's refs could not be listed this poll.
UNLISTED = "unlisted"


class StoreSource:
    def __init__(self, home: Path, repos: Iterable[Path] = ()) -> None:
        self._home = Path(home)
        self._runs = FilesystemRunRepository(self._home)
        self._locations = Locations(self._home)
        self._timeline = Timeline(self._home)
        self._given: dict[str, Path] = {}  # project -> the working tree given for it
        for path in repos:
            top = toplevel(Path(path))
            if top is None:
                raise ValueError(f"not a git repository: {path}")
            self._given.setdefault(project_of(top), top)
        self._opened: dict[Path, Repository] = {}  # one adapter per working tree, kept across polls
        self._tops: dict[Path, Path | None] = {}  # a recorded path's working-tree root, asked of git once
        self._repos: dict[str, Repository] = {}
        self._listings: dict[Path, dict[str, str] | None] = {}  # this poll's refs and commits, per working tree
        self._prefetched: dict[RunKey, tuple[str, Any]] = {}  # a run's history read in a batch, at its listed commit
        self._workspaces: dict[Path, Any] = {}  # this poll's workspace adapters, one per directory
        self._dirs: dict[str, tuple[Any, list[str]]] = {}  # a directory's lstat and its subdirectories, last listed
        self._generations: dict[RunKey, str] = {}  # each run's directory, as a string (a stamp is taken per run per poll)
        self._runkeys: dict[str, tuple[Any, list[RunKey]]] = {}  # a run directory's lstat and its generations
        self._refs: dict[RunKey, str] = {}  # each run's history ref name (a function of its key)
        self._signed: dict[Path, Any] = {}  # each repository's refs signature when it was last listed
        self._match()

    @property
    def home(self) -> Path:
        return self._home

    def _open(self, top: Path) -> Repository:
        if top not in self._opened:
            self._opened[top] = Repository(top, GitArtifactRepository(top))
        return self._opened[top]

    def _match(self) -> None:
        """Each project's repository: the one given for it, else the one recorded for it while it
        still is a directory (a recorded one that has gone or moved is no repository). Git resolves
        each recorded path once; a poll then costs one directory check per record."""
        recorded = {}
        for project, path in self._locations.projects():
            if not path.is_dir():
                self._tops.pop(path, None)  # asked again if it comes back
                continue
            if path not in self._tops:
                self._tops[path] = toplevel(path)
            if self._tops[path] is not None:
                recorded[project] = self._tops[path]
        self._repos = {project: self._open(top) for project, top in {**recorded, **self._given}.items()}

    def keys(self) -> Iterator[RunKey]:
        """Every run generation directory, in name order. Symlinks are not followed (a directory entry's own type). A
        directory whose ``lstat`` is unchanged since the last poll has the same entries (adding, removing or renaming
        one changes it), so only a changed directory is listed again."""
        for project in self._listdir(str(self._home / "projects")):
            runs = project + "/runs"
            if os.path.islink(runs):
                continue
            for run in self._listdir(runs):
                yield from self._generations_of(run)

    def _generations_of(self, run: str) -> list[RunKey]:
        """The run keys of one run directory, listed again only when the directory changed."""
        signature = _signature(run)
        known = self._runkeys.get(run)
        if known is not None and known[0] == signature and signature is not None:
            return known[1]
        found = []
        for generation in [e.path for e in _directories(run)]:
            n = generation_of(generation.rsplit("/", 1)[1])
            if n is not None:
                _, project_id, _, run_id, _ = generation.rsplit("/", 4)
                found.append(RunKey(project_id, run_id, n))
        self._runkeys[run] = (signature, found)
        return found

    def _listdir(self, path: str) -> list[str]:
        signature = _signature(path)
        known = self._dirs.get(path)
        if known is not None and known[0] == signature and signature is not None:
            return known[1]
        entries = [e.path for e in _directories(path)]
        self._dirs[path] = (signature, entries)
        return entries

    def read_state(self, key: RunKey) -> Any:
        try:
            return self._runs.read(key)
        except (OSError, ValueError) as exc:  # a refused symlink or an unsafe segment
            return Corrupt(str(exc))

    def timeline(self, key: RunKey):
        """When the run's writes happened, as the bridge recorded them beside its state (``None`` when unknown)."""
        return self._timeline.read(key)

    def project(self, project_id: str) -> Project:
        repo = self._repos.get(project_id)
        return Project(repo.top.name, str(repo.top)) if repo else Project(None, None)

    def refresh(self) -> None:
        """The recorded locations again, then one ``for-each-ref`` per repository (never per project or run): every
        history ref's commit, for this poll."""
        self._match()
        listings, self._listings = self._listings, {}
        self._workspaces = {}
        for repo in {repo.top: repo for repo in self._repos.values()}.values():
            signature = repo.artifacts.refs_signature()
            known, last = self._signed.get(repo.top), listings.get(repo.top)
            try:
                if signature is None or known is None or last is None or any(
                        known.get(name) != signature.get(name) for name in repo.artifacts.REFS_GLOBAL):
                    listing = repo.artifacts.ref_heads()
                else:
                    # Only refs in a project directory that changed can have moved (or one that came or went): those
                    # are listed again, in one for-each-ref, and every other ref is as last listed.
                    moved = {name for name in known.keys() | signature.keys() if name.count("/") == 4
                             and known.get(name) != signature.get(name)}
                    listing = last if not moved else {
                        **{ref: commit for ref, commit in last.items() if ref.rsplit("/", 1)[0] not in moved},
                        **repo.artifacts.ref_heads_under(moved)}
                self._listings[repo.top] = listing
                self._signed[repo.top] = signature
            except GitError:
                self._listings[repo.top] = None
                self._signed.pop(repo.top, None)

    def _listed(self, key: RunKey) -> tuple[Repository | None, str | None | bool]:
        """The run's repository and its ref as this poll listed it (``False`` when the listing failed)."""
        repo = self._repos.get(key.project_id)
        if repo is None:
            return None, None
        listing = self._listings.get(repo.top)
        if listing is None:
            return repo, False
        ref = self._refs.get(key)
        if ref is None:
            ref = self._refs[key] = repo.artifacts.ref_for(key)
        return repo, listing.get(ref)

    def history_revision(self, key: RunKey) -> str | None:
        repo, listed = self._listed(key)
        if repo is None:
            return None
        if listed is False:
            # The listing failed: published as is (the same damage, the same version), and the run's private ``stamp``
            # makes it read again every poll (``history()`` then reads and reports what it finds).
            return UNLISTED
        return "absent" if listed is None else listed

    @staticmethod
    def _stamped_history(revision: str | None) -> Any:
        """A history revision as a stamp holds it: an unlisted one never equals another (a fresh object, compared by
        identity), so a run whose listing failed is read again every poll. Private: no stamp is ever published."""
        return object() if revision == UNLISTED else revision

    def stamp(self, key: RunKey) -> Any:
        """The run's version, cheaply: the ``lstat`` of its generation directory, of its ``run.json`` and of its
        ``timeline.jsonl`` (equality only, never a time) and its history ref's commit. The plugin writes ``run.json``,
        ``timeline.jsonl`` and ``workspace.json`` only by renaming a new file into the directory (``fsio.atomic_write``),
        which changes the directory; the two files' own ``lstat`` also catch a rewrite in place (a corruption, a copy, a
        hand edit), the damage the observer must show."""
        directory = self._generations.get(key)
        if directory is None:
            try:
                directory = self._generations[key] = str(self._runs.generation_dir(key))
            except (OSError, ValueError):
                return ("unsafe",)
        repo = self._repos.get(key.project_id)
        return (*_files(directory), None if repo is None else repo.top, self._stamped_history(self.history_revision(key)))

    def stamps(self) -> dict[RunKey, Any]:
        """Every run's ``stamp``, in ``keys`` order, in one walk: ``stamp`` for each run, with what is per project (its
        repository and that repository's listing) looked up once."""
        out: dict[RunKey, Any] = {}
        projects: dict[str, tuple[Any, Any, Any]] = {}
        refs, generations = self._refs, self._generations
        for key in self.keys():
            per = projects.get(key.project_id)
            if per is None:
                repo = self._repos.get(key.project_id)
                listing = None if repo is None else self._listings.get(repo.top)
                per = projects[key.project_id] = (repo, None if repo is None else repo.top, listing)
            repo, top, listing = per
            directory = generations.get(key)
            if directory is None:
                out[key] = self.stamp(key)
                continue
            if repo is None:
                history = None
            elif listing is None:
                history = self._stamped_history(self.history_revision(key))  # failed: a stamp that never repeats
            else:
                ref = refs.get(key)
                if ref is None:
                    ref = refs[key] = repo.artifacts.ref_for(key)
                history = listing.get(ref, "absent")
            out[key] = (*_files(directory), top, history)
        return out

    def prefetch(self, keys: Iterable[RunKey]) -> None:
        """Read the histories of ``keys`` in one batch per repository, for ``history`` to return."""
        wanted: dict[Path, list[tuple[RunKey, str]]] = {}
        for key in keys:
            repo, listed = self._listed(key)
            already = self._prefetched.get(key)
            if repo is not None and listed and (already is None or already[0] != listed):
                wanted.setdefault(repo.top, []).append((key, listed))
        for top, runs in wanted.items():
            try:
                read = self._opened[top].artifacts.read_listed({listed for _, listed in runs})
            except (GitError, OSError):
                continue
            for key, listed in runs:
                if listed in read:
                    self._prefetched[key] = (listed, read[listed])

    def history(self, key: RunKey) -> Any:
        repo, listed = self._listed(key)
        if repo is None:
            return None
        batched = self._prefetched.pop(key, None)
        if batched is not None and listed and batched[0] == listed:
            return batched[1]
        try:
            return repo.artifacts.read(key)
        except GitError as exc:
            return Corrupt(str(exc))

    def workspace(self, key: RunKey) -> Any:
        """The run's recorded workspace, else its repository's working tree; ``None`` when there is
        neither or it has gone (the observer reports a run that then needs it as
        ``workspace-unavailable``)."""
        repo = self._repos.get(key.project_id)
        root = self._locations.workspace(key) or (repo.top if repo else None)
        if root not in self._workspaces:  # one adapter per directory within a poll, so its reads are shared
            try:
                self._workspaces[root] = FilesystemWorkspace(root) if root is not None and root.is_dir() else None
            except OSError:
                self._workspaces[root] = None
        return self._workspaces[root]


def _directories(path: str) -> list[os.DirEntry]:
    """The real directories (not symlinks) in ``path``, by name; none when it is not a readable directory."""
    try:
        with os.scandir(path) as entries:
            return sorted((e for e in entries if e.is_dir(follow_symlinks=False)), key=lambda e: e.name)
    except OSError:
        return []


def _files(directory: str) -> tuple[Any, Any, Any]:
    """What a run's stamp reads of its generation directory: the directory's signature, its state's and its timeline's."""
    return _signature(directory), _signature(directory + "/run.json"), _signature(directory + "/" + TIMELINE)


def _signature(path: str) -> tuple[int, int, int, int] | None:
    """What says a file was rewritten, compared for equality only (never read as a time): its inode, size and
    modification and change times. ``None`` when it is not there."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns
