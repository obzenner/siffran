import { EvElement, clip, count, define, esc, joined, plain, stateOf, t } from "../core/element.js";
import { ATTEMPT, LINE, R, RING, SUPPORTS, arrowDef, claimGlyph, claimMark, runMark, runSideMark } from "../core/marks.js";
import { rove, stopIndex } from "../core/rove.js";
import { U, attemptPath, viewLayout, wrapLabel } from "../core/view-layout.js";
import { edgePath } from "../core/layout.js";
import catalogue from "../../catalogue.json" with { type: "json" };

const PIC = catalogue.picture;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/**
 * The view (VIEW.md): every project, run and claim it is given, with their relationships, in one picture that
 * scales to fit its box (R1) within bounds in screen pixels (catalogue.json `picture`): marks at least 11 px and growing with the text, text 11 to
 * 16 px, hit targets at least 24 px. Only past the smallest scale does the picture scroll.
 * - A project is a labelled region (the observer's label for it); its label is the project's one target (R2).
 * - A run is a diamond with its state's colour and glyph, its approved-of-total count and its audit's mark, or the
 *   mark of why it could not be read in full (R4); the count is the run's own (`total`), whatever the filters
 *   show of its claims.
 * - A claim is a circle with its state's colour, and its glyph unless the state is the quiet one (V3); a gating
 *   claim has the thick outline (R4).
 * - A support edge is an arrowed line from the supporter to the claim it supports; a next attempt is a dashed
 *   arrowed line from the earlier run to the later one (R3).
 * It draws what it is given: filtering happens before (app/filter.js), so what a filter removes is simply absent
 * (R5). Where things go is core/view-layout.js. Hints are clipped to 60 characters (D); accessible names are longer.
 * Selection is patched in place, so nothing moves on click (R6) and focus survives. Emits ev-open
 * {type:"project", id} | {type:"run", run} | {type:"claim", run, id}.
 * Keyboard: the picture is one tab stop (core/rove.js); arrow keys and Home/End move over the marks in reading
 * order, Enter or Space opens.
 * @element ev-view
 * @attr {string} project - id of the selected project
 * @attr {string} run - key of the selected run (or of the selected claim's run)
 * @attr {string} claim - id of the selected claim within `run`
 * @prop {{projects:{id:string,name:?string,label:string,runs:object[]}[], empty?:string}} data - as app/filter.js returns
 *   it (runs carry `total` and `approved`); `empty` is the copy key to show when there is nothing (default view.empty)
 */
