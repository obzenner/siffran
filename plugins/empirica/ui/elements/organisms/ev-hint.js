import { PRIORITY, layer } from "../core/dismiss.js";
import { define } from "../core/element.js";

/**
 * The page's single level-1 hint (D). Shows the `data-hint` text of whatever is hovered or focused, in the
 * browser's top layer (Popover API), anchored with CSS anchor positioning, so no ancestor can clip it.
 * Mount exactly once per page. It is a page service, so it is allowed (E3 exceptions) to listen at document level,
 * to observe the DOM while shown, and to use one short timer; all are removed on disconnect.
 * - Hoverable (WCAG 1.4.13): leaving the target starts a short grace period, and moving onto the hint keeps it.
 * - A pointer leaving does not hide a hint whose target has keyboard focus; Esc hides it first (core/dismiss.js: the
 *   innermost open layer takes the press).
 * - If its target is removed from the page (re-rendered away), the hint hides at once.
 * - It is anchored to a proxy box, not to the target: an SVG child is not a reliable CSS anchor, so the proxy (an HTML
 *   box, set through the CSSOM to the target's `getBoundingClientRect()`) carries the anchor name, follows scrolling and
 *   resizing while the hint is shown, and the hint hides when the proxy leaves the screen (`position-visibility`).
 * - It adds itself to the target's aria-describedby and restores the previous value.
 * @element ev-hint
 */
const EVENTS = ["pointerover", "focusin", "pointerout", "focusout"];
/** How long a hint outlives its target (tokens: duration.grace, read as the CSS variable the build emits). */
const grace = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--duration-grace"));
class EvHint extends HTMLElement {
  #target = null;
  #prev = { described: null };
  #proxy = null;
  #timer = 0;
  #off = null;
  #gone = new MutationObserver(() => { if (this.#target && !this.#target.isConnected) this.#hide(); });
  connectedCallback() {
    this.setAttribute("popover", "manual");
    this.setAttribute("role", "tooltip");
    this.id ||= "ev-hint";
    this.#proxy = document.createElement("span");
    this.#proxy.className = "ev-hint-anchor";
    this.#proxy.setAttribute("aria-hidden", "true");
    this.after(this.#proxy);
    this.className = "ev-hint rounded-m bg-primary px-xs py-2xs text-xs text-primary-foreground shadow-2";
    for (const type of EVENTS) document.addEventListener(type, this.#on);
    this.#off = layer({ priority: PRIORITY.hint, open: () => !!this.#target, close: () => this.#hide() });
  }
  disconnectedCallback() {
    this.#off?.();
    this.#hide();
    this.#proxy?.remove();
    for (const type of EVENTS) document.removeEventListener(type, this.#on);
  }
  #inside(node) { return !!node && (this.contains(node) || !!this.#target?.contains(node)); }
  #on = (e) => {
    if (e.type === "pointerover") {
      if (this.#inside(e.target)) { clearTimeout(this.#timer); return; }
      const target = e.target.closest?.("[data-hint]");
      return target ? this.#enter(target) : undefined;  // plain content: let a pending departure hide the hint
    }
    if (e.type === "focusin") return this.#enter(e.target.closest?.("[data-hint]"));
    if (!this.#target || this.#inside(e.relatedTarget)) return;
    if (e.type === "focusout") return this.#hide();
    if (this.#target.contains(document.activeElement)) return;
    clearTimeout(this.#timer);
    this.#timer = setTimeout(() => this.#hide(), grace());
  };
  #enter(target) {
    clearTimeout(this.#timer);
    if (!target || target === this.#target) return;
    this.#hide();
    this.#target = target;
    this.#prev = { described: target.getAttribute("aria-describedby") };
    this.#place();
    for (const type of ["scroll", "resize"]) addEventListener(type, this.#place, { capture: true, passive: true });
    target.setAttribute("aria-describedby", [this.#prev.described, this.id].filter(Boolean).join(" "));
    this.textContent = target.dataset.hint;
    this.showPopover();
    this.#gone.observe(document.body, { childList: true, subtree: true });
  }
  /** Put the proxy box over the target, as the browser draws it now. */
  #place = () => {
    if (!this.#target) return;
    const r = this.#target.getBoundingClientRect();
    for (const [k, v] of Object.entries({ left: r.left, top: r.top, width: r.width, height: r.height })) this.#proxy.style.setProperty(k, `${v}px`);
  };
  #hide() {
    clearTimeout(this.#timer);
    this.#gone.disconnect();
    const target = this.#target;
    if (!target) return;
    for (const type of ["scroll", "resize"]) removeEventListener(type, this.#place, { capture: true });
    if (this.#prev.described) target.setAttribute("aria-describedby", this.#prev.described);
    else target.removeAttribute("aria-describedby");
    this.#target = null;
    if (this.matches(":popover-open")) this.hidePopover();
  }
}
define("ev-hint", EvHint);
