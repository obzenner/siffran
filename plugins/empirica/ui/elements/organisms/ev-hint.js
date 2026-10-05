import { define } from "../core/element.js";

/**
 * The page's single level-1 hint (D). Shows the `data-hint` text of whatever is hovered or focused, in the
 * browser's top layer (Popover API), anchored with CSS anchor positioning, so no ancestor can clip it.
 * Mount exactly once per page. It is a page service, so it is allowed (E3 exceptions) to listen at document level,
 * to observe the DOM while shown, and to use one short timer; all are removed on disconnect.
 * - Hoverable (WCAG 1.4.13): leaving the target starts a short grace period, and moving onto the hint keeps it.
 * - A pointer leaving does not hide a hint whose target has keyboard focus; Esc always hides it.
 * - If its target is removed from the page (re-rendered away), the hint hides at once.
 * - It adds itself to the target's aria-describedby and restores the previous value and anchor-name.
 * @element ev-hint
 */
const EVENTS = ["pointerover", "focusin", "pointerout", "focusout", "keydown"];
const GRACE = 150;
class EvHint extends HTMLElement {
  #target = null;
  #prev = { described: null, anchor: "" };
  #timer = 0;
  #gone = new MutationObserver(() => { if (this.#target && !this.#target.isConnected) this.#hide(); });
  connectedCallback() {
    this.setAttribute("popover", "manual");
    this.setAttribute("role", "tooltip");
    this.id ||= "ev-hint";
    this.className = "ev-hint rounded-m bg-primary px-xs py-2xs text-xs text-primary-foreground shadow-2";
    for (const type of EVENTS) document.addEventListener(type, this.#on);
  }
  disconnectedCallback() {
    this.#hide();
    for (const type of EVENTS) document.removeEventListener(type, this.#on);
  }
  #inside(node) { return !!node && (this.contains(node) || !!this.#target?.contains(node)); }
  #on = (e) => {
    if (e.type === "keydown") { if (e.key === "Escape") this.#hide(); return; }
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
    this.#timer = setTimeout(() => this.#hide(), GRACE);
  };
  #enter(target) {
    clearTimeout(this.#timer);
    if (!target || target === this.#target) return;
    this.#hide();
    this.#target = target;
    this.#prev = { described: target.getAttribute("aria-describedby"), anchor: target.style.getPropertyValue("anchor-name") };
    target.style.setProperty("anchor-name", "--ev-hint");
    target.setAttribute("aria-describedby", [this.#prev.described, this.id].filter(Boolean).join(" "));
    this.textContent = target.dataset.hint;
    this.showPopover();
    this.#gone.observe(document.body, { childList: true, subtree: true });
  }
  #hide() {
    clearTimeout(this.#timer);
    this.#gone.disconnect();
    const target = this.#target;
    if (!target) return;
    if (this.#prev.anchor) target.style.setProperty("anchor-name", this.#prev.anchor);
    else target.style.removeProperty("anchor-name");
    if (this.#prev.described) target.setAttribute("aria-describedby", this.#prev.described);
    else target.removeAttribute("aria-describedby");
    this.#target = null;
    if (this.matches(":popover-open")) this.hidePopover();
  }
}
define("ev-hint", EvHint);
