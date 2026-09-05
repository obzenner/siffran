# CLAUDE.md

This is a multi-host plugin collection (`obzenner/siffran`) for Claude Code,
Codex, and Pi. It contains reusable skills and methodologies plus thin host
adapters; Methodologist's Codex package reuses the shared `think` tree, while
the Python lifecycle hooks remain Empirica/Claude-specific.

## Drive this project through the Makefile

**Agents: manage this project through `make`. Run `make help` first — it lists every lifecycle operation, and its output is generated from the targets themselves, so it cannot go stale.**

Why this is a rule and not a preference: a command that lives only in a chat message or a README snippet drifts from the command that actually works. The Makefile is the single executable definition of this project's lifecycle, so a check that passes for you passes identically in CI and for the next agent.

| Instead of | Run |
|---|---|
| running the test file by path | `make test` |
| ad-hoc `ruff` invocations | `make lint` (`make fmt` to auto-fix) |
| hand-checking manifests | `make validate` |
| hand-checking the Codex package | `make methodologist-codex-check` |
| ad-hoc Codex installation/invocation tests | `make methodologist-codex-smoke` |
| `adrs --ng doctor` | `make adr-check` |
| **everything, before you commit** | **`make check`** |
| hand-editing a `version` field | `make bump PLUGIN=<name> PART=minor` |
| guessing whether the doc tables are current | `make docs-check` |

Rules that follow from this:

- **`make check` must be green before you commit.** It runs lint, tests, manifest validation, and ADR health. If you changed anything under `plugins/`, run it.
- **Add new lifecycle operations as targets**, with a `## description` so they appear in `make help`. If you find yourself explaining a multi-step command in prose, that command belongs in the Makefile.
- **No target commits, pushes, or rewrites history**, by design. `make release-check` verifies and then tells you what is left; publishing stays a human decision.
- **Non-obvious exception:** the generated plugin tables in `CLAUDE.md`/`README.md` are rewritten by the `checkup` skill, which needs a Claude session. `make docs-check` can *detect* drift but not fix it; `make docs` tells you what to run.
- Validators live in `scripts/` as real Python files, not Makefile heredocs — the macOS default GNU Make 3.81 runs each recipe line in its own shell, so embedded heredocs break.

## Repo structure

- `Makefile` — the project lifecycle; `make help` is the entry point
- `scripts/` — validators the Makefile calls (manifest checks, doc-drift check, version bump)
- `.claude-plugin/marketplace.json` — marketplace catalog listing all available plugins
- `.agents/plugins/marketplace.json` — Codex marketplace (Methodologist only; no Empirica hooks)
- `plugins/<name>/.claude-plugin/plugin.json` — plugin manifest (name, version, description)
- `plugins/methodologist/.codex-plugin/plugin.json` — Codex package manifest over the same shared skill tree
- `plugins/<name>/skills/<skill-name>/SKILL.md` — skill definition (frontmatter + instructions)
- `plugins/<name>/hooks/` — Python lifecycle hooks + `hooks.json` wiring them to events
- `plugins/<name>/agents/` — subagent definitions. **Spawn these by their plugin-scoped name** (`empirica:empirica-auditor`); the bare name does not resolve.
- `plugins/<name>/tests/` — committed regression suites, run by `make test`
- `plugins/methodologist/adapters/codex/` — stateless MCP translation into the host-neutral bridge
- `doc/adr/` — architecture decision records (MADR, via the `adrs` CLI)

Plugins can also contain `commands/` alongside these.

## Conventions

### Versioning

Every plugin must have a `version` field in its `plugin.json` following semver (`MAJOR.MINOR.PATCH`). Claude Code uses this to detect updates — if you change plugin code without bumping the version, users won't get the update.

- **PATCH** (0.0.1 → 0.0.2): bug fixes, wording tweaks
- **MINOR** (0.1.0 → 0.2.0): new features, new methodologies, added examples
- **MAJOR** (1.0.0 → 2.0.0): breaking changes to skill behavior or structure

Set the version only in `plugin.json`, not in `marketplace.json`.

### Adding a new plugin

1. Create `plugins/<name>/.claude-plugin/plugin.json` with name, description, version starting at `0.1.0`
2. Create `plugins/<name>/skills/<skill-name>/SKILL.md` with YAML frontmatter and skill body
3. Register in `.claude-plugin/marketplace.json` with name, source path, description, and category
4. Validate with `make validate`, then `make check` before committing

### Adding a methodology to methodologist

1. Create `plugins/methodologist/skills/think/methodologies/<name>.md`
2. Follow the existing pattern: lineage, prevents, core principle, 6 numbered phases with output formats
3. Add the methodology to the routing table in `SKILL.md`
4. Bump the version: `make bump PLUGIN=methodologist PART=minor`

### Skill quality bar

All methodologies must be rooted in computer science, mathematics, or established scientific method. No vibe-based approaches. Each methodology must cite its intellectual lineage and state what failure mode it prevents.

## Validation

```
make check          # lint + tests + manifests + ADR health — run before every commit
make validate       # manifests only
/plugin validate .  # Claude Code's own manifest check, complementary to make validate
```

## Current plugins

The table below and the `## Plugins` table in `README.md` are **generated** — the `checkup` skill regenerates both from each plugin's `plugin.json` (version) and the marketplace/skill descriptions. Do not hand-edit between the markers; edit the source manifests and run `checkup`.

<!-- BEGIN GENERATED: plugins (managed by the checkup skill — do not edit by hand) -->
| Plugin | Version | Description |
|--------|---------|-------------|
| `methodologist` | 0.8.0 | Methodology router — picks and executes formal CS/math reasoning methodologies with tracked phases and structured output. |
| `empirica` | 1.0.0 | Empirical-convergence workflow — adjudicates a claim graph (GSN argument with in-toto evidence) where every claim's confidence must be earned by real external evidence: research citations first, deterministic spike verdicts for machine-checkable claims, then an independent auditor on a different model before a run may report convergence. Records which model actually answered each claim, and reports when audit independence was not obtained. Hook-enforced. |
<!-- END GENERATED: plugins -->

## README

`README.md` is a plain file (not a symlink to this one). It carries a short user-facing description of the marketplace plus the generated plugin table. Its prose is static; only the marked plugin region is regenerated by `checkup`.
