# Empirica governed planning and strictness recovery — worklog

**Status:** verified inventory accepted by Astra; safe as design/implementation baseline subject to open decisions; implementation has not started  
**Worktree:** `obzenner/empirica-governed-planning`  
**Baseline:** `413b86813535af89d4c621d8eb5b60eb60cc1561` (Empirica 2.0.1 packaging/MCP change)  
**Historical comparator:** `3150f4d0423d5949a43968c8ac44495c4d881b47` (Empirica 0.7.0)  
**Rule:** production changes wait until contract decisions, red tests, implementation, fresh receipts,
and independent review agree.

## 1. Goal

Investigate and design the next Empirica slice around two explicit control policies:

1. **deliberative (default):** the agent proposes the exact claim scope and bounded resources; a
   human approves, amends, or rejects the digest-bound proposal before investigation begins;
2. **auto:** the service records and activates the agent-derived plan without the initial human
   approval pause, while preserving every evidence, audit, freshness, and terminal invariant.

Before implementation, establish whether 2.0.1 is actually stricter than 0.7.0, identify every
contract/implementation regression found in a live consuming run, and recover or deliberately amend
accepted assurance-graph guarantees lost in the v2 rewrite.

## 2. Evidence method and provenance

Status vocabulary:

- **VERIFIED:** primary source and/or executable probe establishes the statement.
- **QUALIFIED:** core statement is true but its original wording overstates or conflates behavior.
- **REFUTED:** evidence contradicts it.
- **UNVERIFIED:** plausible but not established; must not drive implementation as fact.
- **PROPOSAL:** possible design, not an existing guarantee or uniquely required solution.

Evidence inspected:

- current source/contracts at baseline `413b868`;
- historical source via `git show 3150f4d:<path>`;
- accepted ADRs 22, 30, 31, 42, 44; proposed ADR 34; frozen 2.0 architecture DAG;
- exact machine-local field run `project s256-28779e60… / session s256-f3433931…`;
- that run's append-only Git artifact ref and exact bound Claude child transcripts;
- bounded old/new graph, evidence, freeze, refutation, pass-accounting, and audit probes;
- current architecture check.

Recorded repository/external facts:

- worktree HEAD is `413b868` on `obzenner/empirica-governed-planning`;
- `gh pr view 28` reported PR #28 `MERGED` with head `413b868`;
- `make empirica-architecture-check` reports 9,440 effective runtime lines across 80 files, ceiling
  9,457, architecture OK;
- earlier direct public-tool test execution reported 10/10, but inventory conclusions do not rely on
  that historical execution; future lifecycle validation uses Make targets.

Astra round 1 independently reviewed the first draft and returned **REJECT**. That rejection found
material omissions in research validation, route enforcement, readable audit goal, frozen-scope
preservation, contract refutation semantics, trust-boundary wording, and total-run boundedness. The
parent independently reproduced those findings before this rewrite. The round-1 artifact is
`/tmp/astra-empirica-governed-planning-review.astra-inventory-review.md`.

## 3. Governed-planning gap

### GP-1 — No deliberative/auto control policy — VERIFIED

Current executable modes are only `multi_provider` and `cli_exec`
(`contracts/empirica/v2/request.schema.json:82-93`; Claude flags
`adapters/claude/invocation.py:13-17`; Pi flags `adapters/pi/src/translate.ts:39-42`). Public action
vocabulary has no plan proposal/approval (`contracts/empirica/v2/public-contract.json:11-17`). There
is no runtime/schema `control_mode`, proposal digest, `ProposePlan`, `ApprovePlan`, or `--auto` path.

0.7.0 also had only `multi_provider` and `cli_exec`, explicitly the two optional modes
(`3150f4d:plugins/empirica/skills/empirica/SKILL.md:487-497`; historical `hooks/modes.py`). It seeded
the claim graph directly at Step 2 (`SKILL.md:150-205`) and did not negotiate it with a human.

### GP-2 — The agent activates the claim graph without human approval — VERIFIED

Current `SKILL.md` §2 instructs the author to construct and submit the graph. A schema-valid
`ObserveAction(kind="graph")` immediately selects its graph artifact
(`core/evaluation.py:345-351`). There is no proposed state or human-decision transition.

The public contract says the human resolves judgment, budget, and product decisions
(`public-contract.json:195`) but defines no trusted human plan-approval action. The trusted
vocabulary is only `evidence_leaf`, `attribution`, `child_event`, and `audit_verdict`
(`public-contract.json:17`). Executable protocol and role prose therefore differ.

### GP-3 — Defaults/the author determine budget without human acceptance — VERIFIED

A run starts with `max_passes=8` and `max_spawns=1`
(`application/transaction.py:66-79`). `configure_run` is a public author action and can raise or
change ceilings subject only to not dropping below consumed work (`core/evaluation.py:372-381`). No
human approval or provenance is required.

