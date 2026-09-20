# D6-C1 — application/bridge subtraction report

## Slice

C1 — application/bridge subtraction (accepted D6-C, §3 serial slice 1).

## Outcome

The shared bridge now composes ONLY `application.v2`. The D6 shell receives a no-location
read-only run port whose `read(opaque_id)` reports every opaque real-store ID unresolved
without touching any legacy RunKey repository, decoder, mapping/index, or allocator. Valid
state-bearing reads (GetRun/RestoreRun/EvaluateRun) return exact `unsupported`/closed. No v1
fallback, old `EmpiricaService` import, handle decoder, or artifact policy is reached.

`build_service`/`handle` require an explicit exact registry `profile_id`; there is no host
default. Missing/unknown profile on a valid request returns correlated v2 `unavailable`/closed.
Invalid requests are validated by `application.protocol.dispatch_request` and return
`invalid_request`/closed BEFORE profile composition. The stdio `main` routes malformed JSON
through the existing `handle`/protocol gateway (no direct duplicate fault): the non-envelope
fails validation and returns `invalid_request`/closed with request_id `invalid-request`.

The v2 service exposes one intentional PRIVATE validated-handler seam (`_dispatch_validated`)
that calls the existing handler directly, skipping the outer protocol gateway. The bridge uses
this seam (via `protocol.dispatch_request` whose handler lazily `build_service`s then calls
`_dispatch_validated`) so the request and response are validated exactly once by the outer
gateway — eliminating double request/response validation on the known-profile bridge path. The
invalid-before-build/profile behavior is preserved (the handler still raises `ValueError` for a
missing/unknown profile, caught and returned as `unavailable`/closed). Public
`service.dispatch` remains for LiveDriver (it still goes through the full protocol gateway).

`cwd` parameters have been removed from `build_service` and `handle` and all C1 tests; C2 callers
will update to the signature without `cwd`.

## Exact deletions — provenance

Two distinct ledgers apply; do not conflate them. The figure `git diff --numstat` can attest is
the **tracked** HEAD deletion. The **effective** removal includes preexisting uncommitted content
that git HEAD never recorded, so the two ledgers differ by an abandoned +9 LOC.

**git HEAD/diff physical deletion ledger** (tracked trees only; `git diff --numstat HEAD` reports
`0 1484`, `0 379`, `0 218`, `0 305`):

| File | HEAD LOC (deleted) |
|-----|-------------:|
| `plugins/empirica/application/service.py` | 1484 |
| `plugins/empirica/application/state.py` | 379 |
| `plugins/empirica/application/wire.py` | 218 |
| `plugins/empirica/adapters/claude/migrate_legacy.py` | 305 |
| **tracked deletion** | **2386** |

**Effective removal ledger** (pre-C1 working-tree measurement; mandatory deletion ledger, §6):

| File | Effective LOC (pre-C1 working) |
|-----|-------------:|
| `plugins/empirica/application/service.py` | 1493 |
| `plugins/empirica/application/state.py` | 379 |
| `plugins/empirica/application/wire.py` | 218 |
| `plugins/empirica/adapters/claude/migrate_legacy.py` | 305 |
| **effective removed runtime** | **2395** |

The pre-C1 experimental working `service.py` contained **+9 uncommitted LOC** over its HEAD
tracked baseline (1484 HEAD → 1493 effective). Those +9 were preexisting, uncommitted, and
abandoned in C1 — they were never committed to git HEAD, so `git diff --numstat` cannot see or
report them. They are visible **only** in the pre-C1 effective inventory/session measurement (the
D6-B 10,378-LOC measurement and the per-file effective count). Therefore the **1493 figure is an
effective measurement, not a HEAD physical deletion**; the HEAD physical deletion for
`service.py` is **1484**.

Effective removed runtime = **2395** = 2386 (tracked deletion) + 9 (abandoned preexisting
uncommitted). The pre-C1 10,378 → post 7,983 measured delta of 2,395 confirms the effective
ledger. The architecture **tracked** baseline (9,458) → post 7,983 delta is **-1,475** — a
different, tracked-only baseline that excludes the abandoned +9 because that +9 was never
committed to HEAD.

Non-runtime contracts (not counted as repayment):

| File | Physical LOC |
|-----|-------------:|
| `contracts/empirica/v1/request.schema.json` | 113 |
| `contracts/empirica/v1/response.schema.json` | 224 |

Tests deleted (subject deleted):

| File | Physical LOC |
|-----|-------------:|
| `plugins/empirica/tests/test_application.py` | 2043 |

## Exact additions (physical LOC)

| File | Physical LOC |
|-----|-------------:|
| `plugins/empirica/tests/test_bridge_v2.py` | 271 |

## Net runtime delta

- Pre-D6-C measured runtime (D6-B): 10,378 LOC across 74 files.
- Post-D6-C1 measured runtime: **7,983 LOC across 70 files** (delta **-2,395**).
- Architecture baseline: 9,458 LOC. D6-C1 delta from baseline: **-1,475**.
- Architecture maximum: 9,457 LOC. D6-C1 lands **1,474 LOC under budget**.
- Effective gross runtime removal: **2,395** (= 2,386 tracked git deletion + 9 abandoned
  preexisting uncommitted; see *Exact deletions — provenance*). `git diff --numstat` reports the
  tracked **2,386** only; it cannot attest the +9.
