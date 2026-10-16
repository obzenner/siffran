"""Black-box conformance driver and fake ports for the Empirica 2.0 SUT seam (D4).

This module is the ONLY SUT surface the conformance tests touch besides ``sut_adapter``.

* :class:`ConformanceDriver` — the protocol tests depend on.
* :func:`new_driver` — the only real-SUT factory used by tests; it builds the fake ports and
  delegates composition to :func:`sut_adapter.bind_live`, which is the only place that knows the
  ``application.v2`` import and service method names.

Design rules (D4 spec §3, §8):

* the driver and fakes contain NO branch on an expected reason/status — they never compute
  approval, reason codes, claim state, audit coverage, budgets, context relevance, or convergence;
* fakes own only observations and transport facts: workspace bytes/errors + SHA-256 digests,
  deterministic harness exit + sealed command bindings, clock ticks, CAS storage, host child
  events;
* no expected-answer stub and no second domain model;
* stdlib + ``hashlib`` only (jsonschema is used by the assertion layer, not the driver).
"""
from __future__ import annotations

import hashlib
import posixpath
import sys
from pathlib import Path
from typing import Any, Protocol

_TESTS = Path(__file__).resolve().parents[1]
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))
from sandbox_isolation import force_absent_sandbox_home  # noqa: E402

from application.ports import CapturedFile, HarnessResult, WorkspaceCapture  # noqa: E402
from core.freshness import (FileObservation, ObservationState, canonical_digest,  # noqa: E402
                            observations_digest)

import sut_adapter  # noqa: E402  # the only composition bridge; no load-time cycle
force_absent_sandbox_home()


# ---------------------------------------------------------------------------
# Failure signal
# ---------------------------------------------------------------------------


class V2SeamAbsent(RuntimeError):
    """The empirica/v2 application composition seam does not exist yet.

    ``new_driver`` (via ``sut_adapter.bind_live``) raises this when it cannot compose the real v2
    service through the fake ports. The assertion layer converts it into a per-case test FAILURE
    (not a collection ERROR) so the suite collects and executes fully and reports each absent
    behavior by name.
    """


# ---------------------------------------------------------------------------
# SUT-facing protocol (the only API tests use)
# ---------------------------------------------------------------------------


class ConformanceDriver(Protocol):
    def request(self, envelope: dict) -> dict: ...
    def workspace_write(self, path: str, content: bytes) -> None: ...
    def workspace_delete(self, path: str) -> None: ...
    def workspace_error(self, path: str, code: str) -> None: ...
    def harness_complete(self, command: str, exit_code: int) -> None: ...
    def reload(self) -> "ConformanceDriver": ...
    def compact(self) -> dict: ...
    def artifacts(self) -> tuple[dict, ...]: ...
    def operational_state(self) -> dict: ...
    # D4-S2 policy-free telemetry seams: observation history (path batches/results) and harness
    # invocation attestations. Trusted lifecycle events use the real private composition ingress
    # (trusted_child_event/trusted_evidence_leaf/trusted_audit_verdict); fake telemetry alone is
    # insufficient. These transport seams never derive claim/audit/run policy.
    def workspace_observe_history(self) -> tuple: ...
    def harness_invocations(self) -> tuple[dict, ...]: ...
    def trusted_child_event(self, run_id: str, child_id: str, event: dict) -> dict: ...
    def trusted_evidence_leaf(self, run_id: str, payload: dict) -> dict: ...
    def trusted_evidence_presentation(self, run_id: str, payload: dict) -> dict: ...
    def trusted_evidence_confirmation(self, run_id: str, payload: dict) -> dict: ...
    def trusted_human_surface(self, run_id: str, surface: str) -> dict: ...
    def trusted_audit_verdict(self, run_id: str, child_id: str, payload: dict) -> dict: ...
    def trusted_attribution(self, run_id: str, payload: dict) -> dict: ...
    # D4 spec §8 test seams (NOT public commands): the pure D9 context selector, and a raw-state
    # injection port for the strict-decoder negatives (45–47). These are test-only surfaces.
    def select_sections(self, operation_context: str, reason_codes: list[str],
                        terminal_status: str | None) -> list[str]: ...
    def inject_run_state(self, key, state: dict) -> None: ...


# ---------------------------------------------------------------------------
# Fake ports — observations and transport facts only
# ---------------------------------------------------------------------------


class FakeClock:
    """Deterministic monotonic clock. Owns the tick fact only: each read returns the time and then moves it on by
    ``step`` (zero by default: a clock that stands still)."""

    def __init__(self, start: float = 0.0, step: float = 0.0) -> None:
        self._t = float(start)
        self._step = float(step)

    def read(self) -> float:
        now = self._t
        self._t += self._step
        return now


