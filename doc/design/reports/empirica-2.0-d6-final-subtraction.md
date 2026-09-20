# D6-C4 — final repository subtraction report

## Slice

C4 — final repository subtraction (accepted D6-C, §3 serial slice 4; closes the D6-C serial
sequence after C1 application/bridge subtraction, C2a/C2b host cutover, C3 Pi v2 cutover).

**Scope:** delete the unreachable v1-era runtime that no supported v2 path imports, so the
architecture validator reports zero target-state violations. **No** D7–D10 behavior is added,
staged, or evaluated by this slice.

## Honest C1–C4 narrative

C1 subtracted the v1 application/bridge (`application/service.py`, `application/state.py`,
`application/wire.py`, `adapters/claude/migrate_legacy.py`) and composed the shared bridge over
`application.v2` only. C2a cut Claude to v2-only; C2b cut Codex to v2-only; C3 cut Pi to v2-only.
Each slice removed its own host's v1 adapters but left three host-neutral core/application modules
that the v2 runtime never imports: `application/knowledge.py`, `core/audit.py`, and
`core/obligations.py`. The architecture validator flagged all of them as `ARCH-FORBIDDEN-SYMBOL`
findings — 22 in total — because each carried v1-era forbidden symbols/fields/literals
(`KIND_EVIDENCE`, `KIND_AUDIT_TICKET`, `audit_ticket`, `nonce`, `verdicts`).

C4 is the final subtraction: it deletes those three unreachable modules, removes the dead
`audit` import/export from `core/__init__.py`, and deletes the seven `AuditCoverage` subject
tests from `tests/test_core.py` (the only test that imported `core.audit`). No supported runtime
import broke — the v2 path (`application.protocol` → `application.v2` → `adapters.bridge`)
never reached any of the three modules. The architecture validator now reports **zero**
target-state violations.

## Current effective runtime / files and baseline delta

| Measurement | Value |
|-------------|------:|
| Architecture baseline (HEAD `4257c8d`) | 9,458 LOC |
| Architecture maximum | 9,457 LOC |
| Pre-C4 effective runtime | 7,202 LOC across 65 files (delta −2,256) |
| **Post-C4 effective runtime** | **6,486 LOC across 62 files (delta −2,972)** |
| Under budget | 2,971 LOC under the 9,457 maximum |

Source: `make empirica-architecture-check` (before and after this slice).

## Every runtime file added/deleted since HEAD (physical LOC)

### Deleted (tracked HEAD → gone)

Every entry is `git diff --numstat HEAD` physical deletion; the first column is the line count
that HEAD tracked. Production deletions (13 files, sum **3,755**) are listed first and counted in
the runtime budget; test deletions are listed separately below. Two ledgers apply for
`application/knowledge.py` (see *service HEAD1484 vs pre-C1 effective1493* note below).

**Production deletions (13 files, sum 3,755):**

| File | HEAD physical LOC | Slice |
|------|------------------:|:-----:|
| `plugins/empirica/application/service.py` | 1,484 | C1 |
| `plugins/empirica/application/state.py` | 379 | C1 |
| `plugins/empirica/application/wire.py` | 218 | C1 |
| `plugins/empirica/adapters/claude/migrate_legacy.py` | 305 | C1 |
| `plugins/empirica/adapters/claude/audit.py` | 44 | C2a |
| `plugins/empirica/adapters/claude/evidence.py` | 127 | C2a |
| `plugins/empirica/adapters/claude/knowledge.py` | 228 | C2a |
| `plugins/empirica/adapters/claude/spike.py` | 142 | C2a |
| `plugins/empirica/adapters/codex/knowledge.py` | 74 | C2b |
| `plugins/empirica/adapters/pi/src/obligations.ts` | 60 | C3 |
| `plugins/empirica/application/knowledge.py` | 458 | **C4** |
| `plugins/empirica/core/audit.py` | 106 | **C4** |
| `plugins/empirica/core/obligations.py` | 130 | **C4** |

**Test deletions (separate from the runtime budget):**

| File | HEAD physical LOC | Slice |
|------|------------------:|:-----:|
| `plugins/empirica/tests/test_application.py` | 2,043 | C1 |
| `plugins/empirica/adapters/claude/tests/fixtures/subagent-final.jsonl` | 2 | C2a |
| `plugins/empirica/adapters/pi/test/audit.test.ts` | 192 | C3 |
| `plugins/empirica/adapters/pi/test/fixtures/get-argument-response.json` | 21 | C3 |
| `plugins/empirica/adapters/pi/test/lifecycle.test.ts` | 89 | C3 |
| `plugins/empirica/adapters/pi/test/nudge.test.ts` | 89 | C3 |
| `plugins/empirica/adapters/pi/test/obligations.test.ts` | 14 | C3 |
| `tests/test_core.py` AuditCoverage section (54 LOC) | 54 | **C4** |
| `tests/test_core.py` legacy IdentityMatrix method (7 LOC) | 7 | C1 |

