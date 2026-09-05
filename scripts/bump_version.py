#!/usr/bin/env python3
"""Bump a plugin's semver version in its plugin.json.

Invoked by `make bump PLUGIN=<name> PART=major|minor|patch`.

plugin.json is the ONLY place a version lives (CLAUDE.md's and README.md's tables are generated
from it, and marketplace.json deliberately carries no version). Claude Code uses this field to
detect updates, so changing plugin code without bumping it means users never receive the change.

Usage: bump_version.py <plugin> [major|minor|patch]   (default: patch)
"""
import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: bump_version.py <plugin> [major|minor|patch]", file=sys.stderr)
        return 2
    plugin = argv[0]
    part = argv[1] if len(argv) > 1 and argv[1] else "patch"

    path = Path("plugins") / plugin / ".claude-plugin" / "plugin.json"
    if not path.is_file():
        print(f"  FAIL no such plugin: {plugin} ({path} missing)", file=sys.stderr)
        return 1

    data = json.loads(path.read_text())
    try:
        major, minor, patch = (int(x) for x in str(data["version"]).split("."))
    except (KeyError, ValueError):
        print(f"  FAIL {path}: version {data.get('version')!r} is not MAJOR.MINOR.PATCH",
              file=sys.stderr)
        return 1

    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    elif part == "patch":
        patch += 1
    else:
        print(f"  FAIL PART must be major|minor|patch, got {part!r}", file=sys.stderr)
        return 2

    previous = data["version"]
    data["version"] = f"{major}.{minor}.{patch}"
    path.write_text(json.dumps(data, indent=2) + "\n")

    # A host-specific Codex manifest is packaging metadata for the same plugin release. Keep it
    # synchronized when present; it is not an independent release stream.
    codex_path = path.parents[1] / ".codex-plugin" / "plugin.json"
    if codex_path.is_file():
        codex_data = json.loads(codex_path.read_text())
        codex_data["version"] = data["version"]
        codex_path.write_text(json.dumps(codex_data, indent=2) + "\n")

    print(f"  {plugin}: {previous} -> {data['version']}")
    if codex_path.is_file():
        print(f"  synced {codex_path}")
    print("  next: regenerate the doc tables (make docs), then make check")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
