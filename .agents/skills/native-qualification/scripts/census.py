#!/usr/bin/env python3
"""Print the checkout facts a qualification records: HEAD, branch, dirty state, plugin versions. Read-only."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


class GitFailed(Exception):
    """git exited nonzero: the checkout's facts are unknown, so none is reported."""


def git(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise GitFailed(f"git {' '.join(args)} exited {r.returncode}: {r.stderr.strip()}")
    return r.stdout.strip()


def main() -> int:
    try:
        return census()
    except GitFailed as e:
        print(e, file=sys.stderr)
        return 1


def census() -> int:
    versions = {}
    for manifest in sorted(ROOT.glob("plugins/*/.claude-plugin/plugin.json")):
        versions[manifest.parents[1].name] = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    head, branch = git("rev-parse", "HEAD"), git("branch", "--show-current")
    print(json.dumps({"root": str(ROOT), "head": head, "branch": branch,
                      "dirty": bool(status), "changed_paths": len(status.splitlines()), "plugin_versions": versions},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
