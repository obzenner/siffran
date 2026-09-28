# Testing and native qualification

The Makefile has three deliberately separate layers. None is a substitute for another.

## Fast contributor gate

`make check` runs static validation plus the fast core, Claude, Codex, and Pi suites. `make check-ci` omits Pi unless `PI_CHECKS=1`; `make test` aliases `make check`, including validator unit tests and Pi. Missing Node/TypeScript tooling fails unless `EMPIRICA_ALLOW_SKIP=1` explicitly acknowledges the omission.

The fast gate retains schema/vendor validation; malformed protocol and state input; private authority and identity checks; core StartRun admission (including structural refusal without a run); contract-tagged refusal/recovery dispositions; state and bridge boundaries; focused real-service governance CAS/replay and consent denial; the Claude model-switch subprocess regression; Pi guards, transport failures, and governance UI controls; and Methodologist core/MCP/adapter tests. Pi bundle validation checks package composition only, so it does not execute the adapter suite a second time.

Runtime-derived Empirica wire-fixture fields are generated with `make contract-fixtures`; shared schema definitions and local `$ref` embeddings are generated with `make contract-schemas`. `contracts/empirica/v2/shared-defs.json` is the never-rewritten source for `invocationProvenance` and `identityObservation`; `public-contract.schema.json` is the source for the prefixed public-contract embedding. `make contract-check` and `make check-static` run both generators in check mode (plus the schema-generator unit regressions), so drift fails without rewriting files; regenerate deliberately after projection or shared-schema changes.

## Targeted integration diagnostics

Run these when changing the named boundary, not on every edit:

| Target | Boundary | Why it is not in the fast gate |
|---|---|---|
| `make empirica-governance-check` | full governance service, CAS/replay/consent, simulated host flows | repeated Git/state setup |
| `make empirica-core-integration` | full governance coverage, strict v2 state, transactions, retry, persistence, complete behavioral matrix | broad filesystem/schema scenarios; includes `empirica-governance-check` so admission/consent negative controls cannot fall outside the milestone gate |
| `make empirica-v2-check ARGS="-k test_name"` | a selected behavioral regression (omit ARGS for all v2 cases) | ordinary unittest selection, no custom runner |
| `make empirica-activation-lifecycle-check ARGS="-k test_name"` | isolated Claude hook lifecycle (omit ARGS for all cases) | full subprocess matrix is expensive; the selected model-switch regression is in the fast gate |
| `make empirica-host-integration` | Claude/Codex lifecycle conformance plus Pi's real Python bridge/scripted UI path | requires Node; repeated subprocess/full-journey setup; blank-goal visibility is exercised through each real service adapter path |
| `make empirica-host-live-check` | retained installed-host release receipts | requires prior operator evidence |

The fast Pi suite also contains one real `govern()` → private Python service round trip with scripted UI: pending edited values, locked confirmation, and exact persisted approval. Run it independently with `make empirica-governance-bridge-check`. This restores targeted cross-language boundary coverage, not a simulated substitute for native human qualification.

`make release-check` deliberately includes the fast gate, all three integration diagnostics and retained installed-host receipts. It is not the everyday development loop. A matching source revision's completed check is reusable evidence; do not rerun identical bytes merely because the next action is a commit.

The old D-stage aliases and custom v2 preflight runner were removed. Full v2 diagnostics now use ordinary `unittest discover`. Historical ADR/design commands remain historical evidence, not current entrypoints.

## What was subtracted

- The repository-root Pi package no longer reruns the same adapter typecheck and Node suite.
- Repeated Pi simulated end-to-end journeys (`adapter-conformance.test.ts` and `governance-ui.test.ts`) were removed. Their UI-only assertions—strict numeric input, Escape/cancellation, unknown choices, reversible modes, locked confirmation, and safe rendering—live in `governance-ui-unit.test.ts`. Real-service CAS, stale/replay, durable guidance, identity-class, and denial rules remain in Python governance tests; a bounded local bridge smoke remains in the Pi suite.
- The duplicate private transport file was merged into `transport.test.ts`.
- The obsolete v2 `__main__.py --preflight` machinery was deleted; it encoded stale fixed file/test counts and duplicated schema/static checks.
- Scripted online Codex sessions and credential copying were deleted. Deterministic package/hook/MCP tests remain.

This deliberately reduces TS-to-real-Python whole-journey coverage: the remaining service and UI tests are not equivalent proof of native composition. Real activation, dialogs, configuration revocation, audit execution and guarded completion now require the skill-led native scenario. Cheap properties still catch malformed input and authority defects that a single human scenario cannot exhaust.

