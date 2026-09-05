# `@siffran/methodologist-pi`

Turnkey Pi 0.84.1 package for the shared Methodologist plugin. It registers
`/think`, contributes the existing `think` skill, and translates named choices
to the host-neutral `methodologist/v1` bridge. The bridge calls the shared
Python core for registry parsing, structural validation, and the six-phase
invariant; the Pi adapter contains no duplicate methodology rules.

## Command behavior

- **`/think <methodology-name>`** — sends the exact name through the production
  stdio bridge. The core validates it against `registry.json` and the
  methodology file, then returns all six canonical phases. Pi renders them in a
  live widget.
- **bare `/think`** — asks the active model to read the shared `SKILL.md` and
  `registry.json`, semantically select by the current task's primary
  uncertainty, and call `methodologist_select`. This is intentionally not a
  keyword router. The tool enters the same named bridge flow as the explicit
  command.
- **genuine ambiguity** — the model can submit exactly two candidates; Pi shows
  `ctx.ui.select`, then sends the human's choice through the named bridge.

The model receives the validated phase plan from the tool and continues the
shared skill's six-phase reasoning instructions. Selection rules and
methodology content remain shared with the Claude plugin.

## Install / run

The plugin root is the standard Pi package; keeping the manifest there makes the
npm/git artifact self-contained with the shared Python core, bridge, skill, and
registry:

```bash
pi -e ./plugins/methodologist
```

It uses `python3` for the stdio bridge by default. Set
`METHODOLOGIST_PYTHON=/path/to/python` when needed.

The default export is fully wired. Embedding hosts and tests can still replace
the transport:

```ts
import { createMethodologistExtension } from "@siffran/methodologist-pi";

export default createMethodologistExtension({ dispatch: myTestOrRpcDispatch });
```

## State and UI

The adapter and bridge read shared resources only. They write nothing under the
repository, `.pi`, or `.claude`. Phase display state is in-memory and rendered
with `ctx.ui.setWidget`; ambiguity uses `ctx.ui.select`.

## Develop

```bash
make methodologist-pi-check
make release-check
```

The adapter tests include a real stdio round trip through the shared core and
assert that an explicit methodology yields exactly six numbered phases.
