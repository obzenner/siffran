# Empirica 2.0 D2C — canonical ArgumentView artifact provenance

**Status:** Implemented historical rationale for the current ArgumentView artifact projection.

## Why reopened

Accepted D2A `ArgumentView.evidence` exposes only a reduced research/spike summary. It cannot express
D1's immutable sealed `spike_request`, request-before-result order, exact prerequisite snapshot,
closed generic plan and input binding, deterministic verdict, or supersession. D4-S2 therefore
invented raw `drv.artifacts()` keys and told production to align to tests, violating the D2A
public-boundary rule.

D2C makes the D1 provenance observable through one canonical typed projection. It does not add a
command, action, reason, next action, status, host tier, or runtime implementation.

## Allowed files

- `doc/design/empirica-2.0-d1-contract.md`
- `doc/design/specs/empirica-2.0-d2a-observable-contract-amendment.md` only a superseding note
- `contracts/empirica/v2/public-contract.json`
- `contracts/empirica/v2/public-contract.schema.json`
- `contracts/empirica/v2/response.schema.json`
- affected v2 GetArgument fixtures
- `scripts/validate_contracts.py`
- `contracts/README.md` only if the file inventory description needs correction

No D3/D4/runtime/Make/manifests/old tests.

## Canonical registry vocabularies

Add required closed PublicContract vocabularies:

```json
"claim_kinds": ["ordinary", "needs-experiment", "needs-decision"],
"artifact_kinds": ["research", "spike_request", "spike"],
"artifact_outcomes": ["supporting", "refuting", "pass", "fail"],
"spike_gates": ["PASS", "FAIL"]
```

Schemas and validators derive these values from the loaded registry; no Python expected tables.
Update the compact reviewed PublicContract digest only after semantic review.

## One representation: `ArgumentView.artifacts`

Replace `ArgumentView.evidence` with required ordered `artifacts`. There is no compatibility alias and
no duplicate evidence summary. Every item is closed and discriminated by `kind`.

Common claim projection correction: every closed `ArgumentView.claims[]` item requires canonical
`kind: ordinary | needs-experiment | needs-decision`, derived from the graph and never inferred by the
validator from available artifacts. This is the D1 claim kind, not a new policy boolean.

Common required fields for every artifact item:

```text
sequence          integer >= 0; append order
artifact_id       digest256
claim_id          nonempty
claim_digest      digest256
kind              research | spike_request | spike
statement_digest  digest256
```

### Research artifact

Additional required:

```text
active             boolean
outcome            supporting | refuting
source_kind        docs | code | runtime | web
source_ref         nonempty
```

No spike fields.

### Sealed spike_request artifact

Additional required:

```text
harness_request_id       nonempty content-addressed request identity
superseded draft          unreleased multi-container author shape (replaced by the Empirica 6 MVP)
draft_digest             digest256
plan_digest              digest256
policy_digest            digest256
snapshot_basis_id        digest256
snapshot_digest          digest256
inputs                    nonempty sealed input summaries with exact file_bindings
backend                   exact immutable qualified generic binding
ordered prerequisite_research_ids  nonempty unique digest256 array
```

It has no caller-selected profile, policy, authority, capture fact, verdict, exit, outcome, active
approval flag, result binding, or supersedes. The draft is author-visible but closed; framework
sealing captures every declared input in one canonical observation basis and derives the plan,
policy, snapshot, and backend identities. Its statement digest covers that exact sealed request.

### Spike result artifact

Additional required:

```text
active                     boolean
outcome                    pass | fail
harness_request_id         nonempty; matches one prior spike_request
ordered prerequisite_research_ids  nonempty unique digest256 array
file_bindings              nonempty array of closed {path, sha256}; path normalized and unique
exit_code                  integer
spike_gate                 PASS | FAIL, derived only from the verdict container's natural exit
supersedes                 digest256 | null
request_artifact_id        digest256; exact prior request artifact
plan_digest                digest256; exact request echo
policy_digest              digest256; exact request echo
snapshot_digest            digest256; exact request echo
backend                    exact immutable request backend echo
result_facts               closed generic operation/container/verdict/cleanup/postflight facts
```

No source fields or authored command/dependent-file aliases. Result statement digest covers the
exact request correlation, generic result facts, prerequisites, and bindings.

## Mechanical invariants

Raw JSON Schema enforces every expressible item closure, discriminator, required/forbidden field,
enum, digest, and path invariant. `validate_contracts.py` additionally enforces for each ArgumentView:

