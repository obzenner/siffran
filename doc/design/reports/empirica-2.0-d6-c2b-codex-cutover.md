# D6-C2b — Codex v2-only cutover report

## Slice

C2b — Codex host v2-only cutover (accepted/frozen D6-C spec, §3 serial slice 2; closes the combined C2 slice
after the C2a Claude-only cutover). **Codex only — no Claude, Pi, application, or core edits.**

## Outcome

The Codex transport reaches the shared bridge through
``bridge.handle(request, profile_id='codex-cli@0.146.0')`` with **no `cwd`** and **exact v2**
correlation. The retained public request builders produce
`contracts/empirica/v2/request.schema.json`-valid v2 envelopes, validated by `jsonschema` in tests:

- **StartRun** — `actor` removed; explicit max values nested under `budgets` (omitted when
  absent); modes emitted only when resolved.
- **ResolveRun** — exact v2 selector.

Removed operations — `void_spawn`, `audit_ticket`, `consume` ticket, `phase`, and the old
`reserve_spawn`/`audit_verdict`/`evidence_leaf`/`attribution`/`child_event` trusted-submission
builders — have **no public builder**: they fail closed locally and are never mapped to a
semantically different v2 action or fabricated.

Codex is **observational** (profile `codex-cli@0.146.0`, tier `observational`). Run identity/location
is D7-owned: at D6 the no-location run port reports every opaque ID unresolved, so `ResolveRun`
returns `unsupported`/closed and no run handle is resolved. State-bearing gates therefore have no
active run to enforce and are **inert**. The fail-closed response mappers and active-handle
branches were deleted and are **D7-D10 future work**; hooks are the exact unsupported/inert D6
behavior — not a retained fail-closed mapping.

All four hook lifecycle functions (`activate`, `pre-tool-use`, `stop`, `restore`) remain importable,
but they do not share one unsupported/fail-closed behavior — the distinction is exact: the bridge's
`ResolveRun` returns closed `unsupported` (no run handle resolved); `activate` renders a fail-open
`systemMessage` carrying the fault `code`; `pre-tool-use`, `stop`, and `restore`
(SessionStart:compact) return native inert `None`. Hooks remain thin.

## Deleted files

Runtime (legacy cross-host knowledge re-export — no retained production caller):

| File | HEAD LOC |
|-----|---------:|
| `plugins/empirica/adapters/codex/knowledge.py` | 74 |
| **deleted runtime total** | **74** |

`knowledge.py` was the sole Codex→Claude import (`from adapters.claude import knowledge as _shared`)
and the sole re-export of the deleted graph/research/spike/regate/audit-ticket/verdict/attribution
builders.

## Retained files

Runtime (all rewritten to exact v2; zero `adapters.claude` imports):

| File | Current LOC |
|-----|------------:|
| `plugins/empirica/adapters/codex/__init__.py` | 35 |
| `plugins/empirica/adapters/codex/correlation.py` | 41 |
| `plugins/empirica/adapters/codex/lifecycle.py` | 288 |
| `plugins/empirica/adapters/codex/transport.py` | 39 |
| **retained runtime total** | **403** |

Tests (radically rewritten to bounded exact-profile/schema/correlation/unsupported/official-shape
tests; lifecycle/subprocess/spike/ticket/compatibility assertions deleted):

| File | Current LOC | Tests |
|-----|------------:|-----:|
| `plugins/empirica/adapters/codex/tests/test_codex_adapter.py` | 438 | 31 |
| **tests total** | **438** | **31** |

## LOC delta

- Pre-C2b Codex adapter runtime (4 retained files at HEAD + 1 deleted): **603 LOC**
  (529 retained + 74 deleted).
- Post-C2b Codex adapter runtime (4 files): **403 LOC**.
- Retained rewrite delta: **−126 LOC** (529 → 403).
- Deletion delta: **−74 LOC** (legacy cross-host knowledge re-export).
- Total runtime delta for the Codex adapter slice: **−200 LOC**.

## C2b barrier results

| Check | Result |
|-------|--------|
| `check-codex` (empirica codex 31) | GREEN |
| strict 44 (`test_d6_strict_v2.py`) | GREEN (44/44) |
| D4 45–47 (`test_protocol_host.py`) | GREEN (3/3 under the v2 harness) |
| preflight (`v2/__main__.py --preflight`) | GREEN |
| `check-claude` (activation 13 + adapter 38 = 51) | GREEN (unchanged — no Claude edits) |
| `check-core` | GREEN |
| `check-static` (lint, validate, docs, ADR, contracts, obligations, vendor, activation) | GREEN |
| architecture: `adapters/codex` violations | **0** |
| architecture: effective runtime | 7,225 ≤ 9,457 |
| `git diff --check` | clean |
| index | empty (no staged files) |

## Architecture report (Python Codex, no DEP-PY/V1/removed symbols)

`adapters/codex` production Python contains:
- **zero** `empirica/v1` protocol literals (now `empirica/v2`);
- **zero** `ARCH-DEP-PY` cross-host imports (the `adapters.claude` imports are deleted);
- **zero** removed-action literals (`reserve_spawn`, `void_spawn`, `consume_audit_ticket`,
  `audit_ticket`, `phase`, `nonce`, `reservation_id`, `verdicts`);
- **zero** `ARCH-ADAPTER-ADJUDICATOR` references (never present in the Codex path).

Architecture validator reports effective runtime of **7,225 LOC across 65 files** (baseline 9,458,
maximum 9,457, delta −2,233). Remaining architecture violations are all owned by other slices — Pi
(`ARCH-FORBIDDEN-SYMBOL`, `ARCH-V1-PROTOCOL`) and application/core (`ARCH-FORBIDDEN-SYMBOL`).
No Pi/Claude/application/core edits were made.

## Combined C2 (C2a + C2b) — implementation complete, pending independent acceptance

C2a (Claude v2-only) and C2b (Codex v2-only) together close the combined C2 slice. Both host
adapters now reach the shared bridge with their fixed exact registry profile and no `cwd`,
emit only schema-valid `empirica/v2` requests with exact v2 correlation. Both use schema-valid
v2/exact profiles; Claude has its documented native mappings; the Codex bridge closes unsupported,
but the native activation renders a fail-open message and the other hooks are inert. Removed
operations fail locally or are absent, never semantic remap. Production scans show no `ARCH-DEP-PY` cross-host imports, `ARCH-V1-PROTOCOL`
literals, or removed-action symbols in either Python host path.

Combined C2 implementation is complete. D6-C final acceptance is reserved for C4; this report
does not self-declare C2 or D6-C accepted.

## Stop conditions

None triggered. No D7 evaluation, D8 child admission, D9 projection, or D10 capability policy was
needed to produce a truthful result. No removed operation was mapped to another v2 action. No
second protocol/state/projector model was added. No Pi edits, staging, commits, subagents, or
outside search were performed.
