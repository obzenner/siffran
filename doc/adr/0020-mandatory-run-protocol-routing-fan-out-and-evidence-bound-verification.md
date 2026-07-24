---
number: 20
title: "Mandatory run protocol: claim graph, two-fold validation, fan-out, and independent audit"
status: accepted
date: 2026-07-24
tags:
  - architecture
  - process
  - verification
  - trust-boundary
links:
  - target: 13
    kind: Depends on
  - target: 18
    kind: Realizes
  - target: 19
    kind: Depends on
  - target: 7
    kind: relatesto
  - target: 9
    kind: relatesto
  - target: 17
    kind: relatesto
---

# Mandatory run protocol: claim graph, two-fold validation, fan-out, and independent audit

## Context and Problem Statement

empirica's steps live as prose in `SKILL.md`. A skill is an instruction to a model, and a
model can silently skip any instruction with no consequence: the Stop gate (ADR-8) reads only
the confidence numbers in the living spec, so it enforces the *shape of the output* (every
unknown ≥ θ or blocked) and nothing about the *process that produced it*. An agent can
therefore reach `converged` while having:

* investigated first and back-labelled everything "known" to skip the loop (inverting the
  router, ADR-5);
* never fetched an external source, never contradicted its own reasoning, and drawn
  conclusions about a repository purely from stale training weights — the exact failure the
  evidence-over-recall stance forbids;
* bypassed the deterministic spike harness (ADR-13) and hand-written a confidence float for a
  `needs-experiment` unknown — the self-attestation hole ADR-18 names;
* run entirely single-agent, so no independent perspective ever checked the author's work.

Every one of these is a real observed run. The workflow is *suggested*, not *enforced*: it
uses the vocabulary of gates without gating the steps. An external adversarial review
(GPT-5.6 Sol, 2026-07-23) and independent field precedent — `github.com/modu-ai/moai-adk`, a
Claude Code plugin that ships separate `plan-auditor`/`sync-auditor` agents on the principle
*"the authoring side cannot grade its own work"* and persists verification evidence to disk —
both converge on the same conclusion: the process must be gated, and the verifier must not be
the author.

This ADR records the protocol every empirica run MUST perform. ADR-21 records the three
technical mechanisms that enforce it on the Claude Code and PI harnesses; this ADR is the
harness-independent contract they enforce.

## Decision Drivers

* A step that cannot be skipped without a gate objecting is the only step that is real; prose
  is advisory (ADR-8/13/17 — "enforce only what you can verify").
* Validation is two-fold and **research comes first**: *every* claim's confidence must be earned
  by external evidence outside the model's training data (Fold 1 — fetched docs, read code,
  runtime, primary sources), and only the machine-verifiable class additionally requires a
  passing spike (Fold 2). The most common failure is grading a claim from training weights with
  no external source consulted at all — that is a Fold-1 violation and it precedes any spike.
* The verifier must be a principal distinct from the author, or "verification" is the author
  grading itself (Goodhart; ADR-13's self-preferential path; moai-adk's auditor split).
* Fan-out is a budgeted cost, not the default; parallelism must earn its coordination cost
  (ADR-17). The protocol prescribes *when* to spawn, not "spawn always."
* The stance is load-bearing, not decorative: parametric knowledge is a hypothesis, and a run
  that resolves an unknown without touching evidence (code/docs/runtime) has not resolved it.

## Considered Options

* **Keep the protocol as SKILL.md prose (status quo).** Rejected: demonstrated to be skippable
  with zero consequence; the gate cannot see process.
* **Enforce output only, document process as best-effort.** Rejected: this is the honest
  "design tool" framing (ADR-18 "do nothing"), but it leaves the plugin's central promise —
  convergence means something — unbacked, and the docs would have to stop claiming it.
* **Mandatory, gate-checked run protocol (chosen).** Model unknowns as a claim graph the agent
  adjudicates (approve / block / discard); make each phase transition a harness-checked gate;
  require two-fold validation (Fold 1 research evidence for every claim, Fold 2 spike for
  experiments); and require an independent auditor pass before a run may report `converged`.

## Decision Outcome

Chosen: **the mandatory run protocol below.** Every empirica run performs these steps in this
order; each numbered gate is a transition the harness refuses to allow until its condition
holds (mechanisms in ADR-21). "The agent" is whichever principal holds the turn; "a subagent"
is a spawned principal with its own context.

