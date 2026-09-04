#!/usr/bin/env python3
"""Validate the Methodologist Pi adapter package.

Two layers, so the check is meaningful whether or not TypeScript tooling exists:

  1. Static/package validation (always, pure Python, no network): the `pi`
     package manifest is well-formed, its declared entry points exist, and the
     source performs no runtime filesystem writes and touches no shared
     `.pi`/`.claude` state (the invariants the task pins).
  2. Dynamic validation (only if `node` is on PATH): run the adapter's
     `node --test` suite. Node >= 22.6 strips TypeScript types natively, so this
     needs no install. When `node` is absent the dynamic layer is skipped and
     the static layer still gives a verdict.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "plugins" / "methodologist" / "adapters" / "pi"

# Runtime filesystem writes and shared-state paths that must never appear in the
# adapter source: the adapter keeps phase state in memory and renders to a Pi
# widget (ADR-32). `readFileSync` etc. are allowed — reading is not a write.
FORBIDDEN_WRITE = re.compile(
    r"\b(writeFile|writeFileSync|appendFile|appendFileSync|mkdir|mkdirSync|"
    r"rm|rmSync|unlink|unlinkSync|createWriteStream|rename|renameSync)\b"
)
FORBIDDEN_STATE = re.compile(r"""['"`][^'"`]*\.(?:pi|claude)\b""")

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+].+)?$")

errors: list[str] = []


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def check_manifest() -> dict:
    manifest_path = ADAPTER / "package.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{rel(manifest_path)}: {exc}")
        return {}

    if not manifest.get("name"):
        errors.append(f"{rel(manifest_path)}: missing 'name'")
    version = manifest.get("version", "")
    if not SEMVER.match(str(version)):
        errors.append(f"{rel(manifest_path)}: 'version' must be semver, got {version!r}")
    if manifest.get("type") != "module":
        errors.append(f"{rel(manifest_path)}: 'type' must be 'module' for TS ESM entries")

    extensions = manifest.get("pi", {}).get("extensions")
    if not isinstance(extensions, list) or not extensions:
        errors.append(f"{rel(manifest_path)}: 'pi.extensions' must be a non-empty array")
        extensions = []
    for entry in extensions:
        target = (ADAPTER / entry).resolve()
        if not target.is_file():
            errors.append(f"{rel(manifest_path)}: pi.extensions entry {entry!r} does not exist")
    return manifest


def check_layout() -> None:
    if not (ADAPTER / "tsconfig.json").is_file():
        errors.append("plugins/methodologist/adapters/pi/tsconfig.json is missing")
    tests = list((ADAPTER / "test").glob("*.test.ts"))
    if not tests:
        errors.append("no *.test.ts files under adapters/pi/test")


def check_no_runtime_writes() -> None:
    for source in sorted((ADAPTER / "src").glob("*.ts")):
        text = source.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            code = line.split("//", 1)[0]  # ignore comment text
            if FORBIDDEN_WRITE.search(code):
                errors.append(f"{rel(source)}:{line_no}: filesystem write in adapter source")
            if FORBIDDEN_STATE.search(code):
                errors.append(f"{rel(source)}:{line_no}: reference to shared .pi/.claude state")


def run_tests() -> int:
    node = shutil.which("node")
    if node is None:
        print("note: node not found — static/package validation only (tests skipped)")
        return 0
    tests = sorted(str(p) for p in (ADAPTER / "test").glob("*.test.ts"))
    if not tests:
        return 0
    print(f"running {len(tests)} test file(s) via node --test")
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [node, "--test", *tests],
        cwd=ADAPTER,
    )
    return completed.returncode


def main() -> int:
    if not ADAPTER.is_dir():
        print(f"ERROR: adapter directory not found: {rel(ADAPTER)}", file=sys.stderr)
        return 1

    check_manifest()
    check_layout()
    check_no_runtime_writes()

    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
        return 1
    print("ok: methodologist Pi adapter package is well-formed")

    return run_tests()


if __name__ == "__main__":
    raise SystemExit(main())
