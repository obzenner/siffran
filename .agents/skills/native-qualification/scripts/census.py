#!/usr/bin/env python3
"""Print the checkout facts a qualification records: HEAD, branch, dirty state, plugin versions. Read-only."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False).stdout.strip()


def main() -> int:
    versions = {}
    for manifest in sorted(ROOT.glob("plugins/*/.claude-plugin/plugin.json")):
        versions[manifest.parents[1].name] = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    print(json.dumps({"root": str(ROOT), "head": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
                      "dirty": bool(status), "changed_paths": len(status.splitlines()), "plugin_versions": versions},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
