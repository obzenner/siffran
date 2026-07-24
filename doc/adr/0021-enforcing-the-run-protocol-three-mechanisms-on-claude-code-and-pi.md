---
number: 21
title: "Enforcing the run protocol: three mechanisms on Claude Code and PI"
status: accepted
date: 2026-07-24
tags:
  - architecture
  - process
  - harness
  - verification
links:
  - target: 20
    kind: Depends on
  - target: 18
    kind: Realizes
  - target: 19
    kind: Depends on
  - target: 13
    kind: relatesto
  - target: 17
    kind: relatesto
---

# Enforcing the run protocol: three mechanisms on Claude Code and PI

## Context and Problem Statement

ADR-20 defines the mandatory run protocol as a harness-independent contract. A contract that
no mechanism enforces is prose (ADR-8/20). This ADR records the **three technical mechanisms**
that make the protocol's process-gates real, and states honestly which of them each supported
harness — **Claude Code** and **PI** — can carry, because the two harnesses have different
control surfaces and overclaiming enforcement is the exact failure this whole line of work
exists to prevent.

Verified harness surfaces (checked against the running tools, 2026-07-24, not recalled):

* **Claude Code** exposes a lifecycle **hook** system: `UserPromptExpansion` (fires at skill
  invocation, can block), `PreToolUse` (fires before a tool/subagent spawn, can deny via
  exit 2), `Stop` (fires at turn end, can block via exit 2), `SessionStart:compact`
  (re-injection). Hooks are external processes the harness runs; the model cannot skip them.
  This is empirica's existing enforcement substrate (ADR-8/17/19).
* **PI** (`pi` v0.80.6, `@earendil-works/pi-coding-agent`) has **no lifecycle-hook system**.
  Its control surfaces are: **extensions** (installed TS packages that register tools and
  flags), **skills**, `--mode rpc|json` (an external orchestrator drives the turn loop and
  reads structured output), `--print` (non-interactive one-shot), a tool **allow/denylist**
  (`--tools`/`--exclude-tools`), and a **subagent extension** that spawns a separate `pi`
  process per task — single `{agent,task}`, parallel `{tasks:[…]}` (cap 8), or chained
  `{chain:[…]}` — capturing structured JSON output (per-task cap 50 KB). Enforcement on PI is
  therefore **orchestrator-driven**, not hook-driven: the gate lives in the process that
  invokes `pi`, not inside a `pi` turn.

## Decision Drivers

* Each protocol gate (ADR-20 P1–P7) must map to a concrete interception point, or it is not
  enforced.
* The mechanism must sit at a layer the model cannot bypass — an external process (Claude Code
  hook) or the orchestrator that owns the `pi` loop (PI), never an instruction inside the
  model's own context.
* Evidence and audit verdicts must be **artifacts on disk** the gate reads, not claims in the
  transcript (ADR-13/18; moai-adk `verify-diet`).
* Where a harness cannot enforce a gate, that MUST be stated, not papered over (ADR-18's honesty
  requirement; the "design tool vs runtime harness" line).

## Decision Outcome

Chosen: **three mechanisms.** Each is defined once as a contract, then mapped to Claude Code
(hook) and PI (orchestrator/extension). All three read and write the run directory
(`.claude/empirica/<run_id>/`, ADR-19), which is the shared state both harnesses agree on.

### Mechanism 1 — Phase-transition gate (enforces P1, P4, P7)

The run is a state machine `route → resolve → assess → audit → converged`; a transition is
refused until its precondition holds. The manifest records the current phase and the pass
counter (ADR-19).

* **Claude Code:** the `Stop` hook (`convergence_gate.py`) already refuses to end the turn
  while unknowns are sub-θ; it is extended to also refuse `converged` unless the phase is
  `audit`-passed (Mechanism 3) and to record `route` was declared before the first evidence
  tool-call (observable: a `PreToolUse` hook stamps the first investigative tool call's
  timestamp into the manifest; if it precedes the route announcement, the audit fails). The
  `UserPromptExpansion` run-start hook seeds the manifest at phase `route`.
