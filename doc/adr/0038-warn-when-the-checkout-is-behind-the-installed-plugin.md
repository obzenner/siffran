---
number: 38
title: "Warn when the checkout is behind the installed plugin"
status: accepted
date: 2026-09-08
tags:
  - tooling
  - usability
links:
  - target: 33
    kind: relatesto
---

# Warn when the checkout is behind the installed plugin

## Context and Problem Statement

The plugin's runtime architecture moved between 0.x and 1.x (operational state and Git-backed
knowledge, adapters/core/application). Dogfooding started on a checkout of an old extraction branch
(`0.5.2`) while the installed, *active* plugin was `1.1.0` — so any edit to the checked-out source
would have targeted code that no longer matches what runs, with nothing to signal the mismatch. The
1.1.0 source was in the repo the whole time (on `main`); the failure was purely that the working tree
sat on a stale branch and nothing said so.

`make status` reads only the checkout's own `plugin.json` versions; nothing reads
`~/.claude/plugins/installed_plugins.json`, so the "you are editing a version behind what runs"
condition is invisible.

## Considered Options

- **A CONTRIBUTING note only.** Cheap, but a doc no one is told to read does not prevent the mistake.
- **Hard-fail `make check` on a behind-checkout.** Rejected: CI and fresh clones do not have an
  installed cache, and a stale-but-intentional checkout is legitimate; this is advice, not a gate.
- **An advisory guard folded into `make status`.** Chosen.

## Decision Outcome

Add `scripts/check_installed_version.py`: read `~/.claude/plugins/installed_plugins.json` (honouring
its real layout), compare each installed plugin's version to the checkout's `plugin.json`, and print a
one-line advisory to stderr when the checkout is *behind* the installed version. It exits 0 always and
no-ops silently when the installed file is absent (CI, fresh clones) or unparsable, so it can be wired
into `make status` without ever failing a build. It never mutates anything.
