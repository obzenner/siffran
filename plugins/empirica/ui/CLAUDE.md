# UI developer guide (read this first)

The observer UI, `plugins/empirica/ui/`. Commands run from the repository root. `UI.md` is the reference (what the
screen is and why); this file is how to change it fast. `scripts/check_generated_docs.py` (in `make check-static`)
holds this file to at most 150 lines and to targets `make help` lists.

## 1. The loop

| Step | Command | Time |
|---|---|---|
| once: the toolchain | `make ui-install` | ~30 s |
| one live demo, left running | `make ui-dev` (N=1000; reused while it runs; `STOP=1` stops it) | ~30 s first, then instant |
| edit | the owning file (section 2) | |
| the related unit tests | `make ui-test FILES=app/geometry.js,elements/views/inbox.js` (no `FILES`: the changed UI files) | ~1 s |
| look at it | `make ui-look URL=<the ui-dev URL>[?view=inbox…] [OUT=x.png] [SIZE=1024x768]`, then open the printed PNG with the read tool | ~2 s |
| **done**, once each, at the end | `make check`, then `make ui-browser` | ~70 s, ~2.5 min |

- The demo serves the UI's files from disk: reload the page (or `ui-look` again) after an edit. A change to the
  observer's Python needs `make ui-dev STOP=1`, then `make ui-dev`.
- A token or style change is built into `dist/` (committed): `make ui-css` (it runs `make ui-tokens`), then
  `make ui-contrast`.
- `make ui-test` also names the browser journeys that hold the files you changed (`tests/journeys.json`); they run in
  `make ui-browser` at the end, not in the loop.
- Other demos: `make ui-demo N=1000` (foreground, prints the URL), `make ui-shots` (about 24 PNGs, both schemes, for a
  person to review).

## 2. Where things live (a value changes only there)

| What | Where |
|---|---|
| colours, fonts, raw sizes and durations | `tokens/tokens.json` (then `make ui-css`) |
| sizes, caps, counts and timings the code reads | `catalogue.json` |
| every word the user sees | `copy/en.json` (through `t()`) |
| a contract value's tone, icon and copy prefix | `states.json`; its value lists `enums.json` are generated (`make ui-enums`) |
| layout maths (the Universe: discs, names, callouts, the camera) | `app/geometry.js`, pure functions |
| markup (HTML string builders) | `elements/views/*.js` |
| components (custom elements, one per file) | `elements/organisms/`, `elements/atoms/` |
| data | only from the observer, through `app/client.js` |

- The UI derives no domain fact. Standing, asks, reasons, counts and order come from the observer as served.
- No literal colour, px, ms or user-visible string in code: `scripts/audit.mjs` (in `make check-ui`) fails on them.

## 3. What needs a test

- **Logic** (geometry, rows, routing, ordering, keys, polling): yes. One test per behaviour, in the module's own test
  file (`tests/geometry.test.mjs` for `app/geometry.js`, `tests/rows.test.mjs`, `tests/route.test.mjs`, …).
- **Tokens, copy and styling:** no new test. The contrast check, the audit, and the clipping and layout invariants of
  `make ui-browser` cover them.
- **A bug:** a failing test first, then the fix.

## 4. Never, during development

- `make mutate`: it runs once before a release (later in CI), not in the loop.
- Hand-made mutation probes (breaking the code to see a test fail).
- `make verify`: the orchestrator's gate, about 15 min.
- Sleep or poll loops: wait on a command that ends, not on a timer.
- An unbounded command: wrap one-offs in `perl -e 'alarm 60; exec @ARGV' …` (macOS has no `timeout`).
- `pkill` or `killall`: kill an exact PID (`make ui-dev STOP=1` does).
- Pixel baselines: nothing compares pixels; the layout invariants and your eyes (`make ui-look`) do.

## 5. Timeouts (a hang is a bug to fix, not to wait out)

| Runner | Limit | Where |
|---|---|---|
| a Vitest test / hook / teardown | 5 s / 10 s / 10 s (the slowest test is ~0.3 s) | `vitest.config.mjs` |
| a suite of `make check` | 600 s, or its own `NAME@SECONDS` | `scripts/run_suites.py` (`RUN_SUITES_TIMEOUT`) |
| a step of `make verify` | 600 s, or its own in `LIMITS` | `scripts/verify.py` |
| a browser journey | 60 s, failing with its name | `tests/browser.journeys.mjs` (`JOURNEY_LIMIT_S`) |
| one Chrome page | ready in 20 s, killed at 90 s | `tests/cdp.mjs` |
| `make ui-look` | 30 s, Chrome's whole group killed | the Makefile |
| `make ui-dev` | the URL within 60 s | the Makefile |

## 6. Contracts

The UI shows only what the observer serves. A new fact goes first to the observer (`plugins/empirica/core/observer.py`,
`plugins/empirica/application/observer.py`), the contract (`contracts/empirica-ui-view/v1/*.schema.json`, fixtures by
`make contract-fixtures`, then `make ui-enums`) and an agreement test
(`plugins/empirica/tests/v2/test_observer_agreement.py`); only then does the UI draw it.

## 7. Debugging a visual issue

1. `make ui-look URL=<the page that shows it>` (add `?view=inbox`, `&zoom=<project>`, `&run=<key>` as the URL does).
2. Read the PNG.
3. Find the owning file in section 2: a colour is a token, a word is copy, a position is `app/geometry.js`, a
   missing element is a view.
4. Fix it there (with a test if it is logic, section 3).
5. `make ui-look` again, and compare.
