"""The observer's background indexer: one daemon thread that keeps the published view current, so a request never pays
for indexing (``Observer.serve_from_index``).

It indexes in slices of ``slice`` seconds (the observer's own budget, read on the clock the observer was given), so a
cold start publishes what it has read after every slice and coverage counts the rest as ``pending``. When nothing is
pending it waits ``idle`` seconds before the next poll. The clock is this adapter's; the application reads none.
"""
from __future__ import annotations

import sys
import threading
import traceback

from application.observer import Observer


class Indexer:
    def __init__(self, observer: Observer, *, slice: float = 0.1, idle: float = 1.0, after=None) -> None:
        """``after`` is called after every slice (the server encodes the newest summary there, so no request does)."""
        self._observer = observer
        self._after = after
        self._slice = slice
        self._idle = idle
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="empirica-observer-indexer", daemon=True)

    def start(self) -> None:
        self._observer.serve_from_index()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def _loop(self) -> None:
        reported = set()
        while not self._stop.is_set():
            try:
                pending = self._observer.index(self._slice)
                if self._after is not None:
                    self._after()
            except Exception as exc:  # noqa: BLE001 - one failed poll is reported, and the next one tried
                line = f"observer indexer: {type(exc).__name__}: {exc}"
                if line not in reported:
                    reported.add(line)
                    traceback.print_exception(exc, file=sys.stderr)
                pending = 0
            # Between slices the thread yields, so requests read the newest view at once.
            self._stop.wait(self._idle if pending == 0 else 0.001)
