# Evidence over Recall (shared spine)

The single principle underneath every methodology in this plugin. Read it before executing any methodology; each methodology references it rather than restating it.

**Lineage:** Lewis et al. (RAG, 2020 — *parametric* vs *non-parametric* memory), Popper (falsifiability, 1934), Feynman ("the first principle is that you must not fool yourself"), Hoare (pre/postconditions, 1969).

## The three faces of one idea

These look like three rules. They are one rule seen from three angles: **a claim is only as good as the evidence you can point at.**

### 1. Parametric knowledge is a hypothesis, never an answer

**Parametric knowledge** is what an agent "knows" from its training weights alone — recall, not observation. It is a *stale, lossy compression of past reality*: frozen at training time, unversioned, unfalsifiable from the inside. It is legitimate for exactly one thing — *generating hypotheses and choosing which questions to ask*. It is never admissible as an answer.

Every load-bearing claim must be discharged against **non-parametric evidence**: the codebase, official documentation, or runtime output. A claim resting on parametric knowledge alone is **UNVERIFIED by definition**.

> **Weights propose; evidence disposes.**

### 2. Every reasoning step must emit a fabrication-resistant artifact

A reasoning step is *observable* when its output could not have been written without actually doing the work. A step is *fakeable* when a plausible sentence can stand in for the work. Prefer the observable form every time:

| Fakeable (prose) | Observable (artifact) |
|---|---|
| "I checked the docs" | the command run + the URL/source cited |
| "I read the file" | `file:line` citation of what was found |
| "evidence: this holds" | the counterexample search attempted, or the code path traced |
| "I considered the alternative" | the alternative stated at full strength *before* deriving against it |
| "I predict X" written after seeing the result | the prediction written *before* the result exists |

The strongest observability device is **predict-before-execute**: a prediction recorded before the evidence arrives cannot be backfilled to match it. The gap between prediction and reality is the finding.

### 3. Open questions are a worklist, not an output

An open question is an *admission you hit a wall* — not a phase you are obligated to fill. The protocol is always:

1. **Surface** candidate questions as you find them.
2. **Resolve** each against evidence — read the code, search the docs, reason it through, run the command.
3. **Surface only the residual** — the questions you genuinely could not close — each tagged with *why* it is blocked and *what you already tried*.

Tags for a surviving question:
- `needs-data` — information you cannot obtain (no access, not in repo, not in docs)
- `needs-decision` — a genuine judgment call that is the user's to make, not derivable from evidence
- `needs-experiment` — runtime evidence you cannot gather here (a benchmark, a prod check)

**The honest default is None.** A question that you can answer by acting is not an open question — answer it. Manufacturing questions to fill a section is the failure mode this rule exists to kill.

## The mandatory stance declaration

Any methodology, before its first phase, emits this line verbatim so the stance is observable in the transcript:

> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are resolved until blocked, then surfaced with what was tried.

If the line is absent from the output, the methodology was not followed.
