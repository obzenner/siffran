// app/universe.js: the Universe's layout, a pure function of one summary, and its helpers that are not geometry
// (app/geometry.js has its own property tests). Island k sits in slot k (the observer's project order), each mark at its
// observer place on its island's sunflower, so a summary lays out the same whatever came before it (a reload, a poll, a
// cold start's coverage), and a later summary moves nothing the earlier one held. The callouts' choice and the
// accessible mirror are pure too, built from the summary the observer served.
import assert from "node:assert/strict";
import { test } from "vitest";
import fc from "fast-check";
import catalogue from "../catalogue.json" with { type: "json" };
import states from "../states.json" with { type: "json" };
import enums from "../enums.json" with { type: "json" };
import { FLAG_COUNT, callouts, layout, lit, mirror, shapePath } from "../app/universe.js";
import { bounds, fitView, footprint, islandRadius, islandSlots, latticePlace, slotTier, toScreen, viewInset } from "../app/geometry.js";
import { summary } from "./fixtures.mjs";

const G = catalogue.universe;
const marks = summary.marks;
const COLUMNS = ["id", "project", "place", "attempt", "standing", "updated", "started", "flags"];
/** A summary of `runs` (each `[project, place]`, place null while unplaced) over projects `ids` (each with its run count). */
function made(ids, runs) {
  const m = { standings: marks.standings, flag_bits: marks.flag_bits, ...Object.fromEntries(COLUMNS.map((k) => [k, []])) };
  runs.forEach(([k, place], n) => { m.id.push(`r${k}-${place}-${n}`); m.project.push(k); m.place.push(place); m.attempt.push(1); m.standing.push(4); m.updated.push(null); m.started.push(null); m.flags.push(0); });
  return { ...summary, projects: ids.map((id, k) => ({ ...summary.projects[0], id, label: id, runs: runs.filter(([p]) => p === k).length })), marks: m };
}
/** The slots' tier a summary lays out at: the smallest that holds its largest island (`slotTier`). */
const tierOf = (s) => slotTier(Math.max(1, ...s.projects.map((p, k) => Math.max(p.runs, ...s.marks.place.filter((x, i) => x !== null && s.marks.project[i] === k).map((x) => x + 1)))), G);
/** Every island's and placed mark's world place, by id. */
function places(L, s) {
  const out = new Map(L.islands.map((c) => [`island ${c.id}`, [c.x, c.y]]));
  s.marks.id.forEach((id, i) => { if (!Number.isNaN(L.x[i])) out.set(id, [L.x[i], L.y[i]]); });
  return out;
}

test("the layout of a summary is a pure function of it: the same summary twice, or after any other, lays out the same", () => {
  const a = layout(summary, G), b = layout(structuredClone(summary), G);
  assert.deepEqual([...places(a, summary)], [...places(b, summary)]);
  assert.deepEqual(a.bounds, b.bounds);
  assert.ok(marks.place.every((p) => p !== null), "the fixture's projects are read in full: every mark placed");
});

test("island k sits in slot k as large as its runs need, each mark at its observer place on its island's sunflower", () => {
  const L = layout(summary, G), reach = Math.max(...summary.projects.map((p, k) => Math.max(p.runs, ...marks.place.filter((_, i) => marks.project[i] === k).map((x) => x + 1))));
  const slots = islandSlots(summary.projects.length, G, slotTier(reach, G));
  summary.projects.forEach((p, k) => {
    const c = L.islands[k];
    const furthest = Math.max(p.runs, ...marks.place.filter((_, i) => marks.project[i] === k).map((x) => x + 1));
    assert.deepEqual([c.id, c.x, c.y, c.r, c.hue], [p.id, slots[k].x, slots[k].y, islandRadius(furthest, G), k % G.island_hues]);
  });
  for (let i = 0; i < marks.id.length; i++) {
    const c = L.islands[marks.project[i]], o = latticePlace(marks.place[i], G);
    assert.ok(Math.abs(L.x[i] - c.x - o.x) < 1e-9 && Math.abs(L.y[i] - c.y - o.y) < 1e-9, `mark ${i}`);
    assert.ok(Math.hypot(L.x[i] - c.x, L.y[i] - c.y) < c.r, `mark ${i} inside its island`);
  }
  const b = L.bounds;
  for (const c of L.islands) { const f = footprint(c.x, c.y, c.r, G); assert.ok(b.x0 <= f.x - f.r + 1e-9 && b.x1 >= f.x + f.r - 1e-9 && b.y0 <= f.y - f.r + 1e-9 && b.y1 >= f.y + f.r - 1e-9); }
});

