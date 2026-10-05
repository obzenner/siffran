#!/usr/bin/env python3
"""Observer read model (``contracts/empirica-ui-view/v1``): agreement, scope, read-only, caching,
and the on-disk store and git adapters.

The store under test is ``tests/v2/observer_store.build_store``: real runs driven through the v2
service. The observer must agree with the plugin's own public projections of the same runs (the
observer reads the plugin and does not judge). It must be independent of the host profile, write
nothing, and re-project only what changed.

Run: python3 plugins/empirica/tests/test_observer.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLUGIN = _HERE.parent
_ROOT = _PLUGIN.parents[1]
for _path in (_HERE / "v2", _HERE, _PLUGIN, _ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import validate_ui_view  # noqa: E402
from application import observer as observer_module  # noqa: E402
from application import protocol as _proto  # noqa: E402
from application.observer import Observer  # noqa: E402
from assertions import (action_graph, action_investigate, action_research, action_route,  # noqa: E402
                        get_argument, get_run, observe_action, start_run)
from observer_store import DriverSource, build_store  # noqa: E402

_STORE = build_store()


def _observe(store=_STORE, **kw):
    return Observer(DriverSource(store), **kw).observe()


def _rows(view):
    return {run["key"]: run for project in view["projects"] for run in project["runs"]}


class ContractTests(unittest.TestCase):
    def test_the_store_satisfies_the_contract_and_its_mirrors(self):
        view, details = _observe()
        self.assertEqual(validate_ui_view.check(view, details), [])

    def test_every_scenario_is_present_and_every_diagnostic_is_exercised(self):
        view, _ = _observe()
        rows = _rows(view)
        self.assertLessEqual(set(_STORE.expect.values()), set(rows))
        schema = validate_ui_view.schemas()[0]
        diagnostics = validate_ui_view._enum(schema["$defs"]["run"]["properties"], "diagnostic")
        seen = {row["diagnostic"] for row in rows.values()} - {None}
        # These need a refusing workspace, damaged history or a repository without the refs:
        # RecoveryTests, ClassificationTests and FilesystemStoreTests cover them.
        self.assertEqual(seen | {"workspace-unavailable", "history-corrupt", "history-missing"},
                         diagnostics)


    def test_history_kinds_are_exactly_the_kinds_the_plugin_records(self):
        """``HISTORY_KINDS`` is what the contract's step kinds are held to; here it is held to what
        the plugin's own service actually writes, across every scenario, in both directions."""
        from application.history_records import HISTORY_KINDS
        _, details = _observe()
        recorded = {step["kind"] for detail in details.values() for step in detail["steps"]}
        self.assertEqual(recorded, set(HISTORY_KINDS))


