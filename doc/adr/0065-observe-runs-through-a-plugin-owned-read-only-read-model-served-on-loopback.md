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
- target: 70
  kind: Amended by
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
  - `summary.schema.json` is the store summary, `row.schema.json` one run's row and `page.schema.json` a page of one-line rows.
  - `run.schema.json` is one run's detail, additive to its summary row.
- **Values:** every value comes from the plugin's own derivations through the same read paths the plugin writes with. Enumerations are held equal to their owners by `scripts/validate_ui_view.py`.
- **Facts the read model publishes so that the UI decides none:**
  - reason text by context (an active run's "waiting for the human", an ended run's "unanswered when this run stopped"), from the one renderer the author view uses;
  - the audit as four facts (verdict, whether it still covers the current argument, whether a re-audit is pending, independence);
  - each claim's `assurance` (approved, covered by a current passing audit, no pending re-audit);
  - `attention` (`needs_you`, `not_ended`, `ended`), `next` (the one obligation a row names) and `outstanding` (every obligation, with who acts and where), built from the obligations the author view itself renders; liveness is never inferred. A human's pending action is the human's: a configuration the agent has asked for and a host can show its human (`core.governance.human_wait`: open, closed unanswered, or rejected, since after a rejection the human says how to go on, as the hosts' turn end settles with `human_wait`) and the consent to end with the scope frozen are `needs_you`; a configuration the agent has yet to ask for, or that nobody can be asked about, and an end the human declined (`terminal_case` `declined`: shown, no consent) are the agent's (`next_turn`), so a declined stop never reads as the human's next step. `core.governance.human_wait` is the one predicate of who acts on a configuration: the run view publishes it (`governance.human_wait`), the observer's standing reads it, and both hosts' turn ends read the view's answer and add only their own approval ingress and the sole-blocker check; the shared cases (`adapters/pi/test/fixtures/turn-end-predicates.json`) hold all three to every governance state;
  - a decision's `status`, history events with their subject and outcome, `governance` (ceilings in force and used, proposed ceilings only while a proposal waits, interactions left);
  - unique project labels, attempt families in order of generation, the run's `goal` and the host-recorded `session`;
  - `coverage`: a run that was not read has null facts, never empty or zero ones.
- **New classifications:** `evidence`, `review` and `assurance`, each a pure function of plugin facts.
- **Partial reads:** a run read only in part carries a `diagnostic` and the facts that could be read.
- **Versions:** each run has a `version` that binds everything its projection read, including the observation of the files its active spikes depend on. The view's `token` binds every version.
- **Fixtures:** the demo fixtures are runs produced by the plugin's own service, vendored into the plugin.

### The UI

- **Ownership rule:** the plugin's core owns trust, scope, obligations, next steps, reason text, currency and governance; the observer read model owns store I/O, coverage, project identity and families of attempts; the hosts own host-observed facts (the session locator); the UI owns presentation only. It formats, lays out, navigates, and groups by the categories the core publishes. `ui/tests/known-patterns.test.mjs` is a regression guard against the derivations earlier versions took in JavaScript, not a proof; the proof is the generated invariant `test_INVARIANT_the_observer_says_what_the_gate_and_author_view_say` (`plugins/empirica/tests/v2/test_observer_agreement.py`), which runs the real service.
- **Two time bases, and presentability (U4):** a run that ended is read on two named bases. The snapshot recorded when it stopped feeds only the at-stop facts (what the stop left unresolved, its scope and basis); every current fact (the audit and its currency, each claim's state, review and assurance, the freshness of its proof, the files that changed) comes from the live snapshot through the owners the author view uses, so a converged run whose files changed afterwards shows its audit not current and its claim not assured, as `GetRun` does. Whether a human is shown a question is its own fact, owned by the confirmation batch and its `prompt_error` (`core.confirmation.presentable`, the predicate both host mediators use); it is not the gate's first blocking reason, so a presentable batch beside agent work is `needs_you` at `next_report`, not "later". A host that has not yet looked for a dialog (`unobserved`) is told as `if_available`, a conditional place that is not `needs_you`: the observer does not predict a surface the host has not shown. The run's row also carries what its host recorded of the session (interactive, delegated; unknown stays null; never liveness). `tests/v2/test_observer_agreement.py` takes the owners as its oracles: `GetRun` and `GetArgument` on the live snapshot, and the real `HostGovernance` and `HostConfirmation` mediators.
- **Shape:** a list of runs under the core's attention groups (Needs you, Not ended, Ended, and runs not read in full), each row saying the goal, attempt, project, outcome, audit, what it owes next and who acts and where, and the session with a copy button; beside it, one run's workspace: Now or Outcome, claims that open in place to their proof, the support graph as an indented list, governance, attempts side by side, history and changed files. The URL selects a run and optionally a claim, nothing else.
- **No picture:** there is no whole-store graph, no glyph-only encoding (a word is always shown; an icon only repeats it), no hint layer and no second mini-app.
- **Generated, not mirrored:** the UI's value lists (`ui/enums.json`) are generated from the contract's schemas; a test regenerates them and fails on drift, and every state vocabulary is checked against them.
- **Freshness:** the page asks whether the store changed every few seconds (ETag), not while the tab is hidden and later after each failed answer; it shows when the observer last answered; a run's document that failed is asked again with backoff and by a Retry button.
- **Checks:** `make check-ui` (audit and node tests, including real-service fixtures) and `make ui-browser` (the inbox, narrowing, one run's sheet, search, the keyboard, history, safe text, motion, recovery and hidden-tab checks at 1440×900 and 1024×768 on the demo, a live store and generated stores of 1 000 and 10 000 runs). `ui/UI.md` is the one document.

### Serving

- **Server:** `adapters/observer/server.py` (`cli.py serve`) uses the standard library only.
- **Network:** it binds to 127.0.0.1 and serves GET and HEAD only. A `Host` header other than `127.0.0.1`, `localhost` or `empirica.localhost` with this server's own port is refused, which defeats DNS rebinding. The CLI prints and opens `http://empirica.localhost:<port>/app/` (browsers resolve `*.localhost` to loopback) and prints the `127.0.0.1` address as a fallback; the port logic is unchanged.
- **Origin:** it sends no CORS headers and a Content-Security-Policy that allows only its own origin.
- **Files:** static files come from an allowlist under `plugins/empirica/ui`. Symlinks out of the tree and dotfiles are refused.
- **Endpoints:**
  - `/api/summary` (and a run's sheet) answers 304 when nothing changed. A poll costs one ref listing per repository and one directory check per recorded location. Git resolves each recorded path once per server, not once per poll.
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

### Amendment (2026-10-12): the observer at volume

- **What changes:** the observer becomes an incremental index (`application/observer.py`): a poll compares each run's stamp (the source's own `stamp`, else its state and history revisions and its timeline) with the one it was projected at, and re-projects only the runs that changed. A poll given a time budget (the adapter's monotonic clock; the application reads none) leaves unreached runs `pending` in the coverage, so a cold start grows `read` against `runs`.
- **New endpoints, same safety:** `GET /api/summary` (facets, aggregates within `since`, coverage, the runs that need a human, columnar universe marks; ETag, 304, gzip), `GET /api/runs` and `GET /api/search` (one-line rows, at most 200, paged by a cursor in standing-then-key order) and `GET /api/run?key=` (the detail with `audits`, `dropped` and `decisions`, beside the run's own row and every attempt's row: a side sheet is one request). Contract `empirica-ui-view/v1` is edited in place, since it was never released: its first draft (`view.schema.json`) and `/api/view` are gone with the old UI (P2), and nothing is compatible with them.
- **New facts, one owner each:** `standing` and `ask` (`core.observer`), every audit verdict (`core.evaluation.audit_verdicts`, whose last is `latest_audit_verdict`), the claims a run no longer stands on with the gate's own why (`core.evaluation.claim_state_cause`, of which the gate's `local_claim_state` is the first element), every human answer, and the store-wide counts, ids and positions (`application/observer_index.py`, functions of published fields and keys only).
- **On the filesystem:** a run's version is the `lstat` of its generation directory and its `run.json` (the plugin writes every file there by renaming a new one in; equality only, never a time) and its history ref's commit. Refs are listed once per repository per poll, only for the project directories whose refs signature changed (`GitArtifactRepository.refs_signature`, read without running git), and not at all while none did; stale histories are read in batches through one `cat-file --batch` (`read_listed`, exactly what the single read returns, or left to it).
- **In the background:** `serve` indexes in a daemon thread (`adapters/observer/indexer.py`) in slices, carrying a cold start on without walking the store again, and publishes an immutable view at most twice a second while one runs. Every contract read answers from the last published view, so a request never pays for indexing; the server encodes each new summary in the indexer's turn. The first answer after start waits for the first publication (one walk of the store and one listing, no run read).
- **Budgets:** `make observer-smoke` times them once at 1,000 runs on a filesystem store of bridge-driven runs cloned with valid refs, in `make verify`; how a pass grows is counted at n and 2n in `make check-observer` (`test_observer_complexity.py`). (Until T1 a 10,000-run `observer-scale` run timed them; counting replaced it.) (`contracts/empirica-ui-view/v1/README.md`).

### Amendment (2026-10-13): change detection by a cost model

- **The model:** a run's projection is a function of its state, its history and the workspace files its active spike heads bind. Only two things change those: the plugin's own writes (a run's stamp moves) and the user's edits to bound files. With N runs, F indexed (workspace, path) pairs, K runs written since the last pass, M bound files whose content changed and R(M) the runs bound to them, a pass reads K states and histories, starts git only for repositories whose refs moved, hashes M files (plus the racily clean ones), projects K + |R(M)| runs, updates the counts 2(K + |R(M)|) times, and stats N + F things.
- **The drift index** (`application/observer_drift.py`): an inverted index from (workspace, path) to the change signature it was hashed at, its observation and the runs bound to it, maintained when a run is projected. A regraph, new spikes or a new workspace rebind the run; the files it no longer binds are let go, and a file no run binds is dropped. The workspace adapter gives the signature from `lstat` alone (`FilesystemWorkspace.signatures`: the root's identity, each directory on the way by type and inode, and the file's type, inode, size, `mtime_ns` and `ctime_ns`; a user cannot set the change time, so every write moves it). A pass hashes, through the workspace's own `observe` and the snapshot's own validation, only the files whose signature moved, so missing, unreadable, refused and renamed files read exactly as `build_observation_snapshot` reads them. A workspace without the port is observed again per run, as before.
- **Racily clean files** (as Git treats its index): a file whose change time is not older than the start of the pass that hashed it is hashed again the next pass, since a write in the same clock tick may leave its signature unchanged. The reference is the adapter's wall clock floored to the second (`now_ns`), so filesystem times coarser than a nanosecond are covered; it is compared only with a file's change time, never orders anything and never reaches a published fact. `core/` and `application/` still read no clock.
- **Reads:** a run projected again only because a file it binds changed reads nothing from the store. The observer keeps each run's inputs as last read, compressed (`zlib` over `pickle`, a few kilobytes a run), keyed by the stamp they were read at.
- **The counts** (`observer_index.Tally`): each run's contribution to the facets, the aggregates over every time, the coverage, the per-project counts and earliest start, the runs that need a human, the page order and an order-free digest of every (key, version) is taken out and put back as the run changes; the store is never recounted. A summary within a `since` window is counted from the rows on demand.
- **Discovery keeps the stat scan.** A change log would make discovery O(K), but the plugin's writers are several processes on several hosts (and copies, restores and deletions of a store happen outside the plugin), so a log would need its own locking, bounded rotation and a full-reconcile fallback for every missed, rotated or corrupt segment, and that fallback is the stat scan anyway. The scan costs N + F `lstat` calls and no reads, which the budgets already meet at 10,000 runs; it is robust to every change made by anything, and it adds no state for the observer to write (the observer writes nothing). A log stays possible later, behind the same stamps.
- **No persisted cache in 5.0.0:** a restarted observer projects every run once again, progressively and honest about coverage. A persisted cache is UI2 F12.
- **Proved by counting, not timing:** an instrumented source and workspace count state reads, history reads, git processes, `lstat`s, hashes (with bytes), projections and count updates, over a heterogeneous store (20 workspaces, spikes binding distinct, overlapping files of realistic size, ended and active runs, a share drifted), in memory and on the filesystem, at 1,000 and 10,000 runs; and the incremental view equals a from-scratch observer after generated sequences of writes and edits. The time budgets remain as a sanity check.

### Consequences

* **Good:** the UI shows exactly the plugin's judgement, and a change to a derivation reaches the UI without a UI change.
* **Good:** one observer per store on the default port, launched from any host (`/empirica:ui` in Claude, `/empirica-ui` in Pi, `make ui` and the CLI everywhere). When something else holds the port, each launch gets its own.
* **Bad:** a project's record names its latest repository. Linked worktrees share one artifact namespace (ADR-31), so the history is unaffected, and each run's workspace is recorded per run.
* **Bad:** a state that is not exactly the current state document is shown as `unreadable`, with nothing read from it (one category: no age, no partial decoding).
