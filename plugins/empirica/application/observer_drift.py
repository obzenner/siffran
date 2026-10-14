"""The observer's drift index: which bound workspace files changed, found by their change signatures alone.

A run's projection depends on its state, its history and the workspace files its active spike heads bind. The first two
change only by the plugin's own writes (a run's stamp says so). The third changes by the user's edits, and that is the
only reason an ended run is ever projected again. So the index keeps, for every (workspace, path) any projected run
binds, the change signature the workspace adapter gives (``signatures``: stats, nothing read), the observation it was
hashed to, and the runs that bind it. A pass compares signatures and hashes only the files whose signature moved, plus
the racily clean ones (as Git does: a file whose change time is not older than the start of the pass that hashed it is
hashed again the next pass, since a write in the same clock tick could leave its signature unchanged). The runs bound to
a file whose observation changed are the only ones projected again for it.

A run is bound when it is projected and unbound first, so a regraph, new spikes or a new workspace rebind it and an old
binding never survives; a file no run binds is dropped. Missing, unreadable and refused files are observations like any
other, read by the workspace's own ``observe`` (exactly as ``build_observation_snapshot`` reads them).

The adapter's clock reading (``now_ns``) is compared only with a file's change time, to decide whether to hash it
again: it never orders anything or reaches a published fact.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from core.freshness import observations_digest, required_paths
from core.records import RunKey

from .observation import _EMPTY_BASIS, ObservationUnavailable, _validate_capture
from .ports import ObservationSnapshot

#: The methods a workspace has when its files can be indexed (``FilesystemWorkspace`` and the test workspace do).
PORT = ("drift_id", "signatures", "now_ns", "changed_at", "basis_of")


def indexable(workspace: Any) -> bool:
    return workspace is not None and all(callable(getattr(workspace, name, None)) for name in PORT)


@dataclass
class _File:
    signature: Any
    observation: Any
    racy: bool
    runs: set = field(default_factory=set)


class DriftIndex:
    def __init__(self, counts: Counter) -> None:
        self._files: dict[tuple[str, str], _File] = {}
        self._bound: dict[RunKey, tuple[str, tuple[str, ...]]] = {}
        self._by_workspace: dict[str, set[str]] = {}
        self._counts = counts
        self._projecting: dict[RunKey, bool] = {}  # runs being projected: whether they were bound again yet

    # --- hashing ------------------------------------------------------------------------------------------------

    def _hash(self, workspace: Any, paths: list[str]) -> dict[str, Any]:
        """The observations of ``paths``, read by the workspace's own ``observe`` and checked as a snapshot checks them."""
        ordered = tuple(sorted(paths))
        try:
            capture = workspace.observe(ordered)
        except Exception as exc:
            raise ObservationUnavailable("workspace observe failed") from exc
        files = _validate_capture(capture, ordered)
        self._counts["hashed_files"] += len(files)
        return {cf.observation.path: cf.observation for cf in files}

    @staticmethod
    def _racy(workspace: Any, signature: Any, now: int) -> bool:
        changed = workspace.changed_at(signature)
        return changed is not None and changed >= now

    # --- a pass --------------------------------------------------------------------------------------------------

    def workspaces(self) -> dict[str, list[RunKey]]:
        """Each indexed workspace and the runs bound to it (any of whose sources names the workspace for this pass)."""
        out: dict[str, list[RunKey]] = {}
        for key, (wsid, _) in self._bound.items():
            out.setdefault(wsid, []).append(key)
        return out

    def sweep(self, current: dict[str, Any]) -> set[RunKey]:
        """Compare every indexed file's signature with the one it was hashed at, in the workspace the source gives this
        pass (``current``, by id; ``None`` when it has gone); hash the ones that moved and the racy ones. Returns the
        runs whose bound observations changed (all of a workspace's runs when it has gone or cannot be observed)."""
        dirty: set[RunKey] = set()
        for wsid, paths in list(self._by_workspace.items()):
            workspace = current.get(wsid)
            if not indexable(workspace) or workspace.drift_id() != wsid:
                dirty |= self._runs_of(wsid)
                continue
            now = workspace.now_ns()
            ordered = sorted(paths)
            signatures = workspace.signatures(ordered)
            self._counts["signatures"] += len(ordered)
            moved = [p for p in ordered if self._files[(wsid, p)].racy
                     or signatures[p] != self._files[(wsid, p)].signature]
            if not moved:
                continue
            try:
                observed = self._hash(workspace, moved)
            except ObservationUnavailable:
                dirty |= self._runs_of(wsid)
                continue
            for path in moved:
                entry = self._files[(wsid, path)]
                if observed[path] != entry.observation:
                    dirty |= entry.runs
                    entry.observation = observed[path]
                entry.signature = signatures[path]
                entry.racy = self._racy(workspace, signatures[path], now)
        return dirty

    def _runs_of(self, wsid: str) -> set[RunKey]:
        return {key for path in self._by_workspace.get(wsid, ()) for key in self._files[(wsid, path)].runs}

    # --- binding ---------------------------------------------------------------------------------------------------

    def begin(self, key: RunKey) -> None:
        """``key`` is being projected again: whatever it binds after ``end`` is what this projection bound."""
        self._projecting[key] = False

    def end(self, key: RunKey) -> None:
        if not self._projecting.pop(key, True):
            self.unbind(key)  # it observed nothing this time: it binds nothing

    def observe(self, key: RunKey, workspace: Any, heads: tuple) -> ObservationSnapshot:
        """The run's observation snapshot (as ``build_observation_snapshot`` gives it), from the index: a file not
        indexed yet is hashed once and indexed; the run is bound to exactly these files (the files it bound before
        and binds no longer are let go only after, so a file it still binds is never dropped and hashed again)."""
        paths = required_paths(heads)
        wsid = workspace.drift_id()
        previous = self._bound.get(key)
        if key in self._projecting:
            self._projecting[key] = True
        if not paths:
            self.unbind(key)
            return ObservationSnapshot((), _EMPTY_BASIS, observations_digest(()))
        new = [p for p in paths if (wsid, p) not in self._files]
        if new:
            now = workspace.now_ns()
            signatures = workspace.signatures(new)
            self._counts["signatures"] += len(new)
            observed = self._hash(workspace, new)
            for path in new:
                self._files[(wsid, path)] = _File(signatures[path], observed[path],
                                                  self._racy(workspace, signatures[path], now))
                self._by_workspace.setdefault(wsid, set()).add(path)
        for path in paths:
            self._files[(wsid, path)].runs.add(key)
        if previous is not None and previous != (wsid, paths):
            self._release(key, previous[0], [p for p in previous[1] if (previous[0], p) not in
                                             {(wsid, q) for q in paths}])
        self._bound[key] = (wsid, paths)
        observations = tuple(self._files[(wsid, p)].observation for p in paths)
        return ObservationSnapshot(observations, workspace.basis_of(observations), observations_digest(observations))

    def unbind(self, key: RunKey) -> None:
        bound = self._bound.pop(key, None)
        if bound is not None:
            self._release(key, *bound)

    def _release(self, key: RunKey, wsid: str, paths) -> None:
        for path in paths:
            entry = self._files.get((wsid, path))
            if entry is None:
                continue
            entry.runs.discard(key)
            if not entry.runs:
                del self._files[(wsid, path)]
                self._by_workspace[wsid].discard(path)
        if not self._by_workspace.get(wsid, True):
            del self._by_workspace[wsid]

    def keys(self) -> set[RunKey]:
        return set(self._bound)

    def bound(self, key: RunKey) -> tuple[str, tuple[str, ...]] | None:
        return self._bound.get(key)

    def size(self) -> int:
        """F: the (workspace, path) pairs indexed."""
        return len(self._files)
