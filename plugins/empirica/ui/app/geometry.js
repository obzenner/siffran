// The Universe's geometry: one catalogue of pure, named functions from data to places (UI.md, The Universe). No DOM, no
// state, no time: each maps numbers to numbers, every constant is named in `catalogue.universe` (`g` below), and each has
// property tests (tests/geometry.test.mjs). Drawing code (elements/organisms/ev-universe.js) only renders what these return.
//
// Units: the world unit is the sunflower's constant `c` (`g.mark_c`), the distance scale of an island's marks; a camera
// `{x, y, s}` looks at world point (x, y) with `s` screen px per world unit. A view is `{w, h, inset: {top, right, bottom,
// left}}` in CSS px: the part of the canvas no fixed overlay covers is the inset rectangle.

/** The golden angle, 2π(1 - 1/φ) = π(3 - √5): consecutive sunflower places turn by it, so no two ever line up (Vogel). */
export const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

// ---- marks: a sunflower per island ---------------------------------------------------------------------------------------

/** Place `i` (0 at the centre) of a sunflower of constant `c`: at radius c·√(i + ½), angle i·φ (Vogel 1979). The ½ puts each
 * place at the middle of its own ring of equal area π·c², so the nearest two places of any n sit ≥ 1.546·c apart (where
 * c·√i would put places 0 and 1 only c apart). A place depends on `i` alone: growth never moves a drawn mark. */
export function sunflower(i, c) {
  const r = c * Math.sqrt(i + 0.5), a = i * GOLDEN_ANGLE;
  return { x: r * Math.cos(a), y: r * Math.sin(a) };
}

/** The radius of an island reaching `n` places: c·√n reaches past the outermost place (c·√(n - ½)), plus a margin; at most
 * its slot's (`slot_places` places), so an island never leaves its slot (`latticePlace` keeps every later place inside). */
export function islandRadius(n, g) {
  return g.mark_c * Math.sqrt(Math.max(1, Math.min(n, g.slot_places))) + g.island_margin;
}

/** Where place `i` of an island sits (its offset from the centre), on one fixed lattice: a place depends on its number
 * alone, never on how many an island holds, so an island grows and never rescales. Places 0 … `slot_places` − 1 are the
 * sunflower's (`sunflower(i, mark_c)`); each later round of `slot_places` falls between them, at the fractional sunflower
 * index j + f (j = i mod `slot_places`, f the round's golden-ratio fraction, never 0 after the first), so every place stays
 * inside the slot (radius under mark_c·√(slot_places + ½)). Past a slot's capacity marks overlap; none ever moves. */
export function latticePlace(i, g) {
  const round = Math.floor(i / g.slot_places), f = round === 0 ? 0 : (round * (Math.sqrt(5) - 1) / 2) % 1;
  const u = (i % g.slot_places) + f, r = g.mark_c * Math.sqrt(u + 0.5), a = u * GOLDEN_ANGLE;
  return { x: r * Math.cos(a), y: r * Math.sin(a) };
}

/** A loop's budget: true for the first `cap` steps, then false. Every counted loop in this catalogue also asks its budget
 * (`loop_cap`, catalogue.universe), so no input, and no single mistaken bound, can make one run without end. */
export function budget(cap) {
  let n = 0;
  return () => n++ < cap;
}

const SLOTS = new Map();
/** The slots' size, in places: the smallest of `slot_tiers` that holds `reach` (the largest island's), the last being
 * `slot_places`. A store of a few small islands packs them close, so the store's camera shows them large and centred; the
 * slots grow only when the largest island crosses a tier, a handful of times in a store's life. */
export function slotTier(reach, g) {
  return g.slot_tiers.find((t) => t >= Math.min(reach, g.slot_places)) ?? g.slot_places;
}

/** The centres of the first `n` island slots: equal discs of an island of `places` places (`islandRadius`, `slotTier`),
 * each with its name's band, packed `island_gap` apart (`packCircles`). For one size, slot k depends on k alone (equal
 * radii, append-only), so an island's place is its index in the observer's project order, whatever its runs, and no two
 * islands' discs ever come closer than `island_gap`; equal circles pack in the same pattern at any size, so a larger
 * tier spreads the same arrangement wider. */
