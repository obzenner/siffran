# siffran

A plugin collection for [Claude Code](https://docs.claude.com/en/docs/claude-code) and the [Pi coding agent](https://pi.dev). It packages reusable, methodology-driven workflows: formal reasoning with Methodologist and evidence-backed convergence with Empirica.

The same host-neutral cores power the Claude Code and Pi adapters. Empirica stores operational state under `~/.empirica-plugin` and durable claims and evidence in Git shadow refs, leaving project worktrees clean.

## Install for Claude Code

Add the marketplace, then install a plugin:

```text
/plugin marketplace add obzenner/siffran
/plugin install methodologist@siffran
/plugin install empirica@siffran
```

## Install for Pi

Install the repository as one Pi package. This enables both Methodologist and Empirica:

```sh
pi install git:github.com/obzenner/siffran
```

Restart Pi after installation, or run `/reload` in an existing session. Available commands include:

```text
/think <intent>                 # structured Methodologist workflow
/think --simple <intent>        # original single-prompt Methodologist mode
/empirica <goal>                # start an evidence-convergence run
/empirica-status                # inspect the current run
```

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
| `methodologist` | 0.7.0 | Methodology router — picks and executes formal CS/math reasoning methodologies with tracked phases and structured output. |
| `empirica` | 1.0.0 | Empirical-convergence workflow — adjudicates a claim graph (GSN argument with in-toto evidence) where every claim's confidence must be earned by real external evidence: research citations first, deterministic spike verdicts for machine-checkable claims, then an independent auditor on a different model before a run may report convergence. Records which model actually answered each claim, and reports when audit independence was not obtained. Hook-enforced. |
<!-- END GENERATED: plugins -->

## Development

The project lifecycle lives in the `Makefile` — run `make help` to see every operation:

```
make check      # lint + tests + manifest validation + ADR health (run before committing)
make status     # plugin versions, ADR count, working-tree state
make bump PLUGIN=<name> PART=minor
```

See [CLAUDE.md](./CLAUDE.md) for repo structure, conventions, and how to add a plugin or methodology. Run `make check` and the `checkup` skill before committing.
