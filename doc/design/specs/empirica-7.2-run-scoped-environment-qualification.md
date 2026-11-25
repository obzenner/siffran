# Empirica 7.2: run-scoped environment preparation and Docker qualification

Status: **r4**: operator decisions applied, auditor review folded in (ADR-0074 proposed; ADR-0060 amended). r2 folds in the independent review
`.review/spec-review-gpt-6.2-sol.md` (findings cited as R<n>). On acceptance it becomes ADR-0074, amending ADR-0071
(spike authority), ADR-0063/0072 (approval, HITL document) and ADR-0073 (auto mode).

## 1. Problem

- A run's Docker authority is chosen once, when `StartRun` creates a **new run generation** (an active valid run is
  reused), by `GenericBackendSelector.select` (`adapters/sandbox/generic_authority.py:257-269`). It checks the private
  scaffolding and empty profile config and reads the machine-wide pointer and record; it neither contacts the daemon
  nor compares the record's `implementation_digest` with the running code (R11). A run can therefore be labelled
  `qualified` against a record that cannot be used.
- Reported incident 2026-11-22 (operator session, not a repository source): pointer → record `9452a5f5…`, created
  Nov 5 with `implementation_digest 4023d3…`; the installed plugin computes `e8b63e…`; every spike was refused.
- The real checks run at installation and at each spike (`GenericAuthorityResolver.resolve`, `:383-408`), after
  configuration and research. On the spike path the image resolver collapses every cause to `authority-drift`
  (`:434-439`, R12), so agents misdiagnose it.
- Getting a usable environment is an operator procedure outside the run (qualify, install, new run). When the work
  starts with "get Docker and the image ready", the order is backwards. A mid-run change (another image, the
  networked profile, a Docker restart, a plugin upgrade) forces the run to be abandoned.
- Qualification requires `wget` and other probe tools in the qualification image, for both profiles
  (`generic_qualification.py:34-37`). Operators reach for the spike image (`python:3.12-slim` has no `wget`, observed
  in the same session), which fails for a reason unrelated to the spikes.

## 2. Goals and non-goals

Goals

- G1. An explicit, ordered environment step inside the run: the agent prepares Docker and images, then the **host**
  qualifies the daemon, the running code and the images, before any spike.
- G2. Deliberative mode: the human approves the agent's environment proposal before the host will qualify it. Auto:
  approval is derived by the core, and the step needs no human.
- G3. A run can requalify whenever it needs an environment change. Every spike keeps the qualification it ran under.
- G4. No machine-wide pointer. A qualification belongs to the run generation that made it.
- G5. Truthful reads: never `qualified` unless this run bound a qualification that is still the active one. Every
  refusal names its closed cause and, only when admissible, the next call.

Non-goals

- The agent gets no Docker authority: no options, mounts, network choice beyond the profile, resources or cleanup.
- Runtime still never pulls, builds, loads, logs in, or starts/stops Docker. **Preparation is the agent's own work
  with its ordinary tools, is never evidence, and is rendered as unverified.**
- Honest limit (R1): Empirica gates `qualify` and spikes; it cannot prove that an unrestricted shell did no
  preparation before consent. Deliberative consent governs what the host will *bind*, not what the agent typed.
- Same-user shell access to the adapter, store or daemon is not isolation (`doc/hosts.md` trust boundary, R3).
- Multi-container, remote engines, Podman and cross-architecture images stay deferred.

## 3. Invariants

- I1. Spike evidence rests only on host observations. The probe workload is host-trusted (§4.3); the author chooses
  only spike workload images.
- I2. Authority changes only by a CAS-published binding after a successful host qualification of an approved
  proposal. Approval or preparation alone never changes it (R4).
- I3. Each spike re-verifies the active binding before its effects and again before admitting its result (R5).
- I4. Exact-ID cleanup, journals and per-endpoint recovery cover qualifications as well as spikes (R6).
- I5. Human approvals bind an immutable core-owned HITL document through ADR-0072's write-first receipt (R2).
- I6. A qualification is audit context, never claim evidence (R7).