class AgreementTests(unittest.TestCase):
    """Every derived value equals the plugin's own public projection of the same run."""

    @classmethod
    def setUpClass(cls):
        cls.view, cls.details = _observe()
        cls.rows = _rows(cls.view)
        cls.case = __import__("observer_store")._Author()

    def _public(self, key, envelope):
        return _STORE.driver.request(envelope(run_id=_handle(key)))["result"]

    def test_claims_audit_and_residuals_equal_the_public_projections(self):
        latest = {}
        for key, row in self.rows.items():
            project, run, generation = key.split("/")
            if latest.get((project, run), (0,))[0] < int(generation):
                latest[(project, run)] = (int(generation), key)
        checked = 0
        for _, key in latest.values():
            row = self.rows[key]
            if row["diagnostic"] is not None or not row["claims"]:
                continue
            argument = self._public(key, get_argument)
            runview = self._public(key, get_run)["run"]
            states = {c["claim_id"]: (c["state"], c["kind"], c["gating"]) for c in argument["argument"]["claims"]}
            self.assertEqual({c["id"]: (c["state"], c["kind"], c["gating"]) for c in row["claims"]},
                             states, key)
            self.assertEqual(row["audit"], runview["audit"]["state"], key)
            self.assertEqual(row["state"], runview["status"], key)
            detail = self.details[key]
            self.assertEqual([r["code"] for r in detail["residuals"]],
                             [r["code"] for r in runview["residuals"]], key)
            self.assertEqual(detail["stale"], runview["freshness"]["changes"], key)
            self.assertEqual(detail["audit"]["findings"], runview["audit"]["findings"], key)
            checked += 1
        self.assertGreaterEqual(checked, 6)

    def test_scenarios_read_as_the_plugin_left_them(self):
        r = {name: self.rows[key] for name, key in _STORE.expect.items()}
        d = {name: self.details[key] for name, key in _STORE.expect.items()}
        claims = {name: {c["id"]: c for c in row["claims"]} for name, row in r.items()}
        self.assertEqual((r["converged"]["state"], r["converged"]["audit"]), ("converged", "passed"))
        self.assertEqual({c["review"] for c in claims["converged"].values()}, {"passed"})
        self.assertEqual(d["converged"]["auditor"] is not None, True)
        self.assertEqual(claims["refuted"]["C1"]["state"], "discarded")
        self.assertEqual(claims["refuted"]["C1"]["blocker"], "claim.refuted")
        self.assertEqual(claims["stale"]["C1"]["blocker"], "claim.spike_stale")
        self.assertIn("src/decode.py", [s["path"] for s in d["stale"]["stale"]])
        self.assertEqual(r["budget"]["state"], "stopped_budget")
        self.assertFalse(claims["frozen"]["C1"]["gating"])
        self.assertEqual(claims["decision"]["C1"]["state"], "blocked")
        self.assertEqual(r["audit-failed"]["previous"], _STORE.expect["decision"])
        self.assertEqual({c["review"] for c in claims["audit-failed"].values()}, {"failed"})
        self.assertTrue(d["audit-failed"]["audit"]["findings"])
        self.assertEqual(r["history-unavailable"]["diagnostic"], "history-unavailable")
        self.assertEqual(r["history-unavailable"]["state"], "active")
        self.assertIsNotNone(d["history-unavailable"]["budgets"])
        self.assertEqual((r["older-format"]["diagnostic"], r["older-format"]["state"]),
                         ("older-format", "converged"))
        self.assertEqual(d["older-format"]["goal"], "Render the legend from one table.")
        self.assertEqual((r["unreadable"]["diagnostic"], r["unreadable"]["state"]), ("unreadable", None))
        self.assertEqual({cid: c["review"] for cid, c in claims["reaudit"].items()},
                         {"C0": "unreviewed", "C1": "stale", "C2": "unreviewed", "C3": "changed"})
        self.assertEqual(r["reaudit"]["audit"], "failed")
        self.assertEqual((claims["decision"]["C0"]["blocker"], claims["decision"]["C0"]["blocker_claim"]),
                         ("claim.human_decision", "C1"))

    def test_the_output_does_not_depend_on_the_host_profile(self):
        reference = _observe()
        for profile_id in _proto.profile_ids():
            with self.subTest(profile=profile_id):
                self.assertEqual(_observe(profile_id=profile_id), reference)


class _Refuse:
    """Wraps a port; any method outside ``allowed`` raises."""

    def __init__(self, inner, allowed):
        self._inner, self._allowed = inner, allowed

    def __getattr__(self, name):
        if name not in self._allowed and not name.startswith("_"):
            raise AssertionError(f"observer called {name}")
        return getattr(self._inner, name)


class ReadOnlyTests(unittest.TestCase):
    def test_the_observer_only_reads(self):
        drv = _STORE.driver
        source = DriverSource(_STORE)
        source._runs = _Refuse(drv._runs, {"read", "generations"})
        source._runs._store = drv._runs._store
        source._artifacts = _Refuse(drv._artifacts, {"read"})
        source._workspace = _Refuse(drv._workspace, {"observe"})
        before = json.dumps(drv.operational_state(), sort_keys=True, default=str)
        Observer(source).observe()
        self.assertEqual(json.dumps(drv.operational_state(), sort_keys=True, default=str), before)