/** A summary of one held mark (place 0 of project `at`) among `projects`. */
const one = (projects, at = 0, place = 0) => ({ projects, marks: { id: ["held"], project: [at], place: [place] } });
test("growth across the cap moves no mark: an island grows and never rescales (AST-2)", () => {
  for (const place of [0, 7, G.slot_places - 1]) {
    const before = layout(one([{ id: "b", runs: G.slot_places }], 0, place), G), after = layout(one([{ id: "b", runs: G.slot_places + 1 }], 0, place), G);
    assert.deepEqual([after.x[0], after.y[0]], [before.x[0], before.y[0]], `place ${place}`);
    const far = layout(one([{ id: "b", runs: 10 * G.slot_places }], 0, place), G);
    assert.deepEqual([far.x[0], far.y[0]], [before.x[0], before.y[0]], `place ${place}, ten times over`);
  }
});
test("a project the observer appends moves no island, and an untimed run in the outer band is drawn on its island (AST-2)", () => {
  const before = layout(one([{ id: "b", runs: 1 }]), G), after = layout(one([{ id: "b", runs: 1 }, { id: "a", runs: 1 }]), G);
  assert.deepEqual([after.x[0], after.y[0]], [before.x[0], before.y[0]], "the observer lists a new project last (its first run starts later)");
  const band = layout(one([{ id: "b", runs: 2 }], 0, 96), G);
  assert.ok(Math.hypot(band.x[0] - band.islands[0].x, band.y[0] - band.islands[0].y) < band.islands[0].r, "inside its island's shore");
});

test("a mark the observer has not placed (its project not read in full) is not drawn, and the rest keep their places", () => {
  const s = structuredClone(summary);
  s.marks.place[0] = null;
  const L = layout(s, G), full = layout(summary, G);
  assert.ok(Number.isNaN(L.x[0]) && Number.isNaN(L.y[0]));
  // the rest keep their places on their islands; the islands themselves move only if the slots' tier changed (L1)
  for (let i = 1; i < marks.id.length; i++) {
    const a = L.islands[marks.project[i]], b = full.islands[marks.project[i]];
    assert.ok(Math.abs(L.x[i] - a.x - (full.x[i] - b.x)) < 1e-9 && Math.abs(L.y[i] - a.y - (full.y[i] - b.y)) < 1e-9, `mark ${i}`);
  }
  if (tierOf(s) === tierOf(summary)) for (let i = 1; i < marks.id.length; i++) assert.deepEqual([L.x[i], L.y[i]], [full.x[i], full.y[i]]);
});

test("property: whatever order, coverage step or growth produced two summaries, an island or mark in both has one place", () => {
  // a store grows: projects are appended to the observer's order and each run takes the next place of its island (the
  // observer's start order); a project still being read has its runs unplaced. Any two summaries along the way, in any
  // order, agree on every island and mark both hold.
  const run = fc.nat(5);
  fc.assert(fc.property(fc.array(fc.tuple(fc.array(run, { minLength: 1, maxLength: 20 }), fc.array(fc.nat(5), { maxLength: 3 })), { minLength: 2, maxLength: 5 }), fc.nat(), fc.nat(), (steps, i, j) => {
    const ids = [], next = [], held = [], seen = [];
    for (const [step, reading] of steps) {
      for (const p of step) { while (ids.length <= p) { ids.push(`p${ids.length}`); next.push(0); } held.push([p, next[p]++]); }
      const s = made(ids, held.map(([p, place]) => [p, reading.includes(p) ? null : place]));
      s.marks.id = held.map((_, n) => `r${n}`);
      seen.push(s);
    }
    const a = seen[i % seen.length], b = seen[j % seen.length], LA = layout(a, G), LB = layout(b, G);
    if (tierOf(a) === tierOf(b)) {
      const A = places(LA, a), B = places(LB, b);
      let shared = 0;
      for (const [k, v] of A) if (B.has(k)) { shared++; assert.deepEqual(B.get(k), v, k); }
      assert.ok(shared >= 1, "the first island is in both");
    }
    // across a tier the islands keep their arrangement, spread by the slots' size: the same pattern, scaled (L1)
    const unit = (s) => 2 * footprint(0, 0, islandRadius(tierOf(s), G), G).r + G.island_gap;
    for (const c of LA.islands) {
      const d = LB.islands.find((x) => x.id === c.id);
      if (d) for (const [u, v] of [[c.x / unit(a), d.x / unit(b)], [(c.y + G.label_world / 2) / unit(a), (d.y + G.label_world / 2) / unit(b)]]) assert.ok(Math.abs(u - v) < 1e-9, c.id);
    }
  }), { seed: 20261014, numRuns: 80 });
});

