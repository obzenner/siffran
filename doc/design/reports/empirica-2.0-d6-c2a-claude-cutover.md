# D6-C2a — Claude v2-only cutover report

## Slice

C2a — Claude host v2-only cutover (accepted D6-C, §3 serial slice 2; CLAUDE ONLY — the Codex
slice C2 is not accepted until the later Codex slice).

## Outcome

The Claude transport reaches the shared bridge through
``bridge.handle(request, profile_id='claude-code@2.1.270')`` with **no `cwd`** and **exact v2**
correlation. Every retained public request builder produces
`contracts/empirica/v2/request.schema.json`-valid v2 envelopes, validated by `jsonschema` in tests:

- **StartRun** — `actor` removed; explicit max values nested under `budgets` (omitted when
  absent); modes emitted only when resolved.
- **EvaluateRun** — `observed_at` is only `string`/`null`; the numeric wall-clock stamp is removed
  (never fabricated). Omitted when the host event carries no timestamp.
- **ResolveRun / RestoreRun / GetArgument** — exact v2 shapes.
- **dispatch** — only `{kind:"dispatch", target, claim_id?}`; actor/witnessed telemetry is no
  longer public admission.
- **mode → configure_run** — exact `configure_run` with `modes`; the old `mode`/`phase` action is
  removed.
- **child_reserve** — used only when `purpose`, `role_profile`, and `execution` are real host
  inputs; otherwise it fails closed locally. No synthetic capability.

Removed operations — `void_spawn`, `audit_ticket`, `consume` ticket, `phase` — and
author-submitted trusted actions (`evidence_leaf`, `attribution`, `child_event`, `audit_verdict`)
have **no public builder**: they fail closed locally and are never mapped to a semantically
different v2 action or fabricated. The adapter adjudicator (`evidence.py`, importing
`two_fold_verdict`) is deleted, removing the only `ARCH-ADAPTER-ADJUDICATOR` violation in the
Claude path.

All seven hook lifecycle functions remain importable and return honest native
unsupported/fail-closed behavior through the strict v2 shell. At D6 the no-location run port
reports every opaque ID unresolved, so `ResolveRun` returns `unsupported`/closed and no run handle
is resolved: state-bearing gates (Stop/restore/spawn) have no active run to enforce and are inert,
while a real launch missing real inputs fails closed locally. Hooks remain thin (≤12 non-blank
lines, one function each).

## Deleted files

Runtime (legacy evidence/ticket/trusted/migration behavior — no retained production caller):

| File | HEAD LOC |
|-----|---------:|
| `plugins/empirica/adapters/claude/evidence.py` | 127 |
| `plugins/empirica/adapters/claude/knowledge.py` | 228 |
| `plugins/empirica/adapters/claude/audit.py` | 44 |
| `plugins/empirica/adapters/claude/spike.py` | 142 |
| **deleted runtime total** | **541** |

Test fixture (subject deleted):

| File | LOC |
|-----|----:|
| `plugins/empirica/adapters/claude/tests/fixtures/subagent-final.jsonl` | 2 |

(`migrate_legacy.py` was already deleted by C1; its HEAD deletion appears in the C1 ledger.)

## Retained files

Runtime (all rewritten to exact v2):

| File | Current LOC |
|-----|------------:|
| `plugins/empirica/adapters/claude/__init__.py` | 114 |
| `plugins/empirica/adapters/claude/completion.py` | 110 |
| `plugins/empirica/adapters/claude/correlation.py` | 41 |
| `plugins/empirica/adapters/claude/dispatch.py` | 128 |
| `plugins/empirica/adapters/claude/fail_direction.py` | 38 |
| `plugins/empirica/adapters/claude/invocation.py` | 118 |
| `plugins/empirica/adapters/claude/lifecycle.py` | 214 |
| `plugins/empirica/adapters/claude/preflight.py` | 130 |
| `plugins/empirica/adapters/claude/restore.py` | 90 |
| `plugins/empirica/adapters/claude/route.py` | 105 |
| `plugins/empirica/adapters/claude/run_start.py` | 121 |
| `plugins/empirica/adapters/claude/selector.py` | 49 |
| `plugins/empirica/adapters/claude/spawn.py` | 107 |
| `plugins/empirica/adapters/claude/transport.py` | 39 |
| **retained runtime total** | **1,404** |

Tests (rewritten to bounded exact-v2 envelope/profile/correlation/schema/unsupported tests;
compatibility assertions deleted):

| File | Current LOC | Tests |
|-----|------------:|-----:|
| `plugins/empirica/adapters/claude/tests/test_claude_adapter.py` | 473 | 38 |
| `plugins/empirica/adapters/claude/tests/test_activation_lifecycle.py` | 179 | 13 |
| **tests total** | **652** | **51** |

## LOC delta

- Pre-C2a Claude adapter runtime (14 retained files at HEAD + 4 deleted, excluding
  `migrate_legacy.py`): **2,018 LOC** (1,477 retained + 541 deleted).
- Post-C2a Claude adapter runtime (14 files): **1,404 LOC**.
- Retained rewrite delta: **−73 LOC** (1,477 → 1,404).
- Deletion delta: **−541 LOC** (legacy evidence/ticket/trusted/spike modules).
- Total runtime delta for the Claude adapter slice: **−614 LOC**.
- Effective runtime (architecture validator): **7,425 LOC** across 66 files (baseline 9,458,
  maximum 9,457, delta −2,033).

## C2a barrier results

| Check | Result |
|-------|--------|
| `check-claude` (activation lifecycle 13 + adapter 38 = 51) | GREEN |
| strict 44 (`test_d6_strict_v2.py`) | GREEN (44/44) |
| D4 45–47 (`test_protocol_host.py`) | GREEN (3/3) |
| preflight (`v2/__main__.py --preflight`) | GREEN |
| `check-core` | GREEN |
| `check-static` (lint, validate, docs, ADR, contracts, obligations, vendor, activation) | GREEN |
| architecture: `adapters/claude` violations | **0** |
| architecture: effective runtime | 7,425 ≤ 9,457 |
| `git diff --check` | clean |
| index | empty (no staged files) |

## Architecture report (Python Claude, no v1/adjudicator)

`adapters/claude` production Python contains:
- **zero** `empirica/v1` protocol literals;
- **zero** `ARCH-ADAPTER-ADJUDICATOR` references (the `evidence.py` import of `two_fold_verdict`
  is deleted);
- **zero** removed-action literals (`reserve_spawn`, `void_spawn`, `consume_audit_ticket`,
  `audit_ticket`, `phase`, `nonce`, `reservation_id`, `verdicts`);
- **zero** cross-host imports (no `adapters.codex`/`adapters.pi`).

Remaining architecture violations (56 total) are all owned by other slices — Codex
(`ARCH-DEP-PY` cross-host imports, `ARCH-V1-PROTOCOL`), Pi (`ARCH-V1-PROTOCOL`,
`ARCH-FORBIDDEN-SYMBOL`), and application/core (`ARCH-FORBIDDEN-SYMBOL`). No Codex/Pi edits were
made.

**C2 is accepted.** The Codex slice (C2b) closed the remaining `ARCH-DEP-PY` cross-host imports
and `ARCH-V1-PROTOCOL` violations in `adapters/codex`; `check-codex` is green and the architecture
validator reports zero Codex production violations. Combined C2 (C2a + C2b) is accepted.

## Stop conditions

None triggered. No D7 evaluation, D8 child admission, D9 projection, or D10 capability policy was
needed to produce a truthful result. No removed operation was mapped to another v2 action. No
second protocol/state/projector model was added.
