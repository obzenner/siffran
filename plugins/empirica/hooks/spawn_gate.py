#!/usr/bin/env python3
"""PreToolUse spawn gate — HARNESS-ENFORCED spawn budget (ADR-17, corrected).

This is the piece that makes the budget real rather than advisory. It fires before
every subagent spawn and DENIES it when the spawn cap is reached. Verified against
code.claude.com/docs/en/hooks (2026-07-23): a PreToolUse hook with matcher "Agent"
intercepts subagent spawns and can deny them.

Contract:
  stdin  : JSON with at least {"tool_name": str, "cwd": str, ...}
  deny   → exit 2 with the reason on stderr (docs: exit 2 is the reliable hard block
           for policy enforcement; more reliable than a JSON `deny` field).
  allow  → exit 0. Every allowed spawn reserves one slot in the ledger (the count is
           ground truth because this hook fires exactly once per spawn).

Why this gates on SPAWNS: actual token spend is not readable mid-session (see
budget.py), but spawns are both countable (fire-once-per-spawn) and denyable here.
Spawns are the dominant cost unit, so a spawn cap is the enforceable budget.

Fail-open by design:
  - not an Agent spawn         → allow (we only gate subagent spawns)
  - no ledger / unbounded cap  → allow (no budget set for this run)
  - malformed stdin            → allow (never wedge an unrelated session)
Only an explicit finite cap that is already reached denies.
"""
import importlib.util
import json
import sys
from pathlib import Path


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


budget = _load("budget")


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return 0  # malformed input — do not wedge the session

    # Only gate subagent spawns. "Task" is a legacy alias for the Agent tool.
    if payload.get("tool_name") not in ("Agent", "Task"):
        return 0

    cwd = Path(str(payload.get("cwd") or "."))
    ledger_path = budget.locate_ledger(cwd)

    # Cheap fast-path: if no ledger file exists at all, this run has no budget — allow
    # without taking a lock or creating scratch dirs. This is a performance skip only; it
    # is safe because a run only becomes budgeted once a ledger is written, and the
    # authoritative decision below still happens under the lock (no allow/deny is made
    # from this unlocked read — review 2.5 TOCTOU is about the finite→reserve path, which
    # stays fully locked in reserve_spawn).
    if not ledger_path.exists():
        return 0

    allowed, ledger = budget.reserve_spawn(ledger_path)
    if allowed:
        return 0

    cap = ledger.get("max_spawns")
    print(
        f"empirica spawn budget exhausted: {ledger.get('spawns', cap)}/{cap} subagent "
        f"spawns used. This spawn is DENIED. Resolve remaining unknowns without spawning, "
        f"mark them `<!-- confidence: N, blocked: needs-budget -->` (ADR-17), or raise "
        f"max_spawns in {ledger_path}.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
