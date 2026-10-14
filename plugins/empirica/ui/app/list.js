// The work list's data and window, with no DOM (tests/list.test.mjs).
//
// `Walk` reads a filtered (or searched) list a page at a time. The server pages in one published view: a cursor from an
// older view is refused (409, `stale`). That is not an error to show: the walk starts again from the first page, reading as
// far as it had read, and swaps the fresh rows in whole, so no row is missed or doubled and the person's place is kept
// (the caller anchors on a row's key). `windowFor` is the slice of rows to draw: never more than `max`, whatever the list.

/** @param {{request:(cursor:?string)=>Promise<object>, page:number}} o
 * `request` is `client.runs(...)` or `client.search(...)` for this list; its outcomes are the client's values. */
export class Walk {
  rows = []; total = null; unsearched = 0; next = null; token = null; failure = null; loading = false; restarts = 0; #over = false; #o; #gen = 0;
  constructor(o) { this.#o = o; }
  /** Stop: answers still in flight are dropped. */
  dispose() { this.#over = true; }

  /** Read from the first page until `want` rows are held (or the list ends), then swap them in. Used to begin, and to read
   * again (a changed store, a stale cursor) as far as the person had got. */
  async start(want = 1) {
    const gen = ++this.#gen;
    this.loading = true;
    // The replacement (its rows and its cursor, token and totals) stays here until the whole walk has succeeded: a walk
    // that fails part way keeps the old rows with the old cursor, so the next `more` restarts it, never mixing two views.
    let cursor = null, fresh = [], tries = 0, last = null;
    for (;;) {
      const r = await this.#o.request(cursor);
      if (this.#over || gen !== this.#gen) return;
      if (r.status === "stale") {
        if (++tries > 3) { this.#end("error"); return; }
        this.restarts++; cursor = null; fresh = [];
        continue;
      }
      if (r.status !== "ok") { this.#end(r.status); return; }
      fresh.push(...r.page.rows);
      last = r.page;
      if (last.next === null || fresh.length >= want) break;
      cursor = last.next;
    }
    this.rows = fresh; this.total = last.total; this.token = last.token; this.next = last.next; this.unsearched = last.unsearched ?? 0;
    this.failure = null; this.loading = false;
  }

  /** The next page, when there is one and none is being read. A stale cursor starts the walk again, as far as it had got plus a page. */
  async more() {
    if (this.next === null || this.loading || this.#over) return;
    const gen = this.#gen, held = this.rows.length;
    this.loading = true;
    const r = await this.#o.request(this.next);
    if (this.#over || gen !== this.#gen) return;
    if (r.status === "stale") { this.loading = false; this.restarts++; await this.start(held + this.#o.page); return; }
    if (r.status !== "ok") { this.#end(r.status); return; }
    this.rows = [...this.rows, ...r.page.rows]; this.total = r.page.total; this.token = r.page.token; this.next = r.page.next;
    this.failure = null; this.loading = false;
  }

  #end(status) { this.failure = status; this.loading = false; }
}

/** The slice [start, end) of `count` rows to draw for a scroll position: what is on screen plus `overscan` beyond it each way,
 * never more than `max` rows (the node budget). */
export function windowFor({ top, view, rowHeight, count, overscan, max }) {
  const first = Math.min(Math.max(0, Math.floor(top / rowHeight) - overscan), Math.max(0, count - 1));
  const span = Math.min(max, Math.ceil(view / rowHeight) + 2 * overscan);
  return { start: first, end: Math.min(count, first + span) };
}

/** The row first on screen at scroll `top` and how far into it the screen starts (`offset`), so the place can be kept by
 * the row's key when rows change; null for no rows. */
export function anchorAt(top, rowHeight, count) {
  if (!count) return null;
  const index = Math.min(count - 1, Math.floor(top / rowHeight));
  return { index, offset: top - index * rowHeight };
}

/** The scroll position that puts the anchored row (now at `index`) where it was on screen. */
export const anchoredTop = (index, offset, rowHeight) => index * rowHeight + offset;

/** Whether the screen reaches within `overscan` rows of the last of `count` held (the next page should be read). */
export const nearEnd = (top, view, rowHeight, count, overscan) => (top + view) / rowHeight >= count - overscan;

/** The least scroll from `top` that shows row `index` whole in a box `view` tall: unchanged when it is on screen. */
export function revealTop(index, top, view, rowHeight) {
  const at = index * rowHeight;
  if (at < top) return at;
  if (at + rowHeight > top + view) return at + rowHeight - view;
  return top;
}
