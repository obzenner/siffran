#!/usr/bin/env python3
"""Verify the generated plugin tables in CLAUDE.md and README.md match the manifests.

Invoked by `make docs-check`. The tables between the BEGIN/END GENERATED markers are written by
the `checkup` skill from each plugin.json; this script does not rewrite them, it only reports
drift. Regeneration needs a Claude session (the skill), so the split is deliberate: a Makefile
target can always TELL you the docs are stale, even where it cannot fix them.

It also guards the UI developer guide (``plugins/empirica/ui/CLAUDE.md``): every ``make <target>`` it names is a target
``make help`` lists (a line ``target: ... ## description`` in the Makefile, the same rule ``make help`` reads), and it stays
at most ``GUIDE_MAX_LINES`` lines, so the guide cannot drift from the lifecycle or grow into prose. And every image
README.md names under ``doc/images/`` exists (``make readme-image`` writes them; this only checks, it renders nothing).

Exit 0 = in sync; 1 = stale or markers missing, with the offending file named.

Usage: check_generated_docs.py [--print|--write]

``--write`` deterministically replaces only the marked generated regions.  It is used by the
Makefile when a non-interactive version bump needs the same canonical output as the checkup skill.
"""
import json
import re
import sys
from pathlib import Path

MARKETPLACE = Path(".claude-plugin/marketplace.json")
TARGETS = ("CLAUDE.md", "README.md")
REGION = re.compile(r"BEGIN GENERATED: plugins.*?-->\n(.*?)<!-- END GENERATED: plugins", re.S)
GUIDE = Path("plugins/empirica/ui/CLAUDE.md")
GUIDE_MAX_LINES = 150
HELP_TARGET = re.compile(r"^([a-zA-Z0-9_-]+):.*?## ", re.M)  # the line make help prints (its awk pattern)
NAMED_TARGET = re.compile(r"\bmake ([a-zA-Z0-9][a-zA-Z0-9_-]*)")
IMAGE_REF = re.compile(r"""(?:\]\(|src=["']|srcset=["'])\s*(?:\./)?(doc/images/[^\s"')]+)""")
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


def guide_problems(guide: Path = GUIDE, makefile: Path = Path("Makefile")) -> list[str]:
    """What is wrong with the UI guide: a ``make`` target it names that ``make help`` does not list, or too many lines."""
    if not guide.is_file():
        return [f"{guide}: file is missing"]
    text = guide.read_text()
    known = set(HELP_TARGET.findall(makefile.read_text()))
    problems = [f"{guide}: names `make {name}`, which make help does not list"
                for name in sorted(set(NAMED_TARGET.findall(text)) - known)]
    lines = len(text.splitlines())
    if lines > GUIDE_MAX_LINES:
        problems.append(f"{guide}: {lines} lines, over the {GUIDE_MAX_LINES}-line limit (keep it commands, not prose)")
    return problems


def image_problems(readme: Path = Path("README.md"), root: Path = Path(".")) -> list[str]:
    """Each image under doc/images/ that ``readme`` names (Markdown, ``src`` or ``srcset``) and that does not exist."""
    if not readme.is_file():
        return []
    named = dict.fromkeys(IMAGE_REF.findall(readme.read_text()))
    return [f"{readme.name}: names {ref}, which does not exist (make readme-image writes the README's images)"
            for ref in named if not (root / ref).is_file()]


def main() -> int:
    want = canonical_table()
    if "--print" in sys.argv[1:]:
        print(want, end="")
        return 0
    if "--write" in sys.argv[1:]:
        for name in TARGETS:
            path = Path(name)
            text = path.read_text()
            match = REGION.search(text)
            if not match:
                print(f"  FAIL {name}: BEGIN/END GENERATED markers missing or malformed",
                      file=sys.stderr)
                return 1
            path.write_text(text[:match.start(1)] + want + text[match.end(1):])
        print(f"  wrote: generated tables in {', '.join(TARGETS)}")
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
    problems += guide_problems()
    problems += image_problems()

    if problems:
        for problem in problems:
            print(f"  FAIL {problem}", file=sys.stderr)
        return 1

    print(f"  ok: generated tables in {', '.join(TARGETS)} match the manifests; {GUIDE} names only make help's targets; README's doc/images exist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