test("an island's slot is its index in the observer's project order, never which project was seen first", () => {
  // the fixture's projects in another order lay out differently: the slot is the observer's order, not first sight
  const s = structuredClone(summary), turned = { ...s, projects: [...s.projects].reverse(), marks: { ...s.marks, project: s.marks.project.map((k) => s.projects.length - 1 - k) } };
  const a = layout(summary, G), b = layout(turned, G);
  assert.notDeepEqual(a.islands.find((c) => c.id === summary.projects[0].id).x, b.islands.find((c) => c.id === summary.projects[0].id).x);
  assert.deepEqual(a.islands.map((c) => [c.x, c.y]), b.islands.map((c) => [c.x, c.y]), "slot k is where island k goes, whichever project it is");
});

test("a one-run island is small: its radius is its one mark and its margin", () => {
  const s = made(["p"], [[0, 0]]);
  assert.equal(layout(s, G).islands[0].r, G.mark_c + G.island_margin);
});

test("the callouts are chosen across the islands, the most urgent of each first, in turn", () => {
  // the 1k screenshot: storefront holds the six newest; the others wait in the needs' order
  const needs = [...Array.from({ length: 6 }, (_, i) => ({ id: `s${i}`, key: `storefront/r${i}/1` })),
                 { id: "h0", key: "harbor/r0/1" }, { id: "o0", key: "orbit/r0/1" }, { id: "s6", key: "storefront/r6/1" },
                 { id: "h1", key: "harbor/r1/1" }].map((n) => ({ ask: "decide_claim", asked_at: null, host: "claude", session: null, ...n }));
  const cap = 6;  // a cap past the islands' count, so the turns show (the catalogue's own cap is held by the next test)
  const { shown, more } = callouts(needs, () => true, 40, cap);
  assert.deepEqual(shown.map((n) => n.id), ["s0", "h0", "o0", "s1", "h1", "s2"], "round-robin over islands, each island's in order");
  assert.equal(more, 40 - cap);
  const one = callouts(needs.filter((n) => n.key.startsWith("storefront")), () => true, 7, cap);
  assert.deepEqual(one.shown.map((n) => n.id), ["s0", "s1", "s2", "s3", "s4", "s5"], "one island: its own, in order");
});

test("at most the cap of callouts, the rest said as the observer's count, never the page's", () => {
  const needs = Array.from({ length: 10 }, (_, i) => ({ id: `n${i}`, key: `k${i}`, ask: "decide_claim", asked_at: null, host: "claude", session: null }));
  const { shown, more } = callouts(needs, () => true, 79, G.callouts);
  assert.equal(shown.length, G.callouts);
  assert.equal(more, 79 - G.callouts, "the store's needs_human count less what is shown");
  const some = callouts(needs, (n) => n.id === "n3" || n.id === "n8", 79, G.callouts);
  assert.deepEqual(some.shown.map((n) => n.id), ["n3", "n8"]);
  assert.equal(some.more, 77);
});

