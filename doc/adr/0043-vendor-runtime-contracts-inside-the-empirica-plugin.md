---
number: 43
title: "Vendor runtime contracts inside the Empirica plugin"
status: accepted
date: 2026-09-20
tags: [empirica, packaging, contracts, claude, codex, pi]
links:
  - target: 30
    kind: Amends
  - target: 42
    kind: Amends
---

# Vendor runtime contracts inside the Empirica plugin

## Context and Problem Statement

Empirica's MCP process loaded its v2 schemas from the marketplace repository root. Claude Code
copies only `plugins/empirica/` into its installation cache, so the installed process crashed before
registering tools. Codex and Pi packaging must not depend on a host preserving paths above the
plugin either.

## Decision Drivers

* The installed plugin must be self-contained on every host and install mode.
* `contracts/empirica/v2/` remains the repository source of truth.
* Shipped data must not drift or be edited independently.
* Runtime resolution must fail closed without a development-tree fallback.

## Considered Options

1. Move the contract source of truth into the plugin (rejected: broad reference churn).
2. Vendor the runtime closure and enforce byte identity (chosen: matches obligation vendoring).
3. Symlink to the repository contracts (rejected: host and install-mode dependent).
4. Copy through wheel/package-data machinery (rejected: plugin installation is a raw directory copy,
   not a Python build).

## Decision Outcome

The seven files loaded by `application/protocol.py` and `adapters/public_tools.py` are generated into
`plugins/empirica/vendor/contracts/empirica/v2/`. Runtime code reads only that plugin-relative copy.
`make vendor-contracts` regenerates it; `make vendor-check`, composed into `check-static`, compares
all bytes and rejects missing, changed, or extra files. The public-tools artifact is code-derived:
change the projection in `adapters/public_tools.py` and the root `public-tools.json` in lockstep,
then run `make vendor-contracts`; import-time equality and vendor checks reject either half alone.

Fixtures and validator-only schemas are not shipped. Repository validators continue to read the
root source of truth, while an isolated-copy test starts the actual MCP server with no parent
`contracts/` directory.

## Consequences

* Good, because installed Claude, Codex, and Pi packages no longer depend on marketplace layout.
* Good, because source and shipped truth cannot diverge unnoticed.
* Bad, because seven JSON files are duplicated in Git and must be regenerated after source changes.
* Neutral, because the duplicate is data, not a second policy implementation.

## Confirmation

`make vendor-check`, `make contract-check`, the isolated MCP-copy regression, and installed-host
MCP discovery prove byte identity, source validity, and runtime self-containment.
