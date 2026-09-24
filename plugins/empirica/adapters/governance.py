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

MAX_CONFIG_BYTES = 128 * 1024
LIMITS = {"max_passes": 1024, "max_spawns": 128, "max_audit_spawns": 128}
_INVISIBLE = re.compile(r"[\\\x00-\x1f\x7f-\x9f\u00ad\u061c\u200b-\u200f\u2028-\u202e\u2060\u2066-\u2069\ufeff]")


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


def safe(value: object) -> str:
    def escape(match):
        code = ord(match.group())
        return "\\\\" if code == 92 else (f"\\x{code:02x}" if code <= 255 else f"\\u{code:04x}")
    return _INVISIBLE.sub(escape, str(value))


def readable(run: dict) -> str:
    g, lines = run["governance"], []
    graph, proposal, context = g["scope"], g["proposal"], g["context"]
    inventory, author = context["inventory"], context["author"]

    def fence(value):
        lines.append("| " + safe(value))

    lines += [f"EMPIRICA SCOPE DECISION — proposal revision {g['plan_revision']}, at most {g['revision_limit']} revisions, mode {g['control_mode']}",
              "Approve the CURRENT displayed proposal; edits are submitted for another review and are NOT approved yet.",
              "Every line beginning '| ' is UNTRUSTED quoted data. Controls, bidi characters, and backslashes are visibly escaped.", "", "GOAL"]
    fence(run["goal"])
    lines += ["", f"CLAIM GRAPH — root {safe(graph['root'])}, {len(graph['claims'])} claims, {len(graph['edges'])} dependencies"]
    for claim in graph["claims"]:
        fence(f"[{claim['id']}] {'gating' if claim['gating'] else 'non-gating'} {claim['kind']} {claim['text']}")
    lines.append("DEPENDENCIES")
    for edge in graph["edges"]:
        fence(f"{edge['from']} {edge['type']} {edge['to']}")
    budgets, modes = proposal["budgets"], proposal["modes"]
    lines += ["", "CONFIGURATION"]
    for key, label in (("max_passes", "Investigation passes"), ("max_spawns", "Child spawns"), ("max_audit_spawns", "Audit spawns")):
        lines.append(f"  {label}: proposed {budgets[key]}, already used {g['budgets'][CEILINGS[key]]}")
    lines += [f"  multi_provider (cross-provider actors): {modes['multi_provider']}",
              f"  cli_exec (external model/actor CLI use): {modes['cli_exec']}",
              f"  Auditor: {safe(proposal['auditor']['provider_id'] + '/' + proposal['auditor']['model_id']) if proposal['auditor'] else 'not selected'}",
              f"  Same-model lowered-independence consent: {proposal['allow_same_model']}",
              f"  Inventory: source={safe(inventory['source'])}, complete={inventory['complete']}, authorized={inventory['authorized']}",
              f"  Author (host-observed): {safe(author['provider_id'] + '/' + author['model_id']) if author else 'unknown'}", "WHO MAY AUDIT"]
    for member in inventory["members"]:
        fence(member["provider_id"] + "/" + member["model_id"])
    lines += ["", f"STATE — {g['state']}; dialogs left {g['interactions_remaining']['proposal']} this revision, {g['interactions_remaining']['total']} total", "OPEN CHANGE REQUEST"]
    request = g.get("change_request")
    if request:
        lines.append(f"  requested at revision {request['plan_revision']} for {request['proposal_digest']}")
        fence(request["text"])
    else:
        lines.append("  none")
    lines += ["", "TECHNICAL DETAIL (secondary)", f"  proposal digest {g['proposal_digest']}",
              f"  ingress {context['ingress']} · plan revision {g['plan_revision']} · revision limit {g['revision_limit']}"]
    return "\n".join(lines)


