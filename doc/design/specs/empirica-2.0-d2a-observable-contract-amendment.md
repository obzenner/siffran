# Empirica 2.0 D2A implementation spec — observable contract amendment

**Status:** Parent-frozen corrective specification.

**Why reopened:** D4 behavioral tests proved that accepted D2 could not express several D1/final-DAG
externally observable facts. Discovery inventories: Luna run `8cbd1b85`, Haiku run `8e92aedc`.

**Writer:** one GLM run, sole writer.

## 1. Scope

Amend D1 and D2 only. Do not implement runtime or modify D3/D4 code. Preserve exact protocol identity
`empirica/v2` and version `2.0.0`; this is pre-release contract completion, not compatibility.

Allowed:

```text
doc/design/empirica-2.0-d1-contract.md
contracts/empirica/v2/public-contract.json
contracts/empirica/v2/public-contract.schema.json
contracts/empirica/v2/request.schema.json       # parity/reference changes only if required
contracts/empirica/v2/response.schema.json
contracts/empirica/v2/host-profiles*.json       # only if conformance references require it
contracts/empirica/v2/fixtures/*.json
scripts/validate_contracts.py
contracts/README.md                              # only if observable surface summary changes
```

Forbidden: runtime, D3 architecture files, D4 tests/report/spec, old contracts/tests, Makefile,
manifests/version, quarantined two-fold files.

## 2. Canonical PublicContract additions

Add closed canonical data, with exact key-set validator parity:

```json
{
  "claim_states": ["open", "approved", "blocked", "discarded"],
  "audit_states": ["not_required", "required", "pending", "passed", "failed", "stale"],
  "independence_states": ["decorrelated", "same_model", "unverified"],
  "freshness_states": ["present", "missing", "unreadable", "non_regular", "outside_workspace"],
  "mode_fields": ["multi_provider", "cli_exec"],
  "operation_contexts": ["bootstrap", "block", "auditor", "restore", "terminal", "on_demand"],
  "untrusted_delimiters": {
    "open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
    "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"
  }
}
```

Order is canonical. Schemas and validator extract/compare these values mechanically; do not maintain
an unverified second table.

Clarify clauses:

- a fresh `spike_request` for the same stale active head is the wire operation implementing public
  next action `spike.regate`; `spike.regate` is not an ObserveAction kind;
- RunView always reports the complete bounded public projection in §3;
- GetArgument returns §4, not a generic RunView alone;
- RestoreRun returns the same bounded RunView shape (or a structured Block/Fault), never counters or
  private state;
- adverse terminal children expose exact canonical Block recovery choices through
  `child.terminal.next_actions`; do not invent a separate state→single-action table.

## 3. Complete RunView schema

Every RunView requires all of:

```text
id
goal
status
modes
contract
obligations
residuals
freshness
children
host
```

`terminal_note` remains optional.

Shapes:

```json
"modes": {
  "multi_provider": false,
  "cli_exec": false
}
```

Both booleans required; defaults are application policy but output always reports effective values.
Request StartRun/configure schemas mirror exactly the canonical mode fields.

```json
"obligations": {"active": [], "deferred": []}
```

Both arrays required. Existing summary item stays closed. Validator checks unique obligation IDs.

```json
"residuals": []
```

Required. Each residual code and params validate against the canonical reason registry.

```json
"freshness": {
  "changes": [
    {"path": "repo/relative/posix", "state": "present"}
  ]
}
```

`freshness` and `changes` required. Changes contain only normalized path and canonical state; no hash,
recorded digest, current digest, filesystem absolute path, or error detail. Empty means no current
reported change. Items are closed, unique, and canonically path-ordered by validator/fixtures.

`children` required (possibly empty). Adverse terminal states
`launch_rejected|failed|cancelled|timed_out|orphaned` require nonempty `recovery_action`; completed
forbids it. Nonterminal states may omit it. Any recovery action must be a canonical next action.

`host` required. Existing profile/tier binding remains exact.

No RunView field for revisions, native child ID, spent/refunded fact, first-terminal fingerprint,
private capability, tickets/reservations/nonces, persisted obligation pointer/history, full contract,
or operational counters.

## 4. Typed GetArgument result

> **Superseded by D2C (`empirica-2.0-d2c-argument-artifact-provenance`).** The reduced
> `ArgumentView.evidence` array defined in this section is replaced by the one canonical
> typed `ArgumentView.artifacts` union (research | `spike_request` | spike), which makes
> the D1 sealed-request provenance, ordering, exact prerequisite snapshot, command/file
> binding, deterministic gate, and supersession observable. There is no compatibility
> alias and no duplicate evidence summary; `evidence` is removed. Everything else in this
> section (the Allow-with-argument branch, `converged == run.status == "converged"`,
> GetRun/RestoreRun/GetContract exclusivity, the claim/edge/audit shapes, and the
> passed/failed current gating coverage rule) is unchanged.

Add an Allow-with-argument response branch. It has:

