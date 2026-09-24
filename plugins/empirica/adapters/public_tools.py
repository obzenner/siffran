"""Model-callable projection of Empirica's canonical public v2 surface."""
from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import jsonschema

from application import protocol as _protocol
from adapters import bridge as _bridge

READ_TOOL = "empirica_read"
OBSERVE_TOOL = "empirica_observe"
REPORT_TOOL = "report_convergence"
_TOOL_ORDER = (READ_TOOL, OBSERVE_TOOL, REPORT_TOOL)
_AUTHOR_KINDS = frozenset(_protocol._PUBLIC_CONTRACT["actions"]["author"])
_PROFILES = frozenset(_protocol._PROFILES)
_PUBLIC_TOOL_ARTIFACT = (Path(__file__).resolve().parents[1]
                         / "vendor/contracts/empirica/v2/public-tools.json")

Dispatch = Callable[[dict, str], dict]


def _deref(value: Any) -> Any:
    """Inline local request-schema refs for MCP clients that do not resolve them."""
    if isinstance(value, list):
        return [_deref(item) for item in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    ref = value.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        name = ref.removeprefix("#/$defs/")
        return _deref(_protocol._REQUEST_SCHEMA["$defs"][name])
    return {key: _deref(item) for key, item in value.items()}


def _author_action_schema() -> dict:
    choices = []
    guidance = _protocol._PUBLIC_CONTRACT["bootstrap"]["actions"]
    for item in _protocol._REQUEST_SCHEMA["$defs"]["action"]["oneOf"]:
        expanded = _deref(item)
        kind = expanded.get("properties", {}).get("kind", {}).get("const")
        if kind in _AUTHOR_KINDS:
            if kind in guidance:
                row = guidance[kind]
                if row["operation"] != kind or row["example"].get("kind") != kind:
                    raise RuntimeError("bootstrap action guidance is bound to the wrong request kind")
                expanded.update(description=row["description"], examples=[copy.deepcopy(row["example"])])
            choices.append(expanded)
    if {row["properties"]["kind"]["const"] for row in choices} != _AUTHOR_KINDS:
        raise RuntimeError("request schema and PublicContract author actions drifted")
    return {"oneOf": choices}


def _project_schemas() -> dict[str, dict]:
    run_id = {
        "type": "string",
        "minLength": 1,
        "description": "Opaque identifier of the active Empirica run.",
    }
    read = {
        "type": "object",
        "additionalProperties": False,
        "required": ["operation"],
        "properties": {
            "operation": {
                "enum": ["GetRun", "GetArgument", "GetContract", "RestoreRun"],
                "description": (
                    "Read the public run view, obtain the current audit argument, "
                    "inspect the public contract, or restore a persisted run."
                ),
            },
            "run_id": run_id,
            "target": {
                "enum": ["index", "section", "full"],
                "description": (
                    "Contract projection for GetContract. Use section with section_id."
                ),
            },
            "section_id": {
                "type": "string",
                "minLength": 1,
                "description": "Public contract section identifier when target is section.",
            },
        },
    }
    observe = {
        "type": "object",
        "additionalProperties": False,
        "required": ["run_id", "action"],
        "properties": {
            "run_id": run_id,
            "action": {
                **_author_action_schema(),
                "description": "One schema-validated public author action for this run.",
            },
        },
    }
    report = {
        "type": "object", "additionalProperties": False, "required": ["run_id"],
        "properties": {
            "run_id": run_id,
            "intent": {
                "enum": ["report_convergence", "stop"],
                "description": (
                    "Request convergence by default, or request an honest non-converged stop."
                ),
            },
        },
    }
    return {READ_TOOL: read, OBSERVE_TOOL: observe, REPORT_TOOL: report}


def _host_handle_schemas(model: dict[str, dict]) -> dict[str, dict]:
    """Mechanically remove the run id that a stateful host adapter injects."""
    result = copy.deepcopy(model)
    for name in (OBSERVE_TOOL, REPORT_TOOL):
        result[name]["properties"].pop("run_id", None)
        result[name]["required"] = [field for field in result[name]["required"]
                                     if field != "run_id"]
    result[READ_TOOL]["properties"].pop("run_id", None)
    return result


def _project_public_tools() -> dict:
    bootstrap = _protocol._PUBLIC_CONTRACT["bootstrap"]
    recovery = _protocol._PUBLIC_CONTRACT["reasons"]
    return {"protocol": _protocol._PROTOCOL,
            "definitions": copy.deepcopy(bootstrap["tools"]),
            "bootstrap_actions": copy.deepcopy(bootstrap["actions"]),
            "governance_decisions": copy.deepcopy(_protocol._PUBLIC_CONTRACT["governance_decisions"]),
            "recovery": {code: {key: copy.deepcopy(recovery[code][key])
                       for key in ("message", "sections", "next_actions")}
                       for code in ("graph.missing", "governance.approval_unavailable",
                                    "governance.inventory_unknown", "governance.author_unknown",
                                    "governance.identity_mismatch", "governance.interaction_limit",
                                    "governance.changes_requested", "governance.decision_conflict",
                                    "governance.inventory_unconfirmed", "governance.same_model_unconfirmed", "governance.stale_proposal")},
            "schemas": {"model": _project_schemas(),
                        "host_handle": _host_handle_schemas(_project_schemas())}}


_projected = _project_public_tools()
_PUBLIC_SCHEMAS = _projected["schemas"]


def _load_artifact() -> dict:
    artifact = json.loads(_PUBLIC_TOOL_ARTIFACT.read_text(encoding="utf-8"))
    if artifact != _projected:
        raise RuntimeError("public-tools.json drifted from the canonical v2 contracts")
    return artifact


class PublicTools:
    """Profile-bound public tools over the canonical Empirica bridge."""

    def __init__(self, profile_id: str, *, dispatch: Dispatch = _bridge.handle, govern=None):
        if profile_id not in _PROFILES:
            raise ValueError(f"unknown exact Empirica profile: {profile_id!r}")
        self._profile_id = profile_id
        self._dispatch = dispatch
        self._govern = govern
        self._artifact = _load_artifact()
        self._schemas = copy.deepcopy(self._artifact["schemas"]["model"])

    def definitions(self) -> list[dict[str, object]]:
        metadata = self._artifact["definitions"]
        definitions = []
        for name in _TOOL_ORDER:
            read_only = name == READ_TOOL
            definitions.append({
                "name": name,
                "title": metadata[name]["title"],
                "description": metadata[name]["description"],
                "inputSchema": copy.deepcopy(self._schemas[name]),
                "annotations": {
                    "readOnlyHint": read_only,
                    "destructiveHint": False,
                    "idempotentHint": read_only,
                    "openWorldHint": False,
                },
            })
        return definitions

    def call(self, name: str, arguments: object) -> dict[str, object]:
        schema = self._schemas.get(name)
        if schema is None:
            return self._error("Unknown public Empirica tool.")
        try:
            jsonschema.validate(arguments, schema)
        except jsonschema.ValidationError as exc:
            return self._error(f"Invalid {name} arguments: {exc.message}")
        assert isinstance(arguments, Mapping)
        read_error = self._read_argument_error(name, arguments)
        if read_error is not None:
            return self._error(read_error)
        command = self._command(name, arguments)
        request = {
            "protocol": _protocol._PROTOCOL,
            "request_id": str(uuid.uuid4()),
            "command": command,
        }
        try:
            response = self._dispatch(request, self._profile_id)
        except Exception:  # public transport failure remains a typed tool error
            return self._error("Empirica bridge unavailable.")
        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(result, dict):
            return self._error("Empirica bridge returned no typed result.")
        if self._govern and name == OBSERVE_TOOL and arguments["action"]["kind"] == "configure_run":
            result = self._govern(result)
        text = json.dumps(result, sort_keys=True, separators=(",", ":"))
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": result,
            "isError": result.get("type") == "Fault",
        }

    @staticmethod
    def _read_argument_error(name: str, arguments: Mapping[str, object]) -> str | None:
        if name != READ_TOOL:
            return None
        operation = arguments["operation"]
        if operation != "GetContract":
            if "run_id" not in arguments:
                return f"Invalid {READ_TOOL} arguments: {operation} requires run_id."
            return None
        if "target" not in arguments:
            return f"Invalid {READ_TOOL} arguments: GetContract requires target."
        if arguments["target"] == "section" and "section_id" not in arguments:
            return (
                f"Invalid {READ_TOOL} arguments: GetContract with target=section "
                "requires section_id."
            )
        return None

    @staticmethod
    def _command(name: str, arguments: Mapping[str, object]) -> dict:
        if name == OBSERVE_TOOL:
            return {"type": "ObserveAction", "run_id": arguments["run_id"],
                    "action": copy.deepcopy(arguments["action"])}
        if name == REPORT_TOOL:
            return {"type": "EvaluateRun", "run_id": arguments["run_id"],
                    "intent": arguments.get("intent", "report_convergence")}
        operation = arguments["operation"]
        if operation == "GetContract":
            command = {"type": "GetContract", "target": arguments["target"]}
            if "section_id" in arguments:
                command["section_id"] = arguments["section_id"]
            return command
        return {"type": operation, "run_id": arguments["run_id"]}

    @staticmethod
    def _error(message: str) -> dict[str, object]:
        return {"content": [{"type": "text", "text": message}], "isError": True}
