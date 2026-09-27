#!/usr/bin/env python3
"""Pi adapter-private bridge to the canonical audit protocol and trusted ingress."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from adapters import bridge  # noqa: E402
from adapters.audit_protocol import (  # noqa: E402
    AuditLaunchPlan, AuditProtocol, IdentityObservation,
)
from adapters.identity import observe  # noqa: E402


def _plan(profile: str, raw: dict) -> AuditLaunchPlan:
    value = raw["plan"]
    durable = bridge.trusted_audit_plan(profile, raw["run_id"], value["child_id"])
    if (not isinstance(durable, dict)
            or durable.get("operation_id") != value.get("operation_id")
            or durable.get("role_profile") != value.get("role_profile")
            or durable.get("argument") != value.get("argument")):
        raise ValueError("audit operation does not match durable plan")
    return AuditLaunchPlan(
        profile, raw["run_id"], value["child_id"], value["role_profile"], durable["argument"],
        durable["operation_id"])


def _plan_json(plan: AuditLaunchPlan) -> dict:
    return {"child_id": plan.child_id, "role_profile": plan.role_profile,
            "argument": plan.argument, "operation_id": plan.operation_id}


def _classify(_profile: str, _raw: dict, payload: dict):
    return observe(payload.get("provider_id"), payload.get("model_id"),
                   source=payload.get("source", "pi-model"))


def _governance_context(profile: str, raw: dict, payload: dict):
    return bridge.trusted_governance_context(profile, raw["run_id"], payload)


def _governance_decision(profile: str, raw: dict, payload: dict):
    return bridge.trusted_governance_decision(profile, raw["run_id"], payload)


def _audit_prepare(profile: str, raw: dict, _payload: dict):
    plan = AuditProtocol(profile).prepare(raw["run_id"], role_profile=raw["role_profile"])
    return {"type": "audit_plan", "plan": _plan_json(plan)}


def _audit_start(profile: str, raw: dict, _payload: dict):
    AuditProtocol(profile).observe_started(_plan(profile, raw), raw["native_id"])
    return {"type": "audit_started"}


def _audit_identity(profile: str, raw: dict, _payload: dict):
    auditor = raw["auditor"]
    AuditProtocol(profile).observe_reviewer(
        _plan(profile, raw), raw["native_id"], auditor=IdentityObservation(
            auditor.get("provider_id"), auditor.get("model_id"), auditor["source"]))
    return {"type": "audit_identity"}


def _audit_failure(profile: str, raw: dict, _payload: dict):
    AuditProtocol(profile).observe_failure(
        _plan(profile, raw), raw["native_id"], raw.get("state", "failed"))
    return {"type": "audit_terminal"}


def _audit_verdict(profile: str, raw: dict, payload: dict):
    admitted = AuditProtocol(profile).observe_verdict(
        _plan(profile, raw), raw["native_id"], payload)
    return {"type": "audit_verdict", "admitted": admitted}


def _child_event(profile: str, raw: dict, payload: dict):
    return bridge.trusted_child_event(profile, raw["run_id"], raw["child_id"], payload)


def _attribution(profile: str, raw: dict, payload: dict):
    return bridge.trusted_attribution(profile, raw["run_id"], payload)


def _evidence_leaf(profile: str, raw: dict, payload: dict):
    return bridge.trusted_evidence_leaf(profile, raw["run_id"], payload)


_HANDLERS = {
    "classify_identity": _classify,
    "governance_context": _governance_context,
    "governance_decision": _governance_decision,
    "audit_prepare": _audit_prepare,
    "audit_start": _audit_start,
    "audit_identity": _audit_identity,
    "audit_failure": _audit_failure,
    "audit_verdict": _audit_verdict,
    "child_event": _child_event,
    "attribution": _attribution,
    "evidence_leaf": _evidence_leaf,
}


def _dispatch(profile: str, raw: dict, payload: dict):
    handler = _HANDLERS.get(raw["operation"])
    if handler is None:
        raise ValueError("unknown private ingress operation")
    return handler(profile, raw, payload)


def main() -> int:
    try:
        raw = json.load(sys.stdin)
        profile = os.environ["EMPIRICA_HOST_PROFILE_ID"]
        result = _dispatch(profile, raw, raw.get("payload", {}))
        json.dump(result, sys.stdout)
        return 0
    except Exception as exc:  # noqa: BLE001 - private adapter boundary fails closed
        print(f"empirica private ingress failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
