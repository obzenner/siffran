# Empirica 2.0 D5/D5-F — pure freshness and immutable spike-execution contracts

**Status:** Parent-revised proposal after Terra/Sol review; requires final acceptance before implementation.

## Purpose and stage boundary

D5 defines the minimum host-neutral facts and ports that D7 must consume. D5-F proves deterministic
freshness, immutable-byte spike execution, fail-closed observation, and no-I/O core. It does **not**
add `application.v2`, dispatch commands, project RunView, mutate repositories, append/supersede
artifacts, implement CAS, change adapters/service, or make D4 behavior green.

D6 later subtracts v1/compatibility and adds strict v2 dispatch. D7 integrates one observation
snapshot into every state-bearing transaction and builds the one D2C spike-result statement. D7-W
owns CAS/retry/interleaving proof. D5 records those handoff obligations but does not simulate a
transaction or add a second state model.

## Allowed files and size debit

Production:

- `plugins/empirica/core/freshness.py`
- `plugins/empirica/application/ports.py`
- `plugins/empirica/application/observation.py`

Tests:

- `plugins/empirica/tests/test_freshness.py`
- `plugins/empirica/tests/test_observation.py`

Architecture metadata/test adjustment only if needed for these exact boundaries. No service,
adapters, v1 wire/state, D4, contracts, manifests, or Make changes.

D5 may add at most **450 reachable production LOC** across the three named files. Tests do not count
as runtime reduction. D5 claims no reduction. Before D6 acceptance, its frozen deletion budget must
repay all D5 runtime additions plus D6 additions and still reduce the D0 reachable total by at least
one line; relocation/generated/vendor code still counts.

## Strict D2 path and digest SSOT

`validate_relative_posix_path(value) -> str` validates; it never cleans or rewrites. It accepts the
input unchanged only when already in D2 strict normalized form:

- nonempty string, `/` separators only;
- no leading `/`, backslash, NUL, colon anywhere, empty segment, `.` segment, `..` segment,
  repeated separator, or trailing separator.

One constructor/predicate is reused for requested paths, bindings, observations, and active heads.
Canonical set-like path unions are lexical duplicate-free tuples **after** strict validation.

A digest is exact lowercase `sha256:<64 hex>`. Canonical JSON hashing sorts mapping keys, preserves
sequence order, and rejects sets/frozensets and unsupported values. Sequence order is identity.
Command digest is SHA-256 of exact UTF-8 command bytes; no trimming/normalization.

## Pure core facts and functions

Closed immutable values in `core/freshness.py`:

```text
FileBinding(path, sha256)
FileObservation(path, state, sha256|null)
ObservationState = present | missing | unreadable | non_regular | outside_workspace
ActiveSpikeHead(artifact_id, claim_id, harness_request_id, file_bindings)
FreshnessChange(path, state)                   # never a hash
StaleHead(artifact_id, changes)
FreshnessEvaluation(stale_heads)
ExecutionFacts(file_bindings, exit_code, gate, result_digest)
```

- bindings always require a digest and are unique by path; their supplied sequence order is identity;
- present observations require a digest; non-present observations require null;
- `ActiveSpikeHead.artifact_id` is digest256; claim/request IDs are nonempty opaque strings;
- bindings are nonempty, strict-path-valid, ordered, and unique by path;
- `FreshnessEvaluation` has one representation: ordered stale heads with each head's ordered changes.
  D7 may derive aggregate public changes/stale IDs; D5 does not store duplicate lists;
- `ExecutionFacts` contains only newly observed execution facts. It repeats no request command,
  request ID, claim identity, snapshot digest, statement digest, or artifact identity. D7 builds and
  hashes the sole D2C spike-result statement.

Pure functions:

1. `required_paths(active_heads)` returns the lexical union of all supplied active-head bindings.
   Every supplied active head is considered regardless of recorded pass/fail gate; active projection
   is trusted D7 input.
2. `validate_observations(requested_paths, observations)` requires exact one-to-one coverage in the
   requested canonical order. Missing, extra, duplicate, reordered, malformed, or path mismatch
   raises typed `ObservationContractError`.
3. `evaluate_freshness(active_heads, observations)` compares each binding to the exact observation.
   Same present digest is fresh. Present digest mismatch yields state `present`; absent/error yields
   its exact state. Output includes only stale heads and hash-free changes, in supplied head/binding
   order. It executes/plans nothing.
