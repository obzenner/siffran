# Mission brief: the Obligation contract — Empirica's agent-facing product interface

Status: working brief for a multi-agent run. Orchestrator: the parent Pi session. Not committed.
Source issue: `empirica_agent_semantic_contracts_issue.docx` (repo root, untracked) — read it first.

## Stance (verbatim, every worker adopts it)

> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

Cite `file:line` for every statement about this repo. Cite a URL + the deciding passage for every
statement about an external standard or a host (Claude Code, Codex, Pi). A claim with no citation is
`UNVERIFIED` and must be labelled so. Recall is not evidence.

## Intent (the root claim, G0)

> A host-neutral, domain-neutral **Obligation contract** module exists in this repo, reusable by any
> plugin on Claude Code, Codex, and Pi; Empirica consumes it so that at every Empirica→agent boundary
> (Block, RestoreRun, adapter rendering, final handoff) the outstanding obligations and their discharge
> conditions are projected losslessly; and a deterministic verifier computes residual obligations from
> trusted observations. The Pi adapter is brought to parity with the documented workflow wherever a
> Pi capability exists, and every remaining gap is named as a gap, not papered over.

## The invariant we are building (from the issue)

```
At every Empirica -> agent boundary:
  obligations before == obligations visible after
  unless a trusted observation explicitly changed their state.
No agent may be required to infer an outstanding obligation from hidden state, history, counts, or prose.
No obligation may exist only in model context.
```

A regression test FAILS if an obligation disappears, becomes less specific, loses its discharge
condition, or becomes recoverable only by model inference.

## Layer model (keep these separate — this is the design)

```
Claim graph / evidence   "What do we know, and why may we believe it?"      (Empirica-specific, exists)
        |
        v
Obligations              "What must the next actor cause/preserve/avoid/show?" (GENERIC — the new module)
        |
        v
Observations / witnesses "What actually happened?"                            (GENERIC data; sources are host/domain specific)
        |
        v
Deterministic verifier   "Which obligations remain?"                          (GENERIC — the new module)
```

The generic module knows NOTHING about GSN, claims, folds, hooks, exit codes, paths, or hosts. It
receives an opaque `because[]` provenance list and an injected `trusted(observation) -> bool`
predicate. Empirica supplies a thin projection `ClaimReason/graph -> Obligation` and an
`evidence/spike/audit artifact -> Observation` mapping.

## Verified starting facts (main @ e9e84b3) — do not re-derive, cite these

| Fact | Where |
|---|---|
| Core computes per-claim remediation as `ClaimReason(claim_id, text, confidence, reason)` on `Block.open_claims` | `plugins/empirica/core/convergence.py:111-124`, `core/decisions.py:44-51,83-97` |
| Application discards it: every Block path calls `wire.block(decision.reason, run)` | `plugins/empirica/application/service.py:869-957` |
| Wire collapses Block to `{type, reason, run}` | `plugins/empirica/application/wire.py:195-196` |
| RestoreRun projects the graph to four integers | `plugins/empirica/application/service.py:244-267` |
| Claude restore renders that snapshot and says "continue resolving the application-reported open work" | `plugins/empirica/adapters/claude/restore.py:45-70` |
| Claude Stop renders only `reason` to stderr | `plugins/empirica/adapters/claude/completion.py:70-100` |
| Codex renders only `reason` | `plugins/empirica/adapters/codex/lifecycle.py:300-301,334-337,362-367` |
| Pi renders only `reason` | `plugins/empirica/adapters/pi/src/translate.ts:103,132,183` |
| Response schema `block` allows additionalProperties (additive change is legal within v1) | `contracts/empirica/v1/response.schema.json` `$defs.block` |
| Test `RS2` locks in counts-only restore | `plugins/empirica/tests/test_application.py:1228-1236` |
| SKILL.md promises "re-injects the graph — including the missing folds" | `plugins/empirica/skills/empirica/SKILL.md:363-366` |
| Pi `/empirica` does not parse mode flags; goal = `args.trim()` | `plugins/empirica/adapters/pi/src/index.ts:111` |
| Pi holds the run handle only in extension memory; the model never receives it | `plugins/empirica/adapters/pi/src/index.ts:99-118` |
| Pi gates tool `report_convergence` but registers no such tool | `plugins/empirica/adapters/pi/src/index.ts:80,166-186` |
| Pi has no route stamp, no spawn gate/audit ticket, no RestoreRun on compaction, no knowledge submission path | `plugins/empirica/adapters/pi/src/index.ts` (absence), `SKILL.md` "Runtime boundary" names only claude/codex |
| Distribution constraint: Claude/Codex install `./plugins/<name>` as plugin root; code outside it is not shipped. Pi installs the repo root package | `.claude-plugin/marketplace.json`, `plugins/empirica/hooks/hooks.json` (`${CLAUDE_PLUGIN_ROOT}`), `package.json` `pi.extensions` |
| Repo rule: lifecycle ops live in `make` targets with `## help`; validators are real files in `scripts/`; `make check` must be green | `CLAUDE.md`, `Makefile` |
| Existing host-neutral pattern to copy: `contracts/<proto>/v1/*.schema.json` + fixtures + a pure `core/` package with injected oracles | ADR-30 `doc/adr/0030-*.md`, `plugins/empirica/core/__init__.py` |

