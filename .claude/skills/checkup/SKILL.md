---
name: checkup
description: "Run a structural health check on this marketplace repo and regenerate the plugin tables in CLAUDE.md and README.md. Verifies manifest integrity, cross-references, version hygiene, then rewrites the generated documentation regions from source manifests. Use before committing, after adding plugins/methodologies or bumping a version, or when something feels off."
allowed-tools: [Read, Glob, Grep, Bash, Edit]
---

# Marketplace Healthcheck

You are running a structural integrity check on this plugin marketplace repo.

Checks 1–5 are **read-only**: execute them in order, do NOT skip any, and do NOT fix the issues they surface — only report them. Check 6 is different: it **regenerates** the plugin tables in `CLAUDE.md` and `README.md` from the source manifests. That is the one place this skill writes to disk.

After all checks complete, produce the summary report.

## Check 1: Marketplace manifest → plugin directories

Read `.claude-plugin/marketplace.json`. For each plugin entry:
- Resolve the `source` path relative to repo root
- Verify the directory exists
- Report: PASS or FAIL with missing path

## Check 2: Plugin manifests

For each plugin directory found in Check 1:
- Verify `.claude-plugin/plugin.json` exists
- Verify it contains `name`, `description`, `version`
- Verify `version` is valid semver (MAJOR.MINOR.PATCH)
- Verify `name` matches the directory name and the marketplace.json entry
- Report: PASS or FAIL per field

## Check 3: Skill integrity

For each plugin directory:
- Glob for `skills/*/SKILL.md`
- For each SKILL.md found, verify frontmatter contains `name` and `description`
- Verify skill directory name matches frontmatter `name`
- Report: PASS or FAIL per skill

## Check 4: Methodology registry consistency

This check is specific to the `methodologist` plugin.

- Run `python3 plugins/methodologist/scripts/validate.py plugins/methodologist/skills/think`
- The script reads registry.json's self-declared schema and checks entries against files on disk
- Report: PASS if exit code 0, FAIL if exit code 1 (include script output)

## Check 5: Version hygiene

For each plugin:
- Run `git diff --name-only HEAD -- plugins/<name>/` (if HEAD exists) to check for uncommitted changes
- If there are changes AND `plugin.json` is not in the changed files, flag: "Content changed without version bump"
- If no HEAD exists (fresh repo), skip this check and report: SKIP (no commits yet)
- Report: PASS, FAIL, or SKIP per plugin

## Check 6: Regenerate the plugin tables (CLAUDE.md + README.md)

The plugin tables in `CLAUDE.md` and `README.md` are **generated**, not hand-maintained. Each lives between marker comments:

```
<!-- BEGIN GENERATED: plugins (managed by the checkup skill — do not edit by hand) -->
...table...
<!-- END GENERATED: plugins -->
```

Regenerate both from source manifests — do NOT invent or preserve prose from the existing tables:

1. Build the canonical rows. Iterate plugins in the order they appear in `marketplace.json`. For each, read `plugins/<name>/.claude-plugin/plugin.json` and take:
   - **Plugin** = `name`, wrapped in backticks
   - **Version** = `version`
   - **Description** = `description` verbatim (`plugin.json` is the single source of truth for the description; do not paraphrase or merge in the marketplace.json description)
2. Render the table with header `| Plugin | Version | Description |` and the separator row, followed by one row per plugin.
3. For each of `CLAUDE.md` and `README.md`: locate the `BEGIN GENERATED: plugins` / `END GENERATED: plugins` markers and replace everything strictly between them with the rendered table. Leave the marker lines and all surrounding prose untouched. Use `Edit` for the replacement.
4. Verify: after editing, the two generated regions must be byte-identical to each other and to the canonical table you built.

Report:
- **PASS** if the regions already matched the canonical table (no edit needed) — note "already in sync".
- **FIXED** if you rewrote one or both regions — list which files changed and the specific rows that differed (e.g. version bump, description change, plugin added/removed).
- **FAIL** if a marker pair is missing or malformed in either file, or a referenced `plugin.json` is missing — report the file and what's wrong; do not guess the location without markers.

## Summary report format

After all checks, produce exactly this format:

```
## Marketplace Healthcheck

| Check | Status | Details |
|-------|--------|---------|
| 1. Manifest → dirs | PASS/FAIL | <details if FAIL> |
| 2. Plugin manifests | PASS/FAIL | <details if FAIL> |
| 3. Skill integrity | PASS/FAIL | <details if FAIL> |
| 4. Methodology routing | PASS/FAIL | <details if FAIL> |
| 5. Version hygiene | PASS/FAIL/SKIP | <details if FAIL> |
| 6. Plugin tables | PASS/FIXED/FAIL | <files regenerated, or mismatch> |

**Result: X/6 passed, Y issues found**
```

If all read-only checks pass and Check 6 needed no changes: "Marketplace is healthy."
If any of checks 1–5 fail: list the issues as a numbered action list.

Checks 1–5 are report-only — do NOT fix what they surface; the user decides. Check 6 is the sole exception: it regenerates the generated plugin-table regions in `CLAUDE.md` and `README.md` from the source manifests, and reports what it changed.
