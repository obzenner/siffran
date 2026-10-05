import { define, t } from "../core/element.js";

/**
 * Main content + a resizable side panel (L1, D2). The main view stays visible; the divider is draggable (pointer,
 * with cancel handled), movable with arrow keys when focused, reset by double-click, and reports its width to
 * assistive tech. Esc or ev-close closes the panel and returns focus to where it was when the panel opened.
 * Widths come from tokens (width.panel, panel-min, main-min via --ev-panel*) and are re-clamped whenever the element
 * resizes. Below panel-min + main-min the element sets `narrow` and the panel takes the full width (D2 exception).
 * Children: exactly two, the main region and then the panel. Emits ev-panel {open} when the panel opens or closes.
 * Behavioural layout element: it may listen at document level for Esc (E3 exception, removed on disconnect).
 * @element ev-split
 * @attr {boolean} open - panel visible
 * @attr {string} key - localStorage key for the remembered width
 */
const STEP = 32;
function px(el, name) {
  const v = getComputedStyle(el).getPropertyValue(name).trim();
  return v.endsWith("rem") ? parseFloat(v) * parseFloat(getComputedStyle(document.documentElement).fontSize) : parseFloat(v);
}
class EvSplit extends HTMLElement {
  static observedAttributes = ["open"];
  #handle = null;
  #return = null;
  #want = 0;  // the width the user chose; resizes clamp from it, so a narrow moment never shrinks it for good
  #resize = new ResizeObserver(() => this.#width(this.#want));
  #onKey = (e) => { if (e.key === "Escape" && this.hasAttribute("open")) this.removeAttribute("open"); };
  #onClose = () => this.removeAttribute("open");
  connectedCallback() {
    if (!this.#handle) this.#build();
    this.#choose(Number(localStorage.getItem(this.#key())) || px(this, "--ev-panel"));
    this.#resize.observe(this);
    document.addEventListener("keydown", this.#onKey);
    this.addEventListener("ev-close", this.#onClose);
  }
  disconnectedCallback() {
    this.#resize.disconnect();
    document.removeEventListener("keydown", this.#onKey);
    this.removeEventListener("ev-close", this.#onClose);
  }
  attributeChangedCallback(name, was, now) {
    if ((was === null) === (now === null)) return;
    if (now !== null) {
      const a = document.activeElement;
      this.#return = a === this.#handle || this.lastElementChild.contains(a) ? this.#return : a;
      this.#width(this.#want);
    } else {
      const a = document.activeElement;
      const lost = !a || a === document.body || a === this.#handle || this.lastElementChild.contains(a);
      if (lost && this.#return?.isConnected) this.#return.focus();
    }
    this.dispatchEvent(new CustomEvent("ev-panel", { bubbles: true, detail: { open: now !== null } }));
  }
  #build() {
    const [, panel] = this.children;
    const h = (this.#handle = document.createElement("div"));
    h.className = "ev-split-handle group w-xs cursor-col-resize justify-center";  // display: app.css
    h.setAttribute("role", "separator");
    h.setAttribute("aria-orientation", "vertical");
    h.setAttribute("aria-label", t("split.handle"));
    h.tabIndex = 0;
    h.innerHTML = `<span class="h-full w-px bg-border group-hover:bg-ring group-focus-visible:bg-ring"></span>`;
    this.insertBefore(h, panel);
    h.addEventListener("pointerdown", this.#drag);
    h.addEventListener("dblclick", () => this.#choose(px(this, "--ev-panel")));
    h.addEventListener("keydown", (e) => {
      const d = { ArrowLeft: STEP, ArrowRight: -STEP }[e.key];
      if (d) { e.preventDefault(); this.#choose(this.#current() + d); }
    });
  }
  #focusPanel() {
    const a = document.activeElement;
    if (this.lastElementChild.contains(a)) return;
    if (a && a !== document.body && a !== this.#handle) this.#return = a;  // keep the main-region invoker
    (this.lastElementChild.querySelector("button, a[href], [tabindex]") ?? this.lastElementChild).focus({ preventScroll: true });
  }
  #key() { return `ev-split:${this.getAttribute("key") ?? "default"}`; }
  #current() { return parseFloat(this.style.getPropertyValue("--ev-split-w")) || px(this, "--ev-panel"); }
  #choose(want) {
    this.#want = want;
    this.#width(want);
    this.#want = this.#current();  // what fitted is what the user chose
    if (!this.hasAttribute("narrow")) localStorage.setItem(this.#key(), String(this.#want));
  }
  #width(want) {
    const total = this.getBoundingClientRect().width, min = px(this, "--ev-panel-min"), mainMin = px(this, "--ev-main-min");
    const narrow = total > 0 && total < min + mainMin;
    const hiding = narrow && !this.hasAttribute("narrow");
    this.toggleAttribute("narrow", narrow);
    // Narrow hides the main region: whatever was focused there would be lost, so focus moves into the panel.
    if (this.hasAttribute("open") && (hiding || (narrow && !this.lastElementChild.contains(document.activeElement)))) this.#focusPanel();
    const max = total > 0 ? Math.max(min, total - mainMin) : want;
    const w = Math.round(Math.min(max, Math.max(min, want)));
    this.style.setProperty("--ev-split-w", `${w}px`);
    if (this.#handle) {
      this.#handle.setAttribute("aria-valuemin", String(Math.round(min)));
      this.#handle.setAttribute("aria-valuemax", String(Math.round(max)));
      this.#handle.setAttribute("aria-valuenow", String(w));
    }
  }
  #drag = (e) => {
    e.preventDefault();
    const h = this.#handle, right = this.getBoundingClientRect().right;
    h.setPointerCapture(e.pointerId);
    this.classList.add("select-none");
    const move = (ev) => this.#choose(right - ev.clientX);
    const end = () => {
      this.classList.remove("select-none");
      for (const [type, fn] of [["pointermove", move], ["pointerup", end], ["pointercancel", end], ["lostpointercapture", end]]) h.removeEventListener(type, fn);
    };
    h.addEventListener("pointermove", move);
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) h.addEventListener(type, end);
  };
}
define("ev-split", EvSplit);