export function islandSlots(n, g, places = g.slot_places) {
  const key = `${n} ${places} ${g.slot_places} ${g.mark_c} ${g.island_margin} ${g.label_world} ${g.island_gap} ${g.pack_aspect} ${g.pack_angles}`;
  if (!SLOTS.has(key)) {
    const f = footprint(0, 0, islandRadius(places, g), g).r;
    SLOTS.set(key, packCircles(Array.from({ length: n }, () => f), g.island_gap, g).map((p) => ({ x: p.x, y: p.y - g.label_world / 2 })));
  }
  return SLOTS.get(key);
}

/** The radius a mark is drawn at, in screen px, at a zoom `level` and camera scale `s`: a dot of `dot_share`·c at the store
 * (at most `dot_max_px`), a shape of `glyph_share`·c zoomed in (at most `glyph_max_px`). Both shares are under half the
 * sunflower's least gap (1.546·c / 2), so no two marks overlap at any n. */
export function markRadius(level, s, g) {
  return level === "store" ? Math.min(g.dot_max_px, g.dot_share * g.mark_c * s) : Math.min(g.glyph_max_px, g.glyph_share * g.mark_c * s);
}

// ---- islands: packed slots, append-only ----------------------------------------------------------------------------------

/** An island's footprint: its disc of radius `r` and the band under it its name takes (`label_world` world units tall), as
 * one circle: centred `label_world / 2` below the disc's centre, `label_world / 2` wider. Packing and fitting use it, so a
 * name under its disc is on no other island and inside the view whenever a world unit is at least `label_px / label_world`
 * px. */
export function footprint(x, y, r, g) {
  return { x, y: y + g.label_world / 2, r: r + g.label_world / 2 };
}

/** Circles of `radii` (in order of first sight) packed close around the origin: each takes the free place nearest the
 * centre (by the elliptical norm √((x/aspect)² + y²), wider than tall like a screen) that keeps `gap` from every circle
 * before it. Its candidates are the places touching one earlier circle (`pack_angles` around it) or two at once. Each
 * place depends only on the circles before it, so packing a longer list never moves an earlier circle (append-only). */
export function packCircles(radii, gap, g) {
  const out = [], aspect = g.pack_aspect, eps = 1e-9, more = budget(g.loop_cap);
  const norm = (p) => Math.hypot(p.x / aspect, p.y);
  const free = (p, r) => out.every((o) => Math.hypot(p.x - o.x, p.y - o.y) >= o.r + r + gap - 1e-7);
  for (const r of radii) {
    if (!out.length) { out.push({ x: 0, y: 0, r }); continue; }
    let best = null, score = Infinity;
    const offer = (p) => { const n = norm(p); if (n < score - eps && free(p, r)) { best = p; score = n; } };
    for (const o of out) for (let k = 0; k < g.pack_angles && more(); k++) {
      const a = (2 * Math.PI * k) / g.pack_angles, d = o.r + r + gap;
      offer({ x: o.x + d * Math.cos(a), y: o.y + d * Math.sin(a) });
    }
    for (let i = 0; i < out.length && more(); i++) for (let j = i + 1; j < out.length && more(); j++) for (const p of touching(out[i], out[j], r, gap)) offer(p);
    out.push({ ...best, r });
  }
  return out;
}

/** The places where a circle of radius `r` touches both `a` and `b` at `gap` (none, or two): the intersections of the circles
 * of radius a.r + r + gap about a and b.r + r + gap about b. */
export function touching(a, b, r, gap) {
  const ra = a.r + r + gap, rb = b.r + r + gap, dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy);
  if (d === 0 || d > ra + rb || d < Math.abs(ra - rb)) return [];
  const along = (ra * ra - rb * rb + d * d) / (2 * d), h = Math.sqrt(Math.max(0, ra * ra - along * along));
  const mx = a.x + (along * dx) / d, my = a.y + (along * dy) / d;
  return [{ x: mx - (h * dy) / d, y: my + (h * dx) / d }, { x: mx + (h * dy) / d, y: my - (h * dx) / d }];
}

/** The world box holding `circles` (`{x, y, r}`); a unit box when there are none. */
export function bounds(circles) {
  if (!circles.length) return { x0: -1, y0: -1, x1: 1, y1: 1 };
  return { x0: Math.min(...circles.map((c) => c.x - c.r)), y0: Math.min(...circles.map((c) => c.y - c.r)),
           x1: Math.max(...circles.map((c) => c.x + c.r)), y1: Math.max(...circles.map((c) => c.y + c.r)) };
}

// ---- the camera ------------------------------------------------------------------------------------------------------------

