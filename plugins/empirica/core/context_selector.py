"""Pure deterministic section selection over a supplied canonical PublicContract."""
from __future__ import annotations

from collections.abc import Mapping


def select_sections(registry: Mapping[str, object], operation_context: str,
                    reason_codes: list[str], terminal_status: str | None) -> list[str]:
    """Project sections without owning a second policy table or performing I/O."""
    selector = registry["presentation_selector"]
    contexts = selector["context_sections"]
    unknown = list(selector["unknown_reason_sections"])
    reasons = registry["reasons"]
    terminal = set(registry["statuses"]) - {"active"}
    if operation_context not in contexts or type(reason_codes) is not list:
        return unknown
    if terminal_status is not None and terminal_status not in terminal:
        return unknown
    if any(type(code) is not str or code not in reasons for code in reason_codes):
        return unknown
    values = list(contexts[operation_context])
    for code in reason_codes:
        values.extend(reasons[code]["sections"])
    if terminal_status is not None:
        values.extend(selector["terminal_sections"])
    return list(dict.fromkeys(values))