## 4. Model

### 4.1 Environment proposal `empirica.environment-proposal/1` (core-owned, closed, canonical)

| Field | Bound |
|---|---|
| `profile_id` | `docker-isolated-v1` / `docker-networked-v1`, and within the host profile ceiling (§4.4) |
| `spike_images` | 1..16 local references, each ≤ 255 chars, same grammar as the spike draft `image` |
| `preparation` | 1..8 sentences, each ≤ 200 chars; shown as the agent's unverified plan |
| `rationale` | 1..600 chars, nonblank |

There is no author-chosen probe image (R3). Free text is escaped by the HITL renderer and must never contain
secrets. Each submission gets a `revision` (monotonic per run). `digest` is the canonical digest of
`{"schema": "empirica.environment-proposal/1", …canonical body}`, so the schema id separates it from every other digest
domain (decided at the E1 review). All free text must be valid UTF-8 (no lone surrogates).

### 4.2 State `empirica.run/5` (R9: one active reference, immutable history, everything else derived)

- `environment`: `null` | `{revision, proposal, digest, state: pending|approved|rejected, receipt_id|null}`. Earlier
  revisions and their receipts are kept immutable in `environment_history`.
- `qualification_attempts`: append-only `{attempt_id, revision, started_stamp, outcome, record_digest|null,
  reason|null}`. Ceiling: 32 started attempts per run (operator decision 2026-11-23, drift check item 4: the ceiling
  only bounds run-state growth; with images resolved per spike and only the major version bound, requalifying is rare).
- The active reference **is the existing `spike_backend` field** (decided while briefing E2; no second field): kinds
  `unqualified` (new) | `qualified` (unchanged shape, its record digest now a `/3` run-scoped record) |
  `spikes_unavailable` (no generic backend composed). The attempt sequence number is derived from the attempts.
- `StartRun` binds `spike_backend = {kind: "unqualified"}` (from E9; until then the selector is unchanged). A `run/4` document is **read-only legacy**: never resumed or
  rewritten; `RestoreRun` reports `run.legacy` and the next call starts a fresh generation (R13, R19). This is new
  behaviour; ADR-0072 classified old state as corrupt.

### 4.3 Host-trusted probe image: digest pinning (R3)

Pattern: **OCI digest pinning**, the one Docker documents ("pin the image to a digest") and that Renovate's
`pinDigests` and Dependabot's docker ecosystem maintain: a `name:tag@sha256:<index digest>` reference. The index
digest is a multi-platform OCI image index, so one reference covers `linux/arm64` and `linux/amd64`, and no image
bytes live in the repository.

- The support contract `contracts/empirica-docker-spike/v1` carries exactly one `probe_image` reference, for example
  `docker.io/library/alpine:3.22@sha256:49340b34…e898f`. The contract digest is part of the implementation manifest.
- During preparation the agent runs `docker pull <that reference>`; the host still never pulls.
- Admission: under the pinned endpoint, one `docker image inspect` of the exact reference must show that index
  digest in some `RepoDigests` entry (`<any repository>@sha256:<pinned digest>`: the digest is the content identity,
  the repository name is not, so a mirror or proxy pull is admitted), a server-native platform and no declared
  volumes. Otherwise `probe-image-untrusted`, with the reference rendered so the agent can pull it. A locally built,
  loaded or retagged image has no `RepoDigests` entry with the pinned digest and is refused (operator decision
  2026-11-23, drift check item 3).
- Observed 2026-11-22, Docker Desktop with the containerd store, arm64: after `docker pull alpine:3.22`,
  `RepoDigests == ["alpine@sha256:49340b…"]` and `Id` equals the index digest. The classic store reports the
  same `RepoDigests`, but its `Id` is the platform config digest. So admission keys on `RepoDigests`, never on
  `Id`, and the sealed record stores both.
- Refresh: `make empirica-probe-image-pin TAG=<tag>` (developer tool; resolves the tag's index digest with
  `docker buildx imagetools inspect` and rewrites the contract). A pin change is a code change and therefore
  invalidates bindings, like any other implementation change.