0.7.0 was not stricter: a missing/unreadable ledger meant unbounded spawning
(`3150f4d:hooks/budget.py:127-147`), and the skill told the agent to set `max_spawns` while reserving
one slot for audit (`3150f4d:SKILL.md:319-337`). Neither version enforces budget consent.

### GP-4 — Trusted digest-bound human acceptance — PROPOSED SECURITY REQUIREMENT

If deliberation is selected as an enforceable threat-model property, a model-callable public tool
cannot prove human approval because the author controls that surface. A separate host-observed
human-originated decision, bound to the exact proposal digest, is the proposed design.

This is not the only conceivable UX and does not exist today. It follows the existing
proposer/accepter analogy but must be decided explicitly. Private ingress is not an OS capability:
accepted ADR 42 states a same-authority process can call adapter code
(`doc/adr/0042-...:60-63`). Hosts unable to expose trustworthy human origin may need to mark
machine-enforced deliberative approval unsupported, or the project may consciously choose a weaker
instruction-only negotiation. That is an open contract decision.

## 4. 0.7.0 → 2.0.1 strictness inventory

Strictness is dimensioned; there is no single monotone verdict.

| Axis | 0.7.0 | 2.0.1 | Verified verdict |
|---|---|---|---|
| Human claim approval | agent writes graph | agent submits graph immediately | absent in both |
| Human budget approval | optional agent-written ledger | defaults + public `configure_run` | absent in both |
| Auto vs deliberative | only provider/CLI modes | only provider/CLI modes | absent in both |
| Direct confidence scoring | author field, insufficient without evidence | confidence not graph input; internally derived | 2.0.1 stricter narrowly against typed scoring |
| Minimum research record | nonempty source and citation required (`3150f4d:evidence.py:330-374`) | payload/citation optional; source defaults to kind | **0.7.0 stricter** |
| Research truth authority | author-writable file; auditor re-reads | public author action; service-owned append | stronger write path/tamper evidence, unchanged truth authority, weaker minimum shape |
| Graph structure | typed GSN vocabulary, legal pairs, DAG | flat claims, shallow endpoint/type checks | **0.7.0 stricter** |
| Root/support scope | `SupportedBy` traversal/pruning | author `gating` booleans/frozen IDs | **0.7.0 structurally stricter** |
| Evidence/artifact ownership | same-user writable JSON | service-owned normal API + append-only Git history | 2.0.1 stronger normal-API integrity, **not** stronger same-user isolation |
| Spike binding | real exit code, callable writer/live files | sealed prerequisite, immutable capture, bounded subprocess, derived gate | 2.0.1 stronger binding; no process sandbox claim |
| Audit operation | predictable ticket + author-writable verdict | host-owned reservation, immutable dossier, private ingress | 2.0.1 stronger normal path; Claude completion currently broken |
| Identity | declared model; CLI mode could witness attribution but did not gate independence | concrete host-observed pair gates same/unverified | 2.0.1 stricter enforced independence |
| Routing | old final gate surfaced route verdict | explicit current `investigate` blocks before route, but Claude ignores denial and other actions do not require stamps | neither provides the claimed end-to-end hard ordering; current is not proven stricter |
| Freeze storage | first-write manifest | service-owned first-write IDs | stronger ownership, but 2.0.1 permits deleted frozen claims |
| Corrupt persisted state | defaults/legacy exceptions | strict v2 codec/classification | 2.0.1 stricter service decode; host reachability caveats remain |
| Terminal APIs | old normal Stop short-circuits terminal but manifest setter can rewrite | evaluator/transaction prevent later status or audit admission | 2.0.1 stricter through supported APIs, not same-user tamper isolation |
| Spawn cap | optional/unbounded default | finite CAS reservation, author-raiseable | stricter instantaneous cap, not a human-approved total-run bound |
| Total-run termination | old active Stop increments bounded variant | unresolved checks can repeat without pass spend; proposed ADR 34 is dead | **0.7.0 had a stronger live termination path** |
| Host portability | Claude hooks | host-neutral core + exact profiles | 2.0.1 stronger architecture, not equivalent host behavior |

**Conclusion:** 2.0.1 strengthens normal-API ownership, immutable-history checks, deterministic
execution binding, strict state decoding, and enforced identity comparison. Same-user execution
remains outside the protocol boundary. Minimum research validity and graph semantics are weaker;
route ordering is not end-to-end enforced; total-run boundedness regressed; and current Claude audit
completion is broken. “2.0.1 is globally stricter” is REFUTED.

## 5. Verified field and implementation findings

### F1 — Dead `core/budget.py` and stranded graph walkers — VERIFIED

No production caller reaches `budget.derive`, `ceiling_for`, or `open_gating_claim_count`.
`core/budget.py` alone calls `claims.gating_goals`; only tests import the budget module.
`evaluation.py` calls only `claim_rules.state_of` (`evaluation.py:153`).

ADR 34 is **proposed**, not accepted. Decide whether to adopt a corrected policy or delete the dead
module/tests and reject/supersede the proposal. The current half-state is contract drift, but neither
remedy is preselected.

