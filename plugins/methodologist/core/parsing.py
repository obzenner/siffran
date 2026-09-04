"""Parse a methodology markdown file and a registry.json into core models.

Pure text/dict -> model transforms plus thin path-loading adapters. The pure
functions take strings and dicts so the core stays host-neutral; the loaders
are the single place that touches the filesystem, and they take the path as an
argument rather than assuming any fixed layout.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import (
    Methodology,
    Phase,
    Registry,
    RegistryEntry,
    RegistrySchema,
)

_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_LINEAGE_RE = re.compile(r"^\*\*Lineage:\*\*\s*(.+?)\s*$", re.MULTILINE)
_PREVENTS_RE = re.compile(r"^\*\*Prevents:\*\*\s*(.+?)\s*$", re.MULTILINE)
_CORE_RE = re.compile(
    r"^##\s+Core principle\s*$\n(.*?)(?=^#{2,3}\s)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_PHASE_RE = re.compile(r"^###\s+Phase\s+(\d+)\s*:\s*(.+?)\s*$", re.MULTILINE)
_OUTPUT_RE = re.compile(r"^\*\*Output format[^\n]*\n", re.MULTILINE)
_FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


def _extract_output_format(body: str) -> str | None:
    """The fenced block a phase declares under '**Output format:**'.

    None  -> the phase declares no output format.
    ""    -> it declares one but no fenced block follows (malformed).
    """

    marker = _OUTPUT_RE.search(body)
    if not marker:
        return None
    fence = _FENCE_RE.search(body, marker.end())
    if not fence:
        return ""
    return fence.group(1).strip()


def parse_methodology(text: str, name: str) -> Methodology:
    """Parse the text of a methodology file into a `Methodology`."""

    headers = list(_PHASE_RE.finditer(text))
    phases = []
    for i, header in enumerate(headers):
        start = header.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[start:end]
        phases.append(
            Phase(
                number=int(header.group(1)),
                title=header.group(2).strip(),
                output_format=_extract_output_format(body),
            )
        )

    # Metadata comes from the preamble (before the first phase, if any).
    preamble = text[: headers[0].start()] if headers else text
    title_match = _TITLE_RE.search(preamble)
    lineage_match = _LINEAGE_RE.search(preamble)
    prevents_match = _PREVENTS_RE.search(preamble)
    # Core principle is captured up to the next header; search the full text so
    # the closing lookahead sees the "## Phases"/"### Phase" that follows it.
    core_match = _CORE_RE.search(text)

    return Methodology(
        name=name,
        lineage=lineage_match.group(1).strip() if lineage_match else "",
        prevents=prevents_match.group(1).strip() if prevents_match else "",
        phases=tuple(phases),
        title=title_match.group(1).strip() if title_match else None,
        core_principle=core_match.group(1).strip() if core_match else None,
    )


def parse_registry(data: dict[str, Any]) -> Registry:
    """Parse a loaded registry.json object into a `Registry`."""

    schema_data = data.get("schema") or {}
    schema = RegistrySchema(
        entries_key=schema_data.get("entries_key", ""),
        files_dir=schema_data.get("files_dir", ""),
        required_fields=tuple(schema_data.get("required_fields", [])),
    )
    if schema.entries_key:
        entries = tuple(
            RegistryEntry(name=entry.get("name", ""), fields=entry)
            for entry in data.get(schema.entries_key, [])
        )
    else:
        entries = ()
    return Registry(schema=schema, entries=entries)


def load_registry(path: str | Path) -> Registry:
    """Read and parse a registry.json from disk (I/O adapter)."""

    return parse_registry(json.loads(Path(path).read_text()))


def load_methodology(path: str | Path) -> Methodology:
    """Read and parse a methodology .md from disk (I/O adapter).

    The methodology name is the file stem, matching the registry convention.
    """

    p = Path(path)
    return parse_methodology(p.read_text(), name=p.stem)
