#!/usr/bin/env python3
"""Static guard for the final Claude adapter activation."""
from __future__ import annotations

import json
import re
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
    "agent_failure.py": "agent_failure_main",
    "subagent_start.py": "subagent_start_main",
    "subagent_stop.py": "subagent_stop_main",
}
FORBIDDEN = re.compile(r"(?:^|[/'\"`])\.(?:claude|pi)(?:/|[\"'`])")
SKILL = ROOT / "skills/empirica/SKILL.md"
REQUIRED_SKILL_REFERENCES = {
    "governance.md",
    "host-capabilities.md",
    "claim-graph.md",
    "evidence.md",
    "budget-freeze.md",
    "audit.md",
    "handoff.md",
}
STALE_SKILL_TERMS = {"spike_harness.py", "EMPIRICA_STALL_DEADLINE_SEC"}


def fail(message: str) -> None:
    print(f"FAIL activation: {message}", file=sys.stderr)
    raise SystemExit(1)


def normal_runtime_files() -> list[Path]:
    files = []
    for base in (ROOT / "core", ROOT / "application", ROOT / "adapters"):
        for path in base.rglob("*.py"):
            if "tests" in path.parts or "quarantine" in path.parts:
                continue
            files.append(path)
    files.extend(HOOKS / name for name in ENTRYPOINTS)
    return files


def main() -> int:
    quarantine = ROOT / "quarantine"
    if quarantine.exists():
        fail(f"retired duplicate authority still exists: {quarantine}")
    # Codex has its own command-string hook schema and one thin multiplexer. Claude hooks are
    # constrained below to registered thin entrypoints.
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
    # Validate the invariant (only registered thin entrypoints), rather than freezing hook config.
    if set(hooks["hooks"]) != {"UserPromptExpansion", "PreToolUse", "PostToolUseFailure",
                                  "Stop", "SubagentStart", "SubagentStop", "SessionStart", "PostModelSwitch"}:
        fail("hooks.json lifecycle events changed")
    for groups in hooks["hooks"].values():
        for group in groups:
            for hook in group.get("hooks", []):
                parts = [hook.get("command"), *hook.get("args", [])]
                names = [match.group(1) for part in parts if isinstance(part, str)
                         for match in [re.search(r"hooks/([^/]+\.py)", part)] if match]
                if len(names) != 1 or names[0] not in ENTRYPOINTS:
                    fail(f"hooks.json bypasses a registered thin entrypoint: {hook}")

    skill_text = SKILL.read_text(encoding="utf-8")
    if len(skill_text.splitlines()) > 300:
        fail("SKILL.md exceeds the 300-line progressive-disclosure budget")
    if len(skill_text.split()) > 3500:
        fail("SKILL.md exceeds the conservative 3,500-word context budget")
    references = SKILL.parent / "references"
    for name in REQUIRED_SKILL_REFERENCES:
        if not (references / name).is_file():
            fail(f"required progressive-disclosure reference is missing: {name}")
        if f"references/{name}" not in skill_text:
            fail(f"SKILL.md does not route to reference: {name}")
    for term in STALE_SKILL_TERMS:
        if term in skill_text:
            fail(f"SKILL.md retains stale runtime term: {term}")
    for required in (
        ">=2.1.278,<2.2.0", ">=0.84.1,<0.85.0", "pi-subagents@0.50.0",
        ">=0.146.0,<0.147.0", "empirica_observe", "empirica_read", "report_convergence",
    ):
        if required not in skill_text:
            fail(f"SKILL.md omits complete host/tool disclosure: {required}")

    for path in [SKILL, *sorted((ROOT / "agents").glob("**/*.md"))]:
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

    print(f"ok: {len(normal_runtime_files())} runtime files and {len(ENTRYPOINTS)} thin hooks enforce activation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
