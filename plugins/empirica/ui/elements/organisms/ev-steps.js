import { EvElement, define, esc } from "../core/element.js";

/**
 * A run's history as a quiet strip of dots, oldest left. Bookkeeping steps are faint pins; spikes, audits and
 * graph changes stand out. Clicking a dot re-emits ev-select from the dot.
 * @element ev-steps
 * @attr {number} selected - step number to highlight
 * @prop {{n:number,kind:string,outcome:?string}[]} data - the run's steps (contract `steps[]`)
 */
class EvSteps extends EvElement {
  static observedAttributes = ["selected"];
  render() {
    const steps = this.data ?? [];
    const sel = Number(this.getAttribute("selected"));
    return `<div class="flex flex-wrap items-center gap-y-2xs">${steps
      .map((s) => `<ev-step value="${esc(s.kind)}" ${s.outcome ? `outcome="${esc(s.outcome)}"` : ""} n="${s.n}" total="${steps.length}" ${s.n === sel ? "selected" : ""}></ev-step>`)
      .join("")}</div>`;
  }
}
define("ev-steps", EvSteps);
