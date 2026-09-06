---
name: empirica
description: "Empirical-convergence development workflow. Adjudicate a claim graph — propose claims, earn each one's confidence with real external evidence (research first, then a deterministic spike where the claim is machine-checkable), discard what evidence refutes — then have an independent auditor verify the run before it may report convergence. Use when starting non-trivial work where the plan is not yet certain — 'how should we build X', 'I'm not sure whether A or B', 'design and implement this feature', 'spike this', 'we don't know if this approach works'. Two paths: known territory goes straight to finalize; unknown territory runs the empirical loop first. Invoke as /empirica <goal>."
allowed-tools: Read Glob Grep Bash Edit Write Agent TaskCreate TaskUpdate WebFetch
compatibility: Designed for Claude Code, Codex CLI 0.146.0+, and Pi; requires methodologist as a companion and python3 for hook-backed hosts.
# TOP-LEVEL on purpose (ADR-28). `argument-hint` only drives autocomplete when it sits here; under
# `metadata` it is arbitrary key-value data and the hint never shows, so the mode flags would be
# undiscoverable. This is a Claude Code-only field, which makes the plugin unpackageable for
# claude.ai and the Skills API — an acceptable and already-unavoidable cost, since empirica's
# enforcement IS its Python lifecycle hooks and those do not run there at all.
argument-hint: "[--cli-exec] [--multi-provider] <goal>"
---

# Empirica — Empirical-Convergence Workflow

You are running a workflow that resolves unknowns to a fixed point *before* it writes
production code. It is NOT freestyle planning. State is an explicit **claim graph** and the
convergence loop is hook-enforced: you cannot silently stop while claims are open, and you
cannot reach `converged` by typing confidence numbers.

**Honest scope (ADR-13/18/19/21):** empirica is a *design-time* harness, not a production trust
boundary. Be precise about what is enforced versus taken on trust — overclaiming enforcement is
the exact failure this plugin exists to prevent.

**Enforced — the model cannot skip these (external hook processes):**
- **Fold-1 research citation.** A claim cannot be approved without a structurally valid
  research record binding it to a source. The Stop gate names the missing fold and refuses.
- **Fold-2 spike verdict.** A `needs-experiment` claim at ≥ θ needs a spike record whose `gate`
  came from a **real subprocess exit code**: `spike_harness.py` is the only code that writes one,
  so the workflow cannot *accidentally* produce a green record without running the check. A
  `files_hash` mismatch detects a tree edited after a green spike, a spike binding no files is
  rejected as vacuous, and a spike predating its research is rejected outright. (A record written
  by hand rather than by the harness is a different matter — see NOT enforced.)
- **Derived claim state.** A claim's terminal state is *computed* from evidence on every read.
  There is no persisted state field: writing `"state": "approved"` into the graph does nothing.
  This one is structural — it holds even against a hostile graph.
- **An audit that never happened, or that no longer covers the run.** Convergence requires both a
  spawn ticket and a verdict, so a run that simply skips the audit — the failure observed in the
  wild — cannot report `converged`. Coverage is checked **per claim** against the claim's text digest
  and its evidence digest, so a reworded claim, a swapped citation, or a refutation arriving later
  un-reviews that claim (ADR-25). The verdict is additionally bound to the argument's **shape**, so a
  claim cannot be detached or deleted to shrink what the audit had to cover (ADR-27).
  Fabricating the artifacts is another matter (see NOT enforced).
- **A frozen run's committed scope.** Freezing defers *later* claims, never the ones it committed
  to; the commitment is first-write-wins and a frozen run still owes a passing audit — a `blocked:`
  tag on a deferred claim cannot buy an exemption (ADR-26, ADR-27).
- **Run identity, fail-closed gating, and termination** (ADR-19): deleting or corrupting an
  active run's claim graph blocks; the loop provably ends in ≤ `max_passes` passes.
- **The spawn budget**, denied at the `PreToolUse` boundary (ADR-17).

**NOT enforced — real limits, stated plainly:**
- **Whether a citation is TRUE.** A hook can verify a citation was recorded; it cannot read the
  source. A fabricated URL or an irrelevant quote passes the hook. **This is the auditor's job**
  (`empirica-auditor` re-reads every citation) — an agentic sensor, not a deterministic gate.