### 4.4 Profile ceiling (R3, R8)

`StartRun.spike_profile` is kept on the wire and **redefined as the host's profile ceiling**: absent or
`docker-isolated-v1` allows only isolated; `docker-networked-v1` allows both. This is an explicit wire
reinterpretation, recorded in the contract. Deliberative documents call out the networked profile; delegated auto may
use networked only when the ceiling allows it.

### 4.5 Qualification record `siffran.empirica.docker-spike-qualification-record/3`

The `/2` facts plus `run_binding` (`project_id`, `run_id`, `generation`, `attempt_id`, `revision`,
`proposal_digest`), `probe_image_id`, `spike_images` (requested ref, the full ID observed at qualification as a
diagnostic, canonical platform, no volumes; no capability list, since spike images are inspected, never probed),
`daemon_id` (Docker `info.ID` when present), and the loaded-release implementation digest. Written durably and
content-addressed under `$EMPIRICA_SANDBOX_HOME/docker-spike-qualification-v3/records/` **before** the binding CAS.
On load, the exact digest and the run-generation lineage are verified. An orphan left by a lost CAS is inert. There
is no pointer namespace; `/2` records and pointers are ignored.

### 4.6 Auditor runtime selection (r4: review `.review/spec-review-auditor-gpt-6.2-sol.md`, findings AR<n>)

The auditor model is part of the run's environment. **No public author action supplies a model, candidate list,
preference, choice key or receipt** (ADR-0060; AR2, AR11).

- **Operator policy snapshot (AR2).** At `StartRun` the host freezes, for the run generation, the operator inputs
  that can affect selection:
  - Pi: the expanded `enabledModels` and the auditor override (`agentOverrides["empirica.empirica-auditor"].model`).
  - Claude: `CLAUDE_CODE_SUBAGENT_MODEL` and the reviewer alias pins.

  Edits to settings made later through ordinary tools do not affect the active run. A live registry check may remove
  a candidate (unavailable, credentials lost) but never add one. A policy change needs a fresh run, or an explicit
  trusted host refresh (Pi `/empirica refresh-auditors`, which is operator-typed and never a tool call). Residual:
  the same user can edit settings before `StartRun`.
- **Inventory (AR1, AR8).** From the snapshot, the host builds a bounded (≤ 16), ordered list
  `{key, launch_target, target_fingerprint, expected_class, eligible, excluded_by}`. `key` is opaque and per
  snapshot. `excluded_by` comes only from `core.evaluation.audit_exclusions`, the sole owner of the launch set. The
  list holds no credentials, paths or provider-private configuration.
  - Pi offers only concrete available matches of `enabledModels`. An empty or missing `enabledModels` gives an
    inventory of the resolved default alone (override, then `subagents.defaultModel`, then the main model). If that
    default is excluded, the result is `auditor-selection-required`, with no candidate and a hint to set
    `enabledModels`.
  - Claude: candidates are the pinned reviewer aliases and a concrete `CLAUDE_CODE_SUBAGENT_MODEL`. Each candidate's
    `expected_class` is its model family, which is the class Claude's identity check can already observe
    (`_effective_identity`). An alias whose family cannot be established is not offered (closed refusal; this is a
    compatibility change for unpinned aliases, noted in the upgrade section). `inherit` is not a candidate.
- **Override (AR3).** An operator override is a hard rule: if it is eligible, it is the only selectable entry. If it
  is excluded or unavailable, the result is a closed refusal (`auditor-override-excluded`). It is never replaced.
- **Default without an override.** The latest accepted human pick for this project and host, if it is in the
  inventory and eligible; otherwise the first eligible entry in host order.
