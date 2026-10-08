---
number: 52
title: Separate harness compatibility from receipt provenance
status: accepted
date: 2026-09-23
tags:
- empirica
- hosts
- compatibility
- receipts
- pi
- audit
links:
- target: 42
  kind: Amends
- target: 51
  kind: Amends
- target: 69
  kind: Amended by
---

# Separate harness compatibility from receipt provenance

## Context and Problem Statement

Empirica used the one harness version that qualified a host profile as both the profile's evidence
baseline and the only version accepted by installed-host receipt verification. Claude Code updates
itself independently, so a patch update from 2.1.278 to 2.1.280 was rejected before the retained
trace's capabilities could be evaluated. Receipt capture also copied the configured expected version
instead of deriving it from the retained native version output. This conflated compatibility policy
with provenance and could misstate the runtime that produced a receipt.

A production Pi trace exposed a separate ordering interaction: the host-owned canonical auditor is
read-only, but generic `pi-subagents` acceptance inference classified the original ambiguous
`audit` call before Empirica replaced it with the bound dossier. The child produced a valid verdict
and was then rejected for lacking writer evidence.

## Decision Drivers

* Preserve the exact observed harness version in every installed-host receipt.
* Do not make equality with one qualification build the compatibility policy.
* Keep compatibility bounded and fail closed outside a reviewed range.
* Continue requiring the real hook/tool/audit lifecycle; a version range alone proves nothing.
* Keep the controlled `pi-subagents` dependency pinned until its integration contract is stable.
* Prevent generic writer completion policy from superseding Empirica's read-only audit contract.

## Considered Options

1. Keep exact harness pins and repeatedly update them (rejected: operationally brittle and confuses
   evidence baseline with compatibility).
2. Accept every future harness version (rejected: unbounded compatibility is an unsupported claim).
3. Admit reviewed half-open compatibility ranges, retain exact observed provenance, and require the
   structural capability trace (chosen).
4. Make the auditor emit generic writer evidence (rejected: false semantics and conflicting output
   contracts).

## Decision Outcome

Each registry profile retains an exact qualification `version` and adds a half-open
`compatibility` interval. Claude is admitted for `>=2.1.278,<2.2.0`; Pi is admitted for
`>=0.84.1,<0.85.0`. These ranges are policy bounds, not evidence by themselves. The installed-host
verifier still requires the full native parent/child lifecycle, durable state, identity separation,
verdict, guarded convergence result, and artifact digests.

Receipt capture parses the retained native version output, rejects malformed or out-of-range
versions, and records that exact value. Verification independently reparses the retained output and
requires equality with the receipt. Claude's normal `X.Y.Z (Claude Code)` output and plain semantic
versions are accepted; arbitrary text is not.

For Pi, the adapter adds a runtime-owned `acceptance: {level: "none"}` only after rejecting every
author-supplied auditor override and resolving the canonical packaged identity. This prevents
extension-ordering from assigning writer evidence gates to the bound read-only auditor. Empirica's
private verdict admission and guarded convergence decision remain unchanged.

## Consequences

* Harness patch releases inside a reviewed range no longer require a Siffran release solely to pass
  version equality.
* Receipts remain reproducible and identify the exact runtime that generated them.
* New minor versions remain unsupported until the compatibility range is reviewed and expanded.
* `pi-subagents@0.50.0` remains exact because it is a controlled runtime dependency, not an
  externally updating harness.
* A live trace is still mandatory; compatibility is not inferred from semver alone.

## Confirmation

`make empirica-host-receipt-unit-check` covers compatible patch releases, incompatible boundaries,
native-output parsing, and provenance mismatch. The Pi gate suite asserts that only the canonical
host-owned auditor receives the explicit read-only acceptance exemption. `make contract-check`
validates ordered compatibility ranges and vendor parity. Full confirmation is `make check`, followed
by new installed-host receipts on the exact observed harness versions before release.

## Amendment (2026-10)

### Changed Decision

The separation of policy and provenance now applies to pi-subagents as well as to Pi. The Pi host interval is a reviewed harness range (`>=0.84.1,<1.1.0`); pi-subagents support is an exact list of reviewed versions under one policy id, `pi-subagents-foreground-audit-v1`, embedded in the profile id (`pi@0.84.1+pi-subagents-foreground-audit-v1`). A range or a `latest` tag never proves compatibility, and there are no compatibility variants: every reviewed version receives the same audit-bound policy. Every 4.1 Pi receipt records the exact Pi version and a `subagents_runtime` object (package, version, owner path, package root, preflight path, policy id) derived from retained state evidence. A missing, mixed, or out-of-policy value is rejected.

### Changed Consequences