- **Any artifact, against deliberate forgery.** The claim graph, evidence leaves, spike records,
  audit tickets and verdicts are all unsigned JSON in a directory the agent holds Write on, and
  no hook intercepts Write/Edit. An agent that *chooses* to hand-write them can converge a run —
  independently verified end to end. The nonce binds a verdict to a recorded spawn but is
  **computable, not secret** (`sha256(run_id:audit:seq)`, both inputs readable), so it is not
  authentication. What the gates actually buy: accidental and lazy skipping becomes impossible,
  and deliberate fabrication becomes a distinct, visible act that leaves artifacts on disk. That
  is file-level trust (ADR-19 G3) — not a security boundary.
- **Claims you never wrote down.** The graph gates the claims in it. A material unknown that was
  never recorded, or a claim detached from the goal, is invisible to every hook. Mitigations:
  the auditor compares the graph against the intent (rubric item 8), and route-before-investigate
  fixes the claim set up front. Neither is airtight.
- **Route ordering** is *witnessed* (timestamps), not gated: the stamp can be coarse, so a
  violation is reported to the auditor rather than hard-blocking the run.
- **Whether a freeze was honest.** Freeze bounds what the *harness* gates; no hook can judge whether
  the committed scope covered the intent. A run that freezes early under-scopes itself visibly — the
  deferred claims are in the result — but the judgement is the auditor's, so freeze moves that
  question from *impossible* to *reviewable*, not to *enforced* (ADR-26).

The production trust boundary on shipped code is CI (ADR-13), downstream of this workflow.
Agentic review may **block** but never **approve** — the deterministic spike is the only approver.

This skill is the design of the ADRs in `doc/adr/` (1–14, 16–24 accepted; 15 superseded by 22;
25–27 proposed, 28–29 accepted) made executable. When a decision here surprises you, the ADR is
the source of truth — read it, don't re-litigate it.

## Step 0: Adopt the stance

**Before anything else**, emit the evidence-over-recall stance declaration verbatim:

> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing
> claim discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open
> questions are resolved until blocked, then surfaced with what was tried.

This is the shared spine of methodologist (the required companion, ADR-3/12); its canonical
home is that plugin's `evidence-over-recall.md`. A run whose only source is the model's own
weights has produced **no** evidence and cannot converge. If that line is absent from your
output, you are not running this workflow.

The user invoked: `$ARGUMENTS`.

**Leading `--` flags are MODE FLAGS, not part of the goal** (ADR-28). `/empirica --cli-exec design X`
means the goal is `design X`, and the run is in CLI-exec mode. Strip any leading flags before you
read the goal — a claim graph rooted in `--cli-exec design X` has a corrupted intent, and it will be
the root of every claim in the run.

You do **not** apply the flags yourself. The active host adapter parses the invocation and sends the
resolved flags in `StartRun`; the application stores them in the operational document under
`~/.empirica-plugin/`. Read them only through `RestoreRun` on the `empirica/v1` adapter API.
A typo such as `--cli-exex` enables nothing and must be surfaced plainly.

**Runtime boundary:** on Claude use `adapters/claude`; on Codex use `adapters/codex` and
`adapters.codex.knowledge`; in both cases use the host's `BridgeTransport` for every graph,
evidence, spike, regate, route, freeze, audit-ticket, verdict, and restore operation. Operational
state belongs only under `~/.empirica-plugin/`; knowledge belongs only under `refs/empirica/*`.
Never create, read, or edit runtime state below `.claude/`, `.codex/`, or `.pi/`, and never edit
either store directly. The explicit `make migrate-legacy` command is the sole legacy-path exception.

**Codex activation and sensor boundary:** Codex runs start only when the prompt begins with
`$empirica` (the namespaced `$empirica:empirica` and legacy `/empirica` spellings are also
accepted). After classifying the goal, record the route before investigative Bash with
`python3 -c 'pass' -- --empirica-route '<reason>'`; the trusted `PreToolUse:Bash` hook stamps it
through the application before the no-op executes. Codex 0.146.0 hosted Responses API WebSearch
does not pass through `PreToolUse`, so its ordering is UNVERIFIED; cite its result normally, but do
not claim the hook witnessed it. Plugin installation also does not trust hooks: if the relevant
hashes are not trusted and enabled in `/hooks`, no hook-enforcement claim is valid.

## Step 1 — Route BEFORE investigating (P1)

Read the goal. Classify each thing it depends on **before you gather evidence**:

