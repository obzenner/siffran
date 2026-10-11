# The Empirica observer UI

A read-only page for people: your runs grouped by what needs you, and one run's workspace (what it owes or how it ended,
its claims with their proof, its attempts and history). It reads only the plugin's observer
(`application/observer.py`, contract [`empirica-ui-view/v1`](../../../contracts/empirica-ui-view/v1/README.md)),
which the plugin serves on 127.0.0.1 (`adapters/observer/server.py`), at `http://empirica.localhost:<port>/app/`. Why it is built this way:
ADR-0065.

## Start it

- **Claude Code:** `/empirica:ui`
- **Pi:** `/empirica-ui`
- **Any host:** `python3 plugins/empirica/adapters/observer/cli.py serve --open`, or `make ui` in a checkout
- **Demo:** `make ui-demo` shows the plugin's demo runs and reads no store

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

- [`UI.md`](UI.md) is the one document: who owns which fact, the screen, the URL, words and states, tokens, layers, freshness, checks.
- `app/` is the page; `elements/` holds its custom elements (atoms to templates).
- Declarations: `enums.json` (generated from the contract), `states.json`, `catalogue.json`, `copy/en.json`, `icons.json`, `tokens/tokens.json`.
- `dist/` and `vendor/` are built and committed, so the UI runs offline without Node. `package.json` is the dev-only toolchain that builds them.

## Develop

`make help` lists the targets:

- `check-ui` (part of `make check`): the audit and the node tests.
- `ui-enums`: regenerate `enums.json` from the contract.
- `ui-browser`: the operator's tasks T1–T7 in headless Chrome against the real server.
- `ui-build`: rebuilds and checks everything.
- `ui-install`: installs the toolchain.
