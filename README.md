# siffran

A plugin collection for [Claude Code](https://docs.claude.com/en/docs/claude-code), [Codex](https://developers.openai.com/codex/), and the [Pi coding agent](https://pi.dev). It packages reusable, methodology-driven workflows: formal reasoning with Methodologist and evidence-backed convergence with Empirica.

The same host-neutral cores power the Claude Code, Codex, and Pi adapters.
Empirica stores operational state under `~/.empirica-plugin` and durable claims
and evidence in Git shadow refs, leaving project worktrees clean.

## Governed Empirica starts

Empirica 3.2.0 defaults to host-mediated approval of the exact claim graph, budgets, modes, and
visible auditor **before investigation**. Prepare from supplied context, record route, propose
scope, and submit `configure_run` to open approval. Material revisions need new approval.
Explicit `/empirica --auto <goal>` is bounded automatic acceptance, not human consent: no budget
increases and at most eight material revisions after first approval.

Claude needs MCP form elicitation and operator-declared authorized inventory; Pi uses its UI and
configured authenticated model registry. Missing UI/inventory fails closed. See the
[governance guide](plugins/empirica/skills/empirica/references/governance.md) for operator configuration,
singleton exceptions, scope limits, and recovery. This release requires fresh run generations;
old runs are not silently migrated/approved. New approval flows have deterministic integration
coverage, not operator-present native approval qualification.

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
codex plugin add empirica@siffran
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

**Empirica on Codex is work in progress and is not supported for convergence.** The exact
Codex 0.146.0 adapter exposes the canonical public MCP tools and a fail-closed Stop gate for
adapter development, but Codex cannot independently observe the resolved auditor model. Its
profile is therefore `observational` with `promotion_status: wip_unsupported`; convergence blocks
with governance/identity reasons rather than trusting configured argv. Deliberative approval
is unavailable on Codex; explicit `--auto` can exercise the bounded experimental path but cannot
prove the auditor identity. Do not rely on the
experimental Codex package for an Empirica convergence claim:

```text
$empirica design and verify the retry policy
```

See [`plugins/empirica/adapters/codex/README.md`](./plugins/empirica/adapters/codex/README.md)
for the pinned payload contract and hosted-WebSearch visibility limit.

Codex plugins are not supported in the IDE extension. Install the shared skill
directly as a repo/user skill there if needed, and use native simple mode only.

## Install for Pi

Install the repository as one Pi package. It bundles the pinned
`pi-subagents@0.50.0` extension and the Empirica auditor role required by the exact
`pi@0.84.1+pi-subagents@0.50.0` profile:

```sh
pi install git:github.com/obzenner/siffran
```

Restart Pi after installation, or run `/reload` in an existing session. Available commands include:

```text
/think <intent>                 # structured Methodologist workflow
/think --simple <intent>        # original single-prompt Methodologist mode
/empirica <goal>                # start a durable Empirica v2 workflow
empirica_observe                 # tool: route, graph, research, spike, freeze
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
receipts. Codex is explicitly `wip_unsupported` and is not part of the supported release set.
`make empirica-host-live-check` requires exact receipts for Claude and Pi only; Pi and Claude async
promotion remains separate and unsupported.

## Plugins

<!-- BEGIN GENERATED: plugins (managed by the checkup skill — do not edit by hand) -->
| Plugin | Version | Description |
|--------|---------|-------------|
| `methodologist` | 0.9.0 | Formal reasoning catalog — lets users choose and execute evidence-backed CS/math methodologies with traced phases and structured output. |
| `empirica` | 3.2.0 | Host-neutral empirical-convergence workflow — binds exact claim scope, budgets and auditor to host-mediated approval (or explicit bounded auto), requires cited research before spikes, and gates convergence on a current independent audit. Unsupported approval and identity capabilities fail closed. |
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
