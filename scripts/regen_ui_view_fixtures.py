#!/usr/bin/env python3
"""Regenerate (or ``--check``) the observer contract fixtures from the plugin's own runs.

    python3 scripts/regen_ui_view_fixtures.py [--check]

The store is ``plugins/empirica/tests/v2/observer_store.build_store``: real runs driven through
the v2 service. The observer projects it; nothing in the fixtures is written by hand. The demo copy
vendored into the plugin (``plugins/empirica/vendor/contracts/empirica-ui-view/v1/fixtures``) is
written alongside; ``--check`` compares both copies with a fresh render.
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
from observer_store import DriverSource, build_store  # noqa: E402

FIXTURES = Path("contracts/empirica-ui-view/v1/fixtures")
TARGETS = (ROOT / FIXTURES, PLUGIN / "vendor" / FIXTURES)


def render() -> dict[str, str]:
    store = build_store()
    view, details = Observer(DriverSource(store)).observe()
    files = {"view.json": view, "runs.json": details, "scenarios.json": dict(sorted(store.expect.items()))}
    return {name: json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
            for name, value in files.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when a committed file differs")
    args = parser.parse_args()
    files = render()
    stale = []
    for target in TARGETS:
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
    print(f"ok: {'checked' if args.check else 'wrote'} {len(files)} observer fixtures x {len(TARGETS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
