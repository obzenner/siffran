#!/usr/bin/env python3
"""Claude/Codex MCP 2025-11-25 stdio transport for Empirica's public tools."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from adapters.public_tools import PublicTools  # noqa: E402

PROTOCOL_VERSION = "2025-11-25"


def profile_from_environment(environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    explicit = env.get("EMPIRICA_HOST_PROFILE_ID")
    if explicit:
        return explicit
    if env.get("CLAUDE_PLUGIN_ROOT"):
        return "claude-code@2.1.278"
    if env.get("PLUGIN_ROOT"):
        return "codex-cli@0.146.0"
    raise ValueError("Empirica MCP requires an exact host profile")


def _version() -> str:
    for manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        try:
            value = json.loads((_PLUGIN_ROOT / manifest).read_text())["version"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(value, str):
            return value
    return "0.0.0"


def _error(request_id: object, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def handle_message(message: object, tools: PublicTools) -> dict[str, object] | None:
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
        if not isinstance(params, dict) or not isinstance(params.get("protocolVersion"), str):
            return _error(request_id, -32602, "Invalid initialization")
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "empirica", "version": _version()},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"tools": tools.definitions()}}
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _error(request_id, -32602, "Invalid tool call")
        names = {item["name"] for item in tools.definitions()}
        if params["name"] not in names:
            return _error(request_id, -32602, "Unknown tool")
        return {"jsonrpc": "2.0", "id": request_id,
                "result": tools.call(params["name"], params.get("arguments"))}
    return _error(request_id, -32601, "Method not found")


def main() -> int:
    try:
        tools = PublicTools(profile_from_environment())
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except (TypeError, ValueError):
            response = _error(None, -32700, "Parse error")
        else:
            try:
                response = handle_message(message, tools)
            except Exception:  # one failing call must not terminate the MCP server
                response = _error(message.get("id") if isinstance(message, dict) else None,
                                  -32603, "Internal error")
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
