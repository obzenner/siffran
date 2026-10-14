#!/usr/bin/env python3
"""Check the repository's licensing: LICENSE, the manifests' SPDX reference, and the vendored licence texts.

- ``LICENSE`` exists and starts with the PolyForm Internal Use required-notice line;
- every Claude ``plugin.json`` and the root and Methodologist ``package.json`` declare exactly LICENSE_REF;
- every git-tracked package.json (outside node_modules) declares it too;
- ``dist/LICENSE-tailwindcss`` exists beside the Tailwind output;
- every top-level directory under ``plugins/empirica/ui/vendor/`` holds a ``LICENSE``.

``check(root)`` returns the list of problems; ``main`` prints them and exits 1 if there are any.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LICENSE_REF = "LicenseRef-PolyForm-Internal-Use-1.0.0"
REQUIRED_NOTICE = "Required Notice: Copyright 2026 Dmitry Lambrianov (https://github.com/obzenner/siffran)"
VENDOR = "plugins/empirica/ui/vendor"
TAILWIND = "plugins/empirica/ui/dist/LICENSE-tailwindcss"


def _declared(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("license")
    except (OSError, ValueError, AttributeError) as error:
        return f"unreadable ({error})"


def _package_jsons(root: Path) -> list[str]:
    """Every git-tracked package.json outside node_modules (all files named so, if root is not a git tree)."""
    try:
        out = subprocess.run(["git", "ls-files", "--", "package.json", "**/package.json"], cwd=root,
                             capture_output=True, text=True, check=True, timeout=30).stdout.split()
    except (OSError, subprocess.SubprocessError):
        out = []
    if not out:
        out = [str(p.relative_to(root)) for p in root.rglob("package.json")]
    return sorted(p for p in out if "node_modules" not in Path(p).parts)


def check(root: Path) -> list[str]:
    problems: list[str] = []
    licence = root / "LICENSE"
    if not licence.is_file():
        problems.append("LICENSE is missing")
    elif not licence.read_text(encoding="utf-8").startswith(REQUIRED_NOTICE + "\n"):
        problems.append(f"LICENSE does not start with the required notice line: {REQUIRED_NOTICE}")
    manifests = sorted(root.glob("plugins/*/.claude-plugin/plugin.json"))
    manifests += [root / p for p in _package_jsons(root)]
    for path in manifests:
        declared = _declared(path) if path.is_file() else "file missing"
        if declared != LICENSE_REF:
            problems.append(f"{path.relative_to(root)}: license is {declared!r}, expected {LICENSE_REF!r}")
    vendor = root / VENDOR
    for sub in sorted(p for p in vendor.iterdir() if p.is_dir()) if vendor.is_dir() else []:
        if not (sub / "LICENSE").is_file():
            problems.append(f"{sub.relative_to(root)} has no LICENSE")
    if not (root / TAILWIND).is_file():
        problems.append(f"{TAILWIND} is missing")
    return problems


def main() -> int:
    problems = check(ROOT)
    for problem in problems:
        print(f"  FAIL {problem}", file=sys.stderr)
    if not problems:
        print("ok: LICENSE, manifests and vendored licence texts")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