The former exact `0.50.0` dependency is an honest historical qualification fact, not a current requirement. New upstream releases require the deterministic matrix and a contract review before admission; `host-profiles.json` is the reviewed decision and the checked-in inventories under `plugins/empirica/adapters/pi/compat/` are its evidence, which `make contract-check` requires to be exactly the same set of versions. Existing 4.0 receipts cannot carry the new field and are not promoted by migration. The upper bound `<1.1.0` is **provisional** at the time of this amendment: it rests on the reviewed Pi 1.0 extension API and the Pi API typecheck, and it becomes supported only when a native receipt from Pi 1.0.0 with a reviewed pi-subagents version (planned: 0.75.0, qualification step A9b) exists. No such receipt exists yet; if that run fails the bound returns to `<0.90.0`. `make empirica-host-live-check` enforces this: whenever the Pi interval ends above 1.0.0 the Pi receipt must come from Pi 1.0.0 or later, and a receipt from an older Pi fails naming the missing 1.x receipt.

## Amendment (2026-10-08): latest hosts, the 1.x line, and the audit context

Supersedes the provisional `<1.1.0` upper bound above; the 2026-10 amendment's history is left as written.

### Changed Decision

- **Pi.** The reviewed Pi interval is `>=1.0.0,<2.0.0`: the user's ruling is that Empirica and Methodologist support the latest Pi, capped by version 1, and (ADR-0069 G9-R1) nothing older than 1.0.0. The qualified Pi version moves to the latest release, `1.1.0`. The Pi API typecheck proves both Pi adapters' mirrors against 1.0.0 and 1.1.0, and no in-range Pi declares or emits `tool_result.error`, so `isError` is the only failure signal and the dead `error` branch is removed.
- **Native receipt.** (Superseded by ADR-0069 G6-R1: the floor is the profile's `version`, not a constant.) The latest release of the widened line must supply the native receipt: `_WIDENED_END_RELEASE` was `{"pi": "1.1.0"}`. While the Pi interval is `>=1.0.0,<2.0.0` the Pi receipt must come from Pi 1.1.0 or later; a receipt from Pi 1.0.x fails naming the required Pi >= 1.1.0 receipt, and a receipt from Pi 2.0.0 is outside the interval. The widening stays provisional until that receipt exists.
- **Claude Code.** The interval is unchanged (`>=2.1.278,<2.2.0`). The qualified version and profile id move to the installed latest, `claude-code@2.1.294`.
- **pi-subagents.** `0.76.1` joins `reviewed_versions` (`0.50.0, 0.64.0, 0.74.0, 0.75.0, 0.76.1`) with its generated inventory and preflight fixture. The four older preflight fixtures were re-captured because `definitionDigest` binds the reviewed auditor definition and had drifted; the digest stays unmasked. The repository development pin is `pi-subagents@0.76.1`.
- **Audit context.** pi-subagents resolves an omitted `context` from the operator's `defaultSubagentContext` (0.64.0 and later), which may be `fork`; the auditor would then inherit the author's transcript. Every reviewed schema declares `context`, so the canonical audit launch sends `context: "fresh"`, and the preflight seam refuses a launch or a preflight response whose context is not `fresh`. The author still may not send `context` (`canonicalAuditorInputError`).
- **Run-start version check.** No run-start refusal for an out-of-range host version is added: compatibility is enforced at receipt capture and verification so harness patch releases need no Siffran release. The runtime refusal that exists (an unreviewed pi-subagents version, `unreviewed_action: refuse`) stays. *Superseded by ADR-0069 (G5-R2): the refusal is now for a version outside the supported range.*

### Changed Consequences

* Existing receipts for Pi 1.0.x and earlier no longer release the interval's end; a Pi 1.1.0 receipt (native run with a reviewed pi-subagents) is required (qualification step A9b).
* Methodologist declares the same Pi interval and its mirror is now typechecked, which found and fixed a handler return type that Pi does not accept.

### Confirmation

`scripts/tests/test_empirica_live_receipts.py` (1.1.0 accepted; 1.0.x inside the interval but refused by the 1.1.0 receipt floor; 0.x and 2.0.0 outside the interval), `make empirica-pi-api-typecheck` (both adapters, Pi 1.0.0 and 1.1.0), `make empirica-subagents-matrix` (five pi-subagents versions), and the Pi suite's `EHB-AUDLAUNCH-02/03`, `EHB-AUDRES-04` and `EHB-INT-03` tests.

## Amendment (2026-10-08): profile ids carry no version, and pi-subagents is admitted in a range

[ADR-0069](0069-admit-hosts-and-audit-runtimes-by-range-and-observed-capability.md) supersedes two statements above and leaves the rest. The profile id is `<host_id>` or `<host_id>+<policy_id>` and never carries a version (`pi+pi-subagents-foreground-audit-v1`, not `pi@1.1.0+...`). pi-subagents support is the range `subagents_compatibility` `[minimum, maximum_exclusive)`, not an exact list of reviewed versions: a version with a checked-in compat record is reviewed, any other version in the range is admitted by its own parameter schema and live preflight and recorded `reviewed: false`, and a version outside the range is refused. A receipt's `subagents_runtime` records `reviewed`. "Every reviewed version receives the same audit-bound policy" still holds.
