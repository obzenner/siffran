#!/usr/bin/env python3
"""PreToolUse:Bash gate — the spawn budget survives CLI-exec dispatch (ADR-24 §5B, ADR-17).

THE PROBLEM THIS SOLVES. ADR-17 makes the spawn budget real by denying at a boundary the model
cannot cross: `spawn_gate.py` fires on the `Agent` tool and exits 2. Mode B dispatches actors as
Bash subprocesses instead — `claude -p`, `codex exec`, `pi -p` — and a Bash call is not an Agent
spawn, so ADR-24 originally recorded Mode B as trading the enforced budget for convention.

V4 showed that objection is narrower than it looked, and this module is the payoff: a `PreToolUse`
hook matched on `Bash` receives the payload, can deny by exit 2, and can charge the SAME
`budget.py` ledger. So a CLI-dispatched actor is gated at the same boundary as an `Agent` spawn.
Mode B keeps the guarantee.

THE RESIDUAL COST, stated plainly because ADR-24's Consequences section commits to stating it at
the point of implementation: this gate must recognise WHICH Bash commands are actor dispatches, so
coverage rests on a command test rather than on a tool name. A dispatch spelled unusually — an
alias, a wrapper script, a here-doc — slips past. That is strictly weaker than `Agent`, where the
tool name IS the signal. It is a real limitation, and it is not the loss of an enforced boundary:
what remains enforced is that every RECOGNISED dispatch is counted and deniable.

DESIGN CHOICES, each closing a way this could do harm:

  * INERT WITH MODE B OFF. Baseline runs must behave exactly as 0.4.x does, and this hook fires on
    every Bash call a user makes. With Mode B off it returns immediately — no lock, no ledger, no
    classification. This is the single most important property here: an over-eager dispatch gate
    would deny ordinary shell commands, which is far worse than a missed dispatch.
  * MATCH ON THE ACTOR CLI PLUS AN EXEC FLAG, NOT ON THE CLI ALONE. `codex --version` is not a
    dispatch; `codex exec "..."` is. Matching a bare binary name would charge the budget for a
    version check — and would charge the DOCTOR's own probes, making the preflight cost spawns.
  * ONE LEDGER. It calls `budget.reserve_spawn` on the path `spawn_gate.py` uses, so a run's cap
    covers Agent spawns and CLI dispatches together. Two ledgers would mean a cap of 6 permitted
    12 actors.
  * FAIL OPEN ON EVERYTHING ELSE. Malformed payload, no session, no manifest, inactive run, no
    ledger → allow. A gate that wedges a session over its own confusion is the failure ADR-19
    forbids outright.
"""
import importlib.util
import json
import re
import shlex
import sys
from pathlib import Path


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


budget = _load("budget")
manifest = _load("manifest")
modes = _load("modes")
actors = _load("actors")

# An actor dispatch = an actor CLI invoked with a flag/subcommand that makes it RUN A MODEL
# non-interactively. Both halves are required, so `--version`, `--help`, and `codex doctor` (the
# doctor's own probes) are not dispatches and cost nothing.
DISPATCH_SIGNATURES = {
    "claude": ("-p", "--print"),
    "codex": ("exec",),
    "pi": ("-p", "--print", "--mode"),
}
# Shell operators that separate one command from the next. A compound command must be split on
# these before classifying, or `echo hi && codex exec ...` would read as an `echo` call and the
# dispatch inside it would go uncounted.
_SEPARATORS = re.compile(r"\|\||&&|;|\||\n")


