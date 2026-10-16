"""Read-only observer: the store as the UI read model (``contracts/empirica-ui-view/v1``).

The observer reads through the plugin's own ports and decides nothing:

* ``run_state.classify_and_decode`` is the only judge of whether a state is current;
* ``snapshot.assemble`` (history and workspace) builds the only evaluation snapshot;
* ``core.observer`` projects it.

It never writes. It calls only ``read``-side methods of what it is given, and a test holds this
against repositories that raise on any write. A run it cannot read fully still appears, with a
``diagnostic`` that says why, instead of being dropped or guessed at.

The adapter supplies a :class:`Source`: where runs are, each run generation's timeline (when its writes
happened, ADR-0070), and which repository (if any) holds a project's history.

Times are labels, never facts the plugin decided on: a time is published only for a write whose own timeline entry
exists and chains to the entry of the write before it (its ``previous_revision`` is that entry's ``revision``), and
otherwise it is ``null``. No artifact is ever given another write's time.

At volume (contract v1) the observer is an incremental index: a poll compares each run's stamp (``Source.stamp``, or the
revisions it is projected from) with the one it was projected at, and re-projects only the runs whose stamp changed. A
poll may be given a time budget: the runs it has not reached yet are counted as ``pending`` in the coverage, so a cold
start grows ``read`` against ``runs`` and never shows a false total. The store-wide facts (facets, aggregates, marks,
pages, search) are ``application.observer_index``'s, computed from the published rows only.

Each ``index`` publishes what it has read as one immutable view. The adapter may index in the background
(``serve_from_index``, ``adapters.observer.indexer``): every contract read then answers from the last published view, and a
request never pays for indexing.
"""
from __future__ import annotations

import re
import pickle
import threading
import zlib
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from core import governance
from core import observer as _core
from core.evaluation import digest
from core.records import Corrupt, Present, RunKey

from . import observer_index as _index
from .observer_drift import DriftIndex, indexable
from . import protocol as _proto
from . import run_state
from .location import encode_handle
from .observation import ObservationUnavailable, build_observation_snapshot
from dataclasses import replace

from .snapshot import (GraphInvalid, HistoryCorrupt, active_spike_heads, assemble, index_artifacts, manifest_chain,
                       recorded_observations)

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
    def timeline(self, key: RunKey) -> tuple[Mapping[str, Any], ...] | None:
        """The generation's recorded writes (``adapters.state.timeline``), in append order; ``None`` when unknown."""
    def project(self, project_id: str) -> Project: ...
    def refresh(self) -> None:
        """Called once at the start of every observation, before any other method: a source may
        take its cheap per-poll snapshot here (one ref listing per repository)."""
    def history_revision(self, key: RunKey) -> str | None:
        """A cheap stamp that changes whenever ``history(key)`` would; ``None`` exactly when no
        repository holds the run's project."""
    # Optional: ``stamp(key)`` (a cheap version that changes whenever any read of the run would) and ``prefetch(keys)``
    # (read these runs' histories together, before they are read one by one).
    def history(self, key: RunKey) -> Any:
        """The artifact read for ``key``, or ``None`` when no repository holds its project."""
    def workspace(self, key: RunKey) -> Any: ...
    # Optional: ``stamp(key)``, a cheap value that changes whenever anything a run is projected from changes (its state,
    # history or timeline). Without it the observer composes one from the revisions (``Observer._stamp``).


def run_key(key: RunKey) -> str:
    return f"{key.project_id}/{key.run_id}/{key.generation}"


def _revision(read: Any) -> str | None:
    return read.revision.token if isinstance(read, Present) else None


