# Empirica 2.0 D1 public behavioral contract

**Status:** Parent-frozen implementation specification; normative machine files are created in D2.

**Protocol:** `empirica/v2` only. Every other wire identity is invalid and every other persisted identity is corrupt.

**Companion:** `empirica-2.0-d1h-host-capabilities.md`.

## 1. Canonical source and discovery

D2 creates one canonical structured **PublicContract** at
`contracts/empirica/v2/public-contract.json`. PublicContract means only this static behavioral
registry. It owns:

- contract sections and clause IDs;
- protocol/action/status closed values;
- stable reason codes and parameter schemas;
- valid next-action IDs;
- reason → next-action and reason → section links;
- host capability vocabulary;
- author-visible role and terminal semantics.

Request/response JSON Schemas are generated from or mechanically checked against this registry.
Python and TypeScript values are generated or parity-checked; they are not semantic owners. ADRs
explain rationale and the skill summarizes the seven-concept loop without copying normative clauses.

V2 persists no obligation-contract artifact, obligation-contract revision, or obligation-contract
pointer. Active/deferred obligations are derived RunView fields from the current evaluation snapshot;
append-only graph/evidence/audit facts are sufficient history. The response field `contract` is
reserved for PublicContract identity and section references. The generic obligations library may be
used as a pure projection/model, but never as persisted run authority.

Every run view carries only:

```json
{
  "contract": {
    "id": "empirica/public",
    "version": "2.0.0",
    "digest": "sha256:...",
    "relevant_sections": ["evidence/freshness"]
  }
}
```

`GetContract` supports `index`, one section ID, or explicit `full`. Full contract retrieval is never
automatically injected. Every successful `GetContract` result (`index|section|full`) carries the
canonical PublicContract digest (the same `sha256:<64 lowercase hex>` that RunView `contract.digest`
reports, computed by the `registry_digest` algorithm over `public-contract.json`) so on-demand
contract discovery can be correlated with a run's contract identity. The digest is a sibling field of
the target payload, not embedded inside PublicContract itself; no fixture or registry duplicates the
digest as a constant.

## 2. Public concepts and roles

Author-facing concepts remain:

1. goal;
2. outstanding obligation;
3. valid next action;
4. trusted observation;
5. residual/handoff;
6. terminal distinction;
7. author, deterministic harness, auditor, and human roles.

Role authority:

| Role | May do | Must not do |
|---|---|---|
| Author | Propose graph/claims, fetch research, request deterministic spike, request attribution display, respond to obligations, stop with residual | Create/alter convergence-relevant attribution; approve its own evidence; audit its own convergence |
| Deterministic harness | Execute a declared command and report exit code/result/file bindings | Decide claim meaning or convergence |
| Auditor | Re-read cited evidence and argument, return pass/fail findings | Approve machine evidence or alter the graph |
| Human | Resolve judgment/budget/product decisions and accept residuals | Be silently simulated by an agent |
| Host adapter | Translate trusted native events and render structured results | Re-decide evidence, budget, audit, or convergence policy |

## 3. Protocol commands and decisions

Commands:

```text
StartRun
ResolveRun
ObserveAction
EvaluateRun
GetRun
GetArgument
GetContract
RestoreRun
```

Decision envelopes remain:

```text
Allow  — requested operation admitted; `converged` is true only with status=converged
Block  — active operation cannot complete; carries structured reasons and next actions
Inert  — no applicable run/event; no state change
Fault  — malformed/corrupt/conflicting/unavailable boundary; fail direction is explicit
```

No consumer branches on rendered prose. Each Block/Fault contains stable code, typed parameters,
and contract section links. A human-readable message is a non-normative rendering.