### P0 — Stance (precondition)
The agent emits the evidence-over-recall stance verbatim. Parametric knowledge is a
hypothesis; every load-bearing claim in the run is discharged against evidence
(code / docs / runtime) or surfaced as UNVERIFIED. A run whose only source is the model's own
weights has produced no evidence and cannot converge.

### P1 — Route BEFORE investigating
The agent classifies each dependency of the intent as **known** (evidence fixes the answer
now — a citable file, doc, or prior ADR) or **unknown** (needs runtime evidence or an
undecidable choice) and announces `Route: known | unknown` to the human **before** gathering
evidence. Routing is a commitment made up front, not a label applied retroactively to justify
a shortcut. The initial unknown set U is the `unknown` dependencies.

### P2 — Seed the claim graph
Each unknown is a **claim** the run must adjudicate. Claims are written to the run directory's
**claim graph** (`.claude/empirica/<run_id>/`, ADR-19; schema per ADR-22 — a GSN argument with
in-toto evidence leaves), each carrying `confidence: 0.0` until validated. A claim is a **node
in a directed graph**, not a checklist row:
resolving one claim derives child claims (specialize-only, ADR-9), and the run walks this graph
— **proposing, validating, approving, or discarding** each node — until every claim on the path
to the goal is adjudicated. This is the internal shape the driving agent operates; the agent's
job is to move each claim to a terminal state (approved-with-evidence, blocked, or discarded),
not to accumulate a to-do list. The graph is the run's internal working memory, never a
repository deliverable (ADR-14/15).

### P3 — Validation is TWO FOLD, and research comes first
A claim's confidence is earned by validation, and validation has two folds. **Fold 1 applies to
every claim; Fold 2 applies only to the experiment class. Fold 1 is not optional and it is not
downstream of Fold 2 — it is the gate that comes first.**

**Fold 1 — RESEARCH (every claim): did external evidence, outside the model's training data,
verify or refute this claim?** Before a claim's confidence may move off 0.0 *at all*, the agent
must have consulted a source that is not its own weights — fetched documentation, read the
actual code in the repo, an API surface, runtime output, a primary source online — and cited it
against the claim. Recall is not evidence (P0). This is the fold the observed failures skipped:
an agent that "read the repo and drew conclusions from training data" performed **zero** Fold-1
validation and every confidence it wrote is unbacked. Each claim records its Fold-1 evidence:
`claim_id → {source, kind: docs|code|runtime|web, citation, supports|refutes, ts}`.
  * `needs-data` claims are resolved entirely in Fold 1 — fetch and cite the source.
  * `needs-decision` claims cannot be validated by the agent; surface to the human, blocked.

**Fold 2 — SPIKE (experiment claims only): did a real deterministic check pass?** A
`needs-experiment` claim additionally requires a spike through the harness (ADR-13) whose verdict
is a real subprocess exit code, binding `claim_id → {command_hash, gate: pass, result_hash,
files_hash, ts}` (ADR-18, enforced ADR-21 M2). Fold 2 **presupposes Fold 1**: you research what
the check should be and what "correct" looks like from external evidence *before* you build the
spike. A passing spike over a claim that was never researched is a green light on an unexamined
assumption.

**Grade, then approve or DISCARD.** After validation the agent grades the claim:
  * evidence **supports** and (for experiments) the spike passed → confidence ≥ θ, **approved**;
  * evidence **refutes** the claim → the claim is **discarded** (removed from the path, its
    refutation recorded), not parked at low confidence — a refuted claim is not a weak claim, it
    is a dead node, and its children are pruned;
  * evidence is **absent or inconclusive** → stays sub-θ, remains open, loops.

### P4 — Fan-out is budgeted
When claims are independent and breadth-bound, the agent spawns subagents to research/spike them
in parallel rather than serially — each subagent returns structured output the parent folds back
into the graph. Competing approaches to one claim may race as parallel subagents (M4), the gate
picking the winner. Fan-out is a budgeted exception (ADR-17): every spawn is charged against the
spawn cap and denied past it. Serial resolution is the default for small runs; fan-out earns its
coordination cost only when breadth is real.