@dataclass(frozen=True)
class Times:
    """When a run's recorded writes happened, as far as its timeline shows each write exactly (ADR-0070).

    A timeline is trusted as one linear chain, in the order it was appended: entry 1 is the only create (no
    ``previous_revision``) and each entry N names entry N-1's ``revision`` as its previous one, with a revision never
    seen before. The chain is the longest prefix that holds; where it breaks (a fork, a create out of order or a second
    one, a backlink, a missing entry) that entry and every later one are unknown, never borrowed. ``by_head`` maps the
    manifest a trusted write committed (its ``committed_artifact_head_id``) to that write's entry, unless two trusted
    writes name the same head (ambiguous: unknown)."""

    by_head: Mapping[str, Mapping[str, Any]]
    started: float | None

    @staticmethod
    def chain(entries: Iterable[Mapping[str, Any]] | None) -> tuple[Mapping[str, Any], ...]:
        """The trusted prefix of ``entries``: the longest one that is a single linear chain from its create."""
        trusted: list[Mapping[str, Any]] = []
        seen: set = set()
        for e in entries or ():
            expected = trusted[-1]["revision"] if trusted else None
            if e["previous_revision"] != expected or e["revision"] in seen or e["revision"] is None:
                break
            trusted.append(e)
            seen.add(e["revision"])
        return tuple(trusted)

    @classmethod
    def of(cls, entries: Iterable[Mapping[str, Any]] | None) -> "Times":
        trusted = cls.chain(entries)
        heads = Counter(e["artifact_head"] for e in trusted)
        by_head = {e["artifact_head"]: e for e in trusted if e["artifact_head"] is not None and heads[e["artifact_head"]] == 1}
        return cls(by_head, trusted[0]["at"] if trusted else None)

    def first(self, holds) -> float | None:
        """The time of the write that made ``holds`` true of the run's configuration: the latest trusted entry for which
        it holds whose own predecessor (a trusted entry) did not, or the create when it holds there. ``None`` when no
        such write is known exactly (its predecessor's entry is missing, so it may have held before)."""
        by_revision = {e["revision"]: e for e in self.by_head.values()}
        found = [e for e in self.by_head.values() if holds(e) and (
            e["previous_revision"] is None or (e["previous_revision"] in by_revision
                                               and not holds(by_revision[e["previous_revision"]])))]
        return max(found, key=lambda e: e["seq"])["at"] if found else None

    def configuration(self, state: Any) -> dict[str, Any]:
        """When the state's configuration was recorded: the write that proposed its current plan (``proposal``) and
        the write that recorded each of its receipts (``receipts``, by id)."""
        gov = state.governance
        current = (gov["plan_revision"], gov["proposal_digest"])
        return {"proposal": self.first(lambda e: (e["plan_revision"], e["proposal_digest"]) == current),
                "receipts": {r["id"]: self.first(lambda e, rid=r["id"]: rid in (e["receipts"] or ()))
                             for r in gov["receipts"]}}

    def at(self, head: str | None) -> float | None:
        """The time of the write that committed manifest ``head``; ``None`` when it was not recorded exactly."""
        found = self.by_head.get(head) if head is not None else None
        return None if found is None else found["at"]

    def run(self, state: Any) -> dict[str, float | None]:
        """``started``, ``updated`` (the write that made the current state) and ``ended`` (the write that set the
        terminal status the state has, from an active one) of a decoded state."""
        ended = None
        if state.status != "active":
            by_revision = {e["revision"]: e for e in self.by_head.values()}
            ends = [e for e in self.by_head.values() if e["status"] == state.status
                    and by_revision.get(e["previous_revision"], {}).get("status") == "active"]
            ended = min(ends, key=lambda e: e["seq"])["at"] if ends else None
        return {"started": self.started, "updated": self.at(state.committed_artifact_head_id), "ended": ended}

    def steps(self, state: Any, stored: Any, history: tuple[Mapping[str, Any], ...]) -> tuple[float | None, ...]:
        """Each history artifact's time: that of the write whose manifest first committed it."""
        by_id = index_artifacts(stored)
        owner = {aid: manifest["artifact_id"] for manifest in manifest_chain(state.committed_artifact_head_id, by_id)
                 for aid in manifest["artifact_ids"]}
        return tuple(self.at(owner.get(item["artifact_id"])) for item in history)


