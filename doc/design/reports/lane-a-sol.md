> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

## Claims established

- **A-1 — The generic v1 data model is immutable and substrate-neutral.** Frozen dataclasses define every scoped contract value and enum: `Witness`, `Obligation`, `Observation`, `Contract`, and `Verdict` (`lib/obligations/model.py:5-48`). The package header explicitly excludes GSN, claims, folds, hooks, hosts, filesystem paths, and exit codes (`lib/obligations/__init__.py:1-7`).
- **A-2 — Verification is injected, pure, deterministic, and order-independent.** `verify()` accepts the trust predicate, reduces trusted observations to a set, sorts obligations by ID, and matches the exact kind/ref/outcome tuple (`lib/obligations/verify.py:7-31`). Because kind is part of the key, a judgment cannot discharge a machine witness (`lib/obligations/verify.py:18-22`). A matching pass witness on a `must_not` obligation is classified as violated (`lib/obligations/verify.py:23-24`). Tests cover trust rejection, reordered observations, exact matching, judgment separation, and prohibition violation (`lib/obligations/tests/test_obligations.py:35-69`).
- **A-3 — Revision is append-only and explicit.** `revise()` rejects reused IDs, retains the complete prior tuple, increments the revision, records `parent_revision`, and names `contract_id@revision` in `supersedes` (`lib/obligations/revise.py:5-13`). Its regression test also proves the original frozen value remains at revision 1 (`lib/obligations/tests/test_obligations.py:71-78`).
- **A-4 — There is one canonical agent-facing projection with a falsifiable preservation predicate.** `project()` emits contract identity, revision lineage, provenance, every sorted obligation, statuses, and all verdict partitions (`lib/obligations/project.py:18-26`). `preserved()` reports stable reasons when an obligation disappears or when must/must_not/witnesses/because/severity changes (`lib/obligations/project.py:28-42`). Tests cover identical projection success and silent witness loss failure (`lib/obligations/tests/test_obligations.py:80-95`).
- **A-5 — Cross-language fixtures and JSON Schema 2020-12 contracts exist.** The protocol documents the three wire values and exact matching/trust rules (`contracts/README.md:10-18`). The three schemas declare the 2020-12 meta-schema (`contracts/obligations/v1/contract.schema.json:2`, `contracts/obligations/v1/observation.schema.json:2`, `contracts/obligations/v1/verdict.schema.json:2`). Six fixtures cover discharge, must-not violation, machine/judgment separation, revision/supersedes, preservation success, and silent witness loss. Python loads and executes all fixtures (`lib/obligations/tests/test_obligations.py:97-147`).
- **A-6 — Distribution drift is mechanically gated.** The vendor checker compares the five shipped Python files byte-for-byte and rejects missing, differing, or extra Python modules (`scripts/check_vendor.py:5-18`). `make vendor-check` and `make obligations-check` are public targets (`Makefile:122-130`), both are dependencies of `check` (`Makefile:64`), and generic tests run under `make test` (`Makefile:69-79`).
- **A-7 — The completed repository check is green.** `make check` exited 0. Tail:

```text
warning: [asymmetric-link] ADR 38 'Warn when the checkout is behind the installed plugin' links to ADR 33 as 'Relates to' but ADR 33 has no link back to ADR 38 [/private/tmp/obligations-contract/doc/adr/0038-warn-when-the-checkout-is-behind-the-installed-plugin.md]

Found 0 error(s), 77 warning(s), 0 info(s)

All checks passed.
```

## Claims refuted

- None.

## Open/blocked (what was tried)

- None blocked. `make obligations-check vendor-check test` initially exceeded the command timeout while the pre-existing full test suite was still running; it had already passed the new checks. I then ran the generic test directly, ran `make lint test` successfully, and finally ran the complete `make check` successfully.
- **UNVERIFIED:** cross-language TypeScript consumption is not established in this lane; the brief assigns the TypeScript mirror/test to Lane C. The substrate-neutral JSON fixtures are present and Python-consumed, but Lane C must provide the independent TS evidence.

## Files changed

