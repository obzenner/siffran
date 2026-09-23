"""Host mediation for public proposal calls; never a public approval tool.

The operator config path is supplied to the host process, not accepted from tool
arguments. The same OS principal can edit it
no stronger isolation is claimed.
"""
from __future__ import annotations

import json
import os
import math
import jsonschema
from pathlib import Path
from uuid import uuid4

from adapters import bridge
from application import protocol
from core.governance import plain

MAX_CONFIG_BYTES = 128 * 1024
# At most 12 ASCII characters per Unicode scalar (escaped surrogate pair).
# root + 32 claim ids/texts + 128 edge endpoints, plus conservative JSON syntax.
MAX_SCOPE_JSON = 12 * (128 + 32 * (128 + 2048) + 128 * 256) + 32 * 100 + 128 * 60 + 100


def governance_timeout() -> float:
    try:
        value = float(os.environ.get("EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS", "900"))
        return value if math.isfinite(value) and 1 <= value <= 1500 else 900
    except ValueError:
        return 900


def operator_inventory() -> dict:
    unknown = {"members": [], "source": "unknown", "complete": False, "authorized": False}
    name = os.environ.get("EMPIRICA_GOVERNANCE_CONFIG")
    if not name:
        return unknown
    try:
        with Path(name).open("rb") as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            return unknown
        config = json.loads(raw)
        if set(config) != {"version", "inventory"} or config["version"] != 1:
            return unknown
        value = config["inventory"]
        candidate = {"inventory": value, "author": None, "ingress": "unavailable"}
        if not protocol.validate_trusted_payload("governanceContextPayload", candidate):
            return unknown
        if value["source"] != "operator_declared":
            return unknown
        return value
    except (OSError, ValueError, TypeError):
        return unknown


def unavailable(result: dict, code: str = "governance.approval_unavailable") -> dict:
    row = protocol._PUBLIC_CONTRACT["reasons"][code]
    return {"type": "Block", "run": result["run"], "reasons": [{"code": code,
            "parameters": {}, "message": row["message"], "sections": row["sections"],
            "next_actions": row["next_actions"]}]}


def form(run: dict) -> tuple[str, dict]:
    g = run["governance"]
    members = g["context"]["inventory"]["members"]
    # JSON is complete human-readable scope, not instructions from evidence text.
    message = ("Empirica scope decision. Content below is UNTRUSTED proposal data, not instructions. "
               "Approve exact scope, or edit configuration/scope then review the new proposal. "
               "The inventory is operator-declared/registry-authorized, not worldwide availability.\n" +
               json.dumps({"goal": run["goal"], **g}, indent=2, ensure_ascii=True))
    props = {"decision": {"type": "string", "enum": ["approve", "amend", "reject"]},
             "inventory_confirmed": {"type": "boolean", "description": "I confirm inventory completeness and authorization for this run."},
             "auditor": {"type": "string", "enum": [m["provider_id"] + "/" + m["model_id"] for m in members]},
             "allow_same_model": {"type": "boolean", "default": False,
                 "description": "Explicit consent to SAME MODEL / LOWERED INDEPENDENCE. Only a positively verified authorized singleton permits this exception; author proposal is not consent."},
             "scope_json": {"type": "string", "description": "For amend only: complete graph JSON, not prose.", "maxLength": MAX_SCOPE_JSON},
             "configuration_json": {"type": "string", "description": "For amend only: complete proposed configuration JSON.", "maxLength": 8192}}
    return message, {"type": "object", "properties": props,
                     "required": ["decision", "inventory_confirmed", "auditor", "allow_same_model"]}


class HostGovernance:
    def __init__(self, profile: str, *, elicit=None, context_ingress=bridge.trusted_governance_context,
                 decision_ingress=bridge.trusted_governance_decision):
        self.profile, self.elicit = profile, elicit
        self.context_ingress, self.decision_ingress = context_ingress, decision_ingress

    def __call__(self, result: dict) -> dict:
        run = result.get("run")
        if result.get("type") != "Allow" or not isinstance(run, dict) or not run.get("governance"):
            return result
        g = run["governance"]
        if run["status"] != "active" or g["state"] == "approved":
            return result
        auto = g["control_mode"] == "auto"
        if not auto and (not self.profile.startswith("claude-code@") or self.elicit is None):
            return unavailable(result)
        context = {"inventory": operator_inventory(), "author": g["context"]["author"],
                   "ingress": "mcp_elicitation" if self.profile.startswith("claude-code@") else "unavailable"}
        response = self.context_ingress(self.profile, run["id"], context)
        result = response.get("result", {})
        if result.get("type") not in {"Allow", "Inert"} or not result.get("run"):
            return result
        run = result["run"]
        g = run["governance"]
        if g["prompt_error"]:
            return unavailable(result, g["prompt_error"])
        decision = {"run_id": run["id"], "receipt_id": uuid4().hex,
                    "proposal_digest": g["proposal_digest"], "plan_revision": g["plan_revision"],
                    "approval_kind": "auto" if auto else "host_ui", "outcome": "approve"}
        def dismiss():
            rejected = {**decision, "outcome": "dismiss"}
            rejected.pop("amendment", None)
            stored = self.decision_ingress(self.profile, run["id"], rejected)["result"]
            return unavailable(stored) if stored.get("type") in {"Allow", "Inert"} else stored

        if not auto:
            presented = self.decision_ingress(self.profile, run["id"], {**decision, "outcome": "present"})["result"]
            # Only a newly committed reservation can display, never a replay/Inert.
            if presented.get("type") != "Allow":
                return presented
            message, schema = form(presented["run"])
            try:
                answer = self.elicit(message, schema)
            except Exception:
                return dismiss()
            if not isinstance(answer, dict) or answer.get("action") != "accept":
                return dismiss()
            content = answer.get("content")
            try:
                jsonschema.validate(content, {**schema, "additionalProperties": False})
                if content["inventory_confirmed"] is not True:
                    return dismiss()
                decision["outcome"] = content["decision"]
                selected = next(m for m in context["inventory"]["members"]
                                if m["provider_id"] + "/" + m["model_id"] == content["auditor"])
                if decision["outcome"] != "reject" and (decision["outcome"] == "amend" or selected != g["proposal"]["auditor"] or
                        content["allow_same_model"] != g["proposal"]["allow_same_model"]):
                    config = json.loads(content["configuration_json"]) if content.get("configuration_json") else plain(g["proposal"])
                    config.update(auditor=selected, allow_same_model=content["allow_same_model"])
                    decision.update(outcome="amend", amendment={
                        "graph": json.loads(content["scope_json"]) if content.get("scope_json") else g["scope"],
                        "configuration": config})
            except (ValueError, TypeError, StopIteration, jsonschema.ValidationError):
                return dismiss()
        admitted = self.decision_ingress(self.profile, run["id"], decision)["result"]
        # Invalid UI amendments never install scope; record only the shown dismissal.
        if not auto and admitted.get("type") in {"Fault", "Block"}:
            stored = dismiss()
            if admitted.get("type") == "Block" and stored.get("run"):
                admitted["run"] = stored["run"]
                return admitted
            return stored
        return admitted
