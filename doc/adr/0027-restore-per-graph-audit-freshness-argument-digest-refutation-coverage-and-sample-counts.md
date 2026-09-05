---
number: 27
title: "Restore per-graph audit freshness: argument digest, refutation coverage, and sample counts"
status: proposed
date: 2026-08-11
tags:
  - verification
  - audit
  - harness
  - evidence
  - convergence
links:
  - target: 25
    kind: Amends
  - target: 26
    kind: Amends
  - target: 20
    kind: Depends on
  - target: 21
    kind: relatesto
  - target: 13
    kind: relatesto
---

# Restore per-graph audit freshness: argument digest, refutation coverage, and sample counts

**Status note:** ADR-25 and ADR-26 shipped in plugin 0.6.0 and were merged. An adversarial review
with fresh context then found five defects in that release, three of them breaking a guarantee those
ADRs explicitly claim. This record corrects them. It does not edit ADR-25 or ADR-26 — those stand as
written, including the sentences this record shows to be false, because an ADR is immutable and the
error is part of the history worth keeping.

## Context and Problem Statement

ADR-25 replaced a per-graph audit freshness check (the ticket's `pass` number, compared against the
run's current pass) with per-claim digests over each approved claim's text and evidence. It asserted
the counter's *"only remaining behaviour is the false positive in driver 2"* and that it was *"fully
subsumed by a direct measurement"* (ADR-25 §Decision Outcome). It also committed to **"No weakening
of coverage… Any change must keep that exactly"** (ADR-25 §Decision Drivers).

Both claims were wrong, and the second was violated. Five findings, each with a reproduction:

**1. A verdict can certify a graph state that never existed.** Per-claim digests are keyed *per
surviving claim*, computed from the current graph. A claim that no longer exists contributes no key,
so its absence is indistinguishable from its never having existed. Sequence: audit passes at pass 1;
a real claim `G2` is derived and blocks at pass 2; `G2` is then removed — by refutation, by deletion,
or by **detaching it from the root** (drop its `SupportedBy` edge, leave the node in the file). The
approved set is now byte-identical to what the pass-1 verdict covered, so the run **converged** on a
verdict written before `G2` existed. The auditor never saw the claim, nor the decision that removed
it. Verified to be blocked by the pre-0.6.0 code and to succeed on 0.6.0 — a genuine regression, not
a pre-existing hole.

The full blind-spot set, all of which left both digests unmoved: `confidence` changes, a `kind` flip
that re-approves a claim whose spike went stale, `refuted_by` removal (DISCARDED → APPROVED), a
**refuting leaf added to an approved claim**, edge changes, and claim deletion.

**2. Freeze bypasses the audit entirely with one `blocked:` tag.** `blocked` was computed over *all*
gating goals including deferred ones, then `audit_owed = not blocked`. So a single
`blocked: needs-decision` on a **post-freeze** claim — a claim the freeze had already set aside, and
which need not carry real work since a post-freeze node is free to write and never gates — made the
run exempt. It reported *"discharged the scope it committed to at freeze time"*, status
`stopped_frozen`, with **zero** independent review. ADR-26 named the auditor as one of *"Two guards
[that] make that hold rather than merely sound plausible"* and said freeze *"moves a scope judgement
from 'impossible' to 'reviewable'"*. It was not reviewable either. The gate's own docstring stated
the correct intent (*"A frozen run is NOT exempt"*); the code contradicted it.

**3. A `--repeat`-backed spike is indistinguishable from a single-sample one on disk.** `runs` and
`repeat` went into the CLI payload and never into the persisted predicate. `command_hash` is over the
inner argv, so it does not encode the repeat either. A 20-sample and a 1-sample spike produced
identical predicates apart from an opaque hash, so no gate, auditor, or human could tell which claims
rested on one lucky exit code — which is the flag's entire premise.

**4 and 5. Two documented invariants had no test that could fail.** `manifest.freeze`'s
first-write-wins guard is shadowed by an identical guard in the CLI wrapper, so every test
short-circuited before reaching it: deleting the manifest-level guard — the one whose docstring
carries the whole anti-bypass argument — left the suite fully green. Likewise the gate's comment that
*"a deferred claim still counts for the audit… deferral suppresses GATING, not existence"* was
unpinned; dropping deferred claims from the audit-approved set was invisible to the suite.

## Decision Drivers

* **Restore what was lost without restoring the false positive.** The pass counter's failure mode was
  firing on the compliant fix-and-loop rhythm. Whatever replaces it must not.
* **Measure the thing, not a proxy.** ADR-25's instinct was right; its coverage was incomplete.
* **A guard no test can turn red is not a guard** — the repo's own `check()` docstring says so.
* **Additive at the evidence layer.** A new predicate field must not shift existing digests.
* **Determinism, fail-closed, no overclaiming** (ADR-19, ADR-21) — unchanged.

## Considered Options

* **A — An `argument_digest` over the graph's shape, plus including refutations in
  `evidence_digest`, plus a persisted `samples` count.**
* **B — Reinstate the pass counter alongside the digests** (the reviewer's first suggestion).
* **C — A per-claim `state_digest` over each claim's graph fields plus a `gating_set_digest`** (the
  reviewer's refined suggestion).
* **D — Fold `confidence` into `evidence_digest`.**

## Decision Outcome

Chosen option: **A.**

**1. `claimgraph.argument_digest(graph)`** — sha256 over the argument's *shape*: root, every node's
`type`/`kind`/`blocked`/`refuted_by`, and the sorted `SupportedBy` edges. An audit verdict records
it; `audit.check` requires it to match the graph on disk. This covers what per-claim digests
structurally cannot — **the set of claims and how they hang together** — so adding, deleting,
detaching, re-parenting, blocking, or discarding a claim invalidates the verdict.

It deliberately **excludes `confidence`**, which moves constantly during a normal loop and is already
covered per claim by the state derivation. Folding it in would re-create ADR-25's false positive at
graph scope, which is the bug this whole line of work exists to remove. `InContextOf` edges are
excluded too: context is not a claim to adjudicate and does not extend the gated path.

The residual is deliberate and stated plainly: **a `confidence` change on an already-approved claim
does not un-review it.** The claim was approved before and is approved after; nothing the auditor
judged changed. That is the one row of the reviewer's table this design accepts.

**2. Refuting leaves are included in `evidence_digest`.** Excluding them had it backwards. Evidence
that *contradicts* an approved, already-reviewed claim is among the most important things an auditor
could be asked to look at again, and while refutations were filtered out, adding one moved no digest.

**3. `samples` in the spike predicate.** Additive, defaults to 1, and a leaf without it reads as 1 —
absence means one run, not "unknown".

**4. `blocked` is computed over non-deferred goals only.** A tag on a claim the freeze set aside
cannot decide anything about the scope the run asserts it discharged.

**5. Both unpinned invariants get tests that fail on sabotage**, verified by reverting each fix.

**Why not B.** The counter fires on compliant behaviour — it is the defect ADR-25 correctly
identified. Reinstating it alongside the digests would restore detection by restoring the false
positive, trading one real problem for another.

**Why not C.** It is close to right and its `gating_set_digest` half is essentially `argument_digest`
in weaker form: a set digest catches membership changes but not **re-parenting**, since detaching a
claim and re-attaching it under a different parent leaves the set identical while changing what the
argument claims. Its per-claim `state_digest` half also folds in `confidence`, which reintroduces the
false positive per claim. Option A takes the structural half and makes it complete.

**Why not D.** Same objection at the leaf layer, and worse: it would make every confidence update
during a normal loop invalidate the claim's evidence digest, so no incremental audit could ever
settle.

### Consequences

* Good, because the guarantee ADR-25 promised and broke is restored, and by direct measurement of
  the argument rather than by a proxy that punishes compliance.
* Good, because a refutation arriving after an audit now un-reviews the claim it contradicts — a hole
  that predated ADR-25's per-claim scheme and that the coarse counter only caught incidentally.
* Good, because the evidence store now says how many samples backed each spike, so `--repeat`'s
  benefit is legible to the auditor and to a human, not just to the process that ran it.
* Good, because two guards that could not fail now can, and each fix's test was verified red on
  revert.
* Bad, because a verdict now carries three digests plus a graph digest, and the auditor's contract
  grows again. All four come from functions in the plugin that the auditor calls; none is
  hand-computed.
* Bad, because any structural change to the graph — including one that only adds a claim — now
  invalidates the *whole* verdict's shape match, so the auditor must re-confirm coverage even where
  per-claim digests are unchanged. This is a smaller cost than it appears: the per-claim entries for
  unchanged claims remain valid and need no re-reading, so the work is re-issuing coverage, not
  re-auditing 22 claims. It is the honest price of catching claim disappearance.
* Neutral, because forgery resistance is unchanged: an author that hand-writes a verdict can compute
  every digest from the run directory. This record sharpens staleness only.

### Confirmation

Executable regressions now live in `plugins/empirica/tests/test_core.py`,
`plugins/empirica/tests/test_application.py`, and the Claude activation lifecycle test (`make test`);
each is verified to fail when its fix is reverted:

1. Detaching a blocking claim does not launder an old verdict; the message says the *argument*
   changed (R52–R53). Re-auditing the new shape converges it (R54).
2. `argument_digest` reacts to detach, add, block, discard, `kind`, and re-parenting — and **not** to
   `confidence` (R55–R61). A verdict with no `argument_digest` is refused (R62–R64).
3. A refuting leaf moves the evidence digest and un-reviews the claim (R65–R67).
4. A deferred `blocked:` tag does not exempt the run from its audit (Z27–Z28), while a tag inside the
   frozen set still does (Z29–Z30).
5. `manifest.freeze` refuses to rewrite a commitment when called directly (Z19–Z21).
6. An approved-but-deferred claim must still be covered by the verdict (Z22–Z25).
7. The spike predicate records `samples`, and a leaf without the field reads as 1 (Q33–Q35).

## More Information

Found by an adversarial review run with fresh context against the merged 0.6.0 diff, given the two
ADRs as the claims to attack and required to supply a runnable reproduction per finding. It also
confirmed what held: freeze's park-then-restore interaction, vacuous freeze, corrupt-freeze fail
direction, `--repeat` conjunctivity and clamping, `_result_hash` stability, the option-B regression
tests, and the legacy flat-verdict refusal.

The transferable lesson is the one ADR-24's build already recorded and this release re-learned: a
control that becomes more *precise* must be checked for what its precision drops. ADR-25 argued the
deleted counter was fully subsumed and did not enumerate what it caught; one afternoon of adversarial
attention produced the counter-example. The enumeration belongs in the ADR that removes a control,
not in the review that follows it.

Deliberately not decided here: whether the auditor's rubric should flag a `needs-experiment` claim
approved by a single sample. `samples` now makes that checkable, but what threshold is right needs
evidence from real runs.
