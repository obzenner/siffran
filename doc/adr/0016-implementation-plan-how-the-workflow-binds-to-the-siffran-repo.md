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
  (ADR-8), transient vs committable (ADR-14), claim-graph state schema (ADR-22, superseding the
  spec-kit substrate of ADR-15).
* Ship a thin usable slice before the heavy engine (ship-or-kill gate).

## Considered Options

Per axis, the alternatives considered and the choice:

* **Plugin identity** — new plugin vs. a skill inside methodologist. → **New plugin**,
  because it ships hooks and its own lifecycle; methodologist stays a pure reasoning router.
* **Hook script language** — bash vs. Python vs. node. → **Python**, matching the repo's
  existing tooling (`plugins/methodologist/scripts/validate.py`); bash rejected as too
  brittle for JSON ledger parsing, node rejected as a new runtime the repo doesn't use.
  (Marked to confirm against the actual hook I/O contract in the first spike — see Drivers.)
* **Claim + confidence representation** — inline in a markdown spec vs. a typed store. →
  **A JSON claim graph** (ADR-22): each claim is a node (GSN element type) carrying a
  confidence value and its in-toto evidence leaves, written to the run directory and parsed by
  the Python hook. (The original build shipped an inline-in-`spec.md` convention under the
  superseded ADR-15; ADR-22 replaces it with the claim graph. Exact on-disk shape proven in the
  build spike, not guessed here.)
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
never referenced (M2 staffing is inline). Claim/argument and evidence standards (GSN, in-toto)
referenced, never vendored (ADR-22, carrying forward ADR-15's reference-don't-vendor principle).

**State.** Claims + confidence + evidence live in the run's **claim graph** (ADR-22: a GSN
argument with in-toto evidence leaves), held in the run directory `.claude/empirica/<run_id>/`
alongside the manifest and transient scratch (spike output, `/think` traces, race bookkeeping) —
all git-ignored (ADR-14/19). Committable output: the goal's resolved deliverable, plus MADR
ADRs when the intent is a decision.

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
* Resolved: the build-step-1 spike confirmed Python hooks fit the I/O contract, and refuted
  the assumed Stop-block *mechanism* (JSON `decision` field → actually exit 2 + stderr), now
  corrected in ADR-8. The spike also confirmed an inline `<!-- confidence: N -->` markdown
  convention (15/15 checks); that convention is **superseded by the JSON claim graph of
  ADR-22** — the confidence value and evidence now live in a claim-graph node, not a markdown
  comment. The spike remains valid as the fitness function that fired.
* Bad, because this ADR is authoritative for repo-binding while per-feature detail lives in the
  run's claim graph (ADR-22); the two must not drift.

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