A fresh `spike_request` for the same stale active head is the wire operation implementing the public
next action `spike.regate`; `spike.regate` is guidance, not an ObserveAction kind. RunView always
reports the complete bounded public projection (modes, obligations, residuals, freshness, children,
host). `GetArgument` returns that RunView plus the typed ArgumentView, not a generic RunView alone.
`GetArgument` is the public bounded provenance projection for research, sealed `spike_request`
artifacts, and spike result artifacts; `active_evidence_ids` is the exact ordered active set keyed
by both claim ID and current wording digest (distinct claims with identical wording never share
active evidence), and `evidence_digest` is the canonical `registry_digest` of that JSON array. The
projection carries the canonical claim kind (`ordinary | needs-experiment | needs-decision`), derived
from the graph, so approval requirements follow D1 §5: `ordinary` requires active supporting
research; `needs-experiment` additionally requires an active passing spike; `needs-decision` cannot
project approved. Internal append storage may differ, but hosts and tests consume only this typed
projection. Route, investigate, and freeze witnesses remain reflected through derived obligations,
claims, and residuals; they are not added to this evidence artifact list.
`RestoreRun` returns the same bounded RunView shape (or a structured Block/Fault), never counters or
private state. Adverse terminal children (`launch_rejected|failed|cancelled|timed_out|orphaned`)
expose exact canonical Block recovery choices through `child.terminal.next_actions`; there is no
separate state→single-action table.

Private trusted ingress is independent of the wire payload: host and application adapters possess
an unexposed capability and invoke application trusted admission; appending a fact to a host
telemetry sink is not admission and cannot satisfy child, evidence, or audit lifecycle tests. The
public contract does not prescribe a concrete runtime class or method name for that capability.

### Run statuses

```text
active
converged
stopped_residual
stopped_frozen
stopped_budget
```

`Allow` carries the single public `converged` boolean; RunView carries status and does not duplicate
that boolean. `Allow.converged` is true exactly when `run.status=converged`. Every status except
`active` is terminal. A terminal run accepts append-only diagnostic facts where explicitly allowed,
but never changes status or produces a later convergence result.

### ObserveAction variants

Author-accessible:

```text
graph             canonical current root-connected claim-dependency DAG
research          one externally observed citation bound to a claim
spike_request     request trusted deterministic harness execution against active research and declared dependent files
configure_run     budget/mode changes while active
route             routing commitment/reason before investigation
investigate       first investigation witness
dispatch          actor/claim assignment witness
freeze            first-write-wins committed claim scope
child_reserve     request a host child for a declared purpose
```

The v2 graph admits only unique, acyclic `SupportedBy` edges directed from a claim to its supporting
claim. Endpoints must be known and distinct, and every claim must be reachable from the declared
root. Invalid candidates produce `graph.invalid` before persistence; an invalid persisted selected
graph makes the aggregate `run.corrupt`.

Trusted adapter/application only:

```text
evidence_leaf     append validated research or application-sealed spike statement
attribution       concrete host-observed actor identity; never author-supplied or alias-derived
child_event       launch/start/terminal native observation bound to a private capability
audit_verdict     emitted only as part of a trusted bound child completion
```

Removed:

```text
evidence          caller-supplied boolean approval
audit_ticket      public knowledge action
consume_audit_ticket
void_spawn
phase             removed from protocol and persisted v2 state; progress is derived
mode              action discriminator replaced by configure_run; mode semantics are preserved
reserve_spawn     replaced by child_reserve
```

This list distinguishes genuine deletion from consolidation: `mode -> configure_run` and
`reserve_spawn -> child_reserve` are not credited as removed behavior. There is no internal v2 phase
enum or phase-transition machine.

There is no public nonce, reservation ID, CAS revision arithmetic, or artifact path. `child_id` is
safe for diagnostics/status; private capability material is never rendered.

## 4. Graph and derived claim state

The graph contains claim identity/text, confidence, kind/tags, root and support edges. It does not
contain trusted claim state.

`state_of(claim, active_evidence, theta)` is the only state authority:

```text
open
approved
discarded
blocked
```

- `approved`: confidence meets threshold and active evidence satisfies approval requirements.
- `discarded`: active evidence genuinely refutes the claim.
- `blocked`: a declared human/data/budget hold is valid and surfaced as residual.
- `open`: otherwise.

