---
number: 52
title: "Separate harness compatibility from receipt provenance"
status: accepted
date: 2026-09-23
tags: [empirica, hosts, compatibility, receipts, pi, audit]
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