UNKNOWN_TIMES = {"started": None, "updated": None, "ended": None}


@dataclass(frozen=True)
class Observed:
    """One run: its summary row and its detail document, both contract-v1-shaped, plus what its
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

    def __init__(self, source: Source, *, profile_id: str | None = None, clock=None, zones=None) -> None:
        """``clock`` (a monotonic seconds reader the adapter passes, e.g. ``time.monotonic``) bounds a poll's
        ``budget``; this layer reads no clock of its own, and without one every poll is complete. ``zones`` (the adapter's
        ``adapters.observer.civil.zone``) gives a time zone's ``day_of`` for the calendar, or ``ValueError``; without
        it only ``UTC`` is known."""
        self._source = source
        self._zones = zones or _index.utc_only
        self._profile_id = profile_id or _proto.profile_ids()[0]
        contract = _proto.public_contract()
        self._reasons = contract["reasons"]
        self._cache: dict[RunKey, tuple[Any, str, Observed]] = {}
        self._clock = clock
        self._keys: frozenset[RunKey] = frozenset()
        self._order: list[RunKey] = []
        self._changes = 0           # bumps whenever a run's projection or the set of runs changes
        self._built: tuple | None = None  # (changes, rows in page order, labels, projects) for the contract views
        self.counts: Counter = Counter()  # operations, by kind: what a pass cost (``projections``, ``hashed_files`` ...)
        self._drift = DriftIndex(self.counts)
        self._dirty: set[RunKey] = set()  # runs whose bound files changed since they were projected
        self._inputs: dict[RunKey, tuple[Any, bytes]] = {}  # each run's inputs as last read, by the stamp read at
        self._tally = _index.Tally(self.counts)  # the store-wide counts, kept one run at a time
        self._summaries: dict[str, dict[str, Any]] = {}  # each indexed run's row, by key
        self._facts_by_key: dict[str, dict[str, list]] = {}  # each indexed run's countable facts, by key
        self._project_ids: list[str] = []
        self._lock = threading.RLock()  # one indexer at a time; published views are read without it
        self._published: _View | None = None
        self._ready = threading.Event()
        self._resume: list[RunKey] = []  # the stale runs a budgeted poll did not reach, for the next one
        self._pending = self._reindexing = 0  # of them, the never read and the read before (known stale)
        self._published_at = float("-inf")
        self._bulk: Mapping[RunKey, Any] | None = None  # this poll's stamps, when the source gives them all at once
        self._indexed = False  # True once the adapter indexes in the background (``serve_from_index``)

    # --- one run ------------------------------------------------------------

    @staticmethod
    def _partial(row: dict[str, Any], version: str, diagnostic: str, *, status: str | None = None,
                 goal: str | None = None, state: Any = None, times: Times | None = None) -> Observed:
        """A run read only in part: the facts it could be read for, and why not the rest."""
        when = UNKNOWN_TIMES if state is None or times is None else times.run(state)
        summary = {**row, **when, "version": version, "state": status, "diagnostic": diagnostic, "goal": goal,
                   "session": None if state is None else _core.session_fact(governance.plain(state.invocation)),
                   "mode": None if state is None else state.governance["control_mode"],
                   "standing": _core.standing(None, None, None, False), "ask": None, "asked_at": None, "flags": None,
                   "attention": None, "next": None, "audit": None, "progress": None, "root": None, "edges": None, "claims": None}
        # What was not read is unknown (null), never an empty list that reads as zero.
        detail = {"contract": CONTRACT, "key": row["key"], "version": version, "goal": goal,
                  "spike_backend": None if state is None else governance.plain(state.spike_backend),
                  "governance": None if state is None else _core.governance_view(_core.governance_facts(state, None)),
                  "audit": None, "stop": None, "residuals": None, "now": None, "outstanding": None, "stale": None, "author": None, "auditor": None,
                  "audits": None, "dropped": None, "decisions": None, "steps": None, "claims": None}
        return Observed(summary, detail)

    def _observe(self, key: RunKey, row: dict[str, Any], read: Any, history: Any,
                 base_digest: str, times: Times) -> Observed:
        if not isinstance(read, Present):
            return self._partial(row, base_digest, "unreadable")
        classified = run_state.classify_and_decode(read.value)
        if classified.kind != "valid":
            # A document that is not exactly the current state is damage, whatever wrote it: nothing is read from it.
            return self._partial(row, base_digest, "unreadable")
        state = classified.state
        partial = {"status": state.status, "goal": state.goal, "state": state, "times": times}
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
            workspace = self._source.workspace(key)
            snapshot = assemble(state, history.value, workspace, run_id=handle,
                                profile_id=self._profile_id,
                                command={"type": "GetRun", "run_id": handle}, require_graph=False,
                                observe=(lambda heads: self._drift.observe(key, workspace, heads))
                                if indexable(workspace) else None)
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
            at = times.steps(state, history.value, snapshot.history)
            facts, detail = _core.project(snapshot, self._reasons, stopped=stopped, recorded=recorded, times=at,
                                          configured=times.configuration(state))
        except ValueError:
            # A reason that cannot be worded is a fact about this one run, never about the whole store.
            return self._partial(row, base_digest, "unreadable", **partial)
        return Observed({**row, **times.run(state), "version": version, "state": state.status, "diagnostic": None,
                         **facts},
                        {"contract": CONTRACT, "key": row["key"], "version": version, **detail},
                        active_spike_heads(snapshot.history, snapshot.graph),
                        snapshot.observation_digest)

    def _unchanged(self, key: RunKey, cached: Observed) -> bool:
        """Whether the files a cached run's freshness was judged against still read the same: the drift index says so
        for an indexed workspace (``_dirty``); a workspace that cannot be indexed is observed again."""
        if key in self._dirty:
            return False
        if not cached.heads or self._drift.bound(key) is not None:
            return True
        try:
            return build_observation_snapshot(cached.heads, self._source.workspace(key)).digest == cached.observation_digest
        except ObservationUnavailable:
            return False

    def _begin(self) -> None:
        """A pass begins: the source's per-poll snapshot, then the drift index's sweep of every bound file's signature."""
        self._source.refresh()
        current = {}
        for wsid, keys in self._drift.workspaces().items():
            # The workspace as the source gives it this pass, asked of the runs bound to it until one names it (a run
            # whose workspace moved names another; it was written, so it is projected again anyway).
            current[wsid] = next((ws for ws in (self._source.workspace(key) for key in keys)
                                  if indexable(ws) and ws.drift_id() == wsid), None)
        if current:
            self._dirty |= self._drift.sweep(current)

    def _previous(self, key: RunKey, keys: frozenset[RunKey]) -> str | None:
        if key.generation <= 1:
            return None  # no attempt comes before the first
        prior = RunKey(key.project_id, key.run_id, key.generation - 1)
        return run_key(prior) if prior in keys else None

    def _stamp(self, key: RunKey, previous: str | None) -> Any:
        """What decides whether a cached projection still holds, cheaply: the source's own stamp when it has one, else
        the state's and history's revisions and the timeline as read. ``previous`` (whether the prior attempt is listed)
        is part of it."""
        if self._bulk is not None and key in self._bulk:
            return previous, self._bulk[key]
        stamp = getattr(self._source, "stamp", None)
        if stamp is not None:
            return previous, stamp(key)
        entries = self._source.timeline(key)
        return (previous, _revision(self._source.read_state(key)), self._source.history_revision(key),
                None if entries is None else (len(entries), entries[-1]["revision"] if entries else None,
                                              entries[-1]["at"] if entries else None))

    def _inputs_kept(self, key: RunKey, keys: frozenset[RunKey]) -> bool:
        kept = self._inputs.get(key)
        return kept is not None and kept[0] == self._stamp(key, self._previous(key, keys))

    def _inputs_of(self, key: RunKey, stamp: Any) -> tuple[Any, Any, Any, Any]:
        """The run's state, timeline, history and history revision: read from the source when the run was written
        since they were last read (its stamp moved), else kept from that read (a run projected again only because a
        file it binds changed reads nothing). Kept compressed (``zlib`` over ``pickle``), a few kilobytes a run; an
        input that cannot be kept is read again next time."""
        kept = self._inputs.get(key)
        if kept is not None and kept[0] == stamp:
            return pickle.loads(zlib.decompress(kept[1]))
        inputs = (self._source.read_state(key), self._source.timeline(key), self._source.history(key),
                  self._source.history_revision(key))
        try:
            self._inputs[key] = (stamp, zlib.compress(pickle.dumps(inputs, pickle.HIGHEST_PROTOCOL), 1))
        except Exception:  # noqa: BLE001 - a source's object that does not pickle is simply read again
            self._inputs.pop(key, None)
        return inputs

    def _fresh(self, key: RunKey, keys: frozenset[RunKey]) -> Observed | None:
        """The cached projection of ``key`` while nothing it was projected from changed, else ``None``."""
        cached = self._cache.get(key)
        if cached is None or cached[0] != self._stamp(key, self._previous(key, keys)):
            return None
        # An unobservable workspace is a fact about now, not about the store: listed (a partial row, counted), but looked
        # at again by every poll.
        if cached[2].summary["diagnostic"] == "workspace-unavailable":
            return None
        return cached[2] if self._unchanged(key, cached[2]) else None

    def _run(self, key: RunKey, keys: frozenset[RunKey], *, stale: bool = False) -> Observed:
        found = None if stale else self._fresh(key, keys)
        if found is not None:
            return found
        previous = self._previous(key, keys)
        stamp = self._stamp(key, previous)
        self._dirty.discard(key)
        self.counts["projections"] += 1
        row = {"key": run_key(key), "id": _index.short_id(run_key(key)), "project": key.project_id, "run": key.run_id,
               "generation": key.generation, "previous": previous, **UNKNOWN_TIMES}
        read, entries, history, revision = self._inputs_of(key, stamp)
        base_digest = digest({"state": _revision(read), "history": revision, "previous": previous, "timeline": entries})
        self._drift.begin(key)  # projected again: bound to exactly what it binds now, and nothing it no longer binds
        try:
            observed = self._observe(key, row, read, history, base_digest, Times.of(entries))
        finally:
            self._drift.end(key)
        self._changes += 1
        self._account(key, observed)
        self._cache[key] = (stamp, base_digest, observed)
        return observed

    # --- the store ----------------------------------------------------------

    def _forget(self, keys: frozenset[RunKey]) -> None:
        if keys != self._keys:
            self._changes += 1
            for gone in set(self._cache) - keys:
                self._account(gone, None)
                del self._cache[gone]
            self._dirty &= keys
            for gone in set(self._inputs) - keys:
                del self._inputs[gone]
            for gone in self._drift.keys() - keys:
                self._drift.unbind(gone)
            self._keys = keys
            self._order = sorted(keys, key=lambda k: (k.project_id, k.run_id, k.generation))
            self._project_ids = list(dict.fromkeys(key.project_id for key in self._order))

    # --- contract v1: the incremental index -----------------------------------

    def poll(self, budget: float | None = None) -> int:
        """Bring the index up to date: every run whose stamp changed is projected again, within ``budget`` seconds when
        one is given (the runs not reached stay as they were, or ``pending`` when never read). Returns how many runs are
        still pending."""
        with self._lock:
            deadline = None if budget is None or self._clock is None else self._clock() + budget
            if self._resume and deadline is not None:
                # The last budgeted poll stopped part way: carry on with the runs it found stale, without walking the
                # store again (each is read as it is now; the next whole poll looks at every run again).
                keys = self._keys
                stale = [key for key in self._resume if key in keys]
            else:
                self._begin()
                stamps = getattr(self._source, "stamps", None)
                # A source that versions every run in one walk (``stamps()``: each run's ``stamp``, in key order) is
                # asked once.
                self._bulk = None if stamps is None else stamps()
                keys = frozenset(self._source.keys() if self._bulk is None else self._bulk)
                self._forget(keys)
                stale = [key for key in self._order if self._fresh(key, keys) is None]
            prefetch = getattr(self._source, "prefetch", None)
            pending, reindexing, self._resume = 0, 0, []
            for n, key in enumerate(stale):
                if deadline is not None and self._clock() >= deadline:
                    # Not reached: one never read is pending; one read before is known stale (``reindexing``): shown as
                    # last read, counted as such, until it is read again.
                    self._resume = stale[n:]
                    pending += sum(k not in self._cache for k in self._resume)
                    reindexing = len(self._resume) - pending
                    break
                if prefetch is not None and n % PREFETCH == 0:
                    # Only the runs whose inputs are read (written since); one projected again for a file reads nothing.
                    wanted = [k for k in stale[n:n + PREFETCH] if not self._inputs_kept(k, keys)]
                    if wanted:
                        prefetch(wanted)
                self._run(key, keys, stale=True)
            self._pending, self._reindexing = pending, reindexing
            self._bulk = None
            return pending + reindexing

    def index(self, budget: float | None = None) -> int:
        """``poll`` then publish what is read as the view every contract read answers from. Returns how many runs are
        still to read: pending, or known stale (``reindexing``), so the indexer keeps going while either is."""
        with self._lock:
            if self._published is None and budget is not None and self._clock is not None:
                self.poll(0)
                self._publish()  # what the store holds, all of it pending, before any run is read
            left = self.poll(budget)
            if (not self._indexed or left == 0 or self._clock is None
                    or self._clock() - self._published_at >= PUBLISH_EVERY):
                self._publish()
            return left

    def _publish(self) -> None:
        pending, reindexing = self._pending, self._reindexing
        rows, labels, projects, facts = self._rows()
        view = self._published
        if (view is None or view.changes != self._changes or view.pending != pending
                or view.reindexing != reindexing):
            tally = self._tally
            # a project is read in full when every run of it is projected: only then are its runs' places published
            unread = {key.project_id for key in self._order if key not in self._cache}
            complete = frozenset(p["id"] for p in projects if p["id"] not in unread)
            # the islands in their slots' order: by each project's first run start, then id (a new project appends)
            projects = _index.island_order([{**p, "runs": tally.per_project[p["id"]],
                                             "needs": tally.needs_per_project[p["id"]],
                                             "standings": tally.standings(p["id"]), "since": tally.since(p["id"])}
                                            for p in projects])
            published = _View(self._changes, pending, reindexing, len(self._order), rows, labels, projects, facts,
                              complete, {o.summary["key"]: o for _, _, o in self._cache.values()},
                              {"aggregates": tally.aggregates(), "window": tally.window(),
                               "coverage": {"runs": len(self._order), "read": tally.read,
                                            "unreadable": tally.unreadable, "partial": tally.partial, "pending": pending,
                                            "reindexing": reindexing},
                               "projects": projects,
                               "needs": sorted(tally.needs.values(), key=_index.asked_order),
                               "mix": tally.mix})
            published.summary(None, "UTC", _index.utc_day)  # the default summary is ready before any request asks
            self._published = published
        if self._clock is not None:
            self._published_at = self._clock()
        self._ready.set()

    @property
    def indexed(self) -> bool:
        """Whether reads answer from the published view only (``serve_from_index``)."""
        return self._indexed

    def serve_from_index(self) -> None:
        """From now on every contract read answers from the last published view and never indexes: the adapter indexes in
        the background (``index``). A read before the first publication waits for it (one slice)."""
        self._indexed = True

    def _view(self, budget: float | None) -> _View:
        if not self._indexed:
            self.index(budget)
        self._ready.wait()
        return self._published

    def _rows(self) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]], dict[str, dict]]:
        """The indexed rows in page order (the tally keeps the order), each project's label, the projects and each
        run's countable facts."""
        if self._built is not None and self._built[0] == self._changes:
            return self._built[1:]
        summaries = self._summaries
        rows = [summaries[key] for _, key in self._tally.ordered]
        facts = dict(self._facts_by_key)
        projects = []
        for project_id in self._project_ids:
            meta = self._source.project(project_id)
            projects.append({"id": project_id, "name": meta.name, "label": meta.name or UNNAMED_PROJECT,
                             "repo": meta.repo})
        _tell_apart(projects)
        labels = {p["id"]: p["label"] for p in projects}
        self._built = (self._changes, rows, labels, projects, facts)
        return rows, labels, projects, facts

    def _account(self, key: RunKey, observed: Observed | None) -> None:
        """Take a run's old contribution out of the tally and put its new one in (``None``: the run is not indexed)."""
        cached = self._cache.get(key)
        if cached is not None:
            old = cached[2].summary
            self._tally.apply(old, self._facts_by_key.get(old["key"]), -1)
            self._summaries.pop(old["key"], None)
            self._facts_by_key.pop(old["key"], None)
        if observed is not None:
            facts = _index.facts(observed.detail)
            self._summaries[observed.summary["key"]] = observed.summary
            self._facts_by_key[observed.summary["key"]] = facts
            self._tally.apply(observed.summary, facts, +1)

    def summary(self, since: float | None = None, tz: str = "UTC", *, budget: float | None = None) -> dict[str, Any]:
        """``/api/summary``: facets, aggregates (within ``since``), coverage, the runs that need a human, the marks and
        the calendar's cells by civil day in ``tz`` (an IANA zone, ``observer_index.tz_of``)."""
        day_of = self._zones(tz)
        return self._view(budget).summary(since, tz, day_of)

    def token(self, since: float | None = None, tz: str = "UTC", *, budget: float | None = None) -> str:
        return self.summary(since, tz, budget=budget)["token"]

    def runs(self, filters: Mapping[str, str], cursor: str | None = None, limit: int = _index.PAGE, *,
             budget: float | None = None) -> dict[str, Any]:
        """``/api/runs``: one-line rows with every filtered facet value, in page order after ``cursor``."""
        for name in filters:
            if name not in ("since", "flag") and name not in _index.FACETS:
                raise ValueError(f"unknown filter {name!r}")
            if name in {"standing", "ask", "flag"}:
                _index.known([filters[name]], name)
        if "since" in filters:
            _index.since_of(filters["since"])
        view = self._view(budget)
        return {"contract": CONTRACT, "pending": view.pending,
                **_index.page(view.rows, view.labels, lambda r: _index.matches(r, filters, view.facts.get(r["key"])),
                              cursor, limit, view.summary(None, "UTC", _index.utc_day)["token"])}

    def search(self, query: str, cursor: str | None = None, limit: int = _index.PAGE, *,
               budget: float | None = None) -> dict[str, Any]:
        """``/api/search``: the runs whose goal, claim text, project label or session label holds every word of
        ``query``; runs whose claims were not read are searched by the rest and counted in ``unsearched``."""
        view = self._view(budget)
        wanted = _index.words(query)
        hay = view.haystacks()
        keep = lambda r: all(w in hay[r["key"]] for w in wanted)  # noqa: E731
        found = _index.page(view.rows, view.labels, keep, cursor, limit, view.summary(None, "UTC", _index.utc_day)["token"])
        unsearched = sum(1 for r in view.rows if r["claims"] is None and not keep(r)) if wanted else 0
        return {"contract": CONTRACT, "pending": view.pending, "query": query, "unsearched": unsearched, **found}

    def rows(self, *, budget: float | None = None) -> list[dict[str, Any]]:
        """Every indexed run's full row (contract v1, ``row.schema.json``), in page order."""
        return list(self._view(budget).rows)

    def details(self) -> dict[str, dict[str, Any]]:
        """Every indexed run's contract sheet (``sheet``'s answer), by key."""
        view = self._view(None)
        return {key: _index.sheet(o.detail, o.summary, view.attempts(o.summary)) for key, o in view.observed.items()}

    def sheet(self, key: str) -> dict[str, Any] | None:
        """``/api/run`` (contract v1): one run's detail with its audits, dropped claims and decisions. Indexed in the
        background, it is the published detail (a run not read yet has none: it is not listed either)."""
        if self._indexed:
            view = self._view(None)
            found = _index.resolve(view.observed.items(), key)
            return None if found is None else _index.sheet(found.detail, found.summary, view.attempts(found.summary))
        with self._lock:
            self._begin()
            keys = frozenset(self._source.keys())
            found = _index.resolve(((run_key(k), k) for k in keys), key)
            if found is None:
                return None
            own = self._run(found, keys)
            family = [self._run(k, keys).summary for k in keys if (k.project_id, k.run_id) == (found.project_id, found.run_id)]
            return _index.sheet(own.detail, own.summary, _index.attempts_of(family, own.summary))