/** The camera that shows world box `b` whole inside the view's inset rectangle, `fit_margin_px` clear of its edges, centred
 * there, filling it on one axis; at most `maxScale` px per world unit when one is given (an island's zoom). */
export function fitView(b, view, maxScale, g) {
  const i = view.inset, m = g.fit_margin_px;
  const w = Math.max(1, view.w - i.left - i.right - 2 * m), h = Math.max(1, view.h - i.top - i.bottom - 2 * m);
  const s = Math.max(1e-3, Math.min(maxScale ?? Infinity, w / Math.max(1e-9, b.x1 - b.x0), h / Math.max(1e-9, b.y1 - b.y0)));
  // the box's centre on the inset rectangle's centre
  const sx = i.left + (view.w - i.left - i.right) / 2, sy = i.top + (view.h - i.top - i.bottom) / 2;
  return { x: (b.x0 + b.x1) / 2 - (sx - view.w / 2) / s, y: (b.y0 + b.y1) / 2 - (sy - view.h / 2) / s, s };
}

/** World point → screen px. */
export function toScreen(cam, view, x, y) { return { sx: (x - cam.x) * cam.s + view.w / 2, sy: (y - cam.y) * cam.s + view.h / 2 }; }
/** Screen px → world point (the inverse of `toScreen`). */
export function toWorld(cam, view, sx, sy) { return { x: (sx - view.w / 2) / cam.s + cam.x, y: (sy - view.h / 2) / cam.s + cam.y }; }

/** The camera a share `k` (0..1) of the way along a flight: eased out (cubic), the scale eased in its logarithm so the
 * zoom's speed looks even (van Wijk and Nuij 2003). k = 0 is `from`, k = 1 is `to`. */
export function flyCamera(from, to, k) {
  const e = 1 - (1 - Math.min(1, Math.max(0, k))) ** 3;
  return { x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e, s: Math.exp(Math.log(from.s) + (Math.log(to.s) - Math.log(from.s)) * e) };
}

// ---- boxes and lines -------------------------------------------------------------------------------------------------------

/** Whether boxes `{x, y, w, h}` overlap (touching edges do not). */
export const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
/** Whether box `b` lies inside the view's inset rectangle. */
export const insideView = (b, view) => b.x >= view.inset.left && b.y >= view.inset.top && b.x + b.w <= view.w - view.inset.right && b.y + b.h <= view.h - view.inset.bottom;
/** Whether point (x, y) is in box `o`. */
export const inBox = (x, y, o) => x >= o.x && x <= o.x + o.w && y >= o.y && y <= o.y + o.h;
/** Whether box `b` meets circle `c` (`{sx, sy, r}`, screen px). */
export function boxMeetsCircle(b, c) {
  const dx = Math.max(b.x - c.sx, 0, c.sx - (b.x + b.w)), dy = Math.max(b.y - c.sy, 0, c.sy - (b.y + b.h));
  return Math.hypot(dx, dy) < c.r;
}

/** Whether the segment from (x1, y1) to (x2, y2) meets box `o` (Liang-Barsky clipping). */
export function crosses(x1, y1, x2, y2, o) {
  let t0 = 0, t1 = 1;
  const dx = x2 - x1, dy = y2 - y1;
  for (const [p, q] of [[-dx, x1 - o.x], [dx, o.x + o.w - x1], [-dy, y1 - o.y], [dy, o.y + o.h - y1]]) {
    if (p === 0) { if (q < 0) return false; continue; }
    const r = q / p;
    if (p < 0) { if (r > t1) return false; if (r > t0) t0 = r; } else { if (r < t0) return false; if (r < t1) t1 = r; }
  }
  return true;
}

/** Whether the segment from (x1, y1) to (x2, y2) meets circle `c` (`{sx, sy, r}`): its nearest point is within the radius. */
export function segmentMeetsCircle(x1, y1, x2, y2, c) {
  const dx = x2 - x1, dy = y2 - y1, len = dx * dx + dy * dy;
  const t = len === 0 ? 0 : Math.min(1, Math.max(0, ((c.sx - x1) * dx + (c.sy - y1) * dy) / len));
  return Math.hypot(x1 + t * dx - c.sx, y1 + t * dy - c.sy) < c.r;
}

/** The point of box `b` nearest (x, y): where a leader from (x, y) ends on it. */
export const nearestOnBox = (x, y, b) => ({ x: Math.min(Math.max(x, b.x), b.x + b.w), y: Math.min(Math.max(y, b.y), b.y + b.h) });

