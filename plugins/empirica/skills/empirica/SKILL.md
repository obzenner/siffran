---
name: empirica
description: "Empirical-convergence development workflow. Track unknowns with confidence scores in a living spec, drive them to a fixed point (spike the ones that need runtime evidence, reason through the rest), then hand a converged spec to implementation. Use when starting non-trivial work where the plan is not yet certain — 'how should we build X', 'I'm not sure whether A or B', 'design and implement this feature', 'spike this', 'we don't know if this approach works'. Two paths: known territory goes straight to finalize; unknown territory runs the empirical loop first. Invoke as /empirica <goal>."
argument-hint: "[goal or feature to build]"
allowed-tools: [Read, Glob, Grep, Bash, Edit, Write, Agent, TaskCreate, TaskUpdate, WebFetch]
---

# Empirica — Empirical-Convergence Workflow

You are running a workflow that resolves unknowns to a fixed point *before* it writes
production code. It is NOT freestyle planning. State is explicit, convergence is
hook-enforced, and every gate is a real deterministic check — not your own judgment.

This skill is the design of ADRs 1–16 in `doc/adr/` made executable. When a decision
here surprises you, the ADR is the source of truth — read it, don't re-litigate it.

## Step 0: Adopt the stance

**Before anything else**, emit the evidence-over-recall stance declaration verbatim:

> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing
> claim discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open
> questions are resolved until blocked, then surfaced with what was tried.

This is the shared spine of methodologist (the required companion, ADR-3/12); its canonical
home is that plugin's `evidence-over-recall.md`. Parametric knowledge is a hypothesis; every
load-bearing claim is discharged against evidence (code / docs / runtime) or marked
UNVERIFIED. If that line is absent from your output, you are not running this workflow.

The user invoked: `$ARGUMENTS` — this is the **goal**.

## Step 1 — M1 Router: known or unknown territory?

Read the goal and the relevant code. Classify each thing the goal depends on:

- **Known** — you can point at evidence (code, docs, a prior ADR) that fixes the answer now.
- **Unknown** — the answer needs runtime evidence or a decision you cannot derive yet.

This is not a binary verdict on the whole task; it is a per-dependency split (ADR-5). The
"known path" is simply the case where the initial unknown set is already empty.

Announce: `Route: **known** | **unknown** — <one line why>`, then list the unknowns you found.

## Step 2 — establish the living spec (state substrate, ADR-15)

All state lives in a **spec** in the run's working directory (`spec.md`), adopting the
spec-kit artifact set by GitHub reference (ADR-15) — fetch the template with `WebFetch`, do
NOT vendor a copy. Templates are pinned to spec-kit `v0.13.4`
(commit `ee883a1d4ecee9afe06a81f1bd38a0b745a8d059`):

| Artifact | Pinned template URL |
|---|---|
| spec | `https://raw.githubusercontent.com/github/spec-kit/ee883a1d4ecee9afe06a81f1bd38a0b745a8d059/templates/spec-template.md` |
| plan | `https://raw.githubusercontent.com/github/spec-kit/ee883a1d4ecee9afe06a81f1bd38a0b745a8d059/templates/plan-template.md` |
| tasks | `https://raw.githubusercontent.com/github/spec-kit/ee883a1d4ecee9afe06a81f1bd38a0b745a8d059/templates/tasks-template.md` |

**Before using a pinned URL, check for a newer release** at
`https://github.com/github/spec-kit/releases`; if one exists, fetch its templates and update
the pin here (bump the plugin version). `research.md`/`data-model.md`/`contracts/` have no
standalone templates upstream — they are produced by the plan flow, not fetched.

Unknowns live as checkbox items **under a `## Unknowns` heading** (the gate only reads
that section, so task checklists elsewhere never block). Each carries an inline confidence:

```
## Unknowns
- [ ] U1: <the unknown, stated as a falsifiable question> <!-- confidence: 0.40 -->
- [ ] U2: <unresolvable — a human call> <!-- confidence: 0.20, blocked: needs-decision -->
```

Convention (enforced by the hook parser, `convergence_gate.py`):
- Confidence is a trailing HTML comment `<!-- confidence: N -->`, N in [0,1]. θ defaults to
  0.8 (`EMPIRICA_THETA` overrides).
- **A checkbox with no / malformed / out-of-range confidence counts as 0.0 and BLOCKS** —
  absence of a score is not convergence. Every unknown you add must be scored.