- **Known** — you can point at evidence (a citable file, doc, or prior ADR) that fixes the
  answer now.
- **Unknown** — the answer needs runtime evidence, or it is a choice you cannot derive.

Announce `Route: **known** | **unknown** — <one line why>` and list the unknowns, **then**
start investigating. Routing is a commitment made up front, not a label applied retroactively
to justify a shortcut (ADR-5/20 — the observed inversion).

**Record the announcement** immediately through
the active host's route operation (`adapters.claude.route.build_route_announcement_request`, or
Codex's `--empirica-route` no-op witnessed by `PreToolUse:Bash`) before any evidence
gathering. The application records both route and first investigation with CAS-guarded monotone
sequence numbers. Skipping the announcement remains a P1 violation.

This is a per-dependency split, not a verdict on the whole task. The "known path" is simply the
case where the initial unknown set is already empty.

## Step 2 — Seed the claim graph (state substrate, ADR-22)

Convergence state is a **claim graph**: a GSN assurance argument with in-toto evidence leaves.
Submit it with the active host's `knowledge.build_graph_request` through `BridgeTransport`. The
application stores immutable knowledge in `refs/empirica/*` and its graph pointer in the operational
document under `~/.empirica-plugin/`; it is never a worktree file. There are no
`spec.md`/`plan.md`/`tasks.md` runtime files.

Each unknown becomes a **Goal** node the run must adjudicate. Node types are GSN elements
(`Goal`, `Strategy`, `Solution`, `Context`, `Assumption`, `Justification`); edges are
`SupportedBy` (inferential/evidential) and `InContextOf` (contextual), per the GSN Community
Standard v3 (SCSC-141C, May 2021, CC BY 4.0).

empirica uses GSN's element and relationship **vocabulary** as its JSON schema. It does **not**
implement the OMG SACM v2.3 metamodel, produces no SACM-conformant XMI, and claims conformance
to **no** SACM compliance point (SACM v2.3 §2 defines five). If XMI export is ever built it
would target the Argumentation Model compliance point only (§2.2), which SACM defines as
independent of the Artifact and Terminology subpackages and which is the point GSN tools
conventionally map onto (Annex A).

Edges are validated against the standard's own permitted-connection lists, so a malformed
argument is rejected rather than gated on. `SupportedBy`: goal→goal, goal→strategy,
goal→solution, strategy→goal (**not** strategy→solution). `InContextOf`: goal or strategy →
context, assumption, or justification. The graph must also be **acyclic** — GSN forbids a goal
supporting itself directly or indirectly, because that is circular reasoning.

```json
{
  "root": "G0",
  "nodes": {
    "G0": {"type": "Goal", "text": "<the intent — the claim the run must establish>",
           "confidence": 0.0},
    "G1": {"type": "Goal", "text": "<a falsifiable sub-claim>",
           "kind": "needs-data", "confidence": 0.0},
    "G2": {"type": "Goal", "text": "<a machine-checkable sub-claim>",
           "kind": "needs-experiment", "confidence": 0.0}
  },
  "edges": [{"from": "G0", "to": "G1", "type": "SupportedBy"},
            {"from": "G0", "to": "G2", "type": "SupportedBy"}]
}
```

Rules the hooks enforce:
- **A claim only gates if it is on the `SupportedBy` path from the root.** Attach every claim to
  the goal, or it is silently ignored — and the auditor treats an unwritten or detached claim as
  a FAIL.
- **`confidence` missing, malformed, or outside [0,1] → 0.0 → blocks.** Absence of proof is not
  proof.
- **A structurally invalid graph is CORRUPT, not "unconverged"** → fail closed. An illegal GSN
  edge (e.g. a Goal `SupportedBy` a Context) is a malformed argument.
- **A residual** — a human call, unobtainable data, an experiment you cannot run, or an
  exhausted budget — takes `"blocked": "needs-decision|needs-data|needs-experiment|needs-budget"`.
  Only these four tags stop gating; an invented tag does not. A blocked claim is surfaced to the
  human and the run reports `converged: false`.

> If routed **known** and every claim is already evidenced and terminal, go to Step 5.

## Step 3 — Validation is TWO FOLD, and research comes FIRST (P3)

This is the heart of the workflow. **Fold 1 applies to every claim; Fold 2 only to the
experiment class; and Fold 2 presupposes Fold 1** — enforced, not advised.

