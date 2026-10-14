"""One timed smoke run at the realistic size (T1 A; UI2-spec §4 as amended 2026-10-13: 1,000 runs), ``make observer-smoke``,
in ``make verify`` only.

What a pass costs is calculated, not timed: ``test_observer_complexity.py`` (in ``make check``) counts every operation at
n and 2n. This run only checks that, at 1,000 runs, the constants are small enough: the budgets of contract
``empirica-ui-view/v1`` on the plugin's own server, over the real filesystem store (``fs_store.build_fs_store``, read by
``StoreSource`` and indexed in the background by ``adapters.observer.indexer.Indexer``, as ``empirica-observer serve``
serves it). The limits are the spec's own, measured as medians:

    During the cold start: every answer at once, with coverage that only grows and always sums to the store
    A summary and a 304                      <= 100 ms
    An indexer pass with nothing changed     <= 100 ms
    An indexer pass with one run changed     <= 300 ms, and the change is published
    Search                                   <= 300 ms
    /api/summary on the wire (gzip)          <= 512 KB
    A runs page                              <= 200 rows, and the cursor walks every row once
"""
from __future__ import annotations

import gzip
import json
import statistics
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parents[1])]

from adapters.observer.indexer import Indexer  # noqa: E402
from adapters.observer.server import Sources, serve  # noqa: E402
from adapters.observer.source import StoreSource  # noqa: E402
from application.observer import Observer  # noqa: E402
from fs_store import build_fs_store, replace_run  # noqa: E402

RUNS, REPOS = 1_000, 20
WARM_MS, CHANGED_MS, SEARCH_MS = 100, 300, 300
SUMMARY_BYTES = 512 * 1024
PAGE_ROWS = 200


def _ms(action, times: int = 5) -> float:
    taken = []
    for _ in range(times):
        start = time.perf_counter()
        action()
        taken.append((time.perf_counter() - start) * 1000)
    return statistics.median(taken)


class SmokeAt1kTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = build_fs_store(Path(cls.tmp.name), RUNS, workspaces=REPOS)
        cls.observer = Observer(StoreSource(cls.store.home), clock=time.monotonic)
        sources = Sources(cls.observer, identity="smoke")
        cls.indexer = Indexer(cls.observer, idle=0.05, after=sources.prepare)
        cls.indexer.start()
        cls.server = serve(sources)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.cold = []  # (coverage, milliseconds) of each summary asked for while the cold start ran
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            start = time.perf_counter()
            status, body, _ = cls._get("/api/summary", **{"Accept-Encoding": "gzip"})
            taken = (time.perf_counter() - start) * 1000
            assert status == 200, status
            coverage = json.loads(gzip.decompress(body))["coverage"]
            cls.cold.append((coverage, taken))
            if coverage["pending"] == 0:
                break
            time.sleep(0.2)
        cls.indexer.stop()  # the passes below are timed alone; requests still answer from the published view

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    @classmethod
    def _get(cls, path: str, **headers: str) -> tuple[int, bytes, dict]:
        request = urllib.request.Request(cls.base + path, headers={"Host": cls.base.removeprefix("http://"), **headers})
        try:
            with urllib.request.urlopen(request) as answer:
                return answer.status, answer.read(), dict(answer.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)

    def test_a_cold_start_answers_at_once_reads_every_run_whole_and_its_coverage_only_grows(self):
        self.assertEqual(self.cold[-1][0], {"runs": RUNS, "read": RUNS, "unreadable": 0, "partial": 0, "pending": 0, "reindexing": 0})
        self.assertGreater(len(self.cold), 1, "the store was answered for before it was read")
        for coverage, _ in self.cold:
            self.assertEqual(coverage["read"] + coverage["unreadable"] + coverage["partial"] + coverage["pending"], RUNS)
        pending = [c["pending"] for c, _ in self.cold]
        self.assertEqual(pending, sorted(pending, reverse=True))
        taken = [ms for _, ms in self.cold]
        print(f"\n  {RUNS} runs: cold start answered {len(taken)} summaries, median {statistics.median(taken):.0f} ms", flush=True)
        self.assertLessEqual(statistics.median(taken), WARM_MS, "a summary while the cold start runs")

    def test_a_summary_a_304_and_a_pass_with_nothing_changed_are_within_budget(self):
        status, _, headers = self._get("/api/summary")
        self.assertEqual(status, 200)
        etag = headers["ETag"]
        self.assertLessEqual(_ms(lambda: self._get("/api/summary", **{"Accept-Encoding": "gzip"})), WARM_MS, "a summary")
        self.assertEqual(self._get("/api/summary", **{"If-None-Match": etag})[0], 304)
        self.assertLessEqual(_ms(lambda: self._get("/api/summary", **{"If-None-Match": etag})), WARM_MS, "a 304")
        self.assertLessEqual(_ms(self.observer.index), WARM_MS, "an indexer pass with nothing changed")

    def test_an_indexer_pass_with_one_run_changed_is_within_budget_and_publishes_it(self):
        changes = iter(range(1, 100))

        def change():
            n = next(changes)
            replace_run(self.store, self.store.keys[n * 7], self.store.keys[n * 7 + REPOS])  # another template
            start = time.perf_counter()
            self.observer.index()
            return (time.perf_counter() - start) * 1000
        before = self.observer.summary()["token"]
        self.assertLessEqual(statistics.median(change() for _ in range(3)), CHANGED_MS)
        self.assertNotEqual(self.observer.summary()["token"], before, "the change is published")

    def test_search_the_summary_s_size_and_a_runs_page_are_within_budget(self):
        self.assertLessEqual(_ms(lambda: self._get("/api/search?q=core%20path")), SEARCH_MS)
        self.assertGreater(json.loads(self._get("/api/search?q=core%20path")[1])["total"], 0)
        status, body, headers = self._get("/api/summary", **{"Accept-Encoding": "gzip"})
        self.assertEqual((status, headers.get("Content-Encoding")), (200, "gzip"))
        self.assertLessEqual(len(body), SUMMARY_BYTES)
        seen, cursor = [], ""
        for _ in range(RUNS // PAGE_ROWS + 2):  # bounded by the pages the store allows: a stuck cursor fails, never spins
            page = json.loads(self._get(f"/api/runs?limit={PAGE_ROWS}&cursor={cursor}")[1])
            self.assertLessEqual(len(page["rows"]), PAGE_ROWS)
            seen += [r["key"] for r in page["rows"]]
            if page["next"] is None:
                break
            cursor = urllib.request.quote(page["next"])
        else:
            self.fail("the page walk did not end within its bound")
        self.assertEqual((len(seen), len(set(seen))), (RUNS, RUNS))
        self.assertEqual(self._get(f"/api/runs?limit={PAGE_ROWS + 1}")[0], 400)


def load_tests(loader, tests, pattern):
    """Only when named (``make observer-smoke``): unittest discovery (``make empirica-v2-check``) leaves it out."""
    return tests if pattern is None else unittest.TestSuite()


if __name__ == "__main__":
    unittest.main()
