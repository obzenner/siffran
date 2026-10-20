# Empirica 2.0 D5/D5-F — pure freshness and immutable generic-spike contracts

**Status:** Implemented historical design lineage, revised in place for Empirica `6.0.0`, wire
`empirica/v2`, and state `empirica.run/3`.

## Purpose and preserved decision

D5 established the separation between pure evidence/freshness decisions and effectful workspace or
sandbox adapters. That architectural decision remains current: core code consumes immutable typed
facts, the application owns capture and correlation, adapters own observation and effects, and no
core module performs filesystem, subprocess, Docker, clock, network, or repository I/O.

The original command-string execution DTOs are not retained. The current design replaces them with a
the then-current unreleased multi-container draft (superseded by the Empirica 6 MVP), a single canonical workspace-capture basis, an immutable
framework-sealed plan and snapshot, an exact run-bound generic backend, and closed generic result
facts. There is no migration reader, dual form, command alias, dependent-file alias, or host execution
fallback.

## Ownership

- `core/freshness.py` owns immutable file observations, file bindings, active spike heads,
  observation-state vocabulary, and canonical observation digests.
- `core/spike_plan_draft.py` validates the author's closed generic draft.
- `core/spike_plan.py` owns framework policy, sealed plan identity, resource/security ceilings,
  workload timeout (120 seconds), and backend-operation timeout (210 seconds).
- `application/observation.py` captures current active bindings for freshness reads only.
- `application/generic_spike.py` captures and seals a draft, constructs immutable generic harness
  requests, validates exact result echoes, and projects request/result facts.
- `application/transaction.py` owns request-before-effects and result-after-effects CAS admission.
- `adapters/sandbox/*` own generic qualification, planning, staging, execution, journaling, cleanup,
  postflight, and bounded process effects.

No layer accepts an authored profile, policy, authority, host path, Docker option, image-acquisition
instruction, credential, derived capture fact, or runtime identity.

## Immutable freshness values

```text
ObservationState = present | missing | unreadable | non_regular | outside_workspace
FileObservation(path, state, sha256 | null)
FileBinding(path, sha256)
ObservationSnapshot(observations, basis_id, observations_digest)
ActiveSpikeHead(artifact_id, claim_id, harness_request_id, file_bindings)
```

Rules retained from D5:

1. paths are normalized repository-relative POSIX paths;
2. present observations require a digest and every non-present observation forbids one;
3. bindings require present content digests;
4. duplicate paths are rejected rather than overwritten;
5. observation order is canonical and participates in `observations_digest`;
6. freshness compares an active result's immutable bindings with one current observation snapshot;
7. present digest mismatch and every non-present state are stale;
8. current equal digests are fresh;
9. no cache, timestamp, or mutable latest pointer is approval authority.

## Closed author draft

A public spike action contains exactly:

```text
kind      spike_request
claim_id  nonempty canonical claim id
plan      superseded unreleased multi-container author shape
```

The draft declares bounded containers, exact selectable repository/config digests supplied by an
operator out of band, numeric users, entrypoint/argv arrays, environment overrides, container workdirs, named input mounts, private tmpfs,
resources, declared inputs, optional private topology, and startup order. Exactly one container is the
verdict container. The isolated profile permits declared internal private networks for intra-plan
communication and denies outbound egress. The networked profile permits framework-created
non-internal private networks, framework-derived sole aliases, and outbound Internet egress. Neither
profile permits host ingress or published ports. Ports, host networking,
privileged mode, device/socket/config mounts, public endpoints, mutable image tags, pulls, credentials,
and ambient host paths are outside the vocabulary. Empirica exposes no public image discovery, and a
qualification probe image is backend evidence rather than a runtime allowlist. Environment override
values persist verbatim in the immutable request, so they must never contain credentials or secrets;
observer/UI/author projections contain only names and no standalone value fingerprint or length. Required content-addressed draft/plan identities can confirm guessed low-entropy values, so authored values must never contain secrets.

## One-basis capture and sealing

`capture_and_seal_generic_plan(draft, workspace)` performs one `Workspace.observe(...)` call over the
globally canonical set of declared paths. It requires each entry to be present, regular, readable,
byte-backed, unique, correctly digested, and within per-file and aggregate framework limits. The
returned immutable snapshot partitions those exact bytes by declared input without reading again.

The application then seals:

- canonical draft and `draft_digest`;
- framework-owned policy and `policy_digest`;
- captured inputs, bindings, basis, bytes, and `snapshot_digest`;
- plan and `plan_digest`;
- exact immutable `GenericQualifiedBackend` selected once at `StartRun`.

A malformed, reordered, missing, extra, duplicate, mutated, or over-limit observation refuses before
sandbox effects. The harness receives no workspace port and cannot reacquire live repository bytes.

## Generic request and result ports

The application port is typed, not a command executor:

```text
GenericHarnessRequest(
  run_id, backend, plan, plan_digest, policy_digest, snapshot
)

GenericHarnessResult(
  run_id, backend, plan_digest, policy_digest, snapshot_digest,
  operation_facts, container_facts, verdict_container_id,
  verdict, verdict_exit, cleanup_proven, postflight_identity_digest
)

GenericHarnessUnavailable(reason)
```

