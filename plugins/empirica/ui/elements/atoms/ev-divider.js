import { define, t } from "../core/element.js";
import { splitClamp, splitKey, toPx } from "../../app/split.js";

/**
 * The divider between the main area and the run sheet (`#sheet-pane`, its next sibling), in the Universe and the Inbox
 * alike. It drags with the pointer (a cancel or a lost capture ends the drag), moves with the arrow, Page and Home/End
 * keys when focused, resets to the default on a double-click, and says the sheet's width to assistive tech (a
 * separator with aria-valuenow). The width is remembered (localStorage) and clamped again whenever the row resizes,
 * between the sheet's minimum and the main area's (tokens width.sheet, sheet-min, main-min, split-step, read as the
 * --ev-sheet* lengths of styles/app.css; app/split.js). Below both minimums it sets `data-narrow` on the body and the
 * sheet takes the full width. The Universe refits by itself: it watches its own size.
 * @element ev-divider
 */
const KEY = "empirica.sheet-width";
class EvDivider extends HTMLElement {
  #want = null;  // the width the reader chose; a resize clamps from it, so a narrow moment never shrinks it for good
  #resize = new ResizeObserver(() => this.#width(this.#want));
  connectedCallback() {
    this.setAttribute("role", "separator");
    this.setAttribute("aria-orientation", "vertical");
    this.setAttribute("aria-controls", "sheet-pane");
    this.setAttribute("aria-label", t("split.handle"));
    this.tabIndex = 0;
    this.innerHTML = `<span class="h-full w-px bg-border" data-line></span>`;
    this.addEventListener("pointerdown", this.#drag);
    this.addEventListener("dblclick", () => this.#choose(this.#len("--ev-sheet")));
    this.addEventListener("keydown", (e) => {
      const to = splitKey(e.key, this.#now(), { min: this.#len("--ev-sheet-min"), max: Number(this.getAttribute("aria-valuemax")), step: this.#len("--ev-split-step") });
      if (to !== undefined) { e.preventDefault(); this.#choose(to); }
    });
    this.#want = Number(localStorage.getItem(KEY)) || this.#len("--ev-sheet");
    // the row, the rail (it hides in the Universe and below the large breakpoint) and this divider (drawn only beside an
    // open sheet): any of them changing size changes the room the sheet has
    for (const el of [this.parentElement, document.getElementById("rail-pane"), this]) if (el) this.#resize.observe(el);
    this.#width(this.#want);
  }
  disconnectedCallback() { this.#resize.disconnect(); }
  get #sheet() { return document.getElementById("sheet-pane"); }
  #len(name) { return toPx(getComputedStyle(this).getPropertyValue(name), parseFloat(getComputedStyle(document.documentElement).fontSize)); }
  #now() { return Number(this.getAttribute("aria-valuenow")) || this.#want; }
  /** The row the main area and the sheet share: the parent's width less the rail (when shown) and this divider. */
  #total() {
    const row = this.parentElement.getBoundingClientRect().width, rail = document.getElementById("rail-pane")?.getBoundingClientRect().width ?? 0;
    return row - rail - this.getBoundingClientRect().width;
  }
  #choose(want) {
    this.#want = want;
    const { w, narrow } = this.#width(want);
    this.#want = w;  // what fitted is what the reader chose
    if (!narrow) localStorage.setItem(KEY, String(w));
  }
  #width(want) {
    const got = splitClamp(want, this.#total(), this.#len("--ev-sheet-min"), this.#len("--ev-main-min"));
    document.body.toggleAttribute("data-narrow", got.narrow);
    this.#sheet?.style.setProperty("--ev-sheet-w", `${got.w}px`);
    this.setAttribute("aria-valuemin", String(Math.round(this.#len("--ev-sheet-min"))));
    this.setAttribute("aria-valuemax", String(Math.round(got.max)));
    this.setAttribute("aria-valuenow", String(got.w));
    this.setAttribute("aria-valuetext", t("split.value", { px: got.w }));
    return got;
  }
  #drag = (e) => {
    e.preventDefault();
    const from = this.#now(), x0 = e.clientX;  // relative to where it was grabbed: pressing it moves nothing
    this.setPointerCapture?.(e.pointerId);
    document.body.classList.add("select-none");
    const move = (ev) => this.#choose(from + x0 - ev.clientX);
    const end = () => {
      document.body.classList.remove("select-none");
      for (const [type, fn] of [["pointermove", move], ["pointerup", end], ["pointercancel", end], ["lostpointercapture", end]]) this.removeEventListener(type, fn);
    };
    this.addEventListener("pointermove", move);
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) this.addEventListener(type, end);
  };
}
define("ev-divider", EvDivider);
