"""Host mediation for public proposal calls; never a public approval tool."""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
import re
from typing import Any, Mapping
from uuid import uuid4

import jsonschema

from adapters import bridge
from application import protocol


@dataclass(frozen=True)
class Approve:
    """A validated approval carrying the exact displayed configuration."""

    configuration: dict[str, dict[str, int]]


@dataclass(frozen=True)
class Reject:
    """A deliberate rejection from the initial review dialog."""


@dataclass(frozen=True)
class Dismiss:
    """A non-decision or invalid host answer that grants no authority."""


def governance_timeout() -> float:
    """Return the bounded host decision timeout in seconds."""
    try:
        value = float(os.environ.get("EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS", "900"))
        return value if math.isfinite(value) and 1 <= value <= 1500 else 900
    except ValueError:
        return 900


def unavailable(result: dict, code: str = "governance.approval_unavailable", *, message: str | None = None) -> dict:
    row = protocol._PUBLIC_CONTRACT["reasons"][code]
    return {"type": "Block", "run": result["run"], "reasons": [{"code": code,
            "parameters": {}, "message": message or row["message"], "sections": row["sections"],
            "next_actions": row["next_actions"]}]}


def _duration(timeout: float) -> str:
    minutes = timeout / 60
    return f"{minutes:g} min" if minutes >= 1 and minutes.is_integer() else f"{timeout:g} sec"


def _line(text: str, limit: int = 72, *, closing: str = "") -> str:
    """Truncate one safe display line while preserving a required suffix."""
    if len(text) <= limit:
        return text
    return text[:limit - len(closing) - 1] + "…" + closing


def _budget_property(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map one budget row to an editable integer property."""
    description = f"used {row['used']} · allowed {row['minimum']}–{row['maximum']}"
    with_help = description + " · " + row["help"]
    return {"type": "integer", "title": row["label"],
            "description": with_help if len(with_help) <= 60 else description,
            "minimum": row["minimum"], "maximum": row["maximum"], "default": row["value"]}


def review_form(dialog: Mapping[str, Any], timeout: float) -> tuple[str, dict]:
    """Map a validated dialog model to Claude's editable elicitation form."""
    reviews = dialog["reviews_left"]["proposal"]
    count = f"{reviews} {'review' if reviews == 1 else 'reviews'} left"
    message = "\n".join((
        _line(f"Empirica · approve run configuration (epoch {dialog['epoch']} · {count} · {_duration(timeout)})"),
        _line(f'Goal: "{dialog["goal"]}"', closing='"'),
        "Accept = approve as shown · edit values → confirm again",
        "Decline = reject · Esc = decide later",
    ))
    properties = {row["key"]: _budget_property(row) for row in dialog["budgets"]}
    return message, {"type": "object", "properties": properties, "required": []}


def _part(row: Mapping[str, Any], old: object) -> str:
    """Render one compact confirmation value and its prior value when changed."""
    show = (lambda value: "on" if value else "off") if isinstance(row["value"], bool) else str
    value, previous = show(row["value"]), show(old)
    suffix = f" (was {previous})" if old != row["value"] else ""
    return f"{row['short']} {value}{suffix}"


def confirm_form(dialog: Mapping[str, Any], before: Mapping[str, Any], timeout: float) -> tuple[str, dict]:
    """Map an amended dialog and its prior display to a buttons-only confirmation."""
    old = {row["key"]: row["value"] for row in before["budgets"]}
    budget_parts = [_part(row, old[row["key"]]) for row in dialog["budgets"]]
    message = "\n".join((
        _line(f"Empirica · confirm edited configuration (epoch {dialog['epoch']} · {_duration(timeout)})"),
        _line(" · ".join(budget_parts)),
        "Accept = approve exactly this · Decline/Esc = keep edits pending",
    ))
    return message, {"type": "object", "properties": {}}


def decision(answer: object, dialog: Mapping[str, Any], schema: Mapping[str, Any], *, confirmation: bool) -> Approve | Reject | Dismiss:
    """Validate one MCP answer and return a typed, fail-closed host decision."""
    if not isinstance(answer, dict):
        return Dismiss()
    action = answer.get("action")
    if action == "decline":
        return Dismiss() if confirmation else Reject()
    if action != "accept":
        return Dismiss()
    raw_content = answer.get("content", {}) if confirmation else answer.get("content")
    if not isinstance(raw_content, dict):
        return Dismiss()
    content = dict(raw_content)
    for row in dialog["budgets"]:
        key = row["key"]
        if isinstance(content.get(key), str) and re.fullmatch(r"[0-9]{1,4}", content[key]):
            content[key] = int(content[key])
        if key in content and (isinstance(content[key], bool) or not isinstance(content[key], int)):
            return Dismiss()
    try:
        jsonschema.validate(content, {**schema, "additionalProperties": False})
    except (TypeError, jsonschema.ValidationError):
        return Dismiss()
    if confirmation:
        return Approve(_configuration(dialog, {})) if not content else Dismiss()
    return Approve(_configuration(dialog, content))


def _configuration(dialog: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """Build an exact configuration from validated form values and displayed defaults."""
    return {
        "budgets": {row["key"]: content.get(row["key"], row["value"]) for row in dialog["budgets"]},
    }


def _strip_presentation(result: dict) -> dict:
    """Return an envelope copy without the private presentation key."""
    if isinstance(result, dict) and "presentation" in result:
        return {key: value for key, value in result.items() if key != "presentation"}
    return result


class HostGovernance:
    def __init__(self, profile: str, *, elicit=None, context_ingress=bridge.trusted_governance_context,
                 decision_ingress=bridge.trusted_governance_decision):
        self.profile, self.elicit = profile, elicit
        self.context_ingress, self.decision_ingress = context_ingress, decision_ingress

    def __call__(self, result: dict, *, confirmation: bool = False) -> dict:
        """Finalize one host mediation and convert all failures to typed closed results."""
        try:
            mediated = self._mediate(result, confirmation=confirmation)
        except Exception:
            run = result.get("run") if isinstance(result, dict) else None
            if isinstance(run, dict):
                return _strip_presentation(unavailable(result))
            return _strip_presentation(result)
        return _strip_presentation(mediated)

    def _mediate(self, result: dict, *, confirmation: bool = False,
                 before: Mapping[str, Any] | None = None) -> dict:
        run = result.get("run")
        if result.get("type") != "Allow" or not isinstance(run, dict) or not run.get("governance"):
            return result
        g = run["governance"]
        expected = (g["plan_revision"], g["proposal_digest"])
        if run["status"] != "active" or g["state"] == "approved":
            return result
        auto = g["control_mode"] == "auto"
        approval_ingress = protocol.host_profile(self.profile)["approval_ingress"]
        if not auto and (approval_ingress == "unavailable" or self.elicit is None):
            return unavailable(result)
        context = {"author": g["context"]["author"], "ingress": approval_ingress}
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

        if auto:
            decision_envelope = {**envelope, "outcome": "approve"}
            action = "approve"
            dialog = None
        else:
            presented = self.decision_ingress(self.profile, run["id"], {**envelope, "outcome": "present"})["result"]
            if presented.get("type") != "Allow":
                return presented
            dialog = presented["presentation"]["dialog"]
            if not isinstance(dialog, dict):
                return dismiss()
            timeout = governance_timeout()
            message, schema = (confirm_form(dialog, before, timeout) if confirmation and before is not None
                               else review_form(dialog, timeout))
            resolved = decision(self.elicit(message, schema), dialog, schema, confirmation=confirmation)
            if isinstance(resolved, Dismiss):
                return dismiss()
            action = "reject" if isinstance(resolved, Reject) else "approve"
            configuration = _configuration(dialog, {}) if isinstance(resolved, Reject) else resolved.configuration
            decision_envelope = {**envelope, "submission": {"action": action, "configuration": configuration}}
        admitted = self.decision_ingress(self.profile, run["id"], decision_envelope)["result"]
        if not auto and admitted.get("type") in {"Fault", "Block"}:
            stored = dismiss()
            if admitted.get("type") == "Block" and stored.get("run"):
                admitted["run"] = stored["run"]
                return admitted
            return stored
        revised = admitted.get("run", {}).get("governance", {}).get("plan_revision") != g["plan_revision"]
        if not confirmation and action == "approve" and admitted.get("type") == "Allow" and revised:
            return self._mediate(admitted, confirmation=True, before=dialog)
        return admitted
