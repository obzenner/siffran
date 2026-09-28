#!/usr/bin/env python3
"""Measure Empirica model-visible context economy from a retained native transcript slice.

QUAL-1 D6: the qualification ``context_economy`` row needs a reproducible counter so independent
operators produce identical totals. The measurement object is the model-visible tool result TEXT
each Empirica tool returns (Pi ``content`` text; Claude MCP ``content`` text) — the characters the
model actually reads — counted in Unicode code points. The envelope size (the full serialized MCP
result including any duplicate ``structuredContent``) is reported SEPARATELY as "normalized
envelope bytes": the UTF-8 byte length of one canonical compact reserialization of the extracted
result. It is a normalized figure (native MCP field-name normalization/extraction is the
operator's slice step), not necessarily the exact native wire byte count, and is never conflated
with the model-visible character count.

Input: a newline-delimited JSON file the operator extracts from the retained transcript/log, one
object per Empirica tool call over the primary run (activation to terminal), in call order:

    {"tool": "empirica_read", "result": {"content": [{"type": "text", "text": "..."}], ...}}

Only Empirica tools are counted. Each object's ``result`` is one host tool result: the model-visible
text is the concatenation of its ``content`` items whose ``type`` is ``text``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

EMPIRICA_TOOLS = ("empirica_read", "empirica_observe", "report_convergence")


class TranscriptError(ValueError):
    """The transcript slice is not the documented newline-delimited tool-result format."""


def model_visible_text(result: dict) -> str:
    """The exact text a host returns to the model: concatenated ``text`` content items.

    An Empirica tool result must carry at least one well-formed text content item. A result whose
    ``content`` is absent/not a list, whose text items carry non-string ``text``, or that carries
    no model-visible text at all is an error (``TranscriptError``) — never silently counted as
    zero, which would let an incomplete transcript undercount the model-visible budget.
    """
    if not isinstance(result, dict):
        raise TranscriptError("each result must be a JSON object")
    content = result.get("content", [])
    if not isinstance(content, list):
        raise TranscriptError("result.content must be a list")
    parts = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if not isinstance(text, str):
            raise TranscriptError("result.content text item carries missing or malformed text")
        parts.append(text)
    if not parts:
        raise TranscriptError("Empirica tool result carries no model-visible text")
    return "".join(parts)


def parse_calls(lines) -> list[dict]:
    calls = []
    for number, raw in enumerate(lines, start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise TranscriptError(f"line {number}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict) or "tool" not in row or "result" not in row:
            raise TranscriptError(f"line {number}: each row needs 'tool' and 'result'")
        calls.append(row)
    return calls


def measure(calls: list[dict]) -> dict:
    """Return per-call and aggregate model-visible character counts plus transport bytes.

    ``max_chars``/``total_chars`` count only Empirica tool results. ``normalized_envelope_bytes``
    are the UTF-8 byte size of one canonical compact reserialization of the full result (text plus
    structuredContent), tracked apart from the model-visible character budget.
    """
    rows = []
    for row in calls:
        tool = row["tool"]
        if tool not in EMPIRICA_TOOLS:
            continue
        text = model_visible_text(row["result"])
        envelope = json.dumps(row["result"], ensure_ascii=False, separators=(",", ":"))
        rows.append({
            "tool": tool,
            "chars": len(text),
            "normalized_envelope_bytes": len(envelope.encode("utf-8")),
        })
    return {
        "empirica_calls": len(rows),
        "max_chars": max((r["chars"] for r in rows), default=0),
        "total_chars": sum(r["chars"] for r in rows),
        "max_normalized_envelope_bytes": max((r["normalized_envelope_bytes"] for r in rows),
                                             default=0),
        "total_normalized_envelope_bytes": sum(r["normalized_envelope_bytes"] for r in rows),
        "calls": rows,
    }


def render(report: dict) -> str:
    lines = [
        f"Empirica tool calls: {report['empirica_calls']}",
        f"Max single model-visible result: {report['max_chars']} chars",
        f"Total model-visible over the primary run: {report['total_chars']} chars",
        f"Normalized envelope bytes (tracked separately): max "
        f"{report['max_normalized_envelope_bytes']} bytes, total "
        f"{report['total_normalized_envelope_bytes']} bytes",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="newline-delimited JSON of Empirica tool results, in call order")
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument("--max-chars", type=int, default=None,
                        help="fail (exit 1) if any single model-visible result exceeds this ceiling")
    args = parser.parse_args(argv)
    try:
        lines = Path(args.path).read_text(encoding="utf-8").splitlines()
        report = measure(parse_calls(lines))
    except OSError as exc:
        print(f"cannot read {args.path}: {exc}")
        return 2
    except TranscriptError as exc:
        print(f"invalid transcript slice: {exc}")
        return 2
    print(json.dumps(report, indent=2) if args.json else render(report))
    if args.max_chars is not None and report["max_chars"] > args.max_chars:
        print(f"context economy: max single result {report['max_chars']} exceeds "
              f"ceiling {args.max_chars}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
