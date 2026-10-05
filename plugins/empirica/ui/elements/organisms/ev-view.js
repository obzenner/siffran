import { EvElement, clip, define, esc, projectName, stateOf, t } from "../core/element.js";
import { ATTEMPT, LINE, R, RING, SUPPORTS, arrowDef, claimGlyph, claimMark, runMark, runSideMark } from "../core/marks.js";
import { rove, stopIndex } from "../core/rove.js";
import { U, viewLayout } from "../core/view-layout.js";
import { edgePath } from "../core/layout.js";

/**
 * The view (VIEW.md): every project, run and claim it is given, with their relationships, in one picture that
 * scales to fit its box (R1: nothing scrolls; with many things everything is smaller).
 * - A project is a labelled region (its folder name, else its place); its label is the project's one target (R2).
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
 * @prop {{projects:{id:string,name:?string,n:number,runs:object[]}[], empty?:string}} data - as app/filter.js returns
 *   it (runs carry `total` and `approved`); `empty` is the copy key to show when there is nothing (default view.empty)
 */
const MARK = "group cursor-pointer outline-none";
const TEXT = "fill-muted-foreground tabular-nums";
class EvView extends EvElement {
  static observedAttributes = ["project", "run", "claim"];
  #size = { width: 0, height: 0 };
  #seen = new ResizeObserver(([e]) => {
    const { width, height } = e.contentRect;
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
    const name = (p) => { const n = p.runs.length; return `${projectName(p, p.n)} · ${n === 1 ? t("view.runs.one") : t("view.runs", { n })}`; };
    const L = viewLayout(d.projects, this.#size, (p) => name(p).length);
    const runs = new Map(d.projects.flatMap((p) => p.runs.map((r) => [r.key, r])));
    const projects = new Map(d.projects.map((p) => [p.id, p]));
    const group = (xs, by) => xs.reduce((m, x) => m.set(x[by], [...(m.get(x[by]) ?? []), x]), new Map());
    const runsOf = group(L.runs, "project"), claimsOf = group(L.claims, "run");
    const count = L.projects.length + L.runs.length + L.claims.length;
    const stop = stopIndex(this, count, 0);
    let i = 0;
    const out = [];
    for (const p of L.projects) {
      const label = name(projects.get(p.id));
      out.push(`<rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="6" class="fill-none stroke-border" stroke-width="1"></rect>`);
      out.push(this.#target(`data-project="${esc(p.id)}"`, label, label,
        `<rect x="${p.x + U.pad - 3}" y="${p.y + 3}" width="${p.w - 2 * U.pad + 6}" height="${U.label - 2}" rx="3" class="${RING}" stroke-width="1.5"></rect><text x="${p.x + U.pad}" y="${p.y + U.label - 3}" font-size="${U.labelText}" class="fill-muted-foreground">${esc(label)}</text>`, i++ === stop));
      for (const r of runsOf.get(p.id) ?? []) {
        const run = runs.get(r.key), n = run.approved, claims = new Map(run.claims.map((x) => [x.id, x]));
        const cx = r.x + U.mark / 2, cy = r.y + U.head / 2;
        const word = (domain, v) => t(`${stateOf(domain, v).copy}.label`);
        const what = [run.state ? word("run", run.state) : null, run.total ? t("outcome.count", { n, total: run.total }) : run.state && !run.diagnostic ? t("view.run.empty") : null,
          run.diagnostic ? word("diagnostic", run.diagnostic) : run.audit ? word("audit", run.audit) : null].filter(Boolean).join(" · ");
        const body = `<rect x="${r.x - 3}" y="${r.y - 1}" width="${U.headW + 4}" height="${U.head + 2}" rx="3" class="${RING}" stroke-width="1.5"></rect>
          ${runMark(cx, cy, run.state)}
          ${run.total ? `<text x="${r.x + U.mark + 4}" y="${cy + 3}" font-size="${U.text}" class="${TEXT}">${esc(t("view.run.count", { n, total: run.total }))}</text>` : ""}
          ${runSideMark(r.x + U.headW - 12, cy - 5, run)}`;
        out.push(this.#target(`data-run="${esc(r.key)}"`, t("view.run.label", { what }), what, body, i++ === stop));
        for (const c of claimsOf.get(r.key) ?? []) {
          const claim = claims.get(c.id), cs = stateOf("claim", claim.state), g = claimGlyph(claim);
          // An approved claim no passing audit reviewed says so: "Approved · No audit yet".
          const word = [t(`${cs.copy}.label`), cs.quiet && g ? t(`${g.copy}.label`) : null].filter(Boolean).join(" · ");
          const body2 = `<circle cx="${c.cx}" cy="${c.cy}" r="${R + 2.5}" class="${RING}" stroke-width="1.5"></circle>${claimMark(c.cx, c.cy, claim)}`;
          const name2 = `${word}${claim.gating ? ` · ${t("view.gating")}` : ""}: ${clip(claim.text, 120)}`;
          out.push(this.#target(`data-run="${esc(r.key)}" data-claim="${esc(c.id)}"`, name2, `${word} · ${claim.text}`, body2, i++ === stop));
        }
      }
    }
    const lines = [
      ...L.supports.map((s) => `<path d="${edgePath(s.points)}" class="${LINE}" stroke-width="${SUPPORTS.width}" marker-end="url(#ev-view-arrow)" data-supports="${esc(s.run)}" data-supporter="${esc(s.supporter)}" data-supported="${esc(s.supported)}"></path>`),
      ...L.attempts.map((a) => `<path d="M${a.a.x},${a.a.y} C${a.a.x},${Math.min(a.a.y, a.b.y) - 14} ${a.b.x},${Math.min(a.a.y, a.b.y) - 14} ${a.b.x},${a.b.y}" class="${LINE}" stroke-width="${ATTEMPT.width}" stroke-dasharray="${ATTEMPT.dash}" marker-end="url(#ev-view-arrow)" data-attempt="${esc(a.to)}" data-previous="${esc(a.from)}"></path>`),
    ];
    return `<svg viewBox="-4 -4 ${L.width + 8} ${L.height + 8}" class="block size-full" preserveAspectRatio="xMidYMin meet" role="group" aria-label="${t("view.label")}">
      ${arrowDef("ev-view-arrow")}
      <g aria-hidden="true">${lines.join("")}</g>
      ${out.join("")}
    </svg>`;
  }
  populate() { this.#mark(); }
}
define("ev-view", EvView);
