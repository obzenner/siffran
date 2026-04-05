---
name: checkup
description: "Run a structural health check on this marketplace repo. Verifies manifest integrity, cross-references, version hygiene, and documentation consistency. Use before committing, after adding plugins/methodologies, or when something feels off."
allowed-tools: [Read, Glob, Grep, Bash]
---

# Marketplace Healthcheck

You are running a structural integrity check on this plugin marketplace repo. Execute every check below in order. Do NOT skip checks. Do NOT fix issues — only report them.

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

## Check 6: CLAUDE.md consistency

- Read `CLAUDE.md` at repo root
- Extract the plugin table (the `| Plugin | Version | Description |` table)
- Compare against marketplace.json:
  - Every plugin in marketplace.json should appear in CLAUDE.md
  - Every plugin in CLAUDE.md should exist in marketplace.json
  - Versions in CLAUDE.md should match plugin.json versions
- Report: PASS or FAIL with specific mismatches

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
| 6. CLAUDE.md consistency | PASS/FAIL | <details if FAIL> |

**Result: X/6 passed, Y issues found**
```

If all checks pass: "Marketplace is healthy."
If any fail: list the issues as a numbered action list.

Do NOT automatically fix anything. Report only. The user decides what to fix.
