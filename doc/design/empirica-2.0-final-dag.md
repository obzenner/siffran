# Empirica 2.0 — final implementation DAG

**Status:** Implemented. D0–D17 release gates completed for Empirica 2.0.0.

**Baseline:** Empirica 1.3.0 at `4257c8d`.

**Historical behavioral reference:** Empirica 0.7.0 at `3150f4d`, the last coherent implementation
before ADR-30/31 host-neutral core and store unification.

**Existing worktree delta:** the uncommitted two-fold-evidence change in this worktree is evidence,
not an accepted implementation. Its host-neutral verdict extraction and complete-leaf-set evaluation
are candidates to retain; its immutable write-time freshness (`2F5`) is rejected and must be reversed.

## 1. Objective

Ship Empirica 2.0 as a **smaller, clearer, host-neutral implementation of the original empirical
convergence concept**, with:

- no loss of supported behavior;
- less hand-maintained runtime code than 1.3;
- fewer independent state and policy representations;
- one owner for every semantic fact;
- a pure functional decision core and explicit imperative ports;
- progressive, context-aware public contracts for agents;
- complete asynchronous-child semantics on every host advertised as supported;
- no backwards-compatibility implementation;
- clear, structured, debuggable reason codes, lifecycle records, and boundary views.

The release is not complete merely because tests pass. It must be **simpler by inventory** and must
preserve the full behavioral matrix in §4.

## 2. Non-negotiable invariants

1. A deterministic harness exit code is the only approver for a machine-checkable claim. Agentic
   review may block; it never approves machine evidence.
2. The author never grades its own convergence. Audit is a distinct principal.
3. `stopped_budget`, `stopped_frozen`, and other terminal non-converged runs never later report
   convergence.
4. Knowledge is append-only. Supersession adds history; it does not rewrite it.
5. Missing or corrupt active graph/state fails closed.
6. Claim state is derived on read, never persisted as an author-editable verdict.
7. Research precedes experimentation.
8. A spike is bound to every declared dependent file. A current digest mismatch makes the spike
   stale; recovery requires a new deterministic execution/re-gate.
   **Freshness evaluation rule:** every run evaluation view that can report derived claim/evidence
   state, a Block reason, audit coverage, obligations, restore state, or convergence assembles a fresh `Workspace.observe` result for
   every file bound by every active spike. Pure core reads only those explicit observations and
   performs no I/O. Mismatch, absence, or unreadability cannot approve. Re-gate executes only stale
   active spike heads and appends superseding evidence; it never mutates the old record.
9. Idle waits and repeated checks do not consume derivation passes.
10. Equal schemas do not imply equal host enforcement. Every host capability claim is explicit and
    tested.

## 3. AI-facing conceptual and context budget

An author agent should need only these concepts:

1. goal;
2. outstanding obligation;
3. valid next action;
4. trusted observation;
5. residual/handoff;
6. terminal distinction;
7. roles: author, deterministic harness, auditor, human.

The normal agent path must not require knowledge of GSN edge legality, in-toto wire shape, store
paths, content hashes, nonces, reservations, CAS sequences, contract revisions, adapter envelopes,
or artifact writer ownership.

### Progressive disclosure

- **Bootstrap:** contract id/version/digest, goal, core invariants, current host limits, and how to
  retrieve details.
- **Action-local:** each Block/hold exposes stable `reason_code`, affected obligation, witness state,
  and one or more `next_actions`; only relevant public-contract section IDs are injected.
- **On demand:** `GetContract(section=...)` returns one complete canonical section.
- **Full:** complete contract and schemas remain discoverable but are never injected automatically.
- **Compaction/restore:** re-inject contract identity plus active obligations and relevant sections,
  not the whole contract.
- **Auditor:** receive audit/evidence clauses relevant to the dossier, not unrelated author guidance.

Section selection is a pure presentation mapping from `operation_context`, ordered reason codes
already emitted by evaluation, and terminal run status. It never inspects obligations, witnesses,
budgets, child records, workspace observations, or host capabilities to infer domain reasons.

