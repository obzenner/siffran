---
number: 37
title: "Give agents a handle-based route builder to match the driving surface"
status: accepted
date: 2026-09-08
tags:
  - workflow
  - api
  - usability
links:
  - target: 30
    kind: Refines
  - target: 20
    kind: relatesto
---

# Give agents a handle-based route builder to match the driving surface

## Context and Problem Statement

An agent driving a run holds only the opaque `run_id` handle the run was minted with. The knowledge
builders it needs — `build_graph_request`, `build_research_request`, `build_spike_request` — take that
handle directly. But the workflow-critical **route announcement** the skill tells the agent to record
(`SKILL.md`: "record the announcement through `adapters.claude.route.build_route_announcement_request`")
is **payload-shaped**: it calls `context_from_payload`, which requires a hook payload carrying a
`session_id`. An agent with only a handle cannot satisfy it — it raises
`SelectorError("session_id must be a non-empty string")`. Dogfooding hit this exactly: the skill's
Step-1 instruction is uncallable as written by the very actor it instructs.

The `route`/`restore`/`spawn`/`completion` builders are payload-shaped because they are the *hook*
adapter's surface (a hook always has a payload, and derives the selector from it). The route
announcement is the one of these an *agent* must issue directly, mid-run, from a handle. The service
already accepts the raw `ObserveAction{run_id, action:{kind:"route", reason}}` — only a handle-shaped
builder is missing.

Two smaller papercuts travel with it: the skill points agents at the payload-shaped builder, and the
knowledge-store's "not a git repository" fault (raised when the bridge is invoked from a non-repo
cwd) does not say that the workspace git repo must be the working directory.

## Considered Options

- **A full argparse run-driving CLI.** Best long-term ergonomics, but a large new surface; out of
  scope here. Deferred.
- **Make the payload builders accept a handle too.** Muddies the hook-vs-agent boundary that keeps
  the payload builders honest.
- **Add a handle-based route builder alongside the knowledge builders.** Chosen — it mirrors
  `build_graph_request`, closes the exact trap, and leaves the hook boundary intact.

## Decision Outcome

- Add `knowledge.build_route_request(run_id, reason, *, correlation_id=None)`, mirroring
  `build_graph_request`, emitting `ObserveAction{kind:"route", reason}` from a handle.
- Update `SKILL.md` to point agents at the handle-based builder (and note the raw `ObserveAction`
  equivalent), keeping the payload-shaped builder as the hook adapter's path.
- Make the knowledge-store git fault name the requirement: the empirica knowledge store needs the
  workspace git repository as the working directory.

The full run-driving CLI remains future work.
