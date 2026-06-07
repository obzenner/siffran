# Requirement-First Development — *verify to ship*

**Lineage:** Stepanov (generic programming — requirements over syntax), Hoare (pre/postconditions, 1969), Popper (falsifiability), property-based testing (QuickCheck, Claessen & Hughes, 2000)
**Prevents:** Unverified requirements becoming load-bearing in shipped code, conflating "compiles" with "correct", over-testing what the toolchain already guarantees while under-testing what leaks past it, "works on the happy path" code that violates an algebraic law on the input that matters

> **Read first:** `../references/three-layer-verification.md` — the shared three-layer model and the full text of Phases 1, 3, and 4. This file owns only the phases that differ for the *verify-to-ship* mode (Phase 2, 5, 6) and the framing below.

## Mode framing

This is the discipline for an **agent developing or verifying code dynamically** — building a change and proving it correct through layered gates, not learning a language. Same spine as *verify-to-learn*, but the terminal activity is **shipping verified code**, so two phases shift purpose:

- **Phase 2 is cost triage, not pedagogy.** The prediction of where each requirement is caught vs. leaks tells you *where to spend harness effort*. You do not write tests for what the type-checker already guarantees; you write them for the leak set. The prediction is a budget, not a lesson.
- **Phase 5 is a verified/unverified ledger, not a curriculum.** The output is a coverage statement for the changeset: every Layer 1 requirement is accounted for as either *gated* (compiler/linter enforces it) or *harnessed* (a test enforces it). A requirement that is neither is an **unverified, load-bearing assumption** — the thing that must not ship silently.

### Relationship to other skills

This is **not** test-driven-development (failing test first → make it pass) nor source-driven-development (verify APIs against docs first). It is orthogonal and composes with both: RFD's contribution is the **Layer 2 / Layer 3 split** — deciding *which* requirements need an executable test at all, by first asking which the toolchain enforces for free. Use TDD to author each harness test; use RFD to decide which tests are worth authoring.

## When the artifact spans a real changeset

Unlike verify-to-learn (one sharp artifact), a development task may touch many functions. Apply the spine **per requirement-bearing unit**, not per file: a unit is anything with a statable postcondition (a parser, a merge, a state transition, a cache eviction rule). Trivial glue with no nontrivial postcondition needs no sheet — say so explicitly rather than padding.

## Phases

### Phase 1: Write the Layer 1 requirement sheet for the change

Per the spine. **Mode-specific:** the artifact is *given by the task*, not chosen for legibility. List the requirement-bearing units in the change and write a sheet per unit. State the weakest sufficient requirement for each — over-stating preconditions here produces over-restrictive code; under-stating produces silent bugs.

### Phase 2: Predict the Layer 2 encoding — as a test budget

For each Layer 1 requirement, name the feature that should encode it and predict caught-vs-silent. **The predicted leak set is your test plan.** Anything you predict the toolchain catches → do **not** harness it (that is wasted test surface duplicating the compiler). Anything you predict leaks → it **must** appear in Phase 4.

**Output format:**
```
Encoding predictions:
  - Requirement <Px/property> → feature <the language's costume for it>
    Toolchain verdict prediction: <caught at compile/lint | silent — leaks to harness>
    Test decision: <skip — toolchain covers it | harness in Phase 4>
Predicted leak set (the Phase 4 test plan): <list>
```

### Phase 3: Obtain code and run the Layer 2 gates

Per the spine. In dev mode the code is what you (or the model) just wrote or changed. A Layer 2 failure here is a fast, cheap signal — fix and re-run before touching the harness.

### Phase 4: Build and run the Layer 3 harness

Per the spine. Author each test for a leaked requirement (TDD applies: write it to fail first if the behavior is new). **Gate to ship:** every requirement in the Phase 2 leak set has a corresponding harness entry with a result.

### Phase 5: Log the split — as a coverage ledger

Classify each requirement per the spine's taxonomy, then convert it into a ship decision. Every Layer 1 requirement must be either *gated* or *harnessed*. Any requirement that is neither is a **load-bearing unverified assumption** — list it explicitly; it is the headline risk of the change.

**Output format:**
```
Gated by the toolchain (no test needed): <requirements + which feature gates them>
Harnessed (test enforces): <requirements + test name>
UNVERIFIED (neither gated nor harnessed): <requirements — these are the risk; justify or close each>
Classification:
  - Universal (would transfer to a rewrite in another language): <list>
  - Local (this language's costume): <list>
Layer 2 green / Layer 3 red findings: <bugs the compiler passed but the harness caught>
```

### Phase 6: Conclusion and ship verdict

State whether the change is safe to ship, with evidence traced to the layers — not "it compiles," but which requirements are gated, which are harnessed, and which (if any) remain unverified.

**Output format:**
```
Conclusion:
  - Ship verdict: <safe to ship | blocked — unverified requirement | requirement bug found>
  - Gated for free: <requirements the toolchain enforced>
  - Proven by harness: <requirements + evidence>
  - Residual risk: <unverified requirements still on us, or "none">
  - Bugs caught that compilation missed: <the Layer 2 green / Layer 3 red cases>
Follow-ups: <any unverified requirement deferred, with explicit justification>
```
