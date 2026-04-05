# CLAUDE.md

This is a Claude Code **plugin marketplace** (`obzenner/siffran`). It contains reusable skills and methodologies distributed via Claude Code's plugin system. No build step, no runtime code — purely declarative configuration and Markdown-based skill definitions.

## Repo structure

- `.claude-plugin/marketplace.json` — marketplace catalog listing all available plugins
- `plugins/<name>/.claude-plugin/plugin.json` — plugin manifest (name, version, description)
- `plugins/<name>/skills/<skill-name>/SKILL.md` — skill definition (frontmatter + instructions)

Plugins can also contain `commands/`, `agents/`, and `hooks/` directories alongside `skills/`.

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
4. Validate with `/plugin validate .`

### Adding a methodology to methodologist

1. Create `plugins/methodologist/skills/think/methodologies/<name>.md`
2. Follow the existing pattern: lineage, prevents, core principle, 6 numbered phases with output formats
3. Add the methodology to the routing table in `SKILL.md`
4. Bump methodologist version (MINOR)

### Skill quality bar

All methodologies must be rooted in computer science, mathematics, or established scientific method. No vibe-based approaches. Each methodology must cite its intellectual lineage and state what failure mode it prevents.

## Validation

```
/plugin validate .
```

Run from repo root to validate marketplace and plugin manifests.

## Current plugins

| Plugin | Version | Description |
|--------|---------|-------------|
| `methodologist` | 0.1.0 | Methodology router — `/think` for auto-detect, `/think <name>` for specific methodology |