```text
type = Allow
converged
run = complete RunView
argument = ArgumentView
```

Enforce the same exact `converged == (run.status == "converged")` relation in raw JSON Schema.
GetRun/RestoreRun cannot include `argument`; GetContract remains disjoint.

`ArgumentView` is closed and requires:

```json
{
  "root_claim_id": "G0",
  "argument_digest": "sha256:<64hex>",
  "goal_digest": "sha256:<64hex>",
  "frozen_scope_digest": null,
  "deferred_scope_digest": "sha256:<64hex>",
  "untrusted_delimiters": {
    "open": "<<<EMPIRICA_UNTRUSTED_DATA>>>",
    "close": "<<<END_EMPIRICA_UNTRUSTED_DATA>>>"
  },
  "claims": [],
  "edges": [],
  "evidence": [],
  "audit": {}
}
```

Digest fields use `sha256:<64 lowercase hex>`; `frozen_scope_digest` may be null before freeze.

Claim item, closed and required fields:

```json
{
  "claim_id": "G0",
  "text": "untrusted author claim text",
  "wording_digest": "sha256:<64hex>",
  "state": "open",
  "gating": true,
  "evidence_digest": "sha256:<64hex>",
  "active_evidence_ids": []
}
```

State uses canonical claim states. `gating` is the bounded public fact that the claim is in the
current committed support scope and therefore participates in audit coverage; it is derived, never
editable state. IDs are nonempty. Validator checks root exists, unique claim IDs, unique evidence
artifact IDs, and evidence references resolve.

Edge item, closed:

```json
{"from": "G0", "to": "S1", "type": "SupportedBy"}
```

Type is exactly `SupportedBy`, directed from claim to supporting claim. Endpoints must resolve and differ, edges are duplicate-free, the relation is acyclic, every claim is root-reachable, and deterministic ordering is validated.

Evidence item, closed:

```json
{
  "artifact_id": "sha256:<64hex>",
  "claim_id": "G0",
  "kind": "research",
  "statement_digest": "sha256:<64hex>",
  "active": true,
  "outcome": "supporting",
  "source_kind": "web",
  "source_ref": "https://example.test/source"
}
```

Required: artifact_id, claim_id, kind, statement_digest, active, outcome. `kind` is
`research|spike`; outcome is
`supporting|refuting|pass|fail|stale|indeterminate`. Research uses supporting/refuting; spike uses
pass/fail/stale/indeterminate. `source_kind` and `source_ref` are optional
nonempty public citation fields. No file hashes, private attribution, capability, nonce, or raw host
transcript.

Audit view, closed and always present:

```json
{
  "state": "not_required",
  "independence": "unverified",
  "reviewed_argument_digest": null,
  "reviewed_goal_digest": null,
  "reviewed_frozen_scope_digest": null,
  "reviewed_deferred_scope_digest": null,
  "reviewed_claims": []
}
```

States/independence use canonical values. Reviewed digest fields are nullable SHA-256 values.
Reviewed claim item is closed `{claim_id, evidence_digest}`. When state is `passed|failed`, reviewed_argument/goal/deferred digests are non-null and equal the
current ArgumentView digests; frozen digest equals the current nullable frozen digest; reviewed
claims are nonempty and equal exactly all current claims where `gating=true` and `state=approved`,
with each reviewed evidence digest equal to that claim's current evidence digest. For
`not_required|required|pending`, reviewed digests are null and reviewed_claims is empty. `stale`
retains non-null prior coverage digests/tuples that need not equal current values. Raw schema enforces
state-local null/non-null/minItems conditions; the validator enforces cross-array equality and current
coverage. Every reviewed claim resolves and is unique.

The untrusted delimiter values exactly equal canonical PublicContract values. Structured claim text
and citation references remain untrusted; renderers must wrap them using these delimiters. No generic
`argument: {additionalProperties:true}` escape hatch.

## 5. Typed reason amendments and raw-schema boundary

The published response schema—not only the repository validator—must reject unknown reason/residual
codes and malformed parameters. Define one reusable `reasonPayload` discriminated union with one
branch per canonical reason code; each branch has exact `code` const and the registry's closed
parameter schema. `reasonEntry` layers required next_actions/sections/optional affected/message on
that payload using Draft 2020-12 `unevaluatedProperties:false` (or an equivalently closed shape).
`residualItem` reuses the same payload and permits only code+parameters. The validator mechanically
extracts every branch and compares code and parameter schema to `public-contract.json`; no Python
reason table.

`GetContract(target=full).full` references the closed
`https://siffran.dev/contracts/empirica/v2/public-contract.schema.json`; arbitrary objects or unknown
private fields are raw-schema-invalid.

Amend `claim.spike_stale.params` to require:

```json
{
  "changes": [
    {"path": "repo/relative/posix", "state": "present"}
  ]
}
```

