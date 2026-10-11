"""Read-only observer: the store as the UI read model (``contracts/empirica-ui-view/v1``).

The observer reads through the plugin's own ports and decides nothing:

* ``run_state.classify_and_decode`` is the only judge of whether a state is current;
* ``snapshot.assemble`` (history and workspace) builds the only evaluation snapshot;
* ``core.observer`` projects it.

It never writes. It calls only ``read``-side methods of what it is given, and a test holds this
against repositories that raise on any write. A run it cannot read fully still appears, with a
``diagnostic`` that says why, instead of being dropped or guessed at.

The adapter supplies a :class:`Source`: where runs are, when each changed, and which repository
(if any) holds a project's history.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from core import governance
from core import observer as _core
from core.evaluation import digest
from core.records import Corrupt, Present, RunKey

from . import protocol as _proto
from . import run_state
from .location import encode_handle
from .observation import ObservationUnavailable, build_observation_snapshot
from dataclasses import replace

from .snapshot import GraphInvalid, HistoryCorrupt, active_spike_heads, assemble, recorded_observations

CONTRACT = "empirica-ui-view/v1"
#: The label of a project no repository matched: a stable word, never a position in the view.
UNNAMED_PROJECT = "Unnamed project"
#: A malformed recorded artifact that ``traverse_history`` admits (it checks only that each is an
#: object) surfaces as one of these while the snapshot is assembled: corrupt history, per run.
_MALFORMED_HISTORY = (HistoryCorrupt, GraphInvalid, KeyError, TypeError, ValueError)


@dataclass(frozen=True)
class Project:
    name: str | None
    repo: str | None


class Source(Protocol):
    """What the adapter knows about storage and repositories. Reads only."""

    def keys(self) -> Iterable[RunKey]: ...
    def read_state(self, key: RunKey) -> Any: ...
    def updated(self, key: RunKey) -> float | None: ...
    def project(self, project_id: str) -> Project: ...
    def refresh(self) -> None:
        """Called once at the start of every observation, before any other method: a source may
        take its cheap per-poll snapshot here (one ref listing per repository)."""
    def history_revision(self, key: RunKey) -> str | None:
        """A cheap stamp that changes whenever ``history(key)`` would; ``None`` exactly when no
        repository holds the run's project."""
    def history(self, key: RunKey) -> Any:
        """The artifact read for ``key``, or ``None`` when no repository holds its project."""
    def workspace(self, key: RunKey) -> Any: ...


def run_key(key: RunKey) -> str:
    return f"{key.project_id}/{key.run_id}/{key.generation}"


def _revision(read: Any) -> str | None:
    return read.revision.token if isinstance(read, Present) else None


@dataclass(frozen=True)
class Observed:
    """One run: its summary row and its detail document, both contract-shaped, plus what its
    freshness was judged against (the active spike heads and their observation digest)."""

    summary: dict[str, Any]
    detail: dict[str, Any]
    heads: tuple = ()
    observation_digest: str | None = None