def form(run: dict) -> tuple[str, dict]:
    g, proposed = run["governance"], run["governance"]["proposal"]
    members = g["context"]["inventory"]["members"]
    props = {"decision": {"type": "string", "enum": ["approve", "request_changes", "reject"], "title": "Your decision"},
             "inventory_confirmed": {"type": "boolean", "title": "Inventory is complete and authorized"},
             "change_request": {"type": "string", "maxLength": 4096, "title": "What must change? (plain language; approves nothing)"}}
    for key, label in (("max_passes", "Investigation passes"), ("max_spawns", "Child spawns"), ("max_audit_spawns", "Audit spawns")):
        props[key] = {"type": "integer", "minimum": max(1 if key == "max_passes" else 0, g["budgets"][CEILINGS[key]]),
                      "maximum": LIMITS[key], "default": proposed["budgets"][key], "title": label}
    props.update(multi_provider={"type": "boolean", "default": proposed["modes"]["multi_provider"], "title": "Cross-provider actors"},
                 cli_exec={"type": "boolean", "default": proposed["modes"]["cli_exec"], "title": "External model/actor CLI use"},
                 auditor={"type": "string", "enum": [safe(m["provider_id"] + "/" + m["model_id"]) for m in members], "title": "Independent auditor"})
    if inventory_status(g["context"]["inventory"]) == "singleton":
        props["allow_same_model"] = {"type": "boolean", "default": False, "title": "SAME MODEL / LOWERED INDEPENDENCE consent"}
    return readable(run), {"type": "object", "properties": props, "required": ["decision"]}


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
        result = self.context_ingress(self.profile, run["id"], context).get("result", {})
        if result.get("type") not in {"Allow", "Inert"} or not result.get("run"):
            return result
        run, g = result["run"], result["run"]["governance"]
        if g["prompt_error"]:
            return unavailable(result, g["prompt_error"])
        decision = {"run_id": run["id"], "receipt_id": uuid4().hex, "proposal_digest": g["proposal_digest"],
                    "plan_revision": g["plan_revision"], "approval_kind": "auto" if auto else "host_ui", "outcome": "approve"}
        def dismiss():
            rejected = {k: v for k, v in {**decision, "outcome": "dismiss"}.items() if k not in {"amendment", "change_request"}}
            stored = self.decision_ingress(self.profile, run["id"], rejected)["result"]
            return unavailable(stored) if stored.get("type") in {"Allow", "Inert"} else stored
        if not auto:
            presented = self.decision_ingress(self.profile, run["id"], {**decision, "outcome": "present"})["result"]
            if presented.get("type") != "Allow":
                return presented
            message, schema = form(presented["run"])
            try:
                answer = self.elicit(message, schema)
                if not isinstance(answer, dict) or answer.get("action") != "accept" or not isinstance(answer.get("content"), dict):
                    return dismiss()
                content = dict(answer["content"])
                for key in LIMITS:
                    if isinstance(content.get(key), str) and re.fullmatch(r"\d{1,4}", content[key]):
                        content[key] = int(content[key])
                jsonschema.validate(content, {**schema, "additionalProperties": False})
                action = content["decision"]
                if action == "reject":
                    decision["outcome"] = "reject"
                else:
                    proposal = plain(g["proposal"])
                    proposal["budgets"].update({k: content.get(k, proposal["budgets"][k]) for k in LIMITS})
                    proposal["modes"].update({k: content.get(k, proposal["modes"][k]) for k in ("multi_provider", "cli_exec")})
                    if "auditor" in content:
                        proposal["auditor"] = next(m for m in context["inventory"]["members"] if safe(m["provider_id"] + "/" + m["model_id"]) == content["auditor"])
                    proposal["allow_same_model"] = content.get("allow_same_model", False if g["inventory_status"] == "singleton" else proposal["allow_same_model"])
                    changed = proposal != plain(g["proposal"])
                    # Exact human text is stored; trimming only decides whether feedback exists.
                    text = content.get("change_request", "")
                    if not text.strip():
                        text = ""
                    if action == "approve" and text:
                        action = "request_changes"
                    if action == "approve" and content.get("inventory_confirmed") is not True:
                        return dismiss()
                    if action == "approve" and changed:
                        action = "amend"
                    if action == "request_changes" and not text:
                        if not changed:
                            return dismiss()
                        action = "amend"
                    decision["outcome"] = action
                    if changed:
                        decision["amendment"] = {"graph": g["scope"], "configuration": proposal}
                    if action == "request_changes":
                        decision["change_request"] = text
            except (ValueError, TypeError, StopIteration, jsonschema.ValidationError):
                return dismiss()
        admitted = self.decision_ingress(self.profile, run["id"], decision)["result"]
        if not auto and admitted.get("type") in {"Fault", "Block"}:
            stored = dismiss()
            if admitted.get("type") == "Block" and stored.get("run"):
                admitted["run"] = stored["run"]
                return admitted
            return stored
        if decision["outcome"] == "request_changes" and admitted.get("type") in {"Allow", "Inert"}:
            return unavailable(admitted, "governance.changes_requested")
        return admitted
