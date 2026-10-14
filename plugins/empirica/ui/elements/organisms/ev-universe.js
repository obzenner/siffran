import catalogue from "../../catalogue.json" with { type: "json" };
import enums from "../../enums.json" with { type: "json" };
import { FLAG_COUNT, callouts, layout, lit as litMark, mirror, shapePath } from "../../app/universe.js";
import { attemptChain, edgePath, bounds, rimFirst, cardBox, claimGraph, fitView, footprint, flyCamera, keyBox, labelWidth, markRadius, nearest, placeCallouts, placeLabels, pulseRadius, toScreen, toWorld, viewInset } from "../../app/geometry.js";
import { EvElement, TONE, clip, count, define, esc, hostName, joined, morph, num, plain, plainCount, setStyle, stateHtml, stateOf, t } from "../core/element.js";

const glyphOf = (domain, value) => stateHtml(domain, value, "glyph");

const G = catalogue.universe;
const FRAMES = 240;          // the frame timings kept for the journeys (the last this many)
const TURN = 2 * Math.PI;    // a full circle
const RING_ARC = 0.75;       // the share of an audit-pending ring drawn, the gap turning
/** A colour `#RRGGBB` at opacity `a` (0..1), as the canvas takes it. */
const alpha = (hex, a) => `${hex}${Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, "0")}`;
/** Literal hue classes per island (E7: Tailwind scans these literals), one per `island_hues`. */
const HUE = ["text-island-1", "text-island-2", "text-island-3", "text-island-4", "text-island-5", "text-island-6", "text-island-7", "text-island-8"];
/** A run's graph at its zoom: rings, nodes (and each node's link box), the run (with its focus ring) and the labels, in px. */
const GRAPH = { ring: G.orbit_ring, node: G.claim_px, hit: G.claim_hit_px, gap: G.claim_gap_px, run: G.run_px + G.focus_px, label_h: G.claim_label_px, cap: G.loop_cap };
/** A shape of `domain` value `value` as a small SVG `r` across half, in its tone (the key, the card and a run's graph). */
const shapeSvg = (domain, value, r, extra = "") => { const s = stateOf(domain, value), q = r + 3;
  return `<svg class="shrink-0 ${TONE[s.tone]?.fg ?? TONE.neutral.fg}" width="${2 * q}" height="${2 * q}" viewBox="${-q} ${-q} ${2 * q} ${2 * q}" aria-hidden="true" focusable="false"><path d="${shapePath(s.shape, r)}" fill="currentColor" fill-rule="evenodd"></path>${extra}</svg>`; };

/**
 * The Universe (UI.md, The Universe): every run a mark on one canvas, each project an island, with an HTML overlay for what
 * a person reads or acts on (the breadcrumb, the needs pill, the period and highlight chips, the island names, the
 * needs-a-human callouts, a run's claims in orbit, the key) and an accessible mirror listing the canvas's items as links.
 * Semantic zoom: the store (each mark a small dot of its standing's colour), a project (`zoom`: every mark its standing's
 * shape), a run (`run`: its claims on their support edges, its proof in the sheet). Every place, size and box comes from
 * app/geometry.js; this element only renders them. The camera flies at most `--duration-fly` between zooms, instantly under
 * reduced motion; a run that needs a human pulses and an audit-pending one turns its ring, and nothing else moves. It
 * decides nothing about a run and counts nothing: every number is the observer's.
 * @element ev-universe
 * @prop {{summary:object, place:object, sheet:?object, goals:Map<string,string>, since:?number, nowMs:number, href:Function, list:Function, needsAll:string, empty:string}} data
 * @fires ev-navigate - detail: the href of a place the canvas was clicked or keyed to
 */
