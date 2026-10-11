#!/usr/bin/env python3
"""Serve the store the real v2 service builds (``observer_store.build_store``) as the observer's live ``store`` source on
127.0.0.1, for the UI's browser journeys (ui/tests/browser.journeys.mjs): the same runs as the vendored demo fixtures,
but projected on request by the running observer instead of read from frozen JSON. Prints the address and serves until
it is stopped.

    python3 plugins/empirica/tests/v2/serve_generated.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _path in (_HERE, _HERE.parent, _HERE.parents[1], _HERE.parents[2] / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.observer.server import Sources, serve  # noqa: E402
from application.observer import Observer  # noqa: E402
from observer_store import DriverSource, build_store  # noqa: E402


def main() -> None:
    server = serve(Sources(Observer(DriverSource(build_store())), identity="generated"))
    print(f"http://127.0.0.1:{server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
