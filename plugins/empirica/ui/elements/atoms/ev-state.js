import { EvElement, TONE, define, icon, stateOf, t } from "../core/element.js";

/**
 * One state as glyph (+ word). Level 0 = icon only, level 1 = hover/focus hint (D, S1).
 * @element ev-state
 * @attr {"file"|"claim"|"audit"|"run"|"step"} domain - which vocabulary
 * @attr {string} value - a value of that vocabulary (states.json); unknown values render as "Unknown"
 * @attr {"icon"|"label"} show - icon only, or icon + word (default label)
 * @attr {"plain"|"chip"} variant - bare, or a Basecoat badge on a pale tint (default plain)
 */
// chip = Basecoat's badge (shape follows the style pack); tone classes set its colours.
const VARIANT = { plain: "inline-flex items-center gap-2xs", chip: "badge" };
class EvState extends EvElement {
  static observedAttributes = ["domain", "value", "show", "variant"];
  render() {
    const s = stateOf(this.getAttribute("domain"), this.getAttribute("value"));
    const tone = TONE[s.tone];
    const chip = this.getAttribute("variant") === "chip";
    const label = t(`${s.copy}.label`);
    const word = this.getAttribute("show") === "icon"
      ? `<span class="sr-only">${label}</span>`
      : chip ? `<span>${label}</span>` : `<span class="text-s font-medium">${label}</span>`;
    return `<span class="${VARIANT[chip ? "chip" : "plain"]} ${tone.fg} ${chip ? tone.tint : ""}" data-hint="${t(`${s.copy}.hint`)}" tabindex="0">${icon(s.icon)}${word}</span>`;
  }
}
define("ev-state", EvState);