Contracts and vendor schemas were not deletion candidates. The shared v2 driver/SUT adapter remain because the live behavioral modules import them; removing that seam would be a test architecture rewrite rather than redundant-runner subtraction.

## Inspecting run state

`make empirica-runs` is the read-only census of operational run state (`scripts/empirica_run_census.py`). It enumerates only the documented store layout `<home>/projects/*/runs/*/gen-*/run.json` under `EMPIRICA_HOME` (default `~/.empirica-plugin`), and qualification homes only at `<root>/<candidate>/<cell>/state` via `ARGS="--qualification-root <dir>"`. Use `--runs`, `--status`, `--host`, `--run <id-prefix>` and `--json` through `ARGS`. Host attribution is inferred from the audit role profile and native id and is labelled as such. Do not search for run state with recursive walks over `$HOME`, `/tmp` or worktrees: they traverse `node_modules` and `.git` and take minutes. `make empirica-run-census-unit-check` (part of `check-static`) guards the fixed layout and the no-write property.

`make claude-subagent-models` reports requested versus served models for Claude Code `Agent` launches (`scripts/claude_subagent_models.py`). It joins each parent session under `~/.claude/projects/<project>/<session>.jsonl` with its `subagents/agent-<id>` transcript at fixed depth. Use `ARGS="--agent empirica:empirica-auditor"`, `--requested-only`, explicit session paths or `--json`. Synthetic transcript rows are excluded from served-model output. A concrete served model is identity evidence only when the adapter also correlates it to the verdict-bearing audit child; a requested alias or configured pin is not. `make claude-subagent-models-unit-check` is part of `check-static`.

`make empirica-pi-auditor-resolution [DIR=<project>] [STRICT=1]` reports which auditor file the pinned pi-subagents resolves for a project in each agent scope (`scripts/pi_auditor_resolution.mjs`). pi-subagents keys package agents by name, so a later user-level package can shadow a project package of the same name in combined discovery. The 4.0 adapter avoids that ambiguity by executing in the project or user scope that resolves its own packaged auditor; the inspection target remains the pre-qualification proof of which file each scope selects. Run it with `STRICT=1` before Pi qualification. `make pi-auditor-resolution-unit-check` (part of `check-pi`) runs it in an isolated HOME, including a shadowing negative control.

`make empirica-context-economy-report FILE=<slice> [MAX=<chars>]` measures the qualification `context_economy` row reproducibly (`scripts/empirica_context_economy.py`). The measurement object is the model-visible tool result **text** each Empirica tool (`empirica_read`, `empirica_observe`, `report_convergence`) returns to the model — the host `content` text — counted in Unicode code points; the interval is the primary run from activation to terminal; only Empirica tools count. The operator extracts the ordered tool results from the retained transcript/log into a newline-delimited JSON slice (one `{"tool": ..., "result": {"content": [{"type": "text", "text": ...}], ...}}` per call). The counter consumes the rendered text as-is (it never JSON-decodes that text), reports the maximum
single and total model-visible characters and, tracked **separately** in UTF-8 bytes, the text-only
transport envelope size. Claude/Codex MCP results contain no duplicate RunView
`structuredContent`; Pi's validated JSON remains host-internal `details`. `MAX=<chars>` fails when
any single result exceeds that ceiling. `make empirica-context-economy-unit-check` (part of
`check-static`) guards the counter's fixed layout and no-write property.

## Native logs and installed-host receipts

For Claude qualification, launch with `make claude-dev CLAUDE=/absolute/supported/claude ARGS="--debug-file /absolute/evidence/claude.debug.log"`, then validate that retained admission log with `make empirica-claude-mcp-log-check LOG=/absolute/evidence/claude.debug.log`; optional expectations belong in `ARGS` as shown by `make help`. Launch Pi with `make pi-dev PI=/absolute/supported/pi`.

After one complete supported-host trace, `make empirica-host-receipt` captures the operator-attested transcript, state, child session, host version, command, and output paths supplied through the variables listed by `make help`. It does not run a host or prove UI checkpoints. `make empirica-host-live-check` validates retained Claude and Pi `Allow(converged=true)` receipts for the current candidate; absent, stale, malformed, or inapplicable receipts fail the release gate.

## Native qualification

`make native-qualification` prints the path to `.claude/skills/native-qualification/SKILL.md`; it launches nothing. The skill specifies `make claude-dev` (directory plugins) and `make pi-dev` (project package override); neither changes the global installation. Follow the skill in an explicitly authorized, operator-present session using a supported Claude or Pi environment; the human supplies every consent response. Codex Empirica can establish only its expected refusal. Partial and failed evidence belongs in the skill's checkpoint ledger; existing converged receipt tools cannot honestly represent every governance checkpoint.
