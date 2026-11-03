import { EvElement, define, esc, t } from "../core/element.js";
import { shortIdentifier } from "../../app/identifiers.js";
import catalogue from "../../catalogue.json" with { type: "json" };

/**
 * One copyable machine identifier (PR 17 §4): the value shown as the one identifier atom — its short form
 * (`elements/core/identifiers.js`, `shortIdentifier`: a digest's head and tail with its `sha256:` kept, a URL's
 * origin, a path's head and tail), the exact value in the chip's `title` and on the clipboard — and the button that
 * copies it. The button says "Copied" for a moment, or that the browser would not let it.
 * With `quiet` (a digest inside prose, a list of identities) the button waits for hover or focus before it shows,
 * so a sentence is not a row of buttons; it is still there, and the tab key still reaches it.
 * @element ev-copy
 * @attr {string} text - the value to copy
 * @attr {string} [label] - what it is, for the button's accessible name (a copy key)
 * @attr {boolean} [quiet] - the Copy button is revealed on hover or focus, not shown always
 */
class EvCopy extends EvElement {
  static observedAttributes = ["text", "label", "quiet"];
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
    // PR 17 §4: the eye reads the short form; the title and the clipboard carry the exact value.
    return `<span class="inline-flex min-w-0 items-center gap-2xs"${this.hasAttribute("quiet") ? " data-copy-quiet" : ""}><code class="ev-id min-w-0" title="${esc(text)}" data-value>${esc(shortIdentifier(text))}</code><button type="button" class="btn shrink-0" data-variant="outline" data-size="sm" data-copy aria-label="${esc(name)}">${t(this.#said ?? "copy.button")}</button></span>`;
  }
}
define("ev-copy", EvCopy);