Malformed graph, missing selected graph, unsupported edge/type, or corrupt active artifact fails
closed. Only claims on the committed support scope gate convergence. Approved claims remain visible;
they are not retired merely because currently satisfied.

## 5. Evidence and ordering

### Research statement

A research leaf is content-addressed and binds:

```text
claim_id
claim_digest
source kind: docs | code | runtime | web
source locator
verbatim/precise citation
result: supports | refutes
observed content digest where available
```

Model recall is not evidence. The source must be fetched/read/observed during the run.

### Spike attestation

A spike leaf is created only from a real harness execution. Before execution, the application appends
an immutable `spike_request` artifact containing a server-generated `harness_request_id`, current
claim ID/digest, command/digest, and the ordered active supporting research artifact IDs. It then
invokes the harness with that sealed snapshot. The resulting spike binds:

```text
claim_id
claim_digest
command and command digest
declared dependent files
exit_code                     # sole machine approval authority
gate: pass | fail             # derived from exit_code, never caller-selected
result digest
harness_request_id and exact prerequisite snapshot
prerequisite research artifact IDs
file bindings: [{path, sha256}]
supersedes                    # prior active spike artifact when re-gating
```

Research-before-experiment is proved by the trusted pre-execution `spike_request`, not timestamps or
artifact append inference. Admission rejects a missing/replayed request, mismatched snapshot, request
created without active supporting research, prerequisite changed/superseded before request, or result
for another request. The author cannot append a spike leaf directly.

A spike cannot remain valid unless every named prerequisite exists, is active, supports the same
current claim digest, and matches the sealed request snapshot. The request artifact is append-only
history, not mutable operational state.

### Complete active-set evaluation

Approval/refutation is recomputed from the complete active leaf set on every read. A per-request or
persisted composite verdict is forbidden. Superseded facts remain append-only history but are absent
from the active set.

Requirements:

- ordinary claim: one active supporting Fold-1 research leaf;
- `needs-experiment`: active supporting Fold 1 plus an active passing Fold-2 spike that names it as a
  prerequisite and is fresh;
- refutation: active refuting research or active failing deterministic spike;
- `needs-decision`: cannot be agent-approved; becomes human residual;
- simultaneous active supporting and refuting research fails closed as an evidence conflict; it does
  not select either research result. An active failing deterministic spike is the machine falsifier
  and refutes the claim even though supporting research was a prerequisite to run it. The author
  must resolve/supersede conflicting research or stop with residual.

## 6. Workspace observation and re-gate

`Workspace.observe(paths)` is the sole current-file observation port. Each result is:

```json
{
  "path": "repo/relative/posix/path",
  "state": "present | missing | unreadable | non_regular | outside_workspace",
  "sha256": "hex-or-null"
}
```

Rules:

- paths are normalized relative to the workspace root with stable POSIX separators;
- absolute escape, `..` escape, symlink traversal, directory/non-regular input, missing, or unreadable
  file cannot approve;
- duplicate paths collapse after normalization; ordering is lexical;
- digest is SHA-256 of bytes; aggregate binding digest, if stored, is derived from the ordered
  per-file bindings and is not the diagnostic authority;
- pure core receives recorded bindings plus current observations and performs no filesystem I/O;
- every **run evaluation view** that reports derived claim/evidence state, Block reasons, audit
  coverage, restore state, obligations, or convergence observes all files bound by active spikes at
  that moment; static `GetContract` retrieval and PublicContract identity/digest lookup perform no
  workspace observation;
- any path/state/digest mismatch yields `claim.spike_stale` and names changed paths without
  exposing hashes to the author;
- the observations used for adjudication are part of the immutable evaluation snapshot committed by
  the same revision/CAS decision; a stale writer or detected workspace change before commit retries
  with a new observation or fails closed—never mixes two observations;
- re-gate runs only stale active spike heads, with their current active research prerequisites, and
  appends a superseding attestation;
- author assertion or auditor judgment cannot refresh a stale spike.

## 7. Audit