// ---- the overlay: names and callouts ----------------------------------------------------------------------------------------

/** How wide an island's name box is, in px, for its two lines of `name` and `sub` characters: the name at `label_char_px` a
 * character, the line under it at `label_sub_char_px`, the wider plus `label_pad_px`. */
export const labelWidth = (name, sub, g) => Math.max(name * g.label_char_px, sub * g.label_sub_char_px) + g.label_pad_px;

/** The box of an island's name, in screen px: two lines (its name, then "N runs · K need you"), `w` wide (`labelWidth`) and
 * `label_px` tall, centred under its disc `label_gap_px` below it. */
export function labelBox(disc, w, g) {
  const h = g.label_px;
  return { x: disc.sx - w / 2, y: disc.sy + disc.r + g.label_gap_px, w, h };
}

/** Each island's name box, in order: under its disc when that is clear of every disc, of the names placed before it and of
 * `fixed` (the overlay's own boxes), else above it, else beside it (right, then left); a name with no clear place stays
 * under its disc (`clear: false`) and the callouts still keep off it. `discs[i]` is island i's on screen, `widths[i]` its
 * name box's width (`labelWidth`). */
export function placeLabels(discs, widths, fixed, view, g) {
  const out = [], more = budget(g.loop_cap);
  for (let i = 0; i < discs.length && more(); i++) {
    const d = discs[i], below = labelBox(d, widths[i], g), { w, h } = below;
    const tries = [below, { ...below, y: d.sy - d.r - g.label_gap_px - h },
      { x: d.sx + d.r + g.label_gap_px, y: d.sy - h / 2, w, h }, { x: d.sx - d.r - g.label_gap_px - w, y: d.sy - h / 2, w, h }];
    const ok = (b) => insideView(b, view) && !fixed.some((o) => overlaps(b, o)) && !out.some((o) => overlaps(b, o))
      && discs.every((o, j) => j === i || !boxMeetsCircle(b, o));
    const pick = tries.find(ok);
    out.push({ ...(pick ?? below), clear: !!pick });
  }
  return out;
}

/** Where the callouts go, in screen px. Each anchor (a needs-a-human mark on screen, `{id, sx, sy, island}`, in the order
 * chosen) takes a free box of `callout_w` × `callout_px` around its mark (`calloutBoxes`): inside the view; clear of every box
 * already placed and of `hard` (the island names, the overlay's boxes); clear of every island's disc in `discs` (screen
 * circles, by island, its own included: a callout never lies over marks) and of every anchor's mark, its own too
 * (`callout_gap` around it); crossed by no leader already drawn. Its leader, from the mark to the box's nearest point, crosses no hard box, no
 * callout, no other leader and no other island's disc; it is at most `callout_reach` times its own island's radius long,
 * and inside its own island at most `callout_slack` times the shortest way out of it (the leader leaves its island
 * straight, so a callout sits outside the disc on the side nearest its mark). Of the free boxes, the one with the shortest
 * leader, one clear of the `soft` circles (zoomed on an island, its other marks) first
 * while there is one. An anchor with no free box within reach is not placed (the "+N more" count holds it) and the next
 * takes its turn, up to `max` placed; one with no island in `discs` reaches across the view. */
