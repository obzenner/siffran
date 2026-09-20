# D6-C3 — Pi v2 cutover report

## Slice

C3 — Pi host v2-only cutover (Terra/Sol correction) plus the C3 cleanup. The
Pi adapter's exact conservative host profile, the `report_convergence` hard
gate semantics, the subagent local fail-closed, the truthful UX, the
canonical-required-exact lexical projection (TS optional subset, no TS extras),
and the parity checks are corrected to the D6 v2 target. No app/core/Python-host
edits.

## Outcome

The Pi adapter is v2-only over `pi@0.84.1`. The `report_convergence` hard gate
permits only a guarded `Allow` when a nonnull handle exists; everything else
denies. The Allow cross invariant is enforced in the central guard. Executable
subagent launches are denied locally (D8-owned). The UX is truthful. The stale
v1 README is replaced. Parity validates every built request against the canonical
`request.schema.json` via Python `jsonschema` plus a canonical-required-exact
lexical/type partition check (TS optional subset, no TS extras) — the TS
projection cannot omit a canonical required field.

## Changes

### Production source — current six runtime TS totals

| File | Change | Current LOC |
|------|--------|------------:|
| `src/contract.ts` | Reduced v2 boundary projection (lexical-projection target); `BlockReason.message?: string` declared (guard asserts shape) | 151 |
| `src/guard.ts` | **Added** by C3: Allow cross invariant (`converged` true iff `status=converged`; false only active/stopped_*); per-reason shape validation for Block (object, nonempty string code, optional string message) and Fault optional message | 195 |
| `src/index.ts` | `/empirica` description exact (`Attempt StartRun for the current goal (D6 strict shell currently returns unsupported).`); header likewise; uses `startRunNotice`; `tool_call` denies executable subagent launches locally | 264 |
| `src/pi-types.ts` | Pi extension API surface types | 97 |
| `src/stdio-transport.ts` | `HOST_PROFILE_ID` → `pi@0.84.1` (conservative, no capability detection) | 162 |
| `src/translate.ts` | `gateFromDecision` denies Inert and all Fault (open+closed); `startRunNotice`; `isExecutableSubagentLaunch`; `subagentUnsupportedReason` | 254 |
| `README.md` | Replaced stale v1 content; `/empirica` row reads `Attempts StartRun; D6 strict shell returns unsupported.` | — |

**Runtime totals:** pre 1146 → current 1123 (six files: 151 + 195 + 264 + 97 + 162
+ 254) → **delta -23**. `guard.ts` is the C3 addition (195 current);
`obligations.ts` (60) is the C3 runtime deletion. The -23 delta is entirely Pi
TS: C3 touched no app/core/Python host. (guard.ts +20 and contract.ts +2 over
the original C3 cut added the Block/Fault per-reason shape validation.)

Nonruntime metadata — `README.md` (replaced stale v1 content; `/empirica` row
reads `Attempts StartRun; D6 strict shell returns unsupported.`) and `package.json`
(description retargeted to the empirica/v2 contract, dropping `/empirica-status`
and the `report-convergence` command; ADR-32 → D6-C) — are excluded from the
runtime totals above.

### Deletions — exact six (C3-owned, not prior-slice)

| File | LOC | Kind |
|------|----:|------|
| `src/obligations.ts` | 60 | runtime |
| `test/audit.test.ts` | 192 | test |
| `test/lifecycle.test.ts` | 89 | test |
| `test/nudge.test.ts` | 89 | test |
| `test/obligations.test.ts` | 14 | test |
| `test/fixtures/get-argument-response.json` | 21 | fixture (874 B) |

These six were deleted by C3 (the v1 obligations contract, the audit/lifecycle/nudge
test surfaces, and the v1 argument fixture). They are not C1/C2 carry-overs.

### Tests — current per-file LOC and actual counts

| File | Change | Current LOC | Tests |
|------|--------|------------:|------:|
| `test/fakes.ts` | Test doubles for the Pi ExtensionAPI surface (FakePi/FakeUi/fakeCtx) | 90 | — |
| `test/gate.test.ts` | +3 direct tool regressions (Block/Inert/openFault execute), +status, +compaction (moved from parity; reuse `wire()`/`startRun()`, deduplicated setup); +1 malformed-Block-reason fails-closed gate regression | 327 | 20 |
| `test/guard.test.ts` | **Added** by C3: cross-invariant rejections, valid-status, Inert, subagent matrix; +Block per-reason shape mutations (null, {}, numeric/empty code, numeric message) +Fault numeric message +valid Block-with-message | 118 | 45 |
| `test/live-bridge.test.ts` | Profile → `pi@0.84.1`; StartRun asserts unsupported/closed; raw v1 asserts invalid_request/closed | 118 | 5 |
| `test/parity.test.ts` | Compact lexical projection (canonical-required-exact partition, TS optional subset, no TS extras) + one builder-table schema validation; integration tests moved out | 186 | 7 |
| `test/registration.test.ts` | Asserts removed v1 surfaces absent (no `/empirica-status`, `/report-convergence` commands, no `empirica_knowledge` tool) | 94 | 9 |
| `test/translate.test.ts` | Notices/classifier table-driven compactly; gate decisions table-driven | 171 | 34 |
| `test/transport.test.ts` | Profile → `pi@0.84.1` | 108 | 9 |

