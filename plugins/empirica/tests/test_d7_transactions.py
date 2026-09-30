#!/usr/bin/env python3
"""D7-W transaction, committed-history, and projection invariants."""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import jsonschema  # noqa: E402
from adapters.audit import child_event  # noqa: E402
from adapters.git.artifact_repo import ArtifactCollision  # noqa: E402
from application import protocol  # noqa: E402
from application.governance import transact as governance_transact  # noqa: E402
from application.history_records import MANIFEST_KEYS, MANIFEST_KIND, MANIFEST_VERSION  # noqa: E402
from application.observation import observation_basis_body  # noqa: E402
from application.snapshot import (HistoryCorrupt, assemble, graph_from_history, make_artifact,  # noqa: E402
                                  state_digest, traverse_history)
from application.ports import (CapturedFile, HarnessResult, ObservationSnapshot,  # noqa: E402
                               WorkspaceCapture)
from application.run_state import decode_state, encode_state  # noqa: E402
from application.location import encode_handle, storage_id  # noqa: E402
from application.transaction import Coordinator as ProductionCoordinator  # noqa: E402
from core.evaluation import (  # noqa: E402
    EvaluationSnapshot, active_spike_heads, artifact, audit_binding, audit_passes,
    claim_digest, evaluate_snapshot, frozen_semantic_digest, independence, valid_graph)
from core.freshness import (FileObservation, ObservationState, canonical_digest,  # noqa: E402
                            observations_digest)

# Sentinel: leave the governance context author unchanged (None means set author to null).
_KEEP = object()


class CoordinatorWithTestInvocation(ProductionCoordinator):
    """Direct coordinator tests supply protocol-required host provenance."""

    def handle(self, command, request_id):
        if command.get("type") == "StartRun" and "invocation" not in command:
            command = {**command, "invocation": dict(TEST_INVOCATION)}
        return super().handle(command, request_id)
from core.projection import project_runview  # noqa: E402
from core.records import (ABSENT, Artifact, Conflict, Corrupt, Present, Revision,  # noqa: E402
                          RunKey)
from core.run import OperationalState  # noqa: E402
from core.governance import initial as initial_governance  # noqa: E402
from governance_setup import TEST_INVOCATION, AUTHOR, AUDITOR, approve_current  # noqa: E402

# The response schema rooted at its RunView definition (same $id, so shared refs resolve).
RUN_VIEW = jsonschema.validators.validator_for(protocol._RESPONSE_SCHEMA)(
    {key: protocol._RESPONSE_SCHEMA[key] for key in ("$schema", "$id", "$defs")}
    | {"$ref": "#/$defs/runView"},
    registry=protocol._SCHEMA_REGISTRY)


class Runs:
    def __init__(self):
        self.data = {}
        self.seq = 0
        self.conflicts = 0
        self.cas_calls = 0
        self.conflict_on = set()
        self.crashes = 0
        self.read_calls = 0
        self.bump_on_read = set()
        self.replace_on_read = {}

    def generations(self, project, session):
        return sorted(k.generation for k in self.data if k.project_id == project and k.run_id == session)

    def read(self, key):
        self.read_calls += 1
        entry = self.data.get(key, ABSENT)
        if self.read_calls in self.replace_on_read and isinstance(entry, Present):
            self.seq += 1
            entry = Present(self.replace_on_read.pop(self.read_calls), Revision(str(self.seq)))
            self.data[key] = entry
        elif self.read_calls in self.bump_on_read and isinstance(entry, Present):
            self.seq += 1
            entry = Present(entry.value, Revision(str(self.seq)))
            self.data[key] = entry
        return entry

    def create(self, key, value):
        if key in self.data:
            raise Conflict(key, None, "exists")
        self.seq += 1
        revision = Revision(str(self.seq))
        self.data[key] = Present(value, revision)
        return revision

    def compare_and_set(self, key, value, expected):
        self.cas_calls += 1
        current = self.data[key]
        if self.crashes:
            self.crashes -= 1
            raise RuntimeError("injected crash between append and CAS")
        if self.conflicts:
            self.conflicts -= 1
            raise Conflict(key, expected, "injected")
        if self.cas_calls in self.conflict_on:
            self.conflict_on.remove(self.cas_calls)
            raise Conflict(key, expected, "injected scheduled")
        if current.revision != expected:
            raise Conflict(key, expected, "stale")
        self.seq += 1
        revision = Revision(str(self.seq))
        self.data[key] = Present(value, revision)
        return revision


class Artifacts:
    def __init__(self):
        self.values = {}
        self.append_calls = 0
        self.read_calls = 0

    def append(self, key, value):
        self.append_calls += 1
        prior = self.values.setdefault(key, {})
        if value.artifact_id in prior and prior[value.artifact_id] != value:
            raise ValueError("collision")
        prior[value.artifact_id] = value

    def read(self, key):
        self.read_calls += 1
        values = self.values.get(key)
        return ABSENT if values is None else Present(frozenset(values.values()), Revision("a"))


class Workspace:
    def __init__(self):
        self.calls = []
        self.files = {}

    def write(self, path, content):
        self.files[path] = bytes(content)

    def observe(self, paths):
        self.calls.append(tuple(paths))
        captured = []
        for path in paths:
            content = self.files.get(path)
            if content is None:
                observation = FileObservation(path, ObservationState.MISSING, None)
            else:
                observation = FileObservation(
                    path, ObservationState.PRESENT,
                    "sha256:" + hashlib.sha256(content).hexdigest())
            captured.append(CapturedFile(observation, content))
        basis = canonical_digest([(c.observation.path, c.observation.state.value,
                                   c.observation.sha256) for c in captured])
        return WorkspaceCapture(basis, tuple(captured))


class Harness:
    def __init__(self):
        self.calls = 0
        self.before_result = None

    def run(self, command, snapshot):
        self.calls += 1
        if self.before_result is not None:
            self.before_result()
        return HarnessResult(0, canonical_digest({"command": command}), snapshot.snapshot_digest)


def activate_investigation(coordinator: ProductionCoordinator, run_id: str) -> None:
    coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                        "action": {"kind": "route", "reason": "test"}}, "route")
    current = coordinator.handle({"type": "GetArgument", "run_id": run_id}, "setup-read")
    if current["result"].get("type") == "Allow":
        approve_current(coordinator, run_id)
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "investigate"}}, "investigate")


def basis_for(observations=(), basis_id="empty") -> Artifact:
    snapshot = ObservationSnapshot(
        tuple(observations), basis_id, observations_digest(observations))
    return make_artifact(observation_basis_body(
        (("workspace", snapshot),), protocol.policy_inputs("test")))


def initial() -> OperationalState:
    basis = basis_for()
    return OperationalState(
        protocol="empirica/v2", state_schema="empirica.run/2", goal="g",
        invocation={**TEST_INVOCATION, "signal": "transaction fixture"}, status="active",
        budgets={"max_passes": 8, "passes_used": 0, "max_spawns": 1, "spawns_used": 0,
                 "max_audit_spawns": 1, "audit_spawns_used": 0},
        governance=initial_governance("g", {"max_passes": 8, "max_spawns": 1, "max_audit_spawns": 1}),
        selected_graph_artifact_id=None, frozen_claim_ids=None, frozen_semantic_digest=None,
        route_stamp=None,
        investigation_stamp=None, stamp_seq=0, last_derivation_digest=None, children=(),
        observation_basis_digest=basis.artifact_id, committed_artifact_head_id=None,
    )


def manifest_for(parent, artifacts, state, *, basis_id="empty",
                 observation_digest=None) -> Artifact:
    body = {
        "kind": MANIFEST_KIND, "version": MANIFEST_VERSION, "parent": parent,
        "artifact_ids": [item.artifact_id for item in artifacts],
        "observation_basis_id": basis_id,
        "observation_digest": observation_digest or canonical_digest(()),
        "observation_basis_digest": state.observation_basis_digest,
        "next_state_digest": state_digest(state),
    }
    assert set(body) | {"artifact_id"} == MANIFEST_KEYS
    return make_artifact(body)


def chain(state: OperationalState, domain=()):
    basis = basis_for()
    state = replace(state, observation_basis_digest=basis.artifact_id)
    values = [basis, *[make_artifact(item["body"]) for item in domain]]
    manifest = manifest_for(None, values, state)
    return replace(state, committed_artifact_head_id=manifest.artifact_id), values + [manifest]


