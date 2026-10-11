"""A small store of real runs for the observer, built by the plugin itself.

Every run here is driven through the real v2 service (``driver.new_driver``) with the conformance
helpers, so each state, history, verdict and diagnostic is something the plugin produced. Nothing
is hand-written except the actions an author and a host would take. The same store backs the
observer tests and the contract fixtures (``scripts/regen_ui_view_fixtures.py``), which the UI
demo and its checks read.

Projects, names and times are the only invented facts: the fake ports have no repositories and
no clock, so :class:`DriverSource` supplies them deterministically.
"""
from __future__ import annotations

import unittest
import uuid
from unittest import mock
from dataclasses import dataclass, field
from typing import Any

import governance_setup
from application.location import storage_id
from application.observer import Project
from assertions import (ConformanceCase, action_graph, get_argument, get_run, action_investigate, action_research,
                        action_spike_request, evaluate, observe_action)
from core.canonical import canonical_digest
from core.records import Present, Revision, RunKey

PROFILE = ConformanceCase.DEFAULT_PROFILE
#: Fixed modification times, one per run in build order (2026-09-28T09:00Z, then every 47 minutes).
T0 = 1790586000.0
STEP = 47 * 60


@dataclass
class Store:
    """The driver plus what the fake ports cannot know: project names and run times."""

    driver: Any
    names: dict[str, str] = field(default_factory=dict)        # storage project id -> name
    unmatched: set[str] = field(default_factory=set)           # projects with no repository
    times: dict[RunKey, float] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)       # scenario -> run key


def _addressed(read: Any, identity) -> Any:
    """The driver's own read type as the domain's ``Present``, with a content revision as the real
    adapters give (file sha256, tree oid; the fakes' own revisions are counters). Any other
    outcome passes through. This adapter is the one place the driver's types are recognised."""
    if type(read).__name__ != "_Present":
        return read
    return Present(read.value, Revision(identity(read.value)))


class DriverSource:
    """An observer ``Source`` over the driver's fake ports (reads only)."""

    def __init__(self, store: Store) -> None:
        drv = store.driver
        self._store, self._runs, self._artifacts = store, drv._runs, drv._artifacts
        self._workspace = drv._workspace

    def keys(self):
        return sorted(self._runs._store, key=lambda k: (k.project_id, k.run_id, k.generation))

    def read_state(self, key):
        return _addressed(self._runs.read(key), canonical_digest)

    def updated(self, key):
        return self._store.times.get(key)

    def project(self, project_id):
        if project_id in self._store.unmatched:
            return Project(None, None)
        name = self._store.names.get(project_id)
        return Project(name, f"/work/{name}" if name else None)

    def refresh(self):
        pass

    def history_revision(self, key):
        read = self.history(key)
        return None if read is None else read.revision.token if isinstance(read, Present) else "absent"

    def history(self, key):
        if key.project_id in self._store.unmatched:
            return None
        return _addressed(self._artifacts.read(key),
                          lambda arts: canonical_digest(sorted(a.artifact_id for a in arts)))

    def workspace(self, key):
        return self._workspace


class _Author(ConformanceCase):
    """The conformance helpers, used as an author and host would act (never as assertions)."""

    def runTest(self):  # pragma: no cover - not a test
        pass


def _graph(root: str, claims: list[tuple[str, str, str]], edges: list[tuple[str, str]]) -> dict:
    return {"root": root,
            "claims": [{"id": cid, "text": text, "kind": kind, "gating": True}
                       for cid, kind, text in claims],
            "edges": [{"from": a, "to": b, "type": "SupportedBy"} for a, b in edges]}