- **Initial choice (AR1, AR3).** It is made in the configuration approval: the write-first configuration document
  lists the inventory and the form carries one choice field over the eligible keys. The receipt binds run
  generation, configuration revision, snapshot digest, document digest, presentation and key. At approval the core
  revalidates eligibility and commits the configuration approval and `auditor_selection` in **one CAS**. An inventory
  change while the dialog is open, cancel, reject or a stale replay grants nothing. Later budget-only amendments keep
  the selection and never re-pick.
  - Interactive auto: the same single human configuration episode.
  - Delegated Pi (no form): the core derives the default deterministically; with none eligible it blocks with a
    closed reason.
  - Claude `-p`: nondelegated cannot approve. Delegated stays documented as broken under default permissions; this
    feature does not change that.
- **Remembered preference (AR2).** `$EMPIRICA_HOME/projects/<project>/auditor-preference.json` holds
  `{host, launch_target, chosen_at}`. It is a lookup hint only: re-matched against the snapshot and live
  eligibility, never a candidate source.
  - Written only after a successful human selection commit, never on a replay or an auto decision. A write failure
    is a diagnostic and does not roll back the approval.
  - Missing, corrupt or other-host entries are ignored. Concurrent writes are atomic, and the last writer wins.
- **State (AR4).** `run/5` holds `auditor_selection: {revision, snapshot_digest, key, launch_target,
  target_fingerprint, expected_class, chosen_by: human|auto|override, receipt_id|null, status:
  valid|excluded|unavailable}`, plus immutable history.
- **Launch and verdict (AR4, AR10; new behaviour).**
  - The audit reservation CAS binds the selection revision, launch target, fingerprint and expected class to the
    audit operation, after re-checking the current exclusions. It spends no spawn on refusal.
  - Adapter preflight must reproduce the same target and the existing capability envelope: canonical auditor, fresh
    context, read-only tools, and the Pi retrieval admitted at `StartRun`, never a wider one.
  - A verdict is admissible only if its correlated, observed served identity matches the reserved `expected_class`
    and satisfies the unchanged per-artifact independence rule. Substitution, even by another independent class, is
    refused. Equivalent spellings of the same provider/model are normalized, not treated as substitution.
- **Re-selection (AR5).**
  - **When it is recomputed:** after an author refresh, after evidence, confirmation or freshness changes, and at
    launch, eligibility is recomputed against the current author and covered producers.
  - **Invalid selection:** an invalid selection stays in history; it blocks a new reservation; and the run renders
    `auditor_reselect` only when that is admissible.
  - **Who decides:** in deliberative mode, a bounded private decision kind `auditor` (≤ 4 per run) with the same
    receipt rules. In auto, the deterministic default; when nothing is eligible, auto settles honestly with
    `auditor-unavailable` rather than looping.
  - **Pending audits:** re-selection waits for a pending audit or makes it stale.
  - **Existing verdicts:** valid verdicts are not rewritten. The selection revision is bound per audit operation and
    kept out of the live argument digest.
  - **Limits:** no pick can cure mixed or unverified producers.
- **Reasons (additive).** `auditor-selection-required`, `auditor-selection-excluded`, `auditor-override-excluded`,
  `auditor-unavailable`, `auditor-served-mismatch`; the existing `audit.auditor_excluded` is reused at reservation.
- **Pi preflight (AR9).** Pi's current pre-`StartRun` veto on the auditor *model* moves to selection. The owner,
  package and capability checks stay before `StartRun`.

## 5. Lifecycle

```
route → graph → configure_run (approval) → investigate → … research …
  → environment (proposal r) → [deliberative: HITL approve | auto: derived] → agent prepares (unverified)
  → qualify (host attempt) → bound → spike_request …
  later: environment (r+1) → approve → prepare → qualify → rebind (old binding stays active until then)
```

- **Order (R1, §11.2 answer).** Environment comes after configuration approval and investigation admission. It is
  needed only before the first `spike_request`. Configuration and environment are never pending at the same time.
- **`environment`** records a new revision. Deliberative: `pending`, then a HITL document kind `environment`; approve
  or reject comes through the private receipt bound to run generation, revision, presentation ID and document digest.
  Cancel, timeout, missing surface or a stale reply leave it `pending`. Reject or revise closes that presentation
  without approving. A new revision while one is pending supersedes it. An A→B→A sequence is three revisions, so
  approving A once does not approve its replay. Auto: the core derives approval under the existing configuration
  or delegation provenance (`governance.expected_approval_kind`); it fabricates no human receipt.