class Observer:
    """Builds the view. Keeps the last projection per run and reuses it while nothing it was
    projected from changed: the state and history revisions, and, for a run with active spikes,
    the observation digest of the files they depend on (a workspace edit can make a spike stale
    without touching the store). A run's ``version`` binds all three, so a poll re-projects, and a
    consumer refetches, exactly the runs that changed."""

    def __init__(self, source: Source, *, profile_id: str | None = None) -> None:
        self._source = source
        self._profile_id = profile_id or _proto.profile_ids()[0]
        contract = _proto.public_contract()
        self._reasons = contract["reasons"]
        self._cache: dict[RunKey, tuple[str, Observed]] = {}

    # --- one run ------------------------------------------------------------

    @staticmethod
    def _partial(row: dict[str, Any], version: str, diagnostic: str, *, status: str | None = None,
                 goal: str | None = None, state: Any = None) -> Observed:
        """A run read only in part: the facts it could be read for, and why not the rest."""
        summary = {**row, "version": version, "state": status, "diagnostic": diagnostic, "goal": goal,
                   "session": None if state is None else _core.session_fact(governance.plain(state.invocation)),
                   "attention": None, "next": None, "audit": None, "progress": None, "root": None, "edges": None, "claims": None}
        # What was not read is unknown (null), never an empty list that reads as zero.
        detail = {"contract": CONTRACT, "key": row["key"], "version": version, "goal": goal,
                  "governance": None if state is None else _core.governance_view(_core.governance_facts(state, None)),
                  "audit": None, "stop": None, "residuals": None, "now": None, "outstanding": None, "stale": None, "author": None, "auditor": None,
                  "steps": None, "claims": None}
        return Observed(summary, detail)

    def _observe(self, key: RunKey, row: dict[str, Any], read: Any, history: Any,
                 base_digest: str) -> Observed:
        if not isinstance(read, Present):
            return self._partial(row, base_digest, "unreadable")
        classified = run_state.classify_and_decode(read.value)
        if classified.kind != "valid":
            # A document that is not exactly the current state is damage, whatever wrote it: nothing is read from it.
            return self._partial(row, base_digest, "unreadable")
        state = classified.state
        partial = {"status": state.status, "goal": state.goal, "state": state}
        if history is None:
            return self._partial(row, base_digest, "history-unavailable", **partial)
        if isinstance(history, Corrupt):
            return self._partial(row, base_digest, "history-corrupt", **partial)
        if not isinstance(history, Present):
            # The plugin reads this as corrupt (an empty store cannot hold the committed head the
            # state names); for a reader the cause is the repository given, so it says so.
            return self._partial(row, base_digest, "history-missing", **partial)
        handle = encode_handle(key)
        try:
            snapshot = assemble(state, history.value, self._source.workspace(key), run_id=handle,
                                profile_id=self._profile_id,
                                command={"type": "GetRun", "run_id": handle}, require_graph=False)
        except ObservationUnavailable:
            return self._partial(row, base_digest, "workspace-unavailable", **partial)
        except _MALFORMED_HISTORY:
            return self._partial(row, base_digest, "history-corrupt", **partial)
        version = _version(base_digest, snapshot.observation_digest)
        stopped, recorded = None, "recorded"
        if state.status != "active":
            # Two time bases. What a run that ended left unresolved is read from what its last transaction recorded (the
            # workspace as it was then): ``stopped``. Everything current about it (its audit, its claims, its proof, the files
            # that changed) is read from the snapshot as it is now, exactly as the author view reads it.
            then = recorded_observations(snapshot.history, state, active_spike_heads(snapshot.history, snapshot.graph))
            if then is None:
                recorded = "unavailable"
            else:
                stopped = replace(snapshot, observations=then.observations, observation_basis_id=then.basis_id,
                                  observation_digest=then.digest)
        try:
            facts, detail = _core.project(snapshot, self._reasons, stopped=stopped, recorded=recorded)
        except ValueError:
            # A reason that cannot be worded is a fact about this one run, never about the whole store.
            return self._partial(row, base_digest, "unreadable", **partial)
        return Observed({**row, "version": version, "state": state.status, "diagnostic": None, **facts},
                        {"contract": CONTRACT, "key": row["key"], "version": version, **detail},
                        active_spike_heads(snapshot.history, snapshot.graph),
                        snapshot.observation_digest)

    def _unchanged(self, key: RunKey, cached: Observed) -> bool:
        """Whether the files a cached run's freshness was judged against still read the same."""
        if not cached.heads:
            return True
        try:
            current = build_observation_snapshot(cached.heads, self._source.workspace(key))
        except ObservationUnavailable:
            return False
        return current.digest == cached.observation_digest

    def _run(self, key: RunKey, keys: frozenset[RunKey]) -> Observed:
        prior = RunKey(key.project_id, key.run_id, key.generation - 1)
        row = {"key": run_key(key), "project": key.project_id, "run": key.run_id, "generation": key.generation,
               "previous": run_key(prior) if prior in keys else None, "updated": self._source.updated(key)}
        read = self._source.read_state(key)
        base_digest = digest({"state": _revision(read), "history": self._source.history_revision(key),
                              "previous": row["previous"], "updated": row["updated"]})
        cached = self._cache.get(key)
        if cached is not None and cached[0] == base_digest and self._unchanged(key, cached[1]):
            return cached[1]
        observed = self._observe(key, row, read, self._source.history(key), base_digest)
        # An unobservable workspace is a fact about now, not about the store: never cached, so the
        # next poll looks again.
        if observed.summary["diagnostic"] != "workspace-unavailable":
            self._cache[key] = (base_digest, observed)
        else:
            self._cache.pop(key, None)
        return observed

    # --- the store ----------------------------------------------------------

    def observe(self) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        """The store summary and every run's detail, keyed by run key."""
        self._source.refresh()
        keys = frozenset(self._source.keys())
        observed = {key: self._run(key, keys) for key in keys}
        for gone in set(self._cache) - keys:
            del self._cache[gone]
        projects: dict[str, list[dict[str, Any]]] = {}
        for key in sorted(keys, key=lambda k: _newest_first(observed[k].summary)):
            projects.setdefault(key.project_id, []).append(observed[key].summary)
        rows = []
        for project_id, runs in projects.items():
            meta = self._source.project(project_id)
            since = [run["updated"] for run in runs if run["updated"] is not None]
            rows.append({"id": project_id, "name": meta.name, "label": meta.name or UNNAMED_PROJECT,
                         "repo": meta.repo, "since": min(since, default=None), "families": _families(runs),
                         "runs": runs})
        _tell_apart(rows)
        view = {"contract": CONTRACT, "coverage": _coverage(rows),
                "token": digest([[p["id"], p["name"], p["repo"], [[run["key"], run["version"]] for run in p["runs"]]]
                                 for p in rows]),
                "projects": rows}
        return view, {run_key(key): observed[key].detail for key in keys}

    def view(self) -> dict[str, Any]:
        return self.observe()[0]

    def detail(self, key: str) -> dict[str, Any] | None:
        """One run's detail by its view key, projected alone (the rest of the store is only listed, and a
        run whose cached projection is current is not projected again); ``None`` when no such run."""
        self._source.refresh()
        keys = frozenset(self._source.keys())
        found = next((k for k in keys if run_key(k) == key), None)
        return None if found is None else self._run(found, keys).detail