Audit becomes owed only after committed gating claims are otherwise terminal and approvable. It
reviews the current argument digest and each approved claim's current claim/evidence digest.

A valid audit requires:

- a host-observed completion bound to one pending audit `child_id` through a private adapter
  capability;
- a structured `pass|fail` verdict and findings;
- current argument digest;
- reviewed claim/evidence digest tuples for every approved gating claim;
- independence derived by the application as `decorrelated | same_model | unverified` from trusted,
  normalized host/configuration identities—not supplied in the verdict.

The application compares the auditor with every agentic actor whose work is covered by approved
gating evidence. Missing, author-supplied, ambiguous, tier-only, aliased-but-unresolved, or
incomparable identity yields `unverified`; `decorrelated` requires a successful normalized comparison.
The auditor never approves deterministic evidence. `same_model` and `unverified` cannot satisfy an
independent-audit obligation.

For a frozen run, the dossier and verdict also bind goal digest, frozen claim-set digest, deferred
claim IDs/digests, and explicit `scope_review: pass|fail`. A passing audit is invalid unless it covers
that tuple, and scope review must fail when deferred claims materially carve out the goal's core.
Committed-scope discharge owes this audit even though `stopped_frozen` remains non-converged;
root-refuted and explicit give-up residual runs do not. Any graph wording/shape, active evidence,
freshness, goal, frozen scope, or deferred tuple change invalidates corresponding coverage.

The old model-visible nonce is removed. Anti-forgery is the private capability field on the single
child record plus exact native child binding. The author cannot create convergence-relevant
attribution or submit `audit_verdict` directly. Deliberate same-OS-user access to the bridge/store
remains outside the threat boundary and is documented honestly.

Audit input may be private where the host supports start-context injection. Audit output privacy is
reported separately and is not promised by any currently supported host profile.

## 8. Child lifecycle

A child is one operational record keyed by server-generated `child_id`. The record contains purpose,
state, spent/refunded fact, deadline, optional bound native ID, first-terminal fingerprint, and private
completion capability. V2 has no separate reservation entity, audit-ticket entity, reservation
sequence, nonce, or ticket-consumption state.

`contracts/empirica/v2/public-contract.json` is the sole transition-table owner. D1-H only maps native
host evidence onto these events:

```text
reserved -> launching -> pending -> completed
    |          |            |-----> failed
    |          |            |-----> cancelled
    |          |            |-----> timed_out
    |          |            `-----> orphaned
    |          `-----------> launch_rejected | failed
    `----------------------> launch_rejected
