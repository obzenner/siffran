import { EvElement, define, esc, stateOf, t } from "../core/element.js";
import { ATTEMPT, LINE, SUPPORTS, arrowDef, auditMark, claimMark, diagnosticMark, runMark } from "../core/marks.js";

/**
 * The view's key (VIEW.md), drawn with the view's own marks (core/marks.js), so it cannot look different from
 * what it explains: the run states, audits, records not fully read and claim states present, each with how many
 * are showing, the gating outline, and the two kinds of line. The page counts what is showing; the key only draws (X1).
 * @element ev-legend
 * @prop {{runs:{value:string,n:number}[], audits:{value:string,n:number}[], diagnostics:{value:string,n:number}[],
 *         claims:{value:string,n:number}[],
 *         gating:number, supports:number, attempts:number}} data
 */
const ROW = "flex items-baseline gap-m text-xs";
const LEAD = "w-3xl shrink-0 text-muted-foreground";
const ITEM = "flex items-center gap-2xs";
const COUNT = "tabular-nums text-muted-foreground";
const pic = (body, w = 14) => `<svg viewBox="0 0 ${w} 14" class="h-m w-auto shrink-0" aria-hidden="true" focusable="false">${body}</svg>`;
class EvLegend extends EvElement {
  render() {
    const d = this.data;
    if (!d) return "";
    const item = (mark, word, n) => `<li class="${ITEM}">${mark}${esc(word)} <span class="${COUNT}">${n}</span></li>`;
    const row = (of, items) => (items.length ? `<div class="${ROW}"><span class="${LEAD}">${t(`legend.of.${of}`)}</span><ul class="flex flex-wrap items-center gap-x-l gap-y-xs text-foreground">${items.join("")}</ul></div>` : "");
    const word = (domain, v) => t(`${stateOf(domain, v).copy}.label`);
    const line = (cls, extra) => pic(`${arrowDef(`ev-legend-${extra.id}`)}<path d="M1,7 L23,7" class="${cls}" stroke-width="${extra.width}" ${extra.dash ? `stroke-dasharray="${extra.dash}"` : ""} marker-end="url(#ev-legend-${extra.id})"></path>`, 24);
    return `<div class="flex flex-col gap-xs" role="group" aria-label="${t("legend.view")}">
      ${row("run", d.runs.map(({ value, n }) => item(pic(runMark(7, 7, value)), word("run", value), n)))}
      ${row("audit", d.audits.map(({ value, n }) => item(pic(auditMark(2, 2, value)), word("audit", value), n)))}
      ${row("diagnostic", (d.diagnostics ?? []).map(({ value, n }) => item(pic(diagnosticMark(2, 2, value)), word("diagnostic", value), n)))}
      ${row("claim", [...d.claims.map(({ value, n }) => item(pic(claimMark(7, 7, value, false)), word("claim", value), n)),
        ...(d.gating ? [item(pic(claimMark(7, 7, "approved", true)), t("legend.gating"), d.gating)] : [])])}
      ${row("line", [...(d.supports ? [item(line(LINE, { id: "s", ...SUPPORTS }), t("legend.supports"), d.supports)] : []),
        ...(d.attempts ? [item(line(LINE, { id: "a", ...ATTEMPT }), t("legend.attempt"), d.attempts)] : [])])}
    </div>`;
  }
}
define("ev-legend", EvLegend);