## 4. Required feature-preservation matrix

| Capability | Required 2.0 behavior |
|---|---|
| Activation | One obvious invocation; model sees goal, modes, contract identity, host capability |
| Route | Route precedes investigation; ordering violation is witnessed and feedback is action-local |
| Claim graph | Derived state; only claims on committed support scope gate; malformed graph fails closed |
| Research | Every claim requires externally observed Fold-1 evidence bound to current wording |
| Experiment | `needs-experiment` additionally requires a real passing harness execution |
| Cross-request evidence | Research and spike may arrive in separate actions and combine on read |
| Freshness | Current file mismatch makes prior spike stale immediately on evaluation |
| Re-gate | Reruns only stale spikes; result is another append-only attestation |
| Refutation | Requires real refuting evidence; discarded claim remains in history |
| Budget | Spawn and derivation limits are enforced; idle waiting costs no pass |
| Async agents | Durable requested/started/pending/completed/failed/cancelled/timed-out/orphaned lifecycle |
| Audit | Distinct principal; dossier delivered privately where host supports it; host observes verdict |
| Freeze | First-write-wins; committed scope still gates/audits; later findings are explicit deferred handoff |
| Terminal | Stopped/frozen/budget results remain honestly non-converged |
| Compaction | Preserve untrusted-data delimiters, goal, modes, active/deferred obligations, witness states, pending children, and next actions |
| Progressive contract | Agent gets enough context for its next valid action without a full contract dump |
| Host honesty | Unsupported enforcement is labelled unsupported, never implied by schema parity |

No row may be removed without an explicit maintainer decision and a major-contract amendment.

## 5. Architecture ownership map

| Layer | Owns | Must not own |
|---|---|---|
| `contracts/empirica/v2` | Public behavior, protocol shape, enums, reason-code shape, next-action IDs, progressive section links, host capability vocabulary | Algorithms, host events, filesystem paths, model/UI prose |
| Pure evidence core | Statement projection, claim binding, fold/order requirements, comparison of recorded and current observations, evidence verdict codes | Filesystem, process, clock, transport, persistence, rendering |
| Pure claim/audit/budget/convergence core | Graph traversal, state derivation, audit coverage, budgets, terminal decision | JSON decoding, CAS, hooks, host semantics |
| Evaluation snapshot | Immutable explicit input joining graph, active evidence, current workspace observations, audit/child state, budget, scope | Repository writes or host UI |
| Application orchestration | Load ports, build snapshots, call pure core, apply transactions/CAS, append artifacts, update pointers | Re-deciding policy, direct host rendering |
| Workspace port | Normalize paths and report current digest/absent/error observations | Claim adjudication, persistence authority, UI messages |
| Spike harness port | Run subprocess and seal command/result/files observations | Claim/audit/convergence decisions |
| Operational repository | Mutable status, counters, one-record async children, pointers, frozen scope | Evidence truth, derived claim state, separate ticket/reservation entities, phase machine |
| Artifact repository | Append-only graph/evidence/spike-request/audit facts | Current lifecycle counters/status or obligation projections |
| RunView projection | Pure ephemeral projection from evaluation snapshot | Independent adjudication, editable state, or persisted revision history |
| Async child lifecycle | Child identity and requested/started/pending/completed/failed/cancelled/timed-out/orphaned transitions; exact admitted-launch/native-start binding where the tier supports it; idempotent completion | Evidence truth or convergence |
| Claude/Codex/Pi adapters | Translate native events, provide configured capabilities, render structured results, own native child IDs | Domain policy, evidence verdicts, budget or audit decisions |

### State authority rules

- The graph and active evidence are authoritative argument content.
- Operational pointers select current append-only artifacts.
- `evaluation.derive_claims` remains the only claim-state and scoped-dependency derivation.
- Active/deferred obligations are ephemeral RunView projections and are never persisted as a
  revisioned obligation-contract artifact or alternative adjudicator.
- Host correlation maps are transport caches; durable async-child state belongs to the application.
- Current workspace observations are ephemeral evaluation inputs; spike attestations remain append-only.
- Frozen composite leaf verdicts are removed.

