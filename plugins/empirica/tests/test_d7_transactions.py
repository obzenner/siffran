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

from application import protocol  # noqa: E402
from application.snapshot import (HistoryCorrupt, graph_from_history, make_artifact,  # noqa: E402
                                  state_digest, traverse_history)
from application.ports import CapturedFile, HarnessResult, WorkspaceCapture  # noqa: E402
from application.run_state import decode_state, encode_state  # noqa: E402
from application.location import encode_handle, storage_id  # noqa: E402
from application.transaction import Coordinator  # noqa: E402
from core.evaluation import (EvaluationSnapshot, artifact, audit_binding, audit_passes,  # noqa: E402
                             evaluate_snapshot)
from core.freshness import FileObservation, ObservationState, canonical_digest  # noqa: E402
from core.projection import project_runview  # noqa: E402
from core.records import (ABSENT, Artifact, Conflict, Corrupt, Present, Revision,  # noqa: E402
                          RunKey)
from core.run import OperationalState  # noqa: E402


class Runs:
    def __init__(self):
        self.data = {}
        self.seq = 0
        self.conflicts = 0
        self.cas_calls = 0
        self.conflict_on = set()
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

    def append(self, key, value):
        prior = self.values.setdefault(key, {})
        if value.artifact_id in prior and prior[value.artifact_id] != value:
            raise ValueError("collision")
        prior[value.artifact_id] = value

    def read(self, key):
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

    def run(self, command, snapshot):
        self.calls += 1
        return HarnessResult(0, canonical_digest({"command": command}), snapshot.snapshot_digest)


def initial() -> OperationalState:
    return OperationalState(
        protocol="empirica/v2", state_schema="empirica.run/2", goal="g", status="active",
        modes={"multi_provider": False, "cli_exec": False},
        budgets={"max_passes": 8, "passes_used": 0, "max_spawns": 1, "spawns_used": 0},
        selected_graph_artifact_id=None, frozen_claim_ids=None, route_stamp=None,
        investigation_stamp=None, stamp_seq=0, last_derivation_digest=None, children=(),
        committed_artifact_head_id=None,
    )


def chain(state: OperationalState, domain=()):
    values = [make_artifact(item["body"]) for item in domain]
    body = {"kind": "transaction_manifest", "version": 1, "parent": None,
            "artifact_ids": [item.artifact_id for item in values],
            "observation_basis_id": "empty", "observation_digest": "sha256:" + "0" * 64,
            "next_state_digest": state_digest(state)}
    manifest = make_artifact(body)
    return replace(state, committed_artifact_head_id=manifest.artifact_id), values + [manifest]


