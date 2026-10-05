"""The machine's run store and the user's repositories as an observer ``Source``. Reads only.

* Runs are enumerated from the run repository's layout (``<home>/projects/<p>/runs/<r>/gen-<n>``);
  each state is read through ``FilesystemRunRepository.read``, the same read the plugin uses.
* A project's history is in the repository the bridge recorded for it when it created a run
  (``adapters.state.locations``), read on every poll, so projects that appear while the observer runs
  are found. A ``--repo`` given explicitly also maps by the plugin's own identity,
  ``storage_id(identity.project_id(repo))`` (the key Claude and Codex derive), for runs recorded before
  the bridge kept locations. A project with neither has no readable history (``None``).
* A run's workspace is the directory recorded for it, else its project's repository.
* History is read through ``GitArtifactRepository.read`` and the workspace through
  ``FilesystemWorkspace``, both read paths of the adapters the plugin writes with.
"""
from __future__ import annotations

import os
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapters.execution import FilesystemWorkspace
from adapters.git.artifact_repo import GitArtifactRepository, GitError, toplevel
from adapters.state import identity
from adapters.state.locations import Locations
from adapters.state.repository import DOC_NAME, GEN_PREFIX, FilesystemRunRepository
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


class StoreSource:
    def __init__(self, home: Path, repos: Iterable[Path] = ()) -> None:
        self._home = Path(home)
        self._runs = FilesystemRunRepository(self._home)
        self._locations = Locations(self._home)
        self._given: dict[str, Path] = {}  # project -> the working tree given for it
        for path in repos:
            top = toplevel(Path(path))
            if top is None:
                raise ValueError(f"not a git repository: {path}")
            self._given.setdefault(project_of(top), top)
        self._opened: dict[Path, Repository] = {}  # one adapter per working tree, kept across polls
        self._tops: dict[Path, Path | None] = {}  # a recorded path's working-tree root, asked of git once
        self._repos: dict[str, Repository] = {}
        self._heads: dict[str, dict[str, str] | None] = {}
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
        projects = self._home / "projects"
        if not projects.is_dir():
            return
        for project in sorted(projects.iterdir()):
            runs = project / "runs"
            if not runs.is_dir() or project.is_symlink() or runs.is_symlink():
                continue
            for run in sorted(runs.iterdir()):
                if run.is_symlink() or not run.is_dir():
                    continue
                for generation in sorted(run.iterdir()):
                    n = generation_of(generation.name)
                    if n is not None and not generation.is_symlink() and (generation / DOC_NAME).is_file():
                        yield RunKey(project.name, run.name, n)

    def read_state(self, key: RunKey) -> Any:
        try:
            return self._runs.read(key)
        except (OSError, ValueError) as exc:  # a refused symlink or an unsafe segment
            return Corrupt(str(exc))

    def updated(self, key: RunKey) -> float | None:
        try:
            return os.lstat(self._runs.generation_dir(key) / DOC_NAME).st_mtime
        except (OSError, ValueError):  # gone, or an unsafe segment
            return None

    def project(self, project_id: str) -> Project:
        repo = self._repos.get(project_id)
        return Project(repo.top.name, str(repo.top)) if repo else Project(None, None)

    def refresh(self) -> None:
        """The recorded locations again, then one ``for-each-ref`` per repository: every history ref's
        commit, for this poll."""
        self._match()
        self._heads = {}
        for project, repo in self._repos.items():
            try:
                self._heads[project] = repo.artifacts.ref_heads()
            except GitError:
                self._heads[project] = None

    def history_revision(self, key: RunKey) -> str | None:
        repo = self._repos.get(key.project_id)
        if repo is None:
            return None
        heads = self._heads.get(key.project_id)
        if heads is None:
            # The listing failed, so nothing says whether the history moved: a stamp that never repeats
            # makes the run re-read every poll (history() then reads and reports what it finds).
            return f"unlisted:{time.monotonic_ns()}"
        return heads.get(repo.artifacts.ref_for(key), "absent")

    def history(self, key: RunKey) -> Any:
        repo = self._repos.get(key.project_id)
        if repo is None:
            return None
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
        try:
            return FilesystemWorkspace(root) if root is not None and root.is_dir() else None
        except OSError:
            return None
