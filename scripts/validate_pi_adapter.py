#!/usr/bin/env python3
"""Validate a Pi adapter package (methodologist or empirica).

Usage: ``validate_pi_adapter.py [<adapter-dir-relative-to-repo-root>]``
(defaults to the Methodologist adapter for backwards compatibility).

Layers, so the check is meaningful whether or not tooling exists:

  1. Static/package validation (always, pure Python, no network): the ``pi``
     package manifest is well-formed, its declared entry points exist, and the
     source performs no runtime filesystem writes and touches no shared
     ``.pi``/``.claude`` state (the invariants the task pins — a Pi adapter holds
     transient state in memory / behind its transport, never on the host).
  2. Bridge smoke (only if the adapter ships a ``bridge.py`` and ``python`` is
     usable): pipe one request envelope through the stdio bridge and assert it
     answers with a well-formed response envelope. Deterministic, no network —
     the bridge must never crash the caller's gate, so even a rejected request
     comes back as a typed ``Fault`` (empirica/v1).
  3. Dynamic validation (only if ``node`` is on PATH): run the adapter's
     ``node --test`` suite. Node >= 22.6 strips TypeScript types natively, so this
     needs no install. When ``node`` is absent the dynamic layer is skipped and
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
DEFAULT_ADAPTER = Path("plugins") / "methodologist" / "adapters" / "pi"

# Runtime filesystem writes and shared-state paths that must never appear in the
# adapter source: a Pi adapter keeps transient state in memory and moves domain
# state through its transport (ADR-30/ADR-32), never onto the host. `readFileSync`
# etc. are allowed — reading is not a write; `spawn` is allowed — a transport may
# invoke a bridge process without itself writing to disk.
FORBIDDEN_WRITE = re.compile(
    r"\b(writeFile|writeFileSync|appendFile|appendFileSync|mkdir|mkdirSync|"
    r"rm|rmSync|unlink|unlinkSync|createWriteStream|rename|renameSync)\b"
)
FORBIDDEN_STATE = re.compile(r"""['"`][^'"`]*\.(?:pi|claude)\b""")

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+].+)?$")


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def check_manifest(adapter: Path, errors: list[str]) -> dict:
    manifest_path = adapter / "package.json"
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
        target = (adapter / entry).resolve()
        if not target.is_file():
            errors.append(f"{rel(manifest_path)}: pi.extensions entry {entry!r} does not exist")
    return manifest


def check_layout(adapter: Path, errors: list[str]) -> None:
    if not (adapter / "tsconfig.json").is_file():
        errors.append(f"{rel(adapter / 'tsconfig.json')} is missing")
    tests = list((adapter / "test").glob("*.test.ts"))
    if not tests:
        errors.append(f"no *.test.ts files under {rel(adapter / 'test')}")


def check_no_runtime_writes(adapter: Path, errors: list[str]) -> None:
    for source in sorted((adapter / "src").glob("*.ts")):
        text = source.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            code = line.split("//", 1)[0]  # ignore comment text
            if FORBIDDEN_WRITE.search(code):
                errors.append(f"{rel(source)}:{line_no}: filesystem write in adapter source")
            if FORBIDDEN_STATE.search(code):
                errors.append(f"{rel(source)}:{line_no}: reference to shared .pi/.claude state")


def check_bridge_smoke(adapter: Path, errors: list[str]) -> None:
    """If the adapter ships a stdio bridge, prove it answers a request with a
    well-formed envelope. Uses a request with an unknown command so the round-trip
    exercises the transport and service wiring without touching real run state."""
    bridge = adapter / "bridge.py"
    if not bridge.is_file():
        return
    python = shutil.which("python3") or shutil.which("python")
    if python is None:
        print("note: python not found — bridge smoke skipped")
        return
    request = json.dumps(
        {"protocol": "empirica/v1", "request_id": "smoke", "command": {"type": "__validate__"}}
    )
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [python, str(bridge)],
            input=request,
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        errors.append(f"{rel(bridge)}: bridge failed to run: {exc}")
        return
    if completed.returncode != 0:
        errors.append(f"{rel(bridge)}: bridge exited {completed.returncode}: {completed.stderr.strip()}")
        return
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        errors.append(f"{rel(bridge)}: bridge output is not JSON: {exc}")
        return
    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, dict) or "type" not in result:
        errors.append(f"{rel(bridge)}: bridge response is not a well-formed envelope")
        return
    if result.get("type") != "Fault":
        errors.append(f"{rel(bridge)}: an unknown command must fault, got {result.get('type')!r}")
        return
    print(f"ok: {rel(bridge)} answers a request with a typed envelope")


def run_tests(adapter: Path) -> int:
    node = shutil.which("node")
    if node is None:
        print("note: node not found — static/package validation only (tests skipped)")
        return 0
    tests = sorted(str(p) for p in (adapter / "test").glob("*.test.ts"))
    if not tests:
        return 0
    print(f"running {len(tests)} test file(s) via node --test")
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [node, "--test", *tests],
        cwd=adapter,
    )
    return completed.returncode


def main(argv: list[str]) -> int:
    adapter_rel = Path(argv[0]) if argv else DEFAULT_ADAPTER
    adapter = (ROOT / adapter_rel).resolve()
    if not adapter.is_dir():
        print(f"ERROR: adapter directory not found: {adapter_rel}", file=sys.stderr)
        return 1

    errors: list[str] = []
    check_manifest(adapter, errors)
    check_layout(adapter, errors)
    check_no_runtime_writes(adapter, errors)

    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"ok: {rel(adapter)} package is well-formed")

    check_bridge_smoke(adapter, errors)
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
        return 1

    return run_tests(adapter)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