export function placeCallouts(anchors, hard, discs, view, g, { soft = [], max = Infinity } = {}) {
  const w = g.callout_w, h = g.callout_px, gap = g.callout_gap, out = [], taken = [...hard], lines = [];
  const marks = anchors.map((a) => ({ ...a, r: g.callout_gap }));
  for (const [n, a] of anchors.entries()) {
    if (out.length >= max) break;
    const own = discs[a.island] ?? null, others = discs.filter((_, k) => k !== a.island);
    const reach = own ? g.callout_reach * own.r : Math.hypot(view.w, view.h);
    const straight = own ? g.callout_slack * Math.max(0, own.r - Math.hypot(a.sx - own.sx, a.sy - own.sy)) : Infinity;
    const led = (b, e) => (!own || insideLength(a.sx, a.sy, e.x, e.y, own) <= straight + 1e-9)
      && taken.every((o) => inBox(a.sx, a.sy, o) || !crosses(a.sx, a.sy, e.x, e.y, o)) && others.every((c) => !segmentMeetsCircle(a.sx, a.sy, e.x, e.y, c))
      && lines.every((l) => !segmentsMeet(a.sx, a.sy, e.x, e.y, l.ax, l.ay, l.lx, l.ly) && !crosses(l.ax, l.ay, l.lx, l.ly, b));
    const free = (b, e) => insideView(b, view) && !taken.some((o) => overlaps(b, o)) && !discs.some((c) => boxMeetsCircle(b, c))
      && !marks.some((c) => boxMeetsCircle(b, c)) && led(b, e);
    const near = soft.filter((c) => Math.hypot(c.sx - a.sx, c.sy - a.sy) > 1e-9);
    const boxes = calloutBoxes(a, reach, g).filter((c) => reach + 1e-9 >= c.len);
    const off = (c) => !near.some((s) => boxMeetsCircle(c.b, s));
    const best = boxes.find((c) => off(c) && free(c.b, c.e)) ?? (near.length ? boxes.find((c) => !off(c) && free(c.b, c.e)) : undefined);
    if (!best) continue;
    taken.push(best.b);
    const p = { id: a.id, x: best.b.x, y: best.b.y, w, h, ax: a.sx, ay: a.sy, lx: best.e.x, ly: best.e.y };
    lines.push(p); out.push(p);
  }
  return out;
}

/** The boxes a callout may take around its mark `a`, nearest first (by leader: the distance from the mark to the box's
 * nearest point; on a tie, in the order made): right of the mark (left edge `callout_gap` past it), left of it, and
 * across it (above or below), `step` px apart each way out to `reach`, where `step` is `callout_step_px` or a fortieth
 * of the reach, whichever is coarser; none nearer than `callout_gap` (it would cover the mark). Each is `{b, e, len}`: the box, its nearest point and the leader's length. */
export function calloutBoxes(a, reach, g) {
  const w = g.callout_w, h = g.callout_px, gap = g.callout_gap, step = Math.max(g.callout_step_px, reach / 40), more = budget(g.loop_cap);
  const kx = Math.ceil(reach / step), ky = Math.ceil((reach + h) / step), across = Math.ceil((w + 2 * gap) / step);
  const xs = [];
  for (let k = 0; k <= kx && more(); k++) xs.push(a.sx + gap + k * step, a.sx - gap - w - k * step);
  for (let k = 1; k < across && more(); k++) xs.push(a.sx + gap - k * step);
  const ys = [a.sy - h / 2];
  for (let j = 1; j <= ky && more(); j++) ys.push(a.sy - h / 2 - j * step, a.sy - h / 2 + j * step);
  const out = [];
  for (const y of ys) for (const x of xs) {
    if (!more()) break;
    const b = { x, y, w, h }, e = nearestOnBox(a.sx, a.sy, b);
    const len = Math.hypot(e.x - a.sx, e.y - a.sy);
    if (len >= gap) out.push({ b, e, len, n: out.length });  // nearer would cover its own mark
  }
  return out.sort((p, q) => p.len - q.len || p.n - q.n);
}

/** The anchors with each island's nearest its rim first (in their order on a tie), each island keeping the turns it had:
 * the k-th anchor of island i becomes island i's k-th nearest the rim (`discs[a.island]`). The callouts then go to the
 * runs whose leaders leave their island soonest, so no leader runs across an island over its other marks. */
export function rimFirst(anchors, discs) {
  const gap = (a) => discs[a.island].r - Math.hypot(a.sx - discs[a.island].sx, a.sy - discs[a.island].sy);
  const by = new Map();
  anchors.forEach((a, n) => { if (!by.has(a.island)) by.set(a.island, []); by.get(a.island).push({ a, d: gap(a), n }); });
  for (const list of by.values()) list.sort((p, q) => p.d - q.d || p.n - q.n);
  const next = new Map();
  return anchors.map((a) => { const k = next.get(a.island) ?? 0; next.set(a.island, k + 1); return by.get(a.island)[k].a; });
}

/** How much of the segment (x1,y1)-(x2,y2) lies inside circle `c` (`{sx, sy, r}`): the length between where it enters and
 * leaves it, 0 when it misses it. */
export function insideLength(x1, y1, x2, y2, c) {
  const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy);
  if (len === 0) return 0;
  // |p + t·d - c|² = r² for t in [0, 1]: t² A + 2t B + C = 0
  const fx = x1 - c.sx, fy = y1 - c.sy, A = dx * dx + dy * dy, B = fx * dx + fy * dy, C = fx * fx + fy * fy - c.r * c.r, disc = B * B - A * C;
  if (disc <= 0) return 0;
  const root = Math.sqrt(disc), t0 = Math.max(0, (-B - root) / A), t1 = Math.min(1, (-B + root) / A);
  return t1 > t0 ? (t1 - t0) * len : 0;
}