## 6. Declarative and SSOT rules

### Canonical declarative data

- v2 protocol commands/actions/statuses/intents;
- public-contract sections and clauses;
- stable reason codes and parameters;
- allowed next-action IDs;
- reason-code → contract-section mapping;
- host capability vocabulary/declarations;
- validated deployment settings and defaults;
- actor-policy exclusions.

### Pure code, not configuration

- graph traversal and canonical serialization;
- digest algorithms;
- fold/order/freshness comparison;
- audit coverage;
- budget derivation;
- convergence and terminal transitions;
- run-view projection;
- process supervision and output safety.

Python/TypeScript mirrors of canonical schema enums must be generated or mechanically checked.
No independently maintained mirror is accepted. The generic obligation Python package remains
canonical; vendored/generated copies require parity checks. Algorithms are not moved to JSON merely
to call them config-driven.

## 7. Reduction and complexity gates

### Hard release gates

1. Release inventory counts **all executable code reachable from supported entrypoints**, including
   generated and vendored runtime code after expansion; only tests, inert data, and documentation
   are excluded. No relocation or indirection may satisfy the budget. Release requires a net
   reduction in this effective inventory relative to `4257c8d`, or a maintainer-approved
   component-level exception explaining which preserved feature requires the increase.
2. `application/service.py` must become a coordinator: it contains no evidence, audit, budget,
   convergence, context-selection, or contract-revision policy. No function may both load
   repositories, adjudicate policy, and commit state. File/LOC metrics are review signals, not
   standalone gates; the gate is reduced reachable runtime complexity with no policy relocation.
3. Pi `index.ts` must be wiring: event registration delegates to command, lifecycle, correlation,
   and renderer modules. Splitting a file without removing responsibilities does not satisfy this
   gate.
4. No new persisted representation without an authority declaration and projection invariant test.
5. No generic framework without at least two current consumers and one named defect it prevents.
6. No free-form reason/status/agent-marker text may control domain behavior.
7. No full public-contract injection on normal turns.

### Required subtraction

- delete `migrate_legacy.py`, Make target, tests, and validator exception;
- reject v1 rather than adapt it;
- remove old-state missing-field defaults;
- remove frozen `evidence_leaf.verdicts`;
- remove direct caller-supplied `evidence ok=true` approval;
- remove duplicate adapter verdict logic;
- remove obsolete compatibility re-exports/comments/fixtures;
- remove string/regex launch classification wherever structured host data exists;
- remove or replace nudge machinery only after equivalent action-local/next-turn guidance is proven;
- remove stale policy branches after canonical reason/action registries exist.

Historical fail-closed recognition, `supersedes`, operational failure fallbacks, and clockless
operation are current safety behavior, not compatibility code.

## 8. Async-child contract

Every fully supported host implements the canonical child states and transitions owned by
`contracts/empirica/v2/public-contract.json`; this section does not define another transition table.
Host start evidence admits canonical `launching -> pending`; pre-start rejection admits
`launch_rejected` and sets the single child record's refunded fact.

Durable identity is the run-scoped `child_id`; purpose, state, host profile, optional native ID,
spent/refunded fact, deadline, and private completion capability are fields of that single record.
Pending work survives compaction/reload, consumes no derivation pass, does not invite duplicate
spawns, and has a
bounded status/recovery path. A late result may be appended but never turns a terminal stopped run
into convergence.

Current 1.3 status is not sufficient: Claude is partial, Pi forces audit foreground, and Codex cannot
observe child output. **D1-H records one capability row per advertised host and lifecycle transition**
(launch, pending, completion, failure, cancellation, timeout, orphan recovery, audit privacy, and
completion veto), with official/live API evidence and an explicit stop decision. An unsupported
transition returns a typed residual/capability reason—never implied success or a silent fallback. A
host is advertised as fully supported only if it passes async conformance; support-tier changes are
maintainer decisions.

## 9. DAG

