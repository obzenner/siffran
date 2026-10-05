"""Where each run's work happens: a sidecar the bridge writes when it creates a run.

    <home>/projects/<project_id>/project.json                          {"repo": "<working tree root>"}
    <home>/projects/<project_id>/runs/<run_id>/gen-<n>/workspace.json  {"workspace": "<directory>"}

``repo`` is the working tree whose Git common directory holds the project's knowledge store (the
artifact refs); ``workspace`` is the directory the run's spikes are observed in. A reader (the
observer) uses them to find every run's history and judge its freshness without being told where,
whatever selector a host derived the project id from.

No run decision reads these files, and they sit outside the strict run state: the run document and
its schema are unchanged. Writes are atomic and idempotent (an unchanged file is not rewritten);
reads refuse symlinks and treat anything malformed as unrecorded.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from core.records import RunKey

from . import fsio
from .repository import FilesystemRunRepository, _safe_segment, empirica_home, is_safe_segment

PROJECT = "project.json"
WORKSPACE = "workspace.json"


class Locations:
    def __init__(self, home: Path | None = None) -> None:
        self._home = Path(home) if home is not None else empirica_home()
        self._runs = FilesystemRunRepository(self._home)

    def _project(self, project_id: str) -> Path:
        return self._home / "projects" / _safe_segment(project_id, "project_id") / PROJECT

    def _workspace(self, key: RunKey) -> Path:
        return self._runs.generation_dir(key) / WORKSPACE

    # --- writing (the bridge, at run creation) ---------------------------------

    def record(self, key: RunKey, repo: Path | None, workspace: Path) -> None:
        """Both records, each on its own: one that cannot be written does not cost the other.
        Raises the first failure (``OSError`` or ``ValueError``) after trying both."""
        writes = [(self._workspace, key, {"workspace": str(workspace)})]
        if repo is not None:
            writes.append((self._project, key.project_id, {"repo": str(repo)}))
        failure = None
        for where, of, value in writes:
            try:
                self._write(where(of), value)
            except (OSError, ValueError) as exc:
                failure = failure or exc
        if failure is not None:
            raise failure

    @staticmethod
    def _write(path: Path, value: dict[str, str]) -> None:
        raw = fsio.dump_json(value)
        if fsio.read_bytes(path) != raw:
            fsio.atomic_write(path, raw)

    # --- reading (the observer) -------------------------------------------------

    def projects(self) -> Iterator[tuple[str, Path]]:
        """Every project with a recorded repository, and that repository."""
        root = self._home / "projects"
        if not root.is_dir():
            return
        for project in sorted(root.iterdir()):
            if is_safe_segment(project.name) and project.is_dir() and not project.is_symlink():
                repo = self.repo(project.name)
                if repo is not None:
                    yield project.name, repo

    def repo(self, project_id: str) -> Path | None:
        return self._read(lambda: self._project(project_id), "repo")

    def workspace(self, key: RunKey) -> Path | None:
        return self._read(lambda: self._workspace(key), "workspace")

    @staticmethod
    def _read(where, field: str) -> Path | None:
        """``field`` of the record at ``where()``; ``None`` for an unsafe name (``where`` raises
        ``ValueError``), a refused symlink, an unreadable file, or anything malformed."""
        try:
            raw = fsio.read_bytes(where())
            value = json.loads(raw) if raw is not None else None
        except (OSError, ValueError):
            return None
        recorded = value.get(field) if isinstance(value, dict) else None
        return Path(recorded) if isinstance(recorded, str) and Path(recorded).is_absolute() else None