class MemoryTimeline:
    """The timeline port (``adapters.state.timeline.Timeline``) held in memory: each generation's raw bytes, written and
    parsed by the adapter's own codec. ``fail_on`` names the appends (1-based, in call order) that fail, as an
    unwritable file would."""

    def __init__(self, fail_on=()) -> None:
        self.raw: dict = {}
        self._fail_on = set(fail_on)
        self._calls = 0

    def append(self, key, previous, revision, value, at) -> None:
        from adapters.state import timeline
        self._calls += 1
        if self._calls in self._fail_on:
            raise OSError("timeline unwritable")
        self.raw[key] = timeline.appended(self.raw.get(key), previous, revision, value, at)

    def read(self, key):
        from adapters.state import timeline
        return timeline.parse(self.raw.get(key))

    def retime(self, change) -> None:
        """Rewrite every recorded time as ``change(key, at)``: what a skewed or different clock would have written."""
        from adapters.state import timeline
        for key, raw in self.raw.items():
            entries = timeline.parse(raw)
            self.raw[key] = timeline.encode([{**e, "at": float(change(key, e["at"]))} for e in entries])


def _normalize(path: str) -> str:
    """Workspace path normalization (D1 §6): relative POSIX, no absolute/.. escape."""
    norm = posixpath.normpath(path)
    if posixpath.isabs(norm) or norm.startswith("..") or norm == ".":
        return ""
    return norm


#: The source a test's research cites by default, held by every driver's workspace: research must
#: quote the cited workspace file verbatim (``core.citations``).
CITED_SOURCE = ("docs/cited.md", "The test observed this source directly.")


class FakeWorkspace:
    """``Workspace.observe`` port backed by an in-memory file map.

    Owns only observations: per-path bytes, per-path error codes, and SHA-256 digests of bytes.
    Normalization and the present/missing/unreadable classification are the port's transport facts
    (D1 §6); no claim/evidence/freshness policy lives here. ``observe_calls`` is transport telemetry
    only (never policy).  ``observe_history`` records tuples of (exact normalized path batch,
    result batch) per observe call — transport facts only.
    """

    #: Where this workspace "lives", as ``FilesystemWorkspace.root`` tells the application.
    root = "/srv/work"

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}
        self._errors: dict[str, str] = {}
        self.observe_calls = 0
        self._observe_history: list[tuple[tuple[str, ...], tuple[dict, ...]]] = []
        # Change signatures, as a filesystem keeps them: (inode, size, mtime, ctime) per path, on a tick clock that
        # moves on every write (``tick`` moves it too: time passing). Read only by a reader's change detection.
        self._tick = 0
        self._inodes = 0
        self._sigs: dict[str, tuple] = {}
        self.hashed_bytes = 0  # transport telemetry: the bytes ``observe`` hashed

    def _touch(self, n: str, *, keep_mtime: bool = False, same_tick: bool = False) -> None:
        if not same_tick:
            self._tick += 1
        old = self._sigs.get(n)
        if same_tick and old is not None and old[3] == self._tick and old[1] == len(self._files.get(n, b"")):
            return  # a write within the tick of the last change, of the same size: the signature cannot tell (racily clean)
        self._inodes += 1
        size = len(self._files.get(n, b""))
        mtime = old[2] if keep_mtime and old is not None else self._tick
        self._sigs[n] = (old[0] if old is not None else self._inodes, size, mtime, self._tick, self._errors.get(n))

    def tick(self) -> None:
        """Time passes."""
        self._tick += 1

    def write(self, path: str, content: bytes, *, keep_mtime: bool = False, same_tick: bool = False) -> None:
        """Write ``path``. ``keep_mtime``: as ``touch -m -d`` restores it (the change time still moves); ``same_tick``:
        within the clock tick of the last change, same size (nothing in the signature moves)."""
        n = _normalize(path)
        if not n:
            return
        self._files[n] = bytes(content)
        self._errors.pop(n, None)
        self._touch(n, keep_mtime=keep_mtime, same_tick=same_tick)

    def delete(self, path: str) -> None:
        n = _normalize(path)
        self._files.pop(n, None)
        self._errors.pop(n, None)
        self._sigs.pop(n, None)
        self._tick += 1

    def set_error(self, path: str, code: str) -> None:
        n = _normalize(path)
        if not n:
            return
        self._errors[n] = code
        self._touch(n)

    # --- the change-detection port ``FilesystemWorkspace`` has --------------------------------------------------

    def drift_id(self) -> str:
        return f"fake:{id(self)}"

    def now_ns(self) -> int:
        return self._tick

    def signatures(self, paths) -> dict:
        return {p: self._sigs.get(_normalize(p)) for p in paths}

    @staticmethod
    def changed_at(signature) -> int | None:
        return None if signature is None else signature[3]

    @staticmethod
    def basis_of(observations) -> str:
        return observations_digest(tuple(observations))

    def observe(self, paths: list[str]) -> WorkspaceCapture:
        self.observe_calls += 1
        # Collapse duplicates after normalization; order is lexical (D1 §6).
        seen: dict[str, None] = {}
        for p in paths:
            n = _normalize(p)
            if n and n not in seen:
                seen[n] = None
        out: list[dict] = []
        for n in sorted(seen):
            if n == "":
                continue
            if n in self._errors:
                code = self._errors[n]
                # D1 §6 freshness states: error codes non_regular and outside_workspace map
                # directly to their canonical state; all other codes yield unreadable.
                if code in ("non_regular", "outside_workspace"):
                    out.append({"path": n, "state": code, "sha256": None})
                else:
                    out.append({"path": n, "state": "unreadable", "sha256": None})
                continue
            if n not in self._files:
                out.append({"path": n, "state": "missing", "sha256": None})
                continue
            digest = hashlib.sha256(self._files[n]).hexdigest()
            self.hashed_bytes += len(self._files[n])
            out.append({"path": n, "state": "present", "sha256": digest})
        self._observe_history.append((tuple(sorted(seen)), tuple(out)))
        files = []
        for row in out:
            sha = row["sha256"]
            if sha is not None and not sha.startswith("sha256:"):
                sha = "sha256:" + sha
            observation = FileObservation(row["path"], ObservationState(row["state"]), sha)
            files.append(CapturedFile(observation, self._files.get(row["path"])
                                      if row["state"] == "present" else None))
        basis = observations_digest(tuple(item.observation for item in files))
        return WorkspaceCapture(basis, tuple(files))

    def observe_history(self) -> tuple[tuple[tuple[str, ...], tuple[dict, ...]], ...]:
        return tuple(self._observe_history)