- **`qualify`** (author request; host effect), admissible only for the latest **approved** revision with attempts
  remaining:
  1. Reserve the attempt durably (charged even if it then fails or is interrupted; not charged for invalid ingress
     or an exact replay).
  2. Under the shared runtime lock: recover prior journals; resolve the endpoint once and pin every read and effect
     to it.
  3. Observe authority (context, version, info including `daemon_id`, capability) and the loaded implementation;
     verify the trusted probe image; resolve every `spike_images` reference and record its ID as a diagnostic
     (spikes resolve their image again per spike).
  4. Run the five fixed probes through the journalled production lifecycle, then prove exact cleanup.
  5. Re-observe the authority and implementation (replacing today's constant postflight, R5).
  6. Write the `/3` record, then CAS `spike_backend` against the exact run generation, approved revision and
     prior binding. If the CAS loses (stop, newer revision, concurrent attempt), the record is orphaned and nothing
     binds.
  - Deadline: one host bound covering the lock wait, recovery, up to 16 inspections, probes, cleanup and publication
    (proposal: 150 s). The Pi and Claude transport budgets add `qualify` to the long-operation class (R6). A
    killed or timed-out attempt binds nothing and leaves recoverable journals.
  - Failure never changes the active binding.
- **Spikes.** Admissible only with an active binding. The plan's image reference must be one of the approved
  revision's `spike_images` references; it is resolved per spike under the same pinned endpoint and checked exactly as
  in 7.1 (local, server-native platform, no declared volumes), and creation is by the ID resolved for that spike with
  `--pull never`, which the spike's sealed request records. Rebuilding a listed image needs no new proposal or
  qualification (operator decision 2026-11-23, drift check item 1). Authority and implementation are
  re-observed before effects and before admitting the result. Spike effects and a rebind are serialized per run. A
  spike request left uncompleted when the binding changes ends with an explicit terminal `superseded-binding`
  outcome, so a retry under the new binding is not blocked (`transaction.py:742-758,806-816`, R4).
- **Server version (operator decision 2026-11-23, drift check item 2).** Within one qualification attempt, pre- and
  post-probe `server_version` must be equal (identity within one attempt). Across the binding only the Docker
  server's **major** version is bound: a patch or minor update keeps the binding; a major change is
  `qualification-stale-daemon`. The full version is kept as a diagnostic, as in `/2`.
- **Deadline (E4 review).** The 60 s operator qualification budget is kept as is until native timing evidence exists.
  The run-scoped `qualify` deadline (proposed 150 s) is set in E5 from that evidence, explicitly budgeting recovery,
  five probes with their re-observations, final re-observation and publication.
- **Timing evidence (operator session 2026-11-23).** E5 at `fbd7b604`, both profiles, real Docker, temporary sandbox
  home, no leftover journals. All bound runs were on **Docker Desktop 29.7.2** (the authority is the selected Docker
  context; `DOCKER_HOST` is not consulted, so runs started with it pointing at Lima still qualified Desktop, as the
  records' `daemon_id` shows): isolated 17.1–18.5 s, networked 15.1–15.8 s over five attempts each. About 12 s is the two
  deliberately waiting probes (timeout, and isolated egress refusal); every other stage is under 1.5 s. Journal recovery
  was not exercised. Lima (`empirica-box`) is used only to run Empirica inside the VM, where the host is Linux and the VM's native
  Docker Engine is admitted; pointing a macOS process at the VM's daemon is refused (only Docker Desktop is admitted on
  macOS), which surfaced a misleading `docker-unreachable` reason, fixed in E5. Deadline: **90 s** (five times the worst measured attempt).
- **Residual (R5).** Equality across a binding is semantic: the socket path, `daemon_id`, the server **major** version
  and capabilities (the full version is compared only within one attempt). A daemon restart
  on the same socket with the same identity is not detectable and is accepted as the same authority.

### 5.1 Closed reasons (additive values; only bounded identifiers such as a probe enum or image index)

| Reason | Meaning | Next call (rendered only when admissible) |
|---|---|---|
| `environment-required` | spike requested, no binding and no approved revision | `environment` |
| `environment-pending` | awaiting the human | none (the human acts) |
| `environment-rejected` | latest revision rejected | `environment` |
| `qualification-required` | approved, nothing bound | `qualify` |
| `qualification-stale-implementation` | loaded release ≠ binding | `qualify` |
| `qualification-stale-daemon` | endpoint, `daemon_id`, server major version or capability ≠ binding | prepare, then `qualify` |
| `image-not-qualified` | spike image reference not in the approved revision | `environment` |
| `probe-image-untrusted` | local probe image ≠ pin | prepare (pull the pinned ref), then `qualify` |
| `qualification-probe-failed` | carries `probe` ∈ the five names | prepare, then `qualify` |
| `docker-unreachable` | no daemon at the endpoint | prepare, then `qualify` |
| `profile-not-allowed` | profile above the host ceiling | `environment` |
| `qualification-attempts-exhausted` | 32 attempts used | continue on the current binding, stop, or start fresh |
| `qualification-conflict` | lost CAS or concurrent attempt | `qualify` |
| `superseded-binding` | spike request outlived its binding | resubmit the `spike_request` |

The existing `authority-drift`, deadline, egress and cleanup reasons are kept and no longer collapsed (R12).

**Wire and persistence of the probe name (E6b review).** `Fault` gains one optional closed member `probe`, whose
values are the contract-owned probe-name enumeration (the five names). The response schema makes it *required* when
`reason` is `qualification-probe-failed` and *forbidden* for every other reason. The run/5 attempt row carries the
same member (`probe`: a probe name, or `null` unless its `failure_reason` is `qualification-probe-failed`), so a later
read reproduces it without message prose. The read projection `qualification.last_failure.reason` takes its values
from the full closed set of qualification failures in this table plus the kept deadline, egress and cleanup reasons,
never from a shorter list, and never a reason encoded into a string.

**The active binding in reads (E6b review).** `qualification.active` is the installed binding reference (the last
successful attempt's record digest and seq), not an in-flight attempt: it stays set while a new attempt runs or
fails, and is `null` only when nothing was ever bound in this run. `attempts_used`, `attempts_max` and `active.seq`
take their bound from the one run ceiling owner (`qualification_attempts`, 32), through the shared/generated bound
(ADR-0075), never a second literal.

## 6. Human in the loop

- HITL `empirica.hitl/1` gains the additive kind `environment`: goal, profile (networked called out), spike images
  with the IDs observed at qualification once known, preparation labelled "the agent's plan — not verified", rationale, previous
  active binding.
- Pi: dialog via the shared builder. Claude Code: elicitation form referencing the document path (ADR-0072).
  Codex: withheld; `qualify` is refused as unsupported unless a host qualification port is composed (R10). There is no
  public approval escape hatch.
- Auto (interactive and delegated): no form; the receipt kind is `auto`, derived as in governance today.

## 7. Audit and reads (R7)

- The reviewed argument gains `environment` context: the active binding, every attempt with its outcome, and for
  each spike request/result the qualification it was sealed under. These facts go into `argument_digest`
  (`projection.py:618-631`) and `progress_basis` (`evaluation.py:312-348`), so a rebind invalidates a stale audit.
- Historical results stay active under the existing staleness rules and are resolved against their own qualification
  (the spike-request **artifact** carries the backend, not `SealedSpikeRequest`, R14).
- Environment obligations count in the turn-end signal (material progress, whose turn it is).
- Reads distinguish a historical **bound** binding from the latest **stale** or **failed** observation, and never
  imply live health from a stored digest. They redact paths, endpoint, cleanup IDs, raw diagnostics and secrets.

## 8. Contracts and versions (R8)

- `empirica/v2` request: additive actions `environment`, `qualify` and `auditor_reselect` (model-free); a new private
  approval variant carrying the auditor choice key, versus a new required member in the existing one (AR6); private host
  inventory and audit-operation binding; `StartRun.spike_profile` reinterpreted as
  the ceiling (§4.4, explicitly documented).
- Response: the existing `qualified` backend variant keeps its shape. It adds a closed variant `unqualified`, optional
  `environment` / `qualification` projections, and the additive reasons.
- Inventory updated in one place per node: request/response/public-tools schemas, HITL, state, observation, tool and
  host registries (`public-tools.json`, `host-boundaries.json` EHB/HX rows), vendored copies, generated goldens,
  `contracts/empirica-docker-spike/v1` (qualification and probe-image pins), `doc/hosts.md`, and `Makefile:781`.
- State `run/4 → run/5`; UI view `empirica-ui-view/v1` additive fields; plugin `empirica` **7.2.0** (operator
  decision: minor). The minor release still carries two operator-visible breaks, and the release notes and
  `doc/upgrading-empirica-7.md` (a new "7.2" section) must state both: open `run/4` runs become read-only legacy,
  and `make generic-docker-install-candidate` is removed.

## 9. Removed

- `GenericBackendSelector`, pointer reader and schema, `make generic-docker-install-candidate` and
  `scripts/generic_docker_install.py`.
- `make generic-docker-qualify-candidate` stays only as a non-binding operator diagnostic wrapping the same probe
  kernel.

## 10. Acceptance

Live (operator, native qualification):

- A1. Configured run; environment approved; Docker stopped → `docker-unreachable`; the agent starts Docker →
  `qualify` binds → spike runs. Preparation is not evidence anywhere.
- A2. Using two compatible 7.2 builds: bound under build 1; upgrade; next spike → `qualification-stale-implementation`;
  `qualify` → spike runs. The audit argument shows both qualifications and each spike's lineage.
- A3. Deliberative: `qualify` is refused before approval; reject keeps the old binding; cancel leaves it pending.
- A4. Auto (interactive and delegated): no prompt after configuration; receipt kind `auto`.
- A5. `python:3.12-slim` spike image plus the pinned probe image qualify together; an unlisted image reference →
  `image-not-qualified`; rebuilding a listed image and spiking again needs no new qualification.

Deterministic (no Docker): stale receipt or revision; A→B→A; failed replacement keeps the old binding; concurrent
attempts; spike versus rebind; retagging between qualify and spike; authority drift during probes; a crash at the
reservation, record and CAS boundaries; old-endpoint recovery; transport timeout; legacy `/4` restore; attempt
exhaustion; Codex refusal; profile ceiling. Auditor: forged choice, stale/ABA inventory and confirmation, empty or
missing `enabledModels`, excluded override, excluded remembered pick, policy or pin change mid-run, model removed
from `enabledModels`, same class with different IDs, substitution by a distinct class, concurrent re-selection versus
reservation, retention of an old valid verdict. Native: Pi and Claude choice → launch → observed verdict; delegated Pi
with no UI; Claude `-p` refusal as a documented limitation.

## 11. Decisions

1. Attempt ceiling: a separate limit of 32 **started** attempts, not `max_passes`.
2. No combined presentation: configuration first, then environment when needed.
3. Probe image: one digest-pinned reference in the support contract; no image in the repository (§4.3).
4. Profile ceiling: the existing `StartRun.spike_profile` (§4.4).
6. Auditor model: chosen at configuration approval from host candidates (Pi: `enabledModels`), with the last pick
   remembered per project as the default (§4.6).
7. An operator override is a hard rule: the sole choice when eligible, a closed refusal otherwise (ADR-0060 already
   says an override "is never replaced").
8. Operator settings are snapshotted per run at `StartRun`; `/empirica refresh-auditors` is operator-typed only.
9. Claude aliases without an established family are not offered; this is noted in the upgrade notes.
5. Version: 7.2.0, landed once at the top of the stack.