def projection_snapshot(*, frozen: bool = False, deferred: bool = False,
                        approved: bool = False):
    """Build a production-coordinator snapshot with a claim and optional approval/freeze.

    ``deferred`` extends the graph after the freeze so projection derives a genuine deferred
    scope; it is therefore invalid without ``frozen``.
    """
    if deferred and not frozen:
        raise ValueError("deferred scope requires a frozen scope")
    runs, artifacts_repo, workspace, harness = Runs(), Artifacts(), Workspace(), Harness()
    coordinator = CoordinatorWithTestInvocation(
        workspace, harness, runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
    started = coordinator.handle(
        {"type": "StartRun", "selector": {"project": "projection", "session": "base"},
         "goal": "g"}, "start")
    run_id = started["result"]["run"]["id"]
    activate_investigation(coordinator, run_id)
    root = {"id": "C0", "text": "unmet claim", "gating": True, "kind": "ordinary"}
    graph = {"root": "C0", "claims": [root], "edges": []}
    coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                        "action": {"kind": "graph", "payload": graph}}, "graph")
    activate_investigation(coordinator, run_id)
    if approved:
        coordinator.handle({"type": "ObserveAction", "run_id": run_id, "action": {
            "kind": "research", "claim_id": "C0", "source_kind": "code",
            "result": "supports", "payload": {
                "source_ref": "plugins/empirica/tests/test_d7_transactions.py",
                "citation": "The projection fixture records real supporting evidence.",
            }}}, "research")
    if frozen:
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "freeze"}}, "freeze")
    if deferred:
        graph = {"root": "C0", "claims": [root, {
            "id": "C1", "text": "deferred claim", "gating": True, "kind": "ordinary",
        }], "edges": [{"from": "C0", "to": "C1", "type": "SupportedBy"}]}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "extend")
    key = next(iter(runs.data))
    state = decode_state(runs.data[key].value)
    snapshot = coordinator._assemble(
        key, state, {"type": "GetRun", "run_id": run_id}, require_graph=True)
    return coordinator, snapshot


