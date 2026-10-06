import { PRIORITY, layer } from "../core/dismiss.js";
import { EvElement, define, esc, joined, quietOf, stateOf, t } from "../core/element.js";
import { ATTEMPT, LINE, SUPPORTS, arrowDef, auditMark, claimMark, diagnosticMark, runMark } from "../core/marks.js";

/**
 * The view's key (VIEW.md), drawn with the view's own marks (core/marks.js), so it cannot look different from
 * what it explains: the run states, audits, records not fully read and claim states present (approved ones no passing
 * audit reviewed apart, by their review), each with how many
 * are showing, the gating outline, and the two kinds of line. The page counts what is showing; the key only draws (X1).
 * It is one line by default (the totals per group); a click opens the full key as a layer over the picture, so the key
 * never takes the picture's room. Esc closes it (core/dismiss.js).
 * @element ev-legend
 * @prop {{total:number, runs:{value:string,n:number}[], audits:{value:string,n:number}[], diagnostics:{value:string,n:number}[],
 *         claims:{value:string,n:number}[], unsettled:{value:string,n:number}[],
 *         gating:number, supports:number, attempts:number}} data
 */
const ROW = "flex items-baseline gap-m text-xs";
const LEAD = "w-3xl shrink-0 text-muted-foreground";
const ITEM = "flex items-center gap-2xs";
const COUNT = "tabular-nums text-muted-foreground";
const pic = (body, w = 14) => `<svg viewBox="0 0 ${w} 14" class="h-m w-auto shrink-0" aria-hidden="true" focusable="false">${body}</svg>`;
class EvLegend extends EvElement {
  #off = null;
  connectedCallback() {
    super.connectedCallback();
    this.#off = layer({ priority: PRIORITY.popover, open: () => !!this.querySelector("details[open]"), close: () => { this.querySelector("details").open = false; this.querySelector("summary").focus(); } });
  }
  disconnectedCallback() { this.#off?.(); }
  render() {
    const d = this.data;
    if (!d) return "";
    const item = (mark, word, n) => `<li class="${ITEM}">${mark}${esc(word)} <span class="${COUNT}">${n}</span></li>`;
    const row = (of, items) => (items.length ? `<div class="${ROW}"><span class="${LEAD}">${t(`legend.of.${of}`)}</span><ul class="flex flex-wrap items-center gap-x-l gap-y-xs text-foreground">${items.join("")}</ul></div>` : "");
    const word = (domain, v) => t(`${stateOf(domain, v).copy}.label`);
    const line = (cls, extra) => pic(`${arrowDef(`ev-legend-${extra.id}`)}<path d="M1,7 L23,7" class="${cls}" stroke-width="${extra.width}" ${extra.dash ? `stroke-dasharray="${extra.dash}"` : ""} marker-end="url(#ev-legend-${extra.id})"></path>`, 24);
    const total = (xs) => (xs ?? []).reduce((n, x) => n + x.n, 0);
    const sums = [["run", d.total ?? total(d.runs)], ["audit", total(d.audits)], ["diagnostic", total(d.diagnostics)], ["claim", total(d.claims) + total(d.unsettled)], ["line", (d.supports ?? 0) + (d.attempts ?? 0)]].filter(([, n]) => n);
    const summary = joined(...sums.map(([of, n]) => `${t(`legend.of.${of}`)} ${n}`));
    // The open attribute survives a redraw, so a poll never folds the key under the reader.
    const open = this.querySelector("details[open]") ? "open" : "";
    return `<details class="ev-key relative" ${open} role="group" aria-label="${t("legend.view")}">
      <summary class="flex items-baseline gap-m text-xs text-muted-foreground"><span class="font-medium text-foreground underline underline-offset-4">${t("legend.key")}</span><span>${summary}</span></summary>
      <div class="absolute inset-x-0 top-full z-10 mt-2xs flex flex-col gap-xs rounded-m border border-border bg-popover p-s shadow-2">
      ${row("run", d.runs.map(({ value, n }) => item(pic(runMark(7, 7, value)), word("run", value), n)))}
      ${row("audit", d.audits.map(({ value, n }) => item(pic(auditMark(2, 2, value)), word("audit", value), n)))}
      ${row("diagnostic", (d.diagnostics ?? []).map(({ value, n }) => item(pic(diagnosticMark(2, 2, value)), word("diagnostic", value), n)))}
      ${row("claim", [...d.claims.map(({ value, n }) => item(pic(claimMark(7, 7, { state: value, review: quietOf("review") })), word("claim", value), n)),
        ...(d.unsettled ?? []).map(({ value, n }) => item(pic(claimMark(7, 7, { state: quietOf("claim"), review: value })),
          t("legend.unsettled", { state: word("claim", quietOf("claim")), review: word("review", value) }), n)),
        ...(d.gating ? [item(pic(claimMark(7, 7, { state: quietOf("claim"), review: quietOf("review"), gating: true })), t("legend.gating"), d.gating)] : [])])}
      ${row("line", [...(d.supports ? [item(line(LINE, { id: "s", ...SUPPORTS }), t("legend.supports"), d.supports)] : []),
        ...(d.attempts ? [item(line(LINE, { id: "a", ...ATTEMPT }), t("legend.attempt"), d.attempts)] : [])])}
      </div>
    </details>`;
  }
}
define("ev-legend", EvLegend);