### Fold 1 — RESEARCH (every claim, first)

A claim's confidence may not leave 0.0 until you have consulted a source that is **not your own
weights** and cited it. Fetched documentation, code you actually read, an API surface, runtime
output, a primary source online. **Recall is not evidence.** Reading a repo and drawing
conclusions from training data is zero Fold-1 validation, and every confidence written that way
is unbacked.

Build each in-toto Statement with the active host's `build_research_request` and submit it through
`BridgeTransport`:

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [{"name": "G1", "digest": {"sha256": "<sha256 of the claim's text>"}}],
  "predicateType": "https://empirica.dev/attestation/research/v1",
  "predicate": {"fold": "research", "kind": "docs|code|runtime|web",
                "source": "<URL/path/command>", "citation": "<the passage that decides it>",
                "result": "supports|refutes", "ts": "<ISO timestamp>"}
}
```

The digest binds the evidence to the claim **as currently worded** — reword a claim and its
evidence no longer counts, because it answered a different question.

- `needs-data` claims resolve entirely in Fold 1: fetch and cite.
- `needs-decision` claims are **never** agent-approvable. Surface them, blocked.

### Fold 2 — SPIKE (`needs-experiment` claims only)

Research what the check should be and what "correct" looks like **first**, then build it. Run it
through the harness, which is the sole writer of spike records:

Use the active host's `knowledge.run_spike(...)` to execute the deterministic harness, then submit
its sealed `SpikeExecution` with `build_spike_request(...)` through `BridgeTransport`. Never write
a spike verdict or evidence leaf directly.

`gate` is `pass` iff the command exited 0 — a real subprocess verdict, never your reading of it.
The record also carries `samples` — how many times the check actually ran — so a reader can tell a
20-sample verdict from a single lucky exit code (ADR-27). List every file the check depends on with
`--file`: they are hashed into the record, so a later edit invalidates the green. A spike whose timestamp precedes its research is **rejected**: a
passing spike over an unresearched claim is a green light on an unexamined assumption.

**After a formatter, re-gate instead of re-typing (ADR-29).** `cargo fmt`, `ruff format`, `prettier`
and friends rewrite bytes, so every spike touching a reformatted file legitimately goes stale. That
detection is correct and stays — a digest that ignored whitespace would be worthless, since
whitespace is semantic in Python, YAML, Makefiles and string literals. What is automated is the
*recovery*:

Call the active host's `build_regate_requests(...)` and dispatch every returned request through
`BridgeTransport`.

It re-runs **only** the stale spikes, using the command each record already stores, at the same
sample count. This is not a way to bless a stale record: every spike is re-executed and its verdict
comes from a fresh exit code, so a re-gate can discover that the formatting pass broke something —
and it exits nonzero when one now fails. Run your formatter, then re-gate, then read the report.

Design the smallest check that **could fail**, and confirm it can by breaking it on purpose. A
check that passes both ways proves nothing.

**One sample is not a verdict — use `--repeat N`.** If the check is not obviously a pure function
of the tree (a property test with an unseeded generator, anything touching time, ordering,
concurrency, the network, or a hash seed), a single run can pass by luck and that one green record
then approves the claim for the rest of the run. `--repeat N` runs it N times and passes only if
**every** run exits 0. Repeating is conjunctive on purpose — a majority rule would let a known-flaky
check approve a claim, which is the property the flag exists to detect. Ask yourself which of your
checks would survive `--repeat 10`, and repeat that one.

The record carries `samples` (how many runs) and `exit_codes` (the status of each, in order), so a
reader can tell 5 clean passes from 4 passes after a failure. A timeout has no exit code and appears
as `null` rather than a fabricated number. Verify with:

Inspect the returned/stored evidence through the knowledge adapter; do not read a runtime file.

**And be suspicious of your own falsification control.** Building ADR-24 produced the sharpest
lesson in this plugin's history: a hand-written list of sabotages was falsified by four consecutive
independent audits, each finding a *different* mutation of the *same* property the previous fix had
just covered. A curated list's coverage is exactly its author's imagination, and you are the worst
placed person to enumerate your own blind spots. Two escalations followed, and both are reusable:

1. **Generate the mutations, don't curate them** — enumerate mechanically from the code, so coverage
   is not a function of what you thought to write down.
2. **Make survivors prove themselves.** A mutation that survives your suite is either an unguarded
   behaviour or genuinely harmless — and "genuinely harmless" must be *demonstrated by execution*,
   not argued in a comment. The audit that broke escalation 1 found four written excuses to be
   factually false. This is just ADR-13 again: the exit code approves, never the author's confidence.

### Grade: approve, block, or DISCARD

- evidence **supports** (and the spike passed) → confidence ≥ θ → **approved**
- evidence **refutes** → **discard** the claim: set `refuted_by` to the refuting evidence id.
  The node and its sub-goals are pruned. A refuted claim is a dead node, **not** a weak one —
  parking it at low confidence is a failure the auditor flags. A discard requires real refuting
  evidence, or it would be the cheapest bypass of all.
- evidence **absent or inconclusive** → stays sub-θ, stays open, loop.

## Budget — the loop is spawn-bounded, and the harness ENFORCES it (ADR-17)

The currency is **subagent spawns, not tokens** — that is what can be both counted truthfully
and *denied*. (Verified: a `PreToolUse` hook can deny an `Agent` spawn; actual token spend is
not readable mid-session by any hook. A token budget would be advisory theater.)

Set `max_spawns` in `StartRun` (or `ObserveAction(configure_budget)`) through the adapter API. The
spawn gate denies any spawn past the cap — exit 2, refused. Fan-out is a **budgeted exception**:
spawn in parallel only when claims are genuinely independent and breadth is real.

| Order | Action | Cost | Gated by |
|-------|--------|------|----------|
| 1 | deterministic gate (`spike_harness.py`) | ~free (subprocess) | **never** — it is the trust boundary |
| 2 | one research/spike agent | 1 spawn | spawn gate |
| 3 | fan-out (N agents) | N spawns | spawn gate, per spawn; independent **and** breadth-bound only |
| 4 | `/think` escalation | 1 spawn | spawn gate + stall detected |
| 5 | **independent audit (required)** | 1 spawn | spawn gate — budget for this one; convergence needs it |

**Reserve a spawn for the auditor.** The audit is mandatory (Step 5), so a run that spends its
last spawn elsewhere cannot converge and will terminate as `stopped_residual`.

**Exhaustion never fabricates convergence.** When the cap denies a spawn and claims remain
sub-θ, tag them `"blocked": "needs-budget"`. The gate then allows the stop but reports
`converged: false` — an honest "did not converge, budget exhausted." To continue, raise
`max_spawns` **and re-invoke `/empirica`**: once a run reaches a terminal status the gate stops
gating it entirely (by design — it must never re-block a finished run), so raising the cap alone
does not restart the loop.

**Two independent bounds, both enforced.** `max_spawns` bounds fan-out cost; the pass budget
bounds loop length. **Do not set `max_passes` a priori** — the run derives its own working budget
from the seeded claim graph (`working_passes = open_claims + 2`, for the audit round and the
finalize check) and **re-derives it as the graph grows**, so a bigger goal earns a bigger budget
without you touching a number (ADR-34). `max_passes` is now the a-priori *ceiling* the working
budget can never exceed; it is raised only by a human via `configure_budget`, never by the run
(ADR-19/28). `EMPIRICA_MAX_PASSES` sets the ceiling floor. A run that never converges terminates
regardless of budget.

**A pass counts knowledge progress, not turn-ends — so waiting on the auditor is safe.** A stop
only spends a pass when the knowledge actually changed (new evidence, verdict, or claim). Idle
stops while a dispatched auditor runs cost **zero** passes, so an 825s audit no longer burns the
budget out from under a converging run (ADR-34). Idle waiting is bounded instead by a **wall-clock
stall deadline** (`EMPIRICA_STALL_DEADLINE_SEC`, default 1800s): if no knowledge progress lands for
that long, the run terminates `stopped_residual`. The auditor writing its verdict *is* progress —
it resets the deadline, and the run converges on the next stop. A verdict that lands even after the
run has terminated is still recorded, so the audit's work is never wasted.

## Step 4 — Assessor: one convergence pass, then end your turn

The Assessor is the fixed-point function `f` (ADR-7). One pass does exactly three things:

1. **Update confidences** from the evidence just gained — never from how convinced you feel.
2. **Derive child claims.** Resolving one claim reveals others. **Specialize only** (ADR-9): a
   derived claim must be strictly narrower than its parent. Widening or restating is a failure
   the auditor flags. *Not machine-checked* — no hook compares a child's scope to its parent's,
   so this is your discipline plus the auditor's review. What IS enforced is termination, by the
   pass counter (`max_passes − passes`), independently of whether derivation specialises.
3. **Discard refuted nodes**, recording the refutation.

Write the graph and **end your turn**. The Stop hook reads it: any open claim on the path to the
goal blocks, and the block message tells you *which evidence fold each claim still owes*. Across
compaction, `SessionStart:compact` re-injects the graph — including the missing folds — so the
loop is durable-resumable (ADR-8/9).

**Do not hand-declare convergence.** Convergence is what the gate says, not what you assert.

**Stall detection (ADR-17) — your judgment, not a hook.** If several consecutive passes derive
no narrower claim and move nothing across θ, the loop is **stalled**, not converging. Escalate
once to `/think` (budget permitting); if it stays stuck, surface the claim as a `blocked:`
residual. **Nothing detects this for you** — no code compares passes to spot a stall, so it is a
self-check. The hard backstop is termination itself: a stall that keeps growing scope but never
converges grinds out the working budget up to the fixed ceiling and reports `stopped_budget`
(needs-budget — the ceiling is raised only by a human via `configure_budget`, never by the run),
while one that produces no knowledge at all trips an idle backstop (a consecutive-no-progress stop
count, or the wall-clock stall deadline when a host clock is present) and reports `stopped_residual`
(ADR-34) — either way it terminates, so a missed stall costs time or passes, never correctness.

### Stop discovering and start closing — `--freeze` (ADR-26)

Discovery is the point of the loop, and that is exactly why it needs an off switch. Nothing in the
gate ever tells you to stop finding things: every pass can legitimately derive new claims, each new
claim owes Fold 1, and the block message only ever offers *resolve* or *blocked:*. A run that keeps
finding real things therefore has one terminal path — grind to `max_passes` and report
`stopped_residual`. That is honest, but it is not finishing.

Freeze is the third exit. It commits the run's **scope**:

Submit `ObserveAction(kind="freeze", claims=[...])` through `BridgeTransport`; the application
makes the first freeze authoritative.

The claims **already gating at that moment** become the set this run must discharge. Claims derived
afterwards are **deferred**: they do not gate, they are reported in the result by id, and they are
handed to the next run as an honest open-items list. The run then stops as soon as its committed set
is terminal, with status `stopped_frozen` and `converged: false` — closed on a declared scope, which
is neither convergence nor an exhausted loop.

**Freeze when the design has stopped changing and the remaining findings are refinements** — the
signal is passes that produce new claims *about* the design rather than evidence *for* it. This is
not stall detection: a stall is unproductive passes; this is productive passes with no ending.

Two things make it a commitment rather than an escape, and you should know both:

- **First write wins.** You cannot re-freeze to enlarge or replace the set, exactly as you cannot
  re-stamp your route. Freezing early does not buy a pass with less work — only with less scope,
  declared up front, with every omission printed in the result.
- **The auditor judges the deferral.** The freeze set and the deferred list go into the audit, and
  rubric item 8 (*the claim set covers the intent*) is evaluated against the **frozen** set. A
  freeze that carved out the intent's core is a **FAIL**. A frozen run still owes a passing audit —
  it is asserting it discharged what it took on, and that assertion gets certified like any other.

Freeze changes no bound: `max_passes` and `max_spawns` are untouched, and a frozen run that cannot
discharge its committed set still terminates at the cap as `stopped_residual`.

## Step 5 — Independent audit BEFORE converged (P6, mandatory)

A converged claim graph is **necessary but not sufficient**. Spawn the auditor:

```
Agent(subagent_type="empirica:empirica-auditor", ...)
```

**The `empirica:` prefix is load-bearing — do not drop it.** A plugin-provided subagent resolves
only under its plugin-scoped name; the bare `empirica-auditor` raises "Agent type not found",
so the spawn never happens, no audit ticket is written, and the run can never converge. This is
the same namespacing trap that once made the skill's own `UserPromptExpansion` matcher silently
never fire (it must be `^empirica:empirica$`). If the spawn errors with "not found" in a session
where this plugin was just installed or updated, reload plugins or restart the session — a stale
session registry can fail to resolve a newly added agent even when the name is correct.

On Codex, plugin bundles do not contribute Claude's `agents/` definitions. Use the native
`spawn_agent` tool and put the literal `empirica-auditor` marker in its dispatcher-visible
`agent_type`, `name`, `task_name`, or `message`; the trusted `PreToolUse:Agent` hook issues the
ticket. This witnesses a requested spawn, not actor identity or independence. If the audit requires
decorrelated model generations, use a witnessed CLI dispatch with an explicit model in `cli_exec`
mode and report any independence not actually obtained.

Pass it the opaque run handle and the **nonce** the spawn gate issued. It verifies the run against
the ADR-20 rubric — above all **re-reading each approved claim's Fold-1 citation to confirm the
cited source actually supports the claim** — and submits its verdict with
`build_audit_verdict_request` through `BridgeTransport`. The Stop gate
requires a `pass` verdict whose nonce matches a real auditor spawn and whose `claims_reviewed`
covers **every** approved claim.

**The audit is incremental, per claim (ADR-25).** Each `claims_reviewed` entry is
`{claim_id, claim_digest, evidence_digest}` — the digests the claim had when the auditor read it.
The gate recomputes both from disk, so:

| What changed since the audit | Effect |
|---|---|
| nothing on this claim | stays reviewed — **no re-review needed** |
| the claim was reworded | that claim un-reviews (`claim_digest` moved) |
| its evidence was swapped, re-recorded, or **contradicted** | that claim un-reviews (`evidence_digest` moved) |
| a claim was added, deleted, detached, re-parented, blocked or discarded | the whole verdict's shape match fails (`argument_digest` moved) |
| a confidence changed on an already-approved claim | nothing — the claim was approved before and after |

The verdict also records an `argument_digest` over the argument's **shape** (ADR-27). Per-claim
digests are keyed per *surviving* claim, so they cannot see a claim **leaving** the gated set —
without this, detaching or deleting a blocking claim let a verdict written before that claim existed
read as full coverage, and the run converged. When the shape changes you must re-confirm coverage,
but the per-claim entries for untouched claims stay valid: re-issue the coverage, don't re-read
everything.

A failing audit therefore no longer costs you the whole graph: fix what it found, re-audit **those**
claims, and the untouched ones stay covered. The block message names exactly which claims are
unreviewed, reworded, or re-evidenced. Both digests come from `evidence.py` and the auditor calls
those functions rather than hashing by hand — one definition, so the writer and the checker cannot
drift. There is **no backwards compatibility**: a verdict listing bare claim-id strings reads as
absent, and absent blocks.

**You cannot satisfy this by writing the verdict yourself.** The author grading its own work is
the failure P6 exists to close (ADR-13; moai-adk's `plan-auditor` split). If the audit fails,
fix what it found and loop — a failing audit is a successful workflow, not a setback.

**Actor routing (ADR-24, superseding ADR-23's tier primitive):** roles bind to **concrete model
generations** in the agent definitions under `agents/` — the auditor is deliberately a *different
generation from the author*, not merely a different tier. A tier collapses the one property a
second model is worth having: two models in one cost class have different blind spots, and what
an independent audit buys is **decorrelated error**. Model ids still live only in the agent
definitions (config), never in workflow logic, so a rename never touches this skill.

A claim may name its own actor when it needs a specific one:

```json
"G4": {"type": "Goal", "kind": "needs-experiment",
       "actor": {"model": "<a concrete model id>", "harness": "claude-code"}}
