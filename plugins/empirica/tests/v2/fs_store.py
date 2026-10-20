"""A real, heterogeneous run store on the filesystem at volume, for ``test_observer_fs*.py``.

A handful of template runs are driven through the bridge (``adapters.bridge.build_service``: the real run repository,
timeline, Git artifact store, workspace and spike harness) in one repository whose working tree holds the files their
spikes bind (``observer_store.BOUND``: distinct, overlapping sets of realistic size). Some stay active, some are stopped,
one has research only. The repository (its working tree and ``.git``) is then copied ``workspaces`` times, and the store
is filled with ``n`` runs: run ``i`` lives in repository ``i % workspaces`` (one project each, recorded as the bridge
records it, ``adapters.state.locations``), as a copy of template ``(i // workspaces) % templates`` (its ``run.json`` and
``timeline.jsonl`` copied, its ``workspace.json`` naming its repository, its artifact ref created at the template's
commit, one ``git update-ref --stdin`` per repository). In every ``drift_every``-th repository ``DRIFTED`` was edited
after the spikes ran, so the runs there that bind it have drifted. Every clone reads through the same adapters as its
template, with a valid history: nothing is ``partial``.

    build_fs_store(base, n, workspaces=20) -> FsStore(home, repos, keys, templates)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

from sandbox_isolation import force_absent_sandbox_home

import governance_setup
from adapters import bridge
from adapters.git.artifact_repo import _COMMIT_IDENTITY, GitArtifactRepository
from adapters.state import identity
from adapters.state.repository import FilesystemRunRepository
from application import generic_spike
from application.location import decode_handle, storage_id
from application.ports import ObservationSnapshot
from application.protocol import host_profile_id
from assertions import (action_graph, action_investigate, action_research, action_route,
                        observe_action, start_run)
from core import spike_plan
from core.evaluation import plan_spike_request, plan_spike_result
from core.freshness import FileObservation, ObservationState, observations_digest
from core.spike_plan_draft import canonicalize_spike_plan_draft
from core.records import RunKey
from driver import FakeLocalImageResolver, FakeSpikeAuthoritySelector
from governance_setup import approve_current, end_with_consent
from observer_store import BOUND, DRIFTED, _generic_spike_action, _GenericFixtureHarness, body
force_absent_sandbox_home()

PROFILE = host_profile_id("claude-code")
FILES = ("run.json", "timeline.jsonl", "workspace.json")


@dataclass
class FsStore:
    home: Path
    repos: list[Path]
    keys: list[RunKey]
    templates: list[RunKey]           # one run of each template, in template order
    repo_of: dict[RunKey, Path] = field(default_factory=dict)

    @property
    def repo(self) -> Path:
        """The first repository (a test of one repository's refs)."""
        return self.repos[0]

    def workspace(self, n: int) -> Path:
        return self.repos[n]


#: Every git call the fixture makes is hermetic: a fixed author, committer and date (as the plugin's own commits,
#: ``artifact_repo._COMMIT_IDENTITY``) and no global or system configuration, so a machine with no git identity (a CI
#: runner, an empty HOME) builds the same store.
_GIT_ENV = {**_COMMIT_IDENTITY, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def _git(cwd: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          input=stdin, env={**os.environ, **_GIT_ENV}).stdout


def _graph(goal: str, kinds: list[str]) -> dict:
    ids = [f"C{n}" for n in range(len(kinds))]
    return {"root": "C0",
            "claims": [{"id": c, "text": f"{goal} ({c})", "kind": k, "gating": True} for c, k in zip(ids, kinds)],
            "edges": [{"from": "C0", "to": c, "type": "SupportedBy"} for c in ids[1:]]}


#: The templates: (session, goal, claim kinds, [(claim, cited file, files its spike binds or None)], ends stopped).
TEMPLATES = (
    ("t-a", "Keep the core path fast.", ["ordinary", "needs-experiment"],
     [("C1", "src/core/a.py", ("src/core/a.py", "src/core/b.py", "src/util/x.py"))], False),
    ("t-b", "Document the core and util paths.", ["ordinary", "needs-experiment", "needs-experiment"],
     [("C1", "src/core/b.py", ("src/core/b.py", "src/core/c.py")),
      ("C2", "src/util/x.py", ("docs/guide.md", "src/util/x.py"))], False),
    ("t-c", "Drop the old core helper.", ["ordinary", "needs-experiment"],
     [("C1", "src/core/a.py", ("src/core/a.py", "src/core/c.py", "tests/t1.py"))], True),
    ("t-d", "Read the util module once.", ["ordinary"], [("C0", "src/util/y.py", None)], False),
    ("t-e", "Cache the util results.", ["ordinary", "needs-experiment"],
     [("C1", "src/util/y.py", ("src/util/y.py", "src/core/a.py"))], True),
)


def _seed_historical_spike(service, run_id: str, action: dict) -> None:
    """Persist sealed generic historical spike evidence without public ingress or workload execution."""
    coordinator = service._coordinator
    key = decode_handle(run_id)
    assert key is not None
    loaded = coordinator._load(key)
    command = {"type": "ObserveAction", "run_id": run_id, "action": action}
    snapshot = coordinator._assemble(key, loaded.state, command, require_graph=True)
    backend = generic_spike.GenericQualifiedBackend(
        "docker", "docker-isolated-v1", "sha256:" + "a" * 64, "sha256:" + "b" * 64)
    sealed = generic_spike.prepare_generic_spike(
        canonicalize_spike_plan_draft(action["plan"]), coordinator.workspace,
        coordinator.image_resolver, backend)
    request = plan_spike_request(
        snapshot, action, generic_spike.sealed_request_facts(sealed, backend))
    assert request.result_type == "Allow", request
    state, _, _, _ = coordinator._commit(
        key, loaded.read.revision, snapshot, loaded.state, request.intent.artifacts)
    request_body = request.intent.artifacts[0]["body"]
    harness_run_id = "r-" + request.intent.artifacts[0]["artifact_id"].split(":", 1)[1][:32]
    harness_request = generic_spike.GenericHarnessRequest(
        harness_run_id, backend, sealed.plan, sealed.plan.digest,
        spike_plan.FRAMEWORK_SPIKE_POLICY.digest, sealed.snapshot)
    result = _GenericFixtureHarness(0).run(harness_request)
    observations = tuple(FileObservation(
        binding.path, ObservationState.PRESENT, binding.sha256)
        for binding in sealed.snapshot.file_bindings)
    observed = ObservationSnapshot(
        observations, sealed.snapshot.basis_id, observations_digest(observations))
    loaded = coordinator._load(key)
    snapshot = coordinator._assemble(key, state, command, require_graph=True)
    planned = plan_spike_result(
        snapshot, request_body,
        generic_spike.sealed_result_facts(result))
    assert planned.result_type == "Allow", planned
    coordinator._commit(
        key, loaded.read.revision, snapshot, state, planned.intent.artifacts,
        additional_observations=(("spike_execution", observed),))
    checked = coordinator._load(key)
    coordinator._assemble(
        key, checked.state, {"type": "GetRun", "run_id": run_id}, require_graph=False)


def _drive(service, repo: Path, session: str, goal: str, kinds: list[str], steps, stopped: bool) -> None:
    """Drive one run; test-only historical spikes preserve bound-file complexity coverage."""
    started = service.dispatch(start_run(goal=goal, request_id=f"start-{session}", project=identity.project_id(repo),
                                         session=session))
    run_id = started["result"]["run"]["id"]
    for action in (action_route(reason="observer"), action_graph(_graph(goal, kinds))):
        service.dispatch(observe_action(run_id=run_id, action=action))
    approve_current(service._coordinator, run_id)
    service.dispatch(observe_action(run_id=run_id, action=action_investigate()))
    for claim, cited, bound in steps:
        line = (repo / cited).read_text().splitlines()[0]
        admitted = service.dispatch(observe_action(run_id=run_id, action=action_research(
            claim_id=claim, source_kind="code", result="supports", payload={"source_ref": cited, "citation": line})))
        assert admitted["result"]["type"] == "Allow", admitted
        if bound:
            _seed_historical_spike(service, run_id, _generic_spike_action(claim, sorted(bound)))
    if stopped:
        end_with_consent(service._coordinator, run_id)


_TEMPLATE: list[Path] = []  # the bridge-driven store, built once per process (driving the bridge costs seconds)


def _drive_one(base: Path, n: int) -> None:
    """Template ``n`` driven through the bridge in its own home and repository under ``base`` (``__main__``: each
    template in its own process, so they are driven side by side; the bridge reads its home and workspace from the
    process's environment and directory)."""
    home, repo = base / "home", base / "work" / "template"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    for path, size in BOUND.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(body(path, size))
    os.environ["EMPIRICA_HOME"] = str(home)
    os.chdir(repo)
    sequence = (uuid.UUID(int=k) for k in range(1 + 1000 * n, 1 << 16))
    with mock.patch.object(governance_setup, "uuid4", lambda: next(sequence)):
        service = bridge.build_service(PROFILE)
    selector = FakeSpikeAuthoritySelector()
    selector.qualified_next = True
    service._coordinator.spike_authority_selector = selector
    service._coordinator.image_resolver = FakeLocalImageResolver()
    _drive(service, repo, *TEMPLATES[n])


def _template() -> Path:
    """Every template driven (side by side, one process each), then gathered into one home and one repository whose
    object store holds every template's history: ``<base>/home`` and ``<base>/work/template``."""
    if not _TEMPLATE:
        import atexit
        import sys
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        atexit.register(tmp.cleanup)
        base = Path(tmp.name).resolve()
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
        children = [subprocess.Popen([sys.executable, __file__, str(base / f"t{n}"), str(n)], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                    for n in range(len(TEMPLATES))]
        for n, child in enumerate(children):
            output, _ = child.communicate()
            if child.returncode:
                raise RuntimeError(f"template {n} failed:\n{output.decode(errors='replace')}")
        repo, home = base / "work" / "template", base / "home" / "projects"
        shutil.copytree(base / "t0" / "work" / "template", repo, symlinks=True)
        for n in range(len(TEMPLATES)):
            if n:
                _git(repo, "fetch", "-q", str(base / f"t{n}" / "work" / "template"),
                     "refs/empirica/*:refs/empirica/*")
            shutil.copytree(base / f"t{n}" / "home" / "projects", home, symlinks=True, dirs_exist_ok=True)
        _TEMPLATE.append(base)
    return _TEMPLATE[0]


def build_fs_store(base: Path, n: int, workspaces: int = 20, drift_every: int = 4) -> FsStore:
    base = Path(base).resolve()
    template = _template()
    home = base / "home"
    (home / "projects").mkdir(parents=True)
    runs, driven = FilesystemRunRepository(home), FilesystemRunRepository(template / "home")
    source_repo = template / "work" / "template"
    artifacts = GitArtifactRepository(source_repo)
    heads = artifacts.ref_heads()
    by_session = {}
    for p in (template / "home" / "projects").iterdir():
        for r in (p / "runs").iterdir():
            by_session[r.name] = RunKey(p.name, r.name, 1)
    templates = [by_session[storage_id(t[0])] for t in TEMPLATES]
    commits = {t: heads[artifacts.ref_for(t)] for t in templates}
    repos = []
    for w in range(min(workspaces, max(n, 1))):
        repo = base / "work" / f"repo-{w:02d}"
        shutil.copytree(source_repo, repo, symlinks=True)
        if w % drift_every == 0:
            (repo / DRIFTED).write_bytes(body(DRIFTED, BOUND[DRIFTED], version=2))
        repos.append(repo)
    keys, refs, repo_of = [], {repo: [] for repo in repos}, {}
    for i in range(n):
        w = i % len(repos)
        repo = repos[w]
        tpl = templates[(i // len(repos)) % len(templates)]
        project = storage_id(f"scale project {w}")
        key = RunKey(project, storage_id(f"scale run {i}"), 1)
        if i < len(repos):
            (home / "projects" / project).mkdir(parents=True, exist_ok=True)
            (home / "projects" / project / "project.json").write_text(json.dumps({"repo": str(repo)}))
        target = runs.generation_dir(key)
        target.mkdir(parents=True)
        for name in FILES[:2]:
            shutil.copyfile(driven.generation_dir(tpl) / name, target / name)
            os.chmod(target / name, 0o600)
        (target / "workspace.json").write_text(json.dumps({"workspace": str(repo)}))  # as the bridge records it
        refs[repo].append(f"create {artifacts.ref_for(key)} {commits[tpl]}\n")
        keys.append(key)
        repo_of[key] = repo
    for repo in repos:  # one process per repository: the clones' refs in, the templates' out
        _git(repo, "update-ref", "--stdin", stdin="".join(refs[repo]) +
             "".join(f"delete {artifacts.ref_for(t)}\n" for t in templates))
    firsts = [keys[k] for k in range(0, min(n, len(repos) * len(templates)), len(repos))]
    return FsStore(home, repos, keys, templates=firsts, repo_of=repo_of)


def replace_run(store: FsStore, key: RunKey, other: RunKey) -> None:
    """Give ``key`` the state and timeline of ``other`` (each written as the bridge writes: a new file renamed over the
    old), and its history ref ``other``'s commit (every repository holds every template's commits): one run being
    written, as an observer sees it."""
    runs = FilesystemRunRepository(store.home)
    for name in ("run.json", "timeline.jsonl"):
        staged = runs.generation_dir(key) / f".{name}.new"
        shutil.copyfile(runs.generation_dir(other) / name, staged)
        os.replace(staged, runs.generation_dir(key) / name)
    commit = GitArtifactRepository(store.repo_of[other]).ref_heads()[GitArtifactRepository(store.repo_of[other]).ref_for(other)]
    _git(store.repo_of[key], "update-ref", GitArtifactRepository(store.repo_of[key]).ref_for(key), commit)


if __name__ == "__main__":  # one template, driven in its own process (``_template``)
    import sys
    _drive_one(Path(sys.argv[1]), int(sys.argv[2]))
