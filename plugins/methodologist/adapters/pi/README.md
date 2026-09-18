# `@siffran/methodologist-pi`

Turnkey Pi 0.84.1 package for the shared Methodologist plugin. It registers
`/think`, contributes the existing `think` skill, retrieves the complete catalog
from the host-neutral `methodologist/v1` bridge, and starts the methodology the
user selects. The bridge calls the shared
Python core for registry parsing, structural validation, and the six-phase
invariant; the Pi adapter contains no duplicate methodology rules.

## Command behavior

- **`/think --simple <intent>`** — sends exactly one non-recursive user prompt
  telling the active model to select and execute from the same shared
  `SKILL.md`, `registry.json`, and methodology files. This path does not call the
  bridge or `methodologist_select`, does not instantiate `HumanPort` or the
  phase widget, and writes no Methodologist workflow/task state. It contains no
  copied methodology instructions or keyword router.
- **`/think <methodology-name>`** — expert shortcut: sends the exact name through
  the production stdio bridge. The core validates it against `registry.json` and
  the methodology file, then the adapter starts the model with the selected
  shared methodology and canonical six-phase plan.
- **bare `/think`** — asks the bridge for every registry entry, displays the
  complete ordered catalog through `ctx.ui.select`, waits for the human's
  choice, validates that choice through the named bridge path, and starts the
  selected methodology. It does not silently auto-select or use keyword routing.
- **genuine ambiguity** — the model can submit exactly two candidates; Pi shows
  `ctx.ui.select`, then sends the human's choice through the named bridge.

The model receives the validated phase plan in its execution kickoff and
continues the shared skill's six-phase reasoning instructions. Simple mode reads
and executes those instructions directly from the shared files. Selection rules
and methodology content remain shared with the Claude plugin.

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
repository, `.pi`, or `.claude`; selection uses `ctx.ui.select`, and the selected
methodology starts through one `pi.sendUserMessage` kickoff. The stateless bridge
renders no persistent phase widget because it has no honest phase-advance
lifecycle. Simple
mode bypasses those UI ports and creates no Methodologist workflow/task state;
its sole effect is one `pi.sendUserMessage` call containing the direct kickoff.

## Develop

```bash
make methodologist-pi-check
make release-check
```

The adapter tests prove simple mode performs zero dispatches, emits exactly one
prompt, writes no extension workflow state, and leaves normal bridge-backed
behavior intact. The package is exercised against the live Pi CLI version named
in `package.json` during release verification.
