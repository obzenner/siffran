"""Thin Empirica v2 application dispatch shell."""
from __future__ import annotations

from core.projection import project_runview
from . import protocol as _proto
from .location import decode_handle
from .run_state import classify_and_decode
from .transaction import Coordinator


class _Service:
    __slots__ = ("_workspace", "_harness", "_runs", "_artifacts", "_host", "_profile_id",
                 "_limits", "_clock", "_coordinator")

    def __init__(self, workspace, harness, runs, artifacts, host, profile_id, limits, clock):
        self._workspace, self._harness, self._runs = workspace, harness, runs
        self._artifacts, self._host, self._profile_id = artifacts, host, profile_id
        self._limits, self._clock = limits or {}, clock
        self._coordinator = Coordinator(workspace, harness, runs, artifacts, profile_id, limits)

    def dispatch(self, raw: dict) -> dict:
        return _proto.dispatch_request(raw, self._handler)

    def _dispatch_validated(self, envelope: dict) -> dict:
        return self._handler(envelope)

    def _handler(self, envelope: dict) -> dict:
        return self._coordinator.handle(envelope["command"], envelope["request_id"])

    def reload(self) -> "_Service":
        return _Service(self._workspace, self._harness, self._runs, self._artifacts,
                        self._host, self._profile_id, self._limits, self._clock)

    def compact(self) -> dict:
        snapshot = self._coordinator.last_snapshot
        if snapshot is None:
            return {"status": "unsupported"}
        view = project_runview(snapshot, self._coordinator._sections(snapshot))
        view["untrusted_delimiters"] = {
            "open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
            "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"}
        return view

    def operational_state(self) -> dict:
        state = self._coordinator.last_state
        if state is None:
            return {}
        return {"status": state.status, "passes_used": state.budgets["passes_used"],
                "spawns_used": state.budgets["spawns_used"], "stamp_seq": state.stamp_seq,
                "frozen_scope": list(state.frozen_claim_ids or ()),
                "committed_artifact_head_id": state.committed_artifact_head_id}

    def _trusted_terminal(self, run_id: str) -> dict | None:
        key = decode_handle(run_id)
        if key is None:
            return Coordinator._inert("trusted-ingress")
        read = self._runs.read(key)
        if not hasattr(read, "value"):
            return Coordinator._inert("trusted-ingress")
        classification = classify_and_decode(read.value)
        if classification.kind != "valid" or classification.state.status != "active":
            return Coordinator._inert("trusted-ingress")
        return None

    def inject_run_state(self, key, state: dict) -> None:
        """Private conformance seam for strict persisted-state classification tests."""
        self._runs.inject(key, dict(state))
        if type(key) is str:
            self._coordinator.injected_run_ids.add(key)

    @staticmethod
    def _valid_trusted(name: str, payload: object) -> bool:
        return _proto.validate_trusted_payload(name, payload)

    def trusted_resolve_child(self, *, run_id, native_id, purpose="audit") -> str | None:
        """Resolve one native execution through private host correlation only."""
        key = decode_handle(run_id)
        if key is None or not isinstance(native_id, str) or not native_id:
            return None
        read = self._runs.read(key)
        if not hasattr(read, "value"):
            return None
        classification = classify_and_decode(read.value)
        if classification.kind != "valid":
            return None
        matches = [child["child_id"] for child in classification.state.children
                   if child["purpose"] == purpose and child.get("native_id") == native_id]
        return matches[0] if len(matches) == 1 else None

    def trusted_audit_plan(self, *, run_id, child_id) -> dict | None:
        """Return one immutable host-owned audit operation; never projected publicly."""
        if not isinstance(child_id, str) or not child_id:
            return None
        result = self._coordinator.trusted_audit_plan(run_id, child_id)
        return result

    def trusted_child_event(self, *, run_id, child_id, event) -> dict:
        terminal = self._trusted_terminal(run_id)
        if terminal:
            return terminal
        if not self._valid_trusted("childEventPayload", event):
            return Coordinator._fault("trusted-ingress", "invalid_request")
        return self._coordinator.trusted_child_event(
            run_id, child_id, event)

    def trusted_evidence_leaf(self, *, run_id, payload) -> dict:
        terminal = self._trusted_terminal(run_id)
        if terminal:
            return terminal
        key = decode_handle(run_id)
        read = self._runs.read(key)
        state = classify_and_decode(read.value).state
        terminal_children = {"completed", "launch_rejected", "failed", "cancelled",
                             "timed_out", "orphaned"}
        if any(child["state"] in terminal_children for child in state.children):
            snapshot = self._coordinator._assemble(
                key, state, {"type": "GetRun", "run_id": run_id}, require_graph=False)
            return self._coordinator._inert_with_run("trusted-ingress", snapshot)
        return Coordinator._fault("trusted-ingress", "unsupported")

    def trusted_audit_verdict(self, *, run_id, child_id, payload) -> dict:
        terminal = self._trusted_terminal(run_id)
        if terminal:
            return terminal
        if not self._valid_trusted("auditVerdictPayload", payload):
            return Coordinator._fault("trusted-ingress", "invalid_request")
        return self._coordinator.trusted_audit_verdict(
            run_id, child_id, payload)

    def trusted_attribution(self, *, run_id, payload) -> dict:
        terminal = self._trusted_terminal(run_id)
        if terminal:
            return terminal
        if not self._valid_trusted("attributionPayload", payload):
            return Coordinator._fault("trusted-ingress", "invalid_request")
        return self._coordinator.trusted_attribution(
            run_id, payload)


def compose(workspace, harness, runs, artifacts, host, profile_id, limits, clock):
    return _Service(workspace, harness, runs, artifacts, host, profile_id, limits, clock)