class CacheTests(unittest.TestCase):
    def test_an_unchanged_store_is_not_re_projected_and_a_change_touches_one_run(self):
        store = build_store()
        obs = Observer(DriverSource(store))
        first, _ = obs.observe()
        with _counting_assemble() as calls:
            again, _ = obs.observe()
            self.assertEqual((again["token"], calls), (first["token"], []))
            key = store.expect["stale"]
            store.driver.request(observe_action(run_id=_handle(key), action=action_research(
                claim_id="C0", source_kind="code", result="supports")))
            changed, _ = obs.observe()
        self.assertNotEqual(changed["token"], first["token"])
        self.assertEqual(len(calls), 1)
        before, after = _rows(first), _rows(changed)
        self.assertEqual([k for k in before if before[k]["version"] != after[k]["version"]], [key])

    def test_a_workspace_edit_changes_exactly_the_runs_whose_spikes_depend_on_it(self):
        store = build_store()
        obs = Observer(DriverSource(store))
        first, _ = obs.observe()
        store.driver.workspace_write("src/cache.py", b"cache v2\n")
        with _counting_assemble() as calls:
            changed, _ = obs.observe()
        self.assertEqual(len(calls), 1)
        before, after = _rows(first), _rows(changed)
        key = store.expect["converged"]
        self.assertEqual([k for k in before if before[k]["version"] != after[k]["version"]], [key])
        claim = {c["id"]: c for c in after[key]["claims"]}["C1"]
        self.assertEqual((claim["state"], claim["blocker"]), ("open", "claim.spike_stale"))
        self.assertEqual(Observer(DriverSource(store)).observe()[0], changed)


class UnobservableWorkspaceTests(unittest.TestCase):
    def test_a_workspace_that_refuses_observation_is_a_diagnostic(self):
        from application.observation import ObservationUnavailable

        class Refusing:
            def observe(self, paths):
                raise ObservationUnavailable("unmounted")

        source = DriverSource(_STORE)
        source.workspace = lambda key: Refusing()
        view, details = Observer(source).observe()
        stale = _rows(view)[_STORE.expect["stale"]]
        self.assertEqual((stale["diagnostic"], stale["claims"]), ("workspace-unavailable", []))
        self.assertEqual(validate_ui_view.check(view, details), [])


class RecoveryTests(unittest.TestCase):
    def test_a_workspace_that_comes_back_is_read_again_by_the_same_observer(self):
        from application.observation import ObservationUnavailable

        class Refusing:
            def observe(self, paths):
                raise ObservationUnavailable("unmounted")

        store = build_store()
        source = DriverSource(store)
        obs = Observer(source)
        key = store.expect["stale"]
        healthy = _rows(obs.view())[key]
        source.workspace = lambda k: Refusing()
        self.assertEqual(_rows(obs.view())[key]["diagnostic"], "workspace-unavailable")
        del source.workspace
        self.assertEqual(_rows(obs.view())[key], healthy)


class ClassificationTests(unittest.TestCase):
    def _inject(self, value):
        from application.location import storage_id
        from core.records import RunKey
        store = build_store()
        key = RunKey(storage_id("atlas"), storage_id("s-injected"), 1)
        store.driver.inject_run_state(key, value)
        store.times[key] = 1.0
        view, details = _observe(store)
        self.assertEqual(validate_ui_view.check(view, details), [])
        return _rows(view)[f"{key.project_id}/{key.run_id}/1"], store

    def _current_state(self):
        store = build_store()
        project, run, generation = store.expect["converged"].split("/")
        from core.records import RunKey
        return json.loads(json.dumps(store.driver._runs.read(RunKey(project, run, int(generation))).value))

    def test_a_current_format_state_that_breaks_an_invariant_is_unreadable_not_older(self):
        state = self._current_state()
        state["budgets"]["passes_used"] = state["budgets"]["max_passes"] + 5
        row, _ = self._inject(state)
        self.assertEqual((row["diagnostic"], row["state"]), ("unreadable", None))

    def test_states_from_earlier_versions_read_goal_and_status(self):
        for older in ({**self._current_state(), "modes": {}},
                      {"schema": "empirica.run/1", "goal": "Old.", "status": "active"}):
            with self.subTest(keys=sorted(older)[:3]):
                row, _ = self._inject(older)
                self.assertEqual(row["diagnostic"], "older-format")
                self.assertIn(row["state"], {"converged", "active"})

    def test_a_run_whose_history_cannot_be_assembled_marks_only_that_run(self):
        """A recorded artifact keeps its content address, so a malformed one is a producer bug
        that ``traverse_history`` admits; assembling it raises a plain error. Simulated here."""
        store = build_store()
        real = observer_module.assemble

        def assemble(state, stored, workspace, **kw):
            if kw["run_id"] == _handle(store.expect["stale"]):
                raise KeyError("harness_request_id")
            return real(state, stored, workspace, **kw)

        with mock.patch.object(observer_module, "assemble", assemble):
            rows = _rows(_observe(store)[0])
        self.assertEqual(rows[store.expect["stale"]]["diagnostic"], "history-corrupt")
        self.assertEqual([k for k, r in rows.items() if r["diagnostic"] == "history-corrupt"],
                         [store.expect["stale"]])