`tests/test_state_adapter.py` remains; 49 LOC were removed from it in C1 (the file
itself is not a 49-LOC file — 49 LOC of its content were removed as part of C1 cleanup).

Non-runtime deletions (contracts, not counted in the runtime budget):
`contracts/empirica/v1/request.schema.json` (113), `contracts/empirica/v1/response.schema.json` (224).

### Added (new runtime files, untracked since HEAD)

These files are untracked (`??` in `git status`); they were introduced by D6-B/C1–C3 and never
existed at HEAD. Physical LOC measured now.

| File | Physical LOC | Slice |
|------|--------------:|:-----:|
| `plugins/empirica/application/observation.py` | 105 | D5 |
| `plugins/empirica/application/ports.py` | 83 | D5 |
| `plugins/empirica/application/protocol.py` | 88 | D6-B |
| `plugins/empirica/application/run_state.py` | 112 | D6-B |
| `plugins/empirica/application/v2.py` | 168 | D6-B |
| `plugins/empirica/core/freshness.py` | 257 | D5 |
| `plugins/empirica/adapters/pi/src/guard.ts` | 195 | C3 |

### Service HEAD1484 vs pre-C1 effective1493

The `application/service.py` distinction documented in C1 applies here too: the HEAD physical
deletion is **1,484** (`git diff --numstat` attests this), while the pre-C1 *effective* working
content was **1,493** (the +9 were preexisting, uncommitted, and abandoned in C1 — never in git
HEAD, so `git diff` cannot see them). For `application/knowledge.py` the HEAD physical count is
**458** while the pre-C4 *effective* working content was **476** (derived: +18 uncommitted,
abandoned in C4). The total effective delta measured pre-C4 (7,202) to post-review (6,486) is
**716** = C4 delta **715** + review comment cleanup **1**. The C4 delta 715 breaks down as
knowledge 476 + audit/obligations 236 (106 + 130) + core-init 3. The knowledge 476 figure is
derived by subtracting audit/obligations 236 and core-init 3 from the C4 delta 715
(715 − 236 − 3 = 476). `core/audit.py` (106) and `core/obligations.py` (130) match HEAD
exactly with no uncommitted drift.

## Modified runtime net (since HEAD, per `git diff --numstat`)

C4 effective changes (this slice only):

| Change | Effective runtime delta |
|--------|------------------------:|
| `core/__init__.py` — dead `audit` import/export removed | −3 |
| Deleted modules (`application/knowledge.py` 476, `core/audit.py` 106, `core/obligations.py` 130) | −712 |
| C4 Audit tests (`test_core.py` AuditCoverage section) | −54 (non-runtime) |
| Final review comment cleanup across `convergence.py` / `evidence.py` | −1 |

The whole-HEAD `git diff --numstat` values for `convergence.py` (+3/−11 = −8) and
`evidence.py` (+141/−1 = +140) include earlier D5/D6-B changes and appear only in the
retained-rewrite aggregate below, **not** in C4 slice attribution. C4's own runtime
contribution to those two files is the −1 net comment cleanup.

**Tracked runtime net (since HEAD):**

| Component | +add | −del | Net |
|-----------|-----:|-----:|----:|
| Retained rewrites (modified tracked files) | +1,291 | −1,516 | −225 |
| Production deletions (13 files) | +0 | −3,755 | −3,755 |
| **Total tracked runtime** | **+1,291** | **−5,271** | **−3,980** |

**Untracked additions (new runtime files):** +1,008 LOC across 7 files (never at HEAD).

**Grand total delta:** −3,980 (tracked) + 1,008 (untracked) = **−2,972**. The architecture validator
attests this: baseline 9,458 → post-C4 6,486 = **−2,972** (65 → 62 files).

## Host exact profiles

| profile_id | current_tier | host |
|------------|-------------|------|
| `claude-code@2.1.270` | foreground_only | Claude Code |
| `codex-cli@0.146.0` | observational | Codex |
| `pi@0.84.1` | foreground_only | Pi |
| `pi@0.84.1+pi-subagents@0.50.0` | foreground_only | Pi + subagents |

Source: `contracts/empirica/v2/host-profiles.json`. C4 makes no host-profile edits; these are the
exact registry profiles D4 case 49 iterates (all profiles), case 48 selects the default profile,
and case 50 exercises capability privacy.

## C3 guard

C4 makes **no** app/core/Python-host adapter edits to the Pi surface. The C3 guard
(`adapters/pi/src/guard.ts`, 195 LOC, added by C3) and the Allow cross invariant are unchanged.
C4's three deletions are host-neutral core/application modules that the Pi adapter never
imported. `make check-pi` passes (129 tests, 7 test files).

## D4 grouping

Full D4 owner ledger (50 cases):

