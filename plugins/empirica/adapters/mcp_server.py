#!/usr/bin/env python3
"""Claude/Codex MCP 2025-11-25 stdio transport for Empirica's public tools."""
from __future__ import annotations

import json
import os
import sys
import queue
import threading
import time
from uuid import uuid4
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from adapters.public_tools import PublicTools  # noqa: E402
from adapters.governance import HostGovernance, governance_timeout  # noqa: E402

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


class McpSession:
    """One serialized transport owner; nested client requests never become replies."""

    def __init__(self, profile: str, incoming, send, *, timeout=None, tools=None):
        self.incoming, self.send = incoming, send
        self.timeout = governance_timeout() if timeout is None else timeout
        self.active_call_id = None
        self.mediator = HostGovernance(profile)
        self.tools = tools or PublicTools(profile, govern=self.mediator)
        self.initialized = False

    def process(self, message):
        if isinstance(message, dict) and message.get("method") == "initialize":
            # No capability upgrades while serving a dialog; initialize is only accepted once.
            if self.initialized:
                return _error(message.get("id"), -32600, "Already initialized")
            params = message.get("params", {})
            caps = params.get("capabilities", {}) if isinstance(params, dict) else {}
            elicitation = caps.get("elicitation") if isinstance(caps, dict) else None
            form_supported = isinstance(elicitation, dict) and (not elicitation or "form" in elicitation)
            response = handle_message(message, self.tools)
            if response and "result" in response:
                self.initialized = True
                self.mediator.elicit = self.elicit if form_supported else None
            return response
        # Unsolicited replies, including late dialog responses, are never new calls.
        if isinstance(message, dict) and "method" not in message:
            return None
        self.active_call_id = message.get("id") if isinstance(message, dict) and message.get("method") == "tools/call" else None
        try:
            return handle_message(message, self.tools)
        finally:
            self.active_call_id = None

    def elicit(self, message: str, schema: dict) -> dict | None:
        request_id = "empirica-dialog-" + uuid4().hex
        self.send({"jsonrpc": "2.0", "id": request_id, "method": "elicitation/create",
                   "params": {"mode": "form", "message": message, "requestedSchema": schema}})
        end = time.monotonic() + self.timeout
        while time.monotonic() < end:
            try:
                item = self.incoming.get(timeout=max(0, end - time.monotonic()))
            except queue.Empty:
                return None
            if item is None:
                return None
            if not isinstance(item, dict) or item.get("jsonrpc") != "2.0":
                continue
            if "method" in item:
                if (item.get("method") == "notifications/cancelled" and self.active_call_id is not None
                        and isinstance(item.get("params"), dict)
                        and type(item["params"].get("requestId")) is type(self.active_call_id)
                        and item["params"].get("requestId") == self.active_call_id):
                    return None
                if item.get("method") == "ping" and "id" in item:
                    self.send({"jsonrpc": "2.0", "id": item["id"], "result": {}})
                    continue
                if "id" in item:
                    self.send(_error(item["id"], -32000, "Governance dialog in progress; retry after completion"))
                continue
            if item.get("id") != request_id:
                continue
            if set(item) != {"jsonrpc", "id", "result"}:
                return None
            return item["result"] if isinstance(item["result"], dict) else None
        return None


def main() -> int:
    incoming = queue.Queue()

    def send(response):
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    def read_messages():
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except (TypeError, ValueError):
                message = {"jsonrpc": "2.0", "id": None, "method": "invalid"}
            incoming.put(message)
        incoming.put(None)

    try:
        session = McpSession(profile_from_environment(), incoming, send)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    threading.Thread(target=read_messages, daemon=True).start()
    while (message := incoming.get()) is not None:
        try:
            response = session.process(message)
        except Exception:
            response = _error(message.get("id") if isinstance(message, dict) else None,
                              -32603, "Internal error")
        if response is not None:
            send(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