class EvUniverse extends EvElement {
  #laid = new WeakMap(); #cam = null; #flight = null; #raf = 0; #focus = null; #hover = null; #colours = null; #keyOpen = false;
  #size = { w: 0, h: 0, dpr: 1 }; #called = new Set(); #names = []; #at = new WeakMap(); #placed = null; #pts = [];
  timings = [];
  constructor() {
    super();
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");
    const motion = () => { this.dataset.motion = reduced.matches ? "reduced" : "full"; this.#frame(); };
    reduced.addEventListener("change", motion); motion();
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { this.#colours = null; this.#frame(); });
    new ResizeObserver(() => this.#resize()).observe(this);
    this.addEventListener("click", (e) => this.#click(e));
    this.addEventListener("pointermove", (e) => this.#move(e));
    this.addEventListener("keydown", (e) => this.#key(e));
    this.addEventListener("wheel", (e) => this.#wheel(e), { passive: false });
  }

  get #reduced() { return this.dataset.motion === "reduced"; }
  get #level() { const p = this.data?.place; return p?.run ? "run" : p?.zoom ? "project" : "store"; }
  /** The canvas as geometry sees it: its size and the part no bar, key or card covers. */
  get #view() { return { w: this.#size.w, h: this.#size.h, inset: viewInset(this.#keyOpen, G, this.#level === "project" && !!this.#zoomed()) }; }
  /** The layout of this summary: a pure function of it (app/universe.js layout), computed once per summary. */
  get #L() { const s = this.data.summary; let l = this.#laid.get(s); if (!l) { l = layout(s, G); this.#laid.set(s, l); } return l; }
  /** The island of project `id` (null when the summary has no such project). */
  #island(id) { const k = this.data.summary.projects.findIndex((p) => p.id === id); return k < 0 ? null : this.#L.islands[k]; }
  #zoomed() { return this.data.summary.projects.find((p) => p.id === this.data.place.zoom) ?? null; }
  /** Mark i's world place, or null while the observer has not placed it. */
  #mark(i) { const L = this.#L; return Number.isNaN(L.x[i]) ? null : { x: L.x[i], y: L.y[i] }; }
  #screen(x, y) { return toScreen(this.#cam, this.#view, x, y); }
  #rows() { const s = this.data.summary; return s.marks.standings.filter((k) => (s.facets.standing[k] ?? 0) > 0); }

  update() {
    const d = this.data;
    if (!d) { this.innerHTML = ""; return; }
    if (!this.querySelector("canvas")) {
      this.innerHTML = `<div class="relative h-full min-h-0 overflow-hidden" data-stage>
        <canvas class="absolute inset-0 size-full" tabindex="0" role="application" data-canvas aria-label="${t("universe.label")}"></canvas>
        <div class="pointer-events-none absolute inset-0" data-overlay></div>
        <div class="pointer-events-none absolute inset-x-0 top-0 flex items-center gap-s px-s" data-top></div>
        <div class="pointer-events-none absolute inset-x-0 bottom-0 flex items-center gap-xs px-s" data-bottom></div>
        <div class="pointer-events-none absolute" data-key></div>
        <p class="sr-only" aria-live="polite" data-said></p>
        <nav class="sr-only" aria-label="${t("universe.mirror")}" data-mirror></nav>
      </div>`;
      this.querySelector("[data-top]").style.height = `${G.hud_top_px}px`;
      this.querySelector("[data-bottom]").style.height = `${G.hud_bottom_px}px`;
      this.addEventListener("click", (e) => { const k = e.target.closest?.("[data-key-toggle]"); if (k) { this.#keyOpen = !this.#keyOpen; this.#paintKey(); this.#paintBottom(); this.#target(); this.#frame(); } });
      this.#resize();
    }
    this.#target();
    this.#paintTop(); this.#paintBottom(); this.#paintKey(); this.#paintMirror();
    this.#frame();
  }

  // ---- camera -----------------------------------------------------------------------------------------------------------
  /** Where the camera should be for the place: the whole store (filled), one island, or one run (its island's scale, on the
   * run). */
  #want() {
    const d = this.data, view = this.#view, m = d.summary.marks;
    const i = this.#level === "run" ? this.#index(m, d.place.run) : -1, at = i >= 0 ? this.#mark(i) : null;
    if (at) {
      const own = this.#L.islands[m.project[i]];
      return { ...fitView(bounds([footprint(own.x, own.y, own.r, G)]), view, G.island_scale_max, G), x: at.x, y: at.y };
    }
    const island = d.place.zoom ? this.#island(d.place.zoom) : null;
    if (island) return fitView(bounds([footprint(island.x, island.y, island.r, G)]), view, G.island_scale_max, G);
    return fitView(this.#L.bounds, view, G.island_scale_max, G);  // a few small islands fill the view, never past an island's zoom
  }
  /** Put the camera where the place wants it. It flies only when the place changed (a navigation) and motion is allowed; a
   * first paint (a load or a reload: the URL's place), a new summary, a resize or the key's toggle puts it there at once. */
  #target() {
    if (!this.#size.w) return;
    const to = this.#want(), from = this.#cam, p = this.data.place, here = `${p.zoom ?? ""} ${p.run ?? ""}`;
    const moved = this.#placed !== null && this.#placed !== here;
    this.#placed = here;
    if (!moved && this.#flight) { this.#flight.to = to; return; }
    if (!from || this.#reduced || !moved) { this.#cam = to; this.#flight = null; return; }
    if (Math.abs(from.x - to.x) + Math.abs(from.y - to.y) < 1e-6 && Math.abs(from.s - to.s) < 1e-9) return;
    this.#flight = { from, to, t0: performance.now(), ms: this.#ms("--duration-fly") };
    this.dataset.flights = String(Number(this.dataset.flights ?? 0) + 1);  // how many flights began (a state, not a time)
  }
  #ms(name) { return parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name)) || 0; }
  #resize() {
    const c = this.querySelector("canvas");
    if (!c) return;
    const r = c.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
    if (!r.width || !r.height) return;
    this.#size = { w: r.width, h: r.height, dpr };
    c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr);
    this.#cam = null; this.#target();
    this.#paintKey();
    this.#frame();
  }

  // ---- drawing ----------------------------------------------------------------------------------------------------------
  /** A mark's index by run id, one map per summary's marks (the callouts read every need, not only the first few). */
  #index(m, id) {
    let at = this.#at.get(m);
    if (!at) { at = new Map(m.id.map((x, i) => [x, i])); this.#at.set(m, at); }
    return at.get(id) ?? -1;
  }

  #frame() { if (!this.#raf && this.data && this.#size.w) this.#raf = requestAnimationFrame((now) => { this.#raf = 0; this.#draw(now); }); }
  #palette() {
    if (this.#colours) return this.#colours;
    const css = getComputedStyle(document.documentElement), v = (n) => css.getPropertyValue(n).trim();
    const tones = Object.fromEntries(catalogue.tones.map((k) => [k, v(`--tone-${k}`)]));
    const hues = Array.from({ length: G.island_hues }, (_, k) => v(`--island-${k + 1}`));
    this.#colours = { tones, hues, ring: v("--ring"), bg: v("--background") };
    return this.#colours;
  }
  /** The islands on screen: each project's disc, in the summary's order. */
  #discs() { return this.#L.islands.map((c) => { const q = this.#screen(c.x, c.y); return { sx: q.sx, sy: q.sy, r: c.r * this.#cam.s }; }); }
  /** The zoomed island's marks on screen as circles (a callout keeps off them while it can). */
  #markDiscs() {
    const d = this.data, m = d.summary.marks, k = d.summary.projects.findIndex((p) => p.id === d.place.zoom), r = markRadius("project", this.#cam.s, G) + G.focus_px, out = [];
    for (let i = 0; i < m.id.length; i++) { const at = m.project[i] === k ? this.#mark(i) : null; if (at) out.push({ ...this.#screen(at.x, at.y), r }); }
    return out;
  }
  #draw(now) {
    const t0 = performance.now(), d = this.data, ctx = this.querySelector("canvas").getContext("2d");
    if (this.#flight) {
      const f = this.#flight, k = (now - f.t0) / Math.max(1, f.ms);
      this.#cam = flyCamera(f.from, f.to, k);
      if (k >= 1) this.#flight = null;
    }
    this.#paintOverlay();
    const { w, h, dpr } = this.#size, P = this.#palette(), m = d.summary.marks, level = this.#level, flying = this.#flight !== null, L = this.#L;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
    const tone = m.standings.map((s) => P.tones[stateOf("standing", s).tone] ?? P.tones.neutral);
    const zoomed = d.summary.projects.findIndex((p) => p.id === d.place.zoom);
    const run = level === "run" ? this.#index(m, d.place.run) : -1;
    const discs = this.#discs();
    // islands: a soft disc of the island's hue fading past its shore, and the shore dashed; a run's zoom fades them all to a
    // background (the run's graph stands alone)
    L.islands.forEach((c, k) => {
      const { sx, sy, r } = discs[k], hue = P.hues[c.hue];
      ctx.globalAlpha = level === "run" ? G.graph_dim : level !== "store" && zoomed >= 0 && zoomed !== k ? G.dim : 1;
      const glow = ctx.createRadialGradient(sx, sy, r * G.glow_inner, sx, sy, r * G.glow_outer);
      glow.addColorStop(0, alpha(hue, G.glow_alpha)); glow.addColorStop(1, alpha(hue, 0));
      ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(sx, sy, r * G.glow_outer, 0, TURN); ctx.fill();
      ctx.strokeStyle = alpha(hue, G.shore_alpha); ctx.lineWidth = G.shore_px; ctx.setLineDash(G.shore_dash);
      ctx.beginPath(); ctx.arc(sx, sy, r, 0, TURN); ctx.stroke(); ctx.setLineDash([]);
    });
    // which marks are lit (the highlight's flag within the period, the observer's window flags; else the period), and
    // which are drawn (placed and on screen)
    const lit = (i) => litMark(m, i, d.place.hl, d.since);
    const asDots = level === "store" || (flying && this.#cam.s < G.island_scale_max / 2);
    const r = markRadius(asDots ? "store" : "project", this.#cam.s, G);
    const pts = [];
    for (let i = 0; i < m.id.length; i++) {
      const { sx, sy } = this.#screen(L.x[i], L.y[i]);
      if (sx >= -r && sy >= -r && sx <= w + r && sy <= h + r) pts.push({ i, sx, sy });
    }
    this.#pts = pts;
    // live state first, under the marks (a ring never hides one): a run that needs a human pulses, still under reduced motion
    let moving = false;
    const pulse = this.#reduced ? 0 : (now % this.#ms("--duration-pulse")) / this.#ms("--duration-pulse");
    for (const { i, sx, sy } of level === "run" ? [] : pts) {
      if (m.standings[m.standing[i]] !== "needs_human" || (level === "store" ? !this.#called.has(m.id[i]) : m.project[i] !== zoomed)) continue;
      const reach = pulseRadius({ sx, sy }, r, this.#names, G);
      ctx.strokeStyle = tone[m.standing[i]]; ctx.lineWidth = G.stroke_px; ctx.globalAlpha = 1 - pulse;
      ctx.beginPath(); ctx.arc(sx, sy, r + (reach - r) * (this.#reduced ? 1 : pulse), 0, TURN); ctx.stroke();
      moving ||= !this.#reduced;
    }
    ctx.globalAlpha = 1;
    if (asDots) {
      // the store: small dense dots of each standing's colour
      for (const { i, sx, sy } of pts) {
        ctx.globalAlpha = lit(i) ? 1 : G.dim;
        ctx.fillStyle = tone[m.standing[i]]; ctx.beginPath(); ctx.arc(sx, sy, r, 0, TURN); ctx.fill();
      }
    } else {
      // zoomed in: each mark its standing's shape, outlined; a failed audit or refutation dotted red; on a run's zoom every
      // other mark a faint texture and the run large in the centre
      const cache = m.standings.map((s) => new Path2D(shapePath(stateOf("standing", s).shape, r)));
      const caught = (1 << m.flag_bits.indexOf("failed_audit")) | (1 << m.flag_bits.indexOf("refuted"));
      ctx.lineJoin = "round";
      for (const { i, sx, sy } of pts) {
        if (i === run) continue;
        const on = level === "project" && lit(i) && m.project[i] === zoomed;
        ctx.globalAlpha = level === "run" ? G.graph_dim : on ? 1 : G.dim;
        ctx.fillStyle = tone[m.standing[i]]; ctx.strokeStyle = P.bg; ctx.lineWidth = G.stroke_px;
        ctx.setTransform(dpr, 0, 0, dpr, dpr * sx, dpr * sy); ctx.fill(cache[m.standing[i]]); ctx.stroke(cache[m.standing[i]]); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        if (level !== "run" && (m.flags[i] & caught)) { ctx.fillStyle = P.tones.negative; ctx.beginPath(); ctx.arc(sx + r, sy - r, G.caught_share * r, 0, TURN); ctx.fill(); }
        if (on && m.standings[m.standing[i]] === "audit_pending") moving = this.#ring(ctx, now, sx, sy, r, tone[m.standing[i]]) || moving;
      }
      const at = run >= 0 ? pts.find((p) => p.i === run) : null;
      if (at) {
        const k = m.standing[run], big = new Path2D(shapePath(stateOf("standing", m.standings[k]).shape, G.run_px));
        ctx.globalAlpha = 1; ctx.fillStyle = tone[k]; ctx.strokeStyle = P.bg; ctx.lineWidth = G.stroke_px;
        ctx.setTransform(dpr, 0, 0, dpr, dpr * at.sx, dpr * at.sy); ctx.fill(big); ctx.stroke(big); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        if (m.flags[run] & caught) { ctx.fillStyle = P.tones.negative; ctx.beginPath(); ctx.arc(at.sx + G.run_px, at.sy - G.run_px, G.caught_share * G.run_px, 0, TURN); ctx.fill(); }
        if (m.standings[k] === "audit_pending") moving = this.#ring(ctx, now, at.sx, at.sy, G.run_px, tone[k]) || moving;
      }
    }
    ctx.globalAlpha = 1;
    this.dataset.markPx = (2 * r).toFixed(1);
    // the store's box on screen and the scale, as the journeys check fit-to-view (every island, its name's band included)
    const b = L.bounds, a = this.#screen(b.x0, b.y0), z = this.#screen(b.x1, b.y1);
    this.dataset.fit = `${[a.sx, a.sy, z.sx, z.sy].map((v) => v.toFixed(1)).join(" ")} ${this.#cam.s.toFixed(4)}`;
    this.#paintFocus(ctx, P);
    this.timings.push({ ms: performance.now() - t0, flying, marks: pts.length });
    if (this.timings.length > FRAMES) this.timings.shift();
    if (this.#flight || moving) this.#frame();
  }
  /** An audit-pending mark's turning ring (still under reduced motion); whether it asks for another frame. */
  #ring(ctx, now, sx, sy, r, colour) {
    const a = this.#reduced ? 0 : (TURN * (now % this.#ms("--duration-ring"))) / this.#ms("--duration-ring");
    ctx.globalAlpha = 1; ctx.strokeStyle = colour; ctx.lineWidth = G.stroke_px;
    ctx.beginPath(); ctx.arc(sx, sy, r + G.focus_px, a, a + TURN * RING_ARC); ctx.stroke();
    return !this.#reduced;
  }
  /** What the last frame drew, for the journeys' reload check: the store's box and scale, and each mark drawn, by id and
   * screen place (rounded to a tenth of a px). */
  drawn() {
    const m = this.data.summary.marks;
    return { fit: this.dataset.fit, marks: this.#pts.map((p) => `${m.id[p.i]} ${p.sx.toFixed(1)} ${p.sy.toFixed(1)}`),
      islands: this.#discs().map((c, k) => `${this.data.summary.projects[k].id} ${c.sx.toFixed(1)} ${c.sy.toFixed(1)} ${c.r.toFixed(1)}`) };
  }

  // ---- the overlay: island names, callouts, the project card, a run's graph -----------------------------------------------
  #paintOverlay() {
    const d = this.data, level = this.#level, m = d.summary.marks, view = this.#view, out = [];
    if (!m.id.length) out.push(`<p class="absolute inset-x-0 top-1/2 text-center text-s text-muted-foreground" data-empty>${t(d.empty)}</p>`);
    const fixed = this.#keyOpen ? [keyBox(this.#rows().length, view, G)] : [];
    const project = level === "project" ? this.#zoomed() : null;
    if (project) { const { html, box: b } = this.#card(project, view); out.push(html); fixed.push(b); }
    const discs = this.#discs();
    // island names: each a link to the work view of its project (the canvas's island is the zoom), in its own hue, with its
    // runs and how many need a human, the observer's counts
    const names = [];
    if (level !== "run") {
      const named = d.summary.projects.map((p, k) => ({ p, k })).filter(({ p }) => level === "store" || p.id === d.place.zoom).slice(0, G.labels);
      const words = named.map(({ p }) => {
        const runs = plainCount("universe.runs", p.runs, { n: num(p.runs) }), need = p.needs ? plainCount("universe.need_you", p.needs, { n: num(p.needs) }) : "";
        // the line is muted; only K, the count that needs a human, is in its tone
        const [pre, post] = plainCount("universe.need_you", p.needs, { n: "\u0000" }).split("\u0000");
        return { name: p.label, sub: [runs, need].filter(Boolean).join(plain("list.join")), html: `${esc(runs)}${need ? `${t("list.join")}${esc(pre)}<span class="font-medium text-tone-human">${esc(num(p.needs))}</span>${esc(post)}` : ""}` };
      });
      const boxes = placeLabels(named.map(({ k }) => discs[k]), words.map((x) => labelWidth(x.name.length, x.sub.length, G)), fixed, view, G);
      named.forEach(({ p, k }, n) => {
        const b = boxes[n];
        names.push(b);
        out.push(`<a class="pointer-events-auto absolute flex flex-col items-center text-center leading-none whitespace-nowrap hover:underline" href="${esc(d.list(p.id))}" data-island-label="${esc(p.id)}" title="${esc(`${p.label}${plain("list.join")}${words[n].sub}`)}" ${box(b)}><span class="text-3xs font-medium ${HUE[this.#L.islands[k].hue]}">${esc(words[n].name)}</span><span class="mt-3xs text-3xs tabular-nums text-muted-foreground">${words[n].html}</span></a>`);
      });
    }
    this.#names = names;  // the pulse rings stop short of them (#draw)
    // callouts: the runs that need a human whose marks are on screen, at most the cap, then how many more the store has; at
    // the store each keeps off every island, zoomed on one off every other island
    if (level !== "run") {
      const where = new Map();
      const visible = (n) => {
        const i = this.#index(m, n.id), at = i < 0 ? null : this.#mark(i);
        if (!at || (level === "project" && d.summary.projects[m.project[i]].id !== d.place.zoom)) return false;
        const s = this.#screen(at.x, at.y);
        where.set(n.id, { ...s, island: m.project[i] });
        return s.sx >= view.inset.left && s.sy >= view.inset.top && s.sx <= view.w - view.inset.right && s.sy <= view.h - view.inset.bottom;
      };
      // the runs that need a human here: the store's at the store, the island's on it (the observer's counts)
      const total = level === "store" ? d.summary.facets.standing.needs_human ?? 0 : this.#zoomed()?.needs ?? 0;
      // every visible need in the callouts' order (the islands in turn), placed until G.callouts fit: a need with no room
      // within its leader's reach gives its turn to the next
      const { shown } = callouts(d.summary.needs, visible, total, d.summary.needs.length, (n) => m.project[this.#index(m, n.id)]);
      const placed = placeCallouts(rimFirst(shown.map((n) => ({ id: n.id, ...where.get(n.id) })), discs), [...names, ...fixed], discs, view, G,
        { max: G.callouts, ...(level === "project" ? { soft: this.#markDiscs() } : {}) });
      this.#called = new Set(placed.map((p) => p.id));
      const byId = new Map(shown.map((n) => [n.id, n]));
      if (placed.length) out.push(`<svg class="absolute inset-0 size-full" aria-hidden="true" focusable="false" data-leaders>${placed.map((p) => `<line x1="${p.ax}" y1="${p.ay}" x2="${p.lx}" y2="${p.ly}" class="stroke-tone-human" stroke-width="${G.stroke_px}"></line>`).join("")}</svg>`);
      for (const p of placed) {
        const n = byId.get(p.id);
        out.push(`<a class="pointer-events-auto absolute flex items-center gap-2xs overflow-hidden rounded-m border border-tone-human bg-background px-xs text-2xs whitespace-nowrap hover:underline" href="${esc(d.href({ run: n.id, claim: null }))}" data-callout="${esc(n.id)}" ${box(p)}>${glyphOf("standing", "needs_human")}<span class="shrink-0 font-semibold">${t(`ask.${n.ask}`)}</span><span class="min-w-0 truncate text-muted-foreground">${hostName(n.host)}</span></a>`);
      }
      this.#paintMore(Math.max(0, total - placed.length));
    } else this.#paintMore(0);
    if (level === "run") out.push(this.#scene(view));
    if (this.#hover !== null && level !== "store") {
      const i = this.#hover, at = this.#mark(i), goal = d.goals.get(m.id[i]), r = markRadius("project", this.#cam.s, G);
      if (at) {
        const s = this.#screen(at.x, at.y);
        out.push(`<p class="absolute rounded-m border border-border bg-background px-xs py-3xs text-xs shadow-1" data-tip data-x="${Math.round(s.sx + 2 * r)}" data-y="${Math.round(s.sy + 2 * r)}">${joined(goal ? esc(clip(goal, 60)) : "", t(`${stateOf("standing", m.standings[m.standing[i]]).copy}.label`), m.attempt[i] > 1 ? t("row.attempt.short", { at: m.attempt[i] }) : "")}</p>`);
      }
    }
    place(this.querySelector("[data-overlay]"), out.join(""));
  }

  /** An island's card (C's project card), top left: its name in its hue, its repository, its runs, and its runs by standing,
   * each the observer's count (`projects[].standings`) in the contract's standing order. */
  #card(p, view) {
    const k = this.data.summary.projects.indexOf(p), rows = this.data.summary.marks.standings.filter((s) => p.standings?.[s]);
    const b = cardBox(rows.length, view, G);
    const items = rows.map((s) => `<li class="flex items-center gap-xs" data-card-row="${s}">${shapeSvg("standing", s, 5)}<span class="min-w-0 flex-1 truncate">${t(`${stateOf("standing", s).copy}.label`)}</span><span class="tabular-nums font-medium">${num(p.standings[s])}</span></li>`).join("");
    const runs = plain(p.runs === 1 ? "universe.runs.one" : "universe.runs", { n: num(p.runs) });
    const html = `<section class="pointer-events-auto absolute flex flex-col overflow-hidden rounded-l border border-border bg-background px-s py-xs text-2xs text-muted-foreground" aria-label="${esc(p.label)}" data-card="${esc(p.id)}" ${box(b)}>
      <span class="truncate text-xs font-medium ${HUE[this.#L.islands[k].hue]}" title="${esc(p.label)}">${esc(p.label)}</span>
      <span class="truncate text-muted-foreground" title="${esc([p.repo, runs].filter(Boolean).join(plain("list.join")))}">${esc([p.repo, runs].filter(Boolean).join(plain("list.join")))}</span>
      <ul class="mt-xs flex flex-col border-t border-border pt-2xs">${items}</ul></section>`;
    return { html, box: b };
  }

  /** A run's zoom as its own scene (C's run zoom): the run large in the centre with its standing's word under it, its claims
   * in orbit (app/geometry.js claimGraph: rings by depth, each claim its state's shape and colour, labelled without
   * overlap, the SupportedBy edges as lines, a needs-decision or needs-experiment claim ringed dashed), and its attempts as a
   * linked chain under it. Each node and attempt is a link (a claim into the sheet, an attempt to its run). */
  #scene(view) {
    const d = this.data, m = d.summary.marks, i = this.#index(m, d.place.run), at = i < 0 ? null : this.#mark(i);
    if (!at) return "";
    const c = this.#screen(at.x, at.y), standing = m.standings[m.standing[i]], st = stateOf("standing", standing);
    const said = plain(`${st.copy}.label`), ww = said.length * G.claim_char_px + G.claim_pad_px;
    const word = { x: -ww / 2, y: G.run_px + G.claim_gap_px, w: ww, h: G.word_px };
    const row = d.sheet?.row, claims = (row?.claims ?? []).slice(0, G.claims_cap);
    const text = new Map(claims.map((cl) => [cl.id, `${cl.id} · ${clip(cl.text, G.claim_chars)}`]));
    const widths = new Map([...text].map(([id, x]) => [id, x.length * G.claim_char_px + G.claim_pad_px]));
    const inside = { x: view.inset.left - c.sx, y: view.inset.top - c.sy, w: view.w - view.inset.left - view.inset.right, h: view.h - view.inset.top - view.inset.bottom };
    const graph = claimGraph(row?.root, claims, row?.edges, widths, GRAPH, [word], inside);
    const attempts = d.sheet?.attempts ?? [];
    const below = Math.max(word.y + word.h + G.claim_gap_px + G.chain_px, (graph.rings.at(-1) ?? 0) + G.claim_px + G.claim_label_px + 2 * G.claim_gap_px + G.chain_px);
    const chain = attempts.length > 1 ? attemptChain(attempts.length, below, G.chain_step_px) : [];
    const xy = (p) => ({ x: c.sx + p.x, y: c.sy + p.y }), node = (id) => xy(graph.at.get(id));
    const line = (a, b, cls) => `<line x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}" class="${cls}" stroke-width="${G.stroke_px}"></line>`;
    // an edge is its claims' line, bent round the run's disc where the straight one would cross it (edgePath)
    const path = (e) => `<polyline data-edge="${esc(e.from)} ${esc(e.to)}" points="${edgePath(graph, e).map((p) => `${(c.sx + p.x).toFixed(1)},${(c.sy + p.y).toFixed(1)}`).join(" ")}" fill="none" class="stroke-border" stroke-width="${G.stroke_px}"></polyline>`;
    const lines = [...graph.edges.map(path),
      graph.root ? line({ x: c.sx, y: c.sy }, node(graph.root), "stroke-border") : "",
      ...chain.slice(1).map((p, n) => line(xy(chain[n]), xy(p), "stroke-muted-foreground"))].join("");
    const out = [`<svg class="absolute inset-0 size-full" aria-hidden="true" focusable="false" data-graph>${lines}</svg>`,
      `<p class="absolute text-center text-xs font-medium tracking-wide uppercase leading-none ${TONE[st.tone]?.fg ?? TONE.neutral.fg}" data-run-word ${box({ ...word, x: c.sx + word.x, y: c.sy + word.y })}>${esc(said)}</p>`];
    const r = G.claim_hit_px;
    for (const cl of claims) {
      const p = node(cl.id), sel = d.place.claim === cl.id, s = stateOf("claim", cl.state), marked = cl.kind !== "ordinary";
      const ring = marked ? `<circle r="${G.claim_px + 1}" fill="none" stroke="currentColor" stroke-dasharray="${G.shore_dash.join(" ")}"></circle>` : "";
      out.push(`<a class="pointer-events-auto absolute flex items-center justify-center rounded-full ${sel ? "ring-2 ring-ring" : ""}" href="${esc(d.href({ claim: sel ? null : cl.id }))}" data-orbit="${esc(cl.id)}" ${sel ? 'aria-current="true"' : ""} data-cx="${Math.round(p.x)}" data-cy="${Math.round(p.y)}" ${box({ x: p.x - r, y: p.y - r, w: 2 * r, h: 2 * r })} title="${esc(joined(cl.text, plain(`${s.copy}.label`), marked ? plain(`kind.${cl.kind}`) : ""))}">${shapeSvg("claim", cl.state, G.claim_px - 1, ring)}<span class="sr-only">${esc(cl.text)} ${t(`${s.copy}.label`)}${marked ? ` ${t(`kind.${cl.kind}`)}` : ""}</span></a>`);
      const lb = graph.labels.get(cl.id);
      if (lb) out.push(`<a class="pointer-events-auto absolute truncate text-xs font-normal leading-none whitespace-nowrap text-muted-foreground hover:underline" href="${esc(d.href({ claim: sel ? null : cl.id }))}" tabindex="-1" aria-hidden="true" data-orbit-label="${esc(cl.id)}" title="${esc(cl.text)}" ${box({ ...lb, x: c.sx + lb.x, y: c.sy + lb.y })}>${esc(text.get(cl.id))}</a>`);
    }
    attempts.forEach((a, n) => {
      if (!chain.length) return;
      const p = xy(chain[n]), here = a.id === d.place.run, q = G.chain_px + 2;
      out.push(`<a class="pointer-events-auto absolute flex items-center justify-center rounded-full ${here ? "ring-2 ring-ring" : ""}" href="${esc(d.href({ run: a.id, claim: null }))}" data-chain="${esc(a.id)}" ${here ? 'aria-current="true"' : ""} ${box({ x: p.x - q, y: p.y - q, w: 2 * q, h: 2 * q })} title="${esc(joined(plain("row.attempt.short", { at: a.generation }), plain(`${stateOf("standing", a.standing).copy}.label`)))}">${shapeSvg("standing", a.standing, G.chain_px - 1)}<span class="sr-only">${t("row.attempt.short", { at: a.generation })} ${t(`${stateOf("standing", a.standing).copy}.label`)}</span></a>`);
    });
    return out.join("");
  }
  /** "+N more need a human", centred in the top bar: the observer's count less the callouts drawn. */
  #paintMore(more) {
    const slot = this.querySelector("[data-more-slot]");
    if (!slot) return;
    const html = more > 0 ? `<a class="pointer-events-auto rounded-m border border-tone-human bg-background px-xs py-3xs text-2xs font-medium text-tone-human hover:underline" href="${esc(this.data.needsAll)}" data-more-needs data-fixed>${count("universe.more", more, { n: num(more) })}</a>` : "";
    morph(slot, html);
  }

  /** The keyboard's ring around the focused item, while the canvas has focus. */
  #paintFocus(ctx, P) {
    const c = this.querySelector("canvas");
    if (document.activeElement !== c || !this.#focus) return;
    const it = this.#keyItems().find((i) => i.id === this.#focus);
    if (!it) return;
    ctx.strokeStyle = P.ring; ctx.lineWidth = G.focus_line_px; ctx.globalAlpha = 1;
    ctx.beginPath(); ctx.arc(it.x, it.y, it.r + G.focus_px, 0, TURN); ctx.stroke();
  }

  // ---- the bars and the key ------------------------------------------------------------------------------------------------
  /** The top bar: where on the map (left), how many more need a human (centre), the needs pill (right). */
  #paintTop() {
    const d = this.data, s = d.summary, level = this.#level, i = level === "run" ? this.#index(s.marks, d.place.run) : -1;
    const project = s.projects.find((p) => p.id === (i >= 0 ? s.projects[s.marks.project[i]].id : d.place.zoom));
    // one line: the store's crumb whole, the project's and the run's truncated, the whole path in the title
    const crumb = [`<a class="shrink-0 whitespace-nowrap hover:underline" href="${esc(d.href({ zoom: null, run: null, claim: null }))}" data-crumb="store">${count("universe.all", s.coverage.runs, { n: num(s.coverage.runs) })}</a>`];
    const path = [plainCount("universe.all", s.coverage.runs, { n: num(s.coverage.runs) })];
    if (project) { crumb.push(`<a class="min-w-0 truncate whitespace-nowrap hover:underline" href="${esc(d.href({ zoom: project.id, run: null, claim: null }))}" data-crumb="project">${esc(project.label)}</a>`); path.push(project.label); }
    if (level === "run") { const goal = d.sheet?.row?.goal ?? d.place.run; crumb.push(`<span class="min-w-0 truncate whitespace-nowrap text-muted-foreground" data-crumb="run">${esc(clip(goal, 48))}</span>`); path.push(goal); }
    const needs = s.facets.standing.needs_human ?? 0;
    const pill = needs ? `<a class="pointer-events-auto rounded-full bg-tone-human px-s py-2xs text-2xs font-medium text-background hover:underline" href="${esc(d.needsAll)}" data-needs-pill>${count("universe.pill", needs, { n: num(needs) })}</a>` : "";
    morph(this.querySelector("[data-top]"), `<div class="flex min-w-0 flex-1"><nav class="pointer-events-auto flex min-w-0 items-center gap-xs overflow-hidden rounded-m border border-border bg-background px-xs py-3xs text-2xs whitespace-nowrap" aria-label="${t("universe.crumbs")}" title="${esc(path.join(" › "))}" data-crumbs>${crumb.join(`<span class="shrink-0 text-muted-foreground" aria-hidden="true">›</span>`)}</nav></div>
      <div class="flex flex-1 justify-center" data-more-slot></div><div class="flex flex-1 justify-end">${pill}</div>`);
  }
  /** The bottom bar: the period, then the trophy chips (each the observer's count, lighting its runs), and the key's toggle. */
  #paintBottom() {
    const d = this.data, s = d.summary, a = s.aggregates;
    const chip = "pointer-events-auto flex h-xl min-w-0 items-center gap-xs rounded-m border px-s text-2xs text-muted-foreground";  // a short bar ellipsizes the words, each whole in its title
    const periods = catalogue.periods.map((k) => {
      const on = d.place.period === k && d.place.since === null;
      return `<a class="pointer-events-auto shrink-0 whitespace-nowrap rounded-s px-xs py-3xs ${on ? "bg-muted text-foreground" : "hover:bg-muted"}" href="${esc(d.href({ period: k, since: null }))}" ${on ? 'aria-current="true"' : ""} data-period="${k}">${t(`period.${k}`)}</a>`;
    }).join("");
    const chips = enums.flag.map((f) => {
      const on = d.place.hl === f;
      return `<a class="${chip} ${on ? "border-ring bg-background text-foreground" : "border-border bg-background hover:bg-muted"}" href="${esc(d.href({ hl: on ? null : f }))}" aria-pressed="${on}" data-hl="${f}"><span class="shrink-0 text-xs font-medium tabular-nums leading-none text-foreground">${num(FLAG_COUNT[f](a))}</span><span class="min-w-0 truncate leading-none" title="${count(`chip.flag.${f}`, FLAG_COUNT[f](a))}">${count(`chip.flag.${f}`, FLAG_COUNT[f](a))}</span></a>`;
    }).join("");
    const toggle = this.#keyOpen ? "" : `<button type="button" class="${chip} ml-auto border-border bg-background hover:bg-muted" data-key-toggle>${t("universe.key.show")}</button>`;
    morph(this.querySelector("[data-bottom]"), `<nav class="pointer-events-auto flex h-xl shrink-0 items-center gap-3xs rounded-m border border-border bg-background px-3xs text-2xs text-muted-foreground" aria-label="${t("universe.chips")}" data-chips>${periods}</nav>${chips}${toggle}`);
  }
  /** The key: each standing's shape and word with the observer's count, in a small card the person may hide. */
  #paintKey() {
    const el = this.querySelector("[data-key]");
    if (!el || !this.data) return;
    if (!this.#keyOpen || !this.#size.w) { morph(el, ""); return; }
    const s = this.data.summary, r = 5, rows = this.#rows();
    const b = keyBox(rows.length, this.#view, G);
    const items = rows.map((k) => {
      const st = stateOf("standing", k);
      return `<li class="flex items-center gap-xs" data-key-row><svg class="size-s shrink-0 ${TONE[st.tone]?.fg ?? TONE.neutral.fg}" viewBox="${-2 * r} ${-2 * r} ${4 * r} ${4 * r}" aria-hidden="true" focusable="false"><path d="${shapePath(st.shape, r)}" fill="currentColor"></path></svg><span class="min-w-0 flex-1 truncate">${t(`${st.copy}.label`)}</span><span class="tabular-nums text-muted-foreground">${num(s.facets.standing[k])}</span></li>`;
    }).join("");
    morph(el, `<section class="pointer-events-auto flex size-full flex-col rounded-l border border-border bg-background px-s py-xs text-2xs text-muted-foreground" aria-label="${t("universe.key")}" data-legend>
      <div class="flex items-center justify-between font-medium text-foreground"><span>${t("universe.key")}</span><button type="button" class="rounded-s px-2xs text-muted-foreground hover:bg-muted" aria-label="${t("universe.key.hide")}" title="${t("universe.key.hide")}" data-key-toggle>×</button></div>
      <ul class="mt-2xs flex flex-col">${items}</ul></section>`);
    setStyle(el, { left: `${b.x}px`, top: `${b.y}px`, width: `${b.w}px`, height: `${b.h}px` });
    el.querySelectorAll("[data-key-row]").forEach((li) => { li.style.height = `${G.key_row_px}px`; });
  }

  // ---- the mirror: the canvas's items as links ----------------------------------------------------------------------------
  #paintMirror() {
    const d = this.data, level = this.#level, s = d.summary, i = level === "run" ? this.#index(s.marks, d.place.run) : -1;
    const shown = level === "store" ? callouts(s.needs, (n) => this.#index(s.marks, n.id) >= 0, 0, G.callouts, (n) => s.marks.project[this.#index(s.marks, n.id)]).shown : [];
    const zoom = i >= 0 ? s.projects[s.marks.project[i]].id : d.place.zoom;
    const items = mirror({ level: level === "run" ? "project" : level, summary: s, zoom, shown, cap: G.mirror_cap });
    const islands = items.islands.map((p) => `<li><a href="${esc(d.href({ zoom: p.id, run: null, claim: null }))}" data-mirror-island="${esc(p.id)}">${count("universe.island", p.runs, { name: esc(p.label), n: num(p.runs) })}</a></li>`).join("");
    const runs = items.runs.map((r) => {
      const standing = r.standing ?? "needs_human", goal = d.goals.get(r.id);
      const words = joined(goal ? esc(clip(goal, 80)) : "", t(`${stateOf("standing", standing).copy}.label`), r.need ? t(`ask.${r.need.ask}`) : "", r.attempt > 1 ? t("row.attempt.short", { at: r.attempt }) : "");
      return `<li><a href="${esc(d.href({ run: r.id, claim: null }))}" data-mirror-run="${esc(r.id)}">${words}</a></li>`;
    }).join("");
    const project = level === "store" ? null : s.projects.find((p) => p.id === zoom);
    const rest = items.rest > 0 && project ? `<li><a href="${esc(d.list(project.id))}" data-mirror-rest>${count("universe.all_in", project.runs, { n: num(project.runs) })}</a></li>` : "";
    const html = `<ul>${islands}${runs}${rest}</ul>`;
    const nav = this.querySelector("[data-mirror]");
    morph(nav, html);
  }

  // ---- input --------------------------------------------------------------------------------------------------------------
  /** What the arrows move between at this zoom, in screen space: the islands, an island's marks on screen, a run's claims. */
  #keyItems() {
    const d = this.data, level = this.#level, m = d.summary.marks, out = [], { w, h } = this.#size;
    if (level === "store") this.#discs().forEach((c, k) => out.push({ id: d.summary.projects[k].id, kind: "island", x: c.sx, y: c.sy, r: c.r }));
    else if (level === "project") {
      const k = d.summary.projects.findIndex((p) => p.id === d.place.zoom), r = markRadius("project", this.#cam.s, G);
      for (let i = 0; i < m.id.length; i++) {
        if (m.project[i] !== k) continue;
        const s = this.#screen(this.#L.x[i], this.#L.y[i]);
        if (s.sx >= 0 && s.sy >= 0 && s.sx <= w && s.sy <= h) out.push({ id: m.id[i], kind: "run", i, x: s.sx, y: s.sy, r });
      }
    } else {
      for (const a of this.querySelectorAll("[data-orbit]")) out.push({ id: a.dataset.orbit, kind: "claim", x: +a.dataset.cx, y: +a.dataset.cy, r: G.claim_px });
      for (const a of this.querySelectorAll("[data-chain]")) out.push({ id: a.dataset.chain, kind: "run", i: this.#index(m, a.dataset.chain), x: +a.dataset.x + +a.dataset.w / 2, y: +a.dataset.y + +a.dataset.h / 2, r: G.chain_px });
    }
    return out;
  }
  #say(it) {
    const d = this.data, m = d.summary.marks;
    let words = "";
    if (it.kind === "island") { const p = d.summary.projects.find((x) => x.id === it.id); words = plainCount("universe.island", p.runs, { name: p.label, n: num(p.runs) }); }
    else if (it.kind === "run") words = [d.goals.get(it.id) ?? "", plain(`${stateOf("standing", m.standings[m.standing[it.i]]).copy}.label`)].filter(Boolean).join(plain("list.join"));
    else words = d.sheet?.row?.claims?.find((c) => c.id === it.id)?.text ?? it.id;
    this.querySelector("[data-said]").textContent = words;
  }
  #open(it) {
    const d = this.data;
    if (!it) return;
    if (it.kind === "island") this.emit("ev-navigate", d.href({ zoom: it.id, run: null, claim: null }));
    else if (it.kind === "run") this.emit("ev-navigate", d.href({ run: it.id, claim: null }));
    else this.emit("ev-navigate", d.href({ claim: it.id }));
  }
  #out() {
    const p = this.data.place;
    if (p.claim) this.emit("ev-navigate", this.data.href({ claim: null }));
    else if (p.run) this.emit("ev-navigate", this.data.href({ run: null }));
    else if (p.zoom) this.emit("ev-navigate", this.data.href({ zoom: null }));
  }
  #key(e) {
    if (e.target !== this.querySelector("canvas") || e.metaKey || e.ctrlKey || e.altKey) return;
    const items = this.#keyItems();
    if (e.key.startsWith("Arrow")) {
      e.preventDefault();
      this.#focus = nearest(items, items.some((i) => i.id === this.#focus) ? this.#focus : null, e.key);
      const it = items.find((i) => i.id === this.#focus);
      if (it) this.#say(it);
      this.#frame();
    } else if (e.key === "Enter" || e.key === "+" || e.key === "=") {
      e.preventDefault();
      this.#open(items.find((i) => i.id === this.#focus) ?? (e.key === "Enter" ? null : items[0]));
    } else if (e.key === "-") { e.preventDefault(); this.#out(); }
  }
  #hit(e) {
    const c = this.querySelector("canvas"), b = c.getBoundingClientRect(), sx = e.clientX - b.left, sy = e.clientY - b.top;
    const d = this.data, level = this.#level, m = d.summary.marks;
    if (level !== "store") {
      let best = null, dist = Math.max(G.min_glyph_px, 2 * markRadius("project", this.#cam.s, G));
      for (let i = 0; i < m.id.length; i++) {
        const s = this.#screen(this.#L.x[i], this.#L.y[i]), g = Math.hypot(s.sx - sx, s.sy - sy);
        if (g < dist) { dist = g; best = i; }
      }
      if (best !== null) return { kind: "run", id: m.id[best], i: best };
    }
    const w = toWorld(this.#cam, this.#view, sx, sy);
    const p = d.summary.projects.find((_, k) => { const c2 = this.#L.islands[k]; return Math.hypot(c2.x - w.x, c2.y - w.y) <= c2.r; });
    return p ? { kind: "island", id: p.id } : null;
  }
  #click(e) {
    if (e.target !== this.querySelector("canvas") || e.button) return;
    const it = this.#hit(e);
    if (it && !(it.kind === "island" && it.id === this.data.place.zoom && !this.data.place.run)) this.#open(it);
  }
  #move(e) {
    if (e.target !== this.querySelector("canvas") || this.#level === "store") { if (this.#hover !== null) { this.#hover = null; this.#frame(); } return; }
    const it = this.#hit(e), i = it?.kind === "run" ? it.i : null;
    if (i !== this.#hover) { this.#hover = i; this.#frame(); }
  }
  #wheel(e) {
    if (e.target !== this.querySelector("canvas") || e.ctrlKey) return;
    e.preventDefault();
    const now = performance.now();
    if (now - (this.wheeled ?? 0) < this.#ms("--duration-fly")) return;
    this.wheeled = now;
    if (e.deltaY < 0) { const it = this.#hit(e); if (it && this.#level !== "run") this.#open(it); } else this.#out();
  }
}

/** A box's data attributes (`place` puts the element there). */
const box = (b) => `data-x="${Math.round(b.x)}" data-y="${Math.round(b.y)}" data-w="${Math.round(b.w)}" data-h="${Math.round(b.h)}"`;

/** Morph `html` into `overlay` (nothing when it is the same; a link that stays keeps its focus), then put each positioned
 * element at the box its data attributes name (the CSP forbids inline style attributes, not the CSSOM). */
function place(overlay, html) {
  morph(overlay, html);
  for (const el of overlay.querySelectorAll("[data-x]")) {
    setStyle(el, { left: `${el.dataset.x}px`, top: `${el.dataset.y}px`, ...(el.dataset.w ? { width: `${el.dataset.w}px`, height: `${el.dataset.h}px` } : {}) });
  }
}
define("ev-universe", EvUniverse);