/** Whether segments (x1,y1)-(x2,y2) and (x3,y3)-(x4,y4) meet: proper crossings and touching ends both count, except when
 * they share their first point (two leaders from one mark). */
export function segmentsMeet(x1, y1, x2, y2, x3, y3, x4, y4) {
  if (x1 === x3 && y1 === y3) return false;
  const side = (ax, ay, bx, by, cx, cy) => Math.sign((bx - ax) * (cy - ay) - (by - ay) * (cx - ax));
  const on = (ax, ay, bx, by, cx, cy) => Math.min(ax, bx) <= cx && cx <= Math.max(ax, bx) && Math.min(ay, by) <= cy && cy <= Math.max(ay, by);
  const d1 = side(x3, y3, x4, y4, x1, y1), d2 = side(x3, y3, x4, y4, x2, y2), d3 = side(x1, y1, x2, y2, x3, y3), d4 = side(x1, y1, x2, y2, x4, y4);
  if (d1 * d2 < 0 && d3 * d4 < 0) return true;
  return (d1 === 0 && on(x3, y3, x4, y4, x1, y1)) || (d2 === 0 && on(x3, y3, x4, y4, x2, y2)) || (d3 === 0 && on(x1, y1, x2, y2, x3, y3)) || (d4 === 0 && on(x1, y1, x2, y2, x4, y4));
}

/** How far a needs-a-human mark's pulse ring reaches, in px: `pulse_scale` times its mark's radius, stopping short of the
 * nearest name box (`names`), so a ring never covers a name; it is drawn under the marks, so it hides none. */
export function pulseRadius(anchor, markR, names, g) {
  let r = g.pulse_scale * markR;
  for (const o of names) {
    const dx = Math.max(o.x - anchor.sx, 0, anchor.sx - (o.x + o.w)), dy = Math.max(o.y - anchor.sy, 0, anchor.sy - (o.y + o.h));
    r = Math.min(r, Math.hypot(dx, dy));
  }
  return Math.max(0, r);
}

/** The key's box, in screen px: `key_w_px` wide in the view's bottom right corner, a row of `key_row_px` per standing plus
 * its head. */
export function keyBox(rows, view, g) {
  const h = g.key_head_px + rows * g.key_row_px;
  return { x: view.w - g.hud_pad_px - g.key_w_px, y: view.h - view.inset.bottom - h, w: g.key_w_px, h };
}

/** The view's inset: the top and bottom bars of the overlay, the key's column on the right while it is open, and the project
 * card's column on the left while one is shown (an island's zoom). */
export function viewInset(keyOpen, g, card = false) {
  return { top: g.hud_top_px, bottom: g.hud_bottom_px, left: card ? g.card_w_px + 2 * g.hud_pad_px : 0, right: keyOpen ? g.key_w_px + 2 * g.hud_pad_px : 0 };
}

/** The project card's box, in screen px: `card_w_px` wide in the top left corner under the top bar, a row of `key_row_px` per
 * standing under its head (`card_head_px`). */
export function cardBox(rows, view, g) {
  return { x: g.hud_pad_px, y: view.inset.top + g.hud_pad_px, w: g.card_w_px, h: g.card_head_px + rows * g.key_row_px };
}

// ---- a run's claims ---------------------------------------------------------------------------------------------------------

/** A run's claims in orbit around the run (C's run zoom), as offsets in px from the run's centre: the root on the first ring,
 * each claim on the ring of its depth on the support graph (its first listing in support order), its supporters spread
 * inside its own wedge; claims the root's tree does not reach share the ring past its deepest. `size`: `{ring, node, gap}`, the
 * px between rings, a node's radius and the least gap between two nodes. Ring d is at least (d + 1)·ring out, at least
 * `ring` past ring d - 1, and far enough out that the two nearest nodes on it (their wedges' least width apart) keep
 * 2·node + gap between their centres, so no two nodes ever overlap. Returns each claim's place by id and the rings' radii. */