test("the mirror holds the same items as the canvas: every island and callout at store zoom, every mark of an island zoomed in", () => {
  const store = mirror({ level: "store", summary, shown: summary.needs, cap: G.mirror_cap });
  assert.deepEqual(store.islands.map((i) => i.id), summary.projects.map((p) => p.id));
  assert.deepEqual(store.runs.map((r) => r.id), summary.needs.map((n) => n.id));
  const project = marks.project[0];
  const zoomed = mirror({ level: "project", summary, zoom: summary.projects[project].id, cap: G.mirror_cap });
  const mine = marks.id.filter((_, i) => marks.project[i] === project);
  assert.deepEqual(zoomed.runs.map((r) => r.id), mine, "every mark of the island, in the observer's order");
  const capped = mirror({ level: "project", summary, zoom: summary.projects[project].id, cap: 1 });
  assert.equal(capped.runs.length, Math.min(1, mine.length));
  assert.equal(capped.rest, summary.projects[project].runs - capped.runs.length, "and says how many more the list holds, by the observer's count");
  assert.equal(summary.projects[project].runs, mine.length);
});

test("one shape per claim state too, each its own path (a claim's node in a run's graph)", () => {
  const paths = Object.values(states.claim).map((c) => shapePath(c.shape, 10));
  assert.equal(new Set(paths).size, paths.length);
});

test("one shape per standing, each drawn from one path (the canvas and the key share it)", () => {
  const paths = summary.marks.standings.map((s) => shapePath(states.standing[s].shape, 10));
  assert.equal(new Set(paths).size, paths.length);
  for (const p of paths) assert.match(p, /^M/);
});

test("the shapes are a closed map: every shape states.json names has a path, and an unknown one is an error (SOL-02)", () => {
  const named = new Set(Object.values(states).flatMap((d) => Object.values(d)).map((v) => v?.shape).filter(Boolean));
  assert.ok(named.size >= 9);
  for (const shape of named) assert.match(shapePath(shape, 10), /^M/, shape);
  for (const bad of ["crosss", "", "toString", undefined]) assert.throws(() => shapePath(bad, 10), /no such shape/, String(bad));
});

/** A path's subpaths, each its points (M, L and the A's end points) and its arcs' radii. */
function parse(d) {
  return d.split("M").filter(Boolean).map((sub) => {
    const pts = [], radii = [];
    for (const [, cmd, args] of sub.replace(/^/, "M").matchAll(/([MLA])([^MLAZ]*)/g)) {
      const v = args.trim().split(/[ ,]+/).map(Number);
      if (cmd === "A") { radii.push([v[0], v[1]]); pts.push([v[5], v[6]]); } else pts.push([v[0], v[1]]);
    }
    return { pts, radii, closed: /Z$/.test(sub) };
  });
}
const R = 10;
test("each shape is its declared geometry, `r` across half, centred on 0,0", () => {
  const P = (s) => parse(shapePath(s, R));
  assert.deepEqual(P("diamond"), [{ pts: [[0, -11.5], [11.5, 0], [0, 11.5], [-11.5, 0]], radii: [], closed: true }]);
  assert.deepEqual(P("triangle"), [{ pts: [[0, -11.5], [10.5, 8.5], [-10.5, 8.5]], radii: [], closed: true }]);
  assert.deepEqual(P("square"), [{ pts: [[-9, -9], [9, -9], [9, 9], [-9, 9]], radii: [], closed: true }]);
  assert.deepEqual(P("hollow-square"), [{ pts: [[-9, -9], [9, -9], [9, 9], [-9, 9]], radii: [], closed: true }, { pts: [[-5.5, -5.5], [-5.5, 5.5], [5.5, 5.5], [5.5, -5.5]], radii: [], closed: true }], "the hole wound the other way");
  assert.deepEqual(P("circle"), [{ pts: [[-10, 0], [10, 0], [-10, 0]], radii: [[10, 10], [10, 10]], closed: true }]);
  assert.deepEqual(P("ring"), [{ pts: [[-10, 0], [10, 0], [-10, 0]], radii: [[10, 10], [10, 10]], closed: true }, { pts: [[-5.5, 0], [5.5, 0], [-5.5, 0]], radii: [[5.5, 5.5], [5.5, 5.5]], closed: true }]);
  assert.deepEqual(P("half"), [{ pts: [[-10, 0], [10, 0]], radii: [[10, 10]], closed: true }, { pts: [[10, 0], [-10, 0], [-5.5, 0], [5.5, 0]], radii: [[10, 10], [5.5, 5.5]], closed: true }]);
  const hex = P("hexagon")[0].pts;
  assert.equal(hex.length, 6);
  hex.forEach(([x, y], k) => { assert.ok(Math.abs(x - 10 * Math.cos((Math.PI / 3) * k)) <= 0.005 && Math.abs(y - 10 * Math.sin((Math.PI / 3) * k)) <= 0.005, `${k}`); });
  assert.deepEqual(P("cross"), [{ pts: [[-10, -3], [-3, -10], [0, -7], [3, -10], [10, -3], [7, 0], [10, 3], [3, 10], [0, 7], [-3, 10], [-10, 3], [-7, 0]], radii: [], closed: true }]);
  // the arc flags: the circle sweeps one way, the ring's hole the other, the half's top clockwise
  assert.match(shapePath("circle", R), /A10 10 0 1 0 10 0A10 10 0 1 0 -10 0Z$/);
  assert.match(shapePath("ring", R), /A5\.5 5\.5 0 1 1 5\.5 0A5\.5 5\.5 0 1 1 -5\.5 0Z$/);
  assert.equal(shapePath("half", R), "M-10 0A10 10 0 0 1 10 0ZM10 0A10 10 0 0 1 -10 0L-5.5 0A5.5 5.5 0 0 0 5.5 0Z");
  assert.equal(shapePath("square", 1 / 3), "M-0.3 -0.3L0.3 -0.3L0.3 0.3L-0.3 0.3Z", "coordinates rounded to hundredths");
});