4. `gate_from_exit_code`: non-boolean integer zero→pass, nonzero→fail; other values reject.
5. `execution_facts(bindings, harness_result)` validates exact result types and derives gate only from
   exit code.
6. Canonical digest helpers reject non-finite floats recursively and serialize only standard JSON
   scalar values, with sequence order preserved.

No public `compare_spike`, `SpikeFreshness`, `RegatePlan`, planner, partial attestation digest, or
aggregate `files_hash` compatibility field.

`core/freshness.py` imports no filesystem, subprocess, clock, environment, repository, adapter, or
application module and performs no I/O.

## Application port facts

`application/ports.py` contains Protocols and immutable transport facts only.

### Captured workspace fact

```text
CapturedFile(observation: FileObservation, content: bytes|null)
```

- present requires immutable bytes whose SHA-256 equals observation.sha256;
- non-present requires null content;
- content is private execution material and is never projected/persisted as evidence;
- a tuple of CapturedFile is one atomic port return, not separately read hashes/bytes.

### Ports

```text
Workspace.observe(paths: tuple[str,...]) -> WorkspaceCapture
WorkspaceCapture(basis_id: str, files: tuple[CapturedFile,...])
SpikeHarness.run(command: str, snapshot: ExecutionSnapshot) -> HarnessResult
HarnessResult(exit_code:int, result_digest:digest256, snapshot_digest:digest256)
```

Workspace returns one coherent batch: a nonempty opaque basis ID plus exactly one capture per
requested path. The complete tuple represents one immutable workspace generation; an implementation
that cannot obtain a coherent multi-file capture fails unavailable rather than mixing generations.
It does not normalize/drop invalid input. Errors become exact non-present states rather than partial
bytes.

`ObservationSnapshot`, `ExecutionSnapshot`, `HarnessResult`, and `ExecutionFacts` are invalid-state-
resistant immutable facts: direct construction validates tuple/type/cardinality/order, unique binding
paths, content-to-binding digests, canonical snapshot digest, digest fields, non-boolean integer exit
code, and `gate == gate_from_exit_code(exit_code)`. Empty observation snapshot uses a nonempty canonical
basis ID. Malformed port values remain representable only at `CapturedFile`/`WorkspaceCapture`, where
the shell rejects them fail-closed.

The harness receives no Workspace and no ambient live-workspace/cwd authority. It executes only
against a materialized read-only view made from captured bytes. Real adapter implementations must
sandbox/materialize that view or fail unavailable; they never fall back to the live workspace.

HarnessResult echoes the exact supplied snapshot digest. It carries no gate/approval/claim/artifact
facts. Harness is a trusted deterministic boundary; application rejects snapshot-digest mismatch.

## Imperative shell factories

`application/observation.py` owns:

```text
build_observation_snapshot(active_heads, workspace) -> ObservationSnapshot
build_execution_snapshot(dependent_paths, workspace) -> ExecutionSnapshot
execute_spike(command, dependent_paths, workspace, harness) -> ExecutionFacts
```

A private `_capture(paths, workspace)` may be shared; there is no duplicated policy.

### Observation snapshot

- compute complete `required_paths` once;
- empty set returns canonical empty immutable snapshot with zero Workspace calls;
- otherwise call Workspace exactly once with the complete tuple;
- catch port exceptions as typed `ObservationUnavailable`;
- validate the coherent batch basis, exact captures/content digests/coverage, then discard private
  contents;
- return immutable observations, opaque basis ID, and digest; no partial/default snapshot.

### Execution snapshot and spike

- dependent paths are a nonempty tuple of strict-valid unique paths; preserve the sealed request's
  exact supplied sequence order and never sort or normalize it;
- command is a nonempty string and is passed byte-for-byte (whitespace is preserved); invalid command
  fails before Workspace/Harness calls;
- call Workspace exactly once for the complete tuple;
- every result must be present with immutable bytes and matching digest; otherwise
  `ObservationUnavailable` before harness invocation;
- construct bindings and digest from that same coherent capture, never a separate per-file or later
  read;
- call harness exactly once with exact command and exact immutable snapshot;
- reject harness exception as `HarnessUnavailable` and echoed snapshot mismatch as
  `HarnessContractError`;