const MARK = "group cursor-pointer outline-none";
const TEXT = "fill-muted-foreground tabular-nums";
class EvView extends EvElement {
  static observedAttributes = ["project", "run", "claim"];
  #size = { width: 0, height: 0 };
  #seen = new ResizeObserver(([e]) => {
    const { width, height } = e.contentRect;
    // Hidden (the panel has the whole width): keep the picture as drawn, so a selection that ends finds it as it was.
    if (!width || !height) return;
    if (Math.abs(width - this.#size.width) < 1 && Math.abs(height - this.#size.height) < 1) return;
    this.#size = { width, height };
    this.update();
  });
  constructor() {
    super();
    const open = (e) => {
      const m = e.target.closest("[data-mark]");
      if (!m) return;
      if (m.dataset.claim) this.emit("ev-open", { type: "claim", run: m.dataset.run, id: m.dataset.claim });
      else if (m.dataset.run) this.emit("ev-open", { type: "run", run: m.dataset.run });
      else this.emit("ev-open", { type: "project", id: m.dataset.project });
    };
    this.addEventListener("click", open);
    this.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target.closest("[data-mark]")) { e.preventDefault(); open(e); } });
    rove(this, "[data-mark]");
  }
  connectedCallback() { this.#seen.observe(this); super.connectedCallback(); }
  /** A redraw (new data, a new size) keeps focus on the same project, run or claim, found by what it is (F7). */
  update() {
    const a = document.activeElement, m = a && this.contains(a) ? a.closest("[data-mark]") : null;
    const at = m && { project: m.dataset.project, run: m.dataset.run, claim: m.dataset.claim };
    super.update();
    if (!at) return;
    const q = (k) => `[data-${k}="${CSS.escape(at[k])}"]`;
    const next = this.querySelector(`[data-mark]${at.project ? q("project") : `${q("run")}${at.claim ? q("claim") : ":not([data-claim])"}`}`);
    if (next && next !== document.activeElement) next.focus({ preventScroll: true });
  }
  disconnectedCallback() { this.#seen.disconnect(); }
  attributeChangedCallback() { if (this.firstElementChild) this.#mark(); }
  /** Patch the selection: one mark, never a re-render. */
  #mark() {
    const p = this.getAttribute("project"), r = this.getAttribute("run"), c = this.getAttribute("claim");
    for (const m of this.querySelectorAll("[data-mark]")) {
      const on = m.dataset.claim ? m.dataset.run === r && m.dataset.claim === c
        : m.dataset.run ? m.dataset.run === r && !c
          : m.dataset.project === p && !r;
      m.toggleAttribute("data-selected", on);
      m.setAttribute("aria-pressed", String(on));
    }
  }
  #target(attrs, label, hint, body, tab) {
    return `<g class="${MARK}" data-mark ${attrs} role="button" tabindex="${tab ? 0 : -1}" aria-pressed="false" aria-label="${esc(label)}" data-hint="${esc(clip(hint, 60))}">${body}</g>`;
  }
  render() {
    const d = this.data;
    if (!d || !this.#size.width) return "";
    if (!d.projects.length) return `<p class="p-l text-s text-muted-foreground">${t(d.empty ?? "view.empty")}</p>`;
    const name = (p) => joined(p.label, count("count.runs", p.runs.length));
    // A label never sets its box's width (VIEW.md Balance): it is wrapped within U.labelCap characters.
    const shape = (p) => { const ls = wrapLabel(name(p)); return { ls, chars: Math.max(...ls.map((s) => s.length)), lines: ls.length }; };
    // Scale: what fits, kept within the bounds that keep marks calm and text legible. Text and targets are sized in
    // screen pixels whatever the scale, so they are converted back to picture units here. A label is drawn at least
    // text_px.min wide-ish whatever the scale, so the room a project reserves for its label grows as the scale falls;
    // the scale is found by trying that a few times (it converges within a few passes: it only ever shrinks the picture).
    const M = U.margin, mark = 2 * R, minScale = Math.max(PIC.mark_px.min, PIC.mark_label_ratio * PIC.text_px.min) / mark, maxScale = PIC.text_px.max / U.labelText;
    const fitOf = (L) => Math.min(this.#size.width / (L.width + 2 * M), this.#size.height / (L.height + 2 * M));
    let L = viewLayout(d.projects, this.#size, (p) => { const s = shape(p); return { chars: s.chars, lines: s.lines }; }, minScale), scale = clamp(fitOf(L), minScale, maxScale);
    for (let pass = 0; pass < 4; pass++) {
      const wide = Math.max(1, PIC.text_px.min / (scale * U.labelText));
      L = viewLayout(d.projects, this.#size, (p) => { const s = shape(p); return { chars: s.chars * wide, lines: s.lines }; }, minScale);
      const next = clamp(fitOf(L), minScale, maxScale);
      if (Math.abs(next - scale) < 1e-3) { scale = next; break; }
      scale = next;
    }
    // The picture fills its box (U1): the cap is the text's. Past the scale where the labels reach their largest
    // size the picture stops growing, and marks grow with the text, not to a fixed size.
    // Text never outgrows its marks: at most mark_px / ratio (a mark is at least ratio times the text beside it).
    const textMax = Math.min(PIC.text_px.max, mark * scale / PIC.mark_label_ratio);
    const px = (v) => v / scale, text = px(clamp(U.text * scale, PIC.text_px.min, textMax)), labelText = px(clamp(U.labelText * scale, PIC.text_px.min, textMax));
    const hit = (cx, cy) => `<rect x="${cx - px(PIC.target_px) / 2}" y="${cy - px(PIC.target_px) / 2}" width="${px(PIC.target_px)}" height="${px(PIC.target_px)}" class="fill-transparent" data-hit></rect>`;
    const runs = new Map(d.projects.flatMap((p) => p.runs.map((r) => [r.key, r])));
    const projects = new Map(d.projects.map((p) => [p.id, p]));
    const group = (xs, by) => xs.reduce((m, x) => m.set(x[by], [...(m.get(x[by]) ?? []), x]), new Map());
    const runsOf = group(L.runs, "project"), claimsOf = group(L.claims, "run");
    const marks = L.projects.length + L.runs.length + L.claims.length;
    const stop = stopIndex(this, marks, 0);
    let i = 0;
    const out = [];
    for (const p of L.projects) {
      const label = name(projects.get(p.id));
      out.push(`<rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="${U.regionRx}" class="fill-none stroke-border" stroke-width="${U.regionStroke}"></rect>`);
      out.push(this.#target(`data-project="${esc(p.id)}"`, label, label,
        `<rect x="${p.x}" y="${p.y}" width="${p.w}" height="${Math.max(U.pad + p.lines * U.label, px(PIC.target_px))}" class="fill-transparent" data-hit></rect><rect x="${p.x + U.pad - U.ringPad}" y="${p.y + U.ringPad}" width="${p.w - 2 * (U.pad - U.ringPad)}" height="${labelText + (p.lines - 1) * U.label + U.ringExtra}" rx="${U.ringRx}" class="${RING}" stroke-width="${U.ringStroke}"></rect><text x="${p.x + U.pad}" y="${p.y + U.ringPad + labelText}" font-size="${labelText}" class="fill-muted-foreground">${shape(projects.get(p.id)).ls.map((s, k) => `<tspan x="${p.x + U.pad}" dy="${k ? U.label : 0}">${esc(s)}</tspan>`).join("")}</text>`, i++ === stop));
      for (const r of runsOf.get(p.id) ?? []) {
        const run = runs.get(r.key), n = run.approved, claims = new Map(run.claims.map((x) => [x.id, x]));
        const cx = r.x + U.mark / 2, cy = r.y + U.head / 2;
        const word = (domain, v) => plain(`${stateOf(domain, v).copy}.label`);
        const what = joined(run.state ? word("run", run.state) : null, run.total ? count("outcome.count", run.total, { n, total: run.total }) : run.state && !run.diagnostic ? plain("view.run.empty") : null,
          run.diagnostic ? word("diagnostic", run.diagnostic) : run.audit ? word("audit", run.audit) : null);
        // The whole head (mark, count and side mark) is the target, not only the diamond.
        const hw = Math.max(U.headW + U.ringExtra, px(PIC.target_px)), hh = Math.max(U.head + 2, px(PIC.target_px));
        const body = `<rect x="${r.x - U.ringPad}" y="${cy - hh / 2}" width="${hw}" height="${hh}" class="fill-transparent" data-hit></rect><rect x="${r.x - U.ringPad}" y="${r.y - 1}" width="${U.headW + U.ringExtra}" height="${U.head + 2}" rx="${U.ringRx}" class="${RING}" stroke-width="${U.ringStroke}"></rect>
          ${runMark(cx, cy, run.state)}
          ${run.total ? `<text x="${r.x + U.mark + U.countGap}" y="${cy + text * U.baseline}" font-size="${text}" class="${TEXT}">${esc(t("view.run.count", { n, total: run.total }))}</text>` : ""}
          ${runSideMark(r.x + U.headW - U.sideX, cy - U.sideY, run)}`;
        out.push(this.#target(`data-run="${esc(r.key)}"`, plain("view.run.label", { what }), what, body, i++ === stop));
        for (const c of claimsOf.get(r.key) ?? []) {
          const claim = claims.get(c.id), cs = stateOf("claim", claim.state), g = claimGlyph(claim);
          // An approved claim no passing audit reviewed says so: "Approved · No audit yet".
          const word = joined(plain(`${cs.copy}.label`), cs.quiet && g ? plain(`${g.copy}.label`) : null);
          const body2 = `${hit(c.cx, c.cy)}<circle cx="${c.cx}" cy="${c.cy}" r="${R + U.claimRing}" class="${RING}" stroke-width="${U.ringStroke}"></circle>${claimMark(c.cx, c.cy, claim)}`;
          const name2 = plain("view.claim.label", { what: joined(word, claim.gating ? plain("view.gating") : null), text: clip(claim.text, 120) });
          out.push(this.#target(`data-run="${esc(r.key)}" data-claim="${esc(c.id)}"`, name2, joined(word, claim.text), body2, i++ === stop));
        }
      }
    }
    const lines = [
      ...L.supports.map((s) => `<path d="${edgePath(s.points)}" class="${LINE}" stroke-width="${SUPPORTS.width}" marker-end="url(#ev-view-arrow)" data-supports="${esc(s.run)}" data-supporter="${esc(s.supporter)}" data-supported="${esc(s.supported)}"></path>`),
      ...L.attempts.map((a) => `<path d="${attemptPath(a.a, a.b, R)}" class="${LINE}" stroke-width="${ATTEMPT.width}" stroke-dasharray="${ATTEMPT.dash}" marker-end="url(#ev-view-arrow)" data-attempt="${esc(a.to)}" data-previous="${esc(a.from)}"></path>`),
    ];
    return `<svg viewBox="${-M} ${-M} ${L.width + 2 * M} ${L.height + 2 * M}" width="${(L.width + 2 * M) * scale}" height="${(L.height + 2 * M) * scale}" class="m-auto block max-w-none shrink-0" role="group" aria-label="${t("view.label")}">
      ${arrowDef("ev-view-arrow")}
      <g aria-hidden="true" class="pointer-events-none">${lines.join("")}</g>
      ${out.join("")}
    </svg>`;
  }
  populate() { this.#mark(); }
}
define("ev-view", EvView);