```text
H0 Historical 0.7 AI-UX audit                         DONE
H1 Preservation matrix / conceptual kernel            DONE
A0 1.3 architecture, SSOT and compatibility audit     DONE
 │
 ▼
D0 Baseline freeze: feature, authority, code/context metrics
 │
 ▼
D1 Public behavior + ownership decisions
 │
 ▼
D1-H Host capability matrix + explicit unsupported stop paths
 │
 ├───────────────────────┬────────────────────────┐
 ▼                       ▼                        ▼
D2 Contract/schema       D3 Architecture          D4 Behavioral + historical UX
fixtures (red-first)     validators (red-first)   conformance tests (red-first)
 │                       │                        │
 └───────────────────────┴────────────┬───────────┘
                                      ▼
D5 Pure evidence/workspace/spike contracts
 │
 ▼
D5-F Live observation/freshness/no-I/O proof
 │
 ▼
D6 Protocol v2 + strict current state + compatibility subtraction
 │
 ▼
D7 Evaluation snapshot + application/service decomposition
 │
 ▼
D7-W Single-writer transaction/CAS/idempotency proof
 │
 ├──────────────────────────────┐
 ▼                              ▼
D8 Async-child lifecycle        D9 Public-contract resolver/context selector
 │                              │
 └───────────────────┬──────────┘
                     ▼
          ┌──────────┼──────────┐
          ▼          ▼          ▼
D10-C Claude      D10-X Codex  D10-P Pi
          └──────────┼──────────┘
                     ▼
D11 Generated mirrors + progressive skill/docs + 2.0.0 bump
 │
 ▼
D3-M Make suite/release dependency validation
 │
 ▼
D12 Full host-neutral and host-conformance gates
 │
 ▼
D13 Real-host dogfood: async, stale/regate, audit, compaction
 │
 ▼
D14 Parallel read-only review: correctness + simplicity + AI UX
 │
 ▼
D15 Single fix worker, then focused re-review if needed
 │
 ▼
D16 Subtractive audit + metrics/feature parity report
 │
 ▼
D17 Final check, final green commit, release-check/commit-check, PR
```

## 10. Node contracts

### D0 — Baseline freeze

**Outputs**

- feature-preservation matrix with executable test location;
- semantic-fact ownership table;
- baseline hand-maintained LOC/module/function metrics;
- model-visible token/context samples for StartRun, ordinary Block, stale spike, audit pending,
  compaction;
- compatibility/deletion inventory;
- current host async capability evidence.

**Gate:** every feature and semantic fact has one intended 2.0 owner.

### D1 — Public behavior, ownership, and capability decisions

**Outputs**

- canonical contract index/sections;
- evidence freshness and re-gate contract;
- async-child lifecycle and budget semantics;
- reason codes, next actions, progressive selection table;
- host capability tiers based on tested APIs;
- strict old-run refusal: preserve/display goal when possible, instruct fresh run, assert no
  convergence claim.

**Gate:** maintainer review. No implementation continues until accepted.

### D1-H — Host capability matrix and stop decisions

For Claude, Codex, and Pi, record official/live evidence for launch, pending, completion, failure,
cancellation, timeout, orphan recovery, audit privacy, completion veto, and progressive contract
delivery. Each unsupported transition has a typed residual/capability response and an explicit
maintainer-approved host support tier. This node gates D2, D4, D8, D10, and D12.

### D2 — Contract/schema fixtures

Create canonical v2 request/response/public-contract schemas and fixtures. Remove v1 from active
runtime/package. Validate section links, reason/action references, digest determinism, and schema
examples.

### D3 — Architecture validators

Add `scripts/validate_empirica_architecture.py` and Make target
`empirica-architecture-check`, included in `check-static`. It must validate:

- allowed layer imports and no adapter → domain policy dependency;
- absence of banned compatibility files/actions/fields;
- schema ↔ Python ↔ TypeScript discriminator parity;
- every reason code references a public-contract clause and next action;
- every host capability has a conformance test declaration;
- generated/vendored files are current;
- architecture budgets from a small reviewed config, measured over effective reachable runtime
  code so relocation/generated indirection cannot game the report;
