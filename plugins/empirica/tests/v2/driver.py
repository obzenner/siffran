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
from typing import Any, Protocol

from application.ports import CapturedFile, HarnessResult, WorkspaceCapture
from core.freshness import FileObservation, ObservationState, canonical_digest

import sut_adapter  # the only composition bridge; no load-time cycle (sut_adapter late-imports this)


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
    def workspace_observe_calls(self) -> int: ...
    # D4-S2 policy-free telemetry seams: observation history (path batches/results) and harness
    # invocation attestations. Trusted lifecycle events use the real private composition ingress
    # (trusted_child_event/trusted_evidence_leaf/trusted_audit_verdict); fake telemetry alone is
    # insufficient. These transport seams never derive claim/audit/run policy.
    def workspace_observe_history(self) -> tuple: ...
    def harness_invocations(self) -> tuple[dict, ...]: ...
    def trusted_child_event(self, run_id: str, child_id: str, event: dict) -> dict: ...
    def trusted_evidence_leaf(self, run_id: str, payload: dict) -> dict: ...
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
    """Deterministic monotonic clock. Owns the tick fact only."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def now(self) -> float:
        self._t += 1.0
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += float(seconds)

    def read(self) -> float:
        return self._t


def _normalize(path: str) -> str:
    """Workspace path normalization (D1 §6): relative POSIX, no absolute/.. escape."""
    norm = posixpath.normpath(path)
    if posixpath.isabs(norm) or norm.startswith("..") or norm == ".":
        return ""
    return norm


class FakeWorkspace:
    """``Workspace.observe`` port backed by an in-memory file map.

    Owns only observations: per-path bytes, per-path error codes, and SHA-256 digests of bytes.
    Normalization and the present/missing/unreadable classification are the port's transport facts
    (D1 §6); no claim/evidence/freshness policy lives here. ``observe_calls`` is transport telemetry
    only (never policy).  ``observe_history`` records tuples of (exact normalized path batch,
    result batch) per observe call — transport facts only.
    """

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}
        self._errors: dict[str, str] = {}
        self.observe_calls = 0
        self._observe_history: list[tuple[tuple[str, ...], tuple[dict, ...]]] = []

    def write(self, path: str, content: bytes) -> None:
        n = _normalize(path)
        if not n:
            return
        self._files[n] = bytes(content)
        self._errors.pop(n, None)

    def delete(self, path: str) -> None:
        n = _normalize(path)
        self._files.pop(n, None)
        self._errors.pop(n, None)

    def set_error(self, path: str, code: str) -> None:
        n = _normalize(path)
        if not n:
            return
        self._errors[n] = code

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
        basis = canonical_digest([(r["path"], r["state"], r["sha256"]) for r in out])
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
               clock: FakeClock | None = None) -> ConformanceDriver:
    """Build the fake ports and compose the real v2 application/service seam through them.

    Fakes own only observations/transport. Composition (the ``application.v2`` import and service
    method calls) is delegated entirely to :func:`sut_adapter.bind_live`; if the v2 seam is absent,
    :class:`V2SeamAbsent` propagates and the assertion layer turns it into a per-case FAILURE.
    """
    workspace = FakeWorkspace()
    harness = FakeSpikeHarness()
    runs = FakeRunRepository()
    artifacts = FakeArtifactRepository()
    host = FakeHostChildSink()
    clk = clock or FakeClock()
    lim = dict(limits or {})
    return sut_adapter.bind_live(
        workspace, harness, runs, artifacts, host, profile_id, lim, clk,
    )
