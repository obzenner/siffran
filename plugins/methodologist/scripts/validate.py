#!/usr/bin/env python3
"""Validate a skill registry and its methodology files against the core models.

Delegates the registry/file sync check to the host-neutral Methodologist core
(`plugins/methodologist/core`) and layers the strengthened structural checks on
top: every methodology file must carry six numbered phases, a lineage and a
prevents line, and a well-formed output block wherever it declares one; the
shared stance reference must carry its mandatory declaration.

A registry.json declares its own structure via a "schema" block:
  - entries_key: which top-level key holds the entries array
  - files_dir: which subdirectory contains the corresponding .md files
  - required_fields: which fields every entry must have

Usage:
  python3 validate.py <path-to-skill-dir>
  python3 validate.py  # defaults to ../skills/think relative to this script

Exit code 0 = all checks pass, 1 = failures found.
"""

import sys
from pathlib import Path

# The core is a sibling package under plugins/methodologist/; make it importable
# without a package install so `make validate` and a bare `python3` both work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import (  # noqa: E402
    load_methodology,
    load_registry,
    validate_methodology_structure,
    validate_registry_against_files,
    validate_stance,
)


def validate(skill_dir: Path) -> list[str]:
    registry_path = skill_dir / "registry.json"
    if not registry_path.exists():
        return [f"Missing: {registry_path}"]

    registry = load_registry(registry_path)
    if not registry.schema.entries_key or not registry.schema.files_dir:
        return ["schema must define 'entries_key' and 'files_dir'"]

    target_dir = skill_dir / registry.schema.files_dir
    if not target_dir.exists():
        return [f"Missing directory: {target_dir}"]

    md_files = sorted(target_dir.glob("*.md"))
    file_stems = {p.stem for p in md_files}

    errors = validate_registry_against_files(registry, file_stems)

    for md in md_files:
        errors.extend(validate_methodology_structure(load_methodology(md)))

    stance_path = skill_dir / "references" / "evidence-over-recall.md"
    if stance_path.exists():
        errors.extend(validate_stance(stance_path.read_text()))
    else:
        errors.append(f"Missing stance reference: {stance_path}")

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