1. `sequence` values are unique and strictly increasing in array order;
2. `artifact_id` values are globally unique;
3. every spike result references exactly one earlier spike_request with matching claim ID/digest,
   harness/request artifact identities, plan/policy/snapshot/backend identities, and ordered
   prerequisite IDs;
4. each spike_request has at most one direct result with the same harness_request_id;
5. `spike_gate == PASS` iff the verdict container naturally exits zero; `FAIL` requires a natural
   nonzero verdict exit. Immutable dossier outcome is lowercase `pass` iff gate PASS, otherwise
   lowercase `fail`. Timeout, forced termination, output limit, OOM, capture, staging, lifecycle,
   cleanup, journal, identity, authority, or postflight ambiguity produces no result artifact;
6. every prerequisite ID names an earlier supporting research artifact for the same claim digest;
   `active` is the current projection and may now be false after later supersession—the sealed request
   proves that the application selected the active snapshot at request time;
7. file binding paths and hashes exactly equal the sealed request inputs captured from the draft in
   one canonical observation basis;
8. non-null supersedes names an earlier spike result for the same claim, never a request/research;
9. for each claim, `active_evidence_ids` equals, in artifact sequence order, the complete set of
   currently active research/spike artifact IDs matching both that claim's ID and current wording
   digest; distinct claims with identical wording digests never share active evidence;
10. claim `evidence_digest` equals the canonical `registry_digest` of that exact ordered
    `active_evidence_ids` JSON array; audit reviewed evidence digests equal that value;
11. an active spike's prerequisite research artifacts are currently active/supporting for the same
    claim digest; historical inactive spike/request rows may reference now-inactive prerequisites;
12. a spike named by a later result's `supersedes` is inactive, and at most one spike result is active
    per current claim digest;
13. approval follows canonical claim kind: `ordinary` requires active supporting research and no
    active refuting/failing evidence; `needs-experiment` additionally requires an active passing spike
    with satisfied prerequisites and no RunView freshness change for its bindings; `needs-decision`
    can never project approved. Contradictory active evidence can never project approved. A stale
    spike is unusable for Fold 2 but does not silently impose Fold 2 on an `ordinary` claim;
14. current passed/failed audit exact gating coverage rules from D2A continue unchanged and therefore
    bind the complete active-set digest above.

Fixtures carry the bounded ordered artifact history needed for these correlations; current `active`
state must not be misread as historical active-at-request state. Do not add hidden validator input.

## Fixtures and negatives

Update GetArgument fixtures to show:

- research only/open;
- research + sealed request + passing result/approved;
- stale then superseding request/result history;
- audited current gating coverage.

Add one-mutation negatives for every discriminator branch and each procedural invariant: result before
request, mismatch harness/claim/plan/policy/snapshot/backend/prerequisites/files, duplicate or
nonmonotonic sequence/ID, wrong typed verdict/natural exit/outcome, bad supersedes, prerequisite
pointing to request/refuting/wrong claim, superseded spike still active, active spike with inactive
prerequisite, omitted/extra/cross-claim active evidence ID (including two claim IDs sharing one
wording digest), wrong canonical evidence digest, ordinary approved research-only positive,
`needs-experiment` approved without spike negative, `needs-decision` approved negative, approved with
active refuting/failing evidence, `needs-experiment` approved with stale spike binding, the removed
command/dependent-file shape, old `evidence` field, and private capability/native IDs.

Fixtures are materialized/correlated from canonical registry data where applicable. No validator-only
wire vocabulary.

## D1 clarification

State that GetArgument is the public bounded provenance projection for research, sealed requests, and
spike results. `active_evidence_ids` is the exact ordered active set, and `evidence_digest` is the
canonical `registry_digest` of that JSON array. Internal append storage may differ, but hosts/tests
consume only this typed projection.
Route/investigation/freeze witnesses remain reflected through derived obligations/claims/residuals;
they are not added to this evidence artifact list.

Clarify private trusted ingress independently of wire payload: host/application adapters possess an
unexposed capability and invoke application trusted admission; appending to a host telemetry sink is
not admission and cannot satisfy child/evidence/audit lifecycle tests. Do not prescribe a concrete
runtime class or method name in D2C.

## Verification

- `make contract-check`
- `make check-static`
- raw schema probes for old evidence and malformed branch fields
- procedural probes for every ordering/correlation invariant
- no `EXPECTED_*` vocabulary tables beyond compact reviewed digests
- `git diff --check`; empty index

## Guardrails

Work only in `/private/tmp/empirica-twofold-fix`. No outside search/reviewer IDs/Git refs. Stop on a
contradiction rather than weakening D1. No staging, commit, push, subagents, or runtime/D4 edits.