- `Makefile`
- `contracts/README.md`
- `contracts/obligations/v1/contract.schema.json`
- `contracts/obligations/v1/observation.schema.json`
- `contracts/obligations/v1/verdict.schema.json`
- `contracts/obligations/v1/fixtures/discharge.json`
- `contracts/obligations/v1/fixtures/must-not-violation.json`
- `contracts/obligations/v1/fixtures/judgment-cannot-discharge-machine.json`
- `contracts/obligations/v1/fixtures/revision-supersedes.json`
- `contracts/obligations/v1/fixtures/preserved-pass.json`
- `contracts/obligations/v1/fixtures/preserved-fail-lost-witness.json`
- `lib/obligations/__init__.py`
- `lib/obligations/model.py`
- `lib/obligations/verify.py`
- `lib/obligations/revise.py`
- `lib/obligations/project.py`
- `lib/obligations/tests/test_obligations.py`
- `plugins/empirica/vendor/obligations/__init__.py`
- `plugins/empirica/vendor/obligations/model.py`
- `plugins/empirica/vendor/obligations/verify.py`
- `plugins/empirica/vendor/obligations/revise.py`
- `plugins/empirica/vendor/obligations/project.py`
- `scripts/check_vendor.py`
- `scripts/validate_obligations.py`

## Requests to other lanes (exact diffs)

- None beyond the already assigned brief. Lane B can import the vendored public API from `plugins/empirica/vendor/obligations`; Lane C should consume every JSON file under `contracts/obligations/v1/fixtures/` in its TypeScript conformance test. No cross-lane file diff is required from Lane A.

## Residual risks

- `validate_obligations.py` performs full instance validation only when the optional `jsonschema` package is installed (`scripts/validate_obligations.py:27-49`); without it, JSON parsing, protocol identity, and schema draft declarations remain checked, while Python fixture execution still checks semantic outcomes.
- `preserved()` compares the scoped semantic fields and detects disappearance but intentionally does not reject added obligations or state/status changes (`lib/obligations/project.py:28-42`); determining whether a state change was backed by a trusted observation remains the caller's responsibility via `verify()`.
- Existing ADR health emits 77 warnings but zero errors; these warnings predate and are outside Lane A's write boundary.
- `git diff --cached --name-only` was empty. No files are staged.

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "Implemented only the frozen generic obligation module, v1 schemas/fixtures, byte-identical Empirica vendor copy, validators, README protocol entry, Make targets, and generic tests; make check exited 0."
    }
  ],
  "changedFiles": [
    "Makefile",
    "contracts/README.md",
    "contracts/obligations/v1/contract.schema.json",
    "contracts/obligations/v1/observation.schema.json",
    "contracts/obligations/v1/verdict.schema.json",
    "contracts/obligations/v1/fixtures/*.json",
    "lib/obligations/__init__.py",
    "lib/obligations/model.py",
    "lib/obligations/verify.py",
    "lib/obligations/revise.py",
    "lib/obligations/project.py",
    "lib/obligations/tests/test_obligations.py",
    "plugins/empirica/vendor/obligations/*.py",
    "scripts/check_vendor.py",
    "scripts/validate_obligations.py"
  ],
  "testsAddedOrUpdated": [
    "lib/obligations/tests/test_obligations.py",
    "contracts/obligations/v1/fixtures/*.json"
  ],
  "commandsRun": [
    {
      "command": "make help",
      "result": "passed",
      "summary": "Inspected repository lifecycle targets before implementation."
    },
    {
      "command": "make obligations-check vendor-check test",
      "result": "failed",
      "summary": "New obligation and vendor checks passed; command timed out during the existing long test suite."
    },
    {
      "command": "python3 lib/obligations/tests/test_obligations.py",
      "result": "passed",
      "summary": "8 generic contract tests passed."
    },
    {
      "command": "make lint test",
      "result": "passed",
      "summary": "Ruff and all repository test suites passed after final formatting."
    },
    {
      "command": "make check",
      "result": "passed",
      "summary": "All checks passed; ADR doctor reported 0 errors and 77 warnings."
    },
    {
      "command": "git diff --cached --name-only",
      "result": "passed",
      "summary": "No staged files."
    }
  ],
  "validationOutput": [
    "ok: 3 schemas, 6 obligation fixtures",
    "ok: 5 byte-identical obligation vendor files",
    "Ran 8 tests: OK",
    "Found 0 error(s), 77 warning(s), 0 info(s)",
    "All checks passed."
  ],
  "residualRisks": [
    "TypeScript fixture consumption remains for Lane C.",
    "Full JSON Schema instance validation is conditional on optional jsonschema availability.",
    "ADR doctor retains 77 pre-existing warnings."
  ],
  "noStagedFiles": true,
  "diffSummary": "Added the generic immutable obligation contract and verifier/projection/revision logic, cross-language schemas and six fixtures, byte-identical Empirica vendor package, deterministic checks, tests, and Makefile gates.",
  "reviewFindings": [
    "no blockers"
  ],
  "manualNotes": "No git add, commit, push, checkout, or stash command was run."
}
```
