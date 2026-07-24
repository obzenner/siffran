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

It has a SECOND job (ADR-20 P6, ADR-21 M3): when the spawned subagent is the independent
auditor, it records a spawn TICKET into the run directory. That ticket is the harness-proven
half of the audit gate — because this hook is an external process the model cannot skip, a run
that never spawned an auditor can never produce a ticket, and the Stop gate refuses `converged`
without one. The auditor is charged to the spawn ledger like any other spawn, which is exactly
where ADR-17's budget and ADR-23's role routing meet.

Fail-open by design:
  - not an Agent spawn         → allow (we only gate subagent spawns)
  - no ledger / unbounded cap  → allow (no budget set for this run)
  - malformed stdin            → allow (never wedge an unrelated session)
Only an explicit finite cap that is already reached denies. Ticket-writing is best-effort and
never denies a spawn: a ticket that cannot be written simply means the Stop gate will not see
an audit, which fails CLOSED later rather than wedging the spawn now.
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
manifest = _load("manifest")
audit = _load("audit")


def _record_auditor_spawn(payload: dict, cwd: Path) -> None:
    """If this spawn is the auditor, issue its ticket. Best-effort by design (see module doc).

    The subagent's identity is read from the tool input, whose exact field name varies by
    harness version, so every plausible spelling is checked — an auditor spawn that went
    unrecognised would silently make convergence unreachable, which is a worse failure than
    checking a few keys.
    """
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    candidates = [tool_input.get(k) for k in
                  ("subagent_type", "subagentType", "agent_type", "agentType", "name")]
    if not any(isinstance(c, str) and audit.AUDITOR_AGENT in c for c in candidates):
        return
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return
    run_path = manifest.locate_run(cwd, session_id)
    run = manifest.read_run(run_path)
    if not run or run.get("status") != "active":
        return  # only an active empirica run has an audit to gate
    try:
        audit.record_spawn(run_path.parent, run.get("run_id") or session_id,
                           run.get("passes", 0))
    except OSError:
        pass  # see module doc: never deny a spawn because a ticket could not be written


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
    _record_auditor_spawn(payload, cwd)
    # Key the ledger to THIS run. Omitting run_id fell back to a shared `default/` directory,
    # so a `max_spawns` written where the docs say to write it (the run dir) was never read and
    # the cap silently did not apply — the very "shared-default-ledger" bug ADR-19 fixed in the
    # signature but not at the call sites (found by an independent doc audit).
    session_id = payload.get("session_id")
    run_id = (manifest.run_id(session_id, cwd)
              if isinstance(session_id, str) and session_id else None)
    ledger_path = budget.locate_ledger(cwd, run_id)

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
        f"mark them `\"blocked\": \"needs-budget\"` in the claim graph (ADR-17), or raise "
        f"max_spawns in {ledger_path}.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