## Placement decision (taken by the orchestrator; workers implement, do not re-litigate)

Option **B**: `lib/obligations/` (stdlib-only Python, frozen dataclasses, no I/O — the source of truth) +
`contracts/obligations/v1/*.schema.json` + fixtures, **vendored byte-identical** into
`plugins/empirica/vendor/obligations/` and enforced by a new `make vendor-check` (script in `scripts/`,
wired into `make check`). A TS type mirror lives in `plugins/empirica/adapters/pi/src/obligations.ts`
and is validated against the same fixtures. Reusability is a *verified* property, not an intention.

## v1 scope of the generic module (frozen — additions go to a v2 note, not into this run)

- `Obligation{id, must, must_not?, witnesses[], state, because[], severity?}`; `state ∈ {open, partial, discharged, violated, blocked, deferred}`.
- `Witness{kind, ref, expect}`; `kind ∈ {test, exit_code, event, artifact, predicate, judgment}`. Exact
  match on `(kind, ref)` — NO predicate language / evaluator in v1. `judgment` is the only kind an
  actor's opinion may discharge, and a `judgment` observation never discharges a machine-kind witness.
- `Observation{kind, ref, outcome, source, at, payload?}` with `outcome ∈ {pass, fail}`.
- `Contract{contract_id, revision, parent_revision?, obligations[], provenance}`; revisions are
  append-only; `revise()` returns a new revision with explicit `supersedes`; nothing mutates in place.
- `verify(contract, observations, trusted) -> Verdict{satisfied[], violated[], residual[], unwitnessed[]}`
  — pure, deterministic, order-independent over observations; `must_not` + observed `pass` = violated.
- `project(contract, verdict) -> agent-facing view` — the ONE shape every boundary emits; and
  `preserved(before_view, after_view) -> bool | reasons` — the property the regression tests assert.
- Schemas + substrate-neutral fixtures under `contracts/obligations/v1/`; Python and TS tests both consume the fixtures.

## Empirica consumption (v1)

1. `plugins/empirica/core/obligations.py`: `ClaimReason`/graph → `Obligation` (witness = the missing fold:
   `artifact:research/<claim_id>`, `exit_code:spike/<claim_id>`, `judgment:audit/<claim_id>` …). Keep
   claim ids as `because`. Deterministic, tested.
2. `wire.block(reason, run, obligations=[...])` and every `_finalize_block` path passes them.
3. `RestoreRun` snapshot gains `obligations: [...]` (counts stay as telemetry). Fix RS2 to assert both.
4. Adapters render obligations: Claude Stop stderr (after the prose), Claude/Codex restore (already dump
   the snapshot → verify lossless), Codex deny reasons, Pi `translate.ts` notices + follow-up nudge.
