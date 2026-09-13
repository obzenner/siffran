[bedrock--gpt-5.6-sol]
> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

## Claims established

- **A1/A2 — Obligation state is derived, and requirements are separated from prohibitions.** `Obligation` now has `mode: require|forbid`, optional `hold`/`hold_reason`, and no persisted `state` or `must_not` (`lib/obligations/model.py:46-69`). `verify()` derives `satisfied`, `holds`, `violated`, and `residual` according to mode (`lib/obligations/verify.py:7-33`).
- **A3 — Verdict partitions are complete and deterministic.** `Verdict` exposes `satisfied`, `holds`, `violated`, `residual`, `unwitnessed`, and `held` (`lib/obligations/model.py:190-204`). Verification sorts obligations by ID and builds `unwitnessed` and `held` only as residual subsets (`lib/obligations/verify.py:15-33`).
- **A4/A5 — Witnesses carry human-readable discharge conditions and canonical refs.** `Witness.description` is mandatory, and the exact ref regex is enforced by `__post_init__` (`lib/obligations/model.py:22-43`). The same pattern is in the contract schema (`contracts/obligations/v1/contract.schema.json:27-40`) and observation schema (`contracts/obligations/v1/observation.schema.json:5-10`).
- **A6 — Observation sources are validated.** Every observation requires a non-empty source, and judgment sources named `anonymous`, `unknown`, or `model` are rejected case-insensitively (`lib/obligations/model.py:93-125`). Tests exercise all three rejected judgment identities (`lib/obligations/tests/test_obligations.py:39-48`).
- **A7 — Revisions retain attributed retirement records.** `Retirement` records the complete obligation, reason, authority, and revision (`lib/obligations/model.py:128-146`). `revise()` requires reason and authority, rejects unknown or reused IDs, retains prior retirement history, and sets `parent_revision` and `supersedes` (`lib/obligations/revise.py:7-28`).
- **A8 — Canonical decoding and preservation are executable.** `canonical()` sorts obligations, witnesses, provenance, and lineage while collapsing `must` whitespace (`lib/obligations/project.py:14-50`). `parse()` reconstructs the contract from canonical or projected views (`lib/obligations/project.py:53-67`). `preserved()` checks presence-or-retirement, mode equality, normalized text equality, witness-set non-removal, and `because` non-removal, returning `Preservation` with deterministic reasons (`lib/obligations/project.py:96-122`). Its header explicitly disclaims deciding natural-language semantic specificity (`lib/obligations/project.py:1-5`).
- **A9/A10 — Views expose each witness outcome and have one deterministic text renderer.** `project()` emits `observed: pass|fail|null` for every witness (`lib/obligations/project.py:70-93`). `render_text()` emits every live obligation’s ID, mode, normalized requirement, hold, witness description and observed outcome, all retirements, and every verdict partition (`lib/obligations/project.py:129-162`).
- **A11 — F1–F15 and additional preservation fixtures are executable.** There are 21 fixtures: F01–F15, five additional preservation cases, and an untrusted-observation case. One generic directory iterator executes verification, revision, projection, exact text, preservation, comparison-order/idempotency, and cold-start round trips without fixture-specific test methods (`lib/obligations/tests/test_obligations.py:88-134`).
- **A12 — Schema validation never silently degrades.** The validator always performs stdlib structural and constructor validation of contracts, views, observations, verdicts, revision additions, and preservation inputs (`scripts/validate_obligations.py:39-115,135-184`). When `jsonschema` exists it additionally validates each instance against the 2020-12 schemas (`scripts/validate_obligations.py:122-134,165-184`). `python3 -S scripts/validate_obligations.py` proved the no-third-party fallback path.
- **A13 — The public API is frozen and vendored byte-identically.** The exact exports are declared in `lib/obligations/__init__.py:8-27`. Every public value has validating `to_json`/`from_json`, plus generic dispatcher functions (`lib/obligations/model.py:38-43,71-90,114-125,141-146,168-187,199-217,223-230`). The vendor checker compares all five modules byte-for-byte (`scripts/check_vendor.py:5-18`).
- **Lifecycle gates are active.** Generic tests run under `make test`; `lib` is linted; `obligations-check` and `vendor-check` are dependencies of `check` (`Makefile:63-85,122-130`).
- **Final validation is green.** Final `make check` tail:

```text
warning: [asymmetric-link] ADR 38 'Warn when the checkout is behind the installed plugin' links to ADR 33 as 'Relates to' but ADR 33 has no link back to ADR 38 [/private/tmp/obligations-contract/doc/adr/0038-warn-when-the-checkout-is-behind-the-installed-plugin.md]

Found 0 error(s), 77 warning(s), 0 info(s)

All checks passed.
```

## Final public API signatures

