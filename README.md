# siffran

A plugin collection for [Claude Code](https://docs.claude.com/en/docs/claude-code), [Codex](https://developers.openai.com/codex/), and the [Pi coding agent](https://pi.dev). It packages reusable, methodology-driven workflows: formal reasoning with Methodologist and evidence-backed convergence with Empirica.

The same host-neutral cores power the Claude Code, Codex, and Pi adapters.
Empirica stores operational state under `~/.empirica-plugin` and durable claims
and evidence in Git shadow refs, leaving project worktrees clean.

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

Empirica's command hooks require a separate trust step. Open `/hooks`, review the
installed definitions, and trust them before use. The current Codex profile is
observational: it cannot provide the author-action, bound-audit, or completion
lifecycle required for convergence. An explicit invocation therefore reports a
typed capability gap rather than starting a full run:

```text
$empirica design and verify the retry policy
```

See [`plugins/empirica/adapters/codex/README.md`](./plugins/empirica/adapters/codex/README.md)
for the pinned payload contract and hosted-WebSearch visibility limit.

Codex plugins are not supported in the IDE extension. Install the shared skill
directly as a repo/user skill there if needed, and use native simple mode only.

## Install for Pi

Install the repository as one Pi package. This enables Methodologist and installs
Empirica's current diagnostic adapter. The exact `pi@0.84.1` Empirica profile is
not yet capable of author actions or a bound audit lifecycle, so it refuses to
start an unusable convergence run:

```sh
pi install git:github.com/obzenner/siffran
```

Restart Pi after installation, or run `/reload` in an existing session. Available commands include:

```text
/think <intent>                 # structured Methodologist workflow
/think --simple <intent>        # original single-prompt Methodologist mode
/empirica <goal>                # report the current typed Pi capability gap
empirica_status                 # tool: inspect a restored run's ID and status
```

`empirica_status` and `report_convergence` remain available for diagnosing an
existing restored handle. Neither provides the missing author or audit path.

To update later:

```sh
pi update --extensions
```

For local development, install the checkout instead:

```sh
pi install "$(pwd)"
```

## Plugins

<!-- BEGIN GENERATED: plugins (managed by the checkup skill — do not edit by hand) -->
| Plugin | Version | Description |
|--------|---------|-------------|
| `methodologist` | 0.8.1 | Methodology router — picks and executes formal CS/math reasoning methodologies with tracked phases and structured output. |
| `empirica` | 2.0.0 | Host-neutral empirical-convergence workflow — routes uncertainty into a claim graph, requires cited research before deterministic spikes, derives claim state, and binds convergence to a current independent audit. Full execution is hook-enforced only on profiles with author-action and bound-audit capabilities; unsupported profiles fail explicitly before starting. |
<!-- END GENERATED: plugins -->

## Development

The project lifecycle lives in the `Makefile` — run `make help` to see every operation:

```
make check      # lint + tests + manifest validation + ADR health (run before committing)
make status     # plugin versions, ADR count, working-tree state
make bump PLUGIN=<name> PART=minor
make methodologist-codex-check  # deterministic package + MCP validation
make methodologist-codex-smoke  # real codex-cli 0.146.0 online smoke
make codex-live-check CODEX='npx -y @openai/codex@0.146.0'
```

The online smoke creates isolated temporary `HOME` and `CODEX_HOME` trees,
installs the local marketplace and plugin, and proves both implicit skill and
MCP tool invocation. It requires `npx`, network access, and either an existing
Codex login (copied only into the throwaway home) or `OPENAI_API_KEY`; the
deterministic check is part of `make check` and has none of those requirements.

See [CLAUDE.md](./CLAUDE.md) for repo structure, conventions, and how to add a plugin or methodology. Run `make check` and the `checkup` skill before committing.
