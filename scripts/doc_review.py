#!/usr/bin/env python3
"""A reminder, never a failure: which presentation docs describe files changed on this branch.

`review_lines(changed, declaration)` is pure. `main()` reads the paths changed since the merge-base with `main`
(committed, working tree, untracked) and prints one line per doc whose globs match; no `main`, no output.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

DECLARATION = Path(__file__).with_name("doc_review.json")


def _regex(glob: str) -> "re.Pattern[str]":
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif glob.startswith("**", i):
            out, i = out + ".*", i + 2
        elif glob[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(glob[i]), i + 1
    return re.compile(out + r"\Z")


def review_lines(changed: list[str], declaration: dict[str, list[str]]) -> list[str]:
    lines = []
    for doc, globs in declaration.items():
        patterns = [_regex(g) for g in globs]
        hits = sorted({p for p in changed if p != doc and any(r.match(p) for r in patterns)})
        if hits:
            lines.append(f"review: {doc} describes {len(hits)} changed files (e.g. {', '.join(hits[:2])}); "
                         "read it and confirm it is current, or update it")
    return lines


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=30, check=True).stdout


def changed_paths() -> "list[str] | None":
    try:
        base = _git("merge-base", "main", "HEAD").strip()
        names = _git("diff", "--name-only", base).splitlines()
        names += _git("ls-files", "--others", "--exclude-standard").splitlines()
    except (subprocess.SubprocessError, OSError):
        return None
    return sorted(set(names))


def main() -> int:
    changed = changed_paths()
    if changed is not None:
        for line in review_lines(changed, json.loads(DECLARATION.read_text())):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
