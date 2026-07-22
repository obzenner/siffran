---
number: 16
title: "Implementation plan: how the workflow binds to the siffran repo"
status: accepted
date: 2026-07-22
tags:
  - architecture
  - implementation
  - build
links:
  - target: 3
    kind: Depends on
  - target: 4
    kind: Depends on
  - target: 8
    kind: Depends on
  - target: 14
    kind: Depends on
  - target: 15
    kind: Depends on
  - target: 13
    kind: relatesto
---

# Implementation plan: how the workflow binds to the siffran repo

## Context and Problem Statement

ADRs 1–15 fix the design (what/why). This ADR fixes the project-level build decisions: what
the plugin is called, where its files live, what language the hooks are written in, how the
confidence-scored unknown attaches to the spec, where transient scratch goes, how it is
versioned and registered, and in what order it is built. It is the single implementation
record for binding the design to this specific repo, grounded in the repo's existing
conventions rather than invented.

## Decision Drivers

* Conform to siffran's established layout and tooling (evidence: `plugins/methodologist/`).
* Some choices (hook runtime specifics, confidence-in-spec parsing) touch current Claude
  Code behaviour — where training data is stale, prefer a spike over a paper decision.
* Honour the design ADRs: self-contained but methodologist-companion (ADR-3), hook-enforced
  (ADR-8), transient vs committable (ADR-14), spec-kit by reference (ADR-15).
* Ship a thin usable slice before the heavy engine (ship-or-kill gate).

## Considered Options

Per axis, the alternatives considered and the choice:

* **Plugin identity** — new plugin vs. a skill inside methodologist. → **New plugin**,
  because it ships hooks and its own lifecycle; methodologist stays a pure reasoning router.
* **Hook script language** — bash vs. Python vs. node. → **Python**, matching the repo's
  existing tooling (`plugins/methodologist/scripts/validate.py`); bash rejected as too
  brittle for JSON ledger parsing, node rejected as a new runtime the repo doesn't use.
  (Marked to confirm against the actual hook I/O contract in the first spike — see Drivers.)
* **Confidence-in-spec representation** — separate JSON store vs. inline in `spec.md`. →
  **Inline in the living spec** (ADR-15): each unknown is a checkbox item tagged with a
  confidence value in a machine-parseable convention (e.g. an HTML-comment or a fixed
  suffix), parsed by the Python hook. Exact syntax proven in the spike, not guessed here.
* **Transient scratch location** — new dir vs. reuse `.claude/`. → **`.claude/` scratch**,
  which the repo's `.gitignore` already excludes (`\.claude/*`), so ADR-14's transient tier
  needs no new ignore rule.

## Decision Outcome

Chosen implementation plan:

**Plugin.** New plugin `plugins/empirica/` (name finalized at build → `empirica`,
invoked `/empirica`) with:
- `.claude-plugin/plugin.json` — semver starting `0.1.0`, author `obzenner` (repo convention).
- `skills/<skill>/SKILL.md` — the workflow skill body (stance declaration + the loop).
- `hooks/hooks.json` — the Stop-hook gate and `SessionStart:compact` re-injection (ADR-8),
  scripts referenced via `${CLAUDE_PLUGIN_ROOT}`.
- `hooks/*.py` — Python hook scripts (the convergence gate reads the spec's unknowns,
  computes `converged()`, blocks the Stop while any unknown < θ).
- Registered in `.claude-plugin/marketplace.json`; the generated tables in `CLAUDE.md` /
  `README.md` regenerated via the `checkup` skill (repo convention — do not hand-edit).

**Dependencies.** methodologist declared a required companion (ADR-3, ADR-12); inkrot `/hr`
never referenced (M2 staffing is inline). spec-kit read by pinned GitHub reference at use
time, never vendored (ADR-15).

**State.** Unknowns + confidence live inline in `spec.md` (ADR-15); transient scratch
(spike output, `/think` traces, race bookkeeping) under `.claude/` scratch, git-ignored
(ADR-14). Committable outputs: spec/research/data-model/contracts/plan/tasks + MADR ADRs.

**deep-planner.** Removed from `marketplace.json` and `plugins/` when this plugin ships
(ADR-4), in the same PR as the replacement.

**Build order (ship-or-kill).**
1. **Spike M3 SpikeHarness + one hook** against a fixture repo — prove (a) a Python Stop
   hook can read a file and block completion, and (b) M3 yields a real `gate: pass|fail`
   from an actual deterministic check. This retires the two stalest assumptions first.
   **DONE** — spike at `.claude/spike-m3`, 15/15 checks; both assumptions confirmed. It
   also refuted the Stop-block mechanism assumed in ADR-8's first draft (exit 2 + stderr is
   honored; a stdout `decision` field is not) — ADR-8 corrected accordingly.
2. Slice 1 — known path (M1 route → M5 `/think` finalize → M7 handoff): a usable skill with
   no engine. **DONE** — `plugins/empirica/` shipped with `SKILL.md` (full loop authored),
   `hooks/` (convergence_gate, spike_harness, state_restore), `hooks.json`; deep-planner
   removed; tables regenerated via `checkup`; `claude plugin validate` passes.
3. Slice 2 — empirical engine (M2 staff, M3/M4 spike+race, M9 assess, hooks, M6 scribe):
   the SKILL.md describes all of these; the remaining build is exercising them on a real
   task and hardening race/staffing beyond the skill's prose instructions.
4. Remove deep-planner; regenerate tables via `checkup`. **DONE** (folded into step 2's PR).

### Consequences

* Good, because every file placement, language, and registration step follows an existing
  repo pattern — no novel project structure to justify.
* Good, because the two training-data-sensitive choices (hook runtime, confidence syntax)
  are explicitly gated on a spike, not asserted.
* Good, because `.claude/` is already git-ignored, so the transient tier is free.
* Good, because Slice 1 ships value before the engine exists.
* Resolved (was: "Python hooks and confidence syntax are provisional"): the build-step-1
  spike confirmed both — Python hooks fit the I/O contract and the inline
  `<!-- confidence: N -->` convention parses cleanly (15/15 checks). The spike did refute
  the assumed Stop-block *mechanism* (JSON `decision` field → actually exit 2 + stderr),
  which is now corrected in ADR-8. The spike was the fitness function and it fired.
* Bad, because this ADR overlaps `plan.md` (ADR-15) by intent — recorded as a decision here,
  it will be mirrored into the per-feature `plan.md` when the build runs, and the two must
  not drift (this ADR is authoritative for repo-binding; `plan.md` for per-feature detail).

### Confirmation

Fitness function: (1) the plugin exists at `plugins/<name>/` with the file set above and
`/plugin validate .` passes; (2) `adrs --ng doctor` stays green; (3) the spike from build
step 1 demonstrably blocks a Stop and returns a real gate result before Slice 1 starts;
(4) `git status` after a run shows only committable artifacts, no `.claude/` scratch (ADR-14);
(5) `marketplace.json` no longer lists deep-planner and `checkup` has regenerated the tables.

## More Information

Grounded in repo evidence: `plugins/methodologist/.claude-plugin/plugin.json` (manifest
shape, semver, author), `plugins/methodologist/scripts/validate.py` (Python tooling), root
`.gitignore` (`.claude/*` already excluded), `CLAUDE.md` (versioning + `checkup`-generated
tables). Depends on ADR-3, ADR-4, ADR-8, ADR-14, ADR-15; realises the build order flagged
across ADR-6/13. The hook-runtime and confidence-syntax choices are provisional pending the
build-step-1 spike (assume-broken-until-proven).
