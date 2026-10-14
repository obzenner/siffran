import catalogue from "../../catalogue.json" with { type: "json" };
import { anchorAt, anchoredTop, nearEnd, revealTop, windowFor } from "../../app/list.js";
import { EvElement, count, define, esc, morph, num, setStyle, t } from "../core/element.js";
import { windowHtml } from "../views/rows.js";

/**
 * The work list: one line per run, drawn a window at a time (never more than `max_rows` row nodes, whatever the list holds),
 * with how many the list has, which filters made it, and a line saying what it is waiting for or why it stopped. The scroll
 * box is drawn once; a new answer redraws the toolbar, the foot and the window only, and the window keeps the row the person
 * was looking at where it was.
 * @element ev-work
 * @prop {{walk:{rows:object[],total:?number,next:?string,loading:boolean,failure:?string,unsearched:number}, chips:{label:string,href:string}[], ctx:object, empty:string}} data
 */
class EvWork extends EvElement {
  #rowHeight = 0; #raf = 0; #anchor = null; #drawn = null;
  constructor() {
    super();
    this.addEventListener("scroll", () => {
      if (this.#raf) return;
      this.#raf = requestAnimationFrame(() => { this.#raf = 0; this.#paint(); this.emit("ev-near-end", this.#nearEnd()); });
    }, true);
  }
  update() {
    const d = this.data;
    if (!d) { this.innerHTML = ""; this.#drawn = null; return; }
    if (!this.querySelector("[data-scroll]")) {
      this.innerHTML = `<div class="flex h-full min-h-0 flex-col"><div class="flex flex-wrap items-center gap-xs border-b border-border px-s py-xs" data-toolbar></div>
        <div class="min-h-0 flex-1 overflow-y-auto" data-scroll tabindex="-1"><div data-window></div><p class="px-s py-xs text-xs text-muted-foreground" aria-live="polite" data-foot></p></div></div>`;
      this.#drawn = null;
    }
    const { walk } = d;
    const chips = d.chips.map((c) => `<a class="badge" data-variant="secondary" href="${esc(c.href)}" data-chip>${esc(c.label)}<span class="sr-only"> ${t("work.remove")}</span> ✕</a>`).join("");
    morph(this.querySelector("[data-toolbar]"), `<h1 class="text-s font-semibold">${walk.total === null ? t("work.title") : count("work.count", walk.total, { n: num(walk.total) })}</h1>${chips}
      ${walk.failure ? `<button type="button" class="btn" data-variant="outline" data-size="sm" data-retry-list>${t("detail.retry")}</button>` : ""}`);
    const state = walk.failure ? t(`app.status.${walk.failure}`) : walk.loading ? t("work.loading") : walk.total === 0 ? t(d.empty) : walk.next ? t("work.more", { shown: num(walk.rows.length), total: num(walk.total) }) : "";
    morph(this.querySelector("[data-foot]"), `${state}${walk.unsearched ? `<span class="block">${count("coverage.unsearched", walk.unsearched, { n: num(walk.unsearched) })}</span>` : ""}`);
    this.#keepPlace();
    this.#paint(true);
  }
  /** After new rows, scroll so the row that was first on screen is still there (by its key, not by pixels). */
  #keepPlace() {
    const box = this.querySelector("[data-scroll]"), a = this.#anchor;
    if (!box || !a || !this.#rowHeight) return;
    const at = this.data.walk.rows.findIndex((r) => r.key === a.key);
    if (at >= 0) box.scrollTop = anchoredTop(at, a.offset, this.#rowHeight);
  }
  /** Draw the window the scroll box is at (when it, or the data, changed). */
  #paint(force = false) {
    const d = this.data, box = this.querySelector("[data-scroll]"), host = this.querySelector("[data-window]");
    if (!d || !box || !host) return;
    const rows = d.walk.rows;
    if (!this.#rowHeight && rows.length) {  // measure a row once: draw a few, read one
      morph(host, windowHtml(rows.slice(0, 3), d.ctx));
      this.#rowHeight = host.querySelector("[data-row]")?.offsetHeight ?? 0;
    }
    if (!this.#rowHeight) { morph(host, ""); return; }
    const { start, end } = windowFor({ top: box.scrollTop, view: box.clientHeight, rowHeight: this.#rowHeight, count: rows.length, overscan: catalogue.overscan, max: catalogue.max_rows });
    const at = anchorAt(box.scrollTop, this.#rowHeight, rows.length);
    this.#anchor = at ? { key: rows[at.index].key, offset: at.offset } : null;
    const same = this.#drawn && this.#drawn.start === start && this.#drawn.end === end && this.#drawn.rows === rows && this.#drawn.ctx === d.ctx;
    if (same && !force) return;
    const a = document.activeElement, kept = a && host.contains(a) ? a.dataset.run : null;
    morph(host, windowHtml(rows.slice(start, end), d.ctx));
    setStyle(host.querySelector("[data-before]"), { height: `${start * this.#rowHeight}px` });
    setStyle(host.querySelector("[data-after]"), { height: `${(rows.length - end) * this.#rowHeight}px` });
    this.#drawn = { start, end, rows, ctx: d.ctx };
    if (kept && !host.contains(document.activeElement)) host.querySelector(`a[data-run="${kept}"]`)?.focus({ preventScroll: true });
  }
  /** Whether the window is near the last row held, so the next page should be read. */
  #nearEnd() {
    const box = this.querySelector("[data-scroll]"), rows = this.data?.walk.rows;
    if (!box || !rows || !this.#rowHeight) return false;
    return nearEnd(box.scrollTop, box.clientHeight, this.#rowHeight, rows.length, catalogue.overscan);
  }
  /** Scroll to row `index` if it is not on screen, draw it, and return its link (so the caller can focus it). */
  reveal(index) {
    const box = this.querySelector("[data-scroll]");
    if (!box || !this.#rowHeight) return null;
    const top = revealTop(index, box.scrollTop, box.clientHeight, this.#rowHeight);
    if (top !== box.scrollTop) box.scrollTop = top;
    this.#paint();
    return this.querySelector(`[data-row] a[data-run="${this.data.walk.rows[index]?.id}"]`);
  }
}
define("ev-work", EvWork);