- no old direct evidence approval or frozen verdict field.

The validator checks structural rules only. Semantic design remains code review plus behavioral tests.
D3 also emits the exact Make dependency graph: every new target belongs to an existing subject
suite, `check`/`check-ci` retain their documented scope, and `release-check` invokes
`commit-check BASE=<review-base>` after `check`.

### D4 — Behavioral conformance tests

Red-first tests cover the complete §4 matrix, especially:

- separate research and spike actions combine;
- arbitrary caller verdict cannot approve;
- every state-bearing operation freshly observes bound files; edit/delete/absent/unreadable makes
  the spike stale or indeterminate immediately, while repeated identical observations are stable;
- pure core contains no filesystem access;
- re-gate appends a new deterministic result and restores approval;
- idle/pending audit consumes no pass;
- async completion is exactly once across reload and out-of-order completion;
- terminal result never reconverges;
- every Block includes structured reason/obligation/witness/next action;
- route-before-investigation violation is witnessed and action-local;
- freeze is first-write-wins and committed scope remains audited;
- compaction preserves untrusted-data delimiters and restores active/deferred obligations plus
  pending child state;
- deterministic context selection has negative/ambiguity fixtures: identical structured inputs
  yield identical sections, unknown codes fail closed to a minimal safe section, and irrelevant
  sections are absent;
- v1 and old state are rejected clearly.

### D5 and D5-F — Pure evidence/workspace/spike contracts and freshness proof

Retain useful portions of the existing two-fold diff, reverse `2F5`, and introduce only the minimal
ports:

```text
Workspace.observe(paths) -> current observations
SpikeHarness.run(command, observations) -> sealed attestation
```

D5 defines the contracts. D5-F proves that **every** state-bearing snapshot construction obtains
fresh observations for every active spike, that absent/error observations fail closed, digesting is
deterministic, and pure core performs no I/O. D7 cannot assemble an evaluation snapshot without
these explicit observations. Adapters do not adjudicate.

### D6 — Protocol v2 and subtraction

Implement schema-driven v2 dispatch, strict current-state decoding, and the required deletion list.
No migration/import path. Replace test shortcuts with real evidence leaves and harness fakes.

### D7 and D7-W — Application decomposition and single-writer proof

Introduce immutable `EvaluationSnapshot`, pure `evaluate_snapshot`, pure `RunViewProjection`, and
imperative `commit_decision`. `EmpiricaService` becomes command/transaction coordination. The
application transaction coordinator is the sole writer of operational state and append-only domain
artifacts; adapters emit commands only. Artifact append and pointer CAS form one retryable
transaction protocol. D7-W proves projection invariants, concurrent/stale-writer retries,
idempotency, append-then-pointer ordering, and fail-closed reads before D8/D10/D12.

### D8 — Async child lifecycle

Implement the durable host-neutral state machine, exact admitted-launch/native-start binding where
supported, idempotent completion,
reload recovery, timeout/orphan behavior, and late-result rule. No evidence or convergence policy.

### D9 — Public-contract discovery

Implement static canonical contract loading/digest, `GetContract(section)`, bootstrap card, pure
context selector, and compaction subset. No template/rules engine and no LLM relevance selector.

### D10 — Host adapters

One writer, sequential host changes; read-only reviews may run in parallel. Each adapter translates
native events into the same application commands, renders stable reasons, supports progressive
contract access, and passes its declared async capability. Host-specific gaps remain explicit.

### D11 — Mirrors, docs, and version

Generate or mechanically validate cross-language discriminators. Update skill to the concise author
mental model and discovery path. ADRs explain decisions without copying normative contract prose.
Run `make bump PLUGIN=empirica PART=major` only after all behavior is implemented; both manifests
become `2.0.0` and generated tables are refreshed through supported tooling.

### D12–D13 — Verification and dogfood

Run subject suites throughout and full `make check`. Real-host flows exercise the actual agent UX,
not only fake host callbacks. Required live scenarios: async auditors, concurrent/out-of-order child
completion, reload, stale spike, re-gate, audit verdict, Block next actions, compaction/restore, and
terminal handoff.

