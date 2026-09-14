# Empirica 2.0 D4-S1 — harness simplification and structural preflight

**Status:** Parent-frozen serial writer milestone.

## Scope

Modify only:

- `plugins/empirica/tests/v2/__main__.py`
- `plugins/empirica/tests/v2/assertions.py`
- `plugins/empirica/tests/v2/driver.py`
- `plugins/empirica/tests/v2/sut_adapter.py`
- the eight `plugins/empirica/tests/v2/test_*.py` files only to remove `checkpoint(...)` calls/import
  comments and replace literal protocol/profile/tier/adverse-state inventories with existing canonical
  helpers; do not otherwise repair case behavior in S1
- `doc/design/reports/empirica-2.0-d4-red-first.md` only to mark S1 partial status, not final acceptance

Do not modify Makefile, contracts, D1–D3, D4 parent spec, runtime, manifests, or old tests.

## Required subtraction

Delete completely:

- `NoOpProbeDriver`, `new_probe_driver`, `_PROBE_ADVERSE`, and all behavior-aware probe state;
- `--probe` runner/mode and its classification/reporting;
- `_CHECKPOINTS`, `ConformanceCase.checkpoint`, BehaviorMismatch/checkpoint prose;
- every checkpoint call from all 50 cases.

`HarnessDefect` may remain solely as a future real-SUT prerequisite diagnostic, with no probe or
mutation-adequacy claims.

## Structural `--preflight`

Implement a green mode with no driver and no behavioral simulation. It must:

1. import and collect exactly 50 unique `test_*` methods from the eight modules;
2. reject skips, expected failures, empty/pass/TODO placeholder bodies, and any remaining checkpoint or
   probe symbol;
3. validate representative instances from every static request/envelope/action builder against the
   accepted request schema; strict negative raw requests are excluded explicitly;
4. validate accepted D2A/D2B response fixtures through response-schema helpers and prove one malformed
   response is rejected;
5. AST-check test modules so `.request(...)` appears only inside central `dispatch`/`raw_dispatch`
   implementation and raw dispatch/state injection are limited to exact named strict test methods; a
   test may invoke a prerequisite helper rather than call `dispatch` directly, so do not claim every
   method contains a direct dispatch call;
6. compare method names/signatures declared by `ConformanceDriver` with `LiveDriver` and relevant fake
   port implementations using `inspect.signature` after removing `self`; optional/default parameters
   must agree;
7. reject copied full protocol/profile/tier/terminal/reason/action/section inventories and obvious
   permissive patterns (`or True`, self-membership, set-only comparison in ordered selector cases,
   conditional-only semantic assertion).

Preflight should remain compact (target `__main__.py` <= 360 lines): use data-driven samples and the
existing schema loader, not a bespoke general linter or duplicated contract validator. Do not hide
forbidden residual strings through concatenation merely to pass grep; the preflight need not search
for its own deleted implementation names.

Preflight output says structural only and explicitly says zero post-binding semantic behavior was
executed. It must not instantiate a SUT/probe or maintain run/child/evidence/freeze state.

## Shared boundary corrections

- Make `harness_complete` signatures identical across protocol/fake/live wrappers. Preserve optional
  `files` only if all implementations support it; otherwise remove it everywhere.
- Derive protocol, identity, profiles, tiers, child terminal/adverse states and transitions from loaded
  canonical JSON. Test scenario literals may select one profile, but no complete mirror table.
- Replace raw-state literal `empirica/v2` with exported canonical protocol helper.
- Keep `sut_adapter.py` as the only production-seam import owner.

## Report

Rewrite only enough to describe D4-S1 accurately: probe removed, preflight structural-only, normal mode
red, post-binding semantics unexecuted, remaining S2/S3 case corrections outstanding. Do not claim D4
acceptance or closure of cases.

## Verification

- `python3 plugins/empirica/tests/v2/__main__.py --preflight` => 0
- normal target collects 50 and is red only at absent real seam
- `rg 'NoOpProbe|new_probe_driver|_CHECKPOINTS|checkpoint\(|--probe|_PROBE_ADVERSE' plugins/empirica/tests/v2` => no matches
- `python3 -m py_compile plugins/empirica/tests/v2/*.py`
- `make check-static`, `make check-core`
- `git diff --check`; empty index

## Execution guardrails

Work only under `/private/tmp/empirica-twofold-fix`. Do not search outside that worktree. Do not use
`find /`, `grep -r /`, Git refs, or reviewer run IDs. All normative findings are in this file and the
parent D4 spec. If an input is missing, stop and report the exact path. No staging, commit, push, or
subagents.
