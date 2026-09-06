#!/usr/bin/env python3
"""Deterministically validate the Methodologist Codex package and bridge."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "methodologist"
CODEX_MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
CLAUDE_MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"
PI_MANIFEST = PLUGIN / "package.json"
MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"
MCP_CONFIG = PLUGIN / ".mcp.json"
MCP_SERVER = PLUGIN / "adapters" / "codex" / "mcp_server.py"
SHARED_SKILL = PLUGIN / "skills" / "think" / "SKILL.md"
OPENAI_METADATA = PLUGIN / "skills" / "think" / "agents" / "openai.yaml"


def _load_json(path: Path, errors: list[str]) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.relative_to(ROOT)}: expected a JSON object")
        return {}
    return value


def _check_manifest(errors: list[str]) -> None:
    codex = _load_json(CODEX_MANIFEST, errors)
    claude = _load_json(CLAUDE_MANIFEST, errors)
    pi = _load_json(PI_MANIFEST, errors)
    required = ("name", "version", "description", "author", "interface")
    for field in required:
        if not codex.get(field):
            errors.append(f"Codex manifest is missing {field!r}")
    if codex.get("name") != PLUGIN.name:
        errors.append("Codex manifest name must match the plugin directory")
    versions = {codex.get("version"), claude.get("version"), pi.get("version")}
    if len(versions) != 1:
        errors.append("Codex, Claude, and Pi Methodologist versions must match")
    if codex.get("skills") != "./skills/":
        errors.append("Codex manifest must expose the existing ./skills/ tree")
    if codex.get("mcpServers") != "./.mcp.json":
        errors.append("Codex manifest must declare the bundled MCP bridge")
    if "hooks" in codex or (PLUGIN / "hooks").exists():
        errors.append("Methodologist Codex packaging must not include hooks")
    if not SHARED_SKILL.is_file():
        errors.append("shared think skill is missing")


def _check_marketplace(errors: list[str]) -> None:
    marketplace = _load_json(MARKETPLACE, errors)
    entries = marketplace.get("plugins")
    if not isinstance(entries, list):
        errors.append("Codex marketplace plugins must be an array")
        return
    matching = [
        entry for entry in entries
        if isinstance(entry, dict) and entry.get("name") == "methodologist"
    ]
    if len(matching) != 1:
        errors.append("Codex marketplace must expose Methodologist exactly once")
        return
    entry = matching[0]
    source = entry.get("source")
    expected_source = {"source": "local", "path": "./plugins/methodologist"}
    if source != expected_source:
        errors.append(f"Methodologist Codex source must be {expected_source!r}")
    policy = entry.get("policy")
    expected_policy = {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}
    if policy != expected_policy:
        errors.append(f"Methodologist Codex policy must be {expected_policy!r}")
    if entry.get("category") != "Productivity":
        errors.append("Methodologist Codex marketplace category must be Productivity")


def _check_mcp_config(errors: list[str]) -> None:
    config = _load_json(MCP_CONFIG, errors)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict) or set(servers) != {"methodologist"}:
        errors.append(".mcp.json must declare exactly the methodologist server")
        return
    server = servers["methodologist"]
    # The same .mcp.json is consumed by two harnesses that resolve paths
    # differently: Codex launches with cwd == plugin root, while Claude Code
    # launches in the user's session dir and only exports CLAUDE_PLUGIN_ROOT.
    # A static path satisfies neither both — so args is a `python3 -c` bootstrap
    # that resolves mcp_server.py from CLAUDE_PLUGIN_ROOT (Claude) with a cwd
    # fallback (Codex). Assert those portability properties, not exact bytes;
    # `_check_stdio_bridge` covers actual behaviour by running the server.
    if server.get("type") != "stdio" or server.get("command") != "python3":
        errors.append("methodologist MCP server must be a stdio python3 server")
    if server.get("startup_timeout_sec") != 10 or server.get("tool_timeout_sec") != 10:
        errors.append("methodologist MCP server must keep the 10s timeouts")
    if "cwd" in server:
        errors.append("methodologist MCP server must not pin cwd (breaks Claude Code)")
    args = server.get("args")
    boot = args[1] if isinstance(args, list) and len(args) == 2 and args[0] == "-c" else ""
    if not boot:
        errors.append("methodologist MCP args must be a ['-c', <bootstrap>] pair")
    else:
        for needle in ("CLAUDE_PLUGIN_ROOT", "adapters/codex/mcp_server.py", "or '.'"):
            if needle not in boot:
                errors.append(f"methodologist MCP bootstrap must reference {needle!r}")
    if not MCP_SERVER.is_file():
        errors.append("Codex MCP server entry point is missing")


def _check_implicit_activation(errors: list[str]) -> None:
    try:
        metadata = OPENAI_METADATA.read_text()
    except OSError as exc:
        errors.append(f"{OPENAI_METADATA.relative_to(ROOT)}: {exc}")
        return
    if "allow_implicit_invocation: true" not in metadata:
        errors.append("Codex skill metadata must allow implicit invocation")
    skill = SHARED_SKILL.read_text()
    if "Codex activates this skill" not in skill or "Native simple mode" not in skill:
        errors.append("shared think skill must define Codex native simple mode")


def _check_stdio_bridge(errors: list[str]) -> None:
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "methodologist_select",
                "arguments": {
                    "methodology": "formal-reasoning",
                    "reason": "deterministic Codex package validation",
                },
            },
        },
    ]
    try:
        completed = subprocess.run(
            [sys.executable, str(MCP_SERVER)],
            input="".join(json.dumps(item) + "\n" for item in messages),
            text=True,
            capture_output=True,
            cwd=PLUGIN,
            timeout=10,
            check=True,
        )
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        errors.append(f"Codex MCP stdio smoke failed: {exc}")
        return
    if [item.get("id") for item in responses] != [1, 2, 3]:
        errors.append("Codex MCP server did not preserve JSON-RPC request ids")
        return
    tools = responses[1].get("result", {}).get("tools", [])
    if [tool.get("name") for tool in tools] != ["methodologist_select"]:
        errors.append("Codex MCP server did not discover methodologist_select")
    result = responses[2].get("result", {})
    selected = result.get("structuredContent", {})
    if result.get("isError") or selected.get("methodology") != "formal-reasoning":
        errors.append("Codex MCP tool did not invoke the host-neutral bridge")
    if len(selected.get("phases", [])) != 6:
        errors.append("Codex MCP bridge did not return six canonical phases")


def main() -> int:
    errors: list[str] = []
    _check_manifest(errors)
    _check_marketplace(errors)
    _check_mcp_config(errors)
    _check_implicit_activation(errors)
    _check_stdio_bridge(errors)
    if errors:
        for error in errors:
            print(f"  FAIL {error}", file=sys.stderr)
        return 1
    print("  ok: Methodologist Codex package, implicit skill, and MCP bridge are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
