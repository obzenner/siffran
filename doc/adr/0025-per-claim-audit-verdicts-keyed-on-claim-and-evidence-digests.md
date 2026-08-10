---
number: 25
title: "Per-claim audit verdicts keyed on claim and evidence digests"
status: proposed
date: 2026-08-10
tags:
  - verification
  - audit
  - harness
  - evidence
links:
  - target: 20
    kind: Amends
  - target: 22
    kind: Depends on
  - target: 13
    kind: relatesto
  - target: 19
    kind: relatesto
  - target: 21
    kind: relatesto
---

# Per-claim audit verdicts keyed on claim and evidence digests

## Context and Problem Statement

ADR-20 P6 requires an independent auditor to pass a run before it may report `converged`. The
verdict that discharges P6 is currently a **whole-graph** object: `claims_reviewed` is a list of
claim ids (`audit.py:177`), coverage is "every approved id appears in that list"
(`audit.py:254-259`), and freshness is one coarse comparison of the ticket's `pass` number against
the run's current pass (`audit.py:261-269`).

Every other binding in this system is per-claim and content-addressed. `evidence.claim_digest()`
(`evidence.py:109`) hashes a claim's text, `_statement` puts that hash in the in-toto subject
(`evidence.py:155-162`), and `_binds` (`evidence.py:316`) refuses a leaf whose digest no longer
matches — so **evidence ages per claim, while the audit ages per graph**. The asymmetry has three
consequences, and the first was observed in a live run:

1. **A verdict cannot age gracefully, so it ages badly.** Adding one claim to a 22-claim graph
   invalidates the audit of the other 21, because the only staleness signal is a pass counter that
   ticked. The auditor is asked to re-review 21 claims whose text and evidence are byte-identical
   to what it already approved.

2. **The normal fix-and-loop rhythm mechanically invalidates the verdict.** `convergence_gate.py:295`
   calls `record_pass` on every audit-fail round. So the intended workflow — audit fails, fix what
   it found, loop — guarantees `t["pass"] < current_pass` on the next round. The staleness check
   fires on exactly the behaviour ADR-20 asks for.

3. **The reported reason can be false.** The block message quotes the verdict's findings
   (`audit.py:251`) with no per-claim record of which findings were addressed, so a gate can quote a
   verdict whose named defects no longer exist. A reviewer reading that message is told the run
   failed for reasons that were fixed two passes ago.

The precision the fix needs already exists in the codebase; the audit layer simply does not use it.

## Decision Drivers

* **Symmetry with evidence.** A reviewed-ness record should invalidate under exactly the conditions
  that invalidate the evidence it reviewed — no coarser, no finer.
* **No weakening of coverage.** ADR-20's guarantee is that an auditor cannot pass a run by reviewing
  one claim and ignoring the rest. Any change must keep that exactly.
* **Do not overclaim (ADR-21).** The verdict is unsigned JSON in a directory the author holds Write
  on. Precision in *staleness* must not be described as protection against *forgery*.
* **Determinism (ADR-19).** No clock, no randomness; the audit path must stay resumable.
* **Fail closed on ambiguity.** An unreadable or unrecognised verdict must block, never pass.

## Considered Options

* **A — Key each reviewed entry on `{claim_id, claim_digest, evidence_digest}`**; drop per-graph
  pass staleness.
* **B — Key on `{claim_id, claim_digest}` only** (the reported proposal, taken literally).
* **C — Keep the flat id list, but recompute staleness per claim from evidence-leaf mtimes.**
* **D — Leave it; instruct the auditor in prose to review incrementally.**

## Decision Outcome

Chosen option: **A — key each reviewed entry on `{claim_id, claim_digest, evidence_digest}`, and
drop the per-graph pass-staleness check entirely.**

`claims_reviewed` becomes a list of objects. `audit.check` recomputes each approved claim's digests
from the graph and the evidence store and requires a matching reviewed entry:

```json
"claims_reviewed": [
  {"claim_id": "G1",
   "claim_digest": "<sha256 of the claim text the auditor read>",
   "evidence_digest": "<sha256 over the supporting leaves the auditor re-read>"}
]
```

`evidence_digest` is computed by a single function in `evidence.py` over the **bound, supporting**
leaves for that claim, in a canonical order, covering each leaf's identity and its decision-bearing
fields. The auditor calls that function rather than hand-rolling a hash; one definition, so the
writer and the checker cannot drift.

The three invalidation cases then land per claim:

| Change | Digest that moves | Result |
|---|---|---|
| new claim added | (no reviewed entry at all) | that claim unreviewed; others stay reviewed |
| claim reworded | `claim_digest` | that claim unreviewed |
| citation swapped or re-evidenced | `evidence_digest` | that claim unreviewed |
| unrelated claim fixed | neither | stays reviewed |