export function orbit(root, claims, edges, size) {
  const below = new Map(claims.map((c) => [c.id, []]));
  for (const e of edges ?? []) below.get(e.from)?.push(e.to);
  const wedge = new Map();
  const place = (id, depth, a0, a1) => {
    if (wedge.has(id)) return;
    wedge.set(id, { depth, a0, a1 });
    const kids = below.get(id).filter((k) => !wedge.has(k) && below.has(k));
    kids.forEach((k, i) => place(k, depth + 1, a0 + ((a1 - a0) * i) / kids.length, a0 + ((a1 - a0) * (i + 1)) / kids.length));
  };
  const turn = 2 * Math.PI, start = -Math.PI / 2, rooted = !!root && below.has(root);
  if (rooted) place(root, 0, start, start + turn);
  // the claims no edge from the root reaches take a ring of their own past the root's tree, so their wedges never overlap it
  const rest = claims.filter((c) => !wedge.has(c.id)), past = rooted ? 1 + Math.max(...[...wedge.values()].map((w) => w.depth)) : 0;
  rest.forEach((c, i) => place(c.id, past, start + (turn * i) / rest.length, start + (turn * (i + 1)) / rest.length));
  const deepest = Math.max(-1, ...[...wedge.values()].map((w) => w.depth)), rings = [], more = budget(size.cap);
  for (let d = 0; d <= deepest && more(); d++) {
    const on = [...wedge.values()].filter((w) => w.depth === d), least = Math.min(...on.map((w) => w.a1 - w.a0));
    const apart = on.length > 1 ? (2 * size.node + size.gap) / (2 * Math.sin(Math.min(Math.PI, least) / 2)) : 0;
    rings.push(Math.max((d + 1) * size.ring, d ? rings[d - 1] + size.ring : 0, apart));
  }
  const at = new Map();
  for (const [id, w] of wedge) { const a = (w.a0 + w.a1) / 2, r = rings[w.depth]; at.set(id, { x: r * Math.cos(a), y: r * Math.sin(a), depth: w.depth }); }
  return { at, rings };
}

/** Where each claim's label goes, in the graph's px offsets: right of its node, else left, below or above it, each box clear
 * of every node (radius `size.node`; with `size.hit`, also the square `hit` across half that node's link takes), of the run
 * (`size.run`), of the labels placed before it and of `fixed`, and inside `inside` when one is given; a label with no clear
 * place is not drawn (null: its node keeps its words in its title). */
export function placeTags(at, widths, size, fixed = [], inside = null) {
  const out = new Map(), boxes = [], h = size.label_h, gap = size.gap;
  const nodes = [{ sx: 0, sy: 0, r: size.run }, ...[...at.values()].map((p) => ({ sx: p.x, sy: p.y, r: size.node }))];
  const hits = size.hit === undefined ? [] : [...at.values()].map((p) => ({ x: p.x - size.hit, y: p.y - size.hit, w: 2 * size.hit, h: 2 * size.hit }));
  const within = (b) => !inside || (b.x >= inside.x && b.y >= inside.y && b.x + b.w <= inside.x + inside.w && b.y + b.h <= inside.y + inside.h);
  for (const [id, p] of at) {
    const w = widths.get(id) ?? 0, n = size.node + gap;
    const tries = [{ x: p.x + n, y: p.y - h / 2 }, { x: p.x - n - w, y: p.y - h / 2 }, { x: p.x - w / 2, y: p.y + n }, { x: p.x - w / 2, y: p.y - n - h }].map((b) => ({ ...b, w, h }));
    const pick = tries.find((b) => within(b) && !nodes.some((c) => boxMeetsCircle(b, c)) && !hits.some((o) => overlaps(b, o)) && !boxes.some((o) => overlaps(b, o)) && !fixed.some((o) => overlaps(b, o)));
    if (pick) boxes.push(pick);
    out.set(id, pick ?? null);
  }
  return out;
}

/** A run's claims as a node-link graph around the run, one pure function for both renderers (the canvas's run zoom and
 * the sheet's small SVG): each claim drawn once at its `orbit` place, each published `SupportedBy` edge between two drawn
 * claims once (a claim that supports several keeps one node and several edges), a link from the run to its root, and each
 * label placed by `placeTags`. `claims` are `{id}` in the row's order, `widths` each label's width by id. */