### F2 — Audit reservation denial loses available typed cause — VERIFIED / QUALIFIED

`AuditProtocol.prepare` converts a non-Allow reservation to
`AuditProtocolError("audit reservation denied")` (`adapters/audit_protocol.py:103-109`), and Claude
prints the generic exception (`adapters/claude/lifecycle.py:165-168`). `budget.exhausted` is reachable
through that reservation. `audit.pending` is normally prechecked with its own message, though a race
can still flatten. The `host.async_*` evaluator reasons require an async request, while this protocol
currently always requests foreground; do not cite them as ordinary causes of this exact call.

### F3 — Default one-spawn cap cannot cover one ordinary child plus mandatory audit — VERIFIED

Default `max_spawns=1` (`application/transaction.py:70`). Every reservation, ordinary or audit,
checks/increments the same counter (`core/evaluation.py:382-409`). Therefore one ordinary child
exhausts the default before audit. Investigation does not always require a child; the defect is the
common child-assisted path, not logical impossibility of all investigation.

The field run used cap 4 and three reservations. That does not prove four is minimal. Separate
bounded investigation/audit counters are a proposal, not the only safe correction.

### F4 — Claude verdict extraction is incompatible with exact handbacks — VERIFIED

The parser requires exactly one fenced block in a supplied string (`adapters/audit.py:29-39`). Claude
`_transcript_observation` extracts only assistant text (`adapters/claude/lifecycle.py:239-264`), and
`subagent_stop_main` parses `last_assistant_message or transcript_final` (`:336-364`).

Exact field evidence:

- each of children `a3623b5b79be9e608` and `ae2e22bccded54f44` has zero assistant-text verdict
  markers and exactly one `SubagentHandback` tool-use `input.message` with one marker;
- both handbacks are schema-valid and their dossier digests/reviewed-claim coverage match their exact
  durable dossier;
- both durable children are `failed`, `spent=true`;
- decoded artifact ref: 22 transaction manifests + 6 research + 1 graph; zero `audit_verdict` and
  zero attribution artifacts.

The second fail verdict did not drive the terminal result. `intent="stop"` moved the run to
`stopped_residual` without audit (`evaluation.py:423-428`). A valid fail verdict should still be
admitted as an audit fact and complete its child.

Parsing an exact bound handback is a promising correction, not proof of full repair. A
`SubagentHandback` input is model-authored content persisted by the host, not host-authored truth.
Exact run/child/operation correlation, one unambiguous handback, schema/dossier binding, identity,
terminal/replay, and fresh installed-host evidence remain mandatory.

### F4b — Ordinary Claude child lifecycle is not durably correlated — VERIFIED

Ordinary Agent launches only reserve (`adapters/claude/lifecycle.py:143-150`). Registered
`SubagentStart`/`SubagentStop` hooks and orphan reconciliation are auditor-only
(`hooks/hooks.json:103-131`; `adapters/audit_protocol.py:141-154`). The field ordinary reservation
remains `reserved`, `native_id=null`, `spent=false`, while the global counter includes it.

The supplied field evidence does not independently prove that scout's native completion or every
Claude 2.1.278 Agent scheduling mode. The source-level defect is narrower and sufficient: ordinary
reservations have no completion correlation path.

### F5 — Audit dossier cannot witness multiple rubric items — VERIFIED

Rubric 7 requires route/investigation witnesses; rubrics 8/9 require judging goal coverage
(`agents/empirica-auditor.md:28-30`), and any unestablished item requires fail (`:43-45`).
`project_argument` provides `goal_digest` but no readable goal and no route/investigation witnesses
(`core/projection.py:147-199`). `child_prompt` supplies only rubric + dossier
(`adapters/audit.py:22-26`). The auditor is forbidden to read operational state.

The exact handbacks disagree on rubric 7. The first claims machine enforcement; that assertion is
not true end-to-end (F9 below). Do not remove rubric 7 on a machine-enforcement premise. Either
actually enforce routing and declare it outside semantic audit, or provide bound witnesses. Add a
readable immutable goal bound by `goal_digest`, or establish another trusted delivery channel, so
rubrics 8/9 are answerable.

### F6 — Missing `empirica_read` in one upgraded session — UNVERIFIED AS CODE DEFECT

Current definitions expose all three tools (`adapters/public_tools.py:19-22`). A cache/update cause
and restart remedy are not established. Add a fresh-session exact-three-tool receipt as a diagnostic;
do not change registration code based on this single report.

### F7 — MCP arbitrary-version echo — RESOLVED IN SOURCE

Baseline `mcp_server.py:16,56-64` returns its supported Claude-compatible MCP `2025-11-25`, not
arbitrary client input. ADR 44 records the host-compatibility decision. Prior live connection evidence
was observed in the parent session but is not embedded in this worklog; require a retained fresh
receipt before release claims.

### F8 — v2 assurance-graph preservation failure — VERIFIED

#### F8.1 Historical/current validation boundary