**Why `evidence_digest` is load-bearing, and why option B is not enough.** `claim_digest` is over
claim *text* only. Swapping a claim's citation for a fabricated one leaves the text — and therefore
the digest — identical. Under option B that produces a claim whose evidence changed after review but
which still reads as reviewed. Today the coarse pass check incidentally catches this, so adopting B
as stated would *narrow* the audit while appearing to sharpen it: a regression disguised as a fix.
This is the ADR-21 lesson again — a control that becomes more precise must be checked for what its
precision drops.

**Why dropping pass-staleness is then correct rather than merely convenient.** The pass counter was
a proxy for "the graph changed since review". With both digests checked per claim, the real thing is
measured directly, and the proxy's only remaining behaviour is the false positive in driver 2. A
proxy that fires on compliant behaviour and is fully subsumed by a direct measurement is not a second
layer of defence; it is noise. It goes.

**No backwards compatibility.** The flat `list[str]` form is not accepted. Verdicts are transient,
git-ignored run state (ADR-14) that dies with the run, so there is nothing in the wild to migrate,
and a dual-form reader would keep the weaker form reachable — which is precisely the "legacy shape as
escape hatch" exploit the gate already had to close once (`convergence_gate.py:222`, and the
regression test that pins it). A verdict in the old shape reads as **absent**, and absent blocks.

### Consequences

* Good, because the auditor's work is preserved exactly where nothing changed: incremental runs
  re-review the claims that actually moved, which is the difference between "review 1" and
  "review 22".
* Good, because the audit's freshness signal becomes the same *kind* of signal as the evidence's —
  content-addressed, per claim — so one mental model covers both.
* Good, because it removes a check that fired on compliant behaviour, and a false positive that users
  learn to route around is worse than no check.
* Good, because a swapped citation is now caught per claim and named as such, instead of being caught
  incidentally by a pass counter that cannot say what changed.
* Bad, because the auditor must compute a digest, so its contract is more than "list the ids you
  looked at". Mitigated by putting the computation in `evidence.py` and having the auditor call it.
* Bad, because a verdict is now shaped data rather than a list of strings, so a malformed one is
  easier to write by hand. It reads as absent and blocks, which is the fail-closed direction.
* Neutral, because the guarantee against **forgery** is unchanged. An author that hand-writes a
  verdict can still compute both digests from the run directory — exactly as it can compute the nonce
  (`audit.py:20-24`). This ADR sharpens staleness, not authenticity, and must not be cited as doing
  more.

### Confirmation

Regression tests in `plugins/empirica/tests/test_hooks.py`, run by `make test`, each of which must
fail if the mechanism is removed:

1. Adding a claim to an audited graph leaves the other claims reviewed and blocks on **only** the new
   one — the headline behaviour.
2. Rewording an audited claim un-reviews **that** claim (its `claim_digest` moves).
3. Swapping an audited claim's evidence leaf un-reviews **that** claim (its `evidence_digest` moves).
   This is the option-B regression, pinned as a test.
4. A verdict in the legacy flat `list[str]` form is refused, not silently accepted.
5. An audit-fail → fix → re-audit round no longer trips staleness on untouched claims (driver 2).
6. Partial coverage is still refused, and the message still names the unreviewed claims (ADR-20's
   guarantee, unchanged).

## Pros and Cons of the Options

### B — `{claim_id, claim_digest}` only

* Good, because it is the smallest change and fixes the headline complaint (re-review 21 to add 1).
* Good, because it needs no new digest function — `claim_digest` already exists.
* Bad, because it is blind to evidence substitution: same claim text, different citation, still reads
  as reviewed. Dropping pass-staleness alongside it would therefore lose a real check.
* Bad, because it invites the belief that reviewed-ness is bound to *the thing the auditor read*,
  when it is bound only to the claim's wording.

### C — Per-claim staleness from evidence-leaf mtimes

* Good, because it requires no schema change to the verdict at all.
* Bad, because mtime is not content: `touch` invalidates, and a byte-identical rewrite invalidates,
  while an atomic replace that preserves mtime does not.
* Bad, because it reintroduces filesystem time into a path ADR-19 deliberately keeps free of clocks,
  and resumed or copied run directories carry meaningless mtimes.

### D — Prose instruction to the auditor

* Good, because it costs nothing to ship.
* Bad, because the gate would still compute staleness per graph, so a correctly-behaving incremental
  auditor would be refused by the check. The defect is in the checker, not the auditor's diligence.
* Bad, because it is the pattern this project explicitly rejects: "instructions are context, not
  configuration" — a rule that must hold every time belongs in the harness.

## More Information

Prompted by a report from an agent that ran empirica end to end and hit all three consequences in one
run; consequence 3 was demonstrated by the run's own final gate message, which quoted a verdict whose
two named defects had already been fixed.

Pass-staleness history: the ticket's `pass` field was added because it was "written and validated but
never COMPARED" (`audit.py:230`, tests R35–R38). That fix was correct for the mechanism then
available. This ADR replaces the proxy with a direct measurement rather than reverting it.

ADR-26 covers the separate question of when a run should stop *discovering* claims. The two were
reported together but are independent decisions.
