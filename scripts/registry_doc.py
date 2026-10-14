#!/usr/bin/env python3
"""Generate the data parts of the host-boundary design document from the registry (ADR-0069).

``contracts/empirica/v2/host-boundaries.json`` owns each expectation's outcomes, scenario ids and sources and
every scenario row; ``doc/design/empirica-host-boundary-registry.md`` owns the prose (trigger, obligation,
failure policy, recovery, visibility, forbidden substitutions, decisions, rationale). The data parts of the
document sit between ``BEGIN GENERATED`` / ``END GENERATED`` markers and are rewritten here, so the two cannot
drift: ``--check`` (and ``make contract-check``) fails on any difference, ``make registry-doc`` rewrites.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "contracts" / "empirica" / "v2" / "host-boundaries.json"
DOCUMENT = ROOT / "doc" / "design" / "empirica-host-boundary-registry.md"

_MARKER = re.compile(r"<!-- BEGIN GENERATED: (?P<key>[^ ]+) \(make registry-doc\) -->\n(?P<body>.*?)<!-- END GENERATED -->",
                     re.S)


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _proof(scenario: dict) -> str:
    tests = scenario.get("covered_by") or []
    parts = [f"{len(tests)} test{'s' if len(tests) != 1 else ''}"] if tests else []
    if scenario.get("live_only"):
        parts.append(f"live-only `{scenario['live_only']}`")
    if scenario.get("ledger_rows"):
        parts.append("ledger " + ", ".join(f"`{row}`" for row in scenario["ledger_rows"]))
    return "; ".join(parts)


def render_scenarios(registry: dict) -> str:
    """The acceptance matrix: one row per scenario, in registry order."""
    lines = ["| Id | Modes | Setup | Action | Expected (C / P / X) | Proof |", "|---|---|---|---|---|---|"]
    for scenario in registry["scenarios"]:
        lines.append(f"| `{scenario['id']}` | {_cell(', '.join(scenario['control_modes']))} | {_cell(scenario['setup'])} | "
                     f"{_cell(scenario['action'])} | {_cell(scenario['expected'])} | {_cell(_proof(scenario))} |")
    return "\n".join(lines) + "\n"


def render_turn_end(registry: dict) -> str:
    """The turn-end table (ADR-0068): the vocabulary, the outcome by run state and control mode, each host's surface and
    the scenarios that prove each row."""
    table = registry["turn_end"]
    modes = list(registry["control_modes"])
    lines = ["| Outcome | Meaning |", "|---|---|"]
    lines += [f"| `{name}` | {_cell(meaning)} |" for name, meaning in table["outcomes"].items()]
    lines += ["", "| Run state | " + " | ".join(f"`{mode}`" for mode in modes) + " |", "|---|" + "---|" * len(modes)]
    for state in table["states"]:
        cells = []
        for mode in modes:
            cell = state["outcomes"][mode]
            notices = ", ".join(f"`{key}`" for key in cell.get("notices", []))
            cells.append(f"`{cell['outcome']}`" + (f" ({notices})" if notices else ""))
        lines.append(f"| {_cell(state['title'])} (`{state['id']}`) | " + " | ".join(cells) + " |")
    surfaces = ("event", "allow", "settle", "pause", "continue", "loop_bound")
    lines += ["", "| Host | " + " | ".join(name.replace("_", " ") for name in surfaces) + " | table-driven test |",
              "|---|" + "---|" * (len(surfaces) + 1)]
    for host, surface in table["hosts"].items():
        lines.append(f"| {host} | " + " | ".join(_cell(surface[name]) for name in surfaces)
                     + f" | `{surface['table_driven']}` |")
    hosts = list(table["hosts"])
    lines += ["", "| Run state | " + " | ".join(hosts) + " |", "|---|" + "---|" * len(hosts)]
    for state in table["states"]:
        lines.append(f"| `{state['id']}` | " + " | ".join(
            ", ".join(f"`{sid}`" for sid in state["scenarios"][host]) for host in hosts) + " |")
    return "\n".join(lines) + "\n"


def render_expectation(expectation: dict) -> str:
    """The registry-owned bullets of one expectation block."""
    lines = []
    for key in ("outcomes", "outcomes_by_mode"):
        value = expectation.get(key)
        if value and "\n" in value:
            lines.append(f"- **{key}.**")
            lines += [f"  {row}" for row in value.split("\n")]
        elif value:
            lines.append(f"- **{key}.** {value}")
    lines.append("- **conformance_scenarios.** " + (", ".join(f"`{sid}`" for sid in expectation["scenarios"]) or "none"))
    if expectation.get("sources"):
        lines.append(f"- **sources.** {expectation['sources']}")
    return "\n".join(lines) + "\n"


def regions(registry: dict) -> dict[str, str]:
    """``{marker key: generated body}`` for every region the document must carry."""
    found = {"scenarios": render_scenarios(registry), "turn_end": render_turn_end(registry)}
    for expectation in registry["expectations"]:
        found[expectation["id"]] = render_expectation(expectation)
    return found


def regenerate(registry: dict, text: str) -> tuple[str, list[str]]:
    """``(text with every region rewritten, problems)``; a problem is a missing region or an unknown one."""
    wanted = regions(registry)
    seen: set[str] = set()
    problems: list[str] = []

    def replace(match: re.Match) -> str:
        key = match["key"]
        seen.add(key)
        if key not in wanted:
            problems.append(f"the document has a generated region {key!r} that the registry does not")
            return match[0]
        return f"<!-- BEGIN GENERATED: {key} (make registry-doc) -->\n{wanted[key]}<!-- END GENERATED -->"

    updated = _MARKER.sub(replace, text)
    problems += [f"the document has no generated region {key!r}" for key in sorted(set(wanted) - seen)]
    return updated, problems


def drift(registry: dict, text: str) -> list[str]:
    """Why ``text`` is not what ``make registry-doc`` would write, or ``[]``."""
    updated, problems = regenerate(registry, text)
    if updated != text:
        problems.append("a generated region is stale; run `make registry-doc`")
    return problems


def main(argv: list[str]) -> int:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    text = DOCUMENT.read_text(encoding="utf-8")
    if "--check" in argv:
        problems = drift(registry, text)
        for problem in problems:
            print(f"registry-doc: {problem}", file=sys.stderr)
        return 1 if problems else 0
    updated, problems = regenerate(registry, text)
    if problems:
        for problem in problems:
            print(f"registry-doc: {problem}", file=sys.stderr)
        return 1
    DOCUMENT.write_text(updated, encoding="utf-8")
    print("host-boundary design document regenerated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