class D7TransactionTests(unittest.TestCase):
    def test_active_spike_heads_preserve_graph_order_for_freshness(self):
        graph = {"claims": [
            {"id": "Z", "text": "first", "kind": "needs-experiment", "gating": True},
            {"id": "A", "text": "second", "kind": "needs-experiment", "gating": True},
        ], "root": "Z", "edges": [{"from": "Z", "to": "A", "type": "SupportedBy"}]}
        history = tuple({
            "kind": "spike", "claim_id": claim["id"], "claim_digest": claim_digest(claim),
            "artifact_id": "sha256:" + format(index, "x") * 64, "harness_request_id": f"request-{claim['id']}",
            "file_bindings": [{"path": f"{claim['id']}.py", "sha256": "sha256:" + "a" * 64}],
        } for index, claim in enumerate(reversed(graph["claims"]), start=1))
        self.assertEqual(
            tuple(head.claim_id for head in active_spike_heads(history, graph)), ("Z", "A"))

    def test_orphan_is_invisible_and_order_comes_from_manifest(self):
        first = artifact("research", {"n": 1})
        second = artifact("research", {"n": 2})
        state, values = chain(initial(), (first, second))
        orphan = make_artifact(artifact("research", {"n": 3})["body"])
        malformed_orphan = Artifact("sha256:" + "f" * 64, "not-json")
        history = traverse_history(state, frozenset([orphan, malformed_orphan, *reversed(values)]))
        self.assertEqual([item["artifact_id"] for item in history
                          if item["kind"] != "observation_basis"],
                         [first["artifact_id"], second["artifact_id"]])

    def test_missing_manifest_and_state_witness_mismatch_fail_closed(self):
        state, values = chain(initial())
        with self.assertRaises(HistoryCorrupt):
            traverse_history(state, ())
        changed = replace(state, stamp_seq=1)
        with self.assertRaises(HistoryCorrupt):
            traverse_history(changed, values)

    def test_observation_basis_validation_and_non_ascii_path(self):
        observation = FileObservation(
            "src/é.py", ObservationState.PRESENT, "sha256:" + "a" * 64)
        unicode_basis = basis_for((observation,), "unicode")
        unicode_state = replace(initial(), observation_basis_digest=unicode_basis.artifact_id)
        unicode_manifest = manifest_for(
            None, (unicode_basis,), unicode_state, basis_id="unicode",
            observation_digest=observations_digest((observation,)))
        unicode_state = replace(
            unicode_state, committed_artifact_head_id=unicode_manifest.artifact_id)
        self.assertEqual(
            traverse_history(unicode_state, (unicode_basis, unicode_manifest))[0]["sources"][0]
            ["observations"][0]["path"],
            "src/é.py")

        def one_manifest(basis, *, state_basis=None, include_basis=True):
            state = replace(initial(), observation_basis_digest=state_basis or basis.artifact_id)
            included = (basis,) if include_basis else ()
            manifest = manifest_for(None, included, state, basis_id="case")
            return replace(state, committed_artifact_head_id=manifest.artifact_id), [*included, manifest]

        base = json.loads(unicode_basis.body)

        def mutate_basis(**changes):
            return make_artifact({**base, **changes})

        def mutate_source(**changes):
            return mutate_basis(sources=[{**base["sources"][0], **changes}])

        malformed = mutate_source(
            observations=[{"path": 1, "state": "bogus", "sha256": None}])
        malformed_source = mutate_source(name=1)
        mismatch = mutate_source(digest=canonical_digest(()))
        cases = {
            "missing observation basis": one_manifest(unicode_basis, include_basis=False),
            "malformed observation basis": one_manifest(malformed),
            "malformed observation basis source": one_manifest(malformed_source),
            "persisted observation digest mismatch": one_manifest(mismatch),
            "policy inputs list": one_manifest(mutate_basis(policy_inputs=[])),
            "policy inputs null": one_manifest(mutate_basis(policy_inputs=None)),
            "nonempty malformed clock inputs": one_manifest(mutate_basis(clock_inputs=[1])),
            "boolean observation basis version": one_manifest(mutate_basis(version=True)),
        }
        base_state, base_values = chain(initial())
        basis = base_values[0]
        prior_manifest = base_values[-1]
        duplicate_manifest = manifest_for(
            prior_manifest.artifact_id, (basis,), base_state, basis_id="empty")
        cases["observation basis deduplication mismatch"] = (
            replace(base_state, committed_artifact_head_id=duplicate_manifest.artifact_id),
            [*base_values, duplicate_manifest])
        other_basis = basis_for((), "other")
        cases["latest observation basis state witness mismatch"] = one_manifest(
            basis, state_basis=other_basis.artifact_id)
        for name, (state, values) in cases.items():
            with self.subTest(name=name), self.assertRaises(HistoryCorrupt):
                traverse_history(state, values)

    def test_cycle_duplicate_and_wrong_kind_fail_closed(self):
        state = initial()
        item = artifact("research", {"n": 1})
        value = make_artifact(item["body"])
        manifest = manifest_for(None, (value, value), state, basis_id="x")
        with self.assertRaises(HistoryCorrupt):
            traverse_history(replace(state, committed_artifact_head_id=manifest.artifact_id),
                             [manifest, value])
        with self.assertRaises(HistoryCorrupt):
            traverse_history(replace(state, committed_artifact_head_id=value.artifact_id), [value])

    def test_cas_retry_reuses_content_addresses_without_duplicates(self):
        runs, artifacts_repo, workspace = Runs(), Artifacts(), Workspace()
        coordinator = CoordinatorWithTestInvocation(workspace, Harness(), runs, artifacts_repo,
                                  "pi@0.84.1+pi-subagents@0.50.0", {})
        start = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                    "goal": "g"}, "start")
        run_id = start["result"]["run"]["id"]
        key = next(iter(runs.data))
        runs.conflicts = 1
        response = coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                       "action": {"kind": "route", "reason": "r"}}, "route")
        self.assertEqual(response["result"]["type"], "Allow")
        self.assertEqual(runs.cas_calls, 2)
        stored = list(artifacts_repo.values[key].values())
        self.assertEqual(len(stored), len({a.artifact_id for a in stored}))
        decoded = protocol._STATE_SCHEMA_ID
        self.assertEqual(decoded, "empirica.run/2")
        self.assertEqual(runs.data[key].value["route_stamp"], 1)
        traverse_history(decode_state(runs.data[key].value), stored)

    def test_append_once_delegates_idempotency_without_repository_preread(self):
        artifacts_repo = Artifacts()
        coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), Runs(), artifacts_repo,
                                  "pi@0.84.1+pi-subagents@0.50.0", {})
        key = RunKey("p", "s", 1)
        value = make_artifact({"kind": "graph", "root": "C0", "claims": [], "edges": []})
        coordinator._append_once(key, value)
        coordinator._append_once(key, value)
        self.assertEqual(artifacts_repo.read_calls, 0)
        self.assertEqual(artifacts_repo.append_calls, 2)
        self.assertEqual(artifacts_repo.values[key], {value.artifact_id: value})

    def test_append_once_surfaces_orphan_collision_without_repository_preread(self):
        artifacts_repo = Artifacts()
        coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), Runs(), artifacts_repo,
                                  "pi@0.84.1+pi-subagents@0.50.0", {})
        key = RunKey("p", "s", 1)
        artifacts_repo.append(key, Artifact("orphan-id", "original"))
        with self.assertRaisesRegex(ValueError, "collision"):
            coordinator._append_once(key, Artifact("orphan-id", "different"))
        self.assertEqual(artifacts_repo.read_calls, 0)
        self.assertEqual(artifacts_repo.values[key]["orphan-id"].body, "original")

    def test_append_collision_during_dispatch_is_closed_without_logical_publication(self):
        class CollisionArtifacts(Artifacts):
            fail = False

            def append(self, key, value):
                if self.fail:
                    raise ArtifactCollision(key, value.artifact_id)
                super().append(key, value)

        runs, artifacts_repo = Runs(), CollisionArtifacts()
        coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo,
                                  "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({
            "type": "StartRun", "selector": {"project": "p", "session": "s"}, "goal": "g",
        }, "start")
        run_id = started["result"]["run"]["id"]
        key = next(iter(runs.data))
        before_state = runs.data[key]
        before_artifacts = dict(artifacts_repo.values[key])
        artifacts_repo.fail = True

        response = coordinator.handle({
            "type": "ObserveAction", "run_id": run_id,
            "action": {"kind": "route", "reason": "r"},
        }, "route")

        self.assertEqual(response["result"], {
            "type": "Fault", "code": "unavailable", "fail_direction": "closed",
        })
        self.assertEqual(runs.data[key], before_state)
        self.assertEqual(artifacts_repo.values[key], before_artifacts)
        self.assertEqual(runs.cas_calls, 0)

    def test_repeated_domain_artifacts_are_idempotent_not_corrupting(self):
        runs, artifacts_repo, workspace = Runs(), Artifacts(), Workspace()
        coordinator = CoordinatorWithTestInvocation(workspace, Harness(), runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                      "goal": "g"}, "start")
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [{"id": "C0", "text": "t", "gating": True,
                                              "kind": "ordinary"}], "edges": []}
        graph_command = {"type": "ObserveAction", "run_id": run_id,
                         "action": {"kind": "graph", "payload": graph}}
        research_command = {"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "research", "claim_id": "C0",
                                       "source_kind": "code", "result": "supports", "payload": {
                                           "source_ref": "test_d7_transactions.py",
                                           "citation": "Repeated artifact fixture."}}}
        activate_investigation(coordinator, run_id)
        for command in (graph_command, graph_command, research_command, research_command):
            self.assertEqual(coordinator.handle(command, "repeat")["result"]["type"], "Allow")
            if command["action"]["kind"] == "graph":
                activate_investigation(coordinator, run_id)
        key = next(iter(runs.data))
        state = decode_state(runs.data[key].value)
        history = traverse_history(state, artifacts_repo.values[key].values())
        self.assertEqual([a["kind"] for a in history],
                         ["observation_basis", "graph", "research"])

    def test_deduplicated_noop_retries_if_revision_changed(self):
        runs, artifacts_repo = Runs(), Artifacts()
        coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                      "goal": "g"}, "start")
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [{"id": "C0", "text": "t", "gating": True,
                                              "kind": "ordinary"}], "edges": []}
        command = {"type": "ObserveAction", "run_id": run_id,
                   "action": {"kind": "graph", "payload": graph}}
        coordinator.handle(command, "first")
        baseline = runs.read_calls
        runs.bump_on_read.add(baseline + 2)
        response = coordinator.handle(command, "repeat")
        self.assertEqual(response["result"]["type"], "Allow")
        self.assertGreaterEqual(runs.read_calls - baseline, 4)

    def test_identical_derivation_does_not_double_consume_pass(self):
        runs, artifacts_repo, workspace = Runs(), Artifacts(), Workspace()
        coordinator = CoordinatorWithTestInvocation(workspace, Harness(), runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                      "goal": "g"}, "start")
        run_id = started["result"]["run"]["id"]
        activate_investigation(coordinator, run_id)
        graph = {"root": "C0", "claims": [{"id": "C0", "text": "t", "gating": True,
                                              "kind": "ordinary"}], "edges": []}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "g")
        activate_investigation(coordinator, run_id)
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "research", "claim_id": "C0",
                                       "source_kind": "code", "result": "supports", "payload": {
                                           "source_ref": "test_d7_transactions.py",
                                           "citation": "Derivation fixture."}}}, "r")
        command = {"type": "EvaluateRun", "run_id": run_id, "intent": "report_convergence"}
        coordinator.handle(command, "e1")
        coordinator.handle(command, "e2")
        key = next(iter(runs.data))
        self.assertEqual(runs.data[key].value["budgets"]["passes_used"], 1)

    def test_corrupt_repository_read_blocks_get_and_resolve(self):
        runs, artifacts_repo = Runs(), Artifacts()
        coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
        key = RunKey(storage_id("p"), storage_id("s"), 1)
        runs.data[key] = Corrupt("bad bytes")
        get_response = coordinator.handle({"type": "GetRun", "run_id": encode_handle(key)}, "get")
        self.assertEqual(get_response["result"]["type"], "Block")
        self.assertEqual(get_response["result"]["reasons"][0]["code"], "run.corrupt")
        resolve = coordinator.handle({"type": "ResolveRun",
                                      "selector": {"project": "p", "session": "s"}}, "resolve")
        self.assertEqual(resolve["result"]["type"], "Block")
        self.assertEqual(resolve["result"]["reasons"][0]["code"], "run.corrupt")

    def test_inconsistent_frozen_semantics_uses_fixed_safe_corrupt_projection(self):
        runs, artifacts_repo = Runs(), Artifacts()
        coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo,
                                  "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                      "goal": "CANARY_REJECTED_GOAL"}, "start")
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [{"id": "C0", "text": "t", "gating": True,
                                               "kind": "ordinary"}], "edges": []}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "graph")
        approve_current(coordinator, run_id)
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "freeze"}}, "freeze")
        key = next(iter(runs.data))
        state = decode_state(runs.data[key].value)
        bad_graph = {"root": "C0", "claims": [{"id": "C0", "text": "replacement",
                                                   "gating": True, "kind": "ordinary"}], "edges": []}
        graph_artifact = make_artifact({"kind": "graph", "graph": bad_graph})
        artifacts_repo.append(key, graph_artifact)
        next_state = replace(state, selected_graph_artifact_id=graph_artifact.artifact_id)
        manifest = manifest_for(state.committed_artifact_head_id, (graph_artifact,), next_state,
                                basis_id="test")
        artifacts_repo.append(key, manifest)
        committed = replace(next_state, committed_artifact_head_id=manifest.artifact_id)
        runs.data[key] = Present(encode_state(committed), Revision("inconsistent"))
        writes_before = runs.cas_calls
        response = coordinator.handle({"type": "GetRun", "run_id": run_id}, "get")
        self.assertEqual(response["result"]["type"], "Block")
        self.assertEqual(response["result"]["reasons"][0]["code"], "run.corrupt")
        self.assertEqual(response["result"]["run"]["goal"], "Unsupported run state.")
        self.assertNotIn("CANARY_REJECTED_GOAL", json.dumps(response))
        self.assertEqual(runs.cas_calls, writes_before)

    def test_frozen_identity_without_selected_graph_is_corrupt_on_all_ingress(self):
        runs, artifacts_repo = Runs(), Artifacts()
        coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo,
                                  "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun",
            "selector": {"project": "p", "session": "s"},
            "goal": "CANARY_MISSING_SELECTION"}, "start")
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [{"id": "C0", "text": "t",
            "gating": True, "kind": "ordinary"}], "edges": []}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "graph")
        approve_current(coordinator, run_id)
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "freeze"}}, "freeze")
        key = next(iter(runs.data))
        state = decode_state(runs.data[key].value)
        next_state = replace(state, selected_graph_artifact_id=None)
        manifest = manifest_for(state.committed_artifact_head_id, (), next_state,
                                basis_id="test")
        artifacts_repo.append(key, manifest)
        corrupt = replace(next_state, committed_artifact_head_id=manifest.artifact_id)
        runs.data[key] = Present(encode_state(corrupt), Revision("missing-selection"))
        writes = (runs.cas_calls, len(artifacts_repo.values[key]))
        operations = (
            lambda: coordinator.handle({"type": "GetRun", "run_id": run_id}, "get"),
            lambda: coordinator.handle({"type": "ResolveRun",
                "selector": {"project": "p", "session": "s"}}, "resolve"),
            lambda: coordinator.trusted_attribution(run_id, {}, "attribution"),
            lambda: coordinator.trusted_audit_verdict(run_id, "child", {}, "verdict"),
            lambda: coordinator.trusted_child_event(run_id, "child", {}, "child"),
        )
        for operation in operations:
            response = operation()
            self.assertEqual(response["result"]["type"], "Block")
            self.assertEqual(response["result"]["reasons"][0]["code"], "run.corrupt")
            self.assertNotIn("CANARY_MISSING_SELECTION", json.dumps(response))
            self.assertEqual((runs.cas_calls, len(artifacts_repo.values[key])), writes)

    def test_read_revision_change_retries_before_projection(self):
        runs, artifacts_repo, workspace = Runs(), Artifacts(), Workspace()
        coordinator = CoordinatorWithTestInvocation(workspace, Harness(), runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                      "goal": "g"}, "start")
        run_id = started["result"]["run"]["id"]
        baseline = runs.read_calls
        runs.bump_on_read.add(baseline + 2)  # revision validation read of the first attempt
        response = coordinator.handle({"type": "GetRun", "run_id": run_id}, "read")
        self.assertEqual(response["result"]["type"], "Allow")
        self.assertGreaterEqual(runs.read_calls - baseline, 4)

    def test_route_order_rejections_have_zero_writes_or_execution(self):
        runs, artifacts_repo, harness = Runs(), Artifacts(), Harness()
        coordinator = CoordinatorWithTestInvocation(Workspace(), harness, runs, artifacts_repo,
                                  "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun",
            "selector": {"project": "p", "session": "route"}, "goal": "g"}, "start")
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [{"id": "C0", "text": "t",
            "gating": True, "kind": "needs-experiment"}], "edges": []}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "graph")
        approve_current(coordinator, run_id)
        key = next(iter(runs.data))

        actions = (
            ({"kind": "research", "claim_id": "C0", "result": "supports",
              "source_kind": "code", "payload": {"source_ref": "package.json",
                  "citation": "pin"}}, "route.required"),
            ({"kind": "child_reserve", "purpose": "work", "role_profile": "worker",
              "execution": "foreground", "resource_class": "investigation"}, "route.required"),
            ({"kind": "spike_request", "claim_id": "C0", "command": "test",
              "dependent_files": []}, "route.required"),
        )
        for action, reason in actions:
            before = (runs.data[key], runs.cas_calls, artifacts_repo.append_calls,
                      len(artifacts_repo.values[key]), harness.calls)
            response = coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                           "action": action}, "unrouted")
            self.assertEqual(response["result"]["reasons"][0]["code"], reason)
            self.assertEqual((runs.data[key], runs.cas_calls, artifacts_repo.append_calls,
                              len(artifacts_repo.values[key]), harness.calls), before)

        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "route", "reason": "code"}}, "route")
        for action, _ in actions:
            before = (runs.data[key], runs.cas_calls, artifacts_repo.append_calls,
                      len(artifacts_repo.values[key]), harness.calls)
            response = coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                           "action": action}, "route-only")
            self.assertEqual(response["result"]["reasons"][0]["code"],
                             "investigation.required")
            self.assertEqual((runs.data[key], runs.cas_calls, artifacts_repo.append_calls,
                              len(artifacts_repo.values[key]), harness.calls), before)

    def test_audit_execution_mode_is_profile_specific_without_granting_generic_async(self):
        def reserve(profile, session, resource_class):
            coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), Runs(), Artifacts(), profile, {})
            started = coordinator.handle({"type": "StartRun",
                "selector": {"project": "p", "session": session}, "goal": "execution",
                "control_mode": "auto" if profile.startswith("codex") else "deliberative",
                "invocation": {**TEST_INVOCATION, "signal": "transaction test"},
                "budgets": {"max_spawns": 1, "max_audit_spawns": 1}}, "start")
            run_id = started["result"]["run"]["id"]
            activate_investigation(coordinator, run_id)
            graph = {"root": "C0", "claims": [{"id": "C0", "text": "t",
                "gating": True, "kind": "ordinary"}], "edges": []}
            coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                "action": {"kind": "graph", "payload": graph}}, "graph")
            activate_investigation(coordinator, run_id)
            coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                "action": {"kind": "research", "claim_id": "C0", "source_kind": "code",
                    "result": "supports", "payload": {"source_ref": "review",
                                                        "citation": "active audit fixture"}}}, "research")
            action = {"kind": "child_reserve", "purpose": "audit",
                "role_profile": "empirica:empirica-auditor", "execution": "async",
                "resource_class": resource_class}
            reserved = coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                           "action": action}, "reserve")["result"]
            if profile.startswith("claude") and resource_class == "audit":
                self.assertEqual(coordinator.handle({"type": "EvaluateRun", "run_id": run_id,
                    "intent": "report_convergence"}, "reserved")["result"]["reasons"][0]["code"],
                                 "audit.pending")
                child_id = reserved["run"]["children"][0]["child_id"]
                coordinator.trusted_child_event(
                    run_id, child_id, child_event("launching", "native"), "launching")
                self.assertEqual(coordinator.handle({"type": "EvaluateRun", "run_id": run_id,
                    "intent": "report_convergence"}, "launching")["result"]["reasons"][0]["code"],
                                 "audit.pending")
            return reserved

        claude_audit = reserve("claude-code@2.1.278", "claude-audit", "audit")
        self.assertEqual(claude_audit["type"], "Allow")
        claude_generic = reserve("claude-code@2.1.278", "claude-generic", "investigation")
        self.assertEqual(claude_generic["reasons"][0]["code"], "host.async_unsupported")
        pi_audit = reserve("pi@0.84.1+pi-subagents@0.50.0", "pi-audit", "audit")
        self.assertEqual(pi_audit["reasons"][0]["code"], "host.async_unsupported")

    def test_split_child_budgets_are_isolated_and_purpose_cannot_select_audit(self):
        def setup(session, audit_limit):
            runs, artifacts_repo = Runs(), Artifacts()
            coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo,
                                      "pi@0.84.1+pi-subagents@0.50.0", {})
            started = coordinator.handle({"type": "StartRun",
                "selector": {"project": "p", "session": session}, "goal": "split",
                "budgets": {"max_spawns": 1, "max_audit_spawns": audit_limit}}, "start")
            self.assertEqual(started["result"]["type"], "Allow")
            run_id = started["result"]["run"]["id"]
            activate_investigation(coordinator, run_id)
            graph = {"root": "C0", "claims": [{"id": "C0", "text": "t",
                "gating": True, "kind": "ordinary"}], "edges": []}
            coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                "action": {"kind": "graph", "payload": graph}}, "graph")
            activate_investigation(coordinator, run_id)
            return coordinator, runs, run_id

        coordinator, runs, run_id = setup("both", 1)
        ordinary = {"kind": "child_reserve", "purpose": "audit", "role_profile": "worker",
                    "execution": "foreground", "resource_class": "investigation"}
        audit = {"kind": "child_reserve", "purpose": "audit",
                 "role_profile": "empirica:empirica-auditor", "execution": "foreground",
                 "resource_class": "audit"}
        runs.conflicts = 1
        self.assertEqual(coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                            "action": ordinary}, "ordinary")["result"]["type"],
                         "Allow")
        runs.conflicts = 1
        self.assertEqual(coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                            "action": audit}, "audit")["result"]["type"],
                         "Allow")
        key = next(iter(runs.data))
        state = runs.data[key].value
        self.assertEqual((state["budgets"]["spawns_used"],
                          state["budgets"]["audit_spawns_used"]), (1, 1))
        self.assertEqual([child["resource_class"] for child in state["children"]],
                         ["investigation", "audit"])
        self.assertIsNone(state["children"][0]["audit_argument"])
        self.assertIsNotNone(state["children"][1]["audit_argument"])
        before = (runs.data[key], runs.cas_calls)
        lower = coordinator.handle({"type": "ObserveAction", "run_id": run_id,
            "action": {"kind": "configure_run",
                       "budgets": {"max_audit_spawns": 0}}}, "lower-audit")
        self.assertEqual(lower["result"]["reasons"][0]["parameters"]["resource"],
                         "audit_spawn")
        self.assertEqual((runs.data[key], runs.cas_calls), before)
        exhausted = coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                        "action": {**ordinary, "purpose": "more"}}, "full")
        self.assertEqual(exhausted["result"]["reasons"][0]["parameters"]["resource"], "spawn")

        coordinator, runs, run_id = setup("audit-zero", 0)
        denied = coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                     "action": audit}, "audit-full")
        self.assertEqual(denied["result"]["reasons"][0]["parameters"]["resource"],
                         "audit_spawn")
        admitted = coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                       "action": {**ordinary, "purpose": "work"}}, "ordinary")
        self.assertEqual(admitted["result"]["type"], "Allow")
        state = runs.data[next(iter(runs.data))].value
        self.assertEqual((state["budgets"]["spawns_used"],
                          state["budgets"]["audit_spawns_used"]), (1, 0))

    def test_invalid_dependency_candidate_has_zero_writes(self):
        def claim(cid):
            return {"id": cid, "text": cid, "gating": True, "kind": "ordinary"}
        invalid = (
            {"root": "", "claims": [claim("")], "edges": []},
            {"root": "C0", "claims": [claim("C0")],
             "edges": [{"from": "C0", "to": "C0", "type": "SupportedBy"}]},
            {"root": "C0", "claims": [claim("C0"), claim("C1")], "edges": []},
            {"root": "C0", "claims": [claim("C0"), claim("C1")],
             "edges": [{"from": "C0", "to": "C1", "type": "SupportedBy"},
                       {"from": "C0", "to": "C1", "type": "SupportedBy"}]},
        )
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                runs, artifacts_repo = Runs(), Artifacts()
                coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo,
                                          "pi@0.84.1+pi-subagents@0.50.0", {})
                started = coordinator.handle({"type": "StartRun",
                    "selector": {"project": "p", "session": "s"}, "goal": "g"}, "start")
                key = next(iter(runs.data))
                writes = (runs.cas_calls, len(artifacts_repo.values[key]))
                response = coordinator.handle({"type": "ObserveAction",
                    "run_id": started["result"]["run"]["id"],
                    "action": {"kind": "graph", "payload": candidate}}, "graph")
                self.assertEqual(response["result"]["reasons"][0]["code"], "graph.invalid")
                self.assertEqual((runs.cas_calls, len(artifacts_repo.values[key])), writes)

    def test_frozen_semantic_identity_is_atomic_and_rejection_has_zero_writes(self):
        runs, artifacts_repo = Runs(), Artifacts()
        coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo,
                                  "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun",
            "selector": {"project": "p", "session": "s"}, "goal": "g"}, "start")
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [
            {"id": "C0", "text": "root", "gating": True, "kind": "ordinary"},
            {"id": "C1", "text": "support", "gating": True, "kind": "ordinary"}],
            "edges": [{"from": "C0", "to": "C1", "type": "SupportedBy"}]}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "graph")
        approve_current(coordinator, run_id)
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "freeze"}}, "freeze")
        key = next(iter(runs.data))
        frozen = decode_state(runs.data[key].value)
        self.assertEqual(frozen.frozen_claim_ids, ("C0", "C1"))
        self.assertEqual(frozen.frozen_semantic_digest,
                         frozen_semantic_digest(graph, frozen.frozen_claim_ids))
        writes = (runs.cas_calls, len(artifacts_repo.values[key]))
        changed = {"root": "C0", "claims": [graph["claims"][0],
                   {**graph["claims"][1], "text": "changed"}], "edges": graph["edges"]}
        response = coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                      "action": {"kind": "graph", "payload": changed}}, "changed")
        self.assertEqual(response["result"]["reasons"][0]["code"], "graph.invalid")
        self.assertEqual((runs.cas_calls, len(artifacts_repo.values[key])), writes)
        repeated = coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                      "action": {"kind": "freeze"}}, "freeze-again")
        self.assertEqual(repeated["result"]["type"], "Inert")
        after = decode_state(runs.data[key].value)
        self.assertEqual((after.frozen_claim_ids, after.frozen_semantic_digest),
                         (frozen.frozen_claim_ids, frozen.frozen_semantic_digest))

    def test_post_plan_capture_tracks_disjoint_graph_heads(self):
        runs, artifacts_repo, workspace, harness = Runs(), Artifacts(), Workspace(), Harness()
        coordinator = CoordinatorWithTestInvocation(workspace, harness, runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                      "goal": "g"}, "start")
        run_id = started["result"]["run"]["id"]
        activate_investigation(coordinator, run_id)
        graph = {"root": "C0", "claims": [
            {"id": "C0", "text": "zero", "gating": True, "kind": "needs-experiment"},
            {"id": "C1", "text": "one", "gating": True, "kind": "needs-experiment"}],
            "edges": [{"from": "C0", "to": "C1", "type": "SupportedBy"}]}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "g")
        activate_investigation(coordinator, run_id)
        for claim, path in (("C0", "src/0.py"), ("C1", "src/1.py")):
            coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                "action": {"kind": "research", "claim_id": claim,
                                           "source_kind": "code", "result": "supports", "payload": {
                                               "source_ref": "test_d7_transactions.py",
                                               "citation": "Post-plan capture fixture."}}}, "r")
            workspace.write(path, claim.encode())
            response = coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                           "action": {"kind": "spike_request", "claim_id": claim,
                                                      "command": "test", "dependent_files": [path]}}, "s")
            self.assertEqual(response["result"]["type"], "Allow")
        self.assertEqual(workspace.calls[-1], ("src/0.py", "src/1.py"))
        c1_graph = {"root": "C1", "claims": [graph["claims"][1]], "edges": []}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": c1_graph}}, "remove")
        activate_investigation(coordinator, run_id)
        self.assertEqual(workspace.calls[-1], ("src/1.py",))
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "restore")
        activate_investigation(coordinator, run_id)
        self.assertEqual(workspace.calls[-1], ("src/0.py", "src/1.py"))
        before = len(workspace.calls)
        evaluated = coordinator.handle({"type": "EvaluateRun", "run_id": run_id,
                                        "intent": "report_convergence"}, "evaluate")
        self.assertEqual(evaluated["result"]["type"], "Block")
        self.assertEqual(evaluated["result"]["reasons"][0]["code"], "audit.required")
        self.assertEqual(len(workspace.calls) - before, 1,
                         "state-only commit must reuse the evaluated observation")

    def test_resolve_retry_rejects_mutated_terminal_and_corrupt_state(self):
        for replacement in ("terminal", "corrupt"):
            with self.subTest(replacement=replacement):
                runs, artifacts_repo = Runs(), Artifacts()
                coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo,
                                          "pi@0.84.1+pi-subagents@0.50.0", {})
                coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                    "goal": "g"}, "start")
                key = next(iter(runs.data))
                value = dict(runs.data[key].value)
                if replacement == "terminal":
                    value["status"] = "stopped_residual"
                else:
                    value.pop("goal")
                baseline = runs.read_calls
                # resolve: initial read followed by revision validation
                runs.replace_on_read[baseline + 2] = value
                response = coordinator.handle({"type": "ResolveRun",
                                               "selector": {"project": "p", "session": "s"}}, "r")
                self.assertEqual(response["result"]["type"], "Block")
                self.assertEqual(response["result"]["reasons"][0]["code"], "run.corrupt")

    def test_resolve_valid_terminal_is_inert_but_corrupt_terminal_blocks(self):
        runs, artifacts_repo = Runs(), Artifacts()
        coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo,
                                  "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                      "goal": "CANARY_TERMINAL_GOAL"}, "start")
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [{"id": "C0", "text": "t", "gating": True,
                                               "kind": "ordinary"}], "edges": []}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "graph")
        approve_current(coordinator, run_id)
        coordinator.handle({"type": "EvaluateRun", "run_id": run_id, "intent": "stop"}, "stop")
        resolve = {"type": "ResolveRun", "selector": {"project": "p", "session": "s"}}
        self.assertEqual(coordinator.handle(resolve, "valid")["result"],
                         {"type": "Inert", "reason": "no_run"})
        key = next(iter(runs.data))
        head = runs.data[key].value["committed_artifact_head_id"]
        artifacts_repo.values[key].pop(head)
        response = coordinator.handle(resolve, "corrupt")
        self.assertEqual(response["result"]["type"], "Block")
        self.assertEqual(response["result"]["reasons"][0]["code"], "run.corrupt")
        self.assertEqual(response["result"]["run"]["goal"], "Unsupported run state.")
        self.assertNotIn("CANARY_TERMINAL_GOAL", json.dumps(response))

    def test_selected_graph_missing_malformed_or_structurally_invalid_is_run_corrupt(self):
        for corruption in ("missing", "malformed", "structural"):
            with self.subTest(corruption=corruption):
                runs, artifacts_repo = Runs(), Artifacts()
                coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo,
                                          "pi@0.84.1+pi-subagents@0.50.0", {})
                started = coordinator.handle({"type": "StartRun",
                                              "selector": {"project": "p", "session": "s"},
                                              "goal": "CANARY_SELECTED_GRAPH_GOAL"}, "start")
                run_id = started["result"]["run"]["id"]
                graph = {"root": "C0", "claims": [{"id": "C0", "text": "t",
                          "gating": True, "kind": "ordinary"}], "edges": []}
                coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                    "action": {"kind": "graph", "payload": graph}}, "g")
                approve_current(coordinator, run_id)
                key = next(iter(runs.data))
                graph_id = runs.data[key].value["selected_graph_artifact_id"]
                if corruption == "malformed":
                    artifacts_repo.values[key][graph_id] = Artifact(graph_id, "not-json")
                elif corruption == "missing":
                    artifacts_repo.values[key].pop(graph_id)
                else:
                    state = decode_state(runs.data[key].value)
                    invalid = make_artifact({"kind": "graph", "graph": {
                        "root": "C0", "claims": graph["claims"],
                        "edges": [{"from": "C0", "to": "C0", "type": "SupportedBy"}]}})
                    artifacts_repo.append(key, invalid)
                    next_state = replace(state, selected_graph_artifact_id=invalid.artifact_id)
                    manifest = manifest_for(
                        state.committed_artifact_head_id, (invalid,), next_state, basis_id="test")
                    artifacts_repo.append(key, manifest)
                    committed = replace(next_state, committed_artifact_head_id=manifest.artifact_id)
                    runs.data[key] = Present(encode_state(committed), Revision("structural"))
                writes_before = runs.cas_calls
                artifact_count = len(artifacts_repo.values[key])
                response = coordinator.handle({"type": "GetArgument", "run_id": run_id}, "arg")
                self.assertEqual(response["result"]["type"], "Block")
                self.assertEqual(response["result"]["reasons"][0]["code"], "run.corrupt")
                self.assertEqual(response["result"]["run"]["goal"], "Unsupported run state.")
                self.assertNotIn("CANARY_SELECTED_GRAPH_GOAL", json.dumps(response))
                self.assertEqual(runs.cas_calls, writes_before)
                self.assertEqual(len(artifacts_repo.values[key]), artifact_count)

    def test_trusted_ingress_maps_corrupt_aggregate_to_fixed_safe_block(self):
        operations = (
            ("trusted_attribution", ({},)),
            ("trusted_audit_verdict", ("child", {})),
            ("trusted_child_event", ("child", {})),
        )
        for operation, args in operations:
            for corruption in ("store", "selected-graph"):
                with self.subTest(operation=operation, corruption=corruption):
                    runs, artifacts_repo = Runs(), Artifacts()
                    coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo,
                                              "pi@0.84.1+pi-subagents@0.50.0", {})
                    started = coordinator.handle({"type": "StartRun",
                        "selector": {"project": "p", "session": "s"},
                        "goal": "CANARY_TRUSTED_GOAL"}, "start")
                    run_id = started["result"]["run"]["id"]
                    graph = {"root": "C0", "claims": [{"id": "C0", "text": "t",
                        "gating": True, "kind": "ordinary"}], "edges": []}
                    coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                        "action": {"kind": "graph", "payload": graph}}, "graph")
                    approve_current(coordinator, run_id)
                    key = next(iter(runs.data))
                    if corruption == "store":
                        runs.data[key] = Corrupt("bad bytes")
                    else:
                        graph_id = runs.data[key].value["selected_graph_artifact_id"]
                        artifacts_repo.values[key].pop(graph_id)
                    writes = (runs.cas_calls, len(artifacts_repo.values[key]))
                    response = getattr(coordinator, operation)(run_id, *args, request_id="trusted")
                    self.assertEqual(response["result"]["type"], "Block")
                    self.assertEqual(response["result"]["reasons"][0]["code"], "run.corrupt")
                    self.assertEqual(response["result"]["run"]["goal"],
                                     "Unsupported run state.")
                    self.assertNotIn("CANARY_TRUSTED_GOAL", json.dumps(response))
                    self.assertEqual((runs.cas_calls, len(artifacts_repo.values[key])), writes)

    def _set_context_author(self, coordinator, run_id, author):
        response = governance_transact(
            coordinator, run_id, {"author": author, "ingress": "pi_ui"}, context=True)
        if response["result"]["type"] not in {"Allow", "Inert"}:
            raise AssertionError(response)

    def _run_with_passing_spike(self, *, conflict=False, switch_context=False,
                                research_author=_KEEP, request_author=_KEEP,
                                completion_author=AUDITOR):
        runs, artifacts_repo, workspace, harness = Runs(), Artifacts(), Workspace(), Harness()
        coordinator = CoordinatorWithTestInvocation(
            workspace, harness, runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle(
            {"type": "StartRun", "selector": {"project": "p", "session": "s"},
             "goal": "g"}, "start")
        run_id = started["result"]["run"]["id"]
        activate_investigation(coordinator, run_id)
        graph = {"root": "C0", "claims": [
            {"id": "C0", "text": "t", "gating": True, "kind": "needs-experiment"}],
            "edges": []}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "g")
        activate_investigation(coordinator, run_id)
        if research_author is not _KEEP:
            self._set_context_author(coordinator, run_id, research_author)
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "research", "claim_id": "C0",
                                       "source_kind": "code", "result": "supports", "payload": {
                                           "source_ref": "test_d7_transactions.py",
                                           "citation": "Observation basis fixture."}}}, "r")
        if request_author is not _KEEP:
            self._set_context_author(coordinator, run_id, request_author)
        workspace.write("src/x.py", b"x")

        def before_result():
            # Runs once inside the single harness invocation, i.e. after the spike request
            # has committed and before the spike RESULT commit. Any context switch here
            # commits its own CAS first; the scheduled conflict then lands on the very next
            # CAS — the result admission — proving the result transaction itself retries.
            if switch_context:
                self._set_context_author(coordinator, run_id, completion_author)
            if conflict:
                runs.conflict_on.add(runs.cas_calls + 1)
        if switch_context or conflict:
            harness.before_result = before_result
        response = coordinator.handle({
            "type": "ObserveAction", "run_id": run_id,
            "action": {"kind": "spike_request", "claim_id": "C0", "command": "test",
                       "dependent_files": ["src/x.py"]}}, "s")
        self.assertEqual(response["result"]["type"], "Allow")
        return coordinator, runs, artifacts_repo, workspace, harness, run_id, next(iter(runs.data))

    def test_spike_result_keeps_sealed_producer_across_context_change_and_result_retry(self):
        # The sealed request's producer must survive a completion-time context switch and
        # a genuine result-CAS retry, across same/mixed/unknown covered-producer cases.
        # reviewer_author is the distinct auditor identity compared by real independence().
        cases = [
            ("same_model", _KEEP, _KEEP, AUTHOR, "same_model"),
            ("mixed", AUTHOR, AUDITOR, AUDITOR, "mixed"),
            ("unverified", AUTHOR, None, AUTHOR, "unverified"),
        ]
        for name, research_author, request_author, reviewer_author, expected in cases:
            with self.subTest(case=name):
                coordinator, runs, artifacts_repo, workspace, harness, _, key = \
                    self._run_with_passing_spike(
                        conflict=True, switch_context=True,
                        research_author=research_author, request_author=request_author)
                # The scheduled conflict was consumed by the result CAS, and the harness
                # ran exactly once (the retry re-commits without re-executing the spike).
                self.assertEqual(runs.conflict_on, set())
                self.assertEqual(harness.calls, 1)
                history = traverse_history(
                    decode_state(runs.data[key].value), artifacts_repo.values[key].values())
                request = next(item for item in history if item["kind"] == "spike_request")
                result = next(item for item in history if item["kind"] == "spike")
                self.assertEqual(result["producer"], request["producer"])
                state = decode_state(runs.data[key].value)
                snapshot = coordinator._assemble(
                    key, state, {"type": "GetRun", "run_id": "unused"}, require_graph=True)
                reviewer = {"kind": "attribution", "subject_kind": "auditor",
                            "child_id": "audit", **reviewer_author}
                classified = independence(
                    replace(snapshot, history=(*snapshot.history, reviewer)),
                    {"child_id": "audit"})
                self.assertEqual(classified, expected)

    def test_spike_result_conflict_does_not_duplicate_basis(self):
        coordinator, runs, artifacts_repo, workspace, harness, _, key = \
            self._run_with_passing_spike(conflict=True)
        self.assertEqual(harness.calls, 1)
        # Immutable execution capture plus one post-plan capture per result attempt;
        # successful projection performs no additional observation.
        self.assertEqual(len(workspace.calls), 3)
        history = traverse_history(
            decode_state(runs.data[key].value), artifacts_repo.values[key].values())
        spikes = [item for item in history if item["kind"] == "spike"]
        self.assertEqual(len(spikes), 1)
        execution_bases = [item for item in history
                           if item["kind"] == "observation_basis"
                           and [source["name"] for source in item["sources"]]
                           == ["workspace", "spike_execution"]]
        self.assertEqual(len(execution_bases), 1)
        latest = json.loads(artifacts_repo.values[key][
            decode_state(runs.data[key].value).committed_artifact_head_id].body)
        self.assertEqual(latest["observation_basis_digest"], execution_bases[0]["artifact_id"])

    def _active_snapshot_with_unmet_claim_and_child(self):
        """An active run with a real unmet ordinary claim (active next=research.record) and a
        recoverable failed child, so terminal suppression has non-trivial guidance to replace."""
        coordinator, snapshot = projection_snapshot()
        child = {"child_id": "child", "purpose": "audit", "role_profile": "auditor",
                 "execution": "foreground", "resource_class": "audit", "state": "failed",
                 "native_id": "native", "deadline": None, "refunded": False,
                 "audit_operation_id": None}
        return coordinator, replace(
            snapshot, state=replace(snapshot.state, children=(child,)))

    def test_every_terminal_projection_suppresses_nested_mutation_actions(self):
        _, active_snapshot = self._active_snapshot_with_unmet_claim_and_child()
        active = project_runview(active_snapshot)
        # Active preconditions: real mutation guidance and a recoverable child exist, so the
        # terminal assertions below cannot pass vacuously.
        claim_row = next(r for r in active["obligations"]["active"] if r["id"] == "claim:C0")
        self.assertEqual(claim_row["next"], ["research.record"])
        self.assertEqual(claim_row["status"], "residual")
        self.assertEqual(active["children"][0]["recovery_action"], "child.retry")
        active_rows = {r["id"]: r for r in active["obligations"]["active"]}

        terminal_next = protocol._PUBLIC_CONTRACT["reasons"]["run.terminal"]["next_actions"]
        # Loop the terminal statuses from the contract, so a newly added status cannot be missed.
        statuses = [s for s in protocol._PUBLIC_CONTRACT["statuses"] if s != "active"]
        self.assertTrue(statuses)
        for status in statuses:
            with self.subTest(status=status):
                terminal = replace(active_snapshot, state=replace(
                    active_snapshot.state, status=status))
                projected = project_runview(terminal)
                # Top-level and every obligation next carry only the contract-owned terminal
                # guidance; no mutation action survives.
                self.assertEqual(projected["next_actions"], terminal_next)
                for row in (*projected["obligations"]["active"], *projected["obligations"]["deferred"]):
                    self.assertEqual(row["next"], terminal_next)
                    # Required/Observed/Missing/status are preserved verbatim from the active row.
                    before = active_rows[row["id"]]
                    self.assertEqual((row["required"], row["observed"], row["missing"], row["status"]),
                                     (before["required"], before["observed"], before["missing"],
                                      before["status"]))
                for row in projected["residuals"]:
                    self.assertEqual(row["next_actions"], terminal_next)
                # A stopped child still names its recovery (the schema requires it), and a
                # terminal run's only recovery is the terminal guidance. The whole terminal
                # RunView must validate, so a projection/schema disagreement cannot hide here.
                self.assertEqual(projected["children"][0]["recovery_action"], terminal_next[0])
                RUN_VIEW.validate(projected)
        # stopped_residual and stopped_budget carry a real residual whose mutation guidance is
        # replaced by the terminal guidance, so removing residual suppression fails the test.
        for status, code in (("stopped_residual", "claim.research_missing"),
                             ("stopped_budget", "budget.exhausted")):
            with self.subTest(residual_status=status):
                terminal = replace(active_snapshot, state=replace(
                    active_snapshot.state, status=status))
                residuals = project_runview(terminal)["residuals"]
                self.assertTrue(any(r["code"] == code for r in residuals), residuals)
                for row in residuals:
                    self.assertEqual(row["next_actions"], terminal_next)

    def test_read_commands_write_nothing(self):
        coordinator, runs, artifacts_repo, _, _, run_id, key = self._run_with_passing_spike()
        writes = (runs.cas_calls, artifacts_repo.append_calls,
                  frozenset(artifacts_repo.values[key]))
        coordinator.handle({"type": "GetRun", "run_id": run_id}, "read-run")
        coordinator.handle({"type": "GetArgument", "run_id": run_id}, "read-argument")
        coordinator.handle({"type": "RestoreRun", "run_id": run_id}, "restore")
        coordinator.handle({"type": "ResolveRun",
                            "selector": {"project": "p", "session": "s"}}, "resolve")
        coordinator.handle({"type": "GetContract", "target": "index"}, "contract")
        self.assertEqual((runs.cas_calls, artifacts_repo.append_calls,
                          frozenset(artifacts_repo.values[key])), writes)

    def test_continue_ignores_changed_workspace(self):
        coordinator, runs, artifacts_repo, workspace, _, run_id, key = \
            self._run_with_passing_spike()
        workspace.write("src/x.py", b"changed")
        writes = (runs.cas_calls, artifacts_repo.append_calls,
                  frozenset(artifacts_repo.values[key]))
        coordinator.handle({"type": "EvaluateRun", "run_id": run_id,
                            "intent": "continue"}, "continue")
        self.assertEqual((runs.cas_calls, artifacts_repo.append_calls,
                          frozenset(artifacts_repo.values[key])), writes)

    def test_open_claim_does_not_consult_freshness(self):
        coordinator, runs, artifacts_repo, workspace, _, run_id, key = \
            self._run_with_passing_spike()
        state = decode_state(runs.data[key].value)
        command = {"type": "EvaluateRun", "run_id": run_id, "intent": "report_convergence"}
        snapshot = assemble(
            state, artifacts_repo.values[key].values(), workspace, run_id=run_id,
            profile_id="pi@0.84.1+pi-subagents@0.50.0", command=command,
            require_graph=True)
        open_snapshot = replace(
            snapshot, history=tuple(item for item in snapshot.history
                                    if item["kind"] not in {"research", "spike"}))
        decision = evaluate_snapshot(open_snapshot, command)
        self.assertEqual(decision.reason_code, "claim.research_missing")
        self.assertFalse(decision.observations_consulted)

    def test_freshness_consulted_flag_matches_claim_sensitivity(self):
        coordinator, runs, artifacts_repo, workspace, _, run_id, key = \
            self._run_with_passing_spike()
        state = decode_state(runs.data[key].value)
        snapshot = assemble(
            state, artifacts_repo.values[key].values(), workspace, run_id=run_id,
            profile_id="pi@0.84.1+pi-subagents@0.50.0",
            command={"type": "EvaluateRun", "run_id": run_id, "intent": "continue"},
            require_graph=True)
        self.assertFalse(evaluate_snapshot(snapshot, snapshot.command).observations_consulted)
        report = {"type": "EvaluateRun", "run_id": run_id, "intent": "report_convergence"}
        self.assertTrue(evaluate_snapshot(snapshot, report).observations_consulted)

    def test_changed_stale_basis_persisted_once(self):
        coordinator, runs, artifacts_repo, workspace, _, run_id, key = \
            self._run_with_passing_spike()
        report = {"type": "EvaluateRun", "run_id": run_id, "intent": "report_convergence"}
        coordinator.handle(report, "baseline")
        prior = decode_state(runs.data[key].value).observation_basis_digest
        workspace.write("src/x.py", b"changed")
        stale = coordinator.handle(report, "stale")
        self.assertEqual(stale["result"]["reasons"][0]["code"], "claim.spike_stale")
        changed = decode_state(runs.data[key].value).observation_basis_digest
        self.assertNotEqual(changed, prior)
        basis_ids = {item["artifact_id"] for item in traverse_history(
            decode_state(runs.data[key].value), artifacts_repo.values[key].values())
                     if item["kind"] == "observation_basis"}
        self.assertIn(changed, basis_ids)
        writes = (runs.cas_calls, artifacts_repo.append_calls, frozenset(basis_ids))
        coordinator.handle(report, "same-stale")
        after_ids = {item["artifact_id"] for item in traverse_history(
            decode_state(runs.data[key].value), artifacts_repo.values[key].values())
                     if item["kind"] == "observation_basis"}
        self.assertEqual((runs.cas_calls, artifacts_repo.append_calls, frozenset(after_ids)), writes)

    def test_restored_bytes_reuse_prior_basis(self):
        coordinator, runs, artifacts_repo, workspace, _, run_id, key = \
            self._run_with_passing_spike()
        report = {"type": "EvaluateRun", "run_id": run_id, "intent": "report_convergence"}
        coordinator.handle(report, "baseline")
        original = decode_state(runs.data[key].value).observation_basis_digest
        workspace.write("src/x.py", b"changed")
        coordinator.handle(report, "stale")
        workspace.write("src/x.py", b"x")
        coordinator.handle(report, "reverted")
        state = decode_state(runs.data[key].value)
        self.assertEqual(state.observation_basis_digest, original)
        latest = json.loads(artifacts_repo.values[key][state.committed_artifact_head_id].body)
        self.assertEqual(latest["observation_basis_digest"], original)
        self.assertNotIn(original, latest["artifact_ids"])

    def test_crash_between_basis_append_and_cas_reuses_basis(self):
        coordinator, runs, artifacts_repo, workspace, _, run_id, key = \
            self._run_with_passing_spike()
        report = {"type": "EvaluateRun", "run_id": run_id, "intent": "report_convergence"}
        coordinator.handle(report, "baseline")
        workspace.write("src/x.py", b"changed")
        runs.crashes = 1
        failed = coordinator.handle(report, "crash")
        self.assertEqual(failed["result"]["type"], "Fault")
        bases_before_retry = {artifact_id for artifact_id, value
                              in artifacts_repo.values[key].items()
                              if json.loads(value.body).get("kind") == "observation_basis"}
        artifacts_before_retry = set(artifacts_repo.values[key])
        coordinator.handle(report, "retry")
        state = decode_state(runs.data[key].value)
        latest = json.loads(artifacts_repo.values[key][state.committed_artifact_head_id].body)
        self.assertIn(state.observation_basis_digest, bases_before_retry)
        self.assertEqual(set(artifacts_repo.values[key]), artifacts_before_retry)
        self.assertEqual(latest["observation_basis_digest"], state.observation_basis_digest)

    def test_inconsistent_persisted_frozen_scope_is_corrupt_not_repairable(self):
        state = replace(initial(), selected_graph_artifact_id="sha256:" + "1" * 64,
                        frozen_claim_ids=("C0",), frozen_semantic_digest="sha256:" + "2" * 64)
        graph = {"root": "R", "claims": [{"id": "R", "text": "root", "kind": "ordinary",
                                             "gating": True}], "edges": []}
        common = {"state": state, "history": (), "graph": graph,
                  "contract": protocol.CONTRACT_VIEW, "run_id": "er2:test:test"}
        report_snapshot = EvaluationSnapshot(
            **common, command={"type": "EvaluateRun", "intent": "report_convergence"})
        report = evaluate_snapshot(
            report_snapshot, {"type": "EvaluateRun", "intent": "report_convergence"})
        self.assertEqual((report.result_type, report.reason_code), ("Block", "graph.invalid"))
        argument = evaluate_snapshot(EvaluationSnapshot(
            **common, command={"type": "GetArgument"}), {"type": "GetArgument"})
        self.assertEqual((argument.result_type, argument.reason_code), ("Block", "graph.invalid"))
        self.assertEqual(audit_binding(report_snapshot), {})
        self.assertFalse(audit_passes(report_snapshot, {"verdict": "pass"}))
        stop = evaluate_snapshot(EvaluationSnapshot(
            **common, command={"type": "EvaluateRun", "intent": "stop"}),
            {"type": "EvaluateRun", "intent": "stop"})
        self.assertEqual((stop.result_type, stop.reason_code), ("Block", "graph.invalid"))
        persisted = ({"artifact_id": state.selected_graph_artifact_id, "kind": "graph",
                      "graph": graph},)
        with self.assertRaises(HistoryCorrupt):
            graph_from_history(state, persisted, required=False)

    def test_persisted_evidence_without_route_witnesses_is_fixed_safe_corrupt(self):
        runs, artifacts_repo = Runs(), Artifacts()
        coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo,
                                  "pi@0.84.1+pi-subagents@0.50.0", {})
        graph = artifact("graph", {"graph": {"root": "C0", "claims": [
            {"id": "C0", "text": "CANARY_ROUTE_EVIDENCE", "gating": True,
             "kind": "ordinary"}], "edges": []}})
        research = artifact("research", {"claim_id": "C0", "claim_digest": "sha256:" + "1" * 64,
            "statement_digest": "sha256:" + "2" * 64, "outcome": "supporting",
            "source_kind": "code", "source_ref": "CANARY_ROUTE_EVIDENCE",
            "citation": "legacy bypass"})
        base = replace(initial(), goal="CANARY_ROUTE_GOAL",
                       selected_graph_artifact_id=graph["artifact_id"])
        state, values = chain(base, (graph, research))
        key = RunKey(storage_id("p"), storage_id("route-corrupt"), 1)
        runs.data[key] = Present(encode_state(state), Revision("route-corrupt"))
        artifacts_repo.values[key] = {item.artifact_id: item for item in values}
        run_id = encode_handle(key)
        writes = (runs.cas_calls, artifacts_repo.append_calls, len(artifacts_repo.values[key]))
        operations = (
            lambda: coordinator.handle({"type": "GetRun", "run_id": run_id}, "get"),
            lambda: coordinator.handle({"type": "EvaluateRun", "run_id": run_id,
                                        "intent": "report_convergence"}, "evaluate"),
            lambda: coordinator.trusted_attribution(run_id, {}, "attribution"),
            lambda: coordinator.trusted_audit_verdict(run_id, "child", {}, "verdict"),
            lambda: coordinator.trusted_child_event(run_id, "child", {}, "child"),
        )
        for operation in operations:
            response = operation()
            self.assertEqual(response["result"]["reasons"][0]["code"], "run.corrupt")
            self.assertNotIn("CANARY_ROUTE", json.dumps(response))
            self.assertEqual((runs.cas_calls, artifacts_repo.append_calls,
                              len(artifacts_repo.values[key])), writes)

    def test_investigative_witnesses_require_exact_int_types_for_every_kind(self):
        evidence_kinds = ("research", "spike_request", "spike", "attribution", "audit_verdict")
        for kind in evidence_kinds:
            with self.subTest(kind=kind):
                runs, artifacts_repo = Runs(), Artifacts()
                coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo,
                                          "pi@0.84.1+pi-subagents@0.50.0", {})
                graph = artifact("graph", {"graph": {"root": "C0", "claims": [
                    {"id": "C0", "text": "typed witness", "gating": True,
                     "kind": "ordinary"}], "edges": []}})
                evidence = artifact(kind, {"route_stamp": True, "investigation_stamp": 2})
                base = replace(initial(), selected_graph_artifact_id=graph["artifact_id"],
                               route_stamp=1, investigation_stamp=2, stamp_seq=2)
                state, values = chain(base, (graph, evidence))
                key = RunKey(storage_id("p"), storage_id(f"typed-{kind}"), 1)
                runs.data[key] = Present(encode_state(state), Revision(f"typed-{kind}"))
                artifacts_repo.values[key] = {item.artifact_id: item for item in values}
                response = coordinator.handle(
                    {"type": "GetRun", "run_id": encode_handle(key)}, "get")
                self.assertEqual(response["result"]["reasons"][0]["code"], "run.corrupt")

    def test_trusted_audit_plan_rejects_inconsistent_investigation_history(self):
        runs, artifacts_repo = Runs(), Artifacts()
        coordinator = CoordinatorWithTestInvocation(Workspace(), Harness(), runs, artifacts_repo,
                                  "pi@0.84.1+pi-subagents@0.50.0", {})
        graph = artifact("graph", {"graph": {"root": "C0", "claims": [
            {"id": "C0", "text": "audit history", "gating": True,
             "kind": "ordinary"}], "edges": []}})
        research = artifact("research", {"route_stamp": 99, "investigation_stamp": 100})
        child = {"child_id": "audit-1", "purpose": "audit", "resource_class": "audit",
                 "state": "reserved",
                 "spent": False, "refunded": False, "deadline": None, "native_id": None,
                 "first_terminal_fingerprint": None, "capability_ref": "cap-1",
                 "audit_operation_id": "sha256:" + "a" * 64,
                 "audit_argument": {"argument_digest": "sha256:" + "b" * 64},
                 "audit_role_profile": "empirica:empirica-auditor"}
        base = replace(initial(), selected_graph_artifact_id=graph["artifact_id"],
                       route_stamp=1, investigation_stamp=2, stamp_seq=2,
                       budgets={**initial().budgets, "audit_spawns_used": 1},
                       children=(child,))
        state, values = chain(base, (graph, research))
        key = RunKey(storage_id("p"), storage_id("audit-history"), 1)
        runs.data[key] = Present(encode_state(state), Revision("audit-history"))
        artifacts_repo.values[key] = {item.artifact_id: item for item in values}
        self.assertIsNone(coordinator.trusted_audit_plan(encode_handle(key), "audit-1"))

    def test_projection_is_deterministic_and_has_no_committed_head(self):
        state = initial()
        snapshot = EvaluationSnapshot(
            state=state, history=(), graph=None, contract=protocol.CONTRACT_VIEW,
            run_id="er2:test:test",
            contract_id=protocol._PUBLIC_CONTRACT["id"], contract_version=protocol._PUBLIC_CONTRACT["version"],
            contract_digest=protocol._DIGEST, profile_id="pi@0.84.1+pi-subagents@0.50.0",
            host_tier="foreground_only", command={"type": "GetRun"})
        left, right = project_runview(snapshot), project_runview(snapshot)
        self.assertEqual(left, right)
        self.assertNotIn("committed_artifact_head_id", json.dumps(left))
        with self.assertRaisesRegex(ValueError, "unknown bootstrap operation"):
            project_runview(replace(snapshot, contract=replace(protocol.CONTRACT_VIEW,
                                                               bootstrap_operations=())))