0.7.0 `hooks/claimgraph.py` validates typed GSN elements, legal pairs, and an acyclic
`SupportedBy` relation (`3150f4d:claimgraph.py:75-100,152-250`). Current `valid_graph` checks a flat
closed claim shape, endpoint existence, and edge-name membership (`core/evaluation.py:79-100`).

Executable probes:

```text
current accepts cycle                 true
current accepts self-loop             true
current accepts detached gating claim true
current accepts duplicate edge        true
0.7.0 rejects cycle                   true
0.7.0 rejects self-loop               true
0.7.0 rejects illegal Goal→Context    true
0.7.0 accepts detached node           true
0.7.0 accepts duplicate edge          true
```

Therefore cycle/self-support/legal-pair recovery is historical preservation. Mandatory reachability
and duplicate-edge rejection are new proposed hardening, not 0.7 guarantees.

#### F8.2 Root/edges are not used for deterministic support-scope adjudication

Current scope is frozen IDs or author `gating` booleans (`evaluation.py:419-422`), not
`SupportedBy` traversal. Root/edges still affect audit digest invalidation and the model auditor's
specialization review (`evaluation.py:279-308`; `agents/empirica-auditor.md:27`). They are not
behavior-free, but they do not determine deterministic support scope.

#### F8.3 Only local `state_of` remains live

`evaluation.claim_state` synthesizes a one-node legacy-shaped graph and calls only
`claim_rules.state_of` (`evaluation.py:137-156`). Traversal, root-refutation, convergence, and
purpose-built shape digest in `core/claims.py:54-142` are unused except through dead budget code and
tests. Direct confidence input is removed, but current citation-free research can still create an
approved ordinary claim (F10); do not generalize the confidence improvement to evidence integrity.

#### F8.4 Root-refutation terminal safety is retained; old graph semantics are not

An in-scope discarded root blocks as `claim.refuted` (`evaluation.py:429-436`). A root outside frozen
scope produces `stopped_frozen`, not converged (`:473-475`). `claims.root_is_refuted` and old subtree
pruning are not called. Classify this as safe non-convergence outcome preserved, original graph
semantics dropped.

#### F8.5 Audit digest detects current graph changes, narrowly

`audit_binding` hashes the complete current graph plus visible research/spike request/spike IDs
(`evaluation.py:279-301`). Edge deletion changes the binding. This proves graph/artifact binding,
not source-content freshness, authentic observation, or preservation of claims deleted before a new
audit (F11).

#### F8.6 Accepted architecture promised the missing guarantees

Accepted ADR 22 requires GSN vocabulary/legal connections/DAG (`doc/adr/0022-...:192-195`). Accepted
ADR 30 says existing GSN behavior remains semantic source/frozen fixtures (`doc/adr/0030-...:53-55`).
The frozen 2.0 matrix requires claims on committed support scope to gate and assigns graph traversal
to pure core (`doc/design/empirica-2.0-final-dag.md:86-126`). No accepted supersession was found.
The architecture check passing despite probes demonstrates missing validator coverage, not
conformance.

#### F8.7 Canonical contract graph prose is stale — VERIFIED

`public-contract.json:212` says the graph contains confidence and tags, but current `valid_graph`
requires exactly `id,text,gating,kind` and rejects extra fields (`evaluation.py:87-90`). This is a
canonical contract/implementation mismatch independent of the GSN decision.

### F9 — Route-before-investigation is not end-to-end enforced — VERIFIED

The explicit `investigate` action blocks if no route (`evaluation.py:337-344`). Claude's
investigative `PreToolUse` calls `dispatch_investigation` best-effort, discards the decision, catches
all exceptions, and returns zero (`adapters/claude/lifecycle.py:172-181`). Research admission, graph
selection, child reservation, and convergence do not require route/investigation stamps.

Parent probe: schema-valid citation-free research was admitted and approved with both stamps `None`.
Historical 0.7 at least surfaced route violations/inconclusive state in final output
(`3150f4d:hooks/convergence_gate.py:343-349,385-407`). Current strictness cannot be claimed until the
native path gates or terminal evaluation rejects missing/inverted order.

### F10 — Citation-free research can approve an ordinary claim — VERIFIED HIGH

Current `actionResearch` requires only kind, claim ID, source kind, and result; payload is optional
(`request.schema.json:642-676`). Evaluator defaults missing `source_ref` to `source_kind`
(`evaluation.py:354-365`). Supporting research then approves an ordinary claim
(`evaluation.py:137-156`).

Parent probe:

```text
citation_free.result Allow
source_ref code
claim state approved
route_stamp None
investigation_stamp None
```

0.7.0 required nonempty source and citation on write/read
(`3150f4d:hooks/evidence.py:226-235,330-374`). Current canonical clauses promise locator, verbatim
citation, and fetched/read/observed source (`public-contract.json:237-238`), so this is both a
historical regression and current contract violation.

Research is a public author assertion. Service-owned append-only storage improves normal API
integrity/tamper evidence; it does not make the source true or host-observed. Decide the minimum
locator/citation/content-digest and observation authority before red tests.

