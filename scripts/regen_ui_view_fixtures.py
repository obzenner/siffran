#!/usr/bin/env python3
"""Regenerate (or ``--check``) the observer contract fixtures from the plugin's own runs.

    python3 scripts/regen_ui_view_fixtures.py [--check]

The runs are ``plugins/empirica/tests/v2/observer_store.build_store``'s, driven through the real service, and of
them only the minimal named selection ``FIXTURE_SCENARIOS`` (one per standing and per ask kind the service reaches;
``FixtureSource``). The observer projects them; nothing in the fixtures is written by hand.
The contract's fixtures (summary, rows, sheets and the scenario table) are written to
``contracts/empirica-ui-view/v1/fixtures`` and byte for byte to the plugin's built-in demo
(``plugins/empirica/vendor/contracts/empirica-ui-view/v1/fixtures``). ``--check`` fails on any byte difference between
the two copies, then on any difference from a fresh render. This is the fixtures' one generator.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/empirica"
for path in (PLUGIN / "tests/v2", PLUGIN / "tests", PLUGIN):
    sys.path.insert(0, str(path))

from application.observer import Observer  # noqa: E402
from observer_store import FixtureSource, build_store, fixture_scenarios  # noqa: E402

FIXTURES = Path("contracts/empirica-ui-view/v1/fixtures")
TARGETS = (ROOT / FIXTURES, PLUGIN / "vendor" / FIXTURES)


def _text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def render() -> dict[Path, dict[str, str]]:
    """Each fixture folder's files: the summary, rows and sheets (one observer over the fixture runs) and the scenario
    table."""
    store = build_store()
    observer = Observer(FixtureSource(store))
    files = {"summary.json": observer.summary(), "rows.json": observer.rows(), "runs.json": observer.details(),
             "scenarios.json": fixture_scenarios(store)}
    return {target: {name: _text(value) for name, value in files.items()} for target in TARGETS}


def copy_problems(contract: Path = ROOT / FIXTURES, vendored: Path = PLUGIN / "vendor" / FIXTURES) -> list[str]:
    """The vendored fixtures are a byte copy of the contract's: the same files, the same bytes."""
    names = sorted({p.name for p in contract.iterdir()} | {p.name for p in vendored.iterdir()})
    return [f"{vendored / name} is not a byte copy of {contract / name}" for name in names
            if not (contract / name).is_file() or not (vendored / name).is_file()
            or (contract / name).read_bytes() != (vendored / name).read_bytes()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when a committed file differs")
    args = parser.parse_args()
    if args.check and (copies := copy_problems()):
        print("the vendored observer fixtures differ from the contract's (run scripts/regen_ui_view_fixtures.py):",
              *copies, sep="\n  ")
        return 1
    rendered = render()
    stale = []
    for target, files in rendered.items():
        for name, text in files.items():
            path = target / name
            if args.check:
                if not path.is_file() or path.read_text(encoding="utf-8") != text:
                    stale.append(str(path.relative_to(ROOT)))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
    if stale:
        print("stale observer fixtures (run scripts/regen_ui_view_fixtures.py):", *stale, sep="\n  ")
        return 1
    print(f"ok: {'checked' if args.check else 'wrote'} {sum(map(len, rendered.values()))} observer fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
