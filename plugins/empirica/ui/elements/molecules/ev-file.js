import { EvElement, TONE, define, esc, icon, stateOf, t } from "../core/element.js";

/**
 * A changed file as a Basecoat item: a tinted square with its state glyph, the path (folder quiet, name strong),
 * the state word. Click opens its detail (emits ev-open {type:"file", path}).
 * Selection is patched in place, so focus survives it.
 * @element ev-file
 * @attr {boolean} selected
 * @prop {{path:string,state:string}} data
 */
const ROW = { on: "item text-left bg-muted", off: "item text-left hover:bg-muted" };
class EvFile extends EvElement {
  static observedAttributes = ["selected"];
  attributeChangedCallback(name) {
    const b = this.querySelector("button");
    if (name === "selected" && b) { b.className = this.hasAttribute("selected") ? ROW.on : ROW.off; return; }
    super.attributeChangedCallback();
  }
  constructor() {
    super();
    this.addEventListener("click", () => this.data && this.emit("ev-open", { type: "file", path: this.data.path }));
  }
  render() {
    const f = this.data;
    if (!f) return "";
    const s = stateOf("file", f.state);
    const tone = TONE[s.tone];
    const i = f.path.lastIndexOf("/");
    return `<button type="button" class="${this.hasAttribute("selected") ? ROW.on : ROW.off}" data-size="sm">
      <figure class="grid size-xl place-items-center rounded-m ${tone.tint} ${tone.fg}">${icon(s.icon)}</figure>
      <section class="min-w-0"><span class="truncate font-mono text-foreground"><span class="text-muted-foreground">${esc(f.path.slice(0, i + 1))}</span>${esc(f.path.slice(i + 1))}</span></section>
      <aside class="text-xs ${tone.fg}" data-hint="${t(`${s.copy}.hint`)}">${t(`${s.copy}.label`)}</aside>
    </button>`;
  }
}
define("ev-file", EvFile);