test("the mirror at an island lists its marks in the observer's order up to the cap, then how many more", () => {
  const k = summary.projects.findIndex((p) => p.runs > 2), p = summary.projects[k];
  const all = summary.marks.id.filter((_, i) => summary.marks.project[i] === k);
  const got = mirror({ level: "project", summary, zoom: p.id, cap: 2 });
  assert.deepEqual(got.runs.map((r) => r.id), all.slice(0, 2));
  assert.deepEqual(got.runs[0], { id: all[0], standing: summary.marks.standings[summary.marks.standing[summary.marks.id.indexOf(all[0])]], attempt: summary.marks.attempt[summary.marks.id.indexOf(all[0])] });
  assert.equal(got.rest, p.runs - 2);
  assert.deepEqual(got.islands, []);
  assert.deepEqual(mirror({ level: "project", summary, zoom: "nope", cap: 5 }), { islands: [], runs: [], rest: 0 });
  const store = mirror({ level: "store", summary, shown: [{ id: "x" }], cap: 5 });
  assert.deepEqual(store, { islands: summary.projects.map((q) => ({ id: q.id, label: q.label, runs: q.runs })), runs: [{ id: "x", need: { id: "x" } }], rest: 0 });
});

test("callouts: each island's needs in order, the islands in turn, at most the cap, the default island its key's project", () => {
  const n = (id, key) => ({ id, key });
  const needs = [n("a1", "A/1"), n("a2", "A/2"), n("b1", "B/1"), n("a3", "A/3"), n("c1", "C/1")];
  assert.deepEqual(callouts(needs, () => true, 9, 4).shown.map((x) => x.id), ["a1", "b1", "c1", "a2"]);
  assert.deepEqual(callouts(needs, () => true, 9, 10).shown.map((x) => x.id), ["a1", "b1", "c1", "a2", "a3"]);
  assert.deepEqual(callouts(needs, () => true, 2, 10).more, 0, "never below none");
  assert.deepEqual(callouts(needs, () => true, 9, 0), { shown: [], more: 9 });
  assert.deepEqual(callouts(needs, (x) => x.id !== "b1", 9, 3).shown.map((x) => x.id), ["a1", "c1", "a2"]);
});