export function claimGraph(root, claims, edges, widths, size, fixed = [], inside = null) {
  const { at, rings } = orbit(root, claims, edges, size);
  const seen = new Set(), lines = [];
  for (const e of edges ?? []) {
    const k = `${e.from} ${e.to}`;
    if (at.has(e.from) && at.has(e.to) && e.from !== e.to && !seen.has(k)) { seen.add(k); lines.push({ from: e.from, to: e.to, via: around(at.get(e.from), at.get(e.to), size) }); }
  }
  return { at, rings, edges: lines, root: at.has(root) ? root : null, labels: placeTags(at, widths, size, fixed, inside) };
}

/** The bends an edge from `a` to `b` takes so it never crosses the run's disc (radius `size.run + size.gap` about the
 * centre): none when the straight line misses it; else points on the circle of the nearer claim's radius, spread evenly
 * along the shorter way round, as few as keep every segment clear (both claims sit on rings past the disc, so a few do). */
function around(a, b, size) {
  const disc = { sx: 0, sy: 0, r: size.run + size.gap };
  if (!segmentMeetsCircle(a.x, a.y, b.x, b.y, disc)) return [];
  const ta = Math.atan2(a.y, a.x), r = Math.min(Math.hypot(a.x, a.y), Math.hypot(b.x, b.y));
  let sweep = Math.atan2(b.y, b.x) - ta;
  if (sweep > Math.PI) sweep -= 2 * Math.PI; else if (sweep < -Math.PI) sweep += 2 * Math.PI;
  if (Math.abs(Math.abs(sweep) - Math.PI) < 1e-9) sweep = Math.PI;  // straight across: one fixed side, the same every time
  for (let k = 1, more = budget(size.cap); more(); k++) {
    const via = Array.from({ length: k }, (_, j) => ({ x: r * Math.cos(ta + (sweep * (j + 1)) / (k + 1)), y: r * Math.sin(ta + (sweep * (j + 1)) / (k + 1)) }));
    const pts = [a, ...via, b];
    if (pts.every((p, j) => !j || !segmentMeetsCircle(pts[j - 1].x, pts[j - 1].y, p.x, p.y, disc))) return via;
  }
  return [];
}

/** An edge's points, from its claim through its bends (`via`) to the claim it supports: the line both renderers draw. */
export function edgePath(graph, e) {
  const p = (id) => ({ x: graph.at.get(id).x, y: graph.at.get(id).y });
  return [p(e.from), ...(e.via ?? []), p(e.to)];
}

/** A run's attempts as a short chain of glyphs, in the graph's px offsets: one per attempt, `step` apart in a row centred
 * under the run `below` px down, linked in order (attempt 1 first). The only relationship between runs 5.0.0 records. */
export function attemptChain(n, below, step) {
  return Array.from({ length: n }, (_, i) => ({ x: (i - (n - 1) / 2) * step, y: below }));
}

/** The box (`{x, y, w, h}`) holding a graph's nodes, its run, its drawn labels and `extra` boxes, `pad` px around: the
 * sheet's SVG takes it as its viewBox. */
export function graphBox(graph, size, extra = [], pad = 0) {
  const boxes = [{ x: -size.run, y: -size.run, w: 2 * size.run, h: 2 * size.run }, ...extra,
    ...[...graph.at.values()].map((p) => ({ x: p.x - size.node, y: p.y - size.node, w: 2 * size.node, h: 2 * size.node })),
    ...[...graph.labels.values()].filter(Boolean)];
  const x0 = Math.min(...boxes.map((b) => b.x)) - pad, y0 = Math.min(...boxes.map((b) => b.y)) - pad;
  return { x: x0, y: y0, w: Math.max(...boxes.map((b) => b.x + b.w)) + pad - x0, h: Math.max(...boxes.map((b) => b.y + b.h)) + pad - y0 };
}

const AXIS = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] };
/** The item an arrow key moves to from item `from` (an id): the nearest whose direction from it is within 45 degrees of the
 * arrow's, weighting distance off the axis twice; `from` itself at an edge, the first item when nothing is focused. */
export function nearest(items, from, key) {
  const here = items.find((i) => i.id === from);
  if (!here) return items[0]?.id ?? null;
  const [ax, ay] = AXIS[key];
  let best = null, score = Infinity;
  for (const i of items) {
    if (i.id === from) continue;
    const dx = i.x - here.x, dy = i.y - here.y, along = dx * ax + dy * ay, across = Math.abs(dx * ay - dy * ax);
    if (along <= 0 || across > along) continue;
    const s = along + 2 * across;
    if (s < score) { score = s; best = i.id; }
  }
  return best ?? from;
}
