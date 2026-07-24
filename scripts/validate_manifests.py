#!/usr/bin/env python3
"""Validate the marketplace catalog against the plugins on disk.

Invoked by `make validate`. A real script rather than a Makefile heredoc because GNU Make 3.81
(the macOS default) runs each recipe line in its own shell, so multi-line heredocs silently
break — and because a validator worth having is worth testing and reading on its own.

Checks, each of which has bitten this repo at least once:
  * every marketplace entry resolves to a real directory with a plugin.json
  * plugin.json carries name/description/version, and version is semver
  * the plugin name agrees with the directory name AND the marketplace entry
  * no plugin exists on disk without being registered in the marketplace
  * every SKILL.md has frontmatter name/description, and name matches its directory

Exit 0 = consistent; 1 = problems, listed on stderr.
"""
import json
import re
import sys
from pathlib import Path

SEMVER = re.compile(r"\d+\.\d+\.\d+")
MARKETPLACE = Path(".claude-plugin/marketplace.json")
PLUGINS = Path("plugins")


def main() -> int:
    errors: list[str] = []

    try:
        catalog = json.loads(MARKETPLACE.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  FAIL {MARKETPLACE}: {exc}", file=sys.stderr)
        return 1

    listed = {p.get("name"): p for p in catalog.get("plugins", [])}
    for name, entry in listed.items():
        source = Path(entry.get("source", ""))
        if not source.is_dir():
            errors.append(f"{name}: source {source} does not exist")
            continue
        manifest = source / ".claude-plugin" / "plugin.json"
        if not manifest.is_file():
            errors.append(f"{name}: missing {manifest}")
            continue
        try:
            data = json.loads(manifest.read_text())
        except json.JSONDecodeError as exc:
            errors.append(f"{name}: {manifest} is not valid JSON ({exc})")
            continue
        for field in ("name", "description", "version"):
            if not data.get(field):
                errors.append(f"{name}: plugin.json missing '{field}'")
        if data.get("name") != name:
            errors.append(f"{name}: plugin.json name is {data.get('name')!r}, "
                          f"marketplace says {name!r}")
        if data.get("name") != source.name:
            errors.append(f"{name}: directory is {source.name!r}, plugin.json says "
                          f"{data.get('name')!r}")
        version = data.get("version", "")
        if not SEMVER.fullmatch(str(version)):
            errors.append(f"{name}: version {version!r} is not MAJOR.MINOR.PATCH")

    # A plugin on disk but absent from the catalog is invisible to users — the failure mode
    # that looks like "my plugin isn't loading".
    for manifest in sorted(PLUGINS.glob("*/.claude-plugin/plugin.json")):
        directory = manifest.parts[1]
        if directory not in listed:
            errors.append(f"{directory}: exists on disk but is not in {MARKETPLACE}")

    for skill in sorted(PLUGINS.glob("*/skills/*/SKILL.md")):
        text = skill.read_text()
        match = re.search(r"^name:\s*(\S+)", text, re.M)
        if not match:
            errors.append(f"{skill}: frontmatter has no 'name'")
        elif match.group(1) != skill.parent.name:
            errors.append(f"{skill}: frontmatter name {match.group(1)!r} != directory "
                          f"{skill.parent.name!r}")
        if not re.search(r"^description:", text, re.M):
            errors.append(f"{skill}: frontmatter has no 'description'")

    if errors:
        for error in errors:
            print(f"  FAIL {error}", file=sys.stderr)
        return 1

    skills = len(list(PLUGINS.glob("*/skills/*/SKILL.md")))
    print(f"  ok: {len(listed)} plugin(s), {skills} skill(s), manifests consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
