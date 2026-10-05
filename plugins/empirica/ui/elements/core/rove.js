// A roving tab stop (one stop in the page's tab order, arrow keys inside), for the view's marks.
// Left/Up: previous mark, Right/Down: next mark, in reading (DOM) order; Home/End: first/last mark.
const MOVES = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 };
const stops = new WeakMap();  // host -> index of the mark holding the stop

/** Which mark (index in DOM order) renders with tabindex 0: the remembered one if still in range, else `fallback`. */
export function stopIndex(host, count, fallback = 0) {
  const i = stops.get(host);
  return i !== undefined && i < count ? i : Math.max(0, Math.min(fallback, count - 1));
}

/** Move the stop to `to` (a mark inside host). */
function giveStop(host, selector, to) {
  const all = [...host.querySelectorAll(selector)];
  for (const b of all) if (b !== to && b.tabIndex === 0) b.tabIndex = -1;
  to.tabIndex = 0;
  stops.set(host, all.indexOf(to));
}

/** Wire the roving behaviour once (call from the element's constructor). */
export function rove(host, selector) {
  host.addEventListener("focusin", (e) => { const m = e.target.closest(selector); if (m) giveStop(host, selector, m); });
  host.addEventListener("keydown", (e) => {
    const m = e.target.closest(selector);
    if (!m || !(e.key in MOVES || e.key === "Home" || e.key === "End")) return;
    e.preventDefault();
    const all = [...host.querySelectorAll(selector)];
    const to = e.key === "Home" ? all[0] : e.key === "End" ? all.at(-1) : all[all.indexOf(m) + MOVES[e.key]];
    if (to) { giveStop(host, selector, to); to.focus(); }
  });
}