def _parts(repo: str) -> list[str]:
    return [p for p in re.split(r"[\\/]+", repo) if p]


def _tell_apart(rows: list[dict[str, Any]]) -> None:
    """Make every project's label unique within this view. A project is its repository's folder; two folders of the same
    name are told apart by the shortest path suffix that differs (``north/tiles``, ``south/tiles``), only for the projects
    that share one. What still shares a label (the same path under two storage ids, or projects no repository matched) gets a
    short stable key of its storage id, the shortest that differs. Never a goal, a time or a position."""
    depth = {row["id"]: 1 for row in rows}

    def label(row: dict[str, Any]) -> str:
        if row["repo"] is None:
            return UNNAMED_PROJECT
        parts = _parts(row["repo"])
        return "/".join(parts[-min(depth[row["id"]], len(parts)):]) if parts else row["repo"]

    while True:
        shared: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            shared.setdefault(label(row), []).append(row)
        deeper = [row for group in shared.values() if len({r["repo"] for r in group}) > 1 for row in group
                  if row["repo"] is not None and depth[row["id"]] < len(_parts(row["repo"]))]
        if not deeper:
            break
        for row in deeper:
            depth[row["id"]] += 1
    for row in rows:
        row["label"] = label(row)
    for text, group in shared.items():
        if len(group) > 1:
            keys = [row["id"].split("-", 1)[-1] for row in group]
            size = next((n for n in range(6, 65) if len({k[:n] for k in keys}) == len(keys)), 64)
            for row, key in zip(group, keys):
                row["label"] = f"{text} · {key[:size]}"


def _families(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The attempts at each run id, chronologically: by generation, never by a file's modification time."""
    attempts: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        attempts.setdefault(run["run"], []).append(run)
    return [{"run": run_id, "attempts": [r["key"] for r in sorted(group, key=lambda r: r["generation"])]}
            for run_id, group in attempts.items()]


def _coverage(projects: list[dict[str, Any]]) -> dict[str, int]:
    """How much of the store was read, so no consumer reads what was not read as zero: runs, those read fully, those whose
    state is damaged (``unreadable``) and those read only in part (any other diagnostic)."""
    runs = [run for project in projects for run in project["runs"]]
    unreadable = sum(run["diagnostic"] == "unreadable" for run in runs)
    read = sum(run["diagnostic"] is None for run in runs)
    return {"runs": len(runs), "read": read, "unreadable": unreadable, "partial": len(runs) - read - unreadable}


def _version(base_digest: str, observation_digest: str) -> str:
    return digest({"base": base_digest, "observations": observation_digest})


def _newest_first(run: Mapping[str, Any]) -> tuple:
    updated = run["updated"]
    return (updated is None, -(updated or 0.0), run["key"])