class _Builder:
    def __init__(self) -> None:
        self.a = _Author()
        self.store = Store(self.a.bind_driver("observer", "store", "observer fixture store", PROFILE))
        self.drv = self.store.driver
        self.files: dict[str, bytes] = {}  # what the workspace holds, so citations quote it verbatim

    def write(self, path: str, content: bytes) -> None:
        self.files[path] = content
        self.drv.workspace_write(path, content)

    # --- author and host acts ----------------------------------------------

    def begin(self, project: str, session: str, goal: str, graph: dict, **start) -> str:
        a, drv = self.a, self.drv
        run_id = a.start_run(drv, goal=goal, project=project, session=session, **start)
        drv.trusted_human_surface(run_id, "human")  # an attended session: evidence waits on its human
        self.store.names.setdefault(storage_id(project), project)
        a.require_route_admitted(drv, run_id)
        a.dispatch(drv, observe_action(run_id=run_id, action=action_graph(graph)))
        drv.approve_governance(run_id)
        a.dispatch(drv, observe_action(run_id=run_id, action=action_investigate()))
        return run_id

    def research(self, run_id: str, claim: str, result: str = "supports", ref: str = "README.md"):
        """Cite ``ref`` verbatim (core.citations): its first line, writing the file if it is new."""
        if ref not in self.files:
            self.write(ref, f"{ref} states this directly.\n".encode())
        citation = self.files[ref].decode().splitlines()[0]
        self.a.dispatch(self.drv, observe_action(run_id=run_id, action=action_research(
            claim_id=claim, source_kind="code", result=result,
            payload={"source_ref": ref, "citation": citation})))

    def cite(self, run_id: str, claim: str, source_kind: str, ref: str, citation: str, **payload):
        """Research the service cannot check against the workspace: a URL, an absolute path or an
        observation (its payload says why no spike could establish it)."""
        self.a.dispatch(self.drv, observe_action(run_id=run_id, action=action_research(
            claim_id=claim, source_kind=source_kind, result="supports",
            payload={"source_ref": ref, "citation": citation, **payload})))

    def record_ids(self, run_id: str) -> dict[str, str]:
        """Each claim's latest research record id, from the plugin's own dossier."""
        argument = self.a.dispatch(self.drv, get_argument(run_id=run_id))["result"]["argument"]
        return {r["claim_id"]: r["artifact_id"] for r in argument["artifacts"] if r["kind"] == "research"}

    def batch_key(self, run_id: str) -> str:
        """The key of the batch the run shows now (a presentation names the batch it shows)."""
        return self.a.dispatch(self.drv, get_run(run_id=run_id))["result"]["run"]["confirmation_batch"]["key"]

    def present_and_answer(self, run_id: str, receipt: str, decisions: list[dict]):
        """The host shows the waiting set once, then the human answers some of it (the rest is Later)."""
        self.drv.trusted_evidence_presentation(
            run_id, {"run_id": run_id, "receipt_id": receipt, "key": self.batch_key(run_id)})
        self.drv.trusted_evidence_confirmation(
            run_id, {"run_id": run_id, "receipt_id": receipt, "decisions": decisions})

    def spike(self, run_id: str, claim: str, command: str, files: dict[str, bytes], exit_code=0):
        for path, content in files.items():
            self.write(path, content)
        self.drv.harness_complete(command, exit_code)
        self.a.dispatch(self.drv, observe_action(run_id=run_id, action=action_spike_request(
            claim_id=claim, command=command, dependent_files=sorted(files))))

    def freeze(self, run_id: str):
        self.a.require_frozen_scope(self.drv, run_id)

    def audit(self, run_id: str, verdict: str, findings: list[str] | None = None,
              variant: str = "distinct"):
        a, drv = self.a, self.drv
        child = a.require_pending_audit_child(drv, run_id)
        a.require_trusted_audit_attribution(drv, run_id, child, variant=variant)
        payload = a.build_audit_verdict_payload(drv, run_id, verdict=verdict, scope_review="pass",
                                                findings=findings)
        drv.trusted_audit_verdict(run_id, child, payload)

    def evaluate(self, run_id: str, intent: str):
        """An evaluation; a stop is answered by its human first (ADR-0063 amendment), as in the attended sessions shown."""
        if intent == "stop":
            return self.a.stop(self.drv, run_id)
        return self.a.dispatch(self.drv, evaluate(run_id=run_id, intent=intent))

    def stamp(self, scenario: str, project: str, session: str):
        """Record the latest generation of (project, session) under ``scenario``."""
        pid, rid = storage_id(project), storage_id(session)
        generation = max(self.drv._runs.generations(pid, rid))
        key = RunKey(pid, rid, generation)
        self.store.times[key] = T0 + STEP * len(self.store.times)
        self.store.expect[scenario] = f"{pid}/{rid}/{generation}"


def build_store() -> Store:
    """Build the store. Approval receipt ids are drawn from a fixed sequence while it builds, so
    the same plugin produces byte-identical fixtures."""
    sequence = (uuid.UUID(int=n) for n in range(1, 1 << 16))
    with mock.patch.object(governance_setup, "uuid4", lambda: next(sequence)):
        return _build()


