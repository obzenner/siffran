import { EvElement, TONE, define, icon, stateOf, t } from "../core/element.js";

/**
 * One value of a contract vocabulary as its word, with its icon beside it (an icon only ever repeats the word).
 * @element ev-state
 * @attr {string} domain - a vocabulary of states.json
 * @attr {string} value - a value of that vocabulary; an unknown value renders as "Unknown"
 * @attr {"plain"|"chip"} variant - bare, or a Basecoat badge on a pale tint (default plain)
 */
class EvState extends EvElement {
  static observedAttributes = ["domain", "value", "variant"];
  render() {
    const s = stateOf(this.getAttribute("domain"), this.getAttribute("value"));
    const tone = TONE[s.tone];
    const label = t(`${s.copy}.label`);
    return this.getAttribute("variant") === "chip"
      ? `<span class="badge ${tone.fg} ${tone.tint}">${icon(s.icon)}<span>${label}</span></span>`
      : `<span class="inline-flex items-center gap-2xs ${tone.fg}">${icon(s.icon)}<span class="text-s font-medium">${label}</span></span>`;
  }
}
define("ev-state", EvState);
