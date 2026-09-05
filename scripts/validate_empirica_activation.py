#!/usr/bin/env python3
"""Static guard for the final Claude adapter activation."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path("plugins/empirica")
HOOKS = ROOT / "hooks"
ENTRYPOINTS = {
    "run_start.py": "run_start_main",
    "spawn_gate.py": "spawn_main",
    "route_stamp.py": "route_main",
    "dispatch_gate.py": "dispatch_main",
    "convergence_gate.py": "completion_main",
    "state_restore.py": "restore_main",
}
FORBIDDEN = re.compile(r"(?:^|[/'\"`])\.(?:claude|pi)(?:/|[\"'`])")


def fail(message: str) -> None:
    print(f"FAIL activation: {message}", file=sys.stderr)
    raise SystemExit(1)


def normal_runtime_files() -> list[Path]:
    files = []
    for base in (ROOT / "core", ROOT / "application", ROOT / "adapters"):
        for path in base.rglob("*.py"):
            if "tests" in path.parts or "quarantine" in path.parts:
                continue
            if path.name == "migrate_legacy.py":
                continue
            files.append(path)
    files.extend(HOOKS / name for name in ENTRYPOINTS)
    return files


def main() -> int:
    quarantine = ROOT / "quarantine"
    if quarantine.exists():
        fail(f"retired duplicate authority still exists: {quarantine}")
    # Codex has its own command-string hook schema and one thin multiplexer. Claude's
    # hooks.json remains byte-for-byte frozen so adding that adapter cannot change this one.
    allowed_hooks = {"hooks.json", "codex.json", "codex_hook.py", *ENTRYPOINTS}
    extra_hooks = sorted(path.name for path in HOOKS.iterdir()
                         if path.is_file() and path.name not in allowed_hooks)
    if extra_hooks:
        fail(f"duplicate hook authority remains: {extra_hooks}")

    for path in normal_runtime_files():
        text = path.read_text(encoding="utf-8")
        if FORBIDDEN.search(text):
            fail(f"normal runtime contains a forbidden host-state path: {path}")
        if "quarantine" in text or "legacy-hooks" in text:
            fail(f"normal runtime can reach the legacy quarantine: {path}")

    for name, function in ENTRYPOINTS.items():
        path = HOOKS / name
        text = path.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) > 12 or f"import {function}" not in text:
            fail(f"{path} is not a thin lifecycle entrypoint")
        if "adapters.claude.lifecycle" not in text:
            fail(f"{path} bypasses the Claude lifecycle adapter")

    hooks = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    expected = subprocess.run(
        ["git", "show", "HEAD:plugins/empirica/hooks/hooks.json"],
        check=True, capture_output=True, text=True,
    ).stdout
    if (HOOKS / "hooks.json").read_text(encoding="utf-8") != expected:
        fail("hooks.json changed from HEAD")
    if set(hooks["hooks"]) != {"UserPromptExpansion", "PreToolUse", "Stop", "SessionStart"}:
        fail("hooks.json lifecycle events changed")

    for path in [ROOT / "skills/empirica/SKILL.md", *sorted((ROOT / "agents").glob("*.md"))]:
        text = path.read_text(encoding="utf-8")
        if "~/.empirica-plugin" not in text or "refs/empirica" not in text:
            fail(f"instruction omits authoritative storage locations: {path}")
        matches = [line for line in text.splitlines() if FORBIDDEN.search(line)]
        if matches:
            # Instructions may name forbidden locations only in an explicit prohibition.
            bad = [line for line in matches
                   if not any(word in line.lower() for word in ("never", "forbid", "do not"))]
            if bad:
                fail(f"instruction contains a non-prohibitive legacy path reference: {path}")

    print(f"ok: {len(normal_runtime_files())} runtime files and six thin hooks enforce activation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