```text
Witness(kind: WitnessKind, ref: str, expect: Outcome, description: str)
Obligation(id: str, mode: Mode, must: str, witnesses: tuple[Witness, ...], because: tuple[str, ...] = (), hold: Hold | None = None, hold_reason: str | None = None, severity: str | None = None)
Observation(kind: WitnessKind, ref: str, outcome: Outcome, source: str, at: str, payload: Mapping[str, Any] | None = None)
Contract(contract_id: str, revision: int, obligations: tuple[Obligation, ...], provenance: tuple[str, ...], parent_revision: int | None = None, supersedes: tuple[str, ...] = (), retired: tuple[Retirement, ...] = ())
Retirement(obligation: Obligation, reason: str, authority: str, at_revision: int)
Verdict(satisfied: tuple[str, ...] = (), holds: tuple[str, ...] = (), violated: tuple[str, ...] = (), residual: tuple[str, ...] = (), unwitnessed: tuple[str, ...] = (), held: tuple[str, ...] = ())
Preservation(ok: bool, reasons: tuple[str, ...] = ())

verify(contract: Contract, observations: Iterable[Observation], trusted: Callable[[Observation], bool]) -> Verdict
revise(contract: Contract, *, add: Iterable[Obligation] = (), retire: Iterable[str] = (), reason: str, authority: str) -> Contract
project(contract: Contract, verdict: Verdict, observations: Iterable[Observation] = ()) -> dict[str, Any]
parse(view: Mapping[str, Any]) -> Contract
canonical(contract: Contract) -> dict[str, Any]
preserved(before_view: Mapping[str, Any], after_view: Mapping[str, Any]) -> Preservation
render_text(view: Mapping[str, Any]) -> str
to_json(value: Any) -> dict[str, Any]
from_json(value_type: type[T], value: Mapping[str, Any]) -> T

Witness.to_json() -> dict[str, Any]
Witness.from_json(value: Mapping[str, Any]) -> Witness
Obligation.to_json() -> dict[str, Any]
Obligation.from_json(value: Mapping[str, Any]) -> Obligation
Observation.to_json() -> dict[str, Any]
Observation.from_json(value: Mapping[str, Any]) -> Observation
Contract.to_json() -> dict[str, Any]
Contract.from_json(value: Mapping[str, Any]) -> Contract
Retirement.to_json() -> dict[str, Any]
Retirement.from_json(value: Mapping[str, Any]) -> Retirement
Verdict.to_json() -> dict[str, Any]
Verdict.from_json(value: Mapping[str, Any]) -> Verdict
Preservation.to_json() -> dict[str, Any]
Preservation.from_json(value: Mapping[str, Any]) -> Preservation
```

## Claims refuted

- **The Phase 1 persisted `state` design was incorrect.** It has been removed; status is now exclusively derived by `verify()` (`lib/obligations/model.py:46-55`, `lib/obligations/verify.py:7-33`).
- **The Phase 1 combined `must`/`must_not` representation was incorrect.** It has been replaced by distinct `require` and `forbid` obligations (`lib/obligations/model.py:46-55`).
- **The Phase 1 `bool | reasons` preservation result was underspecified.** It has been replaced by frozen `Preservation(ok, reasons)` and a canonical decoder/normal form (`lib/obligations/model.py:207-217`, `lib/obligations/project.py:30-67,96-122`).
- **The Phase 1 optional-schema-validation residual was valid.** It is resolved by the unconditional stdlib validation path, independently run with `python3 -S`.

## Open/blocked

- No implementation item is blocked.
- The acceptance contract’s independent reviewer gate is parent-owned. I sent the supervisor a progress update requesting the scheduled independent review; this lane did not launch or impersonate a reviewer.
- The 77 ADR warnings are pre-existing and outside Lane A’s permitted files. They do not fail `make check`.

## Files changed

- `Makefile`
- `contracts/README.md`
- `contracts/obligations/v1/contract.schema.json`
- `contracts/obligations/v1/observation.schema.json`
- `contracts/obligations/v1/verdict.schema.json`
- `contracts/obligations/v1/fixtures/F01-empty.json`
- `contracts/obligations/v1/fixtures/F02-require-pass.json`
- `contracts/obligations/v1/fixtures/F03-require-opposite.json`
- `contracts/obligations/v1/fixtures/F04-wrong-kind.json`
- `contracts/obligations/v1/fixtures/F05-wrong-ref.json`
- `contracts/obligations/v1/fixtures/F06-forbid-observed.json`
- `contracts/obligations/v1/fixtures/F07-forbid-absent.json`
- `contracts/obligations/v1/fixtures/F08-judgment-separation.json`
- `contracts/obligations/v1/fixtures/F09-order-independent.json`
- `contracts/obligations/v1/fixtures/F10-duplicate-idempotent.json`
- `contracts/obligations/v1/fixtures/F11-revision.json`
- `contracts/obligations/v1/fixtures/F12-preserved-drop.json`
- `contracts/obligations/v1/fixtures/F13-preserved-text-change.json`
- `contracts/obligations/v1/fixtures/F14-forbid-payload-ignored.json`
- `contracts/obligations/v1/fixtures/F15-cold-start.json`
- `contracts/obligations/v1/fixtures/P01-additions-preserved.json`
- `contracts/obligations/v1/fixtures/P02-witness-loss.json`
- `contracts/obligations/v1/fixtures/P03-because-loss.json`
- `contracts/obligations/v1/fixtures/P04-whitespace-normalized.json`
- `contracts/obligations/v1/fixtures/P05-explicit-retirement.json`
- `contracts/obligations/v1/fixtures/V01-untrusted.json`
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

## Requests to other lanes

- **Lane B:** Import only the frozen public symbols listed above. Supply only trusted observations to `project()` so `observed` remains the trusted agent-facing result. Reference the view schema as `contracts/obligations/v1/contract.schema.json#/$defs/view`.
- **Lane C:** Mirror the frozen constructors, six verdict partitions, `Preservation`, canonical normalization, preservation reasons, and exact renderer output. Iterate all 21 fixture files; do not hard-code only F01–F15.

## Residual risks

- Independent reviewer adjudication is pending in the parent-owned review phase.
- `project()` intentionally does not apply trust itself; callers must pass the trusted observation subset used by `verify()`. Fixtures demonstrate this with `V01-untrusted.json`.
- If both `pass` and `fail` observations exist for the same exact witness key, projection deterministically prefers the witness’s expected outcome. No adjudicated fixture defines conflicting trusted observations; caller policy may need to reject such evidence upstream.
- ADR doctor reports 77 pre-existing warnings, zero errors.
- `git diff --cached --name-only` was empty. No files are staged.