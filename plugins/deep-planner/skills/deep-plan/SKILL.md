---
name: deep-plan
description: "Iterative codebase investigation and planning skill. Reads code in multiple passes, refining the plan each time until no new information emerges (fixed-point convergence). Separates every work item into business logic, custom code, or external integration code — and verifies all external API surfaces against actual documentation before the plan graduates. Use whenever the user asks to plan a feature, design an implementation, investigate before coding, break down a complex change, or says 'plan this', 'investigate first', 'what do we need to build', 'map out the work', 'deep plan', or any variant of 'understand the code before we write anything'. Also use when a task involves multiple external libraries and getting the integration wrong would be expensive."
argument-hint: "[task description]"
allowed-tools: [Read, Glob, Grep, Bash, Agent, WebSearch, WebFetch, mcp__plugin_context7_context7__resolve-library-id, mcp__plugin_context7_context7__query-docs]
---

# Deep Planner — Iterative Investigation to Fixed-Point

**Lineage:** Jackson (Problem Frames, 2001), Parnas (information hiding, 1972), Cockburn (Hexagonal Architecture, 2005), Cousot & Cousot (abstract interpretation / fixed-point iteration, 1977), Popper (falsification, 1934)
**Prevents:** Plans built on hallucinated APIs, assumed codebase structure, unchecked training-data beliefs, category confusion between domain logic and integration code, single-pass plans that miss what the second read would have caught

## Core principle

A plan is a model of future work. A model is only as good as its correspondence to reality. Reality lives in two places: the existing codebase and the official documentation of external dependencies. Training data is neither — it is a stale, lossy compression of past reality. The methodology is: read the evidence, build the model, re-read the evidence through the lens of the model, find where the model is wrong, correct it, repeat until a full pass produces no corrections. This is fixed-point iteration — the plan converges when applying the investigation function yields no new information.

The convergence is guaranteed because the codebase is a finite artifact (bounded lattice) and each pass can only add information or correct errors (monotonic). A pass that discovers nothing new is the fixed point. A pass that discovers something means the previous model was incomplete — which is the most valuable finding, not a failure.

## The Three Domains

Every work item belongs to exactly one of three domains. This separation is not organizational convenience — it reflects fundamentally different kinds of knowledge, different sources of truth, and different failure modes.

**Business Logic** — the problem domain (Jackson). Rules, workflows, state machines, validation constraints, domain invariants. The source of truth is the requirements and existing domain code. This code should be expressible without mentioning any framework, library, or infrastructure. If you can't describe a business logic item without naming a dependency, it's not pure business logic — split it.

**Custom Code** — the machine domain (Jackson) / adapters (Cockburn). Project-specific utilities, helpers, glue code, configuration, wiring. The source of truth is the existing codebase — its patterns, conventions, directory structure. This code connects business logic to the outside world using idioms already established in the project. Parnas's criterion applies: each custom-code module hides a design decision that is likely to change.

**External Integration** — the connection domain (Jackson) / ports (Cockburn). Code written against external libraries, APIs, SDKs, or frameworks. The source of truth is the official documentation of the external dependency — not training data, not blog posts, not Stack Overflow. Every integration item carries an epistemic status:
- **UNVERIFIED** — assumed API surface, not yet checked against docs
- **VERIFIED** — confirmed against official documentation, with citation
- **UNRESOLVED** — documentation could not be found or is ambiguous, explicitly flagged

No plan graduates with UNVERIFIED items. This is Popper applied to API surfaces: a claim about how a library works is a hypothesis until tested against the documentation. An untested hypothesis in a plan becomes a hallucinated API call in the implementation.

## Invocation

The user invoked: `$ARGUMENTS`

If a task description was provided, use it as the starting point. If not, ask: "What are we planning?"

## Phase 1: Initial Investigation

Goal: build a first-approximation model of the relevant codebase surface.

### Step 1 — Scope

Based on the task, identify which parts of the codebase are relevant. Use Glob and Grep to find entry points, related modules, existing patterns. Discover the project's language, package manager, and dependency manifest by reading the project root — do not assume any specific ecosystem. Read whatever dependency manifest the project uses to identify the external dependency surface.

### Step 2 — Read

Actually read the files. Not filenames — contents. Focus on:
- Where the new work will live (existing directory conventions, module structure)
- What already exists in this domain (avoid reinventing)
- What external dependencies are imported and how they're used today
- What interfaces and contracts the new code must satisfy
- What test files reveal about implicit invariants

### Step 3 — Classify and Draft

For every piece of work you've identified, assign it to a domain. Produce the first draft:

```
## Plan — Pass 1 (Draft)

### Task: <what we're building>

### Business Logic
- [ ] BL-1: <item> — <why this is a domain concern, not an integration concern>

### Custom Code
- [ ] CC-1: <item> — <what existing pattern this follows, cite file>

### External Integration (UNVERIFIED)
- [ ] EI-1: <item> — <library>: <assumed API surface> ⚠️ UNVERIFIED
      HYPOTHESIS: <what you believe the API does, and why you believe it>

### Open Questions
- OQ-1 [needs-data | needs-decision | needs-experiment]: <question>

### Confidence: LOW — first pass, no verifications performed
```

Present this to the user. Then proceed immediately to Phase 2.

## Phase 2: Fixed-Point Loop

This is the core of the skill. Each iteration is one application of the investigation function f(plan) → plan'. The loop terminates when f(plan) = plan (fixed point) or at the hard cap of 5 iterations.

### Iteration structure:

**Step 1 — Re-read through the model, then widen.**

Return to the codebase, but now read it through the lens of your current plan. For each plan item, interrogate:
- Does this item account for every code path it will touch?
- Are there edge cases, error paths, or configuration branches the plan ignores?
- Did I miss a related module, a shared utility, a validation layer?
- Is there existing code that already does part of this? (Parnas: what secret does each module hide — am I duplicating a secret?)
- Does the test suite reveal contracts or invariants my plan doesn't account for?

Read files you skipped previously. Follow import chains you didn't trace. Check config files, middleware, initialization code. These are where implicit dependencies hide.

**Then actively expand scope.** Each pass must explore at least one file category not yet examined. The most dangerous gaps are surfaces you never looked at, not details you missed in files you read. Systematically consider: test files, config/CI files, adjacent modules, type definitions, scripts, documentation, migration files. If a category seems irrelevant, note why — but check at least one file from it to confirm. A "no new discoveries" finding is only trustworthy if the search was broad, not just deep.

**Step 2 — Verify external API surfaces.**

For every External Integration item still marked UNVERIFIED, apply empirical falsification:

1. **State the hypothesis.** "I believe library X exposes function Y with signature Z."
2. **Find the evidence.** Look up the actual documentation. Use context7 (resolve-library-id → query-docs) for libraries it covers. Use WebSearch/WebFetch against official docs for others. If the dependency is installed locally, read the actual source types from the project's dependency cache (discover where the project stores installed dependencies — don't assume a specific location).
3. **Compare hypothesis to evidence.** Do the function names match? Do the signatures match? Are there required parameters you missed? Error cases you didn't account for? Version-specific behavior?
4. **Update the model.**
   - If confirmed: mark VERIFIED, cite the doc source.
   - If wrong: correct the plan item, note what changed and why. This is the most valuable outcome — a caught hallucination.
   - If docs not found: mark UNRESOLVED with the reason. Do not fabricate verification.

Training data may inform which questions to ask, but it must never serve as the answer. The answer comes from documentation or it doesn't come at all.

**Step 3 — Produce the delta report.**

The delta is the diff between plan(n) and plan(n+1). Structure it explicitly:

```
## Delta Report — Pass N

### New discoveries
- <what the previous plan missed — with evidence from code or docs>

### Corrections
- <what was wrong — what it was, what it should be, how you found out>

### Verifications completed
- EI-<n>: <library> — <API surface> — VERIFIED against <doc source>
  [If hypothesis was wrong]: CORRECTED — originally assumed <X>, docs show <Y>

### Domain reclassifications
- <item moved from domain A to domain B> — <why>

### Remaining UNVERIFIED
- EI-<n>: <what still needs checking>

### Open questions resolved
- OQ-<n>: <answer, with source>

### New open questions
- OQ-<n> [needs-data | needs-decision | needs-experiment]: <question>

### Scope expansion this pass
- File categories examined for the first time: <list>
- File categories still unexamined: <list, with justification if claiming irrelevant>
```

**Step 4 — Update the plan.**

Incorporate the delta. Bump the pass number. Recategorize items where needed. An item you thought was Custom Code may turn out to need External Integration (you discovered an undocumented dependency). A Business Logic item may need splitting because part of it is really an adapter concern.

**Step 5 — Evaluate convergence.**

The fixed point is reached when ALL of the following hold:
- New discoveries = ∅
- Corrections = ∅
- Domain reclassifications = ∅
- UNVERIFIED items = ∅ (all VERIFIED or UNRESOLVED with documented reason)
- Answerable open questions = ∅
- Coverage audit passes (see below)

**Coverage audit.** Before claiming convergence, list every category of file in the project and whether you examined it: source code, tests, configs, CI/CD, scripts, documentation, type definitions, migration files, manifests. For each unexamined category, state why it's irrelevant to this plan. If you can't justify the omission, you haven't converged — go back and check it. A fixed point reached by narrow search is a false fixed point. The investigation must be broad before it can be shallow.

If any condition fails → iterate (back to Step 1).
If all conditions hold → proceed to Phase 3 (plan graduation).

**Hard cap: 5 iterations.** If the plan has not converged after 5 passes, the remaining instability itself is information. Produce the plan with gaps explicitly marked. Tell the user what's still moving and why convergence hasn't been reached — this usually indicates either a poorly-scoped task or genuinely complex integration surface that needs a spike.

## Phase 3: Graduated Plan

When the fixed point is reached (or the cap forces termination), perform a decomposition check before producing the final artifact.

**Decomposition check (Parnas).** Review every Custom Code item. Each CC item should hide exactly one design decision — if an item bundles multiple responsibilities (e.g., "discovery + validation + reporting"), split it. The test: could two different developers implement two halves of this item independently? If not, it's two items wearing a trenchcoat. Apply the same check to BL items: each should express one domain rule or one workflow, not a grab-bag.

