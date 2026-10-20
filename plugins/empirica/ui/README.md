# The Empirica observer UI

A read-only page for people. The **Universe**: every run a mark, each project an island, zoom from the store to an
island to a run. The **Inbox**: buckets of what needs you, the calendar, and runs by standing. A run's **sheet**
(resizable): what it owes or how it ended, its immutable spike capability (unavailable or a safely abbreviated qualified
binding, never live health), its claims with their read-only single-container spike requests and admitted results, its audits, attempts and history. Generic spike disclosures show sealed plan/input identities, stream digests, verdict, cleanup, model attribution, and postflight facts; they never show raw output, authored environment values, host paths, credentials or runtime identifiers.
It reads only the plugin's observer (`application/observer.py`, contract
[`empirica-ui-view/v1`](../../../contracts/empirica-ui-view/v1/README.md)), which the plugin serves on 127.0.0.1
(`adapters/observer/server.py`), at `http://empirica.localhost:<port>/app/`. Why it is built this way: ADR-0065.

## Start it

- **Claude Code:** `/empirica:ui`
- **Pi:** `/empirica-ui`
- **Any host:** `python3 plugins/empirica/adapters/observer/cli.py serve --open`, or `make ui` in a checkout
- **Demo:** `make ui-demo` serves 300 generated runs (`N=1000`, `N=10000` at volume) and reads no store; `--demo` on the CLI serves the contract fixtures

Each project's history is found in the repository recorded when its runs were created. For runs
without a recorded location, pass `--repo <checkout>`
(`make ui REPO=<checkout>`). Without it, the current directory's repository is used.

A second start for the same store opens the observer already listening on the default port (8765)
instead of starting another. If something else holds that port, each start gets its own free port.

The printed (and opened) address is `http://empirica.localhost:<port>/app/`: browsers resolve `*.localhost` to this
machine, and the name tells the tab apart from other local servers. The server answers only to `empirica.localhost`,
`localhost` and `127.0.0.1` with its own port (a rebinding guard). The start also prints the `127.0.0.1` address, for a
browser that does not resolve `*.localhost`.

## What is here

- [`UI.md`](UI.md) is the one document: who owns which fact, the screen, the URL, the keyboard, words and states, tokens, layers, freshness, checks.
- `app/` is the page; `elements/` holds its custom elements (atoms, organisms) and the pure views they share.
- Declarations: `enums.json` (generated from the contract), `states.json`, `catalogue.json`, `copy/en.json`, `icons.json`, `tokens/tokens.json`.
- `dist/` and `vendor/` are built and committed, so the UI runs offline without Node. `package.json` is the dev-only toolchain that builds them.

## Develop

`make help` lists the targets:

- `check-ui` (part of `make check`): every Vitest unit test and the audit; needs `make ui-install`.
- `ui-test`: the tests related to `FILES=…` or to the changed UI files; names the browser journeys that also hold them.
- `ui-browser`: headless-Chrome journeys, the standing rules and the layout invariants in both colour schemes (needs Chrome).
- `ui-smoke`: one headless-Chrome smoke run at 1,000 generated runs (needs Chrome).
- `ui-dev`: one live demo for this worktree, N=1000 by default, reused while it runs; `STOP=1` stops it.
- `ui-look`: one PNG of `URL=<page>` (`OUT=`, `SIZE=`), to open with the read tool.
- `ui-demo`: serve a generated demo store (N=300 by default) in the foreground.
- `ui-build`: rebuild everything the UI ships, then check it.
- `ui-vendor`: re-vendor the pinned Basecoat, fonts, Lucide subset and idiomorph into `vendor/`.
- `ui-install`: install the dev-only toolchain.
- `mutate`: mutation testing of the changed lines.
