import { EvElement, define, esc, t } from "../core/element.js";
import catalogue from "../../catalogue.json" with { type: "json" };

/**
 * A value to copy (a session locator): the value itself, selectable, and a button that puts it on the clipboard. The
 * button says "Copied" for a moment, or that the browser would not let it.
 * @element ev-copy
 * @attr {string} text - the value to copy
 * @attr {string} [label] - what it is, for the button's accessible name (a copy key)
 */
class EvCopy extends EvElement {
  static observedAttributes = ["text", "label"];
  #said = null; #timer = 0;
  constructor() {
    super();
    this.addEventListener("click", async (e) => {
      if (!e.target.closest("[data-copy]")) return;
      try { await navigator.clipboard.writeText(this.getAttribute("text") ?? ""); this.#said = "copy.done"; } catch { this.#said = "copy.failed"; }
      clearTimeout(this.#timer);
      this.#timer = setTimeout(() => { this.#said = null; this.update(); }, catalogue.copied_ms);
      this.update();
    });
  }
  disconnectedCallback() { clearTimeout(this.#timer); }
  render() {
    const text = this.getAttribute("text") ?? "", label = this.getAttribute("label");
    const name = label ? `${t("copy.button")}: ${t(label)}` : t("copy.button");
    return `<span class="inline-flex min-w-0 items-center gap-xs"><code class="min-w-0 break-all font-mono text-xs" data-value>${esc(text)}</code><button type="button" class="btn shrink-0" data-variant="outline" data-size="sm" data-copy aria-label="${esc(name)}">${t(this.#said ?? "copy.button")}</button></span>`;
  }
}
define("ev-copy", EvCopy);