class D7TransactionTests(unittest.TestCase):
    def test_orphan_is_invisible_and_order_comes_from_manifest(self):
        first = artifact("research", {"n": 1})
        second = artifact("research", {"n": 2})
        state, values = chain(initial(), (first, second))
        orphan = make_artifact(artifact("research", {"n": 3})["body"])
        malformed_orphan = Artifact("sha256:" + "f" * 64, "not-json")
        history = traverse_history(state, frozenset([orphan, malformed_orphan, *reversed(values)]))
        self.assertEqual([item["artifact_id"] for item in history],
                         [first["artifact_id"], second["artifact_id"]])

    def test_missing_manifest_and_state_witness_mismatch_fail_closed(self):
        state, values = chain(initial())
        with self.assertRaises(HistoryCorrupt):
            traverse_history(state, ())
        changed = replace(state, stamp_seq=1)
        with self.assertRaises(HistoryCorrupt):
            traverse_history(changed, values)

    def test_cycle_duplicate_and_wrong_kind_fail_closed(self):
        state = initial()
        item = artifact("research", {"n": 1})
        value = make_artifact(item["body"])
        duplicate_body = {"kind": "transaction_manifest", "version": 1, "parent": None,
                          "artifact_ids": [value.artifact_id, value.artifact_id],
                          "observation_basis_id": "x", "observation_digest": "sha256:" + "0" * 64,
                          "next_state_digest": state_digest(state)}
        manifest = make_artifact(duplicate_body)
        with self.assertRaises(HistoryCorrupt):
            traverse_history(replace(state, committed_artifact_head_id=manifest.artifact_id),
                             [manifest, value])
        with self.assertRaises(HistoryCorrupt):
            traverse_history(replace(state, committed_artifact_head_id=value.artifact_id), [value])

    def test_cas_retry_reuses_content_addresses_without_duplicates(self):
        runs, artifacts_repo, workspace = Runs(), Artifacts(), Workspace()
        coordinator = Coordinator(workspace, Harness(), runs, artifacts_repo,
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

    def test_repeated_domain_artifacts_are_idempotent_not_corrupting(self):
        runs, artifacts_repo, workspace = Runs(), Artifacts(), Workspace()
        coordinator = Coordinator(workspace, Harness(), runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
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
        for command in (graph_command, graph_command, research_command, research_command):
            self.assertEqual(coordinator.handle(command, "repeat")["result"]["type"], "Allow")
        key = next(iter(runs.data))
        state = decode_state(runs.data[key].value)
        history = traverse_history(state, artifacts_repo.values[key].values())
        self.assertEqual([a["kind"] for a in history], ["graph", "research"])

    def test_deduplicated_noop_retries_if_revision_changed(self):
        runs, artifacts_repo = Runs(), Artifacts()
        coordinator = Coordinator(Workspace(), Harness(), runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
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
        coordinator = Coordinator(workspace, Harness(), runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                      "goal": "g"}, "start")
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [{"id": "C0", "text": "t", "gating": True,
                                              "kind": "ordinary"}], "edges": []}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "g")
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
        coordinator = Coordinator(Workspace(), Harness(), runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
        key = RunKey(storage_id("p"), storage_id("s"), 1)
        runs.data[key] = Corrupt("bad bytes")
        get_response = coordinator.handle({"type": "GetRun", "run_id": encode_handle(key)}, "get")
        self.assertEqual(get_response["result"]["type"], "Block")
        self.assertEqual(get_response["result"]["reasons"][0]["code"], "run.corrupt")
        resolve = coordinator.handle({"type": "ResolveRun",
                                      "selector": {"project": "p", "session": "s"}}, "resolve")
        self.assertEqual(resolve["result"]["type"], "Block")
        self.assertEqual(resolve["result"]["reasons"][0]["code"], "run.corrupt")

    def test_inconsistent_frozen_history_uses_fixed_safe_corrupt_projection(self):
        runs, artifacts_repo = Runs(), Artifacts()
        coordinator = Coordinator(Workspace(), Harness(), runs, artifacts_repo,
                                  "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                      "goal": "CANARY_REJECTED_GOAL"}, "start")
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [{"id": "C0", "text": "t", "gating": True,
                                               "kind": "ordinary"}], "edges": []}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "graph")
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "freeze"}}, "freeze")
        key = next(iter(runs.data))
        state = decode_state(runs.data[key].value)
        bad_graph = {"root": "R", "claims": [{"id": "R", "text": "replacement",
                                                  "gating": True, "kind": "ordinary"}], "edges": []}
        graph_artifact = make_artifact({"kind": "graph", "graph": bad_graph})
        artifacts_repo.append(key, graph_artifact)
        next_state = replace(state, selected_graph_artifact_id=graph_artifact.artifact_id)
        manifest = make_artifact({"kind": "transaction_manifest", "version": 1,
            "parent": state.committed_artifact_head_id, "artifact_ids": [graph_artifact.artifact_id],
            "observation_basis_id": "test", "observation_digest": "sha256:" + "0" * 64,
            "next_state_digest": state_digest(next_state)})
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

    def test_read_revision_change_retries_before_projection(self):
        runs, artifacts_repo, workspace = Runs(), Artifacts(), Workspace()
        coordinator = Coordinator(workspace, Harness(), runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                      "goal": "g"}, "start")
        run_id = started["result"]["run"]["id"]
        baseline = runs.read_calls
        runs.bump_on_read.add(baseline + 2)  # revision validation read of the first attempt
        response = coordinator.handle({"type": "GetRun", "run_id": run_id}, "read")
        self.assertEqual(response["result"]["type"], "Allow")
        self.assertGreaterEqual(runs.read_calls - baseline, 4)

    def test_invalid_dependency_candidate_has_zero_writes(self):
        def claim(cid):
            return {"id": cid, "text": cid, "gating": True, "kind": "ordinary"}
        invalid = (
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
                coordinator = Coordinator(Workspace(), Harness(), runs, artifacts_repo,
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

    def test_post_plan_capture_tracks_disjoint_graph_heads(self):
        runs, artifacts_repo, workspace, harness = Runs(), Artifacts(), Workspace(), Harness()
        coordinator = Coordinator(workspace, harness, runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                      "goal": "g"}, "start")
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [
            {"id": "C0", "text": "zero", "gating": True, "kind": "needs-experiment"},
            {"id": "C1", "text": "one", "gating": True, "kind": "needs-experiment"}],
            "edges": [{"from": "C0", "to": "C1", "type": "SupportedBy"}]}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "g")
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
        self.assertEqual(workspace.calls[-1], ("src/1.py",))
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "restore")
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
                coordinator = Coordinator(Workspace(), Harness(), runs, artifacts_repo,
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
        coordinator = Coordinator(Workspace(), Harness(), runs, artifacts_repo,
                                  "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                      "goal": "CANARY_TERMINAL_GOAL"}, "start")
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [{"id": "C0", "text": "t", "gating": True,
                                               "kind": "ordinary"}], "edges": []}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "graph")
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
                coordinator = Coordinator(Workspace(), Harness(), runs, artifacts_repo,
                                          "pi@0.84.1+pi-subagents@0.50.0", {})
                started = coordinator.handle({"type": "StartRun",
                                              "selector": {"project": "p", "session": "s"},
                                              "goal": "CANARY_SELECTED_GRAPH_GOAL"}, "start")
                run_id = started["result"]["run"]["id"]
                graph = {"root": "C0", "claims": [{"id": "C0", "text": "t",
                          "gating": True, "kind": "ordinary"}], "edges": []}
                coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                    "action": {"kind": "graph", "payload": graph}}, "g")
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
                    manifest = make_artifact({"kind": "transaction_manifest", "version": 1,
                        "parent": state.committed_artifact_head_id,
                        "artifact_ids": [invalid.artifact_id],
                        "observation_basis_id": "test",
                        "observation_digest": "sha256:" + "0" * 64,
                        "next_state_digest": state_digest(next_state)})
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
                    coordinator = Coordinator(Workspace(), Harness(), runs, artifacts_repo,
                                              "pi@0.84.1+pi-subagents@0.50.0", {})
                    started = coordinator.handle({"type": "StartRun",
                        "selector": {"project": "p", "session": "s"},
                        "goal": "CANARY_TRUSTED_GOAL"}, "start")
                    run_id = started["result"]["run"]["id"]
                    graph = {"root": "C0", "claims": [{"id": "C0", "text": "t",
                        "gating": True, "kind": "ordinary"}], "edges": []}
                    coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                                        "action": {"kind": "graph", "payload": graph}}, "graph")
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

    def test_spike_result_conflict_retries_without_rerun_or_post_commit_observation(self):
        runs, artifacts_repo, workspace, harness = Runs(), Artifacts(), Workspace(), Harness()
        coordinator = Coordinator(workspace, harness, runs, artifacts_repo, "pi@0.84.1+pi-subagents@0.50.0", {})
        started = coordinator.handle({"type": "StartRun", "selector": {"project": "p", "session": "s"},
                                      "goal": "g"}, "start")
        run_id = started["result"]["run"]["id"]
        graph = {"root": "C0", "claims": [{"id": "C0", "text": "t", "gating": True,
                                              "kind": "needs-experiment"}], "edges": []}
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "graph", "payload": graph}}, "g")
        coordinator.handle({"type": "ObserveAction", "run_id": run_id,
                            "action": {"kind": "research", "claim_id": "C0",
                                       "source_kind": "code", "result": "supports", "payload": {
                                           "source_ref": "test_d7_transactions.py",
                                           "citation": "Retry fixture."}}}, "r")
        workspace.write("src/x.py", b"x")
        # Request commit is the next CAS; inject the conflict on the following result commit.
        runs.conflict_on.add(runs.cas_calls + 2)
        response = coordinator.handle({
            "type": "ObserveAction", "run_id": run_id,
            "action": {"kind": "spike_request", "claim_id": "C0", "command": "test",
                       "dependent_files": ["src/x.py"]}}, "s")
        self.assertEqual(response["result"]["type"], "Allow")
        self.assertEqual(harness.calls, 1)
        # Immutable execution capture plus one complete post-plan capture per result attempt;
        # successful projection performs no additional observation.
        self.assertEqual(len(workspace.calls), 3)
        key = next(iter(runs.data))
        history = traverse_history(decode_state(runs.data[key].value),
                                   artifacts_repo.values[key].values())
        self.assertEqual(sum(a["kind"] == "spike" for a in history), 1)

    def test_inconsistent_persisted_frozen_scope_is_corrupt_not_repairable(self):
        state = replace(initial(), selected_graph_artifact_id="sha256:" + "1" * 64,
                        frozen_claim_ids=("C0",))
        graph = {"root": "R", "claims": [{"id": "R", "text": "root", "kind": "ordinary",
                                             "gating": True}], "edges": []}
        common = {"state": state, "history": (), "graph": graph, "run_id": "er2:test:test"}
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

    def test_projection_is_deterministic_and_has_no_committed_head(self):
        state = initial()
        snapshot = EvaluationSnapshot(
            state=state, history=(), graph=None, run_id="er2:test:test",
            contract_id="empirica/public", contract_version="2.0.0",
            contract_digest="sha256:" + "0" * 64, profile_id="pi@0.84.1+pi-subagents@0.50.0",
            host_tier="foreground_only", command={"type": "GetRun"})
        left, right = project_runview(snapshot), project_runview(snapshot)
        self.assertEqual(left, right)
        self.assertNotIn("committed_artifact_head_id", json.dumps(left))


if __name__ == "__main__":
    unittest.main(verbosity=2)