```

Rules:

- one server-generated `child_id`; native ID binds once at observed start;
- private capability is never public/model-visible;
- identical duplicate terminal event is idempotent; conflicting terminal event is a Fault;
- launch rejection refunds once; any observed start permanently spends the spawn;
- pending children consume no derivation pass and suppress an equivalent duplicate launch;
- completion may append audit/diagnostic facts but cannot approve deterministic evidence;
- timeout/cancellation/orphan is a residual with explicit recovery action;
- late result never reopens a terminal run;
- child state and recovery survive compaction/reload;
- after any first terminal transition, no later event may admit audit output or change child, audit,
  budget, or convergence state, even while the run remains active;
- byte/semantic-identical replay of the first terminal fingerprint returns the exact `Inert` unchanged; any
  non-identical terminal delivery returns the exact `Fault` unchanged;
- only the first admitted `pending -> completed` transition may carry audit output.

## 9. Freeze, budget, routing, and terminal behavior

### Freeze

Freeze is first-write-wins. It commits the current gating claim IDs. Those claims still require fresh
evidence and audit. Later derived claims are visible as deferred residuals and cannot silently enter
the committed scope.

### Budget

Spawn and derivation budgets are operational state. A derivation pass is consumed only when the
argument/evidence/obligation state makes semantic progress. Idle checks, pending-child waits,
repeated equivalent Stop, and host status polling consume no pass. Launch rejection refunds once;
started work remains spent.

### Route

Route must be observed before investigation. First route and first investigation are first-write-wins
witnesses. A late route is a permanent reported violation; it is not retroactively repaired by
writing another label.

### Terminal

- root refuted: stopped residual, never converged;
- unresolved human/data hold: stopped residual;
- committed frozen scope discharged with later claims: stopped frozen, non-converged; every later graph retains all committed claim IDs or fails closed without replacing the selected graph;
- budget exhausted: stopped budget, non-converged;
- all committed claims approved/fresh and independent audit current/passing: converged.

## 10. Stable next actions

```text
run.start_fresh
run.inspect
route.record
research.record
spike.run
spike.regate
claim.record_refutation
human.request_decision
budget.raise
child.spawn_auditor
child.wait
child.retry
child.cancel
residual.accept
contract.get_section
```

Each next action has a parameter schema in the canonical registry. Renderers may phrase actions for
the host but must preserve IDs and parameters.

## 11. Stable reason codes

| Code | Default next action(s) | Contract section |
|---|---|---|
| `run.no_active` | `run.start_fresh` | `run/lifecycle` |
| `run.terminal` | `run.inspect` | `terminal` |
| `run.corrupt` | `run.start_fresh` | `run/lifecycle` |
| `graph.missing` | `run.inspect` | `claims/graph` |
| `graph.invalid` | `run.inspect` | `claims/graph` |
| `route.required` | `route.record` | `route` |
| `route.late` | `run.inspect` | `route` |
| `claim.research_missing` | `research.record` | `evidence/research` |
| `claim.research_unbound` | `research.record` | `evidence/research` |
| `claim.spike_missing` | `spike.run` | `evidence/spike` |
| `claim.spike_prerequisite_missing` | `research.record`, `spike.run` | `evidence/order` |
| `claim.spike_stale` | `spike.regate` | `evidence/freshness` |
| `claim.human_decision` | `human.request_decision` | `claims/residuals` |
| `claim.refuted` | `run.inspect` | `claims/refutation` |
| `evidence.conflict` | `run.inspect`, `residual.accept` | `claims/refutation` |
| `budget.exhausted` `{resource: spawn|pass}` | `budget.raise`, `residual.accept` | `budget` |
| `audit.required` | `child.spawn_auditor` | `audit` |
| `audit.pending` | `child.wait` | `audit` |
| `audit.unreadable` | `child.retry` | `audit` |
| `audit.failed` | `run.inspect`, `child.retry` | `audit` |
| `audit.stale` `{scope: argument|claim|freeze, claim_id?}` | `child.spawn_auditor` | `audit` |
| `audit.same_model` | `child.spawn_auditor` | `audit/independence` |
| `audit.independence_unverified` | `child.spawn_auditor` | `audit/independence` |
| `child.terminal` `{state: launch_rejected|failed|cancelled|timed_out|orphaned}` | `child.retry`, `residual.accept` | `children` |
| `host.async_unsupported` | `residual.accept` | `hosts/capabilities` |
| `host.audit_output_unobservable` | `residual.accept` | `hosts/capabilities` |
| `freeze.deferred` | `residual.accept` | `freeze` |

Unknown reason/action/section references fail contract validation. At runtime an unknown internal code
renders only a minimal `run.inspect` safe fallback and is a conformance failure.

## 12. Progressive context selection

Canonical sections:

```text
core
roles
run/lifecycle
claims/graph
claims/residuals
claims/refutation
evidence/research
evidence/spike
evidence/order
evidence/freshness
audit
audit/independence
children
budget
route
freeze
terminal
hosts/capabilities
recovery
protocol
```

Deterministic selector input is presentation-only:

```text
operation_context per PublicContract `operation_contexts`
ordered reason codes already produced by evaluation
terminal run status
```

The selector never inspects obligations, witnesses, budgets, child records, workspace observations,
or host capabilities to infer another domain reason. Section selection is defined by the PublicContract
`presentation_selector` and its ordered algorithm, applied over `operation_contexts`, emitted
reason → section links, and terminal run status; the `operation_contexts` values, the per-context
section sets, and the unknown-reason fallback are owned by `contracts/empirica/v2/public-contract.json`
and are not maintained independently in this document.

Identical structured inputs produce identical ordered section IDs. Irrelevant sections are absent.
Actual rendered messages receive deterministic size fixtures in D4/D9.

## 13. Run/debug views

Public run view contains:

```text
goal, status
contract identity/digest/relevant section IDs
active/deferred obligation summaries
structured reasons and next actions
freshness changes as paths/states, not hashes
child_id/purpose/public state/deadline/recovery action
host profile/tier and missing capabilities
terminal note/residuals
```

`GetArgument` adds the current argument/dossier and public evidence citations/digests needed by an
auditor. `RestoreRun` is bounded context, not a dump of operational counters.

Host compaction is a bounded projection assembled from RunView, structured reasons/next actions,
the argument only when auditor context requires it, and the canonical untrusted-data delimiters.
There is no separate public compaction wire command and no internal compaction format exposed in
`response.schema.json`; RestoreRun returns the same bounded RunView shape (or a structured
Block/Fault), never counters, revisions, private capability, or persisted obligation-contract
identity. `GetContract` remains disjoint and never carries an argument.

Implementations may emit a redacted local operator log from existing transaction/child events. No
separate diagnostic API, persisted transition-history projection, or public wire revision is required.
Private capabilities are always redacted.

Every Block is diagnosable from `reason.code`, parameters, affected obligation/witness, current
observation, and next actions without parsing prose.

## 14. Strict rejection of every noncurrent persisted aggregate

Before semantic decoding, persisted state must contain the exact `empirica/v2` protocol and current
state-schema identity, satisfy the closed schema and procedural invariants, and reference a
consistent history. Every other aggregate returns `run.corrupt` and `run.start_fresh`, contributes
no public fields, and receives no defaults, migration, inference, adjudication, or repair.

## 15. D2/D4 acceptance fixtures

D2 creates schema/registry fixtures; D4 makes them behaviorally executable. Required cases include:

- every reason references valid sections/actions and typed parameters;
- every action/status/host tier matches Python and TypeScript mirrors;
- direct boolean evidence and direct author audit verdict are rejected;
- separate Fold-1/Fold-2 actions combine through complete active set;
- a sealed harness request created without research cannot be repaired by research appended during
  execution; duplicate/replayed/mismatched request results are rejected;
- superseded research invalidates dependent spikes until a new request/re-gate; superseded refutation
  no longer discards; simultaneous active support/refutation yields `evidence.conflict`;
- forged/ambiguous/aliased/unattributed actor data cannot produce `decorrelated` independence;
- frozen trivial-scope carve-out cannot pass audit without goal/frozen/deferred scope review;
- spike without active named research prerequisite is rejected;
- edit/delete/unreadable/outside/symlink path makes spike stale/invalid on every state-bearing read;
- re-gate supersedes and restores approval;
- all child transitions/idempotency/refund/reload/late-result cases from D1-H, including completion
  after timeout/cancel/orphan while the run remains active;
- crash/CAS retry matrices for harness request, child terminal admission, audit append, status change,
  and append-before-pointer ordering;
- freshness TOCTOU retries/reobserves or fails closed;
- route ordering, freeze first-write-wins, terminal honesty, corrupt graph/state;
- deterministic progressive selection, unknown fallback, and context upper bounds;
- noncurrent, versionless, null, future/unknown, partially matching, truncated, and
  falsely-converged persisted state all return the same safe `run.corrupt` projection with no field
  reuse or artifact adjudication;
- private capability absent from every public fixture/view.

## 16. Explicit non-goals

- generic workflow/rules/template engine;
- user-programmable reason or transition DSL;
- backwards compatibility or migration;
- absolute audit privacy or same-OS-user store secrecy;
- identical enforcement claims on hosts with different capabilities;
- filesystem/network/process access from pure core;
- exposing internal mechanics to the normal author loop.
