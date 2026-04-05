# Empirical Falsification

**Lineage:** Popper (falsifiability, 1934), Fisher (experimental design), Feynman ("the first principle is that you must not fool yourself")
**Prevents:** Untested assumptions becoming load-bearing, premature optimization based on intuition, architecture driven by speculation instead of evidence, cargo cult engineering

## Core principle

A claim that cannot be tested is not an engineering claim — it's an opinion. For any assertion about system behavior ("this will scale", "this is faster", "users won't notice"), the methodology is: state the claim as a testable hypothesis, design the cheapest experiment that could disprove it, run the experiment, update beliefs based on evidence.

The goal is not to prove you're right. The goal is to find out if you're wrong as cheaply as possible.

## Phases

### Phase 1: Identify the claim

What assertion is being made (or implied) that we're treating as true? Strip it of hedging and make it concrete.

Bad: "This approach should be fine for our scale"
Good: "This approach handles 10K requests/second with p99 latency under 100ms"

**Output format:**
```
Claim: <concrete, measurable assertion>
Source: <who/what is making this claim — intuition, docs, a blog post, past experience>
Stakes: <what breaks if this claim is wrong>
```

### Phase 2: Formulate hypotheses

State the claim as a testable hypothesis (H1) and its null hypothesis (H0).

- **H1** (the claim): <specific, measurable, falsifiable statement>
- **H0** (the null): <what is true if the claim is wrong>

The null hypothesis is what you're trying to disprove. If you can't disprove it, the claim is not supported.

**Key:** H1 must be specific enough that you can describe an observation that would disprove it. If you can't describe what "wrong" looks like, the hypothesis isn't testable.

**Output format:**
```
H1: <the claim, stated precisely>
H0: <what's true if the claim is false>
Falsification criteria: <what observation would disprove H1>
```

### Phase 3: Design the experiment

Design the cheapest experiment that could disprove H1. Not the most thorough — the cheapest. You're optimizing for speed of learning, not rigor of proof.

Experiment types (ordered by cost):
1. **Desk check:** Can you disprove it by reading code/docs/math alone?
2. **Existing data:** Is there monitoring, logs, or benchmarks that already contain the answer?
3. **Micro-benchmark:** A 10-line script that tests the specific claim in isolation
4. **Prototype/spike:** A minimal implementation that exercises the claim in realistic conditions
5. **A/B test / production experiment:** The full thing, only when cheaper options are insufficient

**Output format:**
```
Experiment type: <desk check | existing data | micro-benchmark | spike | production>
Design:
- Setup: <what to build/prepare>
- Execution: <what to run/measure>
- Measurement: <what metric, how to collect>
- Duration: <estimated time to get a result>
- Success criterion: <what confirms H1>
- Failure criterion: <what confirms H0>
```

### Phase 4: Predict outcomes

Before running the experiment, predict what you expect to observe. This is critical — without a prediction, you'll rationalize any result as "expected."

State:
- What you expect to see if H1 is true
- What you expect to see if H0 is true
- What you'd see if neither is quite right (unexpected territory)

**Output format:**
```
Predictions:
- If H1 (claim is true): <expected observation>
- If H0 (claim is false): <expected observation>
- Surprise zone: <observations that would mean we're asking the wrong question>
```

### Phase 5: Execute and observe

Run the experiment. Record the actual observation. Compare to predictions.

**Rules:**
- Record the raw result before interpreting it
- If the result is in the "surprise zone," do NOT force it into H1 or H0 — the model is wrong
- If the result is ambiguous, the experiment needs refinement, not interpretation

**Output format:**
```
Raw result: <what was actually observed>
Matches: <H1 | H0 | surprise zone>
Interpretation: <what this means for the original claim>
```

### Phase 6: Update and decide

Based on evidence:
- If H1 confirmed: proceed with the claim, document the evidence
- If H0 confirmed: the claim is wrong — what changes?
- If surprise zone: the framing was wrong — reformulate and re-test

**Output format:**
```
Verdict: <H1 supported | H0 supported | reframe needed>
Evidence: <summary of what was observed>
Decision: <what to do now>
Residual uncertainty: <what this experiment did NOT test>
```
