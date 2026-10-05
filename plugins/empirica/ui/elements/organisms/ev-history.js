import { EvElement, TONE, define, esc, icon, stepOf, t } from "../core/element.js";

/**
 * A run's history: the dot strip as an overview, then the steps that matter as a readable list (oldest first).
 * Consecutive bookkeeping steps (states.json `quiet`) fold into one quiet line. Consecutive repeats of one kind and
 * outcome gather into one disclosure ("Research · supporting · 6 steps"), open when it holds the selected step;
 * a failure (a negative tone) always stands alone (event-sequence simplification). Dots and rows select the same
 * step (ev-select {step}).
 * @element ev-history
 * @attr {number} selected - step number
 * @prop {{steps:{n:number,kind:string,outcome:?string,claim:?string}[],claims:{id:string,text:string}[]}} data
 */
const ROW = { on: "item flex-nowrap text-left bg-muted", off: "item flex-nowrap text-left hover:bg-muted" };
class EvHistory extends EvElement {
  static observedAttributes = ["selected"];
  constructor() {
    super();
    this.addEventListener("click", (e) => {
      const row = e.target.closest("[data-step]");
      if (row) this.emit("ev-select", { step: Number(row.dataset.step) });
    });
  }
  render() {
    const h = this.data;
    if (!h?.steps?.length) return "";
    const sel = Number(this.getAttribute("selected"));
    const text = new Map((h.claims ?? []).map((c) => [c.id, c.text]));
    const rows = [];
    let quiet = 0;
    const flush = () => {
      if (!quiet) return;
      rows.push(`<li class="px-s py-2xs text-xs text-muted-foreground">${quiet === 1 ? t("history.bookkeeping.one") : t("history.bookkeeping", { n: quiet })}</li>`);
      quiet = 0;
    };
    const row = (s, st) => {
      // A step can point at a claim that a later graph replaced (the record keeps no rename link).
      const about = s.claim && (text.get(s.claim) ?? t("history.claim.gone"));
      return `<button type="button" class="${s.n === sel ? ROW.on : ROW.off}" data-size="xs" data-step="${s.n}">
        <figure class="${TONE[st.tone].fg}">${icon(st.icon)}</figure>
        <section class="min-w-0"><span class="text-s text-foreground">${esc(st.label)}</span>${about ? `<span class="truncate text-xs text-muted-foreground">${esc(about)}</span>` : ""}</section>
        <aside class="text-xs tabular-nums text-muted-foreground">${t("history.step", { n: s.n })}</aside>
      </button>`;
    };
    // Meaningful steps, with consecutive repeats of one state gathered (failures always stand alone).
    const runs = [];
    for (const s of h.steps) {
      const st = stepOf(s.kind, s.outcome);
      if (st.quiet) { runs.push({ quiet: true }); continue; }
      const last = runs.at(-1), same = last && !last.quiet && last.kind === s.kind && last.outcome === s.outcome;
      if (same && st.tone !== "negative") last.steps.push(s);
      else runs.push({ kind: s.kind, outcome: s.outcome, st, steps: [s] });
    }
    for (const g of runs) {
      if (g.quiet) { quiet++; continue; }
      flush();
      const st = g.st;
      if (g.steps.length === 1) { rows.push(`<li>${row(g.steps[0], st)}</li>`); continue; }
      const first = g.steps[0].n, last = g.steps.at(-1).n, open = g.steps.some((s) => s.n === sel);
      rows.push(`<li><details ${open ? "open" : ""}><summary class="${ROW.off} cursor-pointer list-none" data-size="xs" data-group="${first}">
        <figure class="${TONE[st.tone].fg}">${icon(st.icon)}</figure>
        <section class="min-w-0"><span class="text-s text-foreground">${t("history.repeat", { label: st.label, n: g.steps.length })}</span></section>
        <aside class="text-xs tabular-nums text-muted-foreground">${t("history.range", { from: first, to: last })}</aside>
      </summary><ol class="ml-l border-l border-border pl-s">${g.steps.map((s) => `<li>${row(s, st)}</li>`).join("")}</ol></details></li>`);
    }
    flush();
    return `<div class="flex flex-col gap-l"><ev-steps ${sel ? `selected="${sel}"` : ""}></ev-steps><ol class="flex flex-col" aria-label="${t("history.label")}">${rows.join("")}</ol></div>`;
  }
  populate() {
    const strip = this.querySelector("ev-steps");
    if (strip) strip.data = this.data.steps;
  }
}
define("ev-history", EvHistory);
