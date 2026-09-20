---
number: 44
title: "Implement Claude Code's current MCP revision"
status: accepted
date: 2026-09-20
tags: [empirica, mcp, claude, codex]
links:
  - target: 42
    kind: Amends
  - target: 43
    kind: Depends on
---

# Implement Claude Code's current MCP revision

## Context and Problem Statement

Empirica's unreleased stdio server claimed MCP `2025-11-25` but echoed any client-requested version,
so it did not actually perform that revision's version negotiation. The independently published MCP
specification has a newer `2026-07-28` revision, but Claude Code 2.1.278 does not speak it: an
installed-plugin health check opens with the initialization handshake and rejects a current-only
server before tool discovery. Empirica's promoted Claude profile must follow the newest protocol
that its actual host client supports, not an incompatible newer specification.

## Decision Drivers

* Empirica must connect through current Claude Code before it can claim Claude support.
* Implement the host-supported revision exactly rather than echoing arbitrary versions.
* Do not carry two eras when the supported host uses one.
* Re-evaluate the protocol when Claude Code adopts a newer revision.

## Considered Options

1. Keep echoing the client version (rejected: false negotiation).
2. Require MCP `2026-07-28` now (rejected: Claude Code 2.1.278 fails to connect).
3. Implement only Claude Code's current `2025-11-25` lifecycle (chosen).
4. Implement both revisions (rejected: no supported host currently needs both).

## Decision Outcome

The server implements the `2025-11-25` initialization lifecycle used by Claude Code: it accepts an
`initialize` request, always returns the latest protocol version the server actually supports,
advertises the stable tools capability and server identity, ignores the subsequent initialized
notification, and serves `ping`, `tools/list`, and `tools/call` in that negotiated era. Invalid
initialization and tool calls return closed JSON-RPC errors.

The server does not implement `2026-07-28` metadata, `server/discover`, `resultType`, or cache fields
until the promoted Claude host supports that revision. This is host compatibility, not an assertion
that `2025-11-25` is the newest independently published MCP specification.

## Consequences

* Good, because the shipped plugin connects to the real promoted Claude host.
* Good, because version negotiation no longer claims support for arbitrary client input.
* Good, because there is one protocol path and one response shape.
* Bad, because updating Claude's supported MCP revision requires a deliberate server update and new
  installed-host evidence.

## Confirmation

Unit tests cover exact and fallback negotiation, invalid initialization, tool discovery, and an
isolated plugin copy. `claude --plugin-dir plugins/empirica mcp list` on Claude Code 2.1.278 must
report `plugin:empirica:empirica` connected. The lifecycle contract is checked against
`https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle`.
