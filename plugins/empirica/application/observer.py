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

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import jsonschema

from core import observer as _core
from core.evaluation import digest
from core.records import Corrupt, Present, RunKey

from . import protocol as _proto
from . import run_state
from .location import encode_handle
from .observation import ObservationUnavailable, build_observation_snapshot
from .snapshot import GraphInvalid, HistoryCorrupt, active_spike_heads, assemble

CONTRACT = "empirica-ui-view/v1"
#: The label of a project no repository matched: a stable word, never a position in the view.
UNNAMED_PROJECT = "Unnamed project"
#: The marker the plugin's pre-v2 state documents carry instead of ``protocol``/``state_schema``.
_PRE_V2_MARKER = "schema"
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


def state_format(raw: Any) -> str:
    """Why a state the plugin does not decode is not decoded, judged with the plugin's own
    identifiers and validator: ``older-format`` when its shape is not this plugin's (another
    protocol or schema, or the current identifiers on a document the current state schema rejects,
    as the plugin's earlier versions wrote), ``unreadable`` otherwise (not a state document, or one
    of the current shape that breaks the plugin's invariants: damage, not age)."""
    if not isinstance(raw, dict):
        return "unreadable"
    current = (raw.get("protocol") == _proto.protocol_id()
               and raw.get("state_schema") == _proto.state_schema_id())
    if not current:
        marked = any(isinstance(raw.get(field), str) and raw[field].startswith("empirica")
                     for field in ("protocol", "state_schema", _PRE_V2_MARKER))
        return "older-format" if marked else "unreadable"
    try:
        _proto.schema_validator("state").validate(raw)
    except jsonschema.ValidationError:
        return "older-format"
    return "unreadable"


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
        self._messages = {code: spec["message"] for code, spec in contract["reasons"].items()}
        self._statuses = frozenset(contract["statuses"])
        self._cache: dict[RunKey, tuple[str, Observed]] = {}

    # --- one run ------------------------------------------------------------

    @staticmethod
    def _partial(row: dict[str, Any], version: str, diagnostic: str, *, status: str | None = None,
                 goal: str | None = None, state: Any = None) -> Observed:
        """A run read only in part: the facts it could be read for, and why not the rest."""
        summary = {**row, "version": version, "state": status, "diagnostic": diagnostic,
                   "audit": None, "root": None, "edges": [], "claims": []}
        detail = {"contract": CONTRACT, "key": row["key"], "version": version, "goal": goal,
                  "budgets": None if state is None else _core.budgets(state),
                  "control": None if state is None else _core.control(state),
                  "audit": None, "residuals": [], "prompt_error": None, "waiting": [], "stale": [], "author": None, "auditor": None,
                  "steps": [], "claims": []}
        return Observed(summary, detail)

    def _observe(self, key: RunKey, row: dict[str, Any], read: Any, history: Any,
                 base_digest: str) -> Observed:
        if not isinstance(read, Present):
            return self._partial(row, base_digest, "unreadable")
        classified = run_state.classify_and_decode(read.value)
        if classified.kind != "valid":
            raw = read.value
            if state_format(raw) != "older-format":
                return self._partial(row, base_digest, "unreadable")
            status = raw.get("status") if raw.get("status") in self._statuses else None
            goal = raw.get("goal") if isinstance(raw.get("goal"), str) else None
            return self._partial(row, base_digest, "older-format", status=status, goal=goal)
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
        try:
            facts, detail = _core.project(snapshot, self._messages)
        except ValueError:
            # A reason that cannot be worded is a fact about this one run, never about the whole store.
            return self._partial(row, base_digest, "unreadable", **partial)
        return Observed({**row, "version": version, "state": state.status, "diagnostic": None, **facts},
                        {"contract": CONTRACT, "key": row["key"], "version": version, **detail},
                        active_spike_heads(snapshot.history, snapshot.graph), snapshot.observation_digest)

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
        row = {"key": run_key(key), "project": key.project_id, "generation": key.generation,
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
                         "repo": meta.repo, "since": min(since, default=None), "runs": runs})
        _tell_apart(rows)
        view = {"contract": CONTRACT,
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


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
#: How finely a moment is told, coarse to fine; a label gets only as fine as it needs to be unique in its view.
_GRAINS = ("%d %b", "%d %b %H:%M", "%d %b %H:%M:%S")


def _unnamed(first_run: float | None, grain: int = 0) -> str:
    """A project no repository matched, told apart from the others by a human fact: when it began (UTC)."""
    if first_run is None:
        return UNNAMED_PROJECT
    moment = time.gmtime(first_run)
    text = f"{moment.tm_mday} {_MONTHS[moment.tm_mon - 1]}"
    if grain >= 1:
        text += f" {moment.tm_hour:02d}:{moment.tm_min:02d}"
    if grain >= 2:
        text += f":{moment.tm_sec:02d}"
    if grain >= 3:
        text += f".{int((first_run % 1) * 1000):03d}"
    return f"{UNNAMED_PROJECT} · since {text}"


def _tell_apart(rows: list[dict[str, Any]]) -> None:
    """Make the labels of the unnamed projects unique within this view by the coarsest of day, minute, second
    or millisecond (of their first run) that makes each label unique. Never a goal (a goal is
    never a title), an id or a position."""
    unnamed = [r for r in rows if r["name"] is None]
    for grain in range(4):
        for row in unnamed:
            row["label"] = _unnamed(row["since"], grain)
        seen: dict[str, int] = {}
        for row in unnamed:
            seen[row["label"]] = seen.get(row["label"], 0) + 1
        duplicated = {label for label, n in seen.items() if n > 1}
        if not duplicated:
            return
        # only the projects still sharing a label are told finer; the rest keep their coarser, shorter one
        unnamed = [r for r in unnamed if r["label"] in duplicated]


def _version(base_digest: str, observation_digest: str) -> str:
    return digest({"base": base_digest, "observations": observation_digest})


def _newest_first(run: Mapping[str, Any]) -> tuple:
    updated = run["updated"]
    return (updated is None, -(updated or 0.0), run["key"])