def dispatched_harness(command: str) -> str | None:
    """Which actor CLI this Bash command invokes to run a model, or None.

    ONE scan, used by both `is_dispatch` and the §6 advice — an earlier version duplicated the loop,
    which meant a fix to the parsing had to be made twice and a sabotage of one copy left the other
    intact. Same class of drift the P1 ordering logic had before it was consolidated into stamps.py.

    Splits compound commands, so a dispatch hidden after `&&` is still seen — an audit found that
    scanning the whole string only worked when no EARLIER actor-CLI token hit the `break` below, so
    `codex --version && pi -p 'x'` read as not-a-dispatch and went uncounted against the ADR-17
    ledger. Uses shlex so quoting does not confuse the token scan, falling back to a whitespace split
    rather than to nothing, because "I could not parse it" must not become "it is not a dispatch".
    """
    if not isinstance(command, str) or not command.strip():
        return None
    for segment in _SEPARATORS.split(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        if not tokens:
            continue
        # The binary may be reached by path (`/usr/local/bin/codex`) or prefixed by an env
        # assignment or `env`; scan for the first token that names a known actor CLI.
        for index, token in enumerate(tokens):
            name = Path(token).name
            if name in DISPATCH_SIGNATURES:
                if any(flag in tokens[index + 1:] for flag in DISPATCH_SIGNATURES[name]):
                    return name
                break  # this segment invokes an actor CLI but not to run a model
    return None


def is_dispatch(command: str) -> bool:
    """Does this Bash command invoke an actor CLI to run a model? (the gate's predicate)"""
    return dispatched_harness(command) is not None


def session_flag_for(run_id: str, claim_id: str, harness: str) -> list[str]:
    """The argv fragment that pins a dispatch to this claim's own session (ADR-24 §6).

    ONE implementation of the derived-session rule, used by `advice_for` below rather than left as a
    helper nobody calls. Two independent audits flagged the alternative — a derivation with no
    production caller — as schema-without-wiring, and the second was right that adding a link to a
    chain that still ends in dead code fixes nothing.

    The three CLIs spell it differently, all verified: `claude --session-id` REQUIRES a valid UUID;
    `pi --session-id` takes an arbitrary string and creates the session if missing; codex resumes by
    `codex exec resume <id>`. uuid5 satisfies all three at once, which is why §6 derives a UUID
    rather than a shorter token.
    """
    sid = actors.session_id_for(run_id, claim_id)
    if harness == "codex":
        # A resume is a subcommand rather than a flag, so the caller places it after `exec`.
        return ["resume", sid]
    return ["--session-id", sid]


_SESSION_FLAGS = ("--session-id", "resume", "--resume", "--fork-session")


def advice_for(command: str, run_id: str) -> str | None:
    """A note for a dispatch that pins no session, or None when it does (ADR-24 §6).

    This is where §6 becomes behaviour rather than a schema. The gate sees every dispatch, so it is
    the one place that can notice a dispatch starting COLD — which silently discards the context the
    per-claim session exists to preserve, and would leave an auditor re-examining a claim at pass 3
    with none of passes 1-2.

    ADVICE, on the ALLOW path. It cannot know which claim a dispatch is for (the command is opaque),
    so it must not deny: refusing a dispatch over a convention the gate cannot verify would be the
    P1 mistake again — a checker that punishes what it cannot actually see.
    """
    harness = dispatched_harness(command)
    if harness is None or any(flag in (command or "") for flag in _SESSION_FLAGS):
        return None
    example = " ".join(session_flag_for(run_id, "<claim-id>", harness))
    return (f"empirica: this `{harness}` dispatch pins no session, so it starts cold and loses the "
            f"per-claim context ADR-24 §6 exists to preserve. Derive one per (run, claim) and pass "
            f"it — for this run: `{example}` with <claim-id> replaced by the claim being resolved.")


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return 0  # malformed input — never wedge the session

    if payload.get("tool_name") != "Bash":
        return 0

    cwd = Path(str(payload.get("cwd") or "."))
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return 0  # no run identity → nothing to charge

    run_path = manifest.locate_run(cwd, session_id)
    run = manifest.read_run(run_path)
    if not run or run.get("status") != "active":
        return 0  # not an active empirica run

    run_dir = run_path.parent
    # THE INERT BRANCH. Checked before any classification so a baseline run pays nothing at all
    # and cannot possibly be denied by this hook.
    if not modes.enabled(modes.CLI_EXEC, run_dir):
        return 0

    command = (payload.get("tool_input") or {}).get("command")
    if not is_dispatch(command):
        return 0  # ordinary Bash — allow, and do not charge the budget

    # §6 advice, emitted on the ALLOW path before the budget decision so it is seen whether or not
    # a cap exists. stderr, because that is the channel the harness surfaces to the agent.
    advice = advice_for(command, run.get("run_id") or session_id)

    ledger_path = budget.locate_ledger(cwd, run.get("run_id"))
    if not ledger_path.exists():
        if advice:
            print(advice, file=sys.stderr)
        return 0  # no budget set for this run (same fail-open contract as spawn_gate.py)

    allowed, ledger = budget.reserve_spawn(ledger_path)
    if allowed:
        if advice:
            print(advice, file=sys.stderr)
        return 0

    cap = ledger.get("max_spawns")
    print(
        f"empirica spawn budget exhausted: {ledger.get('spawns', cap)}/{cap} actor dispatches "
        f"used. This CLI-exec dispatch is DENIED (ADR-24 §5B — a dispatched actor is charged to "
        f"the same ADR-17 ledger as an `Agent` spawn). Resolve remaining unknowns without "
        f"dispatching, mark them `\"blocked\": \"needs-budget\"` in the claim graph, or raise "
        f"max_spawns in {ledger_path}.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
