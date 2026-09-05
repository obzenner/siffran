#!/usr/bin/env python3
"""Validate the Empirica Codex bundle and optionally smoke the pinned live loader."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "empirica"
CODEX_VERSION = "codex-cli 0.146.0"
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

    test = subprocess.run(
        [sys.executable, str(PLUGIN / "adapters" / "codex" / "tests" /
                             "test_codex_adapter.py")],
        cwd=ROOT,
    )
    if test.returncode != 0:
        fail("payload/lifecycle tests failed")
    print("  ok: Codex manifest, hook schema, payloads, and isolated lifecycle")


def live_validate(command: str) -> None:
    argv = shlex.split(command)
    if not argv:
        fail("CODEX command is empty")
    version = subprocess.run(
        [*argv, "--version"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    if version != CODEX_VERSION:
        fail(f"expected {CODEX_VERSION!r}, got {version!r}")

    with tempfile.TemporaryDirectory(prefix="empirica-codex-home-") as tmp:
        env = {**os.environ, "CODEX_HOME": tmp}
        added = subprocess.run(
            [*argv, "plugin", "marketplace", "add", str(ROOT), "--json"],
            check=True, capture_output=True, text=True, env=env,
        )
        if "siffran" not in added.stdout:
            fail(f"live marketplace add returned an unexpected receipt: {added.stdout.strip()}")
        subprocess.run(
            [*argv, "plugin", "add", "empirica@siffran", "--json"],
            check=True, capture_output=True, text=True, env=env,
        )
        listed = subprocess.run(
            [*argv, "plugin", "list", "--json"],
            check=True, capture_output=True, text=True, env=env,
        )
        listing = json.loads(listed.stdout)
        if "empirica" not in json.dumps(listing):
            fail("live Codex loader did not list the installed Empirica bundle")
    print(f"  ok: {CODEX_VERSION} loaded the isolated Empirica marketplace and plugin")


def main() -> int:
    static_validate()
    if "--live" in sys.argv[1:]:
        live_validate(os.environ.get("CODEX", "codex"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