* **PI:** the orchestrator driving `pi --mode rpc` owns the transitions. It runs `pi` in
  phases: a first `--print` call that must emit the route classification before any
  edit/bash-heavy call is permitted (enforced by launching that phase with `--tools` limited
  to read-only tools), then the resolve/assess phases with the full toolset. The orchestrator,
  not the model, decides when the machine may advance, and writes the phase into the manifest.

### Mechanism 2 — Two-fold evidence binding (enforces P3; realizes ADR-18)

Validation is two-fold and **research (Fold 1) is enforced first and for every claim**; the
spike (Fold 2) applies only to the experiment class. Both folds write artifacts to the run
directory that the gate reads before permitting a claim's confidence or the `converged`
transition.

**Fold 1 — research-citation gate (every claim).** A claim's confidence may leave 0.0 only when
a research record binds it: `claim_id → {source, kind: docs|code|runtime|web, citation,
supports|refutes, ts}` — evidence from outside the model's training data. The gate rejects an
approved claim (confidence ≥ θ) that has no Fold-1 record, and the auditor (Mechanism 3)
independently confirms the citation resolves to a real source, not a fabricated one.
* **Claude Code:** the run records each claim's evidence as it fetches/reads (a `PreToolUse`
  hook on `WebFetch`/`Read`/`Bash` can stamp the tool call and its target into the claim's
  research record, so the citation is harness-observed, not merely typed). The `Stop` hook
  refuses `converged` if any approved claim lacks a research record. What a hook cannot fully
  prove — that the *content* actually supports the claim — is the auditor's job (Mechanism 3),
  which re-reads the cited source.
* **PI:** the orchestrator runs the research phase with the fetch/read toolset and captures the
  tool results via `--mode json`, writing the research record itself; it refuses to advance a
  claim to graded state without one. Same record shape, same run directory.

**Fold 2 — spike gate (experiment claims only).** A `needs-experiment` claim reaches ≥ θ only
via a harness-written spike artifact binding `claim_id → {command_hash, gate: pass,
result_hash, files_hash, ts}`. The gate rejects a ≥ θ experiment claim lacking a matching
`gate: pass` whose `files_hash` still matches. Fold 2 presupposes Fold 1 — the gate requires
the research record to exist before it will accept a spike record for the same claim.
* **Claude Code:** the spike harness (`spike_harness.py`, ADR-13) is the only writer of the
  spike record — it runs the real check and writes the artifact keyed to the claim. The `Stop`
  hook validates the binding before allowing `converged`. The model cannot forge the record
  because it is written from the subprocess exit code, not the transcript, and `files_hash`
  detects a spec edited after the spike.
* **PI:** the spike is a `pi` **tool/extension** (or an orchestrator-run subprocess) whose exit
  code the orchestrator captures via `--mode json`; the orchestrator writes the same spike
  artifact and validates it before permitting the `converged` transition. Same artifact shape,
  same run directory — the enforcement principal is the orchestrator instead of a Stop hook.

### Mechanism 3 — Independent auditor (enforces P6; the author cannot grade itself)

Before `converged`, a **separate principal** verifies the run against the ADR-20 rubric and
writes a verdict artifact. The authoring agent's own claim never satisfies this gate. The
auditor's most important job is the one a hook cannot do: **re-read each approved claim's Fold-1
research citation and confirm the cited source actually supports the claim** — catching a
citation that was fabricated or that does not say what the author claimed. It also checks
route-before-investigate (P1), a valid Fold-2 spike record for every experiment claim, that no
claim was graded from recall alone, that refuted claims were discarded rather than parked, and
that derived claims specialize.

* **Claude Code:** the authoring agent spawns an auditor **subagent** (a `Task`/`Agent` spawn,
  charged to the spawn ledger, ADR-17) with a read-only, audit-only brief; the auditor writes
  its verdict to the run directory. The `Stop` hook requires a passing auditor verdict artifact
  (distinct principal, distinct spawn id) before allowing `converged`. A run cannot audit
  itself because the hook checks the verdict came from a spawned auditor, not the author.
