import { EvElement, define, t } from "../core/element.js";

/**
 * How much of a budget is used: a quiet bar plus "1 of 10".
 * @element ev-meter
 * @attr {string} label - copy key for the budget name (e.g. budget.passes)
 * @attr {number} used
 * @attr {number} max
 */
class EvMeter extends EvElement {
  static observedAttributes = ["label", "used", "max"];
  render() {
    const used = Number(this.getAttribute("used") ?? 0), max = Number(this.getAttribute("max") ?? 0);
    const name = t(this.getAttribute("label") ?? "");
    const amount = t("budget.of", { used, max });
    return `<span class="inline-flex flex-col gap-3xs text-xs text-muted-foreground" data-hint="${name}: ${amount}"><span class="inline-flex justify-between gap-xs"><span>${name}</span><span class="tabular-nums">${amount}</span></span><meter class="ev-meter" aria-label="${name}: ${amount}" min="0" max="${max}" value="${used}" ${used >= max && max > 0 ? 'data-full=""' : ""}>${amount}</meter></span>`;
  }
}
define("ev-meter", EvMeter);
