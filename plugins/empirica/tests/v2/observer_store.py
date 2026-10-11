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
from governance_setup import TEST_INVOCATION, invocation_for, sized_configure_run
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
    paths: dict[str, str] = field(default_factory=dict)        # storage project id -> repository path (default /work/<name>)
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
        return Project(name, self._store.paths.get(project_id, f"/work/{name}") if name else None)

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

    def begin(self, project: str, session: str, goal: str, graph: dict, *, repo: str | None = None,
              surface: str | None = "human", where: tuple[str, str] | None = None, **start) -> str:
        """Start a run, declare its graph and have its configuration approved. ``repo`` is the path of the repository
        the project is told to live in (its folder name is the project's name); ``surface`` is what the host observes
        it can show a human (an unattended session shows nobody); ``where`` is the (label, locator) its host recorded for
        the session, none when the host recorded nothing."""
        a, drv = self.a, self.drv
        if where is not None:
            start["invocation"] = {**TEST_INVOCATION, "signal": "v2 harness", "session": {"label": where[0], "locator": where[1]}}
        run_id = a.start_run(drv, goal=goal, project=project, session=session, **start)
        if surface is not None:  # none: the host has not looked for a dialog (the run's recorded surface stays ``unobserved``)
            drv.trusted_human_surface(run_id, surface)  # an attended session: evidence waits on its human
        if repo is not None:
            self.store.names[storage_id(project)] = repo.rsplit("/", 1)[-1]
            self.store.paths[storage_id(project)] = repo
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
    run = b.begin("atlas", "s-verify", "Document the tile export path.", where=("atlas docs session", "/home/dev/.claude/projects/atlas/6f2c1a.jsonl"), graph=_graph(
        "C0", [("C0", "ordinary", "The export reads every tile through one documented path."),
               ("C1", "ordinary", "The export guide names that path."),
               ("C2", "ordinary", "The vendor specification requires that path."),
               ("C3", "ordinary", "The published tile format allows only that path."),
               ("C4", "ordinary", "The nightly export log shows every tile read through it."),
               ("C5", "ordinary", "The hosting provider's limits allow that path."),
               ("C6", "ordinary", "Last week's run reads no tile any other way."),
               ("C7", "needs-decision", "Keep the old reader for one more release.")],
        [("C0", f"C{n}") for n in range(1, 8)]))
    b.research(run, "C0", ref="docs/index.md")
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
    # The agent's work comes first (the report asks the human only when it has none left): it replaces the rejected log
    # with research the workspace holds, and the human's remaining questions are what the run waits on.
    b.research(run, "C4", ref="docs/export-log.md")
    b.stamp("verification", "atlas", "s-verify")

    # The human is waiting (a deliberative run in an attended session) and the terminal consent is a stopped run's
    # history: both are runs already built above, named for what they show.
    b.store.expect["waiting-human"] = b.store.expect["verification"]
    b.store.expect["terminal-consent"] = b.store.expect["refuted"]

    # atlas: a pass that still covers the argument, and a second audit already pending.
    run = b.begin("atlas", "s-pending-reaudit", "Index tiles by zoom level on write.", where=("index writer", "/home/dev/.pi/sessions/atlas-writer.jsonl"), graph=_graph(
        "C0", [("C0", "ordinary", "The writer emits one index per zoom level."),
               ("C1", "ordinary", "The writer already groups tiles by level.")], [("C0", "C1")]))
    b.research(run, "C1", ref="src/group.py")
    b.research(run, "C0", ref="src/writer2.py")
    b.audit(run, "pass", ["Every approved claim is cited."])
    b.a.require_pending_audit_child(b.drv, run)
    b.stamp("pending-reaudit", "atlas", "s-pending-reaudit")

    # harbor: a claim that needs a human decision was added after the scope was frozen: out of scope, unanswered.
    run = b.begin("harbor", "s-deferred", "Choose the retry backoff ceiling.", _graph(
        "C0", [("C0", "ordinary", "Retries stop after a bounded wait.")], []))
    b.research(run, "C0", ref="import/backoff.py")
    b.freeze(run)
    b.a.dispatch(b.drv, observe_action(run_id=run, action=action_graph(_graph(
        "C0", [("C0", "ordinary", "Retries stop after a bounded wait."),
               ("C1", "needs-decision", "Cap the wait at five minutes rather than ten.")], [("C0", "C1")]))))
    b.stamp("deferred-decision", "harbor", "s-deferred")

    # lumen: the author proposed other ceilings after the first were approved; the human has not answered.
    run = b.begin("lumen", "s-amend", "Thumbnail every preview once.", _graph(
        "C0", [("C0", "ordinary", "Every preview has exactly one thumbnail.")], []))
    b.research(run, "C0", ref="src/thumbs.py")
    b.a.dispatch(b.drv, observe_action(run_id=run, action=sized_configure_run(
        budgets={"max_passes": 3, "max_spawns": 0, "max_audit_spawns": 1}, rationale="One claim, no spikes.")))
    b.stamp("pending-amendment", "lumen", "s-amend")

    # lumen: research recorded on claims, then the argument was rewritten: one claim reworded, one dropped.
    run = b.begin("lumen", "s-reworded", "Cut preview cache misses.", _graph(
        "C0", [("C0", "ordinary", "Preview cache misses are rare."),
               ("C1", "ordinary", "The cache keys include the preview size."),
               ("C2", "ordinary", "The cache is warmed on deploy.")], [("C0", "C1"), ("C0", "C2")]))
    b.research(run, "C1", ref="src/cache_keys.py")
    b.research(run, "C2", ref="deploy/warm.py")
    b.a.dispatch(b.drv, observe_action(run_id=run, action=action_graph(_graph(
        "C0", [("C0", "ordinary", "Preview cache misses are rare."),
               ("C1", "ordinary", "The cache keys include the preview size and the format.")], [("C0", "C1")]))))
    b.stamp("reworded", "lumen", "s-reworded")

    # prism: an unattended run started by a delegating host: nobody can be asked.
    run = b.begin("prism", "s-auto", "Regenerate the thumbnails every night.", _graph(
        "C0", [("C0", "ordinary", "The nightly job regenerates every thumbnail.")], []),
        control_mode="auto", surface="unavailable",
        invocation=invocation_for(PROFILE, delegation=True, human_surface="unavailable", signal="v2 harness"))
    b.stamp("auto-delegated", "prism", "s-auto")

    # One audited run per way the gate refuses an audit, and the runs the gate meets in its other stages: the observer
    # says of each what the gate says (tests/v2/test_observer_agreement.py runs the same through every predicate).
    for variant in ("same_model", "unverified"):
        run = b.begin("atlas", f"s-{variant}", f"Document the {variant.replace('_', ' ')} audit.", _graph(
            "C0", [("C0", "ordinary", "The audit page names its auditor.")], []))
        b.research(run, "C0", ref="docs/audit.md")
        b.audit(run, "pass", variant=variant)
        b.stamp(f"audit-{variant.replace('_', '-')}", "atlas", f"s-{variant}")
    run = b.begin("atlas", "s-parallel", "Check the cache key and its provider limits.", _graph(
        "C0", [("C0", "ordinary", "The cache key is stable."), ("C1", "ordinary", "The provider allows the key size.")],
        [("C0", "C1")]))
    b.cite(run, "C1", "web", "https://provider.example.net/keys", "keys up to 512 bytes are allowed")
    b.stamp("parallel-human", "atlas", "s-parallel")
    # A host that has not yet looked for a dialog (whether anyone can be asked is unknown), and one that did not say whether
    # its session is interactive (that stays unknown too).
    b.begin("atlas", "s-unobserved", "Decide the cache eviction policy.", _graph(
        "C0", [("C0", "needs-decision", "Evict least recently used first.")], []), surface=None,
        invocation=invocation_for(PROFILE, human_surface="unobserved", interactive=None, signal="host startup"))
    b.stamp("unobserved-surface", "atlas", "s-unobserved")
    run = b.begin("atlas", "s-passes", "Measure the cache hit rate.", _graph(
        "C0", [("C0", "ordinary", "The hit rate is above 90 percent.")], []), budgets={"max_passes": 1, "max_spawns": 1, "max_audit_spawns": 2})
    b.research(run, "C0", ref="docs/hit-rate.md")
    b.evaluate(run, "report_convergence")
    b.research(run, "C0", ref="docs/hit-rate-2.md")
    b.stamp("passes-exhausted", "atlas", "s-passes")
    run = b.begin("atlas", "s-drift", "Check the cache eviction order.", _graph(
        "C0", [("C0", "ordinary", "Eviction is least-recently-used."), ("C1", "needs-experiment", "The eviction test passes.")],
        [("C0", "C1")]))
    b.research(run, "C0", ref="docs/eviction.md")
    b.research(run, "C1", ref="docs/eviction-test.md")
    b.spike(run, "C1", "python src/drift.py", {"src/drift.py": b"lru\n"})
    b.evaluate(run, "stop")
    b.write("src/drift.py", b"fifo\n")
    b.stamp("stopped-drift", "atlas", "s-drift")

    # prism: three attempts at one session, each with another goal.
    for goal, last in (("Shrink the thumbnails to 64 pixels.", False), ("Shrink the thumbnails to 128 pixels.", False),
                       ("Serve thumbnails from the CDN instead.", True)):
        run = b.begin("prism", "s-attempts", goal, _graph(
            "C0", [("C0", "ordinary", "The thumbnails are served at the new size.")], []))
        b.research(run, "C0", ref="src/thumb_size.py")
        if not last:
            b.evaluate(run, "stop")
        b.stamp("attempts", "prism", "s-attempts")
    # the first attempt's file was touched last: its modification time says nothing about the order of the attempts
    first = RunKey(storage_id("prism"), storage_id("s-attempts"), 1)
    b.store.times[first] = max(b.store.times.values()) + STEP

    # Two projects with the same folder name in different places.
    for project, repo in (("tiles-north", "/work/north/tiles"), ("tiles-south", "/work/south/tiles")):
        run = b.begin(project, "s-same-name", "Check the tile checksum.", _graph(
            "C0", [("C0", "ordinary", "Every tile has a checksum.")], []), repo=repo)
        b.research(run, "C0", ref="src/checksum.py")
        b.stamp(f"same-name-{project.split('-')[1]}", project, "s-same-name")

    # loose: a run whose repository was not given, so only its state can be read.
    run = b.begin("loose", "s-nohistory", "Tidy the release checklist.", _graph(
        "C0", [("C0", "ordinary", "The checklist names every release step once.")], []))
    b.stamp("history-unavailable", "loose", "s-nohistory")
    b.store.unmatched.add(storage_id("loose"))

    # A readable attempt followed by one that is not exactly the current state document: a family with an unread member.
    run = b.begin("prism", "s-unread-member", "Resize the avatar thumbnails.", _graph(
        "C0", [("C0", "ordinary", "Avatars are served at one size.")], []))
    b.research(run, "C0", ref="src/avatar_size.py")
    b.evaluate(run, "stop")
    b.stamp("attempt-unread-sibling", "prism", "s-unread-member")
    unread = RunKey(storage_id("prism"), storage_id("s-unread-member"), 2)
    b.drv.inject_run_state(unread, {"not": "a run"})
    b.store.times[unread] = b.store.times[RunKey(storage_id("prism"), storage_id("s-unread-member"), 1)] + STEP

    # A state that is not exactly the current state document.
    broken = RunKey(storage_id("harbor"), storage_id("s-broken"), 1)
    b.drv.inject_run_state(broken, {"not": "a run"})
    b.store.times[broken] = T0 - STEP
    b.store.expect["unreadable"] = f"{broken.project_id}/{broken.run_id}/1"
    return b.store


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