def _build() -> Store:
    b = _Builder()

    # atlas: a converged, independently audited run.
    run = b.begin("atlas", "s-converged", "Cache the tile index so a cold start reads one file.", _graph(
        "C0", [("C0", "ordinary", "A cold start reads the tile index from one cache file."),
               ("C1", "needs-experiment", "The cache file round-trips every tile entry."),
               ("C2", "ordinary", "The loader already isolates index parsing in one function.")],
        [("C0", "C1"), ("C0", "C2")]))
    b.research(run, "C2", ref="src/loader.py")
    b.research(run, "C1", ref="src/cache.py")
    b.spike(run, "C1", "python -m pytest tests/test_cache.py", {"src/cache.py": b"cache v1\n"})
    b.research(run, "C0", ref="src/loader.py")
    b.freeze(run)
    b.audit(run, "pass", ["The round-trip spike covers every entry kind."])
    b.evaluate(run, "report_convergence")
    b.stamp("converged", "atlas", "s-converged")

    # atlas: stopped with a refuted claim and an unsupported one.
    run = b.begin("atlas", "s-refuted", "Drop the old tile format.", _graph(
        "C0", [("C0", "ordinary", "No reader depends on the old tile format."),
               ("C1", "ordinary", "The export job writes only the current format."),
               ("C2", "ordinary", "Users were told the old format is going away.")],
        [("C0", "C1"), ("C0", "C2")]))
    b.research(run, "C1", result="refutes", ref="jobs/export.py")
    b.evaluate(run, "stop")
    b.stamp("refuted", "atlas", "s-refuted")

    # atlas: still active; a spike went stale when its file changed afterwards.
    run = b.begin("atlas", "s-stale", "Make tile decoding constant-memory.", _graph(
        "C0", [("C0", "ordinary", "Decoding a tile no longer allocates per pixel."),
               ("C1", "needs-experiment", "The streaming decoder matches the old output.")],
        [("C0", "C1")]))
    b.research(run, "C1", ref="src/decode.py")
    b.spike(run, "C1", "python -m pytest tests/test_decode.py", {"src/decode.py": b"decode v1\n"})
    b.write("src/decode.py", b"decode v2\n")
    b.stamp("stale", "atlas", "s-stale")

    # atlas: audited while one supporter had no support. Afterwards it gained support (so the
    # root, approved now, was never reviewed) and another reviewed claim's evidence grew, so the
    # pass no longer binds the argument: the untouched reviewed claim reads stale.
    run = b.begin("atlas", "s-reaudit", "Split the tile index by zoom level.", _graph(
        "C0", [("C0", "ordinary", "Each zoom level reads only its own index file."),
               ("C1", "ordinary", "The index writer can emit one file per level."),
               ("C2", "ordinary", "No caller reads the combined index directly."),
               ("C3", "ordinary", "The tile server resolves a level before reading.")],
        [("C0", "C1"), ("C0", "C2"), ("C0", "C3")]))
    b.research(run, "C1", ref="src/writer.py")
    b.research(run, "C3", ref="src/server.py")
    b.audit(run, "pass", ["C2 has no support yet."])
    b.research(run, "C2", ref="src/callers.py")
    b.research(run, "C0", ref="src/index.py")
    b.research(run, "C3", ref="docs/server.md")
    b.stamp("reaudit", "atlas", "s-reaudit")

    # harbor: out of passes.
    run = b.begin("harbor", "s-budget", "Find why the nightly import is slow.", _graph(
        "C0", [("C0", "needs-experiment", "The import spends most of its time in one query.")], []),
        budgets={"max_spawns": 1, "max_passes": 1})
    b.research(run, "C0", ref="import/query.sql")
    b.spike(run, "C0", "python bench/import.py", {"import/query.sql": b"select 1\n"})
    b.evaluate(run, "report_convergence")
    b.evaluate(run, "stop")
    b.stamp("budget", "harbor", "s-budget")

    # harbor: frozen scope with a claim added after the freeze.
    run = b.begin("harbor", "s-frozen", "Document the import retry policy.", _graph(
        "C0", [("C0", "ordinary", "Retries back off exponentially up to five attempts.")], []))
    b.research(run, "C0", ref="import/retry.py")
    b.freeze(run)
    b.a.dispatch(b.drv, observe_action(run_id=run, action=action_graph(_graph(
        "C0", [("C0", "ordinary", "Retries back off exponentially up to five attempts."),
               ("C1", "ordinary", "A failed attempt is logged with its cause.")], [("C0", "C1")]))))
    b.evaluate(run, "stop")
    b.stamp("frozen", "harbor", "s-frozen")

    # lumen: a first attempt stops on a human decision; the next generation fails its audit.
    run = b.begin("lumen", "s-retry", "Choose the colour space for exported previews.", _graph(
        "C0", [("C0", "ordinary", "Previews look the same in every supported viewer."),
               ("C1", "needs-decision", "Display P3 is the right default for previews.")],
        [("C0", "C1")]))
    b.research(run, "C0", ref="docs/viewers.md")
    b.evaluate(run, "stop")
    b.stamp("decision", "lumen", "s-retry")
    run = b.begin("lumen", "s-retry", "Choose the colour space for exported previews.", _graph(
        "C0", [("C0", "ordinary", "Previews look the same in every supported viewer."),
               ("C1", "needs-experiment", "Converting to sRGB keeps every swatch within tolerance.")],
        [("C0", "C1")]))
    b.research(run, "C0", ref="docs/viewers.md")
    b.research(run, "C1", ref="src/colour.py")
    b.spike(run, "C1", "python -m pytest tests/test_colour.py", {"src/colour.py": b"srgb\n"})
    b.freeze(run)
    b.audit(run, "fail", ["The tolerance test never checks the wide-gamut swatches."])
    b.stamp("audit-failed", "lumen", "s-retry")

    # atlas: research the service cannot check itself (ADR-0067): one record per way of being verified,
    # and the human's answers to part of what waited for them.
    run = b.begin("atlas", "s-verify", "Document the tile export path.", _graph(
        "C0", [("C0", "ordinary", "The export reads every tile through one documented path."),
               ("C1", "ordinary", "The export guide names that path."),
               ("C2", "ordinary", "The vendor specification requires that path."),
               ("C3", "ordinary", "The published tile format allows only that path."),
               ("C4", "ordinary", "The nightly export log shows every tile read through it."),
               ("C5", "ordinary", "The hosting provider's limits allow that path."),
               ("C6", "ordinary", "Last week's run reads no tile any other way."),
               ("C7", "needs-decision", "Keep the old reader for one more release.")],
        [("C0", f"C{n}") for n in range(1, 8)]))
    b.research(run, "C1", ref="docs/export.md")
    b.cite(run, "C2", "docs", "/opt/vendor/tiles/SPEC.md", "every tile is read through the index")
    b.cite(run, "C3", "web", "https://tiles.example.org/format#export", "readers must use the index")
    b.cite(run, "C4", "runtime", "nightly export log, 2026-09-27", "tile reads: 0 outside the index",
           spike_unavailable="The job only runs on the nightly cluster.")
    b.cite(run, "C5", "web", "https://provider.example.net/limits", "direct tile reads are not available")
    b.cite(run, "C6", "runtime", "export log, 2026-09-21", "no reads outside the index",
           spike_unavailable="The run is over and its inputs are gone.")
    b.evaluate(run, "report_convergence")
    ids = b.record_ids(run)
    b.present_and_answer(run, "verify-1", [
        {"subject": "research", "artifact_id": ids["C3"], "outcome": "confirm",
         "note": "Checked the format page myself."},
        {"subject": "research", "artifact_id": ids["C4"], "outcome": "reject",
         "note": "That log is from a different job."}])
    b.stamp("verification", "atlas", "s-verify")

    # loose: a run whose repository was not given, so only its state can be read.
    run = b.begin("loose", "s-nohistory", "Tidy the release checklist.", _graph(
        "C0", [("C0", "ordinary", "The checklist names every release step once.")], []))
    b.stamp("history-unavailable", "loose", "s-nohistory")
    b.store.unmatched.add(storage_id("loose"))

    # A state that is not exactly the current state document.
    broken = RunKey(storage_id("harbor"), storage_id("s-broken"), 1)
    b.drv.inject_run_state(broken, {"not": "a run"})
    b.store.times[broken] = T0 - STEP
    b.store.expect["unreadable"] = f"{broken.project_id}/{broken.run_id}/1"
    return b.store


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