test("one count per declared flag, the observer's (SOL-03), and a highlight lights the marks whose event is in the window (AST-6)", () => {
  assert.deepEqual(Object.keys(FLAG_COUNT), enums.flag, "every flag the contract declares, and no other");
  for (const f of enums.flag) assert.equal(typeof FLAG_COUNT[f](summary.aggregates), "number", f);
  const m = { flag_bits: enums.flag, flags: [1, 1, 0], window_flags: [1, 0, 0], updated: [10, 900, 900] };
  assert.deepEqual([0, 1, 2].map((i) => lit(m, i, "failed_audit", 500)), [true, false, false], "the old event with a recent write is not lit");
  assert.deepEqual([0, 1, 2].map((i) => lit(m, i, null, 500)), [false, true, true], "no highlight: the period by the latest write");
  assert.deepEqual([0, 1, 2].map((i) => lit(m, i, null, null)), [true, true, true]);
});

/** The store's camera as ev-universe takes it: the world box filled inside the bars, at most `island_scale_max`. */
const storeView = (w, h) => ({ w, h, inset: viewInset(false, G) });
test("at small n the store fills the view: the islands centred, a readable size, the least gap kept (L1)", () => {
  for (const [w, h] of [[1440, 900], [1024, 768]]) for (let n = 1; n <= 6; n++) {
    const s = made(Array.from({ length: n }, (_, k) => `p${k}`), Array.from({ length: n }, (_, k) => [k, 0]));
    const L = layout(s, G), view = storeView(w, h), cam = fitView(L.bounds, view, G.island_scale_max, G);
    const feet = L.islands.map((c) => footprint(c.x, c.y, c.r, G)), b = bounds(feet);
    const a = toScreen(cam, view, b.x0, b.y0), z = toScreen(cam, view, b.x1, b.y1), i = view.inset;
    const iw = w - i.left - i.right, ih = h - i.top - i.bottom, cx = i.left + iw / 2, cy = i.top + ih / 2;
    assert.ok(Math.abs((a.sx + z.sx) / 2 - cx) <= 0.05 * iw && Math.abs((a.sy + z.sy) / 2 - cy) <= 0.05 * ih, `n=${n} ${w}x${h}: centred`);
    assert.ok(Math.max(z.sx - a.sx, z.sy - a.sy) >= 0.5 * Math.min(iw, ih), `n=${n} ${w}x${h}: the islands span half the short side`);
    // a readable size, not a dot in a corner: each island (with its name) at least `fill_share` of the short side across
    for (const f of feet) assert.ok(2 * f.r * cam.s >= G.fill_share * Math.min(iw, ih), `n=${n} ${w}x${h}: ${Math.round(2 * f.r * cam.s)} px of ${Math.min(iw, ih)}`);
    assert.ok(a.sx >= i.left - 1 && a.sy >= i.top - 1 && z.sx <= w - i.right + 1 && z.sy <= h - i.bottom + 1, `n=${n}: inside the bars`);
    for (let p = 0; p < n; p++) for (let q = p + 1; q < n; q++)
      assert.ok(Math.hypot(feet[p].x - feet[q].x, feet[p].y - feet[q].y) >= feet[p].r + feet[q].r + G.island_gap - 1e-6, `n=${n}: the gap`);
  }
});
test("the slots are of the smallest tier that holds every island: they move only when the largest island crosses a tier (L1)", () => {
  for (const reach of [1, 2, 4, 5, 16, 17, 64, 65, G.slot_places, 10 * G.slot_places]) {
    const tier = slotTier(reach, G);
    assert.ok(G.slot_tiers.includes(tier) && tier >= Math.min(reach, G.slot_places), `${reach} → ${tier}`);
    assert.ok(G.slot_tiers.every((t) => t >= tier || t < Math.min(reach, G.slot_places)), "the smallest such tier");
  }
  assert.equal(G.slot_tiers.at(-1), G.slot_places);
  const before = layout(made(["a", "b"], [[0, 0], [1, 0]]), G), after = layout(made(["a", "b"], [[0, 0], [1, 0], [1, 1]]), G);
  assert.deepEqual(after.islands.map((c) => [c.x, c.y]), before.islands.map((c) => [c.x, c.y]), "a run inside the tier moves no island");
});
