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

The former exact `0.50.0` dependency is an honest historical qualification fact, not a current requirement. New upstream releases require the deterministic matrix and a contract review before admission; `host-profiles.json` is the reviewed decision and the checked-in inventories under `plugins/empirica/adapters/pi/compat/` are its evidence, which `make contract-check` requires to be exactly the same set of versions. Existing 4.0 receipts cannot carry the new field and are not promoted by migration. The upper bound `<1.1.0` is admitted on the strength of the reviewed Pi 1.0 extension API, the Pi API typecheck, and the Pi 1.0.0 + pi-subagents 0.74.0 native receipt. Until that receipt exists the widening is provisional, and if it fails the bound returns to `<0.90.0`. `make empirica-host-live-check` enforces this: whenever the Pi interval ends above 1.0.0 the Pi receipt must come from Pi 1.0.0 or later, and a receipt from an older Pi fails naming the missing 1.x receipt.