### D14–D15 — Review loop

Fresh read-only reviewers cover:

- correctness/security/invariants;
- simplification/SSOT/functional boundaries;
- AI UX/progressive context/host honesty.

Parent dispositions findings; one GLM fix worker applies accepted fixes; repeat focused review only
for material changes.

### D16 — Subtractive audit

Produce and review:

- before/after effective reachable runtime inventory (including generated/vendored executable code);
- removed components and representations;
- modules/functions above budget;
- semantic fact ownership and mirror checks;
- model-visible tokens by situation;
- feature-preservation results;
- residual host limitations.

Release blocks unless runtime code is net reduced and every §4 feature remains, or the maintainer
explicitly accepts a documented exception.

### D17 — Final release gate

Run `make check` on the final working tree, inspect the diff, then create the final authorized green
commit. Run `make release-check BASE=<review-base>` on the committed branch (including
commit-message validation), verify the tree remains clean, and only then push/open or update the PR.
Commit, push, and PR actions still require explicit user authority at execution time.

## 11. Test and Make target plan

Add lifecycle targets with `##` help text:

```text
make empirica-contract-check       # canonical contract, schemas, references, fixtures
make empirica-architecture-check   # layers, banned compatibility, mirrors, budgets
make empirica-core-check           # pure core and functional-boundary tests
make empirica-async-check          # host-neutral async lifecycle tests
make empirica-host-check           # deterministic adapter conformance for all hosts
make empirica-context-check        # progressive-disclosure/context fixtures
make empirica-metrics              # before/after code/context report
make commit-check BASE=origin/main # Conventional Commit validation for branch range
```

Exact composition, validated by D3-M:

- `empirica-contract-check`, mirror/reference checks, and architecture structure → `check-static`;
- pure core, snapshot, workspace-fake, async state machine → `check-core`;
- Claude adapter conformance → `check-claude`;
- Codex adapter conformance → `check-codex`;
- Pi adapter/live bridge conformance → `check-pi`;
- `release-check BASE=<review-base>` → `check` followed by `commit-check BASE=<review-base>`.

Each target appears in `make help`. Pi remains optional in CI only where the runner lacks Node/Pi;
release requires a recorded local Pi gate.

Architecture tests must prevent recurrence, not freeze incidental file layout. Prefer dependency and
semantic parity assertions over snapshots of complete files.

## 12. Conventional commit plan

Use Conventional Commits for reviewable, green milestones. Do not commit red tests; retain red-first
evidence in test/report artifacts, then commit each green slice.

Suggested sequence (adjust scopes, preserve ordering):

1. `docs(empirica): freeze the 2.0 public contract and ownership map`
2. `test(empirica): add v2 behavioral and architecture conformance`
3. `refactor(empirica)!: centralize evidence evaluation and live freshness`
4. `refactor(empirica)!: introduce protocol v2 and remove legacy surfaces`
5. `refactor(empirica): separate evaluation snapshots and contract projection`
6. `feat(empirica)!: model durable async child lifecycles`
7. `feat(empirica): add progressive public contract discovery`
8. `feat(empirica)!: align Claude Codex and Pi adapters with v2`
9. `docs(empirica)!: release empirica 2.0.0`

Every breaking commit uses `!` and a `BREAKING CHANGE:` footer naming the removed wire/state behavior.
Each commit is independently green for the relevant focused suites; `make check` is green before the
final commit/PR.

### Commit convention enforcement

Implement a small dependency-free `scripts/validate_commit_messages.py`, invoked by
`make commit-check BASE=<base>`. It validates the branch range against Conventional Commits.
Breaking semantics remain in one machine-checked v2 removal/contract manifest; the validator
requires `!` or `BREAKING CHANGE:` when a commit touches a declared breaking removal, rather than
maintaining a second hard-coded removal list. Include it in `release-check`, not ordinary
`make check`, because working-tree checks may run before commits exist. Do not add Node commitlint,
Git hooks, or another package-manager dependency solely for this rule.