- An unknown you genuinely cannot resolve (a human judgment call, unobtainable data, an
  experiment you can't run here) is a **residual**: tag it
  `<!-- confidence: N, blocked: needs-decision|needs-data|needs-experiment -->`. A blocked
  unknown is surfaced to the human and **stops gating** (the residual protocol of
  evidence-over-recall §3; this is the loop's principled termination, ADR-9).

**Convergence ⇔ every unknown is either ≥ θ or blocked (surfaced)** (ADR-7/9). A known-path
spec is one where that already holds. Fail direction: no `spec.md` → gate allows (not our
run); `spec.md` present but unreadable → gate fails **closed**.

> If routed **known** and the spec already converges, skip to Step 5 (Finalize).

## Step 3 — M2 Staffer + M3/M4 spike the unknowns (unknown path only)

For each sub-θ unknown, decide how to resolve it:

- **needs-experiment** → **spike it (M3/M4)**. Model the production scenario as a small
  runnable check (an integration test / script). The verdict is a *deterministic gate*,
  never your opinion: run `hooks/spike_harness.py <cmd>` — `gate` is the real subprocess
  exit code (`pass` ⇔ exit 0). When several approaches compete, race them (M4) and let the
  gate pick. Spike scratch is transient (ADR-14): keep it under `.claude/` scratch, never commit.
- **needs-data** → fetch the source the goal points you at (docs, an API surface). Verify
  API surfaces against actual docs before trusting them.
- **needs-decision** → a genuine judgment call for the user; surface it, don't guess.

Staffing (M2) is inline — spawn Agents for parallel discovery/spikes as needed. Do **not**
depend on external staffing tools (inkrot `/hr` is forbidden, ADR-3).

## Step 4 — M9 Assessor: one convergence pass, then loop

The Assessor is the fixed-point function `f` (ADR-7). One pass does exactly two things:

1. **Update scores** — raise each unknown's confidence to reflect the evidence just gained.
   An unknown resolved by a passing gate goes to ≥ θ; a refuted approach stays low with the
   refutation recorded.
2. **Derive new unknowns** — resolving one may reveal others. Add them as new sub-θ items,
   recording their origin. **Specialize only** (ADR-9): a derived unknown must be strictly
   narrower than its parent, so the set is well-founded and the loop terminates.

Rewrite the spec's unknowns section with the new scores and any derived items. Then **end
your turn**. The Stop hook (`convergence_gate.py`) reads the spec: if any unknown is still
< θ it blocks and you continue the loop; once all ≥ θ it lets you stop. This is enforced by
the harness, not your memory (ADR-8). Across compaction, `SessionStart:compact` re-injects
the spec so the loop is durable-resumable (ADR-8/9); the 8-block cap means you resume across
turns rather than holding one turn open.

**Do not hand-declare convergence.** Convergence is what the gate says, not what you assert.

## Step 5 — M5 Finalizer: escalate to /think at the confluence

Both paths meet here. This is the expensive tier — use `/think` (methodologist, the required
companion, ADR-3/12/13) **conservatively**: escalate to it when the design is high-stakes,
the loop stalled, or invariants are genuinely ambiguous — not at every step. `/think`
resolves the finalized invariants and produces the reasoning trace.

Finalize the committable artifacts (ADR-14, committable tier):
- **ADRs** (via the `adrs` CLI, MADR format) — every real decision with its rejected
  alternatives. This is `doc/adr/`.
- the **spec** — self-contained: names files/interfaces, states what's out of scope, ends
  with an end-to-end verification step.
- **data-model.md / contracts/** from the spec-kit template (per feature, not one global schema).
- the **implementation task**.

The `/think` trace itself is transient (ADR-14) — it informs the ADR, then is discarded.

## Step 6 — M7 Handoff: hand the converged spec to implementation

With convergence reached and artifacts committed, hand off to implementation. Tests and
code are the committable output; the deterministic test suite is the machine-checkable spec
and the trust boundary (ADR-13) — implementation is "done" when the gates are green, not
when it looks right.

## Data-model summary (what each step emits)

| Tier | Artifacts | Home |
|---|---|---|
| **Transient** (ADR-14) | unknowns working state, spike scratch + raw gate output, `/think` traces, staffing briefs, race bookkeeping | `.claude/` scratch, git-ignored |
| **Committable** (SSOT) | ADRs, the converged spec, data-model/contracts, the impl task, tests + code | git |

Unknowns + confidence live inline in the spec (ADR-15); the spec is the audit trail humans
already read. No bespoke ledger file exists.

## Rules

- Convergence is hook-enforced and gate-defined. Never assert "done" — let the gate decide.
- Every spike gate is a real subprocess exit code, never model judgment (ADR-13).
- Standards over invention: spec-kit by GitHub reference, MADR for decisions — do not invent
  schemas, do not vendor stale copies (ADR-15).
- `/think` is the expensive tier — escalate on stall/ambiguity/high-stakes, not by reflex.
- Derived unknowns specialize only, so the loop terminates (ADR-9).
- methodologist is a required companion; inkrot `/hr` is forbidden (ADR-3).
