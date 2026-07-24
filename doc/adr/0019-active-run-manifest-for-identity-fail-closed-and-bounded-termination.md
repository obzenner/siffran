---
number: 19
title: "Active-run manifest: run identity, fail-closed gating, and bounded termination"
status: accepted
date: 2026-07-24
tags:
  - architecture
  - state
  - termination
  - trust-boundary
links:
  - target: 9
    kind: Refines
  - target: 8
    kind: Depends on
  - target: 17
    kind: relatesto
  - target: 18
    kind: relatesto
---

# Active-run manifest: run identity, fail-closed gating, and bounded termination

## Context and Problem Statement

The external review (`tmp/empirica-review-gpt-5.6-sol.md`) left three findings that share one
root cause — the workflow has no notion of "a run is active in this tree":

* **1.2a** — the Stop gate fails OPEN when `spec.md` is missing, so deleting/renaming the spec
  bypasses convergence; there is no run-activation signal to distinguish "our run, tampered"
  from "not our run."
* **2.5** — a present-but-corrupt budget/state file is read as unbounded, silently disabling
  enforcement at the moment it matters most.
* **2.3** — ADR-9's termination "proof" is prose ("specialize-only derivation"); an infinite
  chain of ever-narrower unknowns is not excluded by any measurable variant. No pass counter
  or cap exists in code.

A design pass (`abstraction-refinement` methodology) showed these are **one missing thing, not
three**: a per-run record the harness owns, which answers (a) is a run active here? (identity),
(b) how many passes, past the cap? (termination), and (c) which unknowns were resolved by which
evidence? (the future evidence binding of ADR-18). Before recording this ADR, the full chain was
proven by a spike (`.claude/spike-manifest`, 14/14 falsification checks) — identity branches,
the termination variant, idempotent lifecycle, and corrupt-vs-absent distinction all demonstrated
in running code, per empirica's own discipline (design → spike → record).

## Decision Drivers

* Enforcement must not depend on model cooperation (the trust model of ADR-8).
* Must NOT break the existing fail-OPEN behaviour for sessions that are not empirica runs — an
  unrelated repo that happens to contain a `spec.md` must stay unblocked.
* Termination must rest on a real well-founded variant, not a natural-language heuristic (2.3).
* No new committable artifact — the record is transient scratch (ADR-14).
* Reuse the hardened file-io already proven in `budget.py` (lock, atomic write, strict coercion),
  not a second bespoke implementation.

## Considered Options

* **Keep spec-presence as the only signal.** Rejected: cannot tell "our run, spec deleted"
  (should block) from "unrelated repo, no spec" (should allow) — the ambiguity behind 1.2a.
* **Put run state inside the living spec.** Rejected: the spec is the model's own working
  memory and freely editable; run identity and the pass counter must be harness-owned (the
  model could reset the counter to escape the cap).