- return `ExecutionFacts` from snapshot bindings + harness result; derive gate only from exit code;
- no append, activation, supersession, approval, request identity, or transaction mutation.

The API accepts command + dependent paths, not an undefined active-head/request union and not a second
sealed-request DTO. D7 supplies those two values from the canonical D2C `spike_request` and correlates
all request-owned IDs/digests/prerequisites when building the result statement.

## Normative D7/D7-W handoff

Every D7 GetRun, GetArgument, RestoreRun, and EvaluateRun transaction that can report derived state,
reasons, audit coverage, obligations, or convergence obtains a complete ObservationSnapshot for the
active-head set selected by that transaction revision. Static GetContract/contract identity lookup is
observation-free.

A transaction may take repeated **complete** captures, but a successful CAS commit has exactly one
unmixed snapshot basis: evaluation and projection are recomputed wholly from the latest selected
snapshot, and the decision binds that snapshot's basis ID/digest plus canonical active-head identity.
A CAS conflict always discards the entire attempt and reselects heads/re-observes. An active-set change
or any workspace-basis change detected before commit likewise discards the attempt and re-observes,
or fails closed. The contract does not invent an unavailable atomic filesystem/CAS operation or
require an unconditional second observation; it requires retry for every detected change and never
permits observations, freshness results, or projections from different captures/attempts to mix.

D7-W must test head add/remove between observation/commit, changed observation after failed CAS,
overlapping paths as one lexical call while all heads evaluate, and full re-observation/recomputation
on retry. These are future D7-W acceptance tests, not D5 fake-CAS behavior.

D7 re-gates only through a fresh canonical D2C spike_request, calls `execute_spike` with that request's
exact command/dependent-file order, and alone appends/supersedes via CAS. D5 has no re-gate planner.

## D5-F proofs

Red-first tests cover:

1. strict already-normalized D2 paths; all empty/dot/dotdot/repeated/trailing/backslash/absolute/
   colon-anywhere/NUL forms reject unchanged, including `dir/a:b`;
2. five observation states and digest/content branch invariants;
3. sequence-preserving canonical JSON digest; mapping permutation stable, sequence permutation changes
   identity, unordered containers and nested/non-nested NaN/positive or negative infinity reject;
4. exact observation coverage/order: missing/extra/duplicate/reordered/path mismatch fail;
5. fresh match and present/missing/unreadable/non_regular/outside_workspace stale changes per head;
6. every supplied active head evaluates, including overlapping paths and heads independent of gate;
7. empty observation snapshot makes zero calls; nonempty makes exactly one complete lexical call;
8. workspace exception/malformed capture/content-digest mismatch fail with no partial snapshot;
9. snapshot/facts immutability plus direct-constructor negatives for cardinality/order/content/digest,
   duplicate bindings, and exit/gate contradiction;
10. execution snapshot preserves the exact supplied dependent-file order, contains matching immutable
    captured bytes/bindings, and harness is called exactly once with no workspace argument;
11. mutate fake workspace after observe returns and before harness runs: harness sees original captured
    bytes and echoed digest, never mutated ambient bytes; a harness requiring ambient cwd/workspace is
    unavailable/rejected by the fake contract;
12. non-present execution capture prevents harness call; harness exception and snapshot echo mismatch
    fail closed;
13. exit code is sole gate authority; empty/non-string command rejects before any port call while
    exact nonempty whitespace is preserved; deterministic ExecutionFacts contain no request/statement/
    attestation identity;
14. AST/import proof core freshness has no I/O imports/calls;
15. historical 2F5 reversal without core I/O: fixed sealed facts + changed supplied observations
    becomes stale; identical supplied observations remain identical.

Fakes record calls/captured bytes/results only and contain no freshness/adjudication policy.

## Stop conditions and verification

Stop if implementation needs public D2 wire changes, v1 compatibility, adapter behavior, transaction
semantics, repository mutation, or a second identity/state representation.

Run `make check-core`, `make check-static`, D4 preflight green, D4 normal still red only at absent seam,
`git diff --check`, empty index. Record exact runtime LOC/file debit and an honest D5 red-first report
containing the observed pre-production ImportError/ModuleNotFoundError commands/output and final green
commands; no reduction claim.