#: How many stale runs a poll asks the source to read together (``Source.prefetch``).
PREFETCH = 16
#: While a cold start is under way, a new view is published at most this often (seconds on the adapter's clock): each
#: publication orders every row and counts the summary, so publishing after every slice would slow the start itself.
PUBLISH_EVERY = 0.5


class _View:
    """What one ``index`` published: the rows in page order, each project's label, the projects, each run's countable
    facts and every run's projection, with the counts the tally kept for them. Never changed once published; a summary
    within a window is counted from the rows on demand and kept."""

    def __init__(self, changes, pending, reindexing, total, rows, labels, projects, facts, complete, observed,
                 kept) -> None:
        self.changes, self.pending, self.reindexing, self.total = changes, pending, reindexing, total
        self.rows, self.labels, self.projects, self.facts, self.observed = rows, labels, projects, facts, observed
        self.complete = complete
        self._kept = kept
        self._summaries: dict[Any, dict[str, Any]] = {}
        self._hay: dict[str, str] | None = None
        self._attempts: dict[tuple[str, str], list[Mapping[str, Any]]] | None = None

    def attempts(self, row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        """The rows of every attempt at ``row``'s run (by generation), grouped once per published view."""
        if self._attempts is None:
            groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
            for r in self.rows:
                groups.setdefault((r["project"], r["run"]), []).append(r)
            self._attempts = {k: sorted(v, key=lambda r: r["generation"]) for k, v in groups.items()}
        return self._attempts[(row["project"], row["run"])]

    def haystacks(self) -> dict[str, str]:
        if self._hay is None:
            self._hay = {r["key"]: _index.haystack(r, self.labels[r["project"]]) for r in self.rows}
        return self._hay

    def summary(self, since: float | None, tz: str, day_of) -> dict[str, Any]:
        since = _index.snapped(since)  # a window is counted in whole UTC hours
        cached = self._summaries.get((since, tz))
        if cached is not None:
            return cached
        kept = self._kept
        everything = kept["aggregates"]
        ids = [p["id"] for p in self.projects]
        value = {"contract": CONTRACT, "coverage": kept["coverage"], "projects": kept["projects"],
                 "facets": everything["by"],
                 "aggregates": everything if since is None else kept["window"].aggregates(since),
                 "needs": kept["needs"], "orders": _index.ORDERS,
                 "marks": _index.marks(self.rows, ids, self.complete, self.facts, since),
                 "calendar": _index.calendar(self.rows, ids, tz, day_of)}
        # every run's line, what is still to read (pending, and known stale), the projects, the window and the zone
        value["token"] = _index.token([f"{kept['mix']:064x}", repr(since), str(self.pending), str(self.reindexing),
                                       digest(self.projects), tz])
        self._summaries[(since, tz)] = value
        return value


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


def _version(base_digest: str, observation_digest: str) -> str:
    return digest({"base": base_digest, "observations": observation_digest})