```

Take the id from the agent definitions or from `make doctor` — this skill deliberately names no
model, so that a model rename never edits the workflow.

Optional and additive — a claim without an `actor` resolves exactly as before. **Attribution comes
from the dispatcher, never the actor:** asked to name itself, a model pinned to one identifier
reported a different one, three times over. So `spawn_gate.py` records the auditor's model from its
definition, and the Stop gate reports whether independence was actually obtained. Those reports do
not block (§3.3) — an in-session attribution is *declared*, not witnessed, and a declared signal
must never be the sole reason a run fails closed.

`fable` is **excluded by policy** (30-day content retention at the vendor), and the exclusion is
enforced in `actors.py`, not left to discipline.

**Two optional modes, both OFF by default.** `multi_provider` allows actors outside this harness
(`codex`, `pi`) — while off they are not even probed; `cli_exec` dispatches actors as subprocesses,
which buys *witnessed* attribution and is charged to the same ADR-17 spawn ledger. A bare Claude
Code + python3 install behaves exactly as 0.4.x does, and that is the point.

Set them **at invocation** (ADR-28) — this is the normal path:

```
/empirica --cli-exec design the retry policy
/empirica --cli-exec --multi-provider spike the parser
/empirica --no-cli-exec resume the audit      # force a mode OFF for one run
```

Precedence, most specific first — unchanged from ADR-24, with the flags landing at the file layer:

| Source | Wins over | Use when |
|---|---|---|
| `EMPIRICA_MODE_*` env | everything | a Makefile target or CI job must force a mode regardless of what was typed |
| invocation flags → `StartRun.modes` | the default | per-run, recorded in `~/.empirica-plugin/`, visible through `RestoreRun` |
| off | — | the default everyone gets |

An env var still beats a flag, deliberately: an operator overriding one run from the outside must
not be silently countermanded by whatever the invocation said.

The preflight `empirica doctor` **spends no inference** — version and config reads only — never
gates the baseline, writes no worktree runtime file, and only *recommends*:
"available" is not "permitted", and it will not reassign a claim for you. Run it yourself with
`make doctor`.

## Step 6 — Finalizer: escalate to /think at the confluence

Both paths meet here. This is the expensive tier — use `/think` (methodologist, the required
companion, ADR-3/12/13) **conservatively**: when the design is high-stakes, the loop stalled, or
invariants are genuinely ambiguous. Not at every step.

Produce the committable output the intent demanded (ADR-14):
- the **goal's resolved deliverable** — code, a document, a review, a design — placed where the
  intent dictates. This is what the run exists to produce.
- **ADRs** (via the `adrs` CLI, MADR format) when the intent is a decision, with rejected
  alternatives.

The claim graph, evidence store, manifest, ledger, audit artifacts and `/think` traces are the
run's internal memory: they stay in the run directory and are **never committed**.

## Step 7 — Handoff

With convergence reached *and the audit passed*, hand off the goal's output. When that output is
code, tests and code are the committable result; the deterministic suite is the machine-checkable
spec and the trust boundary (ADR-13) — "done" is when the gates are green, not when it looks right.

Non-convergence at `max_passes` is reported honestly as `stopped_residual`, never dressed up as
green (ADR-17). A run that closed on a frozen scope reports `stopped_frozen` with its deferred
claims — a different outcome from an exhausted loop, and reported as such. Hand the deferred list
forward: it is the input to the next run, not a footnote.

## Data-model summary

| Tier | Artifacts | Home |
|---|---|---|
| **Operational** | status, phase, modes, spawn budget, audit tickets, graph pointer | `~/.empirica-plugin/` (or `$EMPIRICA_HOME`), accessed only through the API |
| **Knowledge** | claim graphs, in-toto evidence, audit verdicts, attribution | local Git shadow refs under `refs/empirica/*`, accessed only through the API |
| **Committable** (SSOT) | the goal's resolved output (code, document, review, …); ADRs when the intent is a decision; tests | git, at the intent's location |

## Rules

- Convergence is hook-enforced and gate-defined. Never assert "done" — let the gate decide.
- Research (Fold 1) comes first, for every claim. Recall is not evidence.
- Every spike gate is a real subprocess exit code, never model judgment (ADR-13). One sample is not
  a verdict — `--repeat N` any check that is not a pure function of the tree.
- Know when to stop discovering: `--freeze` commits the scope, defers later findings as reported
  open items, and closes the run on purpose instead of at the cap (ADR-26).
- Refuted claims are discarded, not parked at low confidence.
- The author never grades its own convergence — the audit is a distinct principal (ADR-20 P6).
- Derived claims specialize only, so the loop terminates (ADR-9).
- Standards over invention: GSN vocabulary, in-toto attestations, MADR for decisions — reference
  standards, do not vendor or invent schemas (ADR-22).
- Route by **actor identity**, not tier: a cost class does not buy decorrelated error (ADR-24).
  Model ids live in the agent definitions, never in workflow logic.
- **Never let an actor report its own identity** — attribution comes from whatever dispatched it,
  and an unwitnessed attribution is reported as *declared*, never as proven (ADR-24 §2).
- `/think` is the expensive tier — escalate on stall/ambiguity/high-stakes, not by reflex.
- methodologist is a required companion; inkrot `/hr` is forbidden (ADR-3).
