# siffran

A [Claude Code](https://docs.claude.com/en/docs/claude-code) plugin marketplace. It packages reusable, methodology-driven skills — formal reasoning routers, iterative planners, and structural health checks — so they can be installed into any Claude Code project via the plugin system.

There's no build step and no runtime code: every plugin is declarative configuration plus Markdown skill definitions.

## Install

Add the marketplace, then install a plugin:

```
/plugin marketplace add obzenner/siffran
/plugin install methodologist@siffran
```

## Plugins

<!-- BEGIN GENERATED: plugins (managed by the checkup skill — do not edit by hand) -->
| Plugin | Version | Description |
|--------|---------|-------------|
| `methodologist` | 0.4.0 | Methodology router — picks and executes formal CS/math reasoning methodologies with tracked phases and structured output. |
| `empirica` | 0.7.0 | Empirical-convergence workflow — adjudicates a claim graph (GSN argument with in-toto evidence) where every claim's confidence must be earned by real external evidence: research citations first, deterministic spike verdicts for machine-checkable claims, then an independent auditor on a different model before a run may report convergence. Records which model actually answered each claim, and reports when audit independence was not obtained. Hook-enforced. |
<!-- END GENERATED: plugins -->

## Development

The project lifecycle lives in the `Makefile` — run `make help` to see every operation:

```
make check      # lint + tests + manifest validation + ADR health (run before committing)
make status     # plugin versions, ADR count, working-tree state
make bump PLUGIN=<name> PART=minor
```

See [CLAUDE.md](./CLAUDE.md) for repo structure, conventions, and how to add a plugin or methodology. Run `make check` and the `checkup` skill before committing.