## 13. Stop rules

Stop and return to the maintainer if:

- an official host API cannot meet the promised async tier;
- a feature-preservation row would be weakened;
- a new state authority is required;
- net runtime code cannot be reduced without dropping behavior;
- a proposed config moves an algorithm out of pure code;
- progressive discovery cannot supply the next action reliably;
- review finds a security/product decision outside this contract;
- release would require compatibility support.

## 14. Definition of done

Empirica 2.0 is done only when:

- every invariant and feature row has external evidence;
- public contract and reason/action registry are the discoverable agent-facing SSOT;
- supported hosts pass declared async and behavior conformance;
- adapters contain translation, not domain decisions;
- core is pure over explicit inputs;
- current workspace changes invalidate bound spikes and deterministic re-gate recovers them;
- no compatibility or test-only approval path remains;
- runtime code is net reduced and hotspot budgets pass;
- StartRun, Block, pending audit, stale spike, compaction, and terminal contexts are concise and
  actionable;
- conventional commits and all Make gates pass;
- final read-only review has no unresolved blocker;
- parent has inspected the final diff and residual capability matrix.

## 15. Fleet execution model

### Authority

- **Parent/orchestrator:** owns every product, architecture, scope, acceptance, and finding-disposition
  decision; writes/finalizes milestone specs; runs deterministic verification; approves promotion.
- **Luna (`bedrock--gpt-5.6-luna`): discovery only.** Produces semantic dependency/data-flow and
  blast-radius maps with `file:line` evidence. It must not recommend, prioritize, accept/reject,
  adjudicate, or edit.
- **Haiku (`bedrock--claude-haiku-4.5`): discovery only.** Produces fast concrete inventories of
  files, callers, schemas, tests, Make targets, configuration, host events, and commands. It must not
  recommend, prioritize, accept/reject, adjudicate, or edit.
- **GLM (`evroc--glm-5.2`): sole implementation writer.** Implements only a parent-frozen milestone
  spec, reports decisions not covered by the spec instead of making them, and never commits/pushes.
- **Terra (`bedrock--gpt-5.6-terra`): correctness/invariant reviewer.** Read-only; checks behavior,
  state transitions, security/trust, concurrency, and test adequacy against the frozen spec.
- **Sol (`bedrock--gpt-5.6-sol`): simplicity/architecture reviewer.** Read-only; checks subtraction,
  SSOT, functional boundaries, declarative ownership, config discipline, and context cost against
  the frozen spec.
- **Human/operator:** supplies live Claude/Codex/Pi host evidence and makes final support-tier,
  release, commit, push, and PR decisions.

No child may launch another subagent. Discovery output is evidence, never authority. Reviewer scores
are not gates; concrete findings are adjudicated by the parent.

### Per-milestone fleet protocol

```text
1. Parallel discovery
   - Luna: semantic flow/dependency/blast radius
   - Haiku: concrete files/callers/tests/config/host surfaces
2. Parent synthesis
   - resolve discrepancies from source
   - write one frozen milestone implementation spec
3. One GLM writer
   - only writer in the experimental worktree
   - focused tests and required handoff
4. Parent deterministic verification
   - inspect changed code, run focused Make gates, reproduce key behavior
5. Parallel read-only review
   - Terra: correctness/invariants
   - Sol: simplicity/SSOT/FP/reduction
6. Parent disposition
   - blocker / fix now / defer / reject with evidence
7. One GLM fix run when required
8. Focused re-review for materially changed surfaces
9. Parent milestone acceptance
10. Conventional green commit only with explicit user authorization
```

Discovery and review may run in parallel. Writes are always serial. The parent does not edit while a
writer is active in the same worktree.

### Milestone implementation-spec template

Every GLM task receives a saved spec containing:

- goal and exact DAG node(s);
- accepted public-contract clauses/reason codes;
- authoritative owner and allowed dependency direction;
- explicit interfaces and data shapes;
- files/surfaces discovered, without prescribing accidental implementation;
- required behavior and red-first tests;
- invariants and feature-preservation rows;
- required removals and complexity budget;
- non-goals;
- focused Make commands and expected evidence;
- stop/escalation conditions;
- mandatory handoff shape: files changed, behavior, deletions, tests/commands with exit codes,
  before/after metrics, unresolved decisions, residual risks, no staged/committed files.

An implementation agent may choose local code structure inside this contract; it may not choose new
product semantics, compatibility behavior, state authority, host support tier, or dependency
boundary.

### Fleet by DAG milestone

| Milestone | Discovery emphasis | GLM implementation slice | Review emphasis |
|---|---|---|---|
| D0–D1-H | historical clauses, semantic owners, official host async APIs | canonical contract/ownership/capability artifacts only | contract consistency and scope |
| D2–D4 | existing tests/schemas/Make and historical behavior | red-first conformance plus architecture validators | test independence and non-tautology |
| D5–D5-F | evidence callers, workspace paths, regate flows | pure evidence + minimal workspace/spike seams | freshness, no-I/O core, reduction |
| D6 | all v1/state/compat callers | v2 dispatch, strict state, compatibility deletion | fail-closed behavior, feature parity |
| D7–D7-W | service transactions/CAS/projections | snapshot, projection, coordinator decomposition | concurrency, SSOT, actual simplification |
| D8 | native child events and host gaps | host-neutral durable async lifecycle | exactly-once, reload, terminal safety |
| D9 | current model-visible messages/compaction | resolver, reason/action registry, context selector | progressive disclosure and token cost |
| D10-C/X/P | one host at a time | thin host adapter alignment, serially | native behavior and capability honesty |
| D11–D13 | mirrors/docs/targets/live flows | generation, concise skill, version, fixes | drift, host parity, real UX |
| D14–D16 | final diff and reachable runtime inventory | accepted fix-only pass | correctness plus subtractive audit |

## 16. Experimental branch and clean PR promotion

All D0–D16 implementation occurs on a dedicated **experimental branch/worktree**. The current
`fix/empirica-twofold-eval-time` worktree is treated as experimental evidence and should be renamed
or replaced with `experiment/empirica-2.0` before implementation; its existing uncommitted two-fold
changes are not implicitly accepted.

### Experimental branch rules

- one active writer;
- conventional green milestone commits only after explicit user authorization;
- temporary probes/reports live outside production paths or are clearly marked experimental;
- no push/PR from the experimental branch;
- each milestone records accepted commit(s), gates, live evidence, and residuals in a promotion
  manifest;
- failed approaches are deleted, not carried forward as dead alternatives.

### Promotion protocol after D16 convergence

1. Freeze the accepted experimental tree and promotion manifest.
2. Inventory and remove temporary probes, caches, generated drift, stale reports, untracked files,
   debug logging, disabled code, compatibility leftovers, and obsolete worktrees.
3. Fetch latest `origin/main`; confirm the accepted base and resolve upstream changes in the
   experimental branch first.
4. Create a **new clean worktree and PR branch from latest main**, using repository naming
   conventions (proposed `feat/empirica-2.0`). Never turn the experimental worktree into the PR
   worktree.
5. Replay only accepted conventional commits (normally curated cherry-picks). Do not merge the
   experimental branch wholesale. If experimental commits are noisy, reconstruct clean green
   commits from the accepted diff while preserving attribution and test evidence.
6. Compare the promoted production/test/doc tree against the accepted experimental result. Any
   intentional difference is listed in the promotion manifest; no unexplained delta is allowed.
7. Run focused suites, full `make check`, live host acceptance where required, effective-code/context
   metrics, and final read-only Terra/Sol review on the **PR branch**.
8. Run `make release-check BASE=origin/main`, inspect the final diff and clean working tree, then
   push/open the PR only with explicit user authority.
9. Keep the experimental branch/worktree until PR-branch equivalence and gates are proven; then
   delete its worktree/branch and all temporary artifacts with explicit confirmation.

The PR branch is therefore a curated, reproducible implementation of the accepted DAG—not the
historical record of exploration.
