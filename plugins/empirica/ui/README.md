# The Empirica observer UI

A read-only view of the Empirica run store for people: every project, run and claim, how they relate,
and the detail of what you click. It reads only the plugin's observer
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

- [`VIEW.md`](VIEW.md) is the specification of the one view. The browser checks hold the page to it.
- [`CONVENTIONS.md`](CONVENTIONS.md) holds the standing rules: tokens, copy, states, layering, checks.
- `app/` is the page; `elements/` holds its custom elements (atoms to templates); `gallery/` shows every element in every state.
- Declarations: `states.json`, `catalogue.json`, `copy/en.json`, `icons.json`, `tokens/tokens.json`.
- `dist/` and `vendor/` are built and committed, so the UI runs offline without Node. `package.json` is the dev-only toolchain that builds them.

## Develop

`make help` lists the targets:

- `check-ui` (part of `make check`): the audit and the node tests.
- `ui-browser`: headless Chrome against the real server.
- `ui-build`: rebuilds and checks everything.
- `ui-install`: installs the toolchain.