**Total: 1212 test LOC, 129 tests pass, 0 fail.** (Original C3 target <=1200
was 1190/121; the +22 LOC/+8 tests add the Block/Fault per-reason shape
mutations and the malformed-Block-reason gate regression.)

`guard.test.ts` is the C3 test addition (118 LOC / 45 tests). `parity.test.ts`
holds only compact lexical projection + one builder-table `jsonschema`
validation (186 LOC, target <=230 met); the three direct tool regressions moved
to the existing gate-test setup, and status + compaction moved there too (not
covered elsewhere — pure `statusNotice`/`startRunNotice` unit tests live in
`translate.test.ts`, but the registered-handler integration did not).

## Lexical projection (tightened)

`parity.test.ts` parses contract.ts lexically and proves, for each retained
command interface and `RunSelector`, a **canonical-required-exact** partition
with the TS optional fields a **subset** of the schema-optional fields and **no
TS extras**, against the canonical `request.schema.json` `$defs`:

- schema-required ⟺ TS-required (canonical required exact, both directions —
  every canonical required field is present and required in TS; the projection
  cannot omit one);
- TS-optional ⊆ schema-optional (TS optional subset — the projection may omit
  schema-optional fields; schema-optional ⟹ TS-optional is NOT asserted);
- every TS field is a schema property (no TS extras — the reduced projection
  adds nothing).

Plus the `Command` union is exactly the four retained interfaces, `PROTOCOL`
matches the schema const, `EvaluateIntent` matches the schema enum, and
`Budgets`/`Modes` are canonical-optional. One builder table validates every
emitted envelope against the canonical request schema via Python `jsonschema`.

## Gates

### report_convergence hard gate (nonnull handle)

With a nonnull `runHandle`, the gate permits **only** a guarded `Allow`:

| Result | Gate |
|---|---|
| `Allow` (converged true or false) | permit |
| `Block` | deny |
| `Inert` (run gone, handle exists) | deny |
| `Fault` (open or closed) | deny |
| transport error | deny (fail closed) |

`Allow` with `converged=false` is a valid **permit** — machine-approved
non-convergence reporting. Both registered tool `execute` (throws on deny) and
`tool_call` interception (returns `{block}`) route through `gateFromDecision`.

### Allow cross invariant (central guard)

`assertAllowCrossInvariant` in `guard.ts`:
- `converged=true` requires `run.status=converged`
- `converged=false` requires `run.status` in {active, stopped_residual, stopped_frozen, stopped_budget}

Tested true+active and false+converged reject through the guard and the fake
gate.

### Block reason / Fault message shape validation (central guard)

The guard now asserts, for each `Block.reasons[]` entry: it is an object, its
`code` is a nonempty string, and an optional `message`, if present, is a string.
For `Fault`, an optional `message`, if present, must be a string. This is shape
validation only — **no deep reason-registry validation** (the guard does not
check whether `code` is a known reason). Mutation matrix covers `null` and `{}`
entries, numeric and empty `code`, and numeric `message` on both Block and Fault;
one end-to-end gate regression proves a malformed Block reason (`null` entry)
reaches the hard gate and fails closed. `BlockReason.message?: string` is now
declared on the contract projection so the optional field is explicit.

### Subagent local fail-closed (D8-owned)

`isExecutableSubagentLaunch(toolName, input)` returns true only for a fresh
structured launch: exactly one of `agent`, `workflowScript`, `resume` present
and non-null. When `runHandle !== null`, the `tool_call` handler returns
`{ block: true, reason: subagentUnsupportedReason() }` — no dispatch, no child
protocol/state. Read-only management calls (`action: "list"`/`"status"`) and
malformed multi-key launches are inert. No-handle launches are inert.

### Truthful UX

- `/empirica` reports the StartRun attempt and any unsupported result via
  `startRunNotice` — never relabels a start as a status read.
- `empirica_status` reports the run id and status only via `statusNotice`.
- Live `StartRun` asserts exact `unsupported`/`closed`.
- Genuine raw `empirica/v1` bridge test asserts `invalid_request`/`closed`.

## Barrier results

| Check | Result |
|-------|--------|
| `make check` (static + core + claude + codex + pi) | GREEN — All checks passed |
| Pi adapter tests (`node --test`) | GREEN (129/129) |
| Pi typecheck (`tsc --noEmit`) | GREEN |
| Pi static/bridge smoke (`validate_pi_adapter.py`) | GREEN |
| `check-static` (lint, validate, docs, ADR, contracts, obligations, vendor, activation) | GREEN (0 errors) |
| architecture (`validate_empirica_architecture.py`) | 7202 runtime lines (pre 7225 → current 7202, delta -23); 22 violations — all application/core (no Pi-owned) |
| `git diff --check` | clean |
| staged files | none |

## Stop conditions

None triggered. No D7 evaluation, D8 child admission, D9 projection, or D10
capability policy was needed. The subagent denial is D8-owned (the adapter denies
locally because D8 child admission is unavailable for the foreground-only
profile). No removed operation was mapped to another v2 action.

## Pending

C4 (pending — not part of this slice). This report is not self-accepted.