**Concreteness check.** For each CC item, determine: is this structural conformance (reproducing a known pattern) or design work (inventing logic)? Structural conformance items — config files, manifests, frontmatter, CI workflows, directory structures — must include the actual template inline, adapted from the codebase reference. Design items — adapters, pipelines, utilities — get a contract (inputs/outputs/invariants) instead. Requiring a file lookup for a conformance task is extraneous cognitive load (Sweller); including implementation detail for a design task causes fixation (Einstellung).

Produce the final artifact:

```
## Plan — Final (Pass N, <converged | capped at 5>)

### Task: <what we're building>
### Convergence: <N passes, summary of what each pass discovered>

---

### Business Logic
- [ ] BL-<n>: <item>
      DOMAIN RULE: <the business rule or invariant this implements>
      INDEPENDENT OF: <confirm no framework/library dependency>

### Custom Code
- [ ] CC-<n>: <item>
      PATTERN: <existing codebase pattern this follows>
      REFERENCE: <file:line where the pattern is established>
      HIDES: <what design decision this module encapsulates — Parnas>
      [If structural conformance — include TEMPLATE:]
      ```
      <actual config/manifest/frontmatter content, copied from the reference and adapted>
      ```
      [If design/logic — include CONTRACT:]
      INPUTS: <what this module receives>
      OUTPUTS: <what this module produces>
      INVARIANTS: <what must hold>

### External Integration
- [ ] EI-<n>: <item> — STATUS: <VERIFIED | UNRESOLVED>
      LIBRARY: <name@version>
      API SURFACE: <specific functions, methods, types, endpoints used>
      DOCS: <URL or reference used for verification>
      DELTA: <what differed from initial assumption, if anything>
      GOTCHAS: <version constraints, deprecation warnings, error cases to handle>

---

### Unresolved Items
- <anything that could not be fully investigated, with reason and suggested resolution path>

### Risks
- <technical risks surfaced during investigation, each traced to the pass that found it>

### Implementation Order
1. <what to build first> — <why: dependency chain, risk reduction, or information gain>
2. <what depends on 1>
3. <what can parallelize>

### Epistemic Status
- Business Logic items: grounded in <codebase reading | requirements | domain analysis>
- Custom Code items: grounded in <existing pattern at file:line>
- External Integration items: <N VERIFIED, M UNRESOLVED>
- Training data relied upon for: <nothing, ideally | list any items where docs were unavailable>
```

Present this to the user. The plan is a proposal — the user decides what happens next.

## Rules

These are not style preferences. Each prevents a specific, observed failure mode.

1. **Evidence over recall.** Every claim about the codebase must cite a file you read. Every claim about an external API must cite documentation you retrieved. Training data informs which questions to ask — it does not answer them. (Prevents: hallucinated APIs, phantom modules, stale assumptions.)

2. **Domain boundaries are load-bearing.** If an item touches an external API, it is External Integration even if it also contains a business rule. Split it: one item for the rule (BL), one for the integration (EI). Mixing domains in a single item hides the dependency and makes the plan lie about what's independent. (Prevents: domain contamination, false independence claims.)

3. **The codebase is the source of truth for patterns.** Do not invent conventions when the project has established ones. Do not propose patterns that contradict what the codebase already does unless you explicitly flag the deviation and justify it. Cite the file where the pattern lives. (Prevents: style drift, convention conflicts, unnecessary novelty.)

4. **WHAT and WHERE, not HOW — with one exception.** The plan identifies work items, their domain, their dependencies, and their integration points. It does not contain pseudocode, implementation sketches, or algorithm choices for logic work. The developer handles the how.

   **Exception: structural conformance items get concrete artifacts.** When a CC item's task is reproducing an established pattern (config files, manifests, YAML frontmatter, CI workflows, directory structures), include the actual template or example inline in the plan. The reasoning is grounded in two converging bodies of evidence:

   - *Specification theory* (Jackson, Adzic): for items where the format IS the requirement — external interfaces, config schemas dictated by tools, structural patterns dictated by convention — concreteness is specification, not implementation. There is no "how" to defer; the shape is the what.
   - *Cognitive science* (Sweller's split-attention effect, worked example research): requiring the implementer to look up a pattern at file:line while holding the plan item in working memory is pure extraneous cognitive load. When the task is conformance (reproduce this pattern), inline examples improve accuracy. When the task is design (invent a solution), abstract descriptions prevent fixation (Einstellung effect).

   **The test:** Is the implementer being asked to *reproduce a known pattern* or *design something new*? If reproduce → include the artifact. If design → specify the contract (inputs, outputs, invariants) and let them work. (Prevents: premature implementation decisions on logic work; split-attention overhead and pattern deviation on conformance work.)

5. **Uncertainty is signal, not shame.** "I could not verify this" is infinitely more useful than a confident guess. UNRESOLVED with a reason gives the implementer information. VERIFIED-by-hallucination gives them a trap. (Prevents: false confidence, buried unknowns, time wasted on wrong APIs.)
