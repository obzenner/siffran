import { EvElement, define, t } from "../core/element.js";

/**
 * How much of a budget is used: a quiet bar plus "1 of 10".
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
    return `<span class="inline-flex flex-col gap-3xs text-xs text-muted-foreground"><span class="inline-flex justify-between gap-xs">${this.getAttribute("show") === "amount" ? "" : `<span>${name}</span>`}<span class="tabular-nums">${amount}</span></span><meter class="ev-meter" aria-label="${name}: ${amount}" min="0" max="${max}" value="${used}" ${used >= max && max > 0 ? 'data-full=""' : ""}>${amount}</meter></span>`;
  }
}
define("ev-meter", EvMeter);
