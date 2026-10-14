import { EvElement, define, t } from "../core/element.js";

/**
 * How much of a budget is used: a quiet bar plus "1 of 10" (with a zero ceiling, "0 of 0" and an empty track, no native meter).
 * @element ev-meter
 * @attr {string} label - copy key for the budget name (e.g. budget.passes)
 * @attr {number} used
 * @attr {number} max
 * @attr {"name"|"amount"} show - name and amount (default), or the amount alone where the name is already the row's label
 */
class EvMeter extends EvElement {
  static observedAttributes = ["label", "used", "max", "show"];
  render() {
    const used = Number(this.getAttribute("used") ?? 0), max = Number(this.getAttribute("max") ?? 0);
    const name = t(this.getAttribute("label") ?? "");
    const amount = t("budget.of", { used, max });
    // a zero ceiling has no native meter (its max must exceed its min): an empty track, named the same for assistive tech
    const bar = max > 0
      ? `<meter class="ev-meter" aria-label="${name}: ${amount}" min="0" max="${max}" value="${used}" ${used >= max ? 'data-full=""' : ""}>${amount}</meter>`
      : `<span class="ev-meter" role="img" aria-label="${name}: ${amount}" data-meter></span>`;
    return `<span class="inline-flex flex-col gap-3xs text-xs text-muted-foreground"><span class="inline-flex justify-between gap-xs">${this.getAttribute("show") === "amount" ? "" : `<span>${name}</span>`}<span class="tabular-nums">${amount}</span></span>${bar}</span>`;
  }
}
define("ev-meter", EvMeter);
