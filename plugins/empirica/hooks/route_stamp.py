#!/usr/bin/env python3
"""PreToolUse route stamp — records when investigation actually began (ADR-21 M1, ADR-20 P1).

P1 requires the run to classify its dependencies as known/unknown and announce the route
BEFORE gathering evidence. Prose cannot enforce an ordering, but a timestamp can *witness* one:
this hook stamps the first investigative tool call into the manifest, and the auditor compares
that stamp against the route announcement. A route announced after investigation was applied
retroactively to justify a shortcut — the exact inversion ADR-5/20 name.

TWO MODES:
  1. PreToolUse hook (no argv) — stamps the FIRST investigative tool call.
  2. `--announce-route --session <id>` (run from the skill) — stamps WHEN the run announced its
     route. This is the positive act that makes P1 checkable at all: a hook cannot read the
     model's prose, so the route announcement has to leave a mark on disk or there is nothing to
     compare against. `manifest.stamp_route` had no caller before this, which meant `route_ts`
     was permanently None and the P1 check could never fire (found by an independent doc audit).

The asymmetry that makes this worth having: `route_ts` is model-triggered (it could be called
late, or not at all) but `first_tool_ts` is HARNESS-written and first-write-wins. So a run that
investigates before announcing cannot hide it — the harness already recorded the earlier time.
A run that never announces has `route_ts` None, which the check reports as a violation too.

Contract:
  stdin  : JSON with at least {"tool_name": str, "cwd": str, "session_id": str} (hook mode)
  stdout : empty
  exit   : ALWAYS 0. This hook only observes. It must never deny a tool call — a stamp hook
           that could block would turn an observability feature into a way to wedge a session,
           and it fires on ordinary reads where the user expects no gate at all.

Investigative tools only: reading, searching, fetching, and running commands are how a run
gathers evidence. Writing/editing is not investigation, and spawning is charged elsewhere
(spawn_gate.py), so neither stamps.

Time (ADR-19's rule): hooks must not generate timestamps in a resumable run, so the stamp is
taken from the harness payload when it provides one and otherwise from a monotone counter
sourced from the manifest's own pass count — never datetime.now(). If no usable stamp exists,
the hook records nothing rather than inventing an ordering it cannot witness.
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


manifest = _load("manifest")

# Tools that constitute EVIDENCE GATHERING. Kept narrow and explicit: a tool absent from this
# set simply does not stamp, which fails toward "no violation detected" — the honest direction
# for a sensor that cannot see everything.
INVESTIGATIVE_TOOLS = frozenset({
    "Read", "Glob", "Grep", "Bash", "WebFetch", "WebSearch", "NotebookRead", "LSP",
})


def _timestamp(payload: dict) -> str | None:
    """A caller-supplied ordering stamp, or None. Prefers a real ISO timestamp from the
    harness; falls back to any monotone sequence field it provides. Never invents one."""
    for key in ("timestamp", "ts", "time", "event_ts"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"seq:{value}"
    return None


def announce_route(argv: list[str]) -> int:
    """`--announce-route --session <id> [--ts <stamp>] [--cwd <dir>]` — record that the run has
    announced its route (ADR-20 P1). Called by the skill at the end of Step 1, BEFORE any
    evidence gathering. First write wins, so a late second call cannot backdate the commitment.

    Deliberately NOT a hook: nothing in the harness can see a model announce a route in prose,
    so the announcement must be an explicit act. That makes it model-triggered and therefore
    skippable — but skipping it leaves `route_ts` None, which the P1 check reports as a violation,
    so omission is not a way to look compliant.
    """
    opts: dict = {}
    i = 0
    while i < len(argv):
        if argv[i] in ("--session", "--ts", "--cwd") and i + 1 < len(argv):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    session_id = opts.get("session")
    if not session_id:
        print("usage: route_stamp.py --announce-route --session <id> [--ts <stamp>] "
              "[--cwd <dir>]", file=sys.stderr)
        return 2
    cwd = Path(opts.get("cwd") or ".")
    run_path = manifest.locate_run(cwd, session_id)
    run = manifest.read_run(run_path)
    if not run or run.get("status") != "active":
        return 0  # not an active empirica run → nothing to stamp
    # The caller stamps the time (hooks never generate it — ADR-19). Absent a real timestamp,
    # fall back to a pass-relative marker, the same scheme the hook mode uses so the two are
    # comparable.
    ts = opts.get("ts") or f"pass:{run.get('passes', 0)}"
    try:
        manifest.stamp_route(run_path, ts)
    except OSError:
        return 0
    return 0


def main() -> int:
    if "--announce-route" in sys.argv[1:]:
        return announce_route(sys.argv[1:])
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("tool_name") not in INVESTIGATIVE_TOOLS:
        return 0

    # The run announcing its OWN route is not investigation — and stamping it made the P1 check
    # unable to pass. SKILL.md Step 1 has the agent announce by running this script as a Bash
    # command; PreToolUse fires "Before a tool call executes" (docs), so the hook saw the
    # announcement's own Bash call and claimed `first_tool_seq` before the announcement body could
    # claim `route_seq`. Both stamps are first-write-wins, so every compliant run was reported as
    # a P1 VIOLATION — verified live: first_tool_ts='pass:0'/seq=1 vs route_seq=2. A checker that
    # can never pass is the mirror of the vacuity stamps.py exists to remove.
    #
    # This is the same category of exclusion as Write/Edit above: self-observation is not evidence
    # gathering. Narrow on purpose — only a command carrying this script's own announcement flag is
    # skipped, so a genuine `grep`/`python3` call still stamps.
    command = (payload.get("tool_input") or {}).get("command")
    if isinstance(command, str) and "--announce-route" in command:
        return 0

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return 0  # no run identity → nothing to stamp

    cwd = Path(str(payload.get("cwd") or "."))
    run_path = manifest.locate_run(cwd, session_id)
    try:
        run = manifest.read_run(run_path)
    except OSError:
        return 0
    if not run or run.get("status") != "active":
        return 0  # not an active empirica run — observe nothing

    ts = _timestamp(payload)
    if ts is None:
        # No stamp available from the harness. Record the phase-relative fact instead: that
        # investigation happened during THIS pass. Coarse, but honest and comparable.
        ts = f"pass:{run.get('passes', 0)}"
    try:
        manifest.stamp_first_tool(run_path, ts)
    except OSError:
        pass  # observation is best-effort; never fail a user's tool call over a stamp
    return 0


if __name__ == "__main__":
    sys.exit(main())
