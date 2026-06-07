# Requirement-First Verification — *verify to learn*

**Lineage:** Stepanov (generic programming — requirements over syntax), Hoare (pre/postconditions, 1969), Popper (falsifiability), property-based testing (QuickCheck, Claessen & Hughes, 2000)
**Prevents:** Illusion-of-understanding from reading generated code, conflating "compiles" with "correct", learning a language as a disconnected feature list, accepting code that is valid in the language but violates its actual requirements

> **Read first:** `../references/three-layer-verification.md` — the shared three-layer model and the full text of Phases 1, 3, and 4. This file owns only the phases that differ for the *verify-to-learn* mode (Phase 2, 5, 6) and the framing below.

## Mode framing

This is the discipline for **learning a language by verifying generated code rather than authoring it**. The prediction step (Phase 2) and the gate-tracing step (Phase 3) are the friction that makes it work — without them the loop collapses into "run it, looks fine, next," and teaches nothing. The payoff is not the verified code; it is the **map of which language features actually buy you which guarantees**, accumulated across artifacts.

## Phases

### Phase 1: Pick artifact and write the Layer 1 requirement sheet

Per the spine. **Mode-specific artifact choice:** optimize for *legibility over usefulness* — pick the artifact whose requirements are sharpest to state, not the one you happen to need. Avoid sprawl; you want the requirements legible enough that a leak is unmistakable.

### Phase 2: Predict the Layer 2 encoding

For each Layer 1 requirement, name the language feature you expect to encode it, and predict where the toolchain will **catch** a violation versus where it will stay **silent**. **Writing the prediction down is mandatory and sacred — the gap between prediction and reality is the entire lesson.** Skipping it collapses the method into passive reading.

**Output format:**
```
Encoding predictions:
  - Requirement <Px/property> → feature <the language's costume for it>
    Toolchain verdict prediction: <caught at compile/lint | silent — leaks to harness>
    Confidence: <high | low> — <why>
Predicted leak set (requirements no gate will catch): <list — these drive Phase 4>
```

### Phase 3: Obtain code and run the Layer 2 gates

Per the spine. In learn mode the artifact is **generated** (you are verifying code you did not author).

### Phase 4: Build and run the Layer 3 harness

Per the spine.

### Phase 5: Log the split (the Stepanov filter) — as curriculum

Classify each requirement per the spine's taxonomy. In learn mode this accumulating log **IS your curriculum** — a principled account of what each language's features actually buy you, derived from watching requirements get caught or leak, not from a features list. The point of the log is to surface **the strand you keep snagging on** — that is your real syllabus for the next artifact.

**Output format:**
```
Caught for free by the toolchain (tight costume): <requirements + which feature caught them>
Leaked to the harness (language can't help here): <requirements>
Classification:
  - Universal (transfers to any language): <list>
  - Local (this language's costume): <list>
Strand still weak: <the concept I keep snagging on — my real curriculum>
```

### Phase 6: Conclusion and next artifact

State what was learned and pick the next artifact to pull a *different* cluster of interlocking strands (a custom iterator, an enum state machine, a generic container, a small concurrent structure — each lights up a different overlapping cluster). Re-hit some concepts, add new ones, so the web gets covered from several angles.

**Output format:**
```
Conclusion:
  - Code verdict: <correct & idiomatic | encoding bug | requirement bug — with evidence>
  - What this language's costume bought: <which requirements it enforced for free>
  - What stayed on me: <requirements only the harness could catch>
  - Lesson (prediction vs reality gap): <where Phase 2 was wrong, and why>
Next artifact: <what to pick next, and which new strands it forces to interlock>
```
