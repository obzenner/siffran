#!/usr/bin/env python3
"""Check that the named receipt inputs exist and lie outside the checkout. Runs nothing; prints one line per input.

Usage: receipt_args.py HOST=... TRANSCRIPT=<file> STATE=<file> CHILD_SESSION=<file> VERSION_OUTPUT=<file> OUTPUT=<path> ARTIFACTS_REPO=<dir>
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FILES = ("TRANSCRIPT", "STATE", "CHILD_SESSION", "VERSION_OUTPUT")
REQUIRED = ("HOST", *FILES, "COMMAND", "OUTPUT", "ARTIFACTS_REPO")


def problems(args: dict[str, str]) -> list[str]:
    found = [f"{name} is missing" for name in REQUIRED if not args.get(name)]
    for name in (*FILES, "ARTIFACTS_REPO"):
        value = args.get(name)
        if not value:
            continue
        path = Path(value).expanduser().resolve()
        if name == "ARTIFACTS_REPO":
            if not path.is_dir():
                found.append(f"{name} {path} is not a directory")
        elif not path.is_file():
            found.append(f"{name} {path} is not a file")
        elif path == ROOT or ROOT in path.parents:
            found.append(f"{name} {path} is inside the source checkout")
    return found


def main() -> int:
    args = dict(item.split("=", 1) for item in sys.argv[1:] if "=" in item)
    found = problems(args)
    for line in found or ["ok: the inputs exist; the receipt itself is still produced and checked by Make"]:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