| Owner | D4 cases |
|-------|----------|
| D9 | 1, 30–44 |
| D7 | 2–5, 16–21 |
| D5 | 6–11 |
| D5-F | 12–15 |
| D8 | 22–29 |
| D6 | 45–47 |
| D10 | 48–50 |

Focused partition (this slice's scope, D6-owned cases 45–47):

| D4 case | Method | Owner | Status |
|---------|--------|-------|--------|
| 45 | `test_v2_identity_checked_before_decoding` | D6 | **GREEN** |
| 46 | `test_v1_and_old_state_rejected_with_fresh_run_recovery` | D6 | **GREEN** |
| 47 | `test_unknown_fields_actions_fail_closed` | D6 | **GREEN** |

Cases 45–47 (D6-owned) are green (3/3). Cases 48–50 (D10-owned) are **intentionally unsupported**
— they are not errors from a missing seam or v1 residue; they are later-owner cases that this slice
does not implement. The focused 48–50 partition reports **6 failures / 1 error**. The full D4
suite (all 50 cases, pre-D7–D10 tree) reports **12 failures / 75 errors** separately — these are
the D7–D10 owner gaps, not C4 regressions. C4 did not change either partition: the red/green
split is identical before and after this slice.

## Zero violations

- **Architecture:** `make empirica-architecture-check` → `architecture: ok — no target-state
  violations`. All 22 `ARCH-FORBIDDEN-SYMBOL` findings eliminated (the only rule that was red for
  C4).
- **Migration:** none — no migration code added or touched.
- **v1:** none — no v1 protocol, state, or wire path remains in runtime.
- **Cross-host:** none — the three deleted modules were host-neutral and imported by no host
  adapter.
- **Budget:** 6,486 LOC ≤ 9,457 maximum (2,971 under).
- **Forbidden:** zero `ARCH-FORBIDDEN-SYMBOL`, zero `ARCH-FORBIDDEN-PATH`, zero
  `ARCH-V1-PROTOCOL`, zero `ARCH-ADAPTER-ADJUDICATOR`, zero `ARCH-MAKE-LIFECYCLE`.

## Preserved (not deleted)

Per the accepted spec, C4 does **not** delete:

- **D5 freshness** — `core/freshness.py` (257 LOC), `application/observation.py` (105),
  `application/ports.py` (83). `make check-core` / `test_freshness.py` (58 tests) green.
- **ports/repository safety** — `core/ports.py`, `core/records.py`, `test_core.py` (21
  unittest cases plus 31/31 separately executed persistence-port contract checks, the latter
  invoked procedurally by `main()` from the same file) all retained and green.
- **evidence/claims future primitives** — `core/evidence.py`, `core/claims.py` retained.
- **obligation canonical lib/vendor** — `vendor/obligations/` byte-identical
  (`make vendor-check` → 5 byte-identical files). The deleted `core/obligations.py` was a
  v1-era *projection* of the canonical lib, not the canonical lib itself.

## All suite counts / gates

| Check | Result | Count |
|-------|--------|-------|
| `make check` (full) | **PASSED** | all suites green |
| `make check-core` | PASSED | core + claims + budget + freshness + observation + bridge-v2 + git-store |
| `make check-static` | PASSED | lint, validate, docs, ADR, contracts, obligations, vendor, activation |
| `make check-claude` | PASSED | activation lifecycle + adapter |
| `make check-codex` | PASSED | adapter + package/hook |
| `make check-pi` | PASSED | 129 tests, 7 files |
| `make contract-check` | PASSED | 10 schemas, 11 fixtures, 33 v2 fixtures |
| `make obligations-check` | PASSED | 3 schemas, 23 fixtures |
| `make vendor-check` | PASSED | 5 byte-identical files |
| `make activation-check` | PASSED | 50 runtime files, 7 thin hooks |
| `make empirica-architecture-check` | **PASSED** | 0 violations (was 22) |
| strict44 (`test_d6_strict_v2.py`) | **GREEN** | 44/44 |
| D4 45–47 (`test_protocol_host.py`) | **GREEN** | 3/3 (D6-owned) |
| D4 48–50 | RED (expected) | D10 owner — 6 failures / 1 error |
| D4 full suite (50 cases) | RED (expected) | 12 failures / 75 errors (D7–D10 gaps) |
| preflight (`v2/__main__.py --preflight`) | **GREEN** | 50 methods, 29 envelopes, 29 fixtures |
| `test_core.py` | **GREEN** | 21 unittest cases + 31/31 persistence-port contract checks (procedural, invoked by `main()`) |
| `git diff --check` | clean | |
| index | empty (no staged files) | |

## No self-accept

This report is an honest artifact, not self-acceptance. Final acceptance is deferred to
Terra/Sol review per the accepted D6-C spec.

## Stop conditions

None triggered. No D7 evaluation, D8 child admission, D9 projection, or D10 capability policy was
needed. No stage/commit/subagent/outside-search was performed. Runtime remains well under budget.
