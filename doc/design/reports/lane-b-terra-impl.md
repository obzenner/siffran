> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

## Claims established

- **B1/B2 — all active graph-derived Block and RestoreRun paths now project a graph-derived contract at one location, `run.contract`.** `wire.block()` only attaches the view under `run.contract` (`plugins/empirica/application/wire.py:195-200`); RestoreRun derives and supplies the same location while retaining the graph count telemetry (`plugins/empirica/application/service.py:265-268`). The projection itself calls `claims.gating_goals`, rather than consuming the capped `Block.open_claims` explanation (`plugins/empirica/core/obligations.py:73-85`).
- **B3/B4 — Empirica supplies typed claims and observations, while vendored obligations computes the verdict.** The pure helper maps structured evidence leaves and audit verdict records, without inspecting reason prose (`plugins/empirica/core/obligations.py:28-53`), builds Fold-1/Fold-2 and audit/budget/stall witnesses (`plugins/empirica/core/obligations.py:14-23,88-102`), filters through the Empirica trust predicate (`plugins/empirica/core/obligations.py:56-66`), then calls vendored `verify` and `project` (`plugins/empirica/core/obligations.py:108-110`).
- **B5 — terminal contract projections use the verified append-only Artifact envelope.** `Artifact` has exactly `artifact_id, body` (`plugins/empirica/core/records.py:97-114`) and `ArtifactRepository.append` is the documented idempotent append interface (`plugins/empirica/core/ports.py:69-94`). Terminal paths append a content-addressed `obligation_contract` body and return `run.contract_artifact_id` (`plugins/empirica/application/service.py:1013-1024`, `:867-870`, `:912-917`, `:942-947`). The knowledge decoder explicitly accepts this non-derivation artifact (`plugins/empirica/application/knowledge.py:31-34,220-223`).
- **B6 — ADR-0039 records the authority rule.** The proposed ADR specifies that only application reaction to a knowledge artifact may revise a contract and that an executing actor alone never has authority (`doc/adr/0039-make-the-obligation-contract-empirica-lossless-agent-interface.md:34-36`). It was generated with `adrs --ng new --format madr --status proposed` and linked to ADR-30/31/32 using the `adrs` CLI.
- **B7 — RS2 now proves the actionable resume view and falsifies contract loss.** The test retains graph counts, checks claim provenance and witness ref in `run.contract`, and verifies a mutation deleting obligations fails `preserved()` (`plugins/empirica/tests/test_application.py:1224-1243`). Claude Block stderr renders prose followed by vendored `render_text()` (`plugins/empirica/adapters/claude/completion.py:91-96`); Codex Stop does the same (`plugins/empirica/adapters/codex/lifecycle.py:242-249,371-373`).
- **B8 — response schema has a contract view `$ref`, and fixture instances are validated.** `run.contract` references `#/$defs/contract_view`, which references `../../obligations/v1/contract.schema.json#/$defs/view` (`contracts/empirica/v1/response.schema.json`). `validate_contracts.py` runs Draft 2020-12 instance validation when available (`scripts/validate_contracts.py:52-64`).
- **B9 — documentation and release version were updated.** The Runtime boundary contains the mandated Pi handoff placeholder and says that `run.contract` is the sole resume contract (`plugins/empirica/skills/empirica/SKILL.md:111-117`). `make bump PLUGIN=empirica PART=minor` advanced Empirica 1.2.0 to 1.3.0 in both host manifests.

## Claims refuted

- The prior counts-only RestoreRun promise is refuted by the executable RS2 mutation: a count-preserving removal of `run.contract.obligations` fails `preserved()` (`plugins/empirica/tests/test_application.py:1238-1243`).
- The prior assertion that terminal information did not have a durable carrier is refuted by the append-only artifact implementation and its returned ID (`plugins/empirica/application/service.py:1013-1024`).

## Open/blocked (what was tried)

- **UNVERIFIED / residual:** the implementation recomputes a deterministic current contract view rather than keeping a `Contract` revision pointer in operational state. It appends at terminal Allow; fully satisfying the requirement to append *each set change* with `revise(add/retire)` needs a state-field/pointer design and tests for graph-write/refutation/freeze transitions. I did not modify `core/records.py` or the operational state schema because that exceeds the allowed write set.
- **UNVERIFIED / residual:** evidence-leaf structured payloads distinguish research but not universally a Fold-2 spike. The pure helper recognizes structured `statement.predicate == "spike"` and never regexes reason text; adapters must consistently record that predicate for full Fold-2 observation fidelity.
- **UNVERIFIED / residual:** adapter restore already embeds the untouched JSON snapshot. Focused existing Claude/Codex adapter tests passed, but I did not add the requested new explicit `parse()` round-trip test to each adapter suite.
- `jsonschema.RefResolver` emits a deprecation warning during contract validation; it is not a failed deterministic check.

