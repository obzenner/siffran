import { EvElement, TONE, define, stepOf, t } from "../core/element.js";

/**
 * One step of a run's history as a dot. Graph changes, spikes and audits are larger and coloured (a step with an
 * outcome takes its outcome's tone); research, requests and bookkeeping are faint pins (states.json `minor`).
 * @element ev-step
 * @attr {string} value - the step's kind (states.json "step")
 * @attr {string} outcome - its outcome, when it has one (states.json "outcome")
 * @attr {number} n - position (1-based)
 * @attr {number} total - number of steps in the run
 * @attr {boolean} selected - highlighted
 */
const SIZE = { minor: "size-2xs", major: "size-xs" };
class EvStep extends EvElement {
  static observedAttributes = ["value", "outcome", "n", "total", "selected"];
  constructor() {
    super();
    this.addEventListener("click", () => this.emit("ev-select", { step: Number(this.getAttribute("n")) }));
  }
  render() {
    const s = stepOf(this.getAttribute("value"), this.getAttribute("outcome"));
    const pos = t("step.position", { n: this.getAttribute("n"), total: this.getAttribute("total") });
    const hint = `${pos} · ${s.label}`;
    const ring = this.hasAttribute("selected") ? "ring-2 ring-ring ring-offset-2 ring-offset-background" : "";
    return `<button type="button" class="grid size-s place-items-center rounded-full" data-hint="${hint}" aria-label="${hint}"><span class="${s.minor ? SIZE.minor : SIZE.major} rounded-full bg-current ${s.minor ? "text-subtle" : TONE[s.tone].fg} ${ring}"></span></button>`;
  }
}
define("ev-step", EvStep);
