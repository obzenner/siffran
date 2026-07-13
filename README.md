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
| `deep-planner` | 0.3.0 | Iterative investigation-and-planning loop — reads the codebase in passes, separates business logic / custom code / external integrations, verifies all API surfaces against actual docs before graduating the plan. |
<!-- END GENERATED: plugins -->

## Development

See [CLAUDE.md](./CLAUDE.md) for repo structure, conventions, and how to add a plugin or methodology. Run `/plugin validate .` and the `checkup` skill before committing.
