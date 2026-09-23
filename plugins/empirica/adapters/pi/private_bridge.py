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
        durable["operation_id"], durable["auditor"])


def _plan_json(plan: AuditLaunchPlan) -> dict:
    return {"child_id": plan.child_id, "role_profile": plan.role_profile,
            "argument": plan.argument, "operation_id": plan.operation_id, "auditor": plan.auditor}


def main() -> int:
    try:
        raw = json.load(sys.stdin)
        profile = os.environ["EMPIRICA_HOST_PROFILE_ID"]
        operation = raw["operation"]
        run_id = raw["run_id"]
        payload = raw.get("payload", {})
        if operation == "governance_context":
            result = bridge.trusted_governance_context(profile, run_id, payload)
        elif operation == "governance_decision":
            result = bridge.trusted_governance_decision(profile, run_id, payload)
        elif operation == "audit_prepare":
            plan = AuditProtocol(profile).prepare(
                run_id, role_profile=raw["role_profile"])
            result = {"type": "audit_plan", "plan": _plan_json(plan)}
        elif operation == "audit_start":
            plan = _plan(profile, raw)
            AuditProtocol(profile).observe_started(plan, raw["native_id"])
            result = {"type": "audit_started"}
        elif operation == "audit_identity":
            plan = _plan(profile, raw)
            author = raw["author"]
            auditor = raw["auditor"]
            AuditProtocol(profile).observe_identities(
                plan, raw["native_id"],
                author=IdentityObservation(
                    author.get("provider_id"), author.get("model_id"),
                    author["observed_by"], author["source"]),
                auditor=IdentityObservation(
                    auditor.get("provider_id"), auditor.get("model_id"),
                    auditor["observed_by"], auditor["source"]),
            )
            result = {"type": "audit_identity"}
        elif operation == "audit_failure":
            AuditProtocol(profile).observe_failure(
                _plan(profile, raw), raw["native_id"], raw.get("state", "failed"))
            result = {"type": "audit_terminal"}
        elif operation == "audit_verdict":
            admitted = AuditProtocol(profile).observe_verdict(
                _plan(profile, raw), raw["native_id"], payload)
            result = {"type": "audit_verdict", "admitted": admitted}
        elif operation == "child_event":
            result = bridge.trusted_child_event(profile, run_id, raw["child_id"], payload)
        elif operation == "attribution":
            result = bridge.trusted_attribution(profile, run_id, payload)
        elif operation == "evidence_leaf":
            result = bridge.trusted_evidence_leaf(profile, run_id, payload)
        else:
            raise ValueError("unknown private ingress operation")
        json.dump(result, sys.stdout)
        return 0
    except Exception as exc:  # noqa: BLE001 - private adapter boundary fails closed
        print(f"empirica private ingress failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