### F11 — Frozen committed claims can disappear — VERIFIED HIGH

Freeze records IDs once (`evaluation.py:367-371`), but later graph replacement does not require
those IDs (`:345-351`). Evaluation builds states from current graph claims and intersects with frozen
IDs (`:419-422`); audit binding and projection likewise iterate current claims only
(`evaluation.py:283-301`; `projection.py:19-25,153-165`).

Parent pure probe:

1. frozen IDs = `R,C`;
2. current graph contains only approved `R`;
3. current synthetic passing audit binds that reduced graph and distinct host identities;
4. evaluator returns `Allow`, status `converged` while frozen `C` is absent.

This is a deterministic gate omission, not an installed-host exploit and not stale-audit reuse; graph
change invalidates the old audit, but a new dossier silently omits `C`. Require no-silent-deletion or
an explicit human-authorized scope-revision transition.

### F12 — Refutation/conflict implementation diverges from canonical contract — VERIFIED HIGH

Canonical contract says an active failing spike discards a claim without restricting that rule to a
claim kind, and simultaneous active support and refutation returns `evidence.conflict`
(`public-contract.json:229-230`). Current `claim_state` derives refutation only from research and
uses spikes only when deciding approval for `needs-experiment` (`evaluation.py:137-151`). Spike
request admission itself does not require `needs-experiment` (`evaluation.py:166-186`). Parent
probes:

```text
needs-experiment + supporting research + active failing spike
  → state open, claim.spike_missing
ordinary + supporting research + active failing spike
  → state approved, audit.required
ordinary + the same failing spike + synthetic current passing audit/distinct identities
  → Allow, converged
supporting + refuting research
  → state discarded, claim.refuted (not evidence.conflict)
```

The ordinary-claim convergence result is a pure-evaluator demonstration assuming a current passing
audit, not an installed-host exploit. The needs-experiment case and support/refutation conflict are
fail-closed but typed incorrectly; the ordinary case violates the canonical safety outcome and can
converge through the pure evaluator. Add claim-kind-complete red conformance cases before changing
implementation or contract.

### F13 — Current counters do not prove total-run termination — VERIFIED

Unresolved claims return before derivation-pass increment (`evaluation.py:429-466`). Parent probe:
three unchanged `report_convergence` attempts yielded `claim.research_missing`, `passes_used=0`,
status active. The author may raise ceilings through `configure_run` (`:372-381`). Dead/proposed ADR
34 does not bound this path.

0.7.0 active Stop attempts incremented a finite pass variant and eventually stopped
(`3150f4d:hooks/convergence_gate.py:422-435`). Governed planning must decide what spends a pass, how
no-progress terminates, and who may raise ceilings. Finite per-reservation checks are not a total-run
termination theorem.

## 6. Trust-boundary conclusion

Current accepted threat model explicitly excludes same-user/host-authority invocation of adapter code
(ADR 42:60-63). Git history is tamper-evident, not access isolation (ADR 31:67-68). Direct trusted
functions exist in `adapters/bridge.py:103-120`.

Therefore use these precise claims:

- **VERIFIED stronger:** model-visible public tools exclude trusted actions; normal API writes are
  service-owned; artifacts are append-only/tamper-evident; audit operations bind durable state;
  concrete host-observed identity comparisons gate convergence.
- **NOT CLAIMED:** OS principal separation, protection against arbitrary same-authority code
  invocation, cryptographic operator provenance, or spike subprocess sandboxing.

## 7. Proposed next contract (decisions still open)

### 7.1 Control policy

Candidate closed `control_mode`:

- `deliberative` — proposed default;
- `auto` — explicit invocation only.

Auto would change plan activation only. It must not weaken research, spikes, freshness, audit,
identity, or terminal rules, and must not resolve `needs-decision` without a human. This is a design
proposal pending an ADR/contract decision.

### 7.2 Digest-bound plan proposal

Candidate proposal fields:

- structurally valid selected graph + digest;
- material-claim rationale;
- pass/no-progress policy;
- investigation-spawn ceiling;
- audit-attempt ceiling;
- expected spike inventory;
- enabled provider/execution modes;
- proposal digest over every field.

Candidate statuses: `unproposed`, `proposed`, `accepted`, `rejected`, `superseded`. Candidate rule:
in deliberative mode, evidence, spike, child, freeze, audit, and convergence block until the exact
proposal is accepted; material graph/budget change supersedes approval. Human authority mechanism
remains open per GP-4.

### 7.3 Graph model decision — selected by ADR 45

Empirica v2 uses a smaller strict root-connected claim-dependency DAG rather than restoring full
ADR-22 GSN. `SupportedBy` is the only edge type; endpoints are known and distinct, edges are unique,
the relation is acyclic, and every listed claim is reachable from the root. Candidate invalidity is
`graph.invalid` before persistence; an invalid persisted selected graph is aggregate `run.corrupt`.
Seam 4A enforces structure. Making dependency traversal adjudicative remains the separate Seam 4B.

