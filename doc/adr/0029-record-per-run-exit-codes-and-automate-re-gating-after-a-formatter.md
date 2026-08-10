---
number: 29
title: "Record per-run exit codes and automate re-gating after a formatter"
status: accepted
date: 2026-08-11
tags:
  - evidence
  - verification
  - usability
  - harness
links:
  - target: 27
    kind: Amends
  - target: 13
    kind: Depends on
  - target: 21
    kind: relatesto
---

# Record per-run exit codes and automate re-gating after a formatter

## Context and Problem Statement

Two findings from an agent that ran empirica on a Rust project. They are independent, but both concern
Fold-2 records, so they are recorded together.

**1. The documentation oversold the record, and the doc was mine.** ADR-27 added `--repeat N` and a
`samples` field. The skill then claimed *"the per-run exit codes go into the record."* They did not.
`spike_harness.run_gate_repeated` computes a `runs` list carrying each run's `returncode`, and
`_record_evidence` passed only `len(runs)` as `samples` — the codes were discarded. The agent probed
for `exit_codes`, `exits`, and every field containing exit/code/run, found only `samples`, and
correctly reported that either the doc oversells or the field is named something else.

It was the doc. This is precisely the failure mode ADR-21 named and this plugin exists to prevent: a
claim about evidence that the evidence does not support. `samples: 5` says the check ran five times
and says nothing about what happened, so a reader cannot distinguish five clean passes from four
passes after a failure.

**2. `files_hash` friction is real, and the detection is correct.** The same agent reported
`cargo fmt` invalidating eight spike records, twice, forcing a re-gate loop — and was explicit that
*"each catch was legitimate."* That framing is the whole design constraint. A formatter rewrites
bytes, so `files_digest` changes, so eight green spikes correctly go stale. The problem is not the
detection; it is that re-establishing eight verdicts was manual, which makes the honest response to a
formatting pass tedious enough to invite skipping it — and a control people route around is worse
than no control.

## Decision Drivers

* **A doc that oversells evidence is a defect, equal in kind to a gate that does not gate.**
* **Never weaken a digest to reduce friction.** Whitespace is semantic in Python, YAML, Makefiles,
  and string literals; a whitespace-insensitive digest would be worthless.
* **Automating recovery must not become a way to bless a stale record.** The exit code is the only
  approver (ADR-13).
* **Additive at the evidence layer** — no existing digest may shift.
* **Absence must never read as success** (the rule that has already been applied to `confidence`,
  `samples`, and freeze records).

## Considered Options

For finding 1:
* **A1 — Persist the per-run exit codes as `exit_codes`.**
* **B1 — Soften the doc to match the code (delete the claim).**

For finding 2:
* **A2 — `--regate`: re-run exactly the stale spikes, from the command each record already stores.**
* **B2 — Normalise whitespace in `files_digest` so formatting does not invalidate.**
* **C2 — Let a spike record a `files_hash` of the *formatted* tree by running the formatter first.**
* **D2 — Nothing; document that re-gating after a formatter is expected work.**

## Decision Outcome

Chosen: **A1 + A2.**

**`exit_codes` in the spike predicate**, one entry per run, in order. `samples` stays (it is the
count, cheap to read); `exit_codes` is what makes the count checkable. A timeout has no exit code and
is recorded as `null` rather than a fabricated number, because writing 0 or -1 there would invent a
status the OS never returned. A leaf predating the field normalises to `[]` — "not recorded", never a
run that succeeded. `validate_leaf` surfaces both, so the auditor can check `gate` against what the
runs actually returned instead of trusting `gate` alone.

Option B1 was available and cheaper: delete the sentence. Rejected because the data already existed
one function call away, and the honest fix for "the record does not contain what we claim" is to put
it in the record when it is this cheap. Softening the doc would have been the right call only if
persisting were expensive.

**`--regate --run-dir DIR --ts STAMP`** re-runs every Fold-2 leaf whose `files_hash` no longer
matches the tree, using the `command` the record already stores, at the same sample count. Only stale
leaves are touched, so it is cheap to run habitually and never rewrites a valid record.

The load-bearing property, and the one guarded by a test: **each spike is RE-EXECUTED, and its new
verdict comes from a fresh subprocess exit code.** `--regate` is not a blessing operation. A re-gate
must be able to discover that the formatting pass broke something, and it exits nonzero when one now
fails. The sabotage that turns `--regate` into "keep the old gate, refresh the hash" makes the run
converge with two broken checks, and the suite goes red on it.

`validate_leaf` now also surfaces `command`. It was stored in the predicate all along but only
`command_hash` was exposed — enough to detect a *different* command, useless for repeating the *same*
one.

**Why not B2 (normalise whitespace).** It would make the digest lie. Reformatting is usually
semantically neutral in Rust and never guaranteed to be in Python, YAML, or a Makefile, and
`files_digest` has no way to know which language it is looking at. A digest that ignores a class of
real change is the vacuous-tamper-evidence bug ADR-25/27 already had to fix once, in a different
place.

**Why not C2 (format first, then spike).** It only moves the race: the next formatter version, or a
different formatter, invalidates again. It also couples every spike to the project's formatter
configuration, which a plugin that must work on any repo cannot assume.

**Why not D2.** It is defensible — the friction is honest work — but the agent's report is evidence
about behaviour, not preference: eight manual re-runs twice is where people start skipping the
re-gate. Automating the recovery while keeping the detection costs one function and no guarantee.

### Consequences

* Good, because the skill's claim about the record is now true, and checkable by the probe the agent
  ran.
* Good, because a reader can distinguish "5 clean passes" from "4 passes after a failure", which is
  the entire point of `--repeat`.
* Good, because the response to a formatting pass is one command instead of eight, so the honest path
  is also the easy one.
* Good, because `--regate` can turn red, and therefore sometimes will — a formatter that breaks a
  check is now discovered by the recovery step rather than hidden by it.
* Bad, because `--regate` re-runs commands recorded earlier in the run, which is a second execution
  path for spike commands. Mitigated by there being exactly one writer still
  (`evidence.write_spike`, called from this module only) and by the command coming from the record
  rather than from a caller.
* Bad, because a long-running spike suite makes `--regate` slow. It re-runs only stale leaves at
  their original sample count, which bounds it by what the run already chose to pay.
* Neutral, because no digest changes shape. `exit_codes` is additive; existing leaves read as `[]`.

### Confirmation

Tests in `plugins/empirica/tests/test_hooks.py` (`make test`), each verified red on revert:

1. A repeated spike records one exit code per run; a single run records one; `samples` and
   `exit_codes` agree; a leaf without the field reads as `[]`; a timeout stays `null` (Q36–Q41).
2. `--regate` re-runs exactly the stale spikes and restores them; an already-intact run finds nothing
   to do (Q42–Q46).
3. **The anti-blessing test:** with the check genuinely broken, `--regate` reports `failed`, records
   `gate: fail`, and the evidence gate then refuses the claim (Q47–Q49). Sabotaging `--regate` into
   reusing the old gate turns exactly these red.

## More Information

Both findings came from an agent using the plugin on a real Rust project, which is worth noting as a
pattern: ADR-27's five defects came from an adversarial review of the *code*, and these two came from
*use*. The second class is not reachable by review — nobody reviewing `files_digest` would have
predicted "eight records, twice, and the loop is the problem."

The first finding is also the second time in three records that a documentation claim outran the
implementation (ADR-27 finding 3 was the same shape: `--repeat` fixed sampling and left the record
silent). The lesson is narrow and mechanical: when a doc sentence asserts a field exists, the test
that proves it should be written in the same commit as the sentence.
