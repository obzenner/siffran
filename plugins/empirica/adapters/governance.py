"""Host mediation for public proposal calls; never a public approval tool.

The operator config path is supplied to the host process, not accepted from tool
arguments. The same OS principal can edit it; no stronger isolation is claimed.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from uuid import uuid4

import jsonschema

from adapters import bridge
from application import protocol
from core.governance import CEILINGS, inventory_status, plain
from core.projection import safe_text as safe

MAX_CONFIG_BYTES = 128 * 1024


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


def unavailable(result: dict, code: str = "governance.approval_unavailable", *, message: str | None = None) -> dict:
    row = protocol._PUBLIC_CONTRACT["reasons"][code]
    return {"type": "Block", "run": result["run"], "reasons": [{"code": code,
            "parameters": {}, "message": message or row["message"], "sections": row["sections"],
            "next_actions": row["next_actions"]}]}


def form(run: dict, *, confirmation: bool = False) -> tuple[str, dict]:
    g, proposed = run["governance"], run["governance"]["proposal"]
    controls = protocol._GOVERNANCE_CONTROLS
    members = g["context"]["inventory"]["members"]
    actions = controls["confirmation"]["actions"] if confirmation else list(controls["actions"])
    props = {"decision": {"type": "string", "enum": actions, "title": "Your decision"},
             "inventory_confirmed": {"type": "boolean", "title": controls["controls"]["inventory"]}}
    if not confirmation:
        for key, row in controls["controls"]["budgets"].items():
            props[key] = {"type": "integer", "minimum": max(1 if key == "max_passes" else 0, g["budgets"][CEILINGS[key]]),
                          "maximum": row["maximum"], "default": proposed["budgets"][key], "title": row["label"]}
        props.update({key: {"type": "boolean", "default": proposed["modes"][key], "title": label}
                      for key, label in controls["controls"]["modes"].items()})
        props["auditor"] = {"type": "string", "enum": [safe(m["provider_id"] + "/" + m["model_id"]) for m in members],
                            "title": controls["controls"]["auditor"]}
    if inventory_status(g["context"]["inventory"]) == "singleton":
        props["allow_same_model"] = {"type": "boolean", "default": False, "title": "SAME MODEL: checked proposes exception on Edit; consents on Approve"}
    message = controls["confirmation"]["title"] + "\n" if confirmation else ""
    return message + g["review_text"], {"type": "object", "properties": props, "required": ["decision"]}


def feedback_form() -> tuple[str, dict]:
    title = protocol._GOVERNANCE_CONTROLS["controls"]["feedback"]
    return title, {"type": "object", "properties": {"feedback": {"type": "string", "maxLength": 4096, "title": title}},
                   "required": ["feedback"], "additionalProperties": False}


class HostGovernance:
    def __init__(self, profile: str, *, elicit=None, context_ingress=bridge.trusted_governance_context,
                 decision_ingress=bridge.trusted_governance_decision):
        self.profile, self.elicit = profile, elicit
        self.context_ingress, self.decision_ingress = context_ingress, decision_ingress

    def __call__(self, result: dict, *, confirmation: bool = False) -> dict:
        run = result.get("run")
        if result.get("type") != "Allow" or not isinstance(run, dict) or not run.get("governance"):
            return result
        g = run["governance"]
        expected = (g["plan_revision"], g["proposal_digest"])
        if run["status"] != "active" or g["state"] == "approved":
            return result
        auto = g["control_mode"] == "auto"
        if not auto and (not self.profile.startswith("claude-code@") or self.elicit is None):
            return unavailable(result)
        context = {"inventory": operator_inventory(), "author": g["context"]["author"],
                   "ingress": "mcp_elicitation" if self.profile.startswith("claude-code@") else "unavailable"}
        result = self.context_ingress(self.profile, run["id"], context).get("result", {})
        if result.get("type") not in {"Allow", "Inert"} or not result.get("run"):
            return result
        run, g = result["run"], result["run"]["governance"]
        if confirmation and expected != (g["plan_revision"], g["proposal_digest"]):
            return unavailable(result, "governance.stale_proposal")
        if g["prompt_error"]:
            return unavailable(result, g["prompt_error"])
        envelope = {"run_id": run["id"], "receipt_id": uuid4().hex, "proposal_digest": g["proposal_digest"],
                    "plan_revision": g["plan_revision"], "approval_kind": "auto" if auto else "host_ui"}
        def dismiss(message=None):
            stored = self.decision_ingress(self.profile, run["id"], {**envelope, "outcome": "dismiss"})["result"]
            return unavailable(stored, message=message) if stored.get("type") in {"Allow", "Inert"} else stored
        action = "approve"
        if not auto:
            presented = self.decision_ingress(self.profile, run["id"], {**envelope, "outcome": "present"})["result"]
            if presented.get("type") != "Allow":
                return presented
            message, schema = form(presented["run"], confirmation=confirmation)
            try:
                answer = self.elicit(message, schema)
                if not isinstance(answer, dict) or answer.get("action") != "accept" or not isinstance(answer.get("content"), dict):
                    return dismiss()
                content = dict(answer["content"])
                for key in protocol._GOVERNANCE_CONTROLS["controls"]["budgets"]:
                    if isinstance(content.get(key), str) and re.fullmatch(r"\d{1,4}", content[key]):
                        content[key] = int(content[key])
                jsonschema.validate(content, {**schema, "additionalProperties": False})
                action = content["decision"]
                if confirmation and action == "decline":
                    return dismiss()
                proposal = plain(g["proposal"])
                if not confirmation:
                    proposal["budgets"].update({k: content.get(k, proposal["budgets"][k])
                                                for k in protocol._GOVERNANCE_CONTROLS["controls"]["budgets"]})
                    proposal["modes"].update({k: content.get(k, proposal["modes"][k]) for k in ("multi_provider", "cli_exec")})
                    if "auditor" in content:
                        proposal["auditor"] = next(m for m in context["inventory"]["members"] if safe(m["provider_id"] + "/" + m["model_id"]) == content["auditor"])
                    if content.get("allow_same_model") is True:
                        proposal["allow_same_model"] = True
                feedback = ""
                if action == "request_changes":
                    envelope = {**envelope, "receipt_id": uuid4().hex}
                    reserved = self.decision_ingress(self.profile, run["id"], {**envelope, "outcome": "present"})["result"]
                    if reserved.get("type") != "Allow":
                        return reserved
                    feedback_answer = self.elicit(*feedback_form())
                    if not isinstance(feedback_answer, dict) or feedback_answer.get("action") != "accept" or not isinstance(feedback_answer.get("content"), dict):
                        return dismiss()
                    jsonschema.validate(feedback_answer["content"], feedback_form()[1])
                    feedback = feedback_answer["content"]["feedback"]
                submission = {"action": action, "configuration": proposal,
                              "inventory_confirmed": content.get("inventory_confirmed", False),
                              "allow_same_model": content.get("allow_same_model", False)}
                if action == "request_changes":
                    submission["feedback"] = feedback
                decision = {**envelope, "submission": submission}
            except (ValueError, TypeError, StopIteration, jsonschema.ValidationError):
                return dismiss()
        else:
            decision = {**envelope, "outcome": "approve"}
        admitted = self.decision_ingress(self.profile, run["id"], decision)["result"]
        if not auto and admitted.get("type") in {"Fault", "Block"}:
            stored = dismiss()
            if admitted.get("type") == "Block" and stored.get("run"):
                admitted["run"] = stored["run"]
                return admitted
            return stored
        revised = (admitted.get("run", {}).get("governance", {}).get("plan_revision") != g["plan_revision"])
        if not confirmation and action in {"approve", "edit"} and admitted.get("type") == "Allow" and revised:
            # Follow the authoritative amendment result with exactly one locked confirmation.
            return self(admitted, confirmation=True)
        if action == "request_changes" and admitted.get("type") in {"Allow", "Inert"}:
            return unavailable(admitted, "governance.changes_requested")
        if action == "edit" and admitted.get("type") in {"Allow", "Inert"} and not revised:
            return unavailable(admitted)
        return admitted