* **Active-run manifest (chosen).** A harness-owned transient `run.json` in the run directory,
  keyed to `session_id + canonical project root`, created at run start via a
  `UserPromptExpansion` hook (matcher `^empirica:empirica$`, matching the plugin-namespaced
  command name), carrying `status`, monotone `passes`, `max_passes`, `spec_path` (the living
  spec's location in the run directory), and a dormant `evidence` map. Absence of the manifest
  is the "not a run → fail open" signal; an active manifest turns missing/corrupt state into
  fail-closed.

## Decision Outcome

Chosen option: **"Active-run manifest."**

**Creation (verified against code.claude.com/docs/en/hooks and a captured live payload):** a
`UserPromptExpansion` hook with `matcher: "^empirica:empirica$"` fires once when `/empirica`
expands, carrying `session_id` and `cwd` — the run-start signal that also proves the empirica
skill (not an unrelated session) started it. The matcher is anchored to the plugin-namespaced
command name `empirica:empirica`; a bare `empirica` is exact-matched by the harness and never
fires. It writes `.claude/empirica/<run_id>/run.json` where
`run_id = sha256(session_id + canonical root)`, and the living spec sits beside it at
`.claude/empirica/<run_id>/spec.md`. Start is **idempotent**: re-invoking `/empirica` mid-run
does not reset `passes`.

**Fail direction (G1, closes 1.2a + 2.5).** The convergence gate reads the manifest:
- **no manifest** → not an empirica run → existing spec-based **fail-OPEN** path, unchanged
  (unrelated repos stay safe);
- **active manifest, spec missing/unreadable** → **BLOCK** (fail closed);
- **manifest present but corrupt/unparseable** → **BLOCK** (a `__corrupt__` sentinel, distinct
  from absent — corruption of an active run is the moment you most want the gate);
- **status ≠ active** (already stopped/converged) → fail open (done, don't re-block).

**Termination (G2, closes/refines 2.3).** The gate ticks a monotone `passes` counter each Stop
pass. The well-founded variant is `max_passes − passes` over (ℕ, <): it strictly decreases by 1
per pass and is bounded below by 0, so the loop terminates in ≤ `max_passes` passes **regardless
of whether unknowns converge**. At the cap the gate sets `status = stopped_residual`, marks
remaining unknowns `blocked: needs-passes`, and reports non-converged — honest termination, not
the platform's forced 8-block override. "Specialize-only derivation" (ADR-9) is **demoted from
the termination proof to a quality heuristic**: it keeps derived unknowns useful, but the counter
is what guarantees the loop ends. This is the honest correction to ADR-9 (see its update).

**Ownership (G3, honest limit).** `manifest.py` is the sole writer, called only from hooks
(run-start + gate), never the model. Caveat recorded rather than overclaimed: the manifest is a
file on disk and the model has Bash/Write tools, so this is "the model has no instruction to
touch it and tampering is visible," NOT kernel-enforced isolation. Same trust level as the spec
itself; a stronger boundary is out of scope for a file-based harness.

**Identity reuse.** The budget ledger's `run_id` now derives from the manifest, unifying the two
and eliminating the shared-`default`-ledger bug (review 2.4) as a side effect.

**Evidence-ready (G4).** The manifest carries an empty `evidence` map, dormant until ADR-18's
run mode writes unknown→evidence bindings into it — so that feature needs no new substrate.

### Consequences

* Good, because 1.2a, 2.5, and 2.3 are closed by one mechanism with one owner, and the fix keys
  on manifest presence so unrelated repos keep the safe fail-open behaviour.
* Good, because termination now has a real variant (`max_passes − passes`), a genuine proof
  rather than prose — proven to stop a non-converging loop in the spike (check G2c).
* Good, because it reuses `budget.py`'s already-reviewed file-io hardening (shared `_atomic`
  helper), not a second implementation.
* Bad, because G3 (harness-owned) is a soft boundary: a model that actively rewrites its own
  scratch could forge state. This is inherent to file-based harness state and is documented, not
  hidden.
* Bad, because it adds a `UserPromptExpansion` hook and a new module — more surface, and the
  hook-payload field names (`command_name`, `session_id`) must be re-confirmed at build against
  the running Claude Code version (docs verified 2026-07-24; the spike simulated the payload).
* Bad, because `max_passes` needs a sensible default and operator override; too low yields
  `needs-passes` residuals (honest, but a poorer experience).

### Confirmation

Fitness function — proven in `.claude/spike-manifest` (14/14) and to be ported to the committed
`plugins/empirica/tests/` at build: (1) no manifest → gate fails open; (2) active manifest +
missing spec → block; (3) corrupt active manifest → block; (4) stopped run → fail open;
(5) the variant strictly decreases and a non-converging loop stops exactly at `max_passes`
ending `stopped_residual`; (6) re-invoking start does not reset `passes`; (7) run_id is stable
per (session, root) and distinct across sessions.

## More Information

Designed via the `abstraction-refinement` methodology (abstract guarantees G1–G4 → data/boundaries
→ interfaces/flow → algorithms → chain validation) and proven by spike before recording, per
empirica's design→spike→record discipline. Hook mechanics verified against
code.claude.com/docs/en/hooks (`UserPromptExpansion` with `command_name`; `session_id`/`cwd` as
common fields across all hook types). Refines ADR-9 (termination proof is now the variant);
depends on ADR-8 (harness enforcement); relates to ADR-17 (unifies run identity with the spawn
ledger) and ADR-18 (provides the manifest + evidence map that evidence-bound convergence needs).
