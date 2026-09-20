"""Shared model-callable projection of Empirica's public v2 driving surface.

This adapter contains no convergence policy. It mechanically projects author action
schemas from the canonical PublicContract/request schema, wraps tool arguments in a
correlated v2 envelope, and delegates to the one public bridge dispatcher. Trusted
ingress functions are deliberately neither imported nor registered.
"""
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
_PUBLIC_TOOL_ARTIFACT = Path(__file__).resolve().parents[3] / "contracts" / "empirica" / "v2" / "public-tools.json"

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
    for item in _protocol._REQUEST_SCHEMA["$defs"]["action"]["oneOf"]:
        expanded = _deref(item)
        kind = expanded.get("properties", {}).get("kind", {}).get("const")
        if kind in _AUTHOR_KINDS:
            choices.append(expanded)
    if {row["properties"]["kind"]["const"] for row in choices} != _AUTHOR_KINDS:
        raise RuntimeError("request schema and PublicContract author actions drifted")
    return {"oneOf": choices}


def _project_schemas() -> dict[str, dict]:
    run_id = {"type": "string", "minLength": 1}
    read = {
        "type": "object",
        "additionalProperties": False,
        "required": ["operation"],
        "properties": {
            "operation": {"enum": ["GetRun", "GetArgument", "GetContract", "RestoreRun"]},
            "run_id": run_id,
            "target": {"enum": ["index", "section", "full"]},
            "section_id": {"type": "string", "minLength": 1},
        },
        "allOf": [
            {
                "if": {"properties": {"operation": {"const": "GetContract"}},
                       "required": ["operation"]},
                "then": {"required": ["target"]},
                "else": {"required": ["run_id"]},
            },
            {
                "if": {"properties": {"operation": {"const": "GetContract"},
                                       "target": {"const": "section"}},
                       "required": ["operation", "target"]},
                "then": {"required": ["section_id"]},
            },
        ],
    }
    observe = {
        "type": "object",
        "additionalProperties": False,
        "required": ["run_id", "action"],
        "properties": {"run_id": run_id, "action": _author_action_schema()},
    }
    report = {
        "type": "object", "additionalProperties": False, "required": ["run_id"],
        "properties": {"run_id": run_id,
                       "intent": {"enum": ["report_convergence", "stop"]}},
    }
    return {READ_TOOL: read, OBSERVE_TOOL: observe, REPORT_TOOL: report}


def _host_handle_schemas(model: dict[str, dict]) -> dict[str, dict]:
    """Mechanically remove the run id that a stateful host adapter injects."""
    result = copy.deepcopy(model)
    for name in (OBSERVE_TOOL, REPORT_TOOL):
        result[name]["properties"].pop("run_id", None)
        result[name]["required"] = [field for field in result[name]["required"]
                                     if field != "run_id"]
    read = result[READ_TOOL]
    read["properties"].pop("run_id", None)
    read["allOf"][0].pop("else", None)
    return result


def _load_schemas() -> dict[str, dict[str, dict]]:
    artifact = json.loads(_PUBLIC_TOOL_ARTIFACT.read_text(encoding="utf-8"))
    projected = _project_schemas()
    expected = {"protocol": _protocol._PROTOCOL,
                "schemas": {"model": projected,
                            "host_handle": _host_handle_schemas(projected)}}
    if artifact != expected:
        raise RuntimeError("public-tools.json drifted from the canonical v2 contracts")
    return artifact["schemas"]


_PUBLIC_SCHEMAS = _load_schemas()


class PublicTools:
    """Profile-bound public tools over the canonical Empirica bridge."""

    def __init__(self, profile_id: str, *, dispatch: Dispatch = _bridge.handle):
        if profile_id not in _PROFILES:
            raise ValueError(f"unknown exact Empirica profile: {profile_id!r}")
        self._profile_id = profile_id
        self._dispatch = dispatch
        self._schemas = copy.deepcopy(_PUBLIC_SCHEMAS["model"])

    def definitions(self) -> list[dict[str, object]]:
        descriptions = {
            READ_TOOL: "Read the current Empirica run, audit argument, or public contract.",
            OBSERVE_TOOL: "Submit one public Empirica author action for the active run.",
            REPORT_TOOL: "Ask Empirica for a guarded convergence or honest-stop decision.",
        }
        definitions = []
        for name in _TOOL_ORDER:
            read_only = name == READ_TOOL
            definitions.append({
                "name": name,
                "title": descriptions[name],
                "description": descriptions[name],
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
        text = json.dumps(result, sort_keys=True, separators=(",", ":"))
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": result,
            "isError": result.get("type") == "Fault",
        }

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
