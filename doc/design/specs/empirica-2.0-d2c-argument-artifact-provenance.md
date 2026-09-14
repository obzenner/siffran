# Empirica 2.0 D2C — canonical ArgumentView artifact provenance

**Status:** Parent-frozen bounded observable-contract amendment exposed by D4-S2 review.

## Why reopened

Accepted D2A `ArgumentView.evidence` exposes only a reduced research/spike summary. It cannot express
D1's immutable sealed `spike_request`, request-before-result order, exact prerequisite snapshot,
command/file binding, deterministic gate, or supersession. D4-S2 therefore invented raw
`drv.artifacts()` keys and told production to align to tests, violating the D2A public-boundary rule.

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
"spike_gates": ["pass", "fail"]
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
harness_request_id       nonempty opaque identifier
command                  nonempty
command_digest           digest256
ordered dependent_files  nonempty unique strict normalized repo-relative POSIX paths
ordered prerequisite_research_ids  nonempty unique digest256 array
```

It has no caller-selected gate/exit/outcome, no active approval flag, no file result binding, and no
supersedes. Its statement digest covers the exact sealed snapshot.

### Spike result artifact

Additional required:

```text
active                     boolean
outcome                    pass | fail
harness_request_id         nonempty; matches one prior spike_request
command                    nonempty
command_digest             digest256
ordered prerequisite_research_ids  nonempty unique digest256 array
file_bindings              nonempty array of closed {path, sha256}; path normalized and unique
exit_code                  integer
spike_gate                 pass | fail, mechanically equivalent to exit_code == 0
supersedes                 digest256 | null
```

No source fields or dependent_files duplicate. Result statement digest covers exact result,
prerequisites, and bindings.

## Mechanical invariants

Raw JSON Schema enforces every expressible item closure, discriminator, required/forbidden field,
enum, digest, and path invariant. `validate_contracts.py` additionally enforces for each ArgumentView:

1. `sequence` values are unique and strictly increasing in array order;
2. `artifact_id` values are globally unique;
3. every spike result references exactly one earlier spike_request with matching claim ID/digest,
   harness_request_id, command/digest, and ordered prerequisite IDs;
4. each spike_request has at most one direct result with the same harness_request_id;
5. `spike_gate == pass` iff `exit_code == 0`; immutable result outcome is `pass` iff gate pass,
   otherwise `fail`; live stale/indeterminate evaluation belongs to RunView freshness/claim state and
   never rewrites an artifact outcome;
6. every prerequisite ID names an earlier supporting research artifact for the same claim digest;
   `active` is the current projection and may now be false after later supersession—the sealed request
   proves that the application selected the active snapshot at request time;
7. file binding paths exactly equal the sealed request's dependent_files in the same order;
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
request, mismatch harness/claim/command/prerequisites/files, duplicate/nonmonotonic sequence or ID,
wrong gate/exit, bad supersedes, prerequisite pointing to request/refuting/wrong claim, superseded
spike still active, active spike with inactive prerequisite, omitted/extra/cross-claim active evidence
ID (including two claim IDs sharing one wording digest), wrong canonical evidence digest, ordinary
approved research-only positive, `needs-experiment` approved without spike negative, `needs-decision`
approved negative, approved with active refuting/failing evidence, `needs-experiment` approved with
stale spike binding, old `evidence` field, and private capability/native IDs.

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