### P5 — Assessor pass (the fixed-point function f)
One pass updates each claim's confidence from the validation gained (P3), derives child claims
(specialize-only, well-founded, ADR-9), and discards refuted nodes. The agent does not
hand-declare convergence; it writes the graph and ends the turn. The variant
`max_passes − passes` (ADR-19) guarantees termination.

### P6 — Independent audit BEFORE converged
Before a run may report `converged`, a **separate principal** (a spawned auditor subagent, not
the authoring agent) verifies the run against a fixed rubric: routing happened before
investigation (P1); **every approved claim has a Fold-1 research citation to a non-training-data
source** (P3, Fold 1); every `needs-experiment` claim at ≥ θ has a valid Fold-2 spike record (P3);
no claim was graded from recall alone; refuted claims were discarded, not parked; derived claims
specialize. The author cannot sign off on its own convergence. The auditor's verdict is itself
an artifact the gate reads.

### P7 — Convergence and handoff
Convergence ⟺ every claim on the path to the goal is terminal — **approved with Fold-1 research
evidence (and Fold-2 spike evidence where it is an experiment), blocked, or discarded** — **and**
the independent audit passed. Only then does the Stop gate allow the stop. The run produces the
deliverable the intent demanded, placed per the intent (ADR-14); the claim graph stays in the
run directory. Non-convergence at `max_passes` is reported honestly as `stopped_residual`,
never fabricated green (ADR-17).

### Consequences

* Good, because each step is a gate transition, not a request — the observed skips (route
  inversion, recall-only grading, spike bypass, self-grading) become impossible rather than
  discouraged.
* Good, because validation is explicitly two-fold with **research first**: the gate that catches
  the most common failure (grading a claim from training data without consulting any external
  source) is Fold 1, and it precedes and is independent of the spike. A spike can only be built
  on a researched claim.
* Good, because it treats unknowns as a claim graph the agent adjudicates — approve, block, or
  **discard** — so a refuted claim is a pruned dead node, not a low-confidence row that lingers.
* Good, because it realizes ADR-18 (Fold 2) and adds the missing auditor split (P6), closing the
  self-attestation hole within empirica's legitimate design-time scope — CI remains the
  production wall (ADR-13).
* Good, because fan-out is defined by trigger (independent + breadth-bound) and bounded by the
  spawn budget, so parallelism is earned, not reflexive.
* Bad, because it moves empirica decisively from *design tool* toward *runtime harness* — the
  identity shift ADR-18 flagged, now taken deliberately.
* Bad, because the two evidence folds + the auditor add real machinery (research citation store,
  spike store, auditor spawn, per-harness enforcement) and cost — justified only because the
  alternative is a convergence signal that means nothing.
* Bad, because on a harness without process gating the protocol degrades to advice (ADR-21
  records which guarantees hold on which harness — no silent overclaim).

### Confirmation

Fitness function: (1) a claim whose confidence moved off 0.0 with **no Fold-1 research citation
to a non-training-data source** is refused approval and flagged by the auditor — grading from
recall cannot converge; (2) a `needs-experiment` claim at ≥ θ without a Fold-2 spike artifact is
refused by the gate; (3) a run whose route was declared after investigation is flagged (P1);
(4) a refuted claim left parked at low confidence instead of discarded is flagged; (5) the
auditor is a distinct principal — the authoring agent's own claim never satisfies P6; (6)
fan-out spawns are charged to the spawn ledger and denied past the cap (ADR-17); (7) at
`max_passes` the run reports `stopped_residual`, not `converged`.

## More Information

Realizes ADR-18 (evidence-bound convergence, P3 Fold 2) and depends on ADR-19 (run identity + the
evidence store's home) and ADR-13 (deterministic gate as the verdict; agentic audit as a
distinct-principal sensor, not a self-graded verdict). Field precedent: `modu-ai/moai-adk`
(separate `plan-auditor`/`sync-auditor`; *"the authoring side cannot grade its own work"*;
`verify-diet` persisting evidence to `.moai/state/verify/<session>/` with only exit code +
bounded tail in context). Contrast: `Fission-AI/OpenSpec` relies on voluntary adherence and
human review with no process gate — the same hole this ADR closes. Harness-specific
enforcement is ADR-21.