class ValidGraphClaimIdTests(unittest.TestCase):
    """``valid_graph`` gates every claim id (vertex, root, edge endpoints) with the one pattern."""

    ACCEPTED = ("A", "a.b-C_9", "x" * 64)
    REJECTED = ("", "x" * 65, "a b", "claim:C0", "caf\u00e9", "a\nb", "C0\n",
                "<<<EMPIRICA_UNTRUSTED_DATA>>>")

    @staticmethod
    def _graph(root, vertex, edge_from, edge_to):
        claims = [{"id": vertex, "text": "t", "gating": True, "kind": "ordinary"},
                  {"id": "L", "text": "t", "gating": True, "kind": "ordinary"}]
        return {"root": root, "claims": claims,
                "edges": [{"from": edge_from, "to": edge_to, "type": "SupportedBy"}]}

    def test_accepted_ids_in_every_position(self):
        for value in self.ACCEPTED:
            with self.subTest(value=value):
                self.assertTrue(valid_graph(self._graph(value, value, value, "L")))

    def test_rejected_ids_in_every_position(self):
        for value in self.REJECTED:
            with self.subTest(value=value, position="id+root+from"):
                self.assertFalse(valid_graph(self._graph(value, value, value, "L")))
            with self.subTest(value=value, position="root"):
                self.assertFalse(valid_graph(self._graph(value, "R", "R", "L")))
            with self.subTest(value=value, position="from"):
                self.assertFalse(valid_graph(self._graph("R", "R", value, "L")))
            with self.subTest(value=value, position="to"):
                self.assertFalse(valid_graph(self._graph("R", "R", "R", value)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