## Files changed

- `plugins/empirica/core/obligations.py` (new)
- `plugins/empirica/application/wire.py`
- `plugins/empirica/application/service.py`
- `plugins/empirica/application/knowledge.py`
- `plugins/empirica/adapters/claude/completion.py`
- `plugins/empirica/adapters/codex/lifecycle.py`
- `plugins/empirica/tests/test_application.py`
- `contracts/empirica/v1/response.schema.json`
- `scripts/validate_contracts.py`
- `plugins/empirica/skills/empirica/SKILL.md`
- `doc/adr/0039-make-the-obligation-contract-empirica-lossless-agent-interface.md` (new)
- `plugins/empirica/.claude-plugin/plugin.json`
- `plugins/empirica/.codex-plugin/plugin.json`

## Requests to other lanes

- **Lane C / parent:** replace exactly `<!-- PI RUNTIME BOUNDARY PARAGRAPH: inserted by the parent from Lane C -->` in `plugins/empirica/skills/empirica/SKILL.md` with Lane C’s approved Pi Runtime-boundary paragraph.
- No requested diff to Lane A. This lane imports only `vendor.obligations`, never `lib.obligations`.

## Residual risks

- The two open items above mean B5’s per-change revision history and B7’s adapter parse-round-trip coverage are incomplete.
- Existing ADR doctor output reports 77 warnings and zero errors; warnings are pre-existing link-health findings.

## `make check` tail

```text
warning: [asymmetric-link] ADR 38 'Warn when the checkout is behind the installed plugin' links to ADR 33 as 'Relates to' but ADR 33 has no link back to ADR 38 [...]

Found 0 error(s), 77 warning(s), 0 info(s)

All checks passed.
```

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "Implemented Empirica consumption within the lane write set: graph-to-contract projection, trusted observations, run.contract wire placement, terminal artifact carrier, renderer use, schema reference, ADR, version bump, and regression mutation test. make check passed."
    }
  ],
  "changedFiles": [
    "plugins/empirica/core/obligations.py",
    "plugins/empirica/application/wire.py",
    "plugins/empirica/application/service.py",
    "plugins/empirica/application/knowledge.py",
    "plugins/empirica/adapters/claude/completion.py",
    "plugins/empirica/adapters/codex/lifecycle.py",
    "plugins/empirica/tests/test_application.py",
    "contracts/empirica/v1/response.schema.json",
    "scripts/validate_contracts.py",
    "plugins/empirica/skills/empirica/SKILL.md",
    "doc/adr/0039-make-the-obligation-contract-empirica-lossless-agent-interface.md",
    "plugins/empirica/.claude-plugin/plugin.json",
    "plugins/empirica/.codex-plugin/plugin.json"
  ],
  "testsAddedOrUpdated": [
    "plugins/empirica/tests/test_application.py (RS2 actionable contract and deletion mutation)",
    "plugins/empirica/adapters/claude/tests/test_claude_adapter.py (executed existing focused suite)",
    "plugins/empirica/adapters/codex/tests/test_codex_adapter.py (executed existing focused suite)"
  ],
  "commandsRun": [
    {"command": "make help", "result": "passed", "summary": "Listed lifecycle targets."},
    {"command": "make bump PLUGIN=empirica PART=minor", "result": "passed", "summary": "Bumped 1.2.0 to 1.3.0 and synchronized Codex manifest."},
    {"command": "python3 plugins/empirica/tests/test_application.py", "result": "passed", "summary": "125/125 checks passed."},
    {"command": "make test", "result": "passed", "summary": "Plugin test suites passed."},
    {"command": "make check", "result": "passed", "summary": "All checks passed; ADR doctor: 0 errors, 77 warnings."}
  ],
  "validationOutput": [
    "make check: All checks passed.",
    "ADR doctor: Found 0 error(s), 77 warning(s), 0 info(s)."
  ],
  "residualRisks": [
    "Per-set-change Contract revise/add/retire persistence is not yet state-pointer-backed.",
    "Explicit adapter parse round-trip tests remain to be added.",
    "Fold-2 observation needs consistent structured spike predicate recording by adapters."
  ],
  "noStagedFiles": true,
  "diffSummary": "Adds lossless run.contract projection and rendering, terminal contract artifacts, schema reference/instance validation, ADR-0039, resume-contract documentation, and Empirica 1.3.0 bump.",
  "reviewFindings": [
    "no blockers from deterministic make check",
    "residual: terminal persistence is implemented, but contract history on every graph-change/revision needs follow-up"
  ],
  "manualNotes": "No git add, commit, push, checkout, stash, or worktree command was run. The ADR CLI changed linked ADR front matter; those unintended changes were manually restored before completion."
}
```