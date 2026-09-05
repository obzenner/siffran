#!/usr/bin/env python3
"""Codex MCP adapter for the host-neutral Methodologist selection bridge.

This module translates MCP JSON-RPC into ``methodologist/v1`` requests.  It is
deliberately stateless: the shared core remains authoritative for registry and
phase validation, while the installed skill remains authoritative for all
methodology semantics and phase execution.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_ADAPTERS_DIR = _PLUGIN_ROOT / "adapters"
if str(_ADAPTERS_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTERS_DIR))

from bridge import handle as bridge_handle  # noqa: E402

TOOL_NAME = "methodologist_select"
DEFAULT_PROTOCOL_VERSION = "2025-11-25"


def _plugin_version() -> str:
    manifest = _PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
    try:
        value = json.loads(manifest.read_text()).get("version")
    except (OSError, ValueError):
        return "0.0.0"
    return value if isinstance(value, str) else "0.0.0"


def _error(request_id: object, code: int, message: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_definition() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "title": "Validate Methodologist selection",
        "description": (
            "Validate a methodology name selected semantically from the installed "
            "Methodologist registry and return its canonical six-phase plan. Use only "
            "for explicitly requested structured bridge mode; native skill execution "
            "does not need this tool."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "methodology": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
                "candidates": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "rationale"],
                        "properties": {
                            "name": {"type": "string", "minLength": 1},
                            "rationale": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
            "oneOf": [
                {"required": ["methodology", "reason"]},
                {"required": ["candidates"]},
            ],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def _selection(name: str, reason: str, request_id: str) -> dict[str, object]:
    return bridge_handle(
        {
            "protocol": "methodologist/v1",
            "request_id": request_id,
            "command": {
                "type": "SelectMethodology",
                "intent": reason,
                "requested_methodology": name,
            },
        }
    )


def _selection_text(result: dict[str, Any]) -> str:
    lines = [
        f"Using **{result['methodology']}**: {result['reason']}",
        "",
        "Validated phases:",
    ]
    for index, phase in enumerate(result["phases"], start=1):
        number = phase.get("number", index)
        title = phase.get("title", f"Phase {number}")
        lines.append(f"{number}. {title}")
    return "\n".join(lines)


def _tool_result(arguments: object) -> dict[str, object]:
    if not isinstance(arguments, dict):
        return {
            "content": [{"type": "text", "text": "Arguments must be an object."}],
            "isError": True,
        }

    methodology = arguments.get("methodology")
    reason = arguments.get("reason")
    if isinstance(methodology, str) and methodology.strip() and isinstance(reason, str) and reason.strip():
        response = _selection(methodology.strip(), reason.strip(), "codex-mcp-selection")
        result = response["result"]
        if result.get("type") != "MethodologySelected":
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Methodologist selection failed: {result.get('code', 'invalid_request')}",
                    }
                ],
                "structuredContent": result,
                "isError": True,
            }
        return {
            "content": [{"type": "text", "text": _selection_text(result)}],
            "structuredContent": result,
            "isError": False,
        }

    candidates = arguments.get("candidates")
    if isinstance(candidates, list) and len(candidates) == 2:
        canonical: list[dict[str, str]] = []
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                break
            name = candidate.get("name")
            rationale = candidate.get("rationale")
            if not isinstance(name, str) or not name.strip() or not isinstance(rationale, str) or not rationale.strip():
                break
            response = _selection(name.strip(), rationale.strip(), f"codex-mcp-candidate-{index + 1}")
            result = response["result"]
            if result.get("type") != "MethodologySelected":
                break
            canonical.append(
                {"name": str(result["methodology"]), "rationale": rationale.strip()}
            )
        if len(canonical) == 2:
            result = {
                "type": "HumanDecisionRequired",
                "candidates": canonical,
                "question": "Which methodology addresses the primary uncertainty?",
            }
            choices = "\n".join(
                f"- {item['name']}: {item['rationale']}" for item in canonical
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Ask the user to choose before continuing:\n{choices}\n"
                            "Then call this tool again with the chosen methodology and reason."
                        ),
                    }
                ],
                "structuredContent": result,
                "isError": False,
            }

    return {
        "content": [
            {
                "type": "text",
                "text": "Provide methodology + reason, or exactly two valid candidates.",
            }
        ],
        "isError": True,
    }


def handle_message(message: object) -> dict[str, object] | None:
    """Handle one MCP JSON-RPC message; notifications intentionally return None."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")

    request_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        return _error(request_id, -32600, "Invalid Request")

    if request_id is None:
        return None
    if method == "initialize":
        params = message.get("params")
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        protocol = requested if isinstance(requested, str) else DEFAULT_PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "methodologist",
                    "version": _plugin_version(),
                },
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": [_tool_definition()]},
        }
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict) or params.get("name") != TOOL_NAME:
            return _error(request_id, -32602, "Unknown tool")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": _tool_result(params.get("arguments")),
        }
    return _error(request_id, -32601, "Method not found")


def main() -> int:
    for line in sys.stdin:
        try:
            message = json.loads(line)
            response = handle_message(message)
        except (TypeError, ValueError):
            response = _error(None, -32700, "Parse error")
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
