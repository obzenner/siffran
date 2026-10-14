#!/usr/bin/env python3
"""Verify native Claude Code admission and optional calls for Empirica's MCP server."""
from __future__ import annotations

import argparse
import json
import re
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


_SERVER = "plugin:empirica:empirica"
# Claude Code 2.1.291 and later write ``--debug-file`` as text: ``2026-10-07T11:16:58.828Z [DEBUG] message``. Earlier
# builds wrote one JSON object per line. Both are read, and both are scoped to Empirica's own server (another server's failure is not Empirica's).
_TEXT_LINE = re.compile(
    r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z \[(?P<level>[A-Za-z]+)\] (?P<message>.*)$")


def _about_empirica(message: str) -> bool:
    """Whether a text-log entry concerns Empirica's server (the log also carries every other MCP server's failures).

    A tool-level message ("Skipping tool "empirica_read": …") names the tool but not the server, so a required tool
    name counts too."""
    return _SERVER in message or any(tool in message for tool in REQUIRED_TOOLS)


def _text_entries(lines: list[str], path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """``(level, message)`` of a text debug log, with a line that does not start an entry joined to the entry before it;
    only the entries about Empirica's server are returned, with the level the log gave them. Errors name the lines that
    are not a log at all."""
    entries: list[tuple[str, str]] = []
    errors: list[str] = []
    for number, line in enumerate(lines, 1):
        match = _TEXT_LINE.match(line)
        if match:
            entries.append((match["level"], match["message"]))
        elif entries:
            level, message = entries[-1]
            entries[-1] = (level, message + "\n" + line)
        elif line.strip():
            errors.append(f"{path}:{number}: not a Claude debug log line")
    return [(level, message) for level, message in entries if _about_empirica(message)], errors


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
    first = next((line for line in lines if line.strip()), "")
    messages: list[str] = []
    logged_errors: list[str] = []  # what the log itself marks as an error, in either format
    if not _TEXT_LINE.match(first) and first.strip():  # the earlier JSON-lines form
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
        logged_errors = [record["error"] for record in records if isinstance(record.get("error"), str)]
        scoped, scoped_errors = list(filter(_about_empirica, messages)), list(filter(_about_empirica, logged_errors))
    else:
        entries, text_errors = _text_entries(lines, path)
        errors.extend(text_errors)
        messages = [message for _level, message in entries]
        logged_errors = [message for level, message in entries if level.upper() == "ERROR"]
        scoped, scoped_errors = messages, logged_errors  # already scoped by _text_entries

    # What admits Empirica is read only from lines that name its own server: a tool-level line that merely names a
    # tool (kept in ``scoped`` for the rejection checks below) is not evidence that Empirica's server connected or ran.
    own = [text for text in scoped if _SERVER in text]
    if not any("Successfully connected (transport: stdio)" in text for text in own):
        errors.append(f"{path}: no successful stdio connection")
    capabilities = [
        text for text in own if "Connection established with capabilities:" in text
    ]
    if not capabilities:
        errors.append(f"{path}: no established MCP capability record")
    elif not any('"hasTools":true' in text for text in capabilities):
        errors.append(f"{path}: established connection did not advertise tools")
    if expected_version and not any(
        f'"version":"{expected_version}"' in text for text in capabilities
    ):
        errors.append(f"{path}: server version is not {expected_version}")

    # Another server's failure is not Empirica's, in either format (each branch above builds the scoped lists).
    for message in scoped:
        lowered = message.lower()
        if any(marker in lowered for marker in _REJECTION_MARKERS):
            errors.append(f"{path}: Claude MCP rejection: {message}")
    for message in scoped_errors:
        if not any(marker in message.lower() for marker in _REJECTION_MARKERS):  # those are reported above
            errors.append(f"{path}: Claude MCP error: {message}")

    unknown = sorted(set(required_calls) - set(REQUIRED_TOOLS))
    if unknown:
        errors.append(f"unknown required Empirica tools: {', '.join(unknown)}")
    for tool in required_calls:
        called = any(f"Calling MCP tool: {tool}" in text for text in own)
        completed = any(f"Tool '{tool}' completed successfully" in text for text in own)
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
