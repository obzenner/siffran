---
number: 65
title: Observe runs through a plugin-owned read-only read model served on loopback
status: accepted
date: 2026-10-06
tags:
- empirica
- observer
- ui
- state
links:
- target: 31
  kind: Amends
---

# Observe runs through a plugin-owned read-only read model served on loopback

## Context and Problem Statement

A human wants to see the work in the Empirica store: every project, run and claim, and how they relate. The store was built for the plugin, which answers one run at a time through `GetRun` and `GetArgument`. A run's facts are spread over three places:

- its operational state (`<home>/projects/<p>/runs/<r>/gen-<n>/run.json`);
- its knowledge artifacts (`refs/empirica/artifacts/*` in a repository, ADR-31);
- the workspace its spikes ran against.

The interim UI had its own reader, which re-derived claim states and audit currency. That is a second owner of facts the plugin decides. It also could not find a run's repository: a project id is a one-way hash of a host selector (ADR-31). Claude and Codex derive the selector from the repository's Git common directory, but Pi derives it from a hash of the working directory. No reader could map a Pi project back to its repository.

## Decision Drivers

* One owner per fact. The plugin decides claim states, blockers, audit currency and freshness, so a reader shows them and never decides them again.
* Reading must not change anything: not the store, not the refs, not the index, not the workspace.
* A run the reader cannot fully read still appears, and says why.
* The UI ships with every host's plugin, works offline, and is reachable from this machine only.
* Finding a project's history must not depend on where, or on which host, the observer is started.

## Considered Options

* **Keep the UI's own reader.** Rejected: it is a second implementation of the plugin's derivations, and it drifts.
* **Answer the UI with `GetRun` and `GetArgument` per run.** Rejected: these are author views of one active run behind host profiles. They have no store-wide enumeration, and they are not shaped for a display.
* **A read model inside the plugin, published as a contract, served on loopback by the plugin itself.** Chosen.

## Decision Outcome

### The read model

- **Producer:** `application/observer.py`, which uses the pure projection in `core/observer.py`, is the only producer of `contracts/empirica-ui-view/v1`.
  - `view.schema.json` is the store summary.
  - `run.schema.json` is one run's detail, additive to its summary row.
- **Values:** every value comes from the plugin's own derivations through the same read paths the plugin writes with. Enumerations are held equal to their owners by `scripts/validate_ui_view.py`.
- **New classifications:** only two, `evidence` and `review`, each a pure function of plugin facts.
- **Partial reads:** a run read only in part carries a `diagnostic` and the facts that could be read.
- **Versions:** each run has a `version` that binds everything its projection read, including the observation of the files its active spikes depend on. The view's `token` binds every version.
- **Fixtures:** the demo fixtures are runs produced by the plugin's own v2 service, vendored into the plugin.

### Serving

- **Server:** `adapters/observer/server.py` (`cli.py serve`) uses the standard library only.
- **Network:** it binds to 127.0.0.1 and serves GET and HEAD only. A `Host` header other than `127.0.0.1`, `localhost` or `empirica.localhost` with this server's own port is refused, which defeats DNS rebinding. The CLI prints and opens `http://empirica.localhost:<port>/app/` (browsers resolve `*.localhost` to loopback) and prints the `127.0.0.1` address as a fallback; the port logic is unchanged.
- **Origin:** it sends no CORS headers and a Content-Security-Policy that allows only its own origin.
- **Files:** static files come from an allowlist under `plugins/empirica/ui`. Symlinks out of the tree and dotfiles are refused.
- **Endpoints:**
  - `/api/view` answers 304 when nothing changed. A poll costs one ref listing per repository and one directory check per recorded location. Git resolves each recorded path once per server, not once per poll.
  - `/api/about` names which store a server observes, as a digest. A second `serve` of the same store on the same port (8765 by default) opens the running one instead of starting another. The digest identifies the store; it does not authenticate the server, which suits the single-user machine the store assumes.

### Finding a run's history: the location record

When the bridge creates a run (StartRun, on every host), it records where that run works, in `adapters/state/locations.py`:

```
<home>/projects/<project_id>/project.json                          {"repo": "<working tree root>"}
<home>/projects/<project_id>/runs/<run_id>/gen-<n>/workspace.json  {"workspace": "<directory>"}
```

- **What reads it:** the observer reads these files on every poll, so it finds every project's history and each run's workspace whatever selector the host used.
- **What it amends in ADR-31:** a project id stays a one-way hash. These files sit beside the strict run state, not inside it, and no run decision reads them. The run document and its schema are unchanged.
- **Failure:** a failed write never fails the run.
- **Runs without a record:** a run whose record could not be written has none. For it, `--repo` (or the current directory's repository) is matched through the plugin's identity, which works for Claude and Codex.

### Consequences

* **Good:** the UI shows exactly the plugin's judgement, and a change to a derivation reaches the UI without a UI change.
* **Good:** one observer per store on the default port, launched from any host (`/empirica:ui` in Claude, `/empirica-ui` in Pi, `make ui` and the CLI everywhere). When something else holds the port, each launch gets its own.
* **Bad:** a project's record names its latest repository. Linked worktrees share one artifact namespace (ADR-31), so the history is unaffected, and each run's workspace is recorded per run.
* **Bad:** a state that is not exactly the current state document is shown as `unreadable`, with nothing read from it (one category: no age, no partial decoding).
