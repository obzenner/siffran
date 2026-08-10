---
number: 28
title: "Set run modes from the invocation, parsed by the harness"
status: accepted
date: 2026-08-11
tags:
  - workflow
  - harness
  - usability
  - configuration
links:
  - target: 24
    kind: Amends
  - target: 19
    kind: Depends on
  - target: 21
    kind: relatesto
---

# Set run modes from the invocation, parsed by the harness

## Context and Problem Statement

ADR-24 §5 introduced two optional modes (`multi_provider`, `cli_exec`) with precedence
`environment → <run_dir>/modes.json → off`. That decided *where a mode is read from*. It never
decided *how a user sets one*, and the gap shows:

**Nothing in the workflow ever writes `modes.json`.** `modes.write()` exists, is atomic, and refuses
unknown keys — and has no caller outside the tests. So the only usable path today is an environment
variable, which is the worst of the three for the common case: it does not survive a new shell, it is
invisible to the run's own records, and a user who sets it in one terminal and invokes `/empirica` in
another gets baseline behaviour with no indication why.

The natural fix is to pass modes at invocation: `/empirica --cli-exec <goal>`. The question this
record answers is **who parses that flag**, and the answer is not obvious, because the intuitive one
is wrong.

**The agent cannot be the one to apply modes.** `dispatch_gate.py` reads
`modes.enabled(CLI_EXEC, run_dir)` inside a `PreToolUse` hook, and the doctor reads
`modes.state(run_dir)`. Both are separate OS processes that see only their stdin payload; neither can
read `$ARGUMENTS`, and neither can ask the agent. A mode held in the agent's context could therefore
never reach the code that consumes it. Worse, it would be a mode the agent could silently drop —
which inverts the property the whole plugin exists to provide: enforcement outside the model.

So the flag must be parsed by something that (a) sees the invocation and (b) can write to disk before
the agent's first turn. Exactly one component qualifies: the `run_start.py` `UserPromptExpansion`
hook.

**Verified, not assumed:** the real `UserPromptExpansion` payload carries `command_args`
(`"--cli-exec design X"`) and `prompt` (`"/empirica:empirica --cli-exec design X"`) alongside
`session_id` and `cwd`. This comes from a payload captured live in a session and pinned by the
existing run-start regression test — not from the hooks documentation, which does not specify the
event's field list.

## Decision Drivers

* **The consumer is a separate process.** Whatever sets a mode must leave it on disk.
* **The harness should see the invocation, not the agent's summary of it.** An agent that reports
  which modes it applied is an agent trusted to report on itself (ADR-24 finding 3, generalised).
* **Preserve ADR-24's precedence.** An operator forcing a mode from the environment must not be
  countermanded by whatever was typed.
* **A typo must be visible.** `modes.write()` already refuses unknown keys for this reason; the
  invocation path must not be laxer than the API it calls.
* **Baseline unchanged.** A run with no flags must behave exactly as before.

## Considered Options

* **A — Parse in `run_start.py` from `command_args`, persist via `modes.write`; the skill only reads
  and reports.**
* **B — The agent parses `$ARGUMENTS` and calls `modes.write` itself.**
* **C — A declared `arguments:` frontmatter list with `$cli_exec`-style named substitution.**
* **D — Leave it; document `EMPIRICA_MODE_*` better.**

## Decision Outcome

Chosen option: **A.**

`run_start.py` reads `command_args` (falling back to `prompt` with the command name stripped), calls
`modes.parse_flags`, and writes the result with `modes.write` — before the agent's first turn. The
flags land at the **file layer** of ADR-24's precedence, so `EMPIRICA_MODE_*` still wins.

```
/empirica --cli-exec design the retry policy
/empirica --cli-exec --multi-provider spike the parser
/empirica --no-cli-exec resume the audit      # force a mode OFF for one run
```

Three properties are load-bearing:

**Flags are read only in command position.** Parsing stops at the first non-flag token, so a goal
like `make --cli-exec the default` does not enable the mode. This is the identical bug
`dispatch_gate.py` already had to fix for actor dispatches, where a tool named in prose read as an
invocation of it. Getting it wrong here would mean the *subject* of a run silently changes the run's
configuration.

**The goal excludes the flags.** `strip_flags` exists because `$ARGUMENTS` still contains them, and a
claim graph rooted in `--cli-exec design X` has a corrupted intent that then becomes the root of
every claim in the run.

