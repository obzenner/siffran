# Testing and native qualification

The Makefile has three deliberately separate layers. None is a substitute for another.

## Fast contributor gate

`make check` runs static validation plus the fast core, Claude, Codex, and Pi suites. `make check-ci` omits Pi unless `PI_CHECKS=1`; `make test` aliases `make check`, including validator unit tests and Pi. Missing Node/TypeScript tooling fails unless `EMPIRICA_ALLOW_SKIP=1` explicitly acknowledges the omission.

The fast gate retains schema/vendor validation; malformed protocol and state input; private authority and identity checks; state and bridge boundaries; focused real-service governance CAS/replay and consent denial; Pi guards, transport failures, and governance UI controls; and Methodologist core/MCP/adapter tests. Pi bundle validation checks package composition only, so it does not execute the adapter suite a second time.

## Targeted integration diagnostics

Run these when changing the named boundary, not on every edit:

| Target | Boundary | Why it is not in the fast gate |
|---|---|---|
| `make empirica-governance-check` | full governance service, CAS/replay/consent, simulated host flows | repeated Git/state setup |
| `make empirica-core-integration` | strict v2 state, transactions, retry, persistence, complete behavioral matrix | broad filesystem/schema scenarios |
| `make empirica-v2-check ARGS="-k test_name"` | a selected behavioral regression (omit ARGS for all v2 cases) | ordinary unittest selection, no custom runner |
| `make empirica-host-integration` | Claude lifecycle and cross-host simulated conformance | repeated subprocess/full-journey setup |
| `make empirica-host-live-check` | retained installed-host release receipts | requires prior operator evidence |

`make release-check` deliberately includes the fast gate, all three integration diagnostics and retained installed-host receipts. It is not the everyday development loop. A matching source revision's completed check is reusable evidence; do not rerun identical bytes merely because the next action is a commit.

The old D-stage aliases and custom v2 preflight runner were removed. Full v2 diagnostics now use ordinary `unittest discover`. Historical ADR/design commands remain historical evidence, not current entrypoints.

## What was subtracted

- The repository-root Pi package no longer reruns the same adapter typecheck and Node suite.
- Repeated Pi simulated end-to-end journeys (`adapter-conformance.test.ts` and `governance-ui.test.ts`) were removed. Their UI-only assertions—strict numeric input, Escape/cancellation, unknown choices, reversible modes, singleton warning, exact feedback, and safe rendering—live in `governance-ui-unit.test.ts`. Real-service CAS, stale/replay, durable guidance, inventory and consent rules remain in Python governance tests; a bounded local bridge smoke remains in the Pi suite.
- The duplicate private transport file was merged into `transport.test.ts`.
- The obsolete v2 `__main__.py --preflight` machinery was deleted; it encoded stale fixed file/test counts and duplicated schema/static checks.
- Scripted online Codex sessions and credential copying were deleted. Deterministic package/hook/MCP tests remain.

This deliberately reduces TS-to-real-Python whole-journey coverage: the remaining service and UI tests are not equivalent proof of native composition. Real activation, dialogs, revocation, audit execution and guarded completion now require the skill-led native scenario. Cheap properties still catch malformed input and authority defects that a single human scenario cannot exhaust.

Contracts and vendor schemas were not deletion candidates. The shared v2 driver/SUT adapter remain because the live behavioral modules import them; removing that seam would be a test architecture rewrite rather than redundant-runner subtraction.

## Native qualification

`make native-qualification` prints the path to `.claude/skills/native-qualification/SKILL.md`; it launches nothing. The skill specifies `make claude-dev` (directory plugins) and `make pi-dev` (project package override); neither changes the global installation. Follow the skill in an explicitly authorized, operator-present session using a supported Claude or Pi environment; the human supplies every consent response. Codex Empirica can establish only its expected refusal. Partial and failed evidence belongs in the skill's checkpoint ledger; existing converged receipt tools cannot honestly represent every governance checkpoint.