Nonempty, closed, same item/state rules as RunView freshness. A path is strict normalized
repo-relative POSIX: no leading slash, drive prefix, backslash/UNC, empty/repeated separator, `.` or
`..` segment. Use one shared schema definition/check for RunView and reason payloads. Block run
freshness changes and reason changes must match exactly in validator fixtures/conformance.

Amend `freeze.deferred.params` to require:

```json
{
  "claim_ids": ["G-later"],
  "deferred_scope_digest": "sha256:<64hex>"
}
```

Claim IDs nonempty/unique/canonically ordered; digest required.

Keep other empty param objects closed unless D1 already requires a typed field. Do not add internal
telemetry merely to aid tests.

## 6. Restore and compaction boundary

RestoreRun uses complete RunView and relevant sections selected for `restore`. There is no separate
public compaction wire command. Host compaction is a bounded projection assembled from RunView,
structured reasons/next actions, argument only when auditor context requires it, and canonical
untrusted delimiters. It never contains full PublicContract, operational revision/counters, private
capability, or persisted obligation-contract identity.

Add D1 wording making this distinction explicit so D4 tests do not demand an internal compaction
format from response.schema.

## 7. Fixtures

Update every RunView fixture with required modes/obligations/residuals/freshness/children/host. Use
empty values where appropriate; no fixture may rely on omitted defaults.

Add required fixtures:

```text
getargument-active.json
getargument-audited.json
restore-active.json
block-stale-spike-edited.json      # or update existing stale fixture with present change
block-stale-spike-missing.json
block-deferred-scope.json
block-child-cancelled.json
block-child-timeout.json
block-child-orphaned.json
```

GetArgument fixtures include small resolvable claim/edge/evidence/audit projections and canonical
delimiters. Restore fixture has no argument/full contract/private/counters.

Update host required fixture IDs only where a profile genuinely requires one for conformance; do not
list every fixture on every host.

## 8. Validator and negative cases

Extend `scripts/validate_contracts.py` pure checks and reason-specific mutation negatives for:

- exact new PublicContract key sets/order/values through schema mirrors and one compact reviewed
  canonical registry digest assertion; do not duplicate vocabularies in Python;
- host profiles are loaded as canonical data and checked structurally/referentially plus one compact
  reviewed host-registry digest; do not duplicate complete rows in Python;
- request mode schema parity;
- complete required RunView fields and no banned fields;
- canonical mode/freshness/claim/audit/independence enums mirrored in schemas;
- freshness path ordering/state/no hashes and stale reason↔RunView exact match;
- deferred claim ordering/digest;
- child adverse/completed recovery conditions;
- GetArgument branch exclusivity and convergence relation;
- argument root/edge/evidence/reviewed-claim referential integrity, unique artifact IDs, and exact
  passed/failed current gating-claim audit coverage;
- raw reason/residual discriminators and parameter schemas mechanically equal the registry;
- canonical digest formatting and audit cross-field conditions;
- canonical delimiters and absence of private/internal fields recursively;
- Restore fixture boundedness;
- exact required fixture inventory.

Each negative mutates one fact and expects its own diagnostic substring/code. Use the existing current
resolver API. No runtime engine.

## 9. Verification

Required:

```text
make contract-check
make check-static
```

Focused raw-schema probes must reject:

- old partial RunView omitting modes/freshness/host/arrays;
- argument on GetRun-shaped Allow if operation correlation fixture says GetRun;
- GetArgument without argument;
- stale reason with no changes or with hashes;
- unknown reason/residual code or malformed reason/residual parameters;
- arbitrary/private/unknown field in GetContract full;
- Windows/UNC/backslash/dot/dotdot/repeated-separator freshness path;
- unknown freshness/claim/audit/independence value;
- completed child with recovery action;
- adverse terminal child without recovery action;
- malformed/unresolved argument references;
- leaked capability/nonce/revision/ticket/native ID in any public response;
- audited passed/failed state with missing, extra, or stale reviewed claim coverage/digests.

Delete Python `EXPECTED_*` vocabulary/key/transition/profile-row tables. Mirror checks derive commands,
decisions, statuses, actions, states/transitions, reason/action/section IDs, D2A enums/delimiters, mode
fields, and profile facts from loaded canonical JSON. Keep only algorithmic structural constants and
compact expected SHA-256 digests for the reviewed PublicContract and host-profile registries. A
one-sided schema mutation must still fail precisely.

D4 remains unaccepted and intentionally red; do not edit or run its probe as acceptance for D2A.

## 10. Stop/escalate

Stop if implementation requires a public operational counter/revision/secret, a new protocol command,
a new reason/action/section/host tier, raw full-contract persistence in RunView, or an algorithm in
JSON. Do not broaden typed payloads with arbitrary extension maps.

## 11. Handoff

Report changed files, canonical additions, schema branches, fixture count/list, validator/negative
coverage, commands/exits, raw probes, before/after LOC, residual contract risks, forbidden files
untouched, and empty Git index. No staging/commit/push/spawn.
