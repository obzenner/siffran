"""A small store of real runs for the observer, built by the plugin itself.

Every run here is driven through the real v2 service (``driver.new_driver``) with the conformance
helpers, so each state, history, verdict and diagnostic is something the plugin produced. Nothing
is hand-written except the actions an author and a host would take. The same store backs the
observer tests and the contract fixtures (``scripts/regen_ui_view_fixtures.py``), which the UI
demo and its checks read.

Projects and names are the only invented facts: the fake ports have no repositories, so :class:`DriverSource`
supplies them. Times are recorded as a host's bridge records them (``adapters.bridge._RecordingRuns`` writing each run's
timeline, ADR-0070), with a deterministic clock in place of the wall clock.
"""
from __future__ import annotations

import hashlib
import unittest
import uuid
import zlib
from unittest import mock
from dataclasses import dataclass, field
from typing import Any

import governance_setup
from governance_setup import TEST_INVOCATION, sized_configure_run
from application.location import storage_id
from application.observer import Project, run_key
from assertions import (ConformanceCase, action_graph, get_argument, get_run, action_investigate, action_research,
                        action_spike_request, evaluate, observe_action)
from core.canonical import canonical_digest
from core.governance import expected_approval_kind
from application.protocol import host_profile
from core.records import Present, Revision, RunKey
from driver import FakeClock, MemoryTimeline, clone_driver

PROFILE = ConformanceCase.DEFAULT_PROFILE
#: The store's clock: 2026-09-28T09:00Z at the first write, then one minute per write.
T0 = 1790586000.0
STEP = 60.0


@dataclass
class Store:
    """The driver plus what the fake ports cannot know (project names), and the timelines its writes recorded."""

    driver: Any
    names: dict[str, str] = field(default_factory=dict)        # storage project id -> name
    paths: dict[str, str] = field(default_factory=dict)        # storage project id -> repository path (default /work/<name>)
    unmatched: set[str] = field(default_factory=set)           # projects with no repository
    timeline: Any = None
    expect: dict[str, Any] = field(default_factory=dict)       # scenario -> run key


def view_of(observer) -> dict[str, Any]:
    """The index as a test reads it: the summary's projects, each with its rows (newest first) and the attempts at each run
    by generation, beside ``coverage`` and ``token``. Test glue over the v2 owners (``summary``, ``rows``): it decides
    nothing, and the product serves no such document."""
    observer.index()
    summary, rows = observer.summary(), observer.rows()
    by_project: dict[str, list] = {}
    for row in rows:
        by_project.setdefault(row["project"], []).append(row)
    projects = []
    for project in summary["projects"]:
        runs = sorted(by_project.get(project["id"], []),
                      key=lambda r: (r["updated"] is None, -(r["updated"] or 0.0), r["key"]))
        runs_of: dict[str, list] = {}
        for run in runs:
            runs_of.setdefault(run["run"], []).append(run)
        projects.append({**project, "runs": runs, "families": [
            {"run": run_id, "attempts": [r["key"] for r in sorted(group, key=lambda r: r["generation"])]}
            for run_id, group in runs_of.items()]})
    return {"coverage": summary["coverage"], "token": summary["token"], "projects": projects,
            "summary": summary, "rows": rows}


def contract_problems(view: dict[str, Any], details: dict[str, Any]) -> list[str]:
    """What the contract's validator says of a ``view_of`` and the sheets beside it."""
    import validate_ui_view
    return validate_ui_view.check(view["summary"], view["rows"], details)


def observed(observer) -> tuple[dict[str, Any], dict[str, Any]]:
    """``view_of`` and every run's sheet by key."""
    view = view_of(observer)
    return view, observer.details()


def _addressed(read: Any, identity) -> Any:
    """The driver's own read type as the domain's ``Present``, with a content revision as the real
    adapters give (file sha256, tree oid; the fakes' own revisions are counters). Any other
    outcome passes through. This adapter is the one place the driver's types are recognised."""
    if type(read).__name__ != "_Present":
        return read
    return Present(read.value, Revision(identity(read.value)))


