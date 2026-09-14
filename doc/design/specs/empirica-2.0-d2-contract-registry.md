# Empirica 2.0 D2 implementation spec — canonical contract and schemas

**Status:** Frozen writer specification.

**Writer:** one GLM run in `experiment/empirica-2.0`.

**Normative inputs:**

- `doc/design/empirica-2.0-d1-contract.md`;
- `doc/design/empirica-2.0-d1h-host-capabilities.md`;
- `doc/design/empirica-2.0-final-dag.md` D2;
- existing contract conventions under `contracts/` and `scripts/validate_contracts.py`.

D1/D1-H semantics are fixed. The writer may choose small local helper structure but may not rename or
add public semantics, host tiers, states, reasons, actions, sections, or compatibility behavior.

## 1. Goal

Create the machine-readable v2 public contract, exact host profile registry, request/response schemas,
and static fixtures. Extend the existing Make-driven contract validator so these files are
referentially and schematically checked.

D2 is static contract work only. It does not change runtime dispatch, state, evidence, adapters,
skill prose, plugin version, or the quarantined two-fold implementation.

## 2. Required files

Create:

```text
contracts/empirica/v2/public-contract.json
contracts/empirica/v2/public-contract.schema.json
contracts/empirica/v2/host-profiles.json
contracts/empirica/v2/host-profiles.schema.json
contracts/empirica/v2/request.schema.json
contracts/empirica/v2/response.schema.json
contracts/empirica/v2/fixtures/*.json
```

Update only as required:

```text
contracts/README.md
scripts/validate_contracts.py
Makefile                         # only if existing contract-check cannot discover v2 automatically
```

Tests for validator behavior belong in the existing repository test/validator convention discovered
from Make. Do not introduce pytest, Node dependencies, a schema code generator, or a second lifecycle
entrypoint in D2.

## 3. Canonical PublicContract shape

`public-contract.json` is inert structured data, not an interpreted policy/rules DSL.

Top-level required shape:

```json
{
  "id": "empirica/public",
  "version": "2.0.0",
  "protocol": "empirica/v2",
  "commands": [],
  "decisions": [],
  "statuses": [],
  "actions": {"author": [], "trusted": []},
  "child_lifecycle": {
    "states": [],
    "terminal_states": [],
    "transitions": []
  },
  "host_tiers": [],
  "next_actions": {},
  "reasons": {},
  "sections": {}
}
```

Use JSON objects keyed by stable ID for `next_actions`, `reasons`, and `sections`. Object order must
not carry semantics; validator sorts IDs for deterministic reporting.

Do not put a digest inside the registry: runtime D9 computes canonical SHA-256, avoiding a circular
self-digest.

### Closed values

Commands:

```text
StartRun ResolveRun ObserveAction EvaluateRun GetRun GetArgument GetContract RestoreRun
```

Decisions:

```text
Allow Block Inert Fault
```

Statuses:

```text
active converged stopped_residual stopped_frozen stopped_budget
```

Author actions:

```text
graph research spike_request configure_run route investigate dispatch freeze child_reserve
```

Trusted actions:

```text
evidence_leaf attribution child_event audit_verdict
```

Host tiers:

```text
full_async foreground_only observational
```

Child states:

```text
reserved launching pending completed launch_rejected failed cancelled timed_out orphaned
```

Terminal child states:

```text
completed launch_rejected failed cancelled timed_out orphaned
```

Canonical transitions exactly:

```text
reserved -> launching
reserved -> launch_rejected
launching -> pending
launching -> launch_rejected
launching -> failed
pending -> completed
pending -> failed
pending -> cancelled
pending -> timed_out
pending -> orphaned
```

No `started`, `voided`, ticket, nonce, reservation entity, or phase enum.

### Sections, reasons, and next actions

Transcribe exactly from D1 §§10–12. Every section contains:

```text
title
summary
clauses[]: {id, text}
```

Clause IDs are stable slash-qualified IDs under their section and unique globally. Clause text is
concise, author-facing normative behavior; do not copy ADR rationale or internal store/CAS details.

Every next action contains a short description and JSON-Schema-compatible parameter object. Every
reason contains:

```text
parameter schema
ordered next_action IDs
ordered section IDs
short non-normative default message template
```

Parameter schemas are exact:

- `budget.exhausted`: required `resource`, enum `spawn|pass`;
- `audit.stale`: required `scope`, enum `argument|claim|freeze`; optional `claim_id`, permitted only
  for claim scope by validator cross-check;
- `child.terminal`: required `state`, enum `launch_rejected|failed|cancelled|timed_out|orphaned`;
- other reasons use only parameters needed by the D1 message/witness, with no free-form control enum.

Rendered prose is not executable policy. Validator checks links and parameters; runtime core later
emits reasons.

## 4. Host profiles

`host-profiles.json` is exact tested deployment data, separate from public behavior vocabulary.
Include only:

```text
claude-code 2.1.270              foreground_only; candidate full_async probe named
pi 0.84.1 + pi-subagents 0.50.0  candidate full_async; foreground fallback
pi 0.84.1 native                 foreground_only
codex-cli 0.146.0                observational
```

Each row declares:

```text
host/profile IDs and exact versions
current tier
candidate tier if any
required fixture IDs
required live probe IDs
privacy facts: input_private/output_private booleans or unverified
unsupported capability reason IDs
source references
```

No semver ranges, implicit inheritance, automatic newer-version acceptance, or host algorithm.

## 5. Request schema

Protocol identity is exact `empirica/v2`. Preserve opaque request ID/correlation and exact command
discrimination, but implement only the static shape; runtime uses it in D6.

Required command shapes:

- `StartRun`: selector, nonempty goal, optional budgets and modes;
- `ResolveRun`: selector;
- `ObserveAction`: run handle, one discriminated v2 action, optional observation metadata;
- `EvaluateRun`: run handle, intent `continue|report_convergence|stop`, optional observation metadata;
- `GetRun`, `GetArgument`, `RestoreRun`: run handle;
- `GetContract`: target `index|full|section` and required section ID for `section` target.

- `spike_request`: claim ID, nonempty command, and nonempty normalized dependent-file list;
- `configure_run`: at least one budget or closed mode update;
- `route`: nonempty routing reason;
- `investigate` and `freeze`: no payload fields;
- `dispatch`: target actor/role and optional claim ID;
- `child_reserve`: purpose, role/profile request, foreground/async request, and optional deadline;
- trusted `evidence_leaf`, `attribution`, `child_event`, and `audit_verdict`: exact control fields plus
  required private trusted envelope/capability; their control object is closed and extensible domain
  data, if required, lives under one explicit `payload`/`extensions` object.

Trusted actions without the envelope and author actions with a trusted envelope are schema-invalid.
The envelope contains an opaque nonempty capability in wire input; committed examples redact it.

Use strict `additionalProperties:false` for every action/control/envelope object. Do not defer per-kind
control validation to D6; only deep graph/evidence/actor payload semantics may remain for D6.

## 6. Response schema

Every result has exact protocol/request correlation.

- `Allow`: one public `converged` boolean plus status-bearing RunView. RunView does not duplicate
  `converged`; validator requires `Allow.converged == (run.status == "converged")`.
- `Block`: nonempty structured `reasons[]`; each has code, parameters, affected obligation/witness
  references where applicable, ordered next actions, and relevant sections. Human message is
  optional/non-normative.
- `Inert`: closed reason code.
- `Fault`: closed code, fail direction, optional diagnostic message; no free-form behavior branch.

Run view contains PublicContract identity/reference, goal/status/converged, derived obligation and
residual summaries, public child summaries, host profile/tier/missing capabilities, and terminal note.
It contains no private capability, nonce, reservation/ticket, artifact path, CAS revision, obligation
contract revision/pointer/history, phase, raw workspace hash, or native transcript path.

`GetContract` result contains contract index/one section/full data according to target. Static contract
responses do not require run state.

## 7. Fixtures

At minimum add valid fixtures for:

```text
start/bootstrap Allow
structured open-claim Block
stale-spike Block
pending-audit Block
child terminal residual Block
terminal stopped_frozen Allow (converged=false)
terminal stopped_budget Allow (converged=false)
converged Allow (converged=true)
old-version Fault/Block with run.start_fresh
GetContract index
GetContract one section
GetContract full
host async unsupported
host audit output unobservable
trusted child event with redacted private capability input and redacted public output
```

Add invalid in-memory validator cases (not necessarily committed JSON fixtures) for:

- unknown reason/action/section;
- malformed reason parameters;
- reason linked to missing section/action;
- invalid child transition;
- nonterminal state listed terminal or vice versa;
- strict per-kind request branches reject forged/missing trusted envelopes and unknown control fields;
- response `Allow.converged` equals whether `run.status` is `converged`, with no duplicate RunView
  boolean;
- PublicContract identity digest is required and formatted as `sha256:<64 lowercase hex>`;
- residual parameters and child recovery actions validate against canonical reason/action entries;
- GetContract response target and section ID exactly match its request and use target-discriminated
  response branches;
- exact reason/action/section/profile key sets and every schema discriminator mirror the registry;
- private capability or banned v1 fields in a public response;
- phase, nonce, ticket, reservation entity, persisted obligation contract fields;
- semver range or unlisted inherited host profile;
- missing exact protocol/state identity where applicable.

GetContract fixtures must not hand-copy the full registry/index. Store a minimal request plus a narrow
`expected_from_registry` projection directive (or equivalent existing-fixture convention); the
validator materializes the expected response from the canonical registry, then validates and compares
it. The committed registry remains the only full copy.

Fixture names/IDs are stable and are referenced by host profile conformance requirements.

`input_private: "unverified"` is intentional for current host profiles unless official/live evidence
proves the dossier/tool input is hidden from the author; successful input mutation alone is not a
privacy guarantee.

## 8. Validator behavior

Extend `scripts/validate_contracts.py` as the one static contract entrypoint. It must:

1. validate JSON against v2 schemas;
2. validate every committed v2 fixture;
3. check reason → action/section referential integrity;
4. check clause ID global uniqueness and section prefix;
5. check child state/terminal/transition consistency and exact expected transition set;
6. check exact host profile key/fact sets, exact versions, known tiers/reasons/fixtures/probes, and no
   ranges; public run profile/tier combinations must resolve to that registry;
7. check response reason and residual parameters against registry schemas, exact ordered
   next-action/section lists, and known child recovery actions;
8. check PublicContract closed values and exact reason/action/section key sets match this spec;
9. extract request/response schema discriminators/enums and mechanically compare them to the registry;
10. reject banned v1/internal public fields;
11. correlate each GetContract fixture request target/section with its exact result projection;
12. produce deterministic path/ID diagnostics and nonzero exit on drift.

Keep helpers pure: load/validate only in `main()`, with schemas/registry/fixtures/error collectors
passed explicitly. Use the current `referencing.Registry`/jsonschema API so green Make output has no
RefResolver deprecation warning. Negative cases are a table of one mutation each with an expected
diagnostic substring/code, so a case cannot pass for the wrong rejection reason. Do not create a
runtime contract engine.

## 9. Red-first and verification

Before implementation, demonstrate at least one failing validator/test for missing v2 registry or
broken link, then make it green. Record the command and failure summary in the handoff; do not commit
red state.

Use Make only:

```text
make contract-check
make check-static
```

If focused validator tests have an existing Make target, use it. Do not invoke repository test files
by path. Do not run full `make check` for D2 unless focused suites expose a cross-suite issue.

## 10. Forbidden changes

Do not modify:

```text
plugins/empirica/application/**
plugins/empirica/core/**
plugins/empirica/adapters/**
plugins/empirica/hooks/**
plugins/empirica/tests/test_application.py
plugin manifests/version
generated plugin tables
```

Do not delete v1 yet; D6 performs strict runtime cutover/subtraction after v2 conformance exists.
Do not add compatibility adapters, dynamic runtime registry loading, generic rule engines, new package
dependencies, or code generation in D2.

## 11. Stop/escalate

Stop and ask the parent if:

- D1 contains a missing/contradictory closed value;
- JSON Schema cannot express a required invariant without nontrivial executable policy;
- existing contract-check architecture would require runtime application imports;
- a fixture requires inventing a host behavior or reason;
- changes outside the allowed files are needed.

## 12. Mandatory handoff

Report:

- files changed/created;
- exact registry counts: sections, clauses, reasons, next actions, child transitions, host profiles,
  fixtures;
- red-first failure evidence;
- Make commands with exit codes and concise output;
- schema/validator design and any invariant implemented procedurally;
- before/after runtime LOC (must be unchanged for D2);
- unresolved decisions/residual risks;
- confirmation that quarantined files were untouched and no files were staged/committed.