**An unrecognised flag is recorded, never dropped.** `run_start.py` cannot print — its contract is
silence and exit 0, because a run-start failure must never wedge a prompt — so a typo is written to
`modes.json` under a key outside the mode vocabulary and surfaced in the doctor's report. It enables
nothing (`_file_modes` reads only known modes with boolean values) but it is *visible*, because a
user who believes a run is in a mode it is not in will misread everything that follows.

`argument-hint` moves to **top-level** frontmatter, where it actually drives autocomplete; under
`metadata` it is arbitrary key-value data and the hint never renders, leaving the flags
undiscoverable. This makes the plugin unpackageable for claude.ai and the Skills API, which reject
non-spec keys with a hard error — an acceptable cost, and already unavoidable: empirica's enforcement
*is* its Python lifecycle hooks, and those do not run there at all.

**Why not B.** It puts the parse in the one place that cannot be checked. The agent would report
which modes it set, and no hook could verify that report against what the user typed — the same
structure as an actor reporting its own identity, which ADR-24 found a model gets wrong. It also
races: the agent's first turn happens after `run_start.py`, so a mode the agent writes arrives after
the doctor has already run its preflight.

**Why not C.** `arguments:` and `argument-hint` are Claude Code-only fields, and named substitution
would put the parse back in the agent's context anyway — B's problem with extra syntax. A top-level
`argument-hint` is the one Claude Code-only field worth paying for, because it is the discoverability
surface; `arguments:` buys nothing that `parse_flags` does not do better and testably.

**Why not D.** The environment is the right override channel and the wrong primary one. It survives
neither a new shell nor a resumed run, and it leaves no trace in the run's records, so the doctor
cannot report what a run was actually configured with.

### Consequences

* Good, because a mode is now set the way a user would expect, in the same breath as the goal, and
  the setting is recorded per run rather than living in a shell.
* Good, because the harness parses the invocation it actually received, so the modes a run believes
  it is in are the modes the user asked for — not the agent's account of them.
* Good, because `modes.write` finally has a real caller, so its unknown-key guard protects a live
  path instead of only tests.
* Good, because a typo is visible in the doctor's report rather than silently inert.
* Bad, because top-level `argument-hint` forecloses claude.ai/Skills-API packaging. Stated plainly
  rather than hidden: the hooks already foreclosed it, so this changes the failure from "loads but
  does nothing" to "refuses to package", which is the more honest of the two.
* Bad, because flag parsing now exists in two places conceptually — `parse_flags` for modes and
  `dispatch_gate`'s command-position logic for dispatches. They solve the same class of problem and
  could plausibly share code later; today they are small and independent enough that coupling them
  would cost more than it saves.
* Neutral, because nothing about enforceability changes. Modes still gate only what the hooks gate,
  and the hooks still read the file.

### Confirmation

Regression tests in `plugins/empirica/tests/test_hooks.py` (`make test`), each verified to fail when
its mechanism is reverted:

1. `parse_flags` handles both polarities, multiple flags, and empty input; `strip_flags` yields the
   goal (T100–T102).
2. A flag NAMED in the goal (`make --cli-exec the default`) enables nothing — the command-position
   property (T100, verified red when scoping is dropped).
3. An unknown flag is recorded, enables no mode, does not make the run look non-baseline, survives a
   later `modes.write`, and reaches the doctor's report (T103–T108).
4. End to end through the run-start hook as a subprocess with the real captured payload shape: a
   flag reaches `modes.json` as `run-config`, no-flags leaves baseline untouched, a typo is
   recorded, and **env still overrides a flag** (T109–T116).
5. `argument-hint` is top-level and advertises both flags (T117–T119, verified red when nested back
   under `metadata`).

## More Information

Prompted by the observation that modes were reachable only through environment variables, which is
the mechanism ADR-24 intended as the *override*, not the interface.

The `command_args` field is the load-bearing fact here and it is not documented: the hooks reference
does not specify `UserPromptExpansion`'s payload schema. It is known from a live capture pinned in the
suite. If a future Claude Code version drops the field, the `prompt` fallback covers it, and if both
vanish the parse yields no flags and the run is baseline — a degradation, never a wedge.

Test-caught during implementation, worth recording because it is the class of bug this project keeps
finding: `modes.write` rebuilt the file from `_file_modes`, which by design returns only known modes,
so writing a mode after a typo silently DROPPED the typo record. One reader (`_read_unknown`) is now
shared by all three functions that touch the file.
