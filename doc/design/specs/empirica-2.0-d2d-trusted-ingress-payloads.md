# Empirica 2.0 D2D — closed trusted-ingress payloads

> Historical Empirica 2.0 design rationale retained for the current 6.0.0 closed trusted ingress.

**Status:** Implemented; current wire is `empirica/v2` and current state is `empirica.run/3`.

## Why reopened

The trusted actions exist in accepted D2, but their `payload` is the generic open payload schema.
D4-S3a therefore invented audit key `reviewed`, magic model strings, and incomplete attribution
records. Exact independence/audit/child conformance is impossible without one canonical payload
contract.

D2D closes existing trusted action payloads. It adds no action, command, reason, status, host tier,
or runtime implementation.

## Allowed files

D1/D1-H wording, D2A/D2C superseding notes, PublicContract registry/schema if vocabularies are needed,
request schema, request fixtures/contract fixtures, `scripts/validate_contracts.py`, contracts README.
No D3/D4/runtime/Make/manifests/old tests.

## Trusted envelope boundary

The private capability reference remains application/host-held and non-public. Raw request schema
models the host-neutral application ingress shape; author dispatch with a forged/reflected reference
still fails capability admission. Payload validity never grants trust.

## Closed payloads

### `child_event`

Required closed payload:

```text
state          canonical child state excluding reserved
native_id      nonempty string | null
fingerprint    digest256
result_digest  digest256 | null
```

Rules:

- `launching|pending|completed|failed|cancelled|timed_out|orphaned` require non-null native_id;
- `launch_rejected` requires native_id null;
- completed requires non-null result_digest; other states require null result_digest;
- fingerprint is the canonical terminal/event fingerprint used for identical replay detection.

No nested `{event:{...}}` alias.

### `attribution`

Required closed payload:

```text
subject_kind      covered_actor | auditor
subject_id        nonempty stable opaque ID
child_id          nonempty | null
provider_id       nonempty | null
model_id          nonempty | null
observed_by       host | configuration
covered_artifact_ids  unique ordered digest256 array
```

Rules:

- auditor requires non-null child_id and empty covered_artifact_ids;
- covered_actor requires child_id null and nonempty covered_artifact_ids;
- provider/model are both non-null or both null; null means incomparable/unverified;
- payload carries facts only, never `independence`, `decorrelated`, aliases, tier, or verdict;
- each covered artifact ID is later correlated procedurally to current approved gating evidence.

### `audit_verdict`

Required closed payload:

```text
verdict                    pass | fail
findings                   array of nonempty strings
argument_digest            digest256
goal_digest                digest256
frozen_scope_digest        digest256 | null
deferred_scope_digest      digest256
reviewed_claims            unique ordered array of closed {claim_id,evidence_digest}
scope_review               pass | fail | null
```

Rules:

- reviewed_claims is exactly approved gating claims; deferred claims never appear;
- frozen_scope_digest null requires scope_review null;
- frozen_scope_digest non-null requires scope_review pass|fail;
- deferred scope is bound only by deferred_scope_digest plus scope_review and public residual claim
  IDs; there is no duplicate deferred reviewed-claims representation;
- no independence/attribution/model/provider field;
- application compares all fields to current ArgumentView and rejects stale/extra/missing coverage.

### `evidence_leaf`

For the current v2 contract the trusted evidence leaf correlates a deterministic generic result to
an already committed sealed request. It is not a command execution port and does not authorize host
execution. Required closed payload:

```text
harness_request_id          nonempty
request_artifact_id         digest256
plan_digest                 digest256
policy_digest               digest256
snapshot_digest             digest256
prerequisite_research_ids   nonempty unique ordered digest256 array
file_bindings               nonempty closed [{path,sha256}]
exit_code                   integer
result_digest               digest256
```

The application correlates every request identity and derives uppercase `PASS` only from a natural
zero exit and uppercase `FAIL` only from a natural nonzero exit. A timeout, forced termination,
output limit, OOM, capture, staging, lifecycle, cleanup, journal, identity, authority, or postflight
ambiguity carries no result payload. Claim identity, closed plan draft, immutable backend, and
framework-owned policy come from the sealed request and are not caller repeated. Research remains
the author `research` action validated and sealed by the application, not a trusted evidence-leaf
wire variant.

## Registry/schema parity

Add canonical vocabularies only where not already owned (`attribution_subject_kinds`,
`attribution_observers`, `audit_verdicts`, `scope_reviews`). Schema mirrors derive/check registry
values. Child states and paths reuse existing definitions. No Python expected vocabulary tables.

Raw JSON Schema enforces closure, branch/null relations, required fields, enum/digest/path shapes.
Procedural validation/fixtures enforce:

- attribution covered IDs resolve to current covered approved artifacts when admitted;
- auditor child matches one admitted pending audit child;
- audit reviewed_claims exact approved-gating coverage and current digests;
- audit argument/goal/frozen/deferred digests current;
- evidence result matches one already committed sealed generic request exactly and in order, with
  plan, policy, snapshot, file-binding, and request-artifact correlation;
- audit verdict's child_id resolves to exactly one audit-purpose completed child, and an
  auditor attribution's child_id resolves to exactly one audit-purpose pending child.

D2D statically requires these event/verdict fingerprint/digest facts and canonical clauses. It does
**not** add a persisted replay machine or pretend independent one-request fixtures prove behavior —
real idempotent/fault replay semantics are exercised by the D4 bound-SUT cases (24/25/27/28/32),
not by static contract fixtures.

Repository contract validation can use correlated request/response fixture bundles for procedural
facts; it must not implement runtime state or a second policy model.

## Fixtures/negatives

Add valid fixtures for each trusted action plus same-model/decorrelated/unverified attribution pairs
and frozen audit coverage. Add one-mutation raw negatives for unknown/missing/extra fields, null
relations, forbidden independence/gate/deferred reviewed claim, nested event alias, and private/native
leaks. Add procedural negatives for wrong child/artifact/digest/coverage, duplicate identity/path
before any map construction, and mismatched field correlation (request, plan, policy, snapshot,
file hash, natural exit, prerequisite, and result).

## D1 clarification

State these exact normalized payload facts; independence remains application-derived. First terminal
wins before late payload admission. Identical replay yields `Inert`; non-identical terminal replay
is `Fault`. Identical audit-verdict replay returns the exact `Inert` unchanged;
conflicting replay returns the exact `Fault` unchanged. Deferred scope is aggregate-digest/scope-review bound, not a reviewed
claim.

## Verification

`make contract-check`, `make check-static`, raw and procedural probes, no EXPECTED vocab tables,
`git diff --check`, empty index.

## Guardrails

Worktree only. No outside search/reviewer IDs/Git refs. No D4/runtime changes, staging, commit, push,
or subagents. Stop on contradiction.
