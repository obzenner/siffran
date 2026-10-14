#!/usr/bin/env python3
"""Serve the fixture runs the real service builds (``observer_store.FixtureSource``: the ``FIXTURE_SCENARIOS`` of
``build_store``) as the observer's live ``store`` source on 127.0.0.1, for the UI's browser journeys
(ui/tests/browser.journeys.mjs): the same runs as the vendored demo fixtures, but projected on request by the running
observer instead of read from frozen JSON. Prints the address and serves until
it is stopped.

    python3 plugins/empirica/tests/v2/serve_generated.py [--runs N] [--demo [--open]]

``--runs N`` serves N runs instead (``observer_store.ScaleSource``: each a copy of one of the store's runs under a key of
its own), indexed progressively as the UI polls, for trying the observer at volume (contract v1).

``--demo`` serves what ``make ui-demo`` shows (``observer_store.DemoSource``): every standing and ask kind, dropped claims,
audits with findings, several attempts and several projects, with times spread over about 60 days from now, at N runs
(300 unless ``--runs`` says). It needs no Empirica store. ``--open`` opens the page in the browser.
"""
from __future__ import annotations

import argparse
import sys
import time
import webbrowser
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _path in (_HERE, _HERE.parent, _HERE.parents[1], _HERE.parents[2] / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.observer import civil  # noqa: E402
from adapters.observer.server import Sources, serve  # noqa: E402
from application.observer import Observer  # noqa: E402
from adapters.observer.indexer import Indexer  # noqa: E402
from observer_store import DemoSource, FixtureSource, ScaleSource, build_demo_store, build_store  # noqa: E402

#: Up to this many runs a demo is read whole before the page opens; beyond it the index carries on while the UI polls.
WHOLE = 2000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, help="serve this many generated runs (the store's runs, copied)")
    parser.add_argument("--demo", action="store_true", help="the demo store (times over 60 days, attempts, proven runs)")
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    args = parser.parse_args()
    if args.demo:
        runs = args.runs or 300
        source = DemoSource(build_demo_store(min(120, max(24, runs // 8))), runs, now=time.time())
    else:
        store = build_store()
        source = FixtureSource(store) if args.runs is None else ScaleSource(store, args.runs)
    observer = Observer(source, clock=time.monotonic, zones=civil.zone)
    if args.runs is None and not args.demo or args.demo and (args.runs or 300) <= WHOLE:
        observer.index()  # the small store is read whole first, so every page the browser checks shows all of it
    sources = Sources(observer, identity="generated", store_kind="generated")
    Indexer(observer, after=sources.prepare).start()  # then kept current in the background, as ``serve`` does
    server = serve(sources)
    address = f"http://127.0.0.1:{server.server_address[1]}"
    print(address + ("/app/" if args.demo else ""), flush=True)
    if args.open:
        webbrowser.open(f"{address}/app/")
    server.serve_forever()


if __name__ == "__main__":
    main()
