"""Single-writer transaction coordinator for Empirica v2."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from core.context_selector import select_sections
from core.evaluation import (Decision, EvaluationSnapshot, audit_binding, digest, evaluate_snapshot,
                             plan_spike_request, plan_spike_result)
from core.freshness import canonical_digest
from core.projection import project_argument, project_runview
from core.records import Conflict, Corrupt, RunKey
from core.run import OperationalState
from . import protocol as _proto
from . import run_state
from .location import decode_handle, encode_handle, storage_id
from .observation import (HarnessContractError, HarnessUnavailable, ObservationUnavailable,
                          build_observation_snapshot, execute_spike_bound)
from .snapshot import (GraphInvalid, HistoryCorrupt, active_spike_heads as captured_heads,
                       assemble, graph_from_history, make_artifact, state_digest, traverse_history)


class Coordinator:
    def __init__(self, workspace: Any, harness: Any, runs: Any, artifacts: Any,
                 profile_id: str, limits: dict[str, Any] | None = None):
        self.workspace, self.harness, self.runs, self.artifacts = workspace, harness, runs, artifacts
        self.profile_id, self.limits = profile_id, dict(limits or {})
        self.last_state: OperationalState | None = None
        self.last_snapshot: EvaluationSnapshot | None = None
        self.injected_run_ids: set[str] = set()

    @staticmethod
    def _present(value: Any) -> bool:
        return hasattr(value, "value") and hasattr(value, "revision")

    def _read_artifacts(self, key: RunKey) -> Any:
        if self.artifacts is None:
            return ()
        value = self.artifacts.read(key)
        return value.value if self._present(value) else ()

    def _generations(self, project: str, session: str) -> list[int]:
        if hasattr(self.runs, "generations"):
            return list(self.runs.generations(project, session))
        found: list[int] = []
        for generation in range(1, 10000):
            if not self._present(self.runs.read(RunKey(project, session, generation))):
                break
            found.append(generation)
        return found

    def _latest_key(self, selector: dict[str, str]) -> RunKey | None:
        project, session = storage_id(selector["project"]), storage_id(selector["session"])
        generations = self._generations(project, session)
        return RunKey(project, session, generations[-1]) if generations else None

    def _initial_state(self, command: dict[str, Any]) -> OperationalState:
        modes = {"multi_provider": False, "cli_exec": False}
        modes.update({k: v for k, v in self.limits.get("modes", {}).items() if k in modes})
        modes.update(command.get("modes", {}))
        budgets = {"max_passes": 8, "passes_used": 0, "max_spawns": 1, "spawns_used": 0}
        supplied_limits = self.limits.get("budgets", self.limits)
        budgets.update({k: v for k, v in supplied_limits.items() if k in {"max_passes", "max_spawns"}})
        budgets.update(command.get("budgets", {}))
        return OperationalState(
            protocol=_proto._PROTOCOL, state_schema=_proto._STATE_SCHEMA_ID,
            goal=command["goal"], status="active", modes=modes, budgets=budgets,
            selected_graph_artifact_id=None, frozen_claim_ids=None, route_stamp=None,
            investigation_stamp=None, stamp_seq=0, last_derivation_digest=None,
            children=(), committed_artifact_head_id=None,
        )

    def _append_once(self, key: RunKey, value: Any) -> None:
        existing = self._read_artifacts(key)
        if any(a.artifact_id == value.artifact_id for a in existing):
            return
        self.artifacts.append(key, value)

    def _manifest(self, key: RunKey, state: OperationalState, parent: str | None,
                  domain: tuple[dict[str, Any], ...], basis: str, observation_digest: str):
        for item in domain:
            self._append_once(key, make_artifact(item["body"]))
        body = {
            "kind": "transaction_manifest", "version": 1, "parent": parent,
            "artifact_ids": [item["artifact_id"] for item in domain],
            "observation_basis_id": basis, "observation_digest": observation_digest,
            "next_state_digest": state_digest(state),
        }
        manifest = make_artifact(body)
        self._append_once(key, manifest)
        return manifest

    def start(self, command: dict[str, Any], request_id: str) -> dict[str, Any]:
        if self.artifacts is None:
            return self._fault(request_id, "unsupported")
        selector = command["selector"]
        latest = self._latest_key(selector)
        if latest is not None:
            current = self.runs.read(latest)
            if self._present(current):
                classification = run_state.classify_and_decode(current.value)
                if classification.kind == "valid" and classification.state.status == "active":
                    response = self._read_response(latest, current, command, request_id)
                    reasons = response.get("result", {}).get("reasons", [])
                    if not any(r.get("code") == "run.corrupt" for r in reasons):
                        return response
            key = RunKey(latest.project_id, latest.run_id, latest.generation + 1)
        else:
            key = RunKey(storage_id(selector["project"]), storage_id(selector["session"]), 1)
        state = self._initial_state(command)
        empty = canonical_digest(())
        manifest = self._manifest(key, state, None, (), empty, empty)
        state = replace(state, committed_artifact_head_id=manifest.artifact_id)
        try:
            self.runs.create(key, run_state.encode_state(state))
        except Exception:
            winner = self.runs.read(key)
            if not self._present(winner):
                return self._fault(request_id, "conflict")
            return self._read_response(key, winner, command, request_id)
        self.last_state = state
        snapshot = self._assemble(key, state, command, require_graph=False)
        return self._allow(request_id, snapshot)

    def resolve(self, command: dict[str, Any], request_id: str) -> dict[str, Any]:
        key = self._latest_key(command["selector"])
        if key is None:
            return self._inert(request_id)
        read = self.runs.read(key)
        if isinstance(read, Corrupt) or not self._present(read):
            return self._inert(request_id)
        classification = run_state.classify_and_decode(read.value)
        if classification.kind != "valid" or classification.state.status != "active":
            return self._inert(request_id)
        return self._read_response(key, read, command, request_id)

    def handle(self, command: dict[str, Any], request_id: str) -> dict[str, Any]:
        if command["type"] == "GetContract":
            value = _proto.contract_result(command["target"], command.get("section_id"))
            if value is None:
                return self._fault(request_id, "unsupported")
            return {"protocol": _proto._PROTOCOL, "request_id": request_id,
                    "result": {"type": "Allow", "contract_result": value}}
        if command["type"] == "StartRun":
            return self.start(command, request_id)
        if command["type"] == "ResolveRun":
            return self.resolve(command, request_id)
        key = decode_handle(command.get("run_id"))
        if key is None:
            raw_id = command.get("run_id")
            if type(raw_id) is str and (raw_id in self.injected_run_ids or self.artifacts is None):
                candidate = self.runs.read(raw_id)
                if not self._present(candidate):
                    return self._inert(request_id)
                key = raw_id
            else:
                return self._inert(request_id)
        if command["type"] == "ObserveAction" and command["action"]["kind"] == "spike_request":
            return self._spike(key, command, request_id)
        for _ in range(8):
            read = self.runs.read(key)
            if isinstance(read, Corrupt):
                return self._safe_block(key, {}, request_id, "run.corrupt")
            if not self._present(read):
                return self._inert(request_id)
            classification = run_state.classify_and_decode(read.value)
            if classification.kind == "old_unsupported":
                return self._safe_block(key, read.value, request_id, "run.old_version")
            if classification.kind != "valid":
                return self._safe_block(key, read.value, request_id, "run.corrupt")
            state = classification.state
            if self.artifacts is None:
                return self._fault(request_id, "unsupported")
            if state.status != "active" and command["type"] == "EvaluateRun" and command["intent"] == "continue":
                return self._inert(request_id)
            if state.status != "active" and command["type"] == "ObserveAction":
                return self._inert(request_id)
            require_graph = command["type"] == "GetArgument" or (
                command["type"] == "EvaluateRun" and state.selected_graph_artifact_id is not None)
            if command["type"] == "ObserveAction":
                require_graph = command["action"]["kind"] in {"research", "freeze"}
            try:
                snapshot = self._assemble(key, state, command, require_graph=require_graph)
            except HistoryCorrupt:
                if not self._revision_unchanged(key, read.revision):
                    continue
                return self._block_from_state(key, state, command, request_id, "run.corrupt")
            except GraphInvalid:
                if not self._revision_unchanged(key, read.revision):
                    continue
                return self._block_from_state(key, state, command, request_id, "graph.invalid")
            except ObservationUnavailable:
                return self._fault(request_id, "unavailable")
            try:
                decision = evaluate_snapshot(snapshot, command)
            except Exception:
                return self._fault(request_id, "unavailable")
            if decision.result_type in {"Inert", "Fault"} or (
                    decision.result_type == "Block" and decision.intent.state == state
                    and not decision.intent.artifacts) or (
                    decision.result_type == "Allow" and decision.intent.state == state
                    and not decision.intent.artifacts):
                if not self._revision_unchanged(key, read.revision):
                    continue
                self.last_state = state
                return self._response(request_id, snapshot, decision)
            try:
                state, _, _, committed = self._commit(
                    key, read.revision, snapshot, decision.intent.state,
                    decision.intent.artifacts)
            except Exception as exc:
                if self._is_conflict(exc):
                    continue
                return self._fault(request_id, "unavailable")
            self.last_state = state
            return self._response(request_id, committed,
                                  replace(decision, intent=replace(decision.intent, state=state)))
        return self._fault(request_id, "conflict")

    def trusted_attribution(self, run_id: str, payload: dict[str, Any],
                            request_id: str = "trusted-ingress") -> dict[str, Any]:
        return self._trusted_artifact(run_id, "attribution", payload, request_id)

    def _trusted_artifact(self, run_id: str, kind: str, payload: dict[str, Any], request_id: str,
                          child_id: str | None = None) -> dict[str, Any]:
        key = decode_handle(run_id)
        if key is None:
            return self._inert(request_id)
        planned_artifact = {"artifact_id": digest({"kind": kind, **payload}),
                            "body": {"kind": kind, **payload}}
        for _ in range(8):
            read = self.runs.read(key)
            if not self._present(read):
                return self._inert(request_id)
            classified = run_state.classify_and_decode(read.value)
            if classified.kind != "valid" or classified.state.status != "active":
                return self._inert(request_id)
            state = classified.state
            snapshot = self._assemble(key, state, {"type": "GetRun", "run_id": run_id},
                                      require_graph=False)
            same_key = [a for a in snapshot.history if a.get("kind") == kind and (
                (kind == "audit_verdict" and a.get("child_id") == child_id) or
                (kind == "attribution" and a.get("subject_kind") == payload.get("subject_kind")
                 and a.get("subject_id") == payload.get("subject_id")))]
            if same_key:
                if same_key[-1]["artifact_id"] == planned_artifact["artifact_id"]:
                    return self._inert_with_run(request_id, snapshot)
                return self._fault_with_run(request_id, snapshot)
            next_state = state
            if kind == "audit_verdict":
                expected = audit_binding(snapshot)
                bound_keys = ("argument_digest", "goal_digest", "frozen_scope_digest",
                              "deferred_scope_digest", "reviewed_claims")
                if any(payload.get(name) != expected[name] for name in bound_keys):
                    return self._fault_with_run(request_id, snapshot)
                expected_scope = expected["scope_review"]
                if ((expected_scope is None and payload.get("scope_review") is not None) or
                    (expected_scope is not None and payload.get("scope_review") not in {"pass", "fail"}) or
                    (payload.get("verdict") == "pass" and payload.get("scope_review") != expected_scope)):
                    return self._fault_with_run(request_id, snapshot)
                index = next((i for i, c in enumerate(state.children)
                              if c["child_id"] == child_id and c["purpose"] == "audit"), None)
                if index is None or state.children[index]["state"] != "pending":
                    return self._fault_with_run(request_id, snapshot)
                children = list(state.children)
                child = dict(children[index])
                child.update(state="completed", spent=True,
                             first_terminal_fingerprint=planned_artifact["artifact_id"])
                children[index] = child
                next_state = replace(state, children=tuple(children))
            try:
                committed, _, _, planned = self._commit(
                    key, read.revision, snapshot, next_state, (planned_artifact,))
            except Exception as exc:
                if self._is_conflict(exc):
                    continue
                return self._fault(request_id, "unavailable")
            self.last_state = committed
            return self._allow(request_id, planned)
        return self._fault(request_id, "conflict")

    def trusted_audit_verdict(self, run_id: str, child_id: str, payload: dict[str, Any],
                              request_id: str = "trusted-ingress") -> dict[str, Any]:
        body = {"child_id": child_id, **payload}
        return self._trusted_artifact(run_id, "audit_verdict", body, request_id, child_id)

    def trusted_child_event(self, run_id: str, child_id: str, event: dict[str, Any],
                            request_id: str = "trusted-ingress") -> dict[str, Any]:
        key = decode_handle(run_id)
        if key is None:
            return self._inert(request_id)
        terminal = {"completed", "launch_rejected", "failed", "cancelled", "timed_out", "orphaned"}
        edges = {("reserved", "launching"), ("reserved", "launch_rejected"),
                 ("launching", "pending"), ("launching", "launch_rejected"),
                 ("launching", "failed"), ("pending", "completed"),
                 ("pending", "failed"), ("pending", "cancelled"),
                 ("pending", "timed_out"), ("pending", "orphaned")}
        for _ in range(8):
            read = self.runs.read(key)
            if not self._present(read):
                return self._inert(request_id)
            classified = run_state.classify_and_decode(read.value)
            if classified.kind != "valid" or classified.state.status != "active":
                return self._inert(request_id)
            state = classified.state
            snapshot = self._assemble(key, state, {"type": "GetRun", "run_id": run_id},
                                      require_graph=False)
            index = next((i for i, row in enumerate(state.children)
                          if row["child_id"] == child_id), None)
            if index is None:
                return self._fault_with_run(request_id, snapshot)
            child = dict(state.children[index])
            target, fingerprint = event["state"], event["fingerprint"]
            if child["state"] in terminal:
                if child["state"] == target and child["first_terminal_fingerprint"] == fingerprint:
                    return self._inert_with_run(request_id, snapshot)
                return self._fault_with_run(request_id, snapshot)
            if child["state"] == target:
                if child.get("native_id") == event.get("native_id"):
                    return self._allow(request_id, snapshot)
                return self._fault_with_run(request_id, snapshot)
            if (child["state"], target) not in edges:
                return self._fault_with_run(request_id, snapshot)
            child["state"] = target
            if target in {"launching", "pending"} | (terminal - {"launch_rejected"}):
                child["native_id"] = event["native_id"]
                child["spent"] = True
            if target in terminal:
                child["first_terminal_fingerprint"] = fingerprint
            budgets = dict(state.budgets)
            if target == "launch_rejected":
                child.update(spent=False, refunded=True, native_id=None)
                budgets["spawns_used"] -= 1
            children = list(state.children)
            children[index] = child
            next_state = replace(state, children=tuple(children), budgets=budgets)
            try:
                committed, _, _, planned = self._commit(
                    key, read.revision, snapshot, next_state, ())
            except Exception as exc:
                if self._is_conflict(exc):
                    continue
                return self._fault(request_id, "unavailable")
            self.last_state = committed
            if target in {"launch_rejected", "failed", "cancelled", "timed_out", "orphaned"}:
                return self._block_from_snapshot(planned, request_id, "child.terminal",
                                                 {"state": target})
            return self._allow(request_id, planned)
        return self._fault(request_id, "conflict")

    def _fault_with_run(self, request_id: str, snapshot: EvaluationSnapshot):
        return {"protocol": _proto._PROTOCOL, "request_id": request_id,
                "result": {"type": "Fault", "code": "conflict", "fail_direction": "closed",
                           "run": project_runview(snapshot)}}

    def _inert_with_run(self, request_id: str, snapshot: EvaluationSnapshot):
        return {"protocol": _proto._PROTOCOL, "request_id": request_id,
                "result": {"type": "Inert", "reason": "unsupported_host_event",
                           "run": project_runview(snapshot)}}

    def _spike(self, key: RunKey, command: dict[str, Any], request_id: str) -> dict[str, Any]:
        action = command["action"]
        for _ in range(8):
            read = self.runs.read(key)
            if isinstance(read, Corrupt):
                return self._safe_block(key, {}, request_id, "run.corrupt")
            if not self._present(read):
                return self._inert(request_id)
            classification = run_state.classify_and_decode(read.value)
            if classification.kind == "old_unsupported":
                return self._safe_block(key, read.value, request_id, "run.old_version")
            if classification.kind != "valid":
                return self._safe_block(key, read.value, request_id, "run.corrupt")
            if classification.state.status != "active":
                return self._inert(request_id)
            state = classification.state
            try:
                snapshot = self._assemble(key, state, command, require_graph=True)
            except HistoryCorrupt:
                return self._block_from_state(key, state, command, request_id, "run.corrupt")
            except GraphInvalid:
                return self._block_from_state(key, state, command, request_id, "graph.invalid")
            decision = plan_spike_request(snapshot, action)
            if decision.result_type != "Allow":
                return self._response(request_id, snapshot, decision)
            request_art = decision.intent.artifacts[0]
            try:
                state, _, _, _ = self._commit(key, read.revision, snapshot, state, (request_art,))
                break
            except Exception as exc:
                if self._is_conflict(exc):
                    continue
                return self._fault(request_id, "unavailable")
        else:
            return self._fault(request_id, "conflict")
        try:
            facts, _execution, _execution_observation = execute_spike_bound(
                action["command"], tuple(sorted(action["dependent_files"])),
                self.workspace, self.harness)
        except (ObservationUnavailable, HarnessUnavailable, HarnessContractError, ValueError):
            return self._fault(request_id, "unavailable")
        for _ in range(8):
            read = self.runs.read(key)
            if isinstance(read, Corrupt):
                return self._safe_block(key, {}, request_id, "run.corrupt")
            classification = run_state.classify_and_decode(read.value) if self._present(read) else None
            if not classification:
                return self._inert(request_id)
            if classification.kind == "old_unsupported":
                return self._safe_block(key, read.value, request_id, "run.old_version")
            if classification.kind != "valid":
                return self._safe_block(key, read.value, request_id, "run.corrupt")
            state = classification.state
            if state.status != "active":
                return self._inert(request_id)
            try:
                snapshot = self._assemble(key, state, command, require_graph=True)
            except HistoryCorrupt:
                return self._block_from_state(key, state, command, request_id, "run.corrupt")
            except GraphInvalid:
                return self._block_from_state(key, state, command, request_id, "graph.invalid")
            except ObservationUnavailable:
                return self._fault(request_id, "unavailable")
            decision = plan_spike_result(snapshot, request_art["body"], facts)
            if decision.result_type != "Allow":
                return self._response(request_id, snapshot, decision)
            result_art = decision.intent.artifacts[0]
            try:
                state, _, _, committed = self._commit(
                    key, read.revision, snapshot, state, (result_art,))
                self.last_state = state
                return self._allow(request_id, committed)
            except Exception as exc:
                if self._is_conflict(exc):
                    continue
                return self._fault(request_id, "unavailable")
        return self._fault(request_id, "conflict")

    @staticmethod
    def _is_conflict(exc: Exception) -> bool:
        return isinstance(exc, Conflict) or (hasattr(exc, "key") and hasattr(exc, "expected"))

    def _revision_unchanged(self, key: Any, revision: Any) -> bool:
        latest = self.runs.read(key)
        return self._present(latest) and latest.revision == revision

    def _post_plan(self, snapshot: EvaluationSnapshot, state: OperationalState,
                   domain: tuple[dict[str, Any], ...]) -> EvaluationSnapshot:
        history = tuple(snapshot.history) + tuple({"artifact_id": item["artifact_id"], **item["body"]}
                                                  for item in domain)
        graph = graph_from_history(
            state, history, required=state.selected_graph_artifact_id is not None)
        before_heads = captured_heads(tuple(snapshot.history), snapshot.graph)
        after_heads = captured_heads(history, graph)
        if before_heads == after_heads:
            return replace(snapshot, state=state, history=history, graph=graph)
        observation = build_observation_snapshot(after_heads, self.workspace)
        return replace(
            snapshot, state=state, history=history, graph=graph,
            observations=observation.observations,
            observation_basis_id=observation.basis_id,
            observation_digest=observation.digest)

    def _assemble(self, key: RunKey, state: OperationalState, command: dict[str, Any],
                  *, require_graph: bool) -> EvaluationSnapshot:
        return assemble(state, self._read_artifacts(key), self.workspace, run_id=encode_handle(key),
                        profile_id=self.profile_id, command=command, require_graph=require_graph)

    def _commit(self, key: RunKey, revision: Any, snapshot: EvaluationSnapshot,
                next_state: OperationalState, domain: tuple[dict[str, Any], ...]):
        reachable = {item["artifact_id"] for item in snapshot.history}
        unique: dict[str, dict[str, Any]] = {}
        for item in domain:
            if item["artifact_id"] not in reachable:
                unique.setdefault(item["artifact_id"], item)
        effective = tuple(unique.values())
        if not effective and next_state == snapshot.state:
            if not self._revision_unchanged(key, revision):
                raise Conflict(key, revision, "deduplicated no-op lost its read revision")
            return snapshot.state, revision, (), snapshot
        planned = self._post_plan(snapshot, next_state, effective)
        manifest = self._manifest(
            key, next_state, snapshot.state.committed_artifact_head_id, effective,
            planned.observation_basis_id, planned.observation_digest)
        committed = replace(next_state, committed_artifact_head_id=manifest.artifact_id)
        checked = traverse_history(committed, self._read_artifacts(key))
        expected_ids = [item["artifact_id"] for item in snapshot.history] + [
            item["artifact_id"] for item in effective]
        if [item["artifact_id"] for item in checked] != expected_ids:
            raise HistoryCorrupt("provisional committed history mismatch")
        new_revision = self.runs.compare_and_set(
            key, run_state.encode_state(committed), revision)
        return committed, new_revision, effective, replace(planned, state=committed)

    def _read_response(self, key: RunKey, read: Any, command: dict[str, Any], request_id: str,
                       retries: int = 8):
        classification = run_state.classify_and_decode(read.value)
        if command["type"] == "ResolveRun" and (
                classification.kind != "valid" or classification.state.status != "active"):
            return self._inert(request_id)
        if classification.kind == "old_unsupported":
            return self._safe_block(key, read.value, request_id, "run.old_version")
        if classification.kind != "valid":
            return self._safe_block(key, read.value, request_id, "run.corrupt")
        state = classification.state
        try:
            snapshot = self._assemble(key, state, command, require_graph=False)
        except HistoryCorrupt:
            if command["type"] == "ResolveRun":
                return self._inert(request_id)
            return self._block_from_state(key, state, command, request_id, "run.corrupt")
        except ObservationUnavailable:
            return self._fault(request_id, "unavailable")
        if not self._revision_unchanged(key, read.revision):
            if retries <= 1:
                return self._fault(request_id, "conflict")
            latest = self.runs.read(key)
            if isinstance(latest, Corrupt):
                return (self._inert(request_id) if command["type"] == "ResolveRun"
                        else self._safe_block(key, {}, request_id, "run.corrupt"))
            if not self._present(latest):
                return self._inert(request_id)
            return self._read_response(key, latest, command, request_id, retries - 1)
        self.last_state = state
        return self._allow(request_id, snapshot)

    def _response(self, request_id: str, snapshot: EvaluationSnapshot, decision: Decision):
        if decision.result_type == "Inert":
            return self._inert(request_id)
        if decision.result_type == "Fault":
            return self._fault(request_id, decision.reason_code or "unsupported")
        if decision.result_type == "Block":
            return self._block_from_snapshot(snapshot, request_id, decision.reason_code or "run.corrupt",
                                             dict(decision.parameters), decision.affected_obligation_id)
        return self._allow(request_id, snapshot, argument=snapshot.command["type"] == "GetArgument")

    @staticmethod
    def _sections(snapshot: EvaluationSnapshot, reasons: list[str] | None = None) -> list[str]:
        command = snapshot.command or {}
        kind = command.get("type")
        context = ("bootstrap" if kind == "StartRun" else
                   "restore" if kind in {"RestoreRun", "ResolveRun"} else
                   "auditor" if kind == "GetArgument" else
                   "terminal" if snapshot.state.status != "active" else "on_demand")
        terminal = snapshot.state.status if snapshot.state.status != "active" else None
        return select_sections(context, list(reasons or ()), terminal)

    def _allow(self, request_id: str, snapshot: EvaluationSnapshot, argument: bool = False):
        self.last_snapshot = snapshot
        result = {"type": "Allow", "converged": snapshot.state.status == "converged",
                  "run": project_runview(snapshot, self._sections(snapshot))}
        if argument:
            result["argument"] = project_argument(snapshot)
        return {"protocol": _proto._PROTOCOL, "request_id": request_id, "result": result}

    def _reason(self, code: str, parameters: dict[str, Any] | None = None,
                affected: str | None = None):
        spec = _proto._PUBLIC_CONTRACT["reasons"][code]
        row = {"code": code, "parameters": parameters or {},
               "next_actions": list(spec["next_actions"]), "sections": list(spec["sections"]),
               "message": spec["message"]}
        if affected:
            row["affected"] = {"obligation_id": affected}
        return row

    def _block_from_snapshot(self, snapshot: EvaluationSnapshot, request_id: str, code: str,
                             parameters: dict[str, Any] | None = None, affected: str | None = None):
        self.last_snapshot = snapshot
        reason = self._reason(code, parameters, affected)
        run = project_runview(snapshot, select_sections("block", [code],
                                                        snapshot.state.status
                                                        if snapshot.state.status != "active" else None))
        return {"protocol": _proto._PROTOCOL, "request_id": request_id,
                "result": {"type": "Block", "run": run, "reasons": [reason]}}

    def _block_from_state(self, key: RunKey, state: OperationalState, command: dict[str, Any],
                          request_id: str, code: str):
        profile = _proto._PROFILES[self.profile_id]
        run_id = encode_handle(key) if isinstance(key, RunKey) else str(key)
        snapshot = EvaluationSnapshot(state, (), None, run_id=run_id,
            contract_id=_proto._PUBLIC_CONTRACT["id"], contract_version=_proto._PUBLIC_CONTRACT["version"],
            contract_digest=_proto._DIGEST, profile_id=self.profile_id,
            host_tier=profile["current_tier"], command=command)
        return self._block_from_snapshot(snapshot, request_id, code)

    def _safe_block(self, key: RunKey, raw: Any, request_id: str, code: str):
        goal = raw.get("goal") if isinstance(raw, dict) and isinstance(raw.get("goal"), str) else "Unsupported run state."
        spec = _proto._PUBLIC_CONTRACT["reasons"][code]
        sections = list(spec["sections"])
        if "protocol" not in sections:
            sections.append("protocol")
        profile = _proto._PROFILES[self.profile_id]
        run_id = encode_handle(key) if isinstance(key, RunKey) else str(key)
        run = {
            "id": run_id, "goal": goal, "status": "active",
            "modes": {"multi_provider": False, "cli_exec": False},
            "contract": {"id": _proto._PUBLIC_CONTRACT["id"],
                         "version": _proto._PUBLIC_CONTRACT["version"],
                         "digest": _proto._DIGEST, "relevant_sections": sections},
            "obligations": {"active": [], "deferred": []}, "residuals": [],
            "freshness": {"changes": []}, "children": [], "next_actions": [],
            "untrusted_delimiters": {"open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
                                     "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"},
            "host": {"profile_id": self.profile_id, "tier": profile["current_tier"],
                     "missing_capabilities": list(profile["unsupported_reason_ids"])},
        }
        return {"protocol": _proto._PROTOCOL, "request_id": request_id,
                "result": {"type": "Block", "run": run,
                           "reasons": [self._reason(code)]}}

    @staticmethod
    def _inert(request_id: str):
        return {"protocol": _proto._PROTOCOL, "request_id": request_id,
                "result": {"type": "Inert", "reason": "no_run"}}

    @staticmethod
    def _fault(request_id: str, code: str):
        allowed = {"invalid_request", "unsupported", "conflict", "corrupt_run", "corrupt_artifacts", "unavailable"}
        return {"protocol": _proto._PROTOCOL, "request_id": request_id,
                "result": {"type": "Fault", "code": code if code in allowed else "unsupported",
                           "fail_direction": "closed"}}