* **PI:** the orchestrator invokes the auditor as a **subagent** via the subagent extension
  (`{agent: "auditor", task: …}`) — a separate `pi` process with its own context, returning
  structured JSON. The orchestrator gates the `converged` transition on that verdict. PI's
  subagent extension is purpose-built for exactly this (separate process, structured output,
  parallel/chain modes for multi-auditor panels).

### Harness capability summary (no overclaim)

| Protocol gate (ADR-20) | Claude Code | PI |
|---|---|---|
| P1 route-before-investigate | `PreToolUse` timestamp + audit | read-only-tools phase in orchestrator |
| P3 Fold 1 — research citation (every claim) | `PreToolUse` stamps fetch/read; `Stop` hook requires a record; auditor re-reads source | orchestrator captures fetch/read via `--mode json`, writes record; auditor re-reads |
| P3 Fold 2 — spike (experiment claims) | `spike_harness.py` writes, `Stop` hook validates | spike tool/subprocess writes, orchestrator validates |
| P4 fan-out (budgeted) | `Agent` spawn + `PreToolUse` spawn cap (ADR-17) | subagent extension (cap 8) + orchestrator budget |
| P6 independent auditor | auditor subagent + `Stop` hook requires verdict | auditor subagent + orchestrator requires verdict |
| P7 convergence | `Stop` hook (exit 2 blocks) | orchestrator refuses next turn |

**Enforcement principal differs by harness and this is load-bearing:** on Claude Code the
enforcer is an external hook the model cannot skip; on PI the enforcer is the orchestrator that
owns the `pi` loop. A bare interactive `pi` session with no orchestrator has **no process
gate** — there the protocol is advice, and empirica must say so rather than imply enforcement.

### Consequences

* Good, because every ADR-20 gate maps to a concrete, model-unbypassable interception point on
  both supported harnesses, using each harness's real control surface.
* Good, because the three mechanisms share one on-disk substrate (the run directory), so a run
  is portable and inspectable regardless of harness.
* Good, because it states plainly where enforcement does not hold (bare `pi` with no
  orchestrator) instead of overclaiming — the honesty ADR-18 demands.
* Bad, because PI enforcement requires building and running an orchestrator (a `--mode rpc`
  driver); without it, PI runs are advisory. This is real work and a real limitation.
* Bad, because three mechanisms + auditor spawns raise per-run cost and surface area; justified
  only by the protocol they make real (ADR-20).
* Bad, because the harnesses' enforcement models are genuinely different, so the plugin carries
  two enforcement implementations against one protocol — a maintenance cost.

### Confirmation

Fitness function: (1) on Claude Code, an approved claim with no Fold-1 research record, or a
`needs-experiment` claim with no Fold-2 spike record, or a deleted auditor verdict, each makes
the `Stop` hook refuse `converged`; (2) on PI, the `--mode rpc` orchestrator refuses to grade a
claim without its Fold-1 record, to approve an experiment claim without its Fold-2 spike, and to
converge without a subagent auditor verdict; (3) a bare `pi -p` run with no orchestrator is
documented as advisory (no process gate) and the docs make no enforcement claim for it; (4) the
auditor is a distinct spawn/process id in both harnesses — the author's own output never
satisfies P6 — and it re-reads at least one cited source to confirm the citation is real;
(5) the run directory contents (manifest, research records, spike records, auditor verdict) are
identical in shape across harnesses.

## More Information

Depends on ADR-20 (the protocol these mechanisms enforce) and ADR-19 (run identity + the run
directory the mechanisms share); realizes ADR-18 (Mechanism 2 Fold 2 is the evidence-bound gate);
relates to ADR-13 (deterministic verdict) and ADR-17 (spawn budget bounding fan-out and auditor
spawns). Harness facts verified against Claude Code hooks documentation
(code.claude.com/docs/en/hooks) and the running `pi` v0.80.6 (`--help`, the installed subagent
extension at `@earendil-works/pi-coding-agent/examples/extensions/subagent`), 2026-07-24 — not
from recall. Field precedent for the auditor split and disk-persisted evidence:
`modu-ai/moai-adk`.