### 7.4 Route ownership decision

Choose one consistent contract:

- hard machine gate: native investigative actions cannot proceed without route and terminal
  evaluation verifies order; auditor receives a machine-owned attestation or excludes the rubric; or
- semantic audit: dossier includes readable bound route/investigation witnesses and auditor judges.

Current hybrid is invalid: nonblocking host observation plus an unwitnessable mandatory rubric.

## 8. Corrected red-first acceptance inventory

No production implementation before these tests exist and fail:

### Governed planning

1. default StartRun enters a proposed/awaiting-plan state if deliberative mode is adopted;
2. author can propose but cannot self-approve under the selected human-authority model;
3. pre-approval evidence/spike/child/freeze/audit/convergence behavior is explicitly specified;
4. acceptance binds exact proposal digest; material graph/budget change supersedes it;
5. explicit auto activates a recorded plan without pretending human approval;
6. auto still blocks `needs-decision` and requires the same evidence/audit gates.

### Research and graph

7. research without required locator/citation/observation fields fails schema/admission;
8. source truth remains auditor-checked unless host observation is deliberately added;
9. cycle, self-support, and illegal pair cases recover accepted ADR behavior or an ADR explicitly
   supersedes them;
10. reachability/duplicate-edge policy is tested according to the newly selected contract;
11. support scope is derived by exactly one live implementation;
12. refuted root remains non-converged;
13. frozen committed claim deletion blocks or requires explicit authorized scope revision;
14. canonical graph prose and schema agree.

### Route, budget, termination

15. exact native investigative path enforces/records route according to selected policy;
16. terminal evaluation cannot converge with required route evidence absent/inverted;
17. investigation and audit accounting has explicit bounded semantics;
18. generic reservation denial preserves reachable typed reason/parameters;
19. unchanged unresolved attempts terminate under a declared no-progress rule;
20. ceiling-raise authority is explicit and cannot silently defeat the bound.

### Audit and Claude lifecycle

21. audit dossier includes readable goal bound by `goal_digest`;
22. every mandatory rubric item has a dossier witness or is explicitly machine-owned/out of scope;
23. exact correlated one-handback verdict is admitted once;
24. arbitrary transcript fences, multiple handbacks, changed dossier, wrong child, and terminal replay
   fail closed;
25. valid fail verdict is admitted and completes child while still blocking convergence;
26. ordinary Claude child receives correlated start and exactly one terminal transition;
27. fresh exact-profile receipts prove handback repair, identities, verdict admission, and report.

### Contract preservation

28. active failing spikes discard both ordinary and `needs-experiment` claims as specified, and
   simultaneous support/refutation yields the canonical conflict outcome;
29. 0.7 structural adversarial fixtures are ported or explicitly rejected by accepted ADR;
30. v2 append-only, freshness, strict-state, replay, and terminal fixtures remain green;
31. architecture validator detects dead policy modules and promised-but-unexercised graph traversal;
32. installed-host receipt lists exactly three public tools in a fresh session.

## 9. Proposed implementation sequence

1. Record decisions: graph model, human authority, route ownership, budget/no-progress semantics,
   minimum research evidence.
2. Amend canonical public contract/schemas and accepted ADRs; do not implement against stale prose.
3. Add red host-neutral cases for all selected semantics, including F10-F13.
4. Restore one live graph/state implementation; delete stranded duplicates/dead modules.
5. Implement governed-plan transactions, split/selected budget policy, and projections.
6. Repair Claude handback and ordinary child correlation against exact native transcripts.
7. Implement host-specific human decision path or explicit unsupported result.
8. Update skill UX for negotiation/default and explicit auto.
9. Run Make suites, architecture/adversarial checks, and fresh installed-host receipts.
10. Independent review, then resolve this worktree into production only after explicit human approval.

## 10. Worklog chronology

- Created stacked Orca worktree from exact 2.0.1 baseline; no production source changed.
- Read `make help`; recorded current and historical commits.
- Probed old/new graph validators and inspected accepted architecture records.
- Inspected exact field run, artifact ref, and two bound child transcripts read-only.
- Drafted inventory v1.
- Astra round 1: **REJECT**; identified research, route, goal, frozen-scope, trust, refutation, and
  boundedness omissions.
- Parent reproduced citation-free approval, no-stamp approval, unchanged-pass behavior, experiment
  failed-spike mismatch, conflict mismatch, old detached/duplicate acceptance, and deleted-frozen-
  claim convergence with a synthetic current audit.
- Rewrote inventory with dimensioned strictness and explicit design-vs-fact separation.
- Astra round 2: **REJECT (one blocker)**; found ordinary claims ignore active failing spikes and can
  converge in the pure evaluator with a synthetic current passing audit.
- Parent reproduced the ordinary-claim result and added claim-kind-complete acceptance coverage.
- Astra final re-review: **ACCEPT**; worklog is safe as the design/implementation baseline subject to
  its explicitly open decisions. Acceptance does not certify the current implementation or release.