`GenericOperationFacts` bind the operation, qualification record, execution binding, images, inputs,
processes, topology, and timing. Each `GenericContainerFact` contains closed stdout/stderr byte counts
and digests, natural-exit/forced-termination facts, and an exit code only where valid. These facts
contain no raw output, credential, host/runtime/staging path, Docker socket/config value, endpoint,
inspect/attach payload, or runtime container ID.

The exact result validator rejects mismatched run, backend, plan, policy, snapshot, operation,
container set, verdict container, cleanup, or postflight identity. Every refusal is unavailable and
produces no deterministic result artifact.

## Verdict semantics

Only the verdict container's natural exit yields a typed verdict:

- natural exit `0` → `GenericVerdict.PASS` and dossier outcome `pass`;
- natural nonzero exit, including natural `124` or `125` → `GenericVerdict.FAIL` and dossier outcome
  `fail`.

Timeout, forced termination, output limit, OOM, capture, staging, attach, inspect, identity, cleanup,
journal, recovery, authority, census, or postflight ambiguity is unavailable, not FAIL. Service
container exits never decide the verdict. A caller cannot supply the verdict, exit, outcome, gate,
cleanup proof, or postflight identity.

## Authority and sandbox boundary

`StartRun` observes authority once. Only `type(selected) is GenericQualifiedBackend` persists a
qualified binding; every exception, unknown value, old value, or subclass freezes
`{"kind":"spikes_unavailable"}`. Production composition is exactly `GenericQualificationStore`,
`GenericBackendSelector`, `GenericAuthorityResolver`, and `GenericDockerHarness`.

`EMPIRICA_SPIKE_PROFILE` is launch-process authority only. Absence means isolated; only
`docker-isolated-v1` and `docker-networked-v1` are accepted; malformed values refuse before
`StartRun`. Qualification is pull-disabled, uses an already-local exact repository/config digest,
runs seven ordered probes under 600 seconds, proves exact pre/post container/network census equality, and affects only
future runs.

The backend operation owns one 210-second deadline around the 120-second workload. Pi owns a
300-second spike transport deadline and Claude MCP owns 360 seconds, preserving
`120 < 210 < 300 < 360`; ordinary Pi transport remains 30 seconds.

## Two-CAS application admission

A spike request is one transaction with two durable admissions:

1. validate the closed draft and current claim/research prerequisites;
2. capture one canonical workspace basis and seal request facts;
3. append the immutable `spike_request` artifact and manifest, then CAS the run state;
4. only after request CAS succeeds, invoke the generic harness exactly once;
5. validate the closed result against the sealed request and freshly re-observe exact bindings;
6. append the result artifact and manifest, then CAS the run state;
7. on a result-CAS conflict, revalidate and retry only result admission with the same facts; never
   rerun the harness in that call.

A crash or a separate public call may execute a reachable sealed request that has no result. A crash
before request CAS performs no effect. A result without a reachable committed request is never
admitted. Physical artifact orphans not linked by a committed manifest are ignored.

## Fresh state-bearing reads

Every `GetRun`, `GetArgument`, and `EvaluateRun` assembles a fresh observation snapshot for the
complete deduplicated set of active spike bindings. It does not persist freshness. The RunView
projects exact ordered changes; `claim.spike_stale` carries the same change list. Repeating the same
observation is stable. Supersession preserves history, deactivates the old result, and leaves
unrelated active results untouched.

Re-gating uses another canonical public `spike_request`; there is no hidden re-gate command. Only the
stale claim's new request/result supersedes its prior result. Result-CAS retry does not rerun either
the stale claim or unrelated claims.

## Verification retained and strengthened

The restored v2 freshness suite proves:

1. separately submitted research and generic result combine on the same claim;
2. caller-supplied approval fields cannot approve;
3. claim wording changes detach prior evidence while preserving history;
4. request artifact precedes and exactly correlates the result artifact;
5. only natural PASS approves and natural FAIL refutes across claim kinds;
6. stale results remain visible even when another reason dominates claim state;
7. every state-bearing read observes every active bound path;
8. present edits, missing, unreadable, non-regular, outside-workspace, and digest changes fail closed;
9. re-gating invokes the fake generic harness once, supersedes only the stale result, preserves
   unrelated evidence, and restores freshness;
10. all workspace reads cross the explicit observation port.

Generic application, plan, staging, authority, execution, effects, harness, qualification, run/3,
contract, observer, and host-adapter suites independently prove the closed boundaries above. Tests
use a programmable generic fake authority and harness; they do not run Docker or execute a host
command.

## Guardrails

This design retains no command-string execution port, old Docker profile, qualification loader,
alternate harness, host fallback, migration, dual state reader, or compatibility type. Generated
schemas, fixtures, public tools, vendor copies, and goldens are updated only through their Make-owned
generators. Contributor acceptance is `make check`, plus `make adr-check`, `make docs-check`,
`make suites-check`, `make lint`, `make empirica-architecture-check`, and `git diff --check`.
