#!/usr/bin/env python3
"""Verify the generated plugin tables in CLAUDE.md and README.md match the manifests.

Invoked by `make docs-check`. The tables between the BEGIN/END GENERATED markers are written by
the `checkup` skill from each plugin.json; this script does not rewrite them, it only reports
drift. Regeneration needs a Claude session (the skill), so the split is deliberate: a Makefile
target can always TELL you the docs are stale, even where it cannot fix them.

Exit 0 = in sync; 1 = stale or markers missing, with the offending file named.

Usage: check_generated_docs.py [--print]   (--print emits the canonical table for pasting)
"""
import json
import re
import sys
from pathlib import Path

MARKETPLACE = Path(".claude-plugin/marketplace.json")
TARGETS = ("CLAUDE.md", "README.md")
REGION = re.compile(r"BEGIN GENERATED: plugins.*?-->\n(.*?)<!-- END GENERATED: plugins", re.S)
HEADER = "| Plugin | Version | Description |\n|--------|---------|-------------|\n"


def canonical_table() -> str:
    """The table the manifests imply. Plugin order follows marketplace.json, which is the
    catalog's own order — not alphabetical, so the docs mirror the catalog."""
    catalog = json.loads(MARKETPLACE.read_text())
    rows = []
    for entry in catalog["plugins"]:
        manifest = Path(entry["source"]) / ".claude-plugin" / "plugin.json"
        data = json.loads(manifest.read_text())
        # plugin.json is the single source of truth for the description — never the
        # marketplace entry, which drifts.
        rows.append(f"| `{data['name']}` | {data['version']} | {data['description']} |")
    return HEADER + "\n".join(rows) + "\n"


def main() -> int:
    want = canonical_table()
    if "--print" in sys.argv[1:]:
        print(want, end="")
        return 0

    problems: list[str] = []
    for name in TARGETS:
        path = Path(name)
        if not path.is_file():
            problems.append(f"{name}: file is missing")
            continue
        match = REGION.search(path.read_text())
        if not match:
            problems.append(f"{name}: BEGIN/END GENERATED markers missing or malformed")
        elif match.group(1) != want:
            problems.append(f"{name}: plugin table is stale — run the `checkup` skill "
                            f"(see `make docs`)")

    if problems:
        for problem in problems:
            print(f"  FAIL {problem}", file=sys.stderr)
        return 1

    print(f"  ok: generated tables in {', '.join(TARGETS)} match the manifests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