## 11. Implementation seams

### Seam 1 — Claim-evidence adjudication — LANDED, HITL accepted

Contract decisions already implied by the canonical clauses were made executable:

- public research requires non-empty `payload.source_ref` and verbatim `payload.citation`;
- optional `payload.observed_content_digest` must be SHA-256;
- the complete admitted research record is content-addressed and projected to the auditor;
- simultaneous supporting and refuting **research** stays `open` and blocks as
  `evidence.conflict`;
- an active failing deterministic spike is the machine falsifier and dominates conflicting
  research, discarding both ordinary and `needs-experiment` claims as `claim.refuted`.

Red evidence:

- five malformed research variants were accepted by the old schema;
- research conflict projected `discarded` instead of `open`/`evidence.conflict`;
- a failed experiment stayed `open`, and an ordinary claim with a failed spike stayed `approved`.

Green evidence:

- `make contract-check`;
- `make empirica-v2-conformance` (56/56);
- `make empirica-d7-transactions` (14/14);
- `make empirica-host-adapter-check` (Claude, Codex, Pi and public host path);
- `make empirica-architecture-check` (9,456/9,457 effective runtime lines);
- `make check` (all static, core, Claude, Codex, and Pi suites).

Independent non-Anthropic review:

- GPT-5.6 Terra first found a precedence blocker: a failed spike could be hidden by conflicting
  research;
- parent added claim-kind-complete failing-spike + conflicting-research + honest-stop tests and
  changed precedence;
- Terra re-review: **ACCEPT**.

No files are staged or committed. Human accepted strict restart semantics and Seam 1 at its HITL
checkpoint. Next seam: frozen committed-scope preservation (F11), kept separate from graph-shape
restoration.

### Seam 2 — Frozen committed-scope preservation (F11) — LANDED, HITL accepted

Freeze now establishes an inclusion invariant: every later selected graph must contain every frozen
claim ID. A candidate omission blocks as `graph.invalid` before artifact construction or state
replacement; retained-ID additions remain admissible and deferred.

Pre-existing inconsistent active snapshots also fail closed:

- `GetArgument`, research, spikes, audit reservation, audit attribution/verdict, and convergence are
  blocked before projection, budget charging, or audit reasoning;
- `audit_binding` is non-producible and `audit_passes` is false;
- `GetRun`/`RestoreRun` remain available;
- explicit stop remains available and terminates `stopped_residual`, permitting a fresh generation.

Red evidence:

- a two-claim frozen graph accepted a one-claim replacement and silently deleted `C1`;
- a pure pre-existing inconsistent snapshot skipped the missing frozen ID and reached
  `audit.required`.

Green evidence:

- deletion blocks with exact `graph.invalid` and the prior selected graph remains visible;
- a pre-existing inconsistent snapshot blocks argument/audit/convergence in pure core; the following
  strict-subtraction seam classifies the persisted aggregate as `run.corrupt` with no repair path;
- `make contract-check` passes with canonical digest
  `sha256:d306870b3854a9b5580202245c07db864363e7278f368204957e6468e5915e41`;
- `make empirica-v2-conformance` passes (57/57);
- `make empirica-d7-transactions` passes (15/15);
- `make empirica-architecture-check` passes at the exact 9,457-line ceiling;
- final `make check` passes all static, core, Claude, Codex, and Pi suites.

Independent non-Anthropic review:

- GPT-5.6 Terra first surfaced the pre-existing stale pending-audit/retry-budget interaction after a
  valid deferred addition;
- baseline comparison established that interaction predates F11 and belongs to the later audit and
  split-budget lifecycle seams; F11 neither changes whole-graph audit binding nor worsens it;
- narrowed F11 re-review: **ACCEPT**.

No files are staged or committed. Human accepted Seam 2 at its HITL checkpoint and directed that
buggy predecessor states receive no backward-compatibility path; architecture-ceiling growth is
permitted only when explicitly justified. Graph shape/reachability, claim wording immutability,
human-authorized scope revision, and audit retry-budget redesign remain separate seams.

### Seam 3 — Strict backward-compatibility subtraction — LANDED, HITL accepted

Only an exact valid current `empirica/v2` persisted state and consistent reachable history is usable.
Every other persisted aggregate is one `run.corrupt` category with fixed safe goal
`Unsupported run state.`, empty projected operational facts, `run.start_fresh`, and zero writes.
Rejected state/history contributes no goal, modes, status, child, evidence, or terminal facts.

Deleted runtime/public surfaces:

- `old_unsupported` classification and all coordinator branches;
- `run.old_version` reason/schema/contract clauses;
- `block-old-version.json` (replaced by `block-corrupt-state.json`);
- unused `MigrationPort` and `MigrationReport` APIs/re-exports;
- pre-existing frozen-scope graph repair and stop compatibility behavior.

Retained rejection hardening:

