import { EvElement, define, esc, t } from "../core/element.js";

/**
 * Prose that may be long: at most two lines, and "Show all" appears only when the text does not fit in them
 * (measured, and measured again when the box changes width). "Show all" opens it in place; "Show less" folds it. An
 * open or folded state survives a redraw. Used for a goal, a decision's answer, a research quote and a note
 * (long text is disclosed, short text never shows a control).
 * @element ev-more
 * @attr {string} text - the prose as written
 * @attr {string} [label] - a copy key for a small caption above it (e.g. the goal's)
 */
class EvMore extends EvElement {
  static observedAttributes = ["text", "label"];
  #open = false; #watch = null;
  constructor() {
    super();
    this.addEventListener("click", (e) => {
      if (!e.target.closest("[data-more]")) return;
      this.#open = !this.#open;
      this.update();
    });
  }
  disconnectedCallback() { this.#watch?.disconnect(); }
  render() {
    const text = esc(this.getAttribute("text") ?? ""), label = this.getAttribute("label");
    return `<div class="flex min-w-0 max-w-prose flex-col items-start gap-3xs text-s">
      ${label ? `<span class="text-xs font-medium tracking-wide text-muted-foreground uppercase">${t(label)}</span>` : ""}
      <span class="${this.#open ? "min-w-0 max-w-full whitespace-pre-line break-words" : "line-clamp-2 min-w-0 max-w-full whitespace-pre-line break-words"}" data-text>${text}</span>
      <button type="button" class="text-xs text-foreground underline underline-offset-4" data-more aria-expanded="${this.#open}" ${this.#open ? "" : "hidden"}>${t(this.#open ? "more.less" : "more.all")}</button>
    </div>`;
  }
  populate() {
    this.#watch?.disconnect();
    if (this.#open) { this.querySelector("[data-more]").hidden = false; return; }
    const text = this.querySelector("[data-text]"), button = this.querySelector("[data-more]");
    const measure = () => { button.hidden = !(text.clientHeight > 0 && text.scrollHeight > text.clientHeight + 1); };
    measure();
    this.#watch = new ResizeObserver(measure);
    this.#watch.observe(text);
  }
}
define("ev-more", EvMore);