def _handle(key):
    """The plugin's opaque run handle for a run key of the view."""
    from application.location import encode_handle
    from core.records import RunKey
    project, run, generation = key.split("/")
    return encode_handle(RunKey(project, run, int(generation)))


@contextlib.contextmanager
def _counting_assemble():
    """Count the observer's snapshot assemblies (each is one run re-projected)."""
    calls: list[int] = []
    real = observer_module.assemble
    with mock.patch.object(observer_module, "assemble", lambda *a, **k: calls.append(1) or real(*a, **k)):
        yield calls


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout


class FilesystemStoreTests(unittest.TestCase):
    """The real adapters: a run started through the bridge in a temporary repository."""

    PROFILE = "claude-code@2.1.278"

    @classmethod
    def setUpClass(cls):
        """One run for every case (git appends are slow); each case only reads."""
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name).resolve()
        cls.home, cls.repo = base / "home", base / "work" / "atlas"
        cls.repo.mkdir(parents=True)
        _git(cls.repo, "init", "-q")
        (cls.repo / "README.md").write_text("atlas\n")
        env, cwd = os.environ.get("EMPIRICA_HOME"), os.getcwd()
        os.environ["EMPIRICA_HOME"] = str(cls.home)
        os.chdir(cls.repo)
        try:
            cls._start()
        finally:
            os.chdir(cwd)
            if env is None:
                os.environ.pop("EMPIRICA_HOME", None)
            else:
                os.environ["EMPIRICA_HOME"] = env

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def _start(cls):
        from adapters import bridge
        from adapters.state import identity
        from governance_setup import approve_current
        service = bridge.build_service(cls.PROFILE)
        started = service.dispatch(start_run(goal="Cache the tile index.",
                                             project=identity.project_id(cls.repo), session="s1"))
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [
            {"id": "C0", "text": "A cold start reads one file.", "kind": "ordinary", "gating": True}],
            "edges": []}
        for action in (action_route(reason="observer"), action_graph(graph)):
            service.dispatch(observe_action(run_id=run_id, action=action))
        approve_current(service._coordinator, run_id)
        for action in (action_investigate(),
                       action_research(claim_id="C0", source_kind="code", result="supports",
                                       payload={"source_ref": "README.md:1", "citation": "atlas"})):
            admitted = service.dispatch(observe_action(run_id=run_id, action=action))
            assert admitted["result"]["type"] == "Allow", admitted

    def _snapshot(self):
        files = {p: p.read_bytes() for p in sorted(self.home.rglob("*")) if p.is_file()}
        git = sorted((str(p.relative_to(self.repo)), p.stat().st_size, p.stat().st_mtime_ns)
                     for p in (self.repo / ".git").rglob("*"))
        return files, git, _git(self.repo, "for-each-ref"), _git(self.repo, "status", "--porcelain")

    def test_a_run_on_disk_reads_fully_and_nothing_is_written(self):
        from adapters.observer.source import StoreSource
        before = self._snapshot()
        view, details = Observer(StoreSource(self.home, [self.repo])).observe()
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(validate_ui_view.check(view, details), [])
        (project,) = view["projects"]
        self.assertEqual((project["name"], project["repo"]), ("atlas", str(self.repo)))
        (run,) = project["runs"]
        self.assertEqual((run["diagnostic"], run["state"]), (None, "active"))
        self.assertEqual([(c["id"], c["state"], c["evidence"]) for c in run["claims"]],
                         [("C0", "approved", "research")])
        self.assertIsNotNone(run["updated"])

    def test_an_unchanged_poll_reads_no_history(self):
        from adapters.git.artifact_repo import GitArtifactRepository
        from adapters.observer.source import StoreSource
        obs = Observer(StoreSource(self.home, [self.repo]))
        first = obs.view()
        reads = []
        real = GitArtifactRepository.read
        GitArtifactRepository.read = lambda repo, key: reads.append(key) or real(repo, key)
        try:
            again = obs.view()
        finally:
            GitArtifactRepository.read = real
        self.assertEqual((again, reads), (first, []))

    def _home_where(self, repo):
        """A copy of the store whose project records ``repo`` (``None``: no record)."""
        from adapters.state.locations import PROJECT
        home = Path(self.enterContext(tempfile.TemporaryDirectory())) / "home"
        shutil.copytree(self.home, home, symlinks=True)
        (record,) = home.glob(f"projects/*/{PROJECT}")
        if repo is None:
            record.unlink()
        else:
            record.write_text(json.dumps({"repo": str(repo)}))
        return home

    def test_the_bridge_records_where_the_run_works_and_the_observer_finds_it_unasked(self):
        from adapters.observer.source import StoreSource
        from adapters.state.locations import PROJECT, WORKSPACE
        (record,) = self.home.glob(f"projects/*/{PROJECT}")
        (workspace,) = self.home.glob(f"projects/*/runs/*/gen-1/{WORKSPACE}")
        self.assertEqual((json.loads(record.read_text()), json.loads(workspace.read_text())),
                         ({"repo": str(self.repo)}, {"workspace": str(self.repo)}))
        view, details = Observer(StoreSource(self.home)).observe()
        (project,) = view["projects"]
        self.assertEqual((project["name"], project["runs"][0]["diagnostic"]), ("atlas", None))
        self.assertEqual(validate_ui_view.check(view, details), [])

    def test_without_its_repository_a_run_reads_its_state_only(self):
        from adapters.observer.source import StoreSource
        view, details = Observer(StoreSource(self._home_where(None), [])).observe()
        (run,) = view["projects"][0]["runs"]
        self.assertEqual((run["diagnostic"], run["state"], run["claims"]),
                         ("history-unavailable", "active", []))
        self.assertIsNone(view["projects"][0]["name"])
        self.assertEqual(validate_ui_view.check(view, details), [])

    def test_a_repository_without_the_run_s_history_says_so(self):
        from adapters.observer.source import StoreSource
        with tempfile.TemporaryDirectory() as other:
            clone = Path(other).resolve() / "atlas"  # a clone does not copy the artifact refs
            subprocess.run(["git", "clone", "-q", str(self.repo), str(clone)], check=True,
                           capture_output=True)
            (run,) = Observer(StoreSource(self._home_where(clone))).view()["projects"][0]["runs"]
        self.assertEqual((run["diagnostic"], run["state"]), ("history-missing", "active"))

    def test_a_recorded_repository_that_has_gone_is_no_repository(self):
        from adapters.observer.source import StoreSource
        home = self._home_where(Path(self.tmp.name) / "gone")
        (run,) = Observer(StoreSource(home)).view()["projects"][0]["runs"]
        self.assertEqual(run["diagnostic"], "history-unavailable")

    def test_the_command_line_prints_the_view_and_one_run(self):
        from adapters.observer import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--home", str(self.home), "--repo", str(self.repo)]), 0)
        view = json.loads(out.getvalue())
        key = view["projects"][0]["runs"][0]["key"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--home", str(self.home), "--run", key]), 0)
        self.assertEqual(json.loads(out.getvalue())["key"], key)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--home", str(self.home), "--repo", str(self.home)]), 2)


class LocationsTests(unittest.TestCase):
    """``adapters.state.locations`` and its writer, the bridge: a run on any host is found by its
    recorded location, whatever selector its project id came from."""

    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.home, self.repo = base / "home", base / "harbor"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        self.enterContext(mock.patch.dict(os.environ, {"EMPIRICA_HOME": str(self.home)}))
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)

    def _start(self, project):
        from adapters import bridge
        return bridge.build_service(FilesystemStoreTests.PROFILE).dispatch(
            start_run(goal="Index the harbour.", project=project, session="s1"))

    def test_a_project_id_no_identity_maps_is_found_by_its_record(self):
        """Pi derives its project from a hash of the working directory, which no repository maps
        to by the plugin's identity; the recorded location still finds its history."""
        from adapters.observer.source import StoreSource
        self.assertEqual(self._start("0123456789abcdef")["result"]["type"], "Allow")
        (project,) = Observer(StoreSource(self.home)).view()["projects"]
        self.assertEqual((project["name"], project["runs"][0]["diagnostic"]), ("harbor", None))

    def test_a_record_that_cannot_be_written_does_not_fail_the_run_or_cost_the_other_record(self):
        from adapters.state.locations import PROJECT, WORKSPACE
        from application.location import storage_id
        project = self.home / "projects" / storage_id("p")
        (project / "runs").mkdir(parents=True)
        project.chmod(0o555)  # project.json cannot be written; the run's own directory can
        self.addCleanup(project.chmod, 0o755)
        self.assertEqual(self._start("p")["result"]["type"], "Allow")
        self.assertFalse((project / PROJECT).exists())
        self.assertEqual(len(list(project.glob(f"runs/*/gen-1/{WORKSPACE}"))), 1)

    def test_git_resolves_each_recorded_path_once_not_every_poll(self):
        from adapters.observer import source as source_module
        self._start("p")
        calls = []
        real = source_module.toplevel
        with mock.patch.object(source_module, "toplevel", side_effect=lambda p: calls.append(p) or real(p)):
            source = source_module.StoreSource(self.home)
            for _ in range(3):
                source.refresh()
        self.assertEqual(calls, [self.repo])

    def test_a_stray_directory_in_the_store_hides_nothing(self):
        from adapters.observer.source import StoreSource
        self._start("p")
        (self.home / "projects" / "stray copy" / "runs" / "r" / "gen-1").mkdir(parents=True)
        (self.home / "projects" / "stray copy" / "runs" / "r" / "gen-1" / "run.json").write_text("{}")
        (self.home / "projects" / "stray two").mkdir()
        view = Observer(StoreSource(self.home)).view()
        rows = {p["name"]: [r["diagnostic"] for r in p["runs"]] for p in view["projects"]}
        self.assertEqual(rows, {"harbor": [None], None: ["unreadable"]})

    def test_the_view_token_changes_when_a_project_s_recorded_repository_does(self):
        from adapters.observer.source import StoreSource
        from adapters.state.locations import PROJECT
        self._start("p")
        observer = Observer(StoreSource(self.home))
        before = observer.view()["token"]
        moved = self.repo.parent / "harbour"
        shutil.copytree(self.repo, moved, symlinks=True)  # the same history, under another name
        (record,) = self.home.glob(f"projects/*/{PROJECT}")
        record.write_text(json.dumps({"repo": str(moved)}))
        after = observer.view()
        self.assertEqual(after["projects"][0]["name"], "harbour")
        self.assertNotEqual(after["token"], before)

    def test_recording_again_does_not_rewrite_and_a_bad_record_reads_as_none(self):
        from adapters.state.locations import PROJECT, Locations
        from core.records import RunKey
        locations, key = Locations(self.home), RunKey("p", "r", 1)
        locations.record(key, self.repo, self.repo)
        record = self.home / "projects" / "p" / PROJECT
        before = record.stat().st_mtime_ns
        locations.record(key, self.repo, self.repo)
        self.assertEqual((record.stat().st_mtime_ns, locations.repo("p")), (before, self.repo))
        for bad in ("not json", '{"repo": "relative/path"}', '{"repo": 7}', "[]"):
            with self.subTest(bad=bad):
                record.write_text(bad)
                self.assertIsNone(locations.repo("p"))
        record.unlink()
        os.symlink(self.repo / "elsewhere.json", record)
        self.assertIsNone(locations.repo("p"), "a symlinked record is refused")


if __name__ == "__main__":
    unittest.main()
