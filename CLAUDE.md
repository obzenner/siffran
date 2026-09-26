# CLAUDE.md

This is a multi-host **plugin marketplace** (`obzenner/siffran`). It contains reusable skills and methodologies distributed for Claude Code, Codex, and Pi, plus the lifecycle adapters that make Empirica's gates real on supported host surfaces.

## Drive this project through the Makefile

**Agents: manage this project through `make`. Run `make help` first — it lists every lifecycle operation, and its output is generated from the targets themselves, so it cannot go stale.**

Why this is a rule and not a preference: a command that lives only in a chat message or a README snippet drifts from the command that actually works. The Makefile is the single executable definition of this project's lifecycle, so a check that passes for you passes identically in CI and for the next agent.

| Instead of | Run |
|---|---|
| running the test file by path | `make test` |
| ad-hoc `ruff` invocations | `make lint` (`make fmt` to auto-fix) |
| hand-checking manifests | `make validate` |
| dogfooding the Pi adapters from this checkout | `make pi-dev` (this tree overrides the installed siffran; nothing else changes) |
| dogfooding a pushed PR branch inside a real project | `make pi-canary REF=<branch> DIR=<project>` |
| hand-checking the Codex package | `make methodologist-codex-check` |
| native qualification | `make native-qualification` (prints the operator-led skill path; launches nothing) |
| `adrs --ng doctor` | `make adr-check` |
| **everything, before you commit** | **`make check`** |
| only the suite you touched | `make check-static` / `check-core` / `check-claude` / `check-codex` / `check-pi` |
| what CI runs (no Node/Pi on the runner) | `make check-ci` (`PI_CHECKS=1` to include the Pi suite) |
| hand-editing a `version` field | `make bump PLUGIN=<name> PART=minor` |
| guessing whether the doc tables are current | `make docs-check` |

Rules that follow from this:

- **`make check` must be green before you commit.** It is the seconds-fast composition of five subject-matter suites — `check-static`, `check-core`, `check-claude`, `check-codex`, `check-pi`. `check-ci` omits Pi unless `PI_CHECKS=1`; `make test` includes all fast deterministic tests. Expensive persistence and simulated-host matrices are explicit integration diagnostics documented in `doc/testing.md`; native qualification is operator-led and never automatic.
- **Add new lifecycle operations as targets**, with a `## description` so they appear in `make help`. If you find yourself explaining a multi-step command in prose, that command belongs in the Makefile.
- **No target commits, pushes, or rewrites history**, by design. `make release-check` verifies and then tells you what is left; publishing stays a human decision.
- **Non-obvious exception:** the generated plugin tables in `CLAUDE.md`/`README.md` are rewritten by the `checkup` skill, which needs a Claude session. `make docs-check` can *detect* drift but not fix it; `make docs` tells you what to run.
- Validators live in `scripts/` as real Python files, not Makefile heredocs — the macOS default GNU Make 3.81 runs each recipe line in its own shell, so embedded heredocs break.

## Repo structure

- `Makefile` — the project lifecycle; `make help` is the entry point
- `scripts/` — validators the Makefile calls (manifest checks, doc-drift check, version bump)
- `.claude-plugin/marketplace.json` — marketplace catalog listing all available plugins
- `.agents/plugins/marketplace.json` — Codex marketplace catalog for both plugins
- `plugins/<name>/.claude-plugin/plugin.json` — plugin manifest (name, version, description)
- `plugins/<name>/.codex-plugin/plugin.json` — Codex-specific package manifest
- `plugins/<name>/skills/<skill-name>/SKILL.md` — skill definition (frontmatter + instructions)
- `plugins/<name>/hooks/` — Python lifecycle hooks + `hooks.json` wiring them to events
- `plugins/empirica/adapters/codex/` — Codex 0.146.0 payload translation and native hook results
- `plugins/<name>/agents/` — subagent definitions. **Spawn these by their plugin-scoped name** (`empirica:empirica-auditor`); the bare name does not resolve.
- `plugins/<name>/tests/` — committed regressions; fast selections run by `make test`, expensive boundary matrices by the integration targets in `doc/testing.md`
- `plugins/methodologist/adapters/codex/` — stateless MCP translation into the host-neutral bridge
- `doc/adr/` — architecture decision records (MADR, via the `adrs` CLI)

Plugins can also contain `commands/` alongside these.

## Conventions

### Versioning

Every plugin must have a `version` field in its `plugin.json` following semver (`MAJOR.MINOR.PATCH`). Claude Code uses this to detect updates — if you change plugin code without bumping the version, users won't get the update.

- **PATCH** (0.0.1 → 0.0.2): bug fixes, wording tweaks
- **MINOR** (0.1.0 → 0.2.0): new features, new methodologies, added examples
- **MAJOR** (1.0.0 → 2.0.0): breaking changes to skill behavior or structure

Set the version in the host manifests, never in marketplace catalogs. `make bump` updates the
Claude manifest and synchronizes a sibling `.codex-plugin/plugin.json` when one exists.

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
make check          # fast static + core + Claude + Codex + Pi contributor gate
make check-ci       # fast gate without Pi; PI_CHECKS=1 opts Pi in
make check-<suite>  # static | core | claude | codex | pi — the one you are working in
make test           # all fast deterministic tests, including Pi
make native-qualification  # operator procedure entrypoint; launches nothing
make validate       # manifests only
/plugin validate .  # Claude Code's own manifest check, complementary to make validate
```

## Current plugins

The table below and the `## Plugins` table in `README.md` are **generated** — the `checkup` skill regenerates both from each plugin's `plugin.json` (version) and the marketplace/skill descriptions. Do not hand-edit between the markers; edit the source manifests and run `checkup`.

<!-- BEGIN GENERATED: plugins (managed by the checkup skill — do not edit by hand) -->
| Plugin | Version | Description |
|--------|---------|-------------|
| `methodologist` | 0.9.0 | Formal reasoning catalog — lets users choose and execute evidence-backed CS/math methodologies with traced phases and structured output. |
| `empirica` | 4.0.0 | Host-neutral empirical-convergence workflow — binds exact claim scope, budgets and auditor to host-mediated approval (or explicit bounded auto), requires cited research before spikes, and gates convergence on a current independent audit. Unsupported approval and identity capabilities fail closed. |
<!-- END GENERATED: plugins -->

## README

`README.md` is a plain file (not a symlink to this one). It carries a short user-facing description of the marketplace plus the generated plugin table. Its prose is static; only the marked plugin region is regenerated by `checkup`.