- C1 ancillary runtime edits total **0** net (bridge +6, `__init__` -3, v2 `_dispatch_validated` +5,
  convergence -8). With this net-0 ancillary edit, the effective gross removal 2,395 exactly equals
  the pre 10,378 → post 7,983 measured delta of 2,395.

### bridge / `__init__` / convergence / v2 net (exact)

| File | HEAD LOC | Post-C1 LOC | Net |
|------|---------:|------------:|----:|
| `plugins/empirica/adapters/bridge.py` | 133 | 139 | +6 |
| `plugins/empirica/application/__init__.py` | 22 | 19 | -3 |
| `plugins/empirica/application/v2.py` | 163 | 168 | +5 |
| `plugins/empirica/core/convergence.py` | 207 | 199 | -8 |
| **net** | | | **0** |

(`application/v2.py` pre-existed at 163 LOC from the D6-B production seam; C1 added the
`_dispatch_validated` private seam (+5) to reach 168 LOC. With this +5 included, the total
ancillary runtime edit net is 0, so gross mandatory deletion 2,395 exactly equals the pre
10,378 → post 7,983 delta of 2,395.)

## C1 correction (this run)

- **Malformed JSON routing:** `main()` now routes the non-envelope through `handle`/protocol
  gateway instead of emitting a direct duplicate fault. The gateway validates the non-envelope
  (fails) and returns `invalid_request`/closed with request_id `invalid-request`. Test
  `test_stdio_malformed_json_is_invalid_request` asserts the exact request_id.
- **Double validation eliminated:** added `_dispatch_validated` private seam on the v2 service;
  the bridge handler calls it instead of `service.dispatch`, so the known-profile bridge path
  validates request/response exactly once (outer gateway). Regression
  `test_bridge_protocol_gateway_once_handler_once` proves gateway-once/handler-once.
- **`cwd` removal:** removed `cwd` from `build_service`/`handle` signatures and all C1 tests.
- **Test reduction:** removed redundant literal/protocol/import-reload structural tests (already
  covered by architecture/schema); factored one stdio helper (`_stdio`); retained profile,
  invalid precedence, no-location, no-write behavioral coverage. 19 → 17 cases.

## Files changed by C1

Runtime (allowed edits):
- `plugins/empirica/adapters/bridge.py` — rewrote to compose only `application.v2` with a
  no-location run port, explicit exact profile, v1-via-protocol invalid-request precedence;
  malformed JSON routed through `handle`/protocol gateway; `cwd` removed; handler calls
  `_dispatch_validated` (no double validation).
- `plugins/empirica/application/__init__.py` — exports only `compose`, `dispatch_request` (v2).
- `plugins/empirica/application/v2.py` — added private `_dispatch_validated` seam; public
  `dispatch` retained for LiveDriver.
- `plugins/empirica/core/convergence.py` — removed `RunState.is_legacy` field and the
  legacy-without-graph branch.

Lifecycle/build:
- `Makefile` — removed `migrate-legacy` target/PHONY; `check-core` replaces `test_application.py`
  with `test_freshness.py`, `test_observation.py`, `test_d6_strict_v2.py`, `test_bridge_v2.py`.
- `scripts/validate_empirica_activation.py` — removed the `migrate_legacy.py` activation exception.
- `scripts/validate_contracts.py` — v1 fixture validation is structural-only (v1 schemas removed).
- `contracts/README.md` — removed the `empirica/v1` protocol entry.

Tests (disposition per §7):
- `plugins/empirica/tests/test_core.py` — removed the sole `is_legacy` approval test.
- `plugins/empirica/tests/test_state_adapter.py` — removed the `OperationalState` import and the
  `TestPreFixDocumentDecode` compatibility-default class; all repo safety tests retained.
- `plugins/empirica/tests/test_bridge_v2.py` — focused bridge tests (17 cases).

## C1 barrier results

| Check | Result |
|-------|--------|
| strict 44 (`test_d6_strict_v2.py`) | GREEN (44/44) |
| D4 45–47 (`test_protocol_host.py`) | GREEN (3/3) |
| `check-core` | GREEN |
| `check-static` (lint, validate, docs, ADR, contracts, obligations, vendor, activation) | GREEN |
| bridge focused tests (`test_bridge_v2.py`, 17) | GREEN |
| preflight (`v2/__main__.py --preflight`) | GREEN |
| architecture count | 7,983 LOC (<= 9,457) |
| `git diff --check` | clean |
| index | empty (no staged files) |

C1-owned architecture violations eliminated: ARCH-BUDGET (0), ARCH-FORBIDDEN-PATH (0),
ARCH-MAKE-LIFECYCLE (0), ARCH-V1-PROTOCOL in bridge/service/wire/state (0). Remaining violations
(ARCH-ADAPTER-ADJUDICATOR, ARCH-V1-PROTOCOL, ARCH-FORBIDDEN-SYMBOL) are owned by C2/C3 host slices.

## Host suites

Per spec, C1 host suites (check-claude/check-codex/check-pi) may remain red until their owner
slice (C2/C3). They are not part of the C1 barrier.

## Stop conditions

None triggered. No D7 evaluation, D8 child admission, D9 projection, or D10 capability policy was
needed. No second protocol/state/projector model was added. Runtime reached well under 9,457.