class FakeSpikeHarness:
    """``SpikeHarness.run`` port. Owns the deterministic exit code + sealed command binding.

    Sealing is a transport fact: it records the command, its SHA-256 digest, the exit code, the
    gate derived mechanically from the exit code (pass iff 0), and the per-file bindings taken
    from the observations the SUT passes in. It never decides claim state or approval. The staged
    ``files`` content is intentionally NOT a parameter: file bindings come from the observations
    the SUT requests through the Workspace port, so a separate ``files`` argument would be dead,
    misleading input (Sol finding).
    """

    def __init__(self) -> None:
        self._next_exit: int | None = None
        self._history: list[dict] = []

    def complete(self, command: str, exit_code: int) -> None:
        self._next_exit = int(exit_code)

    def run(self, command: str, snapshot) -> HarnessResult:
        exit_code = self._next_exit if self._next_exit is not None else 1
        self._next_exit = None
        command_digest = "sha256:" + hashlib.sha256(command.encode("utf-8")).hexdigest()
        bindings = [{"path": b.path, "sha256": b.sha256} for b in snapshot.file_bindings]
        attestation = {
            "command": command,
            "command_digest": command_digest,
            "exit_code": exit_code,
            "gate": "pass" if exit_code == 0 else "fail",
            "file_bindings": bindings,
        }
        self._history.append(attestation)
        result_digest = canonical_digest(attestation)
        return HarnessResult(exit_code, result_digest, snapshot.snapshot_digest)

    def invocations(self) -> tuple[dict, ...]:
        return tuple(self._history)


class FakeRunRepository:
    """CAS-guarded operational-state store (mirrors the application test fake)."""

    def __init__(self) -> None:
        self._store: dict[tuple, tuple[Any, str]] = {}
        self._counter = 0

    def _mint(self) -> str:
        self._counter += 1
        return f"r{self._counter}"

    def generations(self, project_id, run_id):
        return sorted(key.generation for key in self._store
                      if key.project_id == project_id and key.run_id == run_id)

    def read(self, key):
        entry = self._store.get(key)
        if entry is None:
            return _ABSENT
        value, rev = entry
        return _Present(value, rev)

    def create(self, key, value):
        if key in self._store:
            raise _Conflict(key, None, "already exists")
        rev = self._mint()
        self._store[key] = (value, rev)
        return rev

    def compare_and_set(self, key, value, expected):
        entry = self._store.get(key)
        if entry is None:
            raise _Conflict(key, expected, "absent")
        _, rev = entry
        if rev != expected:
            raise _Conflict(key, expected, "stale")
        new = self._mint()
        self._store[key] = (value, new)
        return new

    def inject(self, key, value):
        rev = self._mint()
        self._store[key] = (value, rev)
        return rev


