#!/usr/bin/env python3
"""Validate the deterministic Empirica Codex bundle."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "empirica"
EVENTS = {"UserPromptSubmit", "PreToolUse", "Stop", "SessionStart"}


def fail(message: str) -> None:
    print(f"  FAIL empirica Codex adapter: {message}", file=sys.stderr)
    raise SystemExit(1)


def static_validate() -> None:
    codex_manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())
    claude_manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    if codex_manifest.get("name") != "empirica":
        fail(".codex-plugin manifest name is not empirica")
    if codex_manifest.get("version") != claude_manifest.get("version"):
        fail("Codex and Claude manifests have different versions")
    if codex_manifest.get("hooks") != "./hooks/codex.json":
        fail("Codex manifest does not select its native hooks file")
    if codex_manifest.get("skills") != "./skills/":
        fail("Codex manifest does not package the shared skill")

    marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text())
    entries = {entry.get("name"): entry for entry in marketplace.get("plugins", [])}
    entry = entries.get("empirica")
    if not isinstance(entry, dict):
        fail("Codex marketplace does not list Empirica")
    if entry.get("source", {}).get("path") != "./plugins/empirica":
        fail("Codex marketplace Empirica source is wrong")

    hooks = json.loads((PLUGIN / "hooks" / "codex.json").read_text())
    if set(hooks.get("hooks", {})) != EVENTS:
        fail("Codex hook events differ from the reviewed lifecycle surface")
    pre = hooks["hooks"]["PreToolUse"]
    if {group.get("matcher") for group in pre} != {"Agent", "Bash"}:
        fail("PreToolUse must cover exactly Agent and Bash")
    for event, groups in hooks["hooks"].items():
        for group in groups:
            for handler in group.get("hooks", []):
                if set(handler) - {"type", "command", "timeout", "statusMessage",
                                   "additionalContextLimit", "async", "commandWindows"}:
                    fail(f"{event} handler contains a non-Codex 0.146.0 field")
                if handler.get("type") != "command":
                    fail(f"{event} is not a command hook")
                command = handler.get("command")
                if not isinstance(command, str) or "${PLUGIN_ROOT}" not in command:
                    fail(f"{event} command is not rooted in the installed plugin")
                if "CLAUDE_PLUGIN_ROOT" in command:
                    fail(f"{event} command uses the wrong plugin-root variable")

    forbidden = re.compile(r"(?:^|[/'\"`])\.codex(?:/|[\"'`])")
    for path in (PLUGIN / "adapters" / "codex").rglob("*.py"):
        if "tests" not in path.parts and forbidden.search(path.read_text()):
            fail(f"normal adapter code names a repository-local Codex state path: {path}")

    print("  ok: Codex manifest, hook schema, and isolated package layout")


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    static_validate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
