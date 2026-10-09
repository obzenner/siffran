# siffran

A plugin collection for [Claude Code](https://docs.claude.com/en/docs/claude-code), [Codex](https://developers.openai.com/codex/), and the [Pi coding agent](https://pi.dev). It packages reusable, methodology-driven workflows: formal reasoning with Methodologist and evidence-backed convergence with Empirica.

The same host-neutral cores power the Claude Code, Codex (Methodologist only), and Pi adapters.
Empirica stores operational state under `~/.empirica-plugin` and durable claims
and evidence in Git shadow refs, leaving project worktrees clean.

## Governed Empirica starts

Empirica 4.0.0 defaults to host-mediated approval of run configuration—budget ceilings and
control mode—**before investigation**. The immutable goal is shown read-only; the claim graph
defines the work but is not approvable. Prepare from supplied context, record route, propose scope,
and submit `configure_run` to open approval. Configuration changes need a new exact decision;
graph changes preserve configuration authority while invalidating stale audit coverage. Explicit
`/empirica --auto <goal>` is bounded automatic acceptance, not human consent: it cannot increase
budgets and requires an interactive invocation or recorded operator delegation.

Claude needs MCP form elicitation and Pi uses its UI. Reviewer configuration stays with the host,
and the adapter observes the identity that actually produced the verdict. See the
[governance guide](plugins/empirica/skills/empirica/references/governance.md) for configuration and
auto provenance, and the [audit guide](plugins/empirica/skills/empirica/references/audit.md#independence-reporting)
for identity classification. Inventory-shaped old runs fail closed and require fresh generations;
they are never silently migrated or approved. New approval flows have deterministic integration
coverage, not operator-present native approval qualification.

## Upgrading from 4.x to 5.0

Empirica 5.0 does not migrate old runs, artifacts or receipts; they fail closed. To move over, start a
fresh session. A URL source now needs a human on Claude and Pi, and delegated or non-interactive runs
cannot confirm. v3 live receipts need a fresh operator capture. The wire family `empirica/v2` is distinct
from the plugin version 5.0.0.

## Install for Claude Code

Add the marketplace, then install a plugin:

```text
/plugin marketplace add obzenner/siffran
/plugin install methodologist@siffran
/plugin install empirica@siffran
```

## Install for Codex

Codex CLI 0.146.0 and the Codex desktop surface can install Methodologist from
the repository marketplace:

```sh
codex plugin marketplace add obzenner/siffran
codex plugin add methodologist@siffran
```

Start a new Codex session after installation. Methodologist's shared `think`
skill then has two honest modes:

- **Native simple mode (default):** ask a matching question such as “think
  through this architecture decision” and let Codex activate the skill
  implicitly, or mention `$think` explicitly. This is stateless and executes
  the methodology directly. Codex does not expose Methodologist as a custom
  `/think` slash command.
- **Structured bridge mode (opt-in):** ask Codex to “use `$think` in structured
  bridge mode.” On Codex surfaces that load plugin MCP servers, the bundled
  read-only `methodologist_select` tool validates the model's semantic choice
  against the shared registry and returns the canonical six-phase plan. Codex
  has no Methodologist task widget, so phases execute in the conversation
  without claiming host-native tracking.

Methodologist requires Python 3 only for structured bridge mode. Before
installing, review
[`plugins/methodologist/.codex-plugin/plugin.json`](./plugins/methodologist/.codex-plugin/plugin.json),
[`plugins/methodologist/.mcp.json`](./plugins/methodologist/.mcp.json), and the
small MCP adapter. The Methodologist package contains no hooks. Codex's normal
sandbox and MCP approval policy still apply.

**Empirica is not available on Codex.** Only Methodologist is offered through the Codex marketplace (ADR-0069 G10-R1).

Codex plugins are not supported in the IDE extension. Install the shared skill
directly as a repo/user skill there if needed, and use native simple mode only.

## Install for Pi

Empirica's independent audit runs through the external
[`pi-subagents`](https://www.npmjs.com/package/pi-subagents) extension; this package does **not**
bundle or load it. The Pi adapter is POSIX-only (macOS and Linux): spikes run through `/bin/sh` under
POSIX resource limits, and runtime provenance records POSIX paths; Windows is not supported. Install the
runtime first, then the repository (which carries Empirica, the Methodologist, and the Empirica auditor
role):

```sh
pi install npm:pi-subagents@0.76.1
pi install git:github.com/obzenner/siffran
```

At `session_start` the Empirica adapter resolves the single extension that registered the `subagent`
tool from Pi's own tool inventory, reads that package's exact version, and binds its launch preflight.
It refuses to start or configure a run (with a `host.subagents_*` reason) when there is no `subagent`
tool, when the session's slash commands show more than one loaded `pi-subagents` package (Pi reports
only the first `subagent` registration, so a second copy is seen through its commands, not its tool),
or when the owner's package or version cannot be verified. A second copy is detected only while its
extension registers commands; Pi runs and reports the first registrant, and that is the one bound.
Admitted `pi-subagents` versions are a range, `>=0.50.0,<1.0.0`, under the policy
`pi-subagents-foreground-audit-v1` (Pi `>=1.0.0,<2.0.0`). A version outside the supported range fails closed:
`/empirica` and `configure_run` are refused with `host.subagents_version_unsupported` before any run is
created. Inside the range a version is admitted by what the adapter observes of it, not by a list: its own
parameter schema must declare every field the audit launch sends, and its live launch preflight must resolve
the packaged auditor, the configured model and a fresh context, and its tool allowlist may hold only the tools the profile's `audit_child_tools` allows. A version in the table below has a checked-in
compat record and is *reviewed*. Any other version in the range is admitted, recorded as `reviewed: false`
and shown on every run view while it stays unreviewed (`host: pi-subagents <version> (not reviewed; preflight passed)`);
because nobody has reviewed its actions, a `subagent` action runs during a run only if some reviewed version has that
name and the classifier calls it read-only (`list`, `status`, `models`, ...); launches are classified as usual. The
versions in the range are admitted; only the rows of the table below are reviewed. `make empirica-subagents-matrix-update` lists releases newer than the newest
reviewed one; reviewing one means adding its compat record, preflight fixture and classifier cases.

| `pi-subagents` | Requires Pi | Native receipt (4.1) | Status |
|---|---|---|---|
| `0.50.0` | `>=1.0.0` | pending A9 | reviewed; the 4.0 qualification version |
| `0.64.0` | `>=1.0.0` | Pi 0.87.1 (A9a, `ebf426f`) | reviewed |
| `0.74.0` | `>=1.0.0` | pending A9 | reviewed |
| `0.75.0` | `>=1.0.0` | pending A9 | reviewed |
| `0.76.1` | `>=1.0.0` | pending A9 | reviewed; the repository's development pin |

"Requires Pi" is each release's own declared `@earendil-works/pi-ai` peer, raised to the floor of the supported Pi interval (1.0.0, which every reviewed release's peer is below). It is
recorded in `plugins/empirica/adapters/pi/compat/` and checked against this table. "Native receipt" marks
the pairs for which an installed-host run has been recorded; `pending A9` means none has yet. The
0.64.0 receipt is native evidence for that pair; the release receipt the live check requires must come
from the profile's qualified Pi version or later (`host-profiles.json` `version`, today 1.1.0; A9b, pending).

Install the newest reviewed version and pin the exact
version, as in the commands above; an unpinned install may resolve to a release outside the range, which is
refused, or to an unreviewed one inside it, which runs with only the reviewed read-only `subagent` actions allowed during a run.

Restart Pi after installation, or run `/reload` in an existing session. Available commands include:

```text
/think <intent>                 # structured Methodologist workflow
/think --simple <intent>        # original single-prompt Methodologist mode
/empirica <goal>                # start a durable Empirica v2 workflow
/empirica-ui                    # open the observer UI for your store (see below)
empirica_observe                 # tool: route, graph, configure_run, investigate, research, spike_request, freeze
empirica_read                    # tool: complete RunView, argument, contract
report_convergence               # tool: guarded terminal decision
```

The Pi adapter binds `empirica.empirica-auditor` as a foreground child, correlates
its `tool_result`, and admits trusted facts through non-model-callable private ingress.
It ignores requested result-model metadata and binds identity to the final native assistant
record in the exact host-generated child session when that record produced the admitted verdict.
Missing or ambiguous session evidence blocks. Pi also has no native completion veto; call
`report_convergence` before any status claim.

To update later:

```sh
pi update --extensions
```

For local development, install the checkout instead:

```sh
pi install "$(pwd)"
```

The exact Claude Code and Pi foreground profiles were promoted from installed-host observations;
release certification additionally requires fresh operator-attested, candidate-bound structural
receipts. Empirica is not available on Codex.
`make empirica-host-live-check` requires exact receipts for Claude and Pi only; Pi and Claude async
promotion remains separate and unsupported.

## Watching the store

The Empirica plugin ships a read-only observer UI: every project, run and claim in your store, with
how they relate, and the detail of whatever you click. It is served by the plugin itself on
127.0.0.1 (at `empirica.localhost:<port>`) and updates while runs progress.

| Host | Start it with |
|---|---|
| Claude Code | `/empirica:ui` |
| Pi | `/empirica-ui` |
| Any (from this checkout) | `make ui`, or `make ui-demo` for the plugin's demo runs |

A second start for the same store opens the observer already listening on the default port instead
of starting another. See
[`plugins/empirica/ui/README.md`](./plugins/empirica/ui/README.md) and ADR-0065.

## Plugins

<!-- BEGIN GENERATED: plugins (managed by the checkup skill — do not edit by hand) -->
| Plugin | Version | Description |
|--------|---------|-------------|
| `methodologist` | 0.10.0 | Formal reasoning catalog — lets users choose and execute evidence-backed CS/math methodologies with traced phases and structured output. |
| `empirica` | 5.0.0 | Host-neutral empirical-convergence workflow — requires exact run-configuration approval (or provenance-bound auto), cited research before spikes, per-artifact producer attribution, and a current distinct host-observed audit. Unknown or mixed identity evidence fails closed. |
<!-- END GENERATED: plugins -->

## Development

The project lifecycle lives in the `Makefile` — run `make help` to see every operation:

```
make check      # fast deterministic contributor gate (run before committing)
make status     # plugin versions, ADR count, working-tree state
make bump PLUGIN=<name> PART=minor
make methodologist-codex-check  # deterministic package + MCP validation
make native-qualification       # print the operator-led native skill entrypoint; launches nothing
```

The fast gate never launches online/native sessions. Expensive persistence and simulated-host
matrices are explicit integration diagnostics, while installed-host qualification is a separate
operator-led procedure. See [doc/testing.md](./doc/testing.md) for the coverage/cost map and
native limitations.

See [CLAUDE.md](./CLAUDE.md) for repo structure, conventions, and how to add a plugin or methodology. Run `make check` and the `checkup` skill before committing.