def step_problems(detail: dict[str, Any], argument: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Each research, spike request and spike step stands as the public ``GetArgument`` says of its claim: ``current`` (its
    claim, as worded when it was recorded), ``replaced`` (the claim reworded since) or ``gone`` (the claim left the
    argument). The problems, and each step's standing."""
    wording = {c["claim_id"]: c["wording_digest"] for c in argument["claims"]}
    recorded = [a for a in argument["artifacts"] if a["kind"] in {"research", "spike_request", "spike"}]
    steps = [s for s in detail["steps"] or () if s["kind"] in {"research", "spike_request", "spike"}]
    if [(s["kind"], s["claim"]) for s in steps] != [(a["kind"], a["claim_id"]) for a in recorded]:
        return ["the steps are not the argument's records, in order"], []
    problems, seen = [], []
    for step, artifact in zip(steps, recorded):
        want = ("gone" if artifact["claim_id"] not in wording
                else "replaced" if artifact["claim_digest"] != wording[artifact["claim_id"]] else "current")
        seen.append(want)
        if step["standing"] != want:
            problems.append(f"step {step['kind']} of {step['claim']}: {step['standing']}, the core says {want}")
    return problems, seen


def decision_problems(row: dict[str, Any], detail: dict[str, Any], argument: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Each ``needs-decision`` claim's decision as the observer reads it, against the public ``GetArgument`` projection of
    the same run (answer present, scope, state), never the observer: the problems, and the status each claim should
    read (approved, rejected, waiting, unanswered, deferred, not_asked)."""
    problems, seen = [], []
    for claim in argument["claims"]:
        if claim["kind"] != "needs-decision":
            continue
        decision = next(c for c in detail["claims"] if c["id"] == claim["claim_id"])["decision"]
        answered = claim.get("human_decision")
        if answered is not None:
            want = {"approve": "approved", "reject": "rejected"}[answered["outcome"]]
        elif claim["gating"]:
            want = ("waiting" if row["state"] == "active" else "unanswered") if claim["state"] == "blocked" else "not_asked"
        else:
            want = "deferred"
        seen.append(want)
        at = f"{row['key']} {claim['claim_id']}"
        if decision["status"] != want:
            problems.append(f"{at}: decision {decision['status']}, the core says {want}")
        if (decision["outcome"] is not None) != (answered is not None):
            problems.append(f"{at}: an outcome exactly when the human answered")
        if (decision["where"] is not None) != (want == "waiting"):
            problems.append(f"{at}: a place exactly while it waits")
    return problems, seen


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

    def timeline(self, key):
        return self._store.timeline.read(key)

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


#: The contract fixtures' runs (P3b): the minimal named selection of the store's scenarios, one per standing and one per
#: ask kind the service reaches. Each brings every attempt of its session (``audit-failed`` its first, so journey 7 has
#: attempts side by side). The store keeps the rest as the observer tests' oracle; the fixtures, the demo they vendor and the UI journeys'
#: live store hold only these (``FixtureSource``; ``scripts/regen_ui_view_fixtures.py`` is their one generator).
FIXTURE_SCENARIOS = (
    "unreadable",          # unreadable (a state that is not a run)
    "verification",        # needs_human, decide_claim (and confirmed and rejected sources)
    "parallel-human",      # needs_human, confirm_source
    "pending-reaudit",     # audit_pending
    "reaudit",             # changed_since_audit
    "audit-failed",        # not_ended after a failed audit, its first attempt stopped on a decision (two attempts)
    "approval-pending",    # needs_human, approve_configuration: the dialog shown, not answered
    "pending-amendment",   # needs_human, approve_revision
    "freeze-scope",        # needs_human, freeze_scope
    "converged",           # proven
    "refuted",             # stopped_residual, a refuted claim
    "frozen",              # stopped_frozen
    "budget",              # stopped_budget
)


def fixture_keys(store: Store) -> set:
    """Every attempt of each ``FIXTURE_SCENARIOS`` session."""
    families = {tuple(store.expect[name].split("/")[:2]) for name in FIXTURE_SCENARIOS}
    return {k for k in store.driver._runs._store if (k.project_id, k.run_id) in families}


def fixture_scenarios(store: Store) -> dict[str, str]:
    """The scenario table of the fixtures: each selected scenario, and any other name for a run they hold."""
    keys = {run_key(k) for k in fixture_keys(store)}
    return {name: key for name, key in sorted(store.expect.items()) if key in keys}


class FixtureSource(DriverSource):
    """The store's fixture runs only (``FIXTURE_SCENARIOS``), read as ``DriverSource`` reads them."""

    def __init__(self, store: Store) -> None:
        super().__init__(store)
        self._only = fixture_keys(store)

    def keys(self):
        return [k for k in super().keys() if k in self._only]


class ScaleSource:
    """An observer ``Source`` of ``n`` runs over ``projects`` projects, each run a copy of one of the store's runs (its
    state, history, timeline and workspace, as the real service wrote them), under a key of its own: the store at volume
    without driving the service ten thousand times. ``replace(key, template)`` gives one run another template's records,
    which is what one run changing looks like to the observer; ``stamp`` is the cheap per-run version the index compares."""

    def __init__(self, store: Store, n: int, projects: int = 40) -> None:
        source = DriverSource(store)
        self._templates = [(source.read_state(k), source.timeline(k), source.history_revision(k), source.history(k),
                            source.workspace(k)) for k in source.keys()]
        self._names = {}
        self._map: dict[RunKey, list[int]] = {}
        for i in range(n):
            project = storage_id(f"scale project {i % projects}")
            self._names[project] = f"project-{i % projects:02d}"
            self._map[RunKey(project, storage_id(f"scale run {i}"), 1)] = [i % len(self._templates), 0]
        self._keys = sorted(self._map, key=lambda k: (k.project_id, k.run_id, k.generation))

    def replace(self, key: RunKey, template: int) -> None:
        entry = self._map[key]
        entry[0], entry[1] = template % len(self._templates), entry[1] + 1

    def keys(self):
        return self._keys

    def stamp(self, key):
        template, changes = self._map[key]
        return template, changes

    def _t(self, key):
        return self._templates[self._map[key][0]]

    def read_state(self, key):
        return self._t(key)[0]

    def timeline(self, key):
        return self._t(key)[1]

    def project(self, project_id):
        name = self._names[project_id]
        return Project(name, f"/work/{name}")

    def refresh(self):
        pass

    def history_revision(self, key):
        return self._t(key)[2]

    def history(self, key):
        return self._t(key)[3]

    def workspace(self, key):
        return self._t(key)[4]


class DemoSource(ScaleSource):
    """The store at volume as a person would see it in a demo: ``n`` runs over named projects (``DEMO_PROJECTS``), their
    times spread over about ``days`` days back from ``now`` (so a calendar, a period and "asked N days ago" have something
    to show), about 3% of them with no timeline at all (time unknown, never zero), and about one in ten run again as a
    second or third attempt (the same run id, generation 2 and 3). Everything else is what the real service wrote: each
    run is a copy of one of the store's runs under a key of its own, its timeline shifted as a whole so that its last write
    lands on its day (the chain of writes, their order and their gaps are the real ones; only where they sit on the
    calendar differs, and none is after ``now``). The store's own runs are in it under their own keys (an audit binds the
    run it was made for, so only those keep a current pass: the proven and audit-pending standings come from them and from
    the copies of the runs that are not read in part). Deterministic for a ``seed``."""

    def __init__(self, store: Store, n: int, *, now: float, days: int = 60, seed: int = 7) -> None:
        import random
        super().__init__(store, 0)
        from application.observer import Observer
        self._shift: dict[RunKey, float | None] = {}
        self._map = {}
        self._names = {}
        self._repos: dict[str, str] = {}
        self._own = DriverSource(store)
        rng, i = random.Random(seed), 0
        rows = {r["key"]: r for r in Observer(self._own).rows()}
        readable = [n_ for n_, k in enumerate(self._own.keys())
                    if rows[run_key(k)]["diagnostic"] is None and rows[run_key(k)]["standing"] != "proven"]

        def last(entries) -> float:
            return max((e["at"] for e in entries or ()), default=T0)

        for index, key in enumerate(self._own.keys()):
            if len(self._map) >= n:
                break
            self._map[key] = [index, 0]
            age = (rng.random() ** 2) * days * 86400
            self._shift[key] = None if rng.random() < 0.03 else now - age - last(self._own.timeline(key))
        while len(self._map) < n:
            name, repo = DEMO_PROJECTS[i % len(DEMO_PROJECTS)]
            project = storage_id(name)
            self._names[project] = name
            self._repos[project] = repo or f"/work/{name}"
            run = storage_id(f"demo run {i}")
            attempts = rng.choice((2, 3)) if i % 10 == 9 else 1
            # Recent days hold more runs than old ones: a day's age is the square of a uniform draw.
            age = (rng.random() ** 2) * days * 86400
            untimed = rng.random() < 0.03
            for generation in range(1, attempts + 1):
                if len(self._map) >= n:
                    break
                key = RunKey(project, run, generation)
                template = rng.choice(readable)
                self._map[key] = [template, 0]
                # An earlier attempt came before a later one by a few hours.
                self._shift[key] = (None if untimed else
                                    now - age - (attempts - generation) * 3 * 3600 - last(self._templates[template][1]))
            i += 1
        self._keys = sorted(self._map, key=lambda k: (k.project_id, k.run_id, k.generation))

    def timeline(self, key):
        entries, shift = super().timeline(key), self._shift[key]
        if entries is None or shift is None:
            return None
        return [{**e, "at": e["at"] + shift} for e in entries]

    def project(self, project_id):
        if project_id in self._names:
            return Project(self._names[project_id], self._repos[project_id])
        return self._own.project(project_id)


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
    def __init__(self, clock: FakeClock | None = None, timeline: MemoryTimeline | None = None) -> None:
        self.a = _Author()
        timeline = MemoryTimeline() if timeline is None else timeline
        self.store = Store(self.a.bind_driver("observer", "store", "observer fixture store", PROFILE,
                                              clock=clock or FakeClock(T0, STEP), timeline=timeline),
                           timeline=timeline)
        self.drv = self.store.driver
        self.files: dict[str, bytes] = {}  # what the workspace holds, so citations quote it verbatim
        self.independence_then: list[str | None] = []  # each verdict's independence as GetRun said it when recorded

    def write(self, path: str, content: bytes) -> None:
        self.files[path] = content
        self.drv.workspace_write(path, content)

    # --- author and host acts ----------------------------------------------

    def begin(self, project: str, session: str, goal: str, graph: dict, *, repo: str | None = None,
              surface: str | None = "human", where: tuple[str, str] | None = None, approve: bool = True, **start) -> str:
        """Start a run, declare its graph and have its configuration approved. ``repo`` is the path of the repository
        the project is told to live in (its folder name is the project's name); ``surface`` is what the host observes
        it can show a human (an unattended session shows nobody); ``where`` is the (label, locator) its host recorded for
        the session, none when the host recorded nothing. ``approve=False`` leaves the configuration the graph proposed
        waiting on its human (the run does not investigate)."""
        a, drv = self.a, self.drv
        # The host a real one records at StartRun (``adapters.invocation.provenance``): the host whose folder holds the
        # session it recorded (Pi's ``.pi``, Claude Code's ``.claude``), else, with no locator, Pi for about a third of the
        # sessions (by the session's name, so the same every build) and Claude Code for the rest.
        host = (("pi" if "/.pi/" in where[1] else "claude") if where
                else "pi" if zlib.crc32(session.encode()) % 3 == 0 else "claude")
        start.setdefault("invocation", {**TEST_INVOCATION, "host": host, "signal": "v2 harness",
                                        **({} if where is None else {"session": {"label": where[0], "locator": where[1]}})})
        run_id = a.start_run(drv, goal=goal, project=project, session=session, **start)
        if surface is not None:  # none: the host has not looked for a dialog (the run's recorded surface stays ``unobserved``)
            drv.trusted_human_surface(run_id, surface)  # an attended session: evidence waits on its human
        if repo is not None:
            self.store.names[storage_id(project)] = repo.rsplit("/", 1)[-1]
            self.store.paths[storage_id(project)] = repo
        self.store.names.setdefault(storage_id(project), project)
        a.require_route_admitted(drv, run_id)
        a.dispatch(drv, observe_action(run_id=run_id, action=action_graph(graph)))
        if not approve:
            return run_id
        drv.approve_governance(run_id)
        a.dispatch(drv, observe_action(run_id=run_id, action=action_investigate()))
        return run_id

    def ask_configuration(self, run_id: str, receipt: str, *, outcome: str | None = "present"):
        """The agent's ``configure_run`` (sized from the placeholder, unless the run already sized it), then its host's
        mediation, as Claude Code's and Pi's do it (``adapters.governance.HostGovernance``, ``governance-ui.ts``): the host
        records its approval ingress and shows its human the dialog (``present``: open, not answered yet), which may be
        closed without an answer (``dismiss``: Esc, a timeout) or answered no (``reject``). ``outcome=None``: the agent asked and no host mediated."""
        g = self.a.dispatch(self.drv, get_run(run_id=run_id))["result"]["run"]["governance"]
        if g["proposal"]["rationale"] is None:
            self.a.dispatch(self.drv, observe_action(run_id=run_id, action=sized_configure_run(
                budgets=g["proposal"]["budgets"], rationale=governance_setup.SIZED_RATIONALE)))
        if outcome is None:
            return
        service = self.drv._service
        ingress = host_profile(PROFILE)["approval_ingress"]
        g = service.trusted_governance_context(run_id=run_id, payload={"author": governance_setup.AUTHOR,
                                                                        "ingress": ingress})["result"]["run"]["governance"]
        envelope = {"run_id": run_id, "receipt_id": receipt, "proposal_digest": g["proposal_digest"],
                    "plan_revision": g["plan_revision"], "approval_kind": expected_approval_kind(g)}
        done = service.trusted_governance_decision(run_id=run_id, payload={**envelope, "outcome": "present"})
        assert done["result"]["type"] == "Allow", done
        if outcome in ("dismiss", "reject"):  # Esc or a timeout closes it; Reject submits the human's no
            payload = ({**envelope, "outcome": "dismiss"} if outcome == "dismiss" else
                       {**envelope, "submission": {"action": "reject", "configuration": {"budgets": g["proposal"]["budgets"]}}})
            done = service.trusted_governance_decision(run_id=run_id, payload=payload)
            assert done["result"]["type"] == "Allow", done

    def ask_to_end(self, run_id: str, receipt: str):
        """A stop the host shows its human, who has not answered yet (``end_run`` without the answer)."""
        self.evaluate_raw(run_id, "stop")
        self.drv.trusted_human_surface(run_id, "human")
        batch = self.evaluate_raw(run_id, "stop")["result"]["run"]["confirmation_batch"]
        self.drv.trusted_evidence_presentation(run_id, {"run_id": run_id, "receipt_id": receipt, "key": batch["key"]})

    def evaluate_raw(self, run_id: str, intent: str):
        return self.a.dispatch(self.drv, evaluate(run_id=run_id, intent=intent))

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
        # What the author view says of this verdict as it stands the moment it is recorded: an oracle for later.
        self.independence_then.append(a.dispatch(drv, get_run(run_id=run_id))["result"]["run"]["audit"]["independence"])

    def evaluate(self, run_id: str, intent: str):
        """An evaluation; a stop is answered by its human first (ADR-0063 amendment), as in the attended sessions shown."""
        if intent == "stop":
            return self.a.stop(self.drv, run_id)
        return self.a.dispatch(self.drv, evaluate(run_id=run_id, intent=intent))

    def stamp(self, scenario: str, project: str, session: str):
        """Record the latest generation of (project, session) under ``scenario``."""
        pid, rid = storage_id(project), storage_id(session)
        generation = max(self.drv._runs.generations(pid, rid))
        self.store.expect[scenario] = f"{pid}/{rid}/{generation}"


_BUILT: list[Store] = []


def copy_store(store: Store) -> Store:
    """An independent copy of ``store``: a fresh service over copies of its ports (``driver.clone_driver``)."""
    drv = clone_driver(store.driver)
    return Store(drv, dict(store.names), dict(store.paths), set(store.unmatched), drv._runs._timeline, dict(store.expect))


def build_store(clock: FakeClock | None = None, timeline: MemoryTimeline | None = None) -> Store:
    """Build the store. Approval receipt ids are drawn from a fixed sequence while it builds, and its writes are timed
    by a deterministic clock, so the same plugin produces byte-identical fixtures. The default store is driven once per
    process and every call returns an independent copy of it (the same runs, nothing shared)."""
    if clock is None and timeline is None and _BUILT:
        return copy_store(_BUILT[0])
    sequence = (uuid.UUID(int=n) for n in range(1, 1 << 16))
    with mock.patch.object(governance_setup, "uuid4", lambda: next(sequence)):
        store = _build(clock, timeline)
    if clock is None and timeline is None:
        _BUILT.append(store)
        return copy_store(store)
    return store


def walk_pages(fetch, limit: int) -> tuple[list[dict], dict]:
    """Every row of a paged answer, page after page: ``fetch(cursor, limit)`` gives one page. Bounded by the pages its
    total allows (total / limit + 1): a walk whose cursor does not advance fails loudly instead of spinning."""
    rows, cursor, pages, bound = [], None, 0, None
    while True:
        page = fetch(cursor, limit)
        pages += 1
        bound = page["total"] // limit + 1 if bound is None else bound
        if pages > bound:
            raise AssertionError(f"a page walk past its bound: {pages} pages for {page['total']} rows at {limit} a page")
        rows += page["rows"]
        cursor = page["next"]
        if cursor is None:
            return rows, page


def build_demo_store(proven: int) -> Store:
    """The store, and ``proven`` more runs, each driven through the real service with its own goal and claims
    (``demo_goals``) in a project of its own choosing (``DEMO_PROJECTS``): one in three converges under an independent
    auditor (the settled runs a demo needs, which a copy of a run cannot be: an audit binds the run it was made for), the
    others stop on refuting evidence, fail an audit, wait on a human's decision or are still being worked on."""
    sequence = (uuid.UUID(int=n) for n in range(1, 1 << 16))
    with mock.patch.object(governance_setup, "uuid4", lambda: next(sequence)):
        return _build(None, None, proven=proven)


def _build(clock: FakeClock | None, timeline: MemoryTimeline | None, proven: int = 0) -> Store:
    b = _Builder(clock, timeline)

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
    # a long unbroken token, as real audit reasons carry (a digest, a path): the sheet wraps it inside its width
    b.audit(run, "fail", ["The tolerance test never checks the wide-gamut swatches: research artifact "
                          "sha256:8db61ff4da9b90192441e9833d4306964aeb94e1bf64248638f124bca4268e21 cites "
                          "tests/colour/tolerance/wide_gamut_swatches_display_p3_rec2020_regression_cases.py:3,5,16."])
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

    # atlas: a pass that still covers the argument, and a second audit already pending.
    run = b.begin("atlas", "s-pending-reaudit", "Index tiles by zoom level on write.", where=("index writer", "/home/dev/.pi/sessions/atlas-writer.jsonl"), graph=_graph(
        "C0", [("C0", "ordinary", "The writer emits one index per zoom level."),
               ("C1", "ordinary", "The writer already groups tiles by level.")], [("C0", "C1")]))
    b.research(run, "C1", ref="src/group.py")
    b.research(run, "C0", ref="src/writer2.py")
    b.audit(run, "pass", ["Every approved claim is cited."])
    b.a.require_pending_audit_child(b.drv, run)
    b.stamp("pending-reaudit", "atlas", "s-pending-reaudit")

    # lumen: the author proposed other ceilings after the first were approved; the human has not answered.
    run = b.begin("lumen", "s-amend", "Thumbnail every preview once.", _graph(
        "C0", [("C0", "ordinary", "Every preview has exactly one thumbnail.")], []))
    b.research(run, "C0", ref="src/thumbs.py")
    b.a.dispatch(b.drv, observe_action(run_id=run, action=sized_configure_run(
        budgets={"max_passes": 3, "max_spawns": 0, "max_audit_spawns": 1}, rationale="One claim, no spikes.")))
    b.stamp("pending-amendment", "lumen", "s-amend")

    # atlas: a source a human must confirm, beside research the agent still owes. The other stages of the gate (the
    # audits it refuses, an unattended run, a host that has not looked, a spent ceiling, drift after a stop, regraphs)
    # are generated by test_observer_stateful.py, not kept here.
    run = b.begin("atlas", "s-parallel", "Check the cache key and its provider limits.", _graph(
        "C0", [("C0", "ordinary", "The cache key is stable."), ("C1", "ordinary", "The provider allows the key size.")],
        [("C0", "C1")]))
    b.cite(run, "C1", "web", "https://provider.example.net/keys", "keys up to 512 bytes are allowed")
    b.stamp("parallel-human", "atlas", "s-parallel")

    # prism: three attempts at one session, each with another goal.
    for goal, last in (("Shrink the thumbnails to 64 pixels.", False), ("Shrink the thumbnails to 128 pixels.", False),
                       ("Serve thumbnails from the CDN instead.", True)):
        run = b.begin("prism", "s-attempts", goal, _graph(
            "C0", [("C0", "ordinary", "The thumbnails are served at the new size.")], []))
        b.research(run, "C0", ref="src/thumb_size.py")
        if not last:
            b.evaluate(run, "stop")
        b.stamp("attempts", "prism", "s-attempts")

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
    b.drv.inject_run_state(unread, {"not": "a run"})  # injected, not written by the service: no timeline

    # A state that is not exactly the current state document.
    broken = RunKey(storage_id("harbor"), storage_id("s-broken"), 1)
    b.drv.inject_run_state(broken, {"not": "a run"})
    b.store.expect["unreadable"] = f"{broken.project_id}/{broken.run_id}/1"

    # The human asks of a run's governance and of its end (P3b, P3c): a configuration its host has shown its human, who
    # has not answered (the normal bootstrap: route, graph, configure_run and the dialog), and a run whose scope was
    # frozen, audited, that the gate asks its human to end as frozen.
    run = b.begin("quarry", "s-configure", "Choose the gravel grading for the north pit.", _graph(
        "C0", [("C0", "ordinary", "The north pit grading meets the road spec.")], []), approve=False)
    b.ask_configuration(run, "configure-shown")
    b.stamp("approval-pending", "quarry", "s-configure")
    run = b.begin("quarry", "s-freeze", "Write down the blasting schedule.", _graph(
        "C0", [("C0", "ordinary", "Blasts happen only on weekday mornings.")], []))
    b.research(run, "C0", ref="blast/schedule.md")
    b.freeze(run)
    b.a.dispatch(b.drv, observe_action(run_id=run, action=action_graph(_graph(
        "C0", [("C0", "ordinary", "Blasts happen only on weekday mornings."),
               ("C1", "ordinary", "Neighbours are told a day ahead.")], [("C0", "C1")]))))
    b.audit(run, "pass", ["The frozen claim is cited."])
    b.stamp("freeze-scope", "quarry", "s-freeze")
    for n, ((goal, thing), (name, repo)) in enumerate(zip(demo_goals(proven), _demo_projects(proven))):
        _varied(b, n, goal, thing, name, repo)
    return b.store


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


# --- a heterogeneous store at volume (P1b) ------------------------------------------------------------------------------

#: The files the heterogeneous templates' spikes bind, each set distinct and overlapping others (``src/core/a.py`` is
#: bound by three templates, ``src/core/b.py`` by two), at realistic sizes.
BOUND = {"src/core/a.py": 24_000, "src/core/b.py": 9_000, "src/core/c.py": 15_000, "src/util/x.py": 4_000,
         "src/util/y.py": 31_000, "docs/guide.md": 12_000, "tests/t1.py": 6_000}
#: The file a drifted workspace has edited since the spikes ran.
DRIFTED = "src/core/b.py"


def body(path: str, size: int, version: int = 1) -> bytes:
    """Deterministic text of about ``size`` bytes for ``path``."""
    line = f"# {path} v{version}: the quick brown fox jumps over the lazy dog\n".encode()
    return (line * (size // len(line) + 1))[:size]


def build_hetero_templates() -> Store:
    """Template runs whose spikes bind distinct, overlapping file sets: active ones, a stopped one, a converged one and
    one with research only. Driven through the real service once per process."""
    if _HETERO:
        return copy_store(_HETERO[0])
    sequence = (uuid.UUID(int=n) for n in range(1, 1 << 16))
    with mock.patch.object(governance_setup, "uuid4", lambda: next(sequence)):
        b = _Builder()
        files = {p: body(p, n) for p, n in BOUND.items()}

        def bind(*paths):
            return {p: files[p] for p in paths}
        two = _graph("C0", [("C0", "ordinary", "The core path is unchanged."),
                            ("C1", "needs-experiment", "The core tests pass.")], [("C0", "C1")])
        three = _graph("C0", [("C0", "ordinary", "The guide matches the code."),
                              ("C1", "needs-experiment", "The core tests pass."),
                              ("C2", "needs-experiment", "The util tests pass.")], [("C0", "C1"), ("C0", "C2")])
        run = b.begin("hetero", "t-a", "Keep the core path fast.", two)
        b.research(run, "C1", ref="src/core/a.py")
        b.spike(run, "C1", "pytest tests/core", bind("src/core/a.py", "src/core/b.py", "src/util/x.py"))
        run = b.begin("hetero", "t-b", "Document the core and util paths.", three)
        b.research(run, "C1", ref="src/core/b.py")
        b.spike(run, "C1", "pytest tests/core", bind("src/core/b.py", "src/core/c.py"))
        b.research(run, "C2", ref="src/util/x.py")
        b.spike(run, "C2", "pytest tests/util", bind("docs/guide.md", "src/util/x.py"))
        run = b.begin("hetero", "t-c", "Drop the old core helper.", two)
        b.research(run, "C1", ref="src/core/a.py")
        b.spike(run, "C1", "pytest tests/t1.py", bind("src/core/a.py", "src/core/c.py", "tests/t1.py"))
        b.evaluate(run, "stop")
        run = b.begin("hetero", "t-d", "Read the util module once.", _graph(
            "C0", [("C0", "ordinary", "The util module is read once.")], []))
        b.research(run, "C0", ref="src/util/y.py")
        run = b.begin("hetero", "t-e", "Cache the util results.", two)
        b.research(run, "C1", ref="src/util/y.py")
        b.spike(run, "C1", "pytest tests/util", bind("src/util/y.py", "src/core/a.py"))
        b.research(run, "C0", ref="src/core/a.py")
        b.freeze(run)
        b.audit(run, "pass", ["The util spike covers the cache."])
        b.evaluate(run, "report_convergence")
    _HETERO.append(b.store)
    return copy_store(b.store)


_HETERO: list[Store] = []


class HeteroSource(ScaleSource):
    """``n`` runs over ``workspaces`` workspaces (each a copy of the templates' files, run ``i`` in workspace
    ``i % workspaces``), one project per workspace; in every ``drift_every``-th workspace ``DRIFTED`` was edited after
    the spikes ran, so the runs there that bind it have drifted. ``replace(key, template)`` is one of our writes,
    ``edit(w, path)`` a user's edit, ``delete(key)`` a deleted run."""

    def __init__(self, store: Store, n: int, workspaces: int = 20, drift_every: int = 4) -> None:
        import copy
        super().__init__(store, n, projects=workspaces)
        template = store.driver._workspace
        self.spaces = [copy.deepcopy(template) for _ in range(workspaces)]
        self._space_of = {}
        for i in range(n):  # run i: workspace (and project) i % workspaces, and every template in every workspace
            key = RunKey(storage_id(f"scale project {i % workspaces}"), storage_id(f"scale run {i}"), 1)
            self._map[key][0] = (i // workspaces) % len(self._templates)
            self._space_of[key] = self.spaces[i % workspaces]
        for w, space in enumerate(self.spaces):
            if w % drift_every == 0:
                space.write(DRIFTED, body(DRIFTED, BOUND[DRIFTED], version=2))
            space.tick()

    def edit(self, workspace: int, path: str, version: int, **how) -> None:
        self.spaces[workspace].write(path, body(path, BOUND.get(path, 1000), version), **how)

    def delete(self, key: RunKey) -> None:
        del self._map[key]
        self._keys = [k for k in self._keys if k != key]

    def workspace(self, key):
        return self._space_of[key]

    def templates(self) -> int:
        return len(self._templates)


# --- the demo's variety (P3) ----------------------------------------------------------------------------------------------

#: What a demo run is about, and what it does to it: every pairing is a goal (``demo_goals``), each one distinct.
DEMO_THINGS = ("the tile index", "the export job", "the preview cache", "the nightly import", "the retry policy",
               "the avatar thumbnails", "the session store", "the billing webhook", "the search ranking", "the audit log",
               "the config loader", "the PDF renderer", "the rate limiter", "the CSV importer", "the sync daemon",
               "the release notes", "the onboarding email", "the font loader", "the checkout form", "the cron scheduler",
               "the image resizer", "the feature flags", "the password reset flow", "the GraphQL gateway",
               "the metrics exporter", "the dark-mode palette", "the locale files", "the upload limits")
DEMO_ACTS = ("Speed up {}.", "Document {}.", "Cache {} between requests.", "Remove the dead code in {}.",
             "Make {} constant-memory.", "Add retries to {}.", "Measure {} under load.", "Split {} by tenant.",
             "Migrate {} to the new schema.", "Fix the flaky test in {}.", "Choose a timeout for {}.",
             "Harden {} against malformed input.", "Log every failure in {}.", "Deprecate the old API of {}.")
#: The projects the driven runs live in, beyond the store's own: several sit in nested folders of one workspace.
DEMO_PROJECTS = (("atlas", None), ("ledger", None), ("harbor", None), ("prism", None), ("lumen", None),
                 ("delta", None), ("quill", None), ("orbit", None), ("sprout", None), ("kiln", None),
                 ("payments", "/work/acme/payments"), ("storefront", "/work/acme/storefront"),
                 ("mobile", "/work/acme/mobile"), ("infra", "/work/platform/infra"), ("docs-site", "/work/platform/docs-site"),
                 ("design-system", "/work/platform/design-system"))


def demo_goals(n: int, seed: int = 7) -> list[tuple[str, str]]:
    """``n`` distinct (goal, thing) pairs, deterministic for ``seed``: a demo's runs read like different work."""
    import random
    pairs = [(act.format(thing), thing) for thing in DEMO_THINGS for act in DEMO_ACTS]
    random.Random(seed).shuffle(pairs)
    return [pairs[i % len(pairs)] for i in range(n)]


def _demo_projects(n: int, seed: int = 7) -> list[tuple[str, str | None]]:
    import random
    rng = random.Random(seed + 1)
    return [DEMO_PROJECTS[rng.randrange(len(DEMO_PROJECTS))] for _ in range(n)]


def _varied(b: "_Builder", n: int, goal: str, thing: str, name: str, repo: str | None) -> None:
    """One varied demo run through the real service: its shape picked by ``n`` (converged, stopped on a refutation, failed
    its audit, waiting on a human's decision, or still active), its claims worded from what it is about."""
    subject = thing[0].upper() + thing[1:]
    slug = "".join(ch if ch.isalnum() else "_" for ch in thing.removeprefix("the "))
    path, check = f"src/{slug}.py", f"tests/test_{slug}.py"
    shape = n % 6
    session = f"s-demo-{n}"
    if shape in (0, 3):  # converged, independently audited
        run = b.begin(name, session, goal, _graph(
            "C0", [("C0", "ordinary", f"{subject} does what the goal asks."),
                   ("C1", "needs-experiment", f"The tests for {thing} pass."),
                   ("C2", "ordinary", f"{subject} has one owner module.")], [("C0", "C1"), ("C0", "C2")]), repo=repo)
        b.research(run, "C2", ref=path)
        b.research(run, "C1", ref=path)
        b.spike(run, "C1", f"python -m pytest {check}", {check: f"check {n}\n".encode()})
        b.research(run, "C0", ref=path)
        b.freeze(run)
        b.audit(run, "pass", [f"The spike covers {thing}."])
        b.evaluate(run, "report_convergence")
    elif shape == 1:  # stopped: evidence refuted a claim
        run = b.begin(name, session, goal, _graph(
            "C0", [("C0", "ordinary", f"No caller depends on the old behaviour of {thing}."),
                   ("C1", "ordinary", f"{subject} is only used by the web app.")], [("C0", "C1")]), repo=repo)
        b.research(run, "C1", result="refutes", ref=f"jobs/{slug}.py")
        b.evaluate(run, "stop")
    elif shape == 2:  # an audit caught a problem
        run = b.begin(name, session, goal, _graph(
            "C0", [("C0", "ordinary", f"{subject} handles every input it is given."),
                   ("C1", "needs-experiment", f"The edge-case test for {thing} passes.")], [("C0", "C1")]), repo=repo)
        b.research(run, "C0", ref=path)
        b.research(run, "C1", ref=path)
        b.spike(run, "C1", f"python -m pytest {check} -k edge", {check: f"edge {n}\n".encode()})
        b.freeze(run)
        b.audit(run, "fail", [f"The edge-case test never feeds {thing} an empty input (research artifact "
                              f"sha256:{hashlib.sha256(name.encode()).hexdigest()} cites {check}:1,2)."])
    elif shape == 4:  # waits on a human's decision
        run = b.begin(name, session, goal, _graph(
            "C0", [("C0", "ordinary", f"{subject} keeps working for existing users."),
                   ("C1", "needs-decision", f"Ship the change to {thing} behind a flag first.")], [("C0", "C1")]), repo=repo)
        b.research(run, "C0", ref=path)
        b.evaluate(run, "report_convergence")
    else:  # still being worked on
        run = b.begin(name, session, goal, _graph(
            "C0", [("C0", "ordinary", f"{subject} is measurably better after the change."),
                   ("C1", "ordinary", f"The current behaviour of {thing} is written down.")], [("C0", "C1")]), repo=repo)
        b.research(run, "C1", ref=f"docs/{slug}.md")
    b.stamp(f"demo-{n}", name, session)