5. Final handoff: on a terminal `Allow`, the run view carries the final contract revision (deliverable
   obligations + residuals). Persist the contract as an `Artifact` in the existing `ArtifactRepository`.
6. End-to-end regression: core decision → service → wire → each adapter render → RestoreRun after a
   simulated compaction. Assert `preserved()` at every hop. Also: a test that FAILS if `open_claims`
   is dropped again (mutation-style falsification).
7. SKILL.md resume-contract text made exactly true; a Pi "Runtime boundary" paragraph added.
8. ADR `0039` (MADR via `adrs` CLI) — the obligation contract as the product interface; ADR `0040` — Pi
   adapter parity and named gaps. `make bump PLUGIN=empirica PART=minor`.

## Pi parity (v1) — implement what Pi can do, name what it cannot

Implement: mode-flag parsing in `/empirica` (ADR-28); surface the run handle to the model (e.g. inject
via a system/context message and `/empirica-status`); register the gated `report_convergence` tool (or
rename the gate to a tool that exists); intercept subagent spawns on `tool_call` for the spawn budget +
audit ticket; dispatch `RestoreRun` on Pi's compaction/session-start event; render obligations in
every notice. Read the Pi extension API from
`/Users/dmitry.lambrianov/.nvm/versions/node/v24.15.0/lib/node_modules/@earendil-works/pi-coding-agent/docs/extensions.md`
(+ `compaction.md`, `sessions.md`) and cite it. Where Pi has no capability (e.g. a hard Stop veto),
record it as a named gap in ADR-0040 and in SKILL.md — never as an implied enforcement.

## Worker boundaries (one writer per worktree; the parent merges)

| Lane | Agent | Writes | Reads | Deliverable |
|---|---|---|---|---|
| **A. Contract + lib** | Sol | `lib/obligations/**`, `contracts/obligations/**`, `scripts/validate_obligations.py`, `scripts/check_vendor.py`, `Makefile` targets | issue doc, this brief, ADR-30/31, `core/decisions.py`, `core/convergence.py` | the generic module, schemas, fixtures, tests; `make check` green |
| **B. Empirica consumption (Python)** | Terra | `plugins/empirica/core/obligations.py`, `application/**`, `adapters/claude/**`, `adapters/codex/**`, `plugins/empirica/tests/**`, `contracts/empirica/v1/**`, `SKILL.md`, ADR-0039 | Lane A output (vendored), the verified-facts table | lossless Block/Restore/handoff on Claude+Codex, e2e regression, RS2 fixed |
| **C. Pi parity (TypeScript)** | Luna | `plugins/empirica/adapters/pi/**`, `SKILL.md` Pi paragraph, ADR-0040 | Pi docs, Lane A schemas/fixtures, `translate.ts`, `index.ts` | Pi adapter renders obligations; parity items implemented; gaps named |
| **D. Independent audit** | GLM (different vendor — decorrelated error, ADR-24) | nothing (read-only) | everything | per-claim PASS/FAIL with evidence; attempts to falsify the regression tests by mutation |
| **Orchestrator** | parent | this brief, merges, ADR sign-off | worker outputs | adjudication, freeze, final report; never commits without the human |

Rules for every lane:
- Do not touch files outside your lane. If you need a change elsewhere, write it as a **request** in
  your report with the exact diff, and stop.
- Tests are the spec. Write the failing test first; the deterministic check is the only approver
  (ADR-13). `make check` must be green at the end of your lane; say so with the command output.
- Report in this shape: **Claims established** (id, statement, evidence), **Claims refuted**,
  **Open/blocked** (with what was tried), **Files changed**, **Requests to other lanes**, **Residual risks**.
- No commits, no pushes, no branch changes. The parent owns git.

## Non-goals (v1)

Replacing the claim graph or GSN; removing the auditor; a predicate/evaluator language; making every
semantic statement decidable; prescribing agent trajectories; exposing internal evidence machinery
downstream; a generic eval platform; a hard Stop veto on Pi if the host does not offer one.
