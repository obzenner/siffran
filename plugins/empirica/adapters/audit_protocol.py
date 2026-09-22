"""One host-neutral audit lifecycle over narrow native host seams.

The protocol owns reservation, dossier binding, trusted lifecycle ordering, identity
attribution, replay handling, failure reconciliation, and verdict admission. Host adapters
only decide when a native execution has started and translate its terminal output.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import uuid4

from adapters import bridge as application_bridge
from adapters.audit import child_event

Dispatch = Callable[[dict, str], dict]
TrustedEvent = Callable[[str, str, str, dict], dict]
TrustedAttribution = Callable[[str, str, dict], dict]
TrustedVerdict = Callable[[str, str, str, dict], dict]
TrustedPlan = Callable[[str, str, str], dict | None]


class AuditProtocolError(RuntimeError):
    """A typed audit operation could not preserve its lifecycle invariants."""


@dataclass(frozen=True)
class IdentityObservation:
    provider_id: str | None
    model_id: str | None
    observed_by: str
    source: str

    @property
    def concrete(self) -> bool:
        return bool(self.provider_id and self.model_id)


@dataclass(frozen=True)
class AuditLaunchPlan:
    profile_id: str
    run_id: str
    child_id: str
    role_profile: str
    argument: dict
    operation_id: str = ""

    @property
    def evidence_ids(self) -> list[str]:
        claims = self.argument.get("claims", [])
        return [artifact_id
                for claim in claims if isinstance(claim, Mapping)
                and claim.get("gating") is True and claim.get("state") == "approved"
                for artifact_id in claim.get("active_evidence_ids", [])
                if isinstance(artifact_id, str)]


class AuditProtocol:
    """Canonical lifecycle; the exact host profile owns foreground versus async execution."""

    def __init__(
        self, profile_id: str, *, execution: str = "foreground",
        dispatch: Dispatch = application_bridge.handle,
        child_event_ingress: TrustedEvent = application_bridge.trusted_child_event,
        attribution_ingress: TrustedAttribution = application_bridge.trusted_attribution,
        verdict_ingress: TrustedVerdict = application_bridge.trusted_audit_verdict,
        plan_ingress: TrustedPlan = application_bridge.trusted_audit_plan,
    ) -> None:
        if execution not in {"foreground", "async"}:
            raise ValueError("audit execution must be foreground or async")
        self.profile_id, self.execution = profile_id, execution
        self._dispatch = dispatch
        self._child_event = child_event_ingress
        self._attribution = attribution_ingress
        self._verdict = verdict_ingress
        self._plan = plan_ingress

    def _request(self, command: dict) -> dict:
        response = self._dispatch({
            "protocol": "empirica/v2", "request_id": f"audit:{uuid4().hex}",
            "command": command,
        }, self.profile_id)
        if not isinstance(response, Mapping):
            raise AuditProtocolError("audit bridge returned no response")
        return dict(response)

    @staticmethod
    def _allow_or_inert(response: object) -> bool:
        return (isinstance(response, Mapping)
                and isinstance(response.get("result"), Mapping)
                and response["result"].get("type") in {"Allow", "Inert"})

    def prepare(self, run_id: str, *, role_profile: str) -> AuditLaunchPlan:
        """Atomically reserve one host-owned audit child and bind the current dossier."""
        current = self._request({"type": "GetRun", "run_id": run_id})
        current_result = current.get("result", {})
        current_run = current_result.get("run", {}) if isinstance(current_result, Mapping) else {}
        current_children = (current_run.get("children", [])
                            if isinstance(current_run, Mapping) else [])
        # The coordinator atomically distinguishes a current audit from a stale pending one.
        before = {child.get("child_id") for child in current_children
                  if isinstance(child, Mapping)}
        reserved = self._request({"type": "ObserveAction", "run_id": run_id, "action": {
            "kind": "child_reserve", "purpose": "audit", "role_profile": role_profile,
            "execution": self.execution, "resource_class": "audit",
        }})
        result = reserved.get("result", {})
        if not isinstance(result, Mapping) or result.get("type") != "Allow":
            raise AuditProtocolError("audit reservation denied")
        run = result.get("run", {})
        children = run.get("children", []) if isinstance(run, Mapping) else []
        candidates = [child for child in children if isinstance(child, Mapping)
                      and child.get("child_id") not in before
                      and child.get("resource_class") == "audit" and child.get("state") == "reserved"]
        if len(candidates) != 1 or not isinstance(candidates[0].get("child_id"), str):
            for candidate in candidates:
                candidate_id = candidate.get("child_id")
                if isinstance(candidate_id, str):
                    self._terminal(candidate_id, run_id, "launch_rejected", None)
            raise AuditProtocolError("audit reservation is not uniquely bound")
        child_id = str(candidates[0]["child_id"])
        try:
            operation = self._plan(self.profile_id, run_id, child_id)
            argument = operation.get("argument") if isinstance(operation, Mapping) else None
            operation_id = operation.get("operation_id") if isinstance(operation, Mapping) else None
            durable_role = operation.get("role_profile") if isinstance(operation, Mapping) else None
            if (not isinstance(argument, dict) or not isinstance(operation_id, str)
                    or durable_role != role_profile):
                raise AuditProtocolError("durable audit operation unavailable")
            return AuditLaunchPlan(
                self.profile_id, run_id, child_id, durable_role, argument, operation_id)
        except Exception as exc:
            try:
                self._terminal(child_id, run_id, "launch_rejected", None)
            except AuditProtocolError:
                pass
            if isinstance(exc, AuditProtocolError):
                raise
            raise AuditProtocolError("durable audit operation unavailable") from exc

    def reconcile_orphans(self, run_id: str, *, native_prefix: str, include_pending: bool = True) -> int:
        current = self._request({"type": "GetRun", "run_id": run_id})
        result = current.get("result", {})
        run = result.get("run", {}) if isinstance(result, Mapping) else {}
        children = run.get("children", []) if isinstance(run, Mapping) else []
        active = [child for child in children if isinstance(child, Mapping)
                  and child.get("resource_class") == "audit"
                  and child.get("state") in ({"reserved", "launching", "pending"} if include_pending
                                              else {"reserved", "launching"})
                  and isinstance(child.get("child_id"), str)]
        for child in active:
            child_id = str(child["child_id"])
            self._terminal(child_id, run_id, "orphaned", f"{native_prefix}:{child_id}")
        return len(active)

    def observe_started(self, plan: AuditLaunchPlan, native_id: str) -> None:
        """Bind a host-observed native start to the reserved operation."""
        if not native_id:
            raise AuditProtocolError("native execution id is required")
        stage = "reserved"
        try:
            self._require(self._child_event(
                self.profile_id, plan.run_id, plan.child_id,
                child_event("launching", native_id)))
            stage = "launching"
            self._require(self._child_event(
                self.profile_id, plan.run_id, plan.child_id,
                child_event("pending", native_id)))
        except Exception:
            terminal = "launch_rejected" if stage in {"reserved", "launching"} else "failed"
            self._terminal(plan.child_id, plan.run_id, terminal,
                           None if terminal == "launch_rejected" else native_id)
            raise

    def observe_identities(
        self, plan: AuditLaunchPlan, native_id: str, *,
        author: IdentityObservation, auditor: IdentityObservation,
    ) -> None:
        """Admit identities observed for the exact pending audit operation."""
        try:
            self._require(self._attribution(self.profile_id, plan.run_id, {
                "subject_kind": "covered_actor",
                "subject_id": f"author:{plan.argument.get('argument_digest', plan.child_id)}",
                "child_id": None,
                "provider_id": author.provider_id,
                "model_id": author.model_id,
                "observed_by": author.observed_by,
                "covered_artifact_ids": plan.evidence_ids,
            }))
            self._require(self._attribution(self.profile_id, plan.run_id, {
                "subject_kind": "auditor", "subject_id": f"auditor:{plan.child_id}",
                "child_id": plan.child_id,
                "provider_id": auditor.provider_id,
                "model_id": auditor.model_id,
                "observed_by": auditor.observed_by,
                "covered_artifact_ids": [],
            }))
        except Exception:
            self._terminal(plan.child_id, plan.run_id, "failed", native_id)
            raise AuditProtocolError("audit identity observation rejected")

    def observe_verdict(
        self, plan: AuditLaunchPlan, native_id: str, candidate: Mapping[str, object],
    ) -> bool:
        """Admit the first bound verdict; rejection closes the exact operation as failed."""
        response = self._verdict(
            self.profile_id, plan.run_id, plan.child_id, dict(candidate))
        admitted = self._allow_or_inert(response)
        if not admitted:
            self._terminal(plan.child_id, plan.run_id, "failed", native_id)
        return admitted

    def observe_failure(self, plan: AuditLaunchPlan, native_id: str, state: str = "failed") -> None:
        if state not in {"failed", "cancelled", "timed_out", "orphaned"}:
            raise ValueError("invalid started-audit failure state")
        self._terminal(plan.child_id, plan.run_id, state, native_id)

    def reject(self, plan: AuditLaunchPlan) -> None:
        self._terminal(plan.child_id, plan.run_id, "launch_rejected", None)

    def _terminal(self, child_id: str, run_id: str, state: str, native_id: str | None) -> None:
        response = self._child_event(
            self.profile_id, run_id, child_id, child_event(state, native_id))
        try:
            result = response["result"]
            result_type = result.get("type")
            reconciled = (result_type in {"Allow", "Inert"} or (result_type == "Block"
                and len(result.get("reasons", [])) == 1
                and result["reasons"][0].get("code") == "child.terminal"
                and result["reasons"][0].get("parameters") == {"state": state})) and sum(
                    (child.get("child_id"), child.get("state"), child.get("resource_class"))
                    == (child_id, state, "audit") for child in result.get("run", {}).get("children", [])) == 1
        except (AttributeError, KeyError, TypeError):
            reconciled = False
        if not reconciled:
            raise AuditProtocolError(f"audit terminal reconciliation rejected: {state}")

    @classmethod
    def _require(cls, response: object) -> None:
        if not cls._allow_or_inert(response):
            raise AuditProtocolError("trusted audit ingress rejected")