- raw non-v2 wire remains exact `invalid_request`/closed;
- candidate graph deletion remains `graph.invalid` before persistence;
- architecture bans on v1 runtime and migration targets remain;
- generation isolation remains, and `StartRun` may allocate a clean next generation without reading
  or rewriting the rejected slice.

Red evidence:

- noncurrent persisted identities classified `old_unsupported` and exposed `run.old_version`;
- safe projections reused a rejected raw string goal;
- corrupt history projected decoded state through `_block_from_state`;
- `ResolveRun` hid corrupt and corrupt-terminal history as `Inert/no_run`;
- an inconsistent frozen history could be repaired or stopped instead of rejected as corrupt.

Green evidence:

- all noncurrent/invalid persisted identity and history cases produce sole fixed-safe `run.corrupt`;
- valid terminal `ResolveRun` remains `Inert`, but terminal state with corrupt history is
  `run.corrupt` with no goal leak;
- deliberately persisted frozen-scope/history inconsistency produces fixed-safe `run.corrupt`, no
  canary, and zero CAS;
- no `run.old_version`, `old_unsupported`, `block-old-version`, `MigrationPort`, or
  `MigrationReport` remains outside historical ADR/worklog material;
- canonical contract digest is
  `sha256:c8562cc5cc3119001e131dfdefaa8cfcf5087594f8c26782955e464e36b37dda`;
- `make contract-check`, D7 transactions (17/17), lint, architecture, and final `make check` pass;
- effective runtime is 9,378 lines, 79 below the unchanged 9,457 ceiling.

Independent non-Anthropic review:

- GPT-5.6 Terra found unsafe decoded-state projection after history corruption and corrupt
  `ResolveRun` suppression; parent added coordinator-level regressions and fixed every path;
- Terra then found terminal `ResolveRun` validated status before history; validation was reordered
  and a valid-terminal-versus-corrupt-terminal regression added;
- final re-review: **ACCEPT**.

No files are staged or committed. Human accepted Seam 3 at its HITL checkpoint. Historical
ADRs/worklog retain factual history; current runtime, contracts, schemas, fixtures, active specs,
tests, and operator surfaces expose only strict v2.

### Seam 4A — Structural claim-dependency DAG integrity — LANDED, HITL accepted

ADR 45 supersedes ADR 22's full-GSN graph shape with a smaller strict v2 dependency DAG.
`SupportedBy` is the only accepted edge, directed from a claim to its supporting claim. The
validator rejects unknown or equal endpoints, exact duplicate edges, self-loops, cycles, and any
claim not reachable from the declared root. A rooted branching diamond remains valid.

Trust-boundary taxonomy:

- invalid candidate graph → sole `graph.invalid`, zero artifact/CAS writes, prior selected graph
  unchanged;
- null selected pointer → valid state until a graph-required operation;
- missing, malformed, content-invalid, or structurally invalid persisted selected graph → fixed-safe
  `run.corrupt`, no semantic leakage, zero writes;
- the same corruption rule now covers trusted attribution, verdict, and child-event ingress rather
  than allowing an exception or `Inert` suppression.

Red evidence:

- host-neutral conformance accepted self-loop, duplicate, cyclic, detached, and `InContextOf`
  candidate graphs (five failing variants; unknown endpoint already rejected);
- D7 returned `graph.invalid` for missing/malformed persisted selected artifacts instead of
  `run.corrupt`;
- trusted ingress produced three `Inert` results for corrupt run storage and three uncaught
  `HistoryCorrupt` exceptions for a missing selected graph artifact.

Green evidence:

- conformance covers all invalid shapes, non-replacement, and a valid branching shared-dependency
  DAG; `make empirica-v2-conformance` passes 59/59;
- D7 covers candidate zero writes, persisted missing/malformed/structurally-invalid graph safety,
  and all three trusted ingress paths; 19/19 passes;
- canonical response schema and seven-file vendor closure admit only `SupportedBy`;
- public contract, D1/D2A/D7 specs, skill reference, and ADR 45 agree;
- canonical contract digest is
  `sha256:cc25cee80a10845fb62bb698c8c3cc56520b986de81783dda7c77c0453b47d4f`;
- contract, vendor, host-adapter, core, lint, ADR, architecture, and full `make check` pass;
- effective runtime is 9,407 lines, 50 below the unchanged 9,457 ceiling.

Independent non-Anthropic review:

- GPT-5.6 Terra accepted the linear structural validator, taxonomy, schema/contract parity, and
  positive/negative graph coverage, but found corrupt aggregate handling missing on trusted ingress;
- parent reproduced the defect red-first across attribution, audit verdict, and child events, then
  fixed both trusted loops with revision-aware fixed-safe corruption handling;
- final re-review: **ACCEPT**.

Seam 4A deliberately does not make dependency state adjudicative. That is Seam 4B: one live
root traversal must determine parent/dependency outcomes and stranded graph walkers must be
consolidated or removed. No files are staged or committed. Human accepted Seam 4A and authorized
committing the accepted seams before proceeding.
