#!/usr/bin/env python3
"""Verify native Claude Code admission and optional calls for Empirica's MCP server."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED_TOOLS = ("empirica_read", "empirica_observe", "report_convergence")
_TOOL_PREFIX = "mcp__plugin_empirica_empirica__"
_REJECTION_MARKERS = (
    "skipping tool",
    "excluded",
    "input schema",
    "connection failed",
    "failed to connect",
)


def _record_strings(record: dict) -> list[str]:
    metadata = {"timestamp", "sessionId", "cwd"}
    return [
        value
        for key, value in record.items()
        if key not in metadata and isinstance(value, str)
    ]


def check_init_result(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return [f"cannot read {path}: {exc}"]
    except (TypeError, ValueError) as exc:
        return [f"{path}: invalid Claude result JSON: {exc}"]
    rows = payload if isinstance(payload, list) else [payload]
    initializations = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("type") == "system"
        and row.get("subtype") == "init"
    ]
    if len(initializations) != 1:
        return [f"{path}: expected exactly one Claude system init record"]
    tools = initializations[0].get("tools")
    if not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools):
        return [f"{path}: Claude system init tools must be an array of strings"]
    expected = {_TOOL_PREFIX + name for name in REQUIRED_TOOLS}
    missing = sorted(expected - set(tools))
    if missing:
        return [f"{path}: Claude init omitted required tools: {', '.join(missing)}"]
    return []


def check_log(
    path: Path,
    *,
    required_calls: tuple[str, ...] = (),
    expected_version: str | None = None,
) -> list[str]:
    errors: list[str] = []
    records: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [f"cannot read {path}: {exc}"]
    if not lines:
        return [f"{path}: log is empty"]
    for line_number, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
        except (TypeError, ValueError) as exc:
            errors.append(f"{path}:{line_number}: invalid JSON: {exc}")
            continue
        if not isinstance(record, dict):
            errors.append(f"{path}:{line_number}: record must be an object")
            continue
        records.append(record)

    messages = [value for record in records for value in _record_strings(record)]
    if not any("Successfully connected (transport: stdio)" in text for text in messages):
        errors.append(f"{path}: no successful stdio connection")
    capabilities = [
        text for text in messages if "Connection established with capabilities:" in text
    ]
    if not capabilities:
        errors.append(f"{path}: no established MCP capability record")
    elif not any('"hasTools":true' in text for text in capabilities):
        errors.append(f"{path}: established connection did not advertise tools")
    if expected_version and not any(
        f'"version":"{expected_version}"' in text for text in capabilities
    ):
        errors.append(f"{path}: server version is not {expected_version}")

    for message in messages:
        lowered = message.lower()
        if any(marker in lowered for marker in _REJECTION_MARKERS):
            errors.append(f"{path}: Claude MCP rejection: {message}")
    for record in records:
        message = record.get("error")
        if isinstance(message, str) and not any(
            marker in message.lower() for marker in _REJECTION_MARKERS
        ):
            errors.append(f"{path}: Claude MCP error: {message}")

    unknown = sorted(set(required_calls) - set(REQUIRED_TOOLS))
    if unknown:
        errors.append(f"unknown required Empirica tools: {', '.join(unknown)}")
    for tool in required_calls:
        called = any(f"Calling MCP tool: {tool}" in text for text in messages)
        completed = any(f"Tool '{tool}' completed successfully" in text for text in messages)
        if not called:
            errors.append(f"{path}: no native call observed for {tool}")
        if not completed:
            errors.append(f"{path}: no successful native completion observed for {tool}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--require-call", action="append", default=[], choices=REQUIRED_TOOLS)
    parser.add_argument("--expected-version")
    parser.add_argument("--init-result", type=Path)
    args = parser.parse_args()
    errors = check_log(
        args.log,
        required_calls=tuple(args.require_call),
        expected_version=args.expected_version,
    )
    if args.init_result is not None:
        errors.extend(check_init_result(args.init_result))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    calls = f"; calls={','.join(args.require_call)}" if args.require_call else ""
    print(f"ok: Claude admitted Empirica MCP from {args.log}{calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
