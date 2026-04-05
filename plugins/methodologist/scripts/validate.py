#!/usr/bin/env python3
"""Validate a skill registry against files on disk.

A registry.json declares its own structure via a "schema" block:
  - entries_key: which top-level key holds the entries array
  - files_dir: which subdirectory contains the corresponding .md files
  - required_fields: which fields every entry must have

Usage:
  python3 validate.py <path-to-skill-dir>
  python3 validate.py  # defaults to ../skills/think relative to this script

Exit code 0 = all checks pass, 1 = failures found.
"""

import json
import sys
from pathlib import Path


def validate(skill_dir: Path) -> list[str]:
    errors: list[str] = []

    registry_path = skill_dir / "registry.json"
    if not registry_path.exists():
        return [f"Missing: {registry_path}"]

    with open(registry_path) as f:
        registry = json.load(f)

    schema = registry.get("schema")
    if not schema:
        return ["registry.json missing 'schema' block"]

    entries_key = schema.get("entries_key")
    files_dir = schema.get("files_dir")
    required_fields = set(schema.get("required_fields", []))

    if not entries_key or not files_dir:
        return ["schema must define 'entries_key' and 'files_dir'"]

    target_dir = skill_dir / files_dir
    if not target_dir.exists():
        return [f"Missing directory: {target_dir}"]

    entries = registry.get(entries_key, [])
    registry_names: set[str] = set()

    for i, entry in enumerate(entries):
        missing = required_fields - set(entry.keys())
        if missing:
            errors.append(f"Entry {i}: missing fields {missing}")

        name = entry.get("name")
        if not name:
            errors.append(f"Entry {i}: missing 'name'")
            continue

        registry_names.add(name)
        if not (target_dir / f"{name}.md").exists():
            errors.append(f"Registry has '{name}' but {name}.md not found in {files_dir}/")

    file_names = {p.stem for p in target_dir.glob("*.md")}
    for name in sorted(file_names - registry_names):
        errors.append(f"File '{name}.md' exists in {files_dir}/ but not in registry")

    return errors


def main() -> int:
    if len(sys.argv) > 1:
        skill_dir = Path(sys.argv[1])
    else:
        skill_dir = Path(__file__).parent.parent / "skills" / "think"

    errors = validate(skill_dir)

    if errors:
        print("FAIL")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(f"PASS — {skill_dir.name} registry and files are in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
