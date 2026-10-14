// app/list.js: a list is read a page at a time and restarts on a stale cursor without losing or doubling a row; the window is bounded.
import assert from "node:assert/strict";
import { test } from "vitest";
import fc from "fast-check";
import { Walk, anchorAt, anchoredTop, nearEnd, revealTop, windowFor } from "../app/list.js";

const mk = (from, to) => Array.from({ length: to - from }, (_, i) => ({ key: `k${from + i}`, id: String(from + i) }));
/** A server of `total` rows in pages of `size`; `stale` is how many first cursor requests it refuses. */
function source(total, size, { stale = 0, fail = null } = {}) {
  const log = [];
  return { log, request: async (cursor) => {
    log.push(cursor);
    if (fail) return { status: fail };
    if (cursor && stale > 0) { stale--; return { status: "stale", token: "t2" }; }
    const at = cursor ? Number(cursor) : 0, end = Math.min(total, at + size);
    return { status: "ok", page: { rows: mk(at, end), total, token: "t", next: end < total ? String(end) : null, unsearched: 3 } };
  } };
}
test("a walk reads from the first page until it holds what was wanted, then page by page", async () => {
  const s = source(25, 10), w = new Walk({ request: s.request, page: 10 });
  await w.start(1); assert.equal(w.rows.length, 10);
  await w.more(); await w.more(); await w.more();
  assert.deepEqual([w.rows.length, w.total, w.next, w.unsearched], [25, 25, null, 3]);
  assert.deepEqual(w.rows.map((r) => r.key), mk(0, 25).map((r) => r.key));
});
test("a stale cursor restarts the walk and reads as far as it had read: no row missed, none doubled", async () => {
  const s = source(40, 10, { stale: 1 }), w = new Walk({ request: s.request, page: 10 });
  await w.start(1); await w.more();
  assert.equal(w.restarts, 1); assert.equal(w.rows.length, 20);
  assert.equal(new Set(w.rows.map((r) => r.key)).size, 20);
  const again = source(40, 10), v = new Walk({ request: again.request, page: 10 });
  await v.start(25); assert.equal(v.rows.length, 30);
});
test("a failure is said and keeps what was read; asking again recovers", async () => {
  const w = new Walk({ request: source(5, 10, { fail: "offline" }).request, page: 10 });
  await w.start(1); assert.deepEqual([w.failure, w.rows.length, w.loading], ["offline", 0, false]);
  const ok = source(30, 10), v = new Walk({ request: async (c) => (c === "10" && v.fail ? { status: "error" } : ok.request(c)), page: 10 });
  await v.start(1); v.fail = true; await v.more();
  assert.deepEqual([v.failure, v.rows.length], ["error", 10]);
  v.fail = false; await v.more(); assert.deepEqual([v.failure, v.rows.length], [null, 20]);
});
test("a disposed walk drops late answers", async () => {
  const w = new Walk({ request: source(5, 10).request, page: 10 });
  const p = w.start(1); w.dispose(); await p; assert.deepEqual(w.rows, []);
});
test("the window never holds more than max rows, whatever the list and the position", () => {
  for (const count of [0, 1, 50, 10000]) for (const top of [0, 1234, 1e9]) {
    const { start, end } = windowFor({ top, view: 900, rowHeight: 32, count, overscan: 20, max: 300 });
    assert.ok(end - start <= 300 && start >= 0 && end <= count, JSON.stringify({ count, top, start, end }));
  }
  const { start, end } = windowFor({ top: 3200, view: 320, rowHeight: 32, count: 1000, overscan: 5, max: 300 });
  assert.ok(start <= 100 && end >= 110);
});

const scroll = fc.record({ rowHeight: fc.integer({ min: 8, max: 64 }), count: fc.integer({ min: 1, max: 5000 }), view: fc.integer({ min: 40, max: 2000 }) })
  .chain((o) => fc.record({ ...Object.fromEntries(Object.entries(o).map(([k, v]) => [k, fc.constant(v)])), top: fc.integer({ min: 0, max: o.rowHeight * o.count }), index: fc.nat(o.count - 1) }));
const SCROLL = { seed: 20261016, numRuns: 200 };

test("the anchor is the row first on screen and how far into it the screen starts; restoring it gives the same scroll", () => {
  fc.assert(fc.property(scroll, ({ top, rowHeight, count }) => {
    const a = anchorAt(top, rowHeight, count);
    assert.ok(Number.isInteger(a.index) && a.index >= 0 && a.index < count);
    if (top < count * rowHeight) {
      assert.ok(a.offset >= 0 && a.offset < rowHeight, "inside the row");
      assert.equal(a.index, Math.floor(top / rowHeight));
    } else assert.equal(a.index, count - 1, "past the end: the last row");
    assert.equal(anchoredTop(a.index, a.offset, rowHeight), top);
  }), SCROLL);
  assert.equal(anchorAt(10, 32, 0), null, "no rows, no anchor");
  // rows moved: the anchored row now at index 7, the same offset into it
  assert.equal(anchoredTop(7, 5, 32), 7 * 32 + 5);
});

test("near the end exactly when the screen reaches within overscan rows of the last row held", () => {
  fc.assert(fc.property(scroll, fc.integer({ min: 0, max: 40 }), ({ top, view, rowHeight, count }, overscan) => {
    const bottom = (top + view) / rowHeight;
    assert.equal(nearEnd(top, view, rowHeight, count, overscan), bottom >= count - overscan);
  }), SCROLL);
  assert.equal(nearEnd(0, 320, 32, 15, 5), true, "10 rows on screen, 15 held, overscan 5: at the edge");
  assert.equal(nearEnd(0, 320, 32, 16, 5), false);
});

test("revealing a row scrolls the least that shows it whole, and not at all when it is on screen", () => {
  fc.assert(fc.property(scroll, ({ top, view, rowHeight, index }) => {
    const to = revealTop(index, top, view, rowHeight), at = index * rowHeight;
    if (view >= rowHeight) assert.ok(at >= to && at + rowHeight <= to + view, "the row is whole on screen");
    const shown = at >= top && at + rowHeight <= top + view;
    if (shown) assert.equal(to, top, "already shown: no scroll");
    else assert.equal(to, at < top ? at : at + rowHeight - view, "the nearer edge");
  }), SCROLL);
});
test("a restart that fails after its first page keeps the old rows and the old cursor, so the walk never mixes two views (AST-1)", async () => {
  const ok = (ids, next, token = "old") => ({ status: "ok", page: { rows: ids.map((id) => ({ id, key: id })), total: 6, next, token } });
  const replies = [ok(["a", "b"], "old2"), ok(["c", "d"], "old4"), { status: "stale", token: "new" }, ok(["x", "a"], "new2", "new"),
    { status: "offline" }, { status: "stale", token: "new" }, ok(["x", "a"], "new2", "new"), ok(["b", "c"], "new4", "new"), ok(["d", "e"], null, "new")];
  const w = new Walk({ page: 2, request: async () => replies.shift() });
  await w.start(4);
  await w.more();  // stale: the restart reads a first page of the new view, then fails
  assert.deepEqual([w.rows.map((r) => r.id), w.next, w.token, w.total, w.failure], [["a", "b", "c", "d"], "old4", "old", 6, "offline"]);
  await w.more();  // the old cursor: the view is restarted again, whole this time
  assert.deepEqual([w.rows.map((r) => r.id), w.next, w.token], [["x", "a", "b", "c", "d", "e"], null, "new"]);
});
