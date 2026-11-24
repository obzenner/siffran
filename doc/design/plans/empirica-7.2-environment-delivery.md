# Delivery plan: Empirica 7.2 run-scoped environment qualification

Spec: `doc/design/specs/empirica-7.2-run-scoped-environment-qualification.md` (r4). Stack prefix `e`; branches
`obzenner/empirica-e<N>-<slug>`; one PR per node; base = previous node in stack order.

## 1. DORA sizing rules

- One concern per PR, reviewable in under 30 minutes: ≤ 400 changed lines excluding generated goldens (S ≤ 150,
  M ≤ 400; anything larger is split before work starts).
- Every PR is green on `make check` plus its named diagnostic on its own branch.
- The new path lands before the old one is removed (E15), so no intermediate branch crashes; each either runs
  spikes or refuses with a closed reason.
- The stack lands on `main` once, through the top release PR (precedent: the a/b stack via #101). Pi installs from
  git `main`, so nothing partial reaches users.
- ADR-0074 is `proposed` from E0, grows with each PR, and is `accepted` in E16 (precedent: ADR-0073).
- `make mutate` runs on the changed lines of every core and transaction node. A reviewer reads every PR before it is
  marked ready.

## 2. Nodes

| Node | Title | Area | Deps | Size | Gate |
|---|---|---|---|---|---|
| E0 | ADR-0074 (proposed) + spec r4 + this plan | docs | – | S | `adr-check docs-check` |
| E0a | `scripts/mutate.py`: run `plugins/empirica` tests with `PYTHONPATH=plugins/empirica:plugins/empirica/tests` (found by E4; mutation gates unusable without it) | scripts | – | S | `scripts` tests, `make mutate` on one core file |
| E1 | Environment proposal model: closed body, bounds, canonical digest, revisions | core (pure) | E0 | S | `check-core`, mutate |
| E2a | `run/5` state schema: environment, history, attempts, `unqualified` backend variant; fixtures, run5 binding test | contracts, core, run_state | E1 | M | `empirica-run5-check contract-check` |
| E2b | `run/4` read-only legacy classification, `run.legacy` restore reason, fresh generation | application, observer | E2a | S | `empirica-run5-check check-observer` |
| E2c | Contract-driven conformance (ADR-0075): schema-derived Hypothesis strategies for `contracts/empirica/v2`, `x-core-validator`/`x-core-only` annotations, generated core bounds module, bidirectional schema⇔core agreement harness, static annotation guard; replaces hand-written mirror tests of E1/E2a and the parity part of `validate_contracts.py` | scripts, contracts, core | E2a fixes | M | `contract-check check-core check-static` |
| E3a | Digest-pinned probe image in the support contract: field, README rule, validator + negative tests, vendored copy | contracts, scripts | E0 | M | `empirica-docker-support-contract-check` |
| E3b | Runtime pin reader, implementation-manifest roots, `make empirica-probe-image-pin` + `scripts/pin_probe_image.py` (fake runner tests) | sandbox, scripts | E3a | M | `empirica-manifest-check`, `check-static` |
| E4 | Authority observation r2: endpoint pinned once, `daemon_id`, loaded-release digest, real postflight re-observation, uncollapsed reasons | sandbox | E0 | M | `empirica-generic-authority-check` |
| E5 | Run-scoped qualification owner: shared runtime lock + journal recovery, trusted probe image, spike-image sealing, deadline, `/3` record write-first, no pointer | sandbox | E3, E4 | M | `empirica-generic-qualification-check`, `-execution-check` |
| E6a | Wire: author actions `environment`, `qualify`; `spike_profile` as ceiling; interim closed refusal `environment.not_available` | contracts, core | E1 | M | `contract-check check-pi` |
| E6b | Wire: §5.1 reasons + hints, optional run-view projections, clauses, host-boundary rows | contracts | E6a | M | `contract-check empirica-docker-support-contract-check` |
| E7 | Governance: proposal admission, deliberative receipts (approve, reject, cancel, stale, supersede, A→B→A), auto derivation, profile ceiling, attempt ceiling | core | E2, E6 | M | `check-core`, mutate |
| E8 | HITL kind `environment` + shared builder (escaping, write-first) | core/hitl, adapters/governance | E7 | S | `check-core check-claude` |
| E9 | Transaction 1: `qualify` attempt reservation, effect via port, binding CAS, orphan handling, StartRun binds null | application, bridge | E2, E5, E7 | M | `empirica-transaction-check`, `bridge-composition-check` |
| E10 | Transaction 2: spike admission against the active binding, sealed image membership, pre/post re-observation, spike/rebind serialization, `superseded-binding` | application | E9 | M | `empirica-transaction-check`, `-harness-check` |
| E11 | Audit and reads: environment in `argument_digest` and `progress_basis`, per-spike lineage, turn-end obligations, author-view next calls, redaction | core, adapters/author_view, audit | E10 | M | `check-core check-evidence` |
| E12 | Pi: environment dialog + `qualify` long-operation transport budget | Pi (TS) | E8, E9 | S | `check-pi` |
| E13 | Claude: environment elicitation + transport budget; Codex refusal test | Claude, Codex | E8, E9 | S | `check-claude check-codex` |
| E17 | Auditor eligibility + `auditor_selection` state + inventory model (pure; `audit_exclusions` stays the owner) | core | E2, E7 | M | `check-core`, mutate |
| E18 | Auditor reservation binding + verdict admission expected-vs-served + re-selection obligations and turn-end | core, application | E17, E11 | M | `check-core check-evidence`, mutate |
| E19 | Auditor wire + HITL: private approval variant with choice key, `auditor` decision kind, `auditor_reselect`, reasons, registries | contracts, core/hitl | E6, E8, E17 | M | `contract-check check-core` |
| E20 | Approval transaction: one CAS for configuration + selection; operator snapshot at StartRun; preference store | application, adapters/state | E19 | M | `check-core`, persistence diagnostic |
| E21 | Pi auditor: snapshot + inventory from `enabledModels`, move the model veto out of pre-StartRun preflight, launch reproduces the target, `/empirica refresh-auditors` | Pi (TS) | E20 | M | `check-pi` |
| E22 | Pi + Claude dialogs/forms for the choice and re-selection | Pi, Claude | E21, E23 | S | `check-pi check-claude` |
| E23 | Claude auditor: pinned-alias/override inventory with family class, launch, served-identity correlation | Claude | E20 | M | `check-claude` |
| E14 | Observer + UI: environment step, attempts, bound/stale/failed, spike lineage, auditor selected/stale/served | observer, ui | E11, E18, E22 | M | `check-observer check-ui`, `ui-look` |
| E15 | Removal + docs: selector, pointer, install target/script, diagnostic-only qualify target, `hosts.md`, `testing.md`, `Makefile:781`, `doc_review.json`, skill and references | sandbox, Makefile, docs, skills | E10, E11 | M | `make check`, `skills-check suites-check` |
| E16 | Release 7.2.0: `make bump PLUGIN=empirica PART=minor`, a 7.2 section in `doc/upgrading-empirica-7.md` naming the two operator-visible breaks, ADR accepted, generated tables, native qualification evidence (A1–A5 + auditor native cases) | release | all | S | `make verify`, `release-check`, operator native qualification |

## 3. DAG and waves

```
E0 ┬ E1 ┬ E2 ───────┬ E7 ┬ E8 ─────────────┬ E12 ┐
   │    └ E6 ───────┘    │                 └ E13 ┤
   ├ E3 ┐                └─────┐                 │
   └ E4 ┴ E5 ──────────────────┴ E9 ─ E10 ─ E11 ┬ E14 ┤
                                          E10,E11 └ E15 ┴ E16
```

| Wave | Parallel workers | Base |
|---|---|---|
| W0 | E0 (coordinator) | origin/main |
| W1 | E1, E3, E4 | E0 |
| W2 | E2, E6, E5 | E1 / E1 / E3+E4 restacked |
| W3 | E7 | E2+E6 restacked |
| W4 | E8, E9 | E7 (E9 also on E5) |
| W5 | E10; then E11 (one worker, sequential) — in parallel E12, E13 | E9 / E8+E9 |
| W5b | E17 → E19 → E20 (one worker), in parallel E18 after E11 | E7, E8 |
| W5c | E21, E23 in parallel, then E22 | E20 |
| W6 | E14, E15 | E11, E18 |
| W7 | E16 (coordinator + operator) | top |

Up to 3 concurrent workers per wave. Linear `gh stack` order:
`E0 E0a E1 E3a E3b E4 E2a E2c E2b E6a E6b E5 E7 E8 E9 E10 E11 E12 E13 E17 E18 E19 E20 E21 E23 E22 E14 E15 E16`.

## 4. Workers and comms

- Run: Orca orchestration `run_d8c8dda7ef46` (already created; the spec review is settled there).
- Per node: `orca worktree create --repo id:cd12c1a2-… --name empirica-e<N> --base-branch <dep tip>`, then
  `orca terminal create --command "pi --model evroc/zai-org/GLM-5.2"`, then
  `orca orchestration worker-start --run run_d8c8dda7ef46 --terminal <handle> --spec "<brief>"`. Orca's
  `agent launch` cannot set a pi model, so the model goes on the pi command line and the Dispatch is attached to
  that terminal.
- The coordinator waits with `orca orchestration check --wait --types worker_done,escalation,question` (no polling),
  answers questions through `reply`, and acknowledges each delivery after processing it.
- Each brief names: the spec sections, the exact files, what is out of scope, the tests to add, the gates to run,
  "do not edit generated goldens" (the coordinator regenerates them at restack), "no push, no Docker", and the
  `worker_done` contract (outcome, files, report path).
- Review per PR: a read-only `openai/gpt-6.2-sol` (medium) terminal under the same Run with a review brief. Blocking
  findings go back to the node's worker before the PR is marked ready.
- One writer per worktree. A settled worker is released after the review is accepted.

## 5. gh stack strategy

1. `gh stack init` from `obzenner/empirica-e0-environment-spec` on `main`.
2. When a node passes review and its commit is approved: `gh stack add obzenner/empirica-e<N>-<slug>` in the stack
   order; parallel branches are rebased into place with `gh stack rebase`.
3. `gh stack submit` opens or updates **draft** PRs, each based on the previous one. The PR body carries the spec
   sections, the ADR delta, the gates run, the reviewer verdict and the mutation survivors.
4. Review bottom-up; a fix goes to the lowest branch that owns it, then `gh stack rebase`.
5. Land: when E16 is green and approved, fold each PR into its base top-down (as #91→#101), so `main` receives
   one merge containing 7.2.0.
6. Nothing is committed, pushed, merged or released without the operator's explicit instruction.

## 6. Contract-driven test migration (after E16)

Operator decision 2026-11-23: tests must not restate contract values; they are generated from the contracts
(ADR-0075). Inside the e stack, E2c introduces the harness and every later node registers its definitions instead of
writing mirror tests. Once 7.2.0 has landed, the rest of the project moves to the same approach as its own stack
(`obzenner/conformance-t*`), one contract family per node, each removing the hand-written tests it replaces:

| Node | Scope | Notes |
|---|---|---|
| T0 | Inventory: a script that lists every test assertion restating a contract value (literal bound, enum, pattern, reason code), by file; the baseline count is the migration's progress metric | read-only; fails nothing yet |
| T1 | `contracts/empirica/v2` remainder: request/response/public-tools/public-contract defs not covered by E2c | largest family |
| T2 | `contracts/empirica-ui-view/v1`: fold `scripts/ui_view_strategies.py` into the shared harness (it is its origin) | removes the duplicate strategy builder |
| T3 | `contracts/empirica-docker-spike/v1`: support contract and limits; production constants generated or harness-checked | replaces the constant-comparison half of the Docker contract check |
| T4 | `contracts/obligations/v1` and `contracts/methodologist/v1` | Methodologist core and MCP adapter |
| T5 | TypeScript side: fast-check arbitraries from the same schemas for the Pi adapters (`adapters/pi/test`, Methodologist Pi) | one arbitrary builder, generated from the schemas |
| T6 | Enforcement: the T0 inventory becomes a failing guard in `check-static` (zero restated contract values outside the named `x-core-only` tests); `doc/testing.md` and the review brief updated | lands last |

## 7. Review and acceptance policy (operator, 2026-11-23)

- Every node is reviewed until accepted, as many rounds as it takes; from E7 on each node registers its own new
  shapes with the conformance harness, so review concentrates on behaviour.
- Integration is additionally accepted by real sessions: a Pi session run inside the Lima VM (`empirica-box`, the
  stack branch checked out in the VM), observed in the Empirica UI served from the VM, once E9 and E12 land (environment proposal, human approval, prepare, `qualify`, spike); the final
  native qualification at E16, also in Lima.

## 8. Operator decisions (recorded 2026-11-22)

1. Workers may commit on their own `obzenner/empirica-e*` branches in their worktrees; no push.
2. Probe image: one digest-pinned reference, no image in the repository.
3. Profile ceiling: `StartRun.spike_profile`.
4. Version 7.2.0 (minor); single landing at the stack top.
5. Auditor model: chosen at configuration approval; Pi candidates from `enabledModels`; last pick remembered per project.