class FakeArtifactRepository:
    """Append-only content-addressed store (ordered, preserves append sequence).

    The D4-S2 spec requires asserting append order by artifact sequence returned from
    ``artifacts()``. The store preserves global append order across all keys.
    """

    def __init__(self) -> None:
        self._all: list = []
        self._by_key: dict[tuple, list] = {}
        self._counter = 0

    def append(self, key, artifact) -> None:
        self._all.append(artifact)
        self._by_key.setdefault(key, []).append(artifact)

    def read(self, key):
        entry = self._by_key.get(key)
        if entry is None:
            return _ABSENT
        self._counter += 1
        return _Present(tuple(entry), f"rev-{self._counter}")

    def all(self) -> tuple:
        return tuple(self._all)


class FakeHostChildSink:
    """Host-native child-event transport. Owns the event bytes the host observed, nothing more.

    D4-S2: this is transport telemetry only — recording delivery identity is permitted but is
    NOT admission. Trusted lifecycle events use the real private composition ingress defined in
    ``sut_adapter.py``; fake telemetry alone cannot satisfy a test.
    """

    def __init__(self) -> None:
        self._events: list[tuple[str, dict]] = []

    def deliver(self, child_id: str, event: dict) -> None:
        self._events.append((child_id, dict(event)))

    def events(self) -> list[tuple[str, dict]]:
        return list(self._events)


# Lightweight stand-ins for core.records so the driver imports no core policy. These are the FAKE
# CAS repositories' internal record shapes (test-only ports), NOT a mandated service architecture;
# the real service (D6/D7) owns its own records behind sut_adapter.compose_application.
class _Present:
    __slots__ = ("value", "revision")

    def __init__(self, value, revision):
        self.value = value
        self.revision = revision


class _Conflict(Exception):
    def __init__(self, key, expected, reason):
        super().__init__(reason)
        self.key = key
        self.expected = expected


_ABSENT = object()


# ---------------------------------------------------------------------------
# The only real-SUT factory used by tests
# ---------------------------------------------------------------------------


def new_driver(profile_id: str, *, limits: dict | None = None,
               clock: FakeClock | None = None, timeline: MemoryTimeline | None = None) -> ConformanceDriver:
    """Build the fake ports and compose the real v2 application/service seam through them.

    Fakes own only observations/transport. Composition (the ``application.v2`` import and service
    method calls) is delegated entirely to :func:`sut_adapter.bind_live`; if the v2 seam is absent,
    :class:`V2SeamAbsent` propagates and the assertion layer turns it into a per-case FAILURE.

    With a ``timeline``, the run repository is the bridge's recording one (``adapters.bridge._RecordingRuns``), timing
    each write with ``clock``: the same seam a host's bridge uses, with a deterministic clock.
    """
    workspace = FakeWorkspace()
    workspace.write(CITED_SOURCE[0], f"# Cited\n\n{CITED_SOURCE[1]}\n".encode())
    harness = FakeSpikeHarness()
    runs = FakeRunRepository()
    artifacts = FakeArtifactRepository()
    host = FakeHostChildSink()
    clk = clock or FakeClock()
    if timeline is not None:
        from adapters.bridge import _RecordingRuns
        runs = _RecordingRuns(runs, timeline=timeline, clock=clk.read)
    lim = dict(limits or {})
    return sut_adapter.bind_live(
        workspace, harness, runs, artifacts, host, profile_id, lim, clk,
    )


def clone_driver(drv) -> ConformanceDriver:
    """A fresh service over copies of ``drv``'s fake ports: the same runs, artifacts, workspace, timeline and clock, and
    nothing shared, so a test may change the copy freely. Cheaper than driving the same runs again."""
    import copy
    from adapters.bridge import _RecordingRuns
    runs = drv._runs
    recording = isinstance(runs, _RecordingRuns)
    clock = copy.deepcopy(drv._clock)
    inner = copy.deepcopy(runs._runs if recording else runs)
    if recording:
        inner = _RecordingRuns(inner, timeline=copy.deepcopy(runs._timeline), clock=clock.read)
    return sut_adapter.bind_live(copy.deepcopy(drv._workspace), copy.deepcopy(drv._harness), inner,
                                 copy.deepcopy(drv._artifacts), copy.deepcopy(drv._host), drv._profile_id,
                                 dict(drv._limits), clock)
