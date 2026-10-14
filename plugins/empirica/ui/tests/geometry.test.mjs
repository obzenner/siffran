// app/geometry.js: the Universe's catalogue of pure geometry. Property tests (fast-check, fixed seed, bounded runs) state
// each function's invariant over generated inputs; a failure shrinks to a minimal case and prints its seed.
import assert from "node:assert/strict";
import { test } from "vitest";
import fc from "fast-check";
import catalogue from "../catalogue.json" with { type: "json" };
import {
  GOLDEN_ANGLE, attemptChain, edgePath, budget, calloutBoxes, insideLength, rimFirst, bounds, cardBox, claimGraph, footprint, graphBox, labelBox, labelWidth, boxMeetsCircle, crosses, fitView, flyCamera, inBox, insideView,
  islandRadius, islandSlots, keyBox, latticePlace, markRadius, nearest, nearestOnBox, orbit, placeTags, segmentsMeet, overlaps, packCircles, placeCallouts, placeLabels, pulseRadius, segmentMeetsCircle,
  sunflower, toScreen, toWorld, touching, viewInset,
} from "../app/geometry.js";

const G = catalogue.universe;
const RUNS = { seed: 20261014, numRuns: 60 };
const EPS = 1e-6;
/** The sunflower's least gap, proven below for every n up to 4,096 (c = 1). */
const LEAST_GAP = 1.546;

test("the golden angle is 2π(1 - 1/φ)", () => {
  const phi = (1 + Math.sqrt(5)) / 2;
  assert.ok(Math.abs(GOLDEN_ANGLE - 2 * Math.PI * (1 - 1 / phi)) < 1e-12);
});

test("a sunflower place depends on its number alone, at radius c·√(i+½) and angle i·φ", () => {
  fc.assert(fc.property(fc.nat(5000), fc.double({ min: 0.1, max: 50, noNaN: true }), (i, c) => {
    const p = sunflower(i, c);
    assert.ok(Math.abs(Math.hypot(p.x, p.y) - c * Math.sqrt(i + 0.5)) < EPS * c * 100);
    assert.deepEqual(sunflower(i, c), p, "the same place every time");
  }), RUNS);
});

test("no two sunflower places are closer than 1.546·c at any n (so marks drawn under half of it never overlap)", () => {
  const pts = Array.from({ length: 4096 }, (_, i) => sunflower(i, 1));
  // a grid of cells as wide as the gap: a nearer pair would share a cell or touch a neighbouring one
  const cell = new Map(), key = (x, y) => `${Math.floor(x / LEAST_GAP)},${Math.floor(y / LEAST_GAP)}`;
  let least = Infinity;
  pts.forEach((p, i) => {
    const cx = Math.floor(p.x / LEAST_GAP), cy = Math.floor(p.y / LEAST_GAP);
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (const j of cell.get(`${cx + dx},${cy + dy}`) ?? []) least = Math.min(least, Math.hypot(p.x - pts[j].x, p.y - pts[j].y));
    const k = key(p.x, p.y); if (!cell.has(k)) cell.set(k, []); cell.get(k).push(i);
  });
  assert.ok(least >= LEAST_GAP, least);
  assert.ok(G.dot_share < LEAST_GAP / 2 && G.glyph_share < LEAST_GAP / 2, "the drawn marks fit inside half the gap");
});

test("an island of n places holds every one of them, grows with n up to its slot's capacity, and never leaves its slot", () => {
  fc.assert(fc.property(fc.integer({ min: 1, max: 4096 }), (n) => {
    const r = islandRadius(n, G);
    for (const i of [0, n - 1]) { const p = latticePlace(i, G); assert.ok(Math.hypot(p.x, p.y) < r, `place ${i} of ${n}`); }
    if (n < G.slot_places) assert.ok(islandRadius(n + 1, G) > r);
    else assert.equal(r, islandRadius(G.slot_places, G), "at most its slot");
  }), RUNS);
});

test("a lattice place depends on its number alone: the sunflower's up to the cap, then between them, and never rescaled (AST-2)", () => {
  for (let i = 0; i < G.slot_places; i++) assert.deepEqual(latticePlace(i, G), sunflower(i, G.mark_c), `place ${i}`);
  const taken = new Set(Array.from({ length: G.slot_places }, (_, i) => { const p = latticePlace(i, G); return `${p.x.toFixed(9)} ${p.y.toFixed(9)}`; }));
  fc.assert(fc.property(fc.integer({ min: G.slot_places, max: 20 * G.slot_places }), (i) => {
    const p = latticePlace(i, G);
    assert.deepEqual(latticePlace(i, G), p, "the same place every time");
    assert.ok(Math.hypot(p.x, p.y) < islandRadius(G.slot_places, G), "inside the slot");
    assert.ok(!taken.has(`${p.x.toFixed(9)} ${p.y.toFixed(9)}`), "never on a place of the first round");
  }), RUNS);
});

test("slot k depends on k alone (append-only), and no two islands' discs come closer than the gap at any run counts", () => {
  fc.assert(fc.property(fc.array(fc.integer({ min: 0, max: 6000 }), { minLength: 1, maxLength: 24 }), (runs) => {
    const slots = islandSlots(runs.length, G), fewer = islandSlots(Math.max(1, runs.length - 3), G);
    fewer.forEach((p, k) => assert.deepEqual(p, slots[k], "a longer store moves no slot"));
    const discs = slots.map((p, k) => ({ ...p, r: islandRadius(runs[k], G) }));
    for (let i = 0; i < discs.length; i++) for (let j = i + 1; j < discs.length; j++) {
      const a = discs[i], b = discs[j], fa = footprint(a.x, a.y, a.r, G), fb = footprint(b.x, b.y, b.r, G);
      assert.ok(Math.hypot(a.x - b.x, a.y - b.y) >= a.r + b.r + G.island_gap - 1e-6, `discs ${i} and ${j}`);
      assert.ok(Math.hypot(fa.x - fb.x, fa.y - fb.y) >= fa.r + fb.r + G.island_gap - 1e-6, `names' bands ${i} and ${j}`);
    }
  }), RUNS);
});

test("a mark's radius is the dot at the store and the glyph zoomed in, both under half the sunflower's least gap", () => {
  fc.assert(fc.property(fc.double({ min: 0.01, max: 100, noNaN: true }), (s) => {
    const dot = markRadius("store", s, G), glyph = markRadius("project", s, G);
    assert.ok(dot <= (LEAST_GAP / 2) * G.mark_c * s && glyph <= (LEAST_GAP / 2) * G.mark_c * s);
    assert.ok(glyph <= G.glyph_max_px && dot <= G.dot_max_px);
  }), RUNS);
});

const radii = fc.array(fc.double({ min: 0.5, max: 60, noNaN: true }), { minLength: 1, maxLength: 24 });
test("packed islands never meet, keep the gap, and the first sits in the centre", () => {
  fc.assert(fc.property(radii, (rs) => {
    const out = packCircles(rs, G.island_gap, G);
    assert.equal(out.length, rs.length);
    assert.deepEqual([out[0].x, out[0].y], [0, 0]);
    for (let i = 0; i < out.length; i++) for (let j = i + 1; j < out.length; j++)
      assert.ok(Math.hypot(out[i].x - out[j].x, out[i].y - out[j].y) >= out[i].r + out[j].r + G.island_gap - 1e-6, `${i} and ${j}`);
  }), RUNS);
});

test("packing is append-only: a new island moves none packed before it", () => {
  fc.assert(fc.property(radii, radii, (a, b) => {
    const before = packCircles(a, G.island_gap, G), after = packCircles([...a, ...b], G.island_gap, G);
    assert.deepEqual(after.slice(0, a.length), before);
  }), RUNS);
});

test("packing is close: an island touches one packed before it (it sits no further out than it must)", () => {
  fc.assert(fc.property(radii, (rs) => {
    const out = packCircles(rs, G.island_gap, G);
    for (let i = 1; i < out.length; i++)
      assert.ok(out.slice(0, i).some((o) => Math.abs(Math.hypot(o.x - out[i].x, o.y - out[i].y) - (o.r + out[i].r + G.island_gap)) < 1e-6), `island ${i} touches none`);
  }), RUNS);
});

test("a circle touching two others sits at the gap from both", () => {
  fc.assert(fc.property(fc.double({ min: -50, max: 50, noNaN: true }), fc.double({ min: 1, max: 20, noNaN: true }), fc.double({ min: 1, max: 20, noNaN: true }), fc.double({ min: 0.5, max: 10, noNaN: true }), (bx, ra, r, gap) => {
    const a = { x: 0, y: 0, r: ra }, b = { x: bx, y: 3, r: ra / 2 };
    for (const p of touching(a, b, r, gap)) {
      assert.ok(Math.abs(Math.hypot(p.x - a.x, p.y - a.y) - (a.r + r + gap)) < 1e-6);
      assert.ok(Math.abs(Math.hypot(p.x - b.x, p.y - b.y) - (b.r + r + gap)) < 1e-6);
    }
  }), RUNS);
});

const views = fc.record({ w: fc.integer({ min: 400, max: 2600 }), h: fc.integer({ min: 300, max: 1600 }), keyOpen: fc.boolean() })
  .map(({ w, h, keyOpen }) => ({ w, h, inset: viewInset(keyOpen, G) }));
test("fit-to-view shows every island inside the part no bar covers, a margin clear, never above its scale cap", () => {
  fc.assert(fc.property(radii, views, fc.double({ min: 1, max: 40, noNaN: true }), (rs, view, cap) => {
    const b = bounds(packCircles(rs, G.island_gap, G)), cam = fitView(b, view, cap, G);
    const a = toScreen(cam, view, b.x0, b.y0), z = toScreen(cam, view, b.x1, b.y1), i = view.inset, m = G.fit_margin_px;
    assert.ok(cam.s <= cap + 1e-9);
    assert.ok(a.sx >= i.left + m - 1e-6 && a.sy >= i.top + m - 1e-6 && z.sx <= view.w - i.right - m + 1e-6 && z.sy <= view.h - i.bottom - m + 1e-6, JSON.stringify([a, z, view]));
    // centred in the uncovered part: equal room left and right, above and below
    assert.ok(Math.abs((a.sx - i.left) - (view.w - i.right - z.sx)) < 1e-6 && Math.abs((a.sy - i.top) - (view.h - i.bottom - z.sy)) < 1e-6);
  }), RUNS);
});

test("fit-to-view uses the room it has: one axis fills it to the margin unless the scale cap stops it", () => {
  fc.assert(fc.property(radii, views, (rs, view) => {
    const b = bounds(packCircles(rs, G.island_gap, G)), cam = fitView(b, view, Infinity, G);
    const a = toScreen(cam, view, b.x0, b.y0), z = toScreen(cam, view, b.x1, b.y1), i = view.inset, m = G.fit_margin_px;
    assert.ok(Math.abs(a.sx - (i.left + m)) < 1e-6 || Math.abs(a.sy - (i.top + m)) < 1e-6);
    void z;
  }), RUNS);
});

test("screen and world are each other's inverse", () => {
  fc.assert(fc.property(fc.record({ x: fc.double({ min: -1e3, max: 1e3, noNaN: true }), y: fc.double({ min: -1e3, max: 1e3, noNaN: true }), s: fc.double({ min: 0.01, max: 100, noNaN: true }) }), views,
    fc.double({ min: -1e3, max: 1e3, noNaN: true }), fc.double({ min: -1e3, max: 1e3, noNaN: true }), (cam, view, x, y) => {
      const p = toScreen(cam, view, x, y), q = toWorld(cam, view, p.sx, p.sy);
      assert.ok(Math.abs(q.x - x) < 1e-6 * (1 + Math.abs(x)) && Math.abs(q.y - y) < 1e-6 * (1 + Math.abs(y)));
    }), RUNS);
});

test("a flight starts where it was and ends where it goes, its scale between the two all the way", () => {
  const cam = fc.record({ x: fc.double({ min: -1e3, max: 1e3, noNaN: true }), y: fc.double({ min: -1e3, max: 1e3, noNaN: true }), s: fc.double({ min: 0.01, max: 100, noNaN: true }) });
  fc.assert(fc.property(cam, cam, fc.double({ min: 0, max: 1, noNaN: true }), (a, b, k) => {
    const same = (p, q) => Math.abs(p.x - q.x) < 1e-6 && Math.abs(p.y - q.y) < 1e-6 && Math.abs(p.s - q.s) < 1e-9 * (1 + q.s);
    assert.ok(same(flyCamera(a, b, 0), a) && same(flyCamera(a, b, 1), b));
    const s = flyCamera(a, b, k).s;
    assert.ok(s >= Math.min(a.s, b.s) - 1e-9 && s <= Math.max(a.s, b.s) + 1e-9);
  }), RUNS);
});

const box = fc.record({ x: fc.integer({ min: 0, max: 1200 }), y: fc.integer({ min: 0, max: 800 }), w: fc.integer({ min: 4, max: 300 }), h: fc.integer({ min: 4, max: 80 }) });
test("a segment and a box: Liang-Barsky agrees with sampling along the segment", () => {
  fc.assert(fc.property(box, fc.integer({ min: -100, max: 1400 }), fc.integer({ min: -100, max: 900 }), fc.integer({ min: -100, max: 1400 }), fc.integer({ min: -100, max: 900 }), (o, x1, y1, x2, y2) => {
    const sampled = Array.from({ length: 2001 }, (_, k) => k / 2000).some((t) => inBox(x1 + t * (x2 - x1), y1 + t * (y2 - y1), o));
    if (sampled) assert.ok(crosses(x1, y1, x2, y2, o), "a sampled point inside is a crossing");
  }), RUNS);
});

test("a segment and a circle meet exactly when the segment's nearest point is inside", () => {
  fc.assert(fc.property(fc.integer({ min: 0, max: 800 }), fc.integer({ min: 0, max: 800 }), fc.integer({ min: 1, max: 100 }), fc.integer({ min: 0, max: 800 }), fc.integer({ min: 0, max: 800 }), fc.integer({ min: 0, max: 800 }), fc.integer({ min: 0, max: 800 }), (sx, sy, r, x1, y1, x2, y2) => {
    const c = { sx, sy, r }, sampled = Array.from({ length: 4001 }, (_, k) => k / 4000).some((t) => Math.hypot(x1 + t * (x2 - x1) - sx, y1 + t * (y2 - y1) - sy) < r - 0.5);
    if (sampled) assert.ok(segmentMeetsCircle(x1, y1, x2, y2, c));
    if (Math.hypot(x1 - sx, y1 - sy) > r + 1 && Math.hypot(x2 - sx, y2 - sy) > r + 1 && !Array.from({ length: 4001 }, (_, k) => k / 4000).some((t) => Math.hypot(x1 + t * (x2 - x1) - sx, y1 + t * (y2 - y1) - sy) < r + 0.5)) assert.ok(!segmentMeetsCircle(x1, y1, x2, y2, c));
  }), RUNS);
});

test("a leader ends on its box, at the box's point nearest the mark", () => {
  fc.assert(fc.property(box, fc.integer({ min: -100, max: 1400 }), fc.integer({ min: -100, max: 900 }), (b, x, y) => {
    const e = nearestOnBox(x, y, b);
    assert.ok(inBox(e.x, e.y, b));
    for (const [px, py] of [[b.x, b.y], [b.x + b.w, b.y + b.h], [b.x + b.w / 2, b.y]]) assert.ok(Math.hypot(e.x - x, e.y - y) <= Math.hypot(px - x, py - y) + 1e-9);
  }), RUNS);
});

/** Islands on a screen as the Universe draws them: packed, fitted, as screen discs. */
const screens = fc.tuple(fc.array(fc.integer({ min: 1, max: 300 }), { minLength: 1, maxLength: 20 }), fc.constantFrom([1440, 900], [1024, 768]), fc.boolean())
  .map(([runs, [w, h], keyOpen]) => {
    const view = { w, h, inset: viewInset(keyOpen, G) }, slots = islandSlots(runs.length, G);
    const drawn = slots.map((p, k) => ({ ...p, r: islandRadius(runs[k], G) })), cam = fitView(bounds(drawn.map((c) => footprint(c.x, c.y, c.r, G))), view, G.island_scale_max, G);
    return { view, runs, cam, discs: drawn.map((c) => ({ ...toScreen(cam, view, c.x, c.y), r: c.r * cam.s })) };
  });

test("islands never meet on screen, the gap between any two at least island_gap at the camera's scale", () => {
  fc.assert(fc.property(screens, ({ discs, cam }) => {
    for (let i = 0; i < discs.length; i++) for (let j = i + 1; j < discs.length; j++) assert.ok(Math.hypot(discs[i].sx - discs[j].sx, discs[i].sy - discs[j].sy) >= discs[i].r + discs[j].r + G.island_gap * cam.s - 1e-6);
  }), RUNS);
});

test("the store fills the view: the islands' box covers at least 70% of the view's short side, at every size, a few islands too", () => {
  fc.assert(fc.property(screens, ({ view, cam, runs }) => {
    const slots = islandSlots(runs.length, G), b = bounds(slots.map((p, k) => footprint(p.x, p.y, islandRadius(runs[k], G), G)));
    const a = toScreen(cam, view, b.x0, b.y0), z = toScreen(cam, view, b.x1, b.y1);
    assert.ok(Math.max(z.sx - a.sx, z.sy - a.sy) >= 0.7 * Math.min(view.w, view.h), JSON.stringify([runs.length, z.sx - a.sx, z.sy - a.sy, view.w, view.h]));
  }), RUNS);
});

test("names: each clear one is inside the view, on no disc, no other name and not the key; under its disc by default", () => {
  fc.assert(fc.property(screens, fc.array(fc.integer({ min: 2, max: 30 }), { minLength: 20, maxLength: 20 }), ({ view, discs }, chars) => {
    const key = keyBox(5, view, G), out = placeLabels(discs, chars.slice(0, discs.length).map((n) => labelWidth(n, n, G)), [key], view, G);
    out.forEach((b, i) => {
      if (!b.clear) return;
      assert.ok(insideView(b, view) && !overlaps(b, key));
      assert.ok(discs.every((d, j) => j === i || !boxMeetsCircle(b, d)));
      out.forEach((o, j) => { if (j < i) assert.ok(!overlaps(b, o), `name ${i} on name ${j}`); });
    });
    assert.equal(out.length, discs.length);
  }), RUNS);
});

/** Each anchor's own island: an anchor at fraction `f` of island `k`'s radius, at angle `a`. */
const anchorsOn = (discs, picks) => picks.map(([k, f, a], n) => { const d = discs[k % discs.length]; return { id: `n${n}`, island: k % discs.length, sx: d.sx + f * d.r * Math.cos(a), sy: d.sy + f * d.r * Math.sin(a) }; });
const picks = fc.array(fc.tuple(fc.nat(19), fc.double({ min: 0, max: 0.95, noNaN: true }), fc.double({ min: 0, max: 6.3, noNaN: true })), { minLength: 1, maxLength: 6 });

test("callouts: none on another, a name, the key or any island's disc, all inside the view, each led from its mark crossing no name, callout, leader or other island", () => {
  fc.assert(fc.property(screens, picks, ({ view, discs }, ps) => {
    const anchors = anchorsOn(discs, ps), names = placeLabels(discs, discs.map(() => labelWidth(10, 18, G)), [], view, G), key = keyBox(5, view, G), hard = [...names, key];
    const out = placeCallouts(anchors, hard, discs, view, G);
    out.forEach((p, i) => {
      const b = { x: p.x, y: p.y, w: p.w, h: p.h }, a = anchors.find((x) => x.id === p.id);
      assert.ok(insideView(b, view), "inside the view");
      assert.ok(hard.every((o) => !overlaps(b, o)), "on no name and not the key");
      assert.ok(discs.every((c) => !boxMeetsCircle(b, c)), "over no island's disc, its own included");
      anchors.forEach((o) => { if (o.id !== p.id) assert.ok(!boxMeetsCircle(b, { ...o, r: G.callout_gap }), "on no other callout's mark"); });
      out.forEach((o, j) => { if (j !== i) assert.ok(!overlaps(b, { x: o.x, y: o.y, w: o.w, h: o.h }), "on no other callout"); });
      assert.deepEqual([p.ax, p.ay], [a.sx, a.sy], "led from its mark");
      assert.ok(inBox(p.lx, p.ly, b), "to its box");
      assert.ok(hard.every((o) => inBox(a.sx, a.sy, o) || !crosses(a.sx, a.sy, p.lx, p.ly, o)), "crossing no name");
      assert.ok(discs.every((c, k) => k === a.island || !segmentMeetsCircle(a.sx, a.sy, p.lx, p.ly, c)), "crossing no other island");
      out.forEach((o, j) => { if (j !== i) {
        assert.ok(!segmentsMeet(a.sx, a.sy, p.lx, p.ly, o.ax, o.ay, o.lx, o.ly) || (a.sx === o.ax && a.sy === o.ay), "crossing no other leader");
        assert.ok(inBox(a.sx, a.sy, o) || !crosses(a.sx, a.sy, p.lx, p.ly, o), "crossing no other callout");
      } });
    });
  }), RUNS);
});

test("a leader is short: at most callout_reach times its island's radius, and inside its own island at most callout_slack times the shortest way out", () => {
  fc.assert(fc.property(screens, picks, ({ view, discs }, ps) => {
    const anchors = anchorsOn(discs, ps);
    for (const p of placeCallouts(anchors, [], discs, view, G)) {
      const a = anchors.find((x) => x.id === p.id), own = discs[a.island];
      assert.ok(Math.hypot(p.lx - p.ax, p.ly - p.ay) <= G.callout_reach * own.r + 1e-6, "within reach");
      const out = own.r - Math.hypot(a.sx - own.sx, a.sy - own.sy);
      assert.ok(insideLength(p.ax, p.ay, p.lx, p.ly, own) <= G.callout_slack * out + 1e-6, "straight out of its island");
    }
  }), RUNS);
});

test("with no free box within reach a run gets no callout (the +N more counts it); the rest are still placed", () => {
  const view = { w: 1440, h: 900, inset: viewInset(false, G) }, own = { sx: 700, sy: 450, r: 40 };
  // a wall all around the island, the reach's width: nothing free near it
  const wall = [{ x: 0, y: 0, w: 1440, h: 412 }, { x: 0, y: 520, w: 1440, h: 380 }, { x: 0, y: 412, w: 630, h: 108 }, { x: 770, y: 412, w: 670, h: 108 }];
  assert.deepEqual(placeCallouts([{ id: "a", island: 0, sx: 700, sy: 450 }], wall, [own], view, G), []);
  const pen = { x: 200, y: 100, w: 200, h: 200 };
  const two = placeCallouts([{ id: "b", island: 1, sx: 300, sy: 200 }, { id: "a", island: 0, sx: 700, sy: 450 }], [...wall.slice(1), pen], [own, { sx: 300, sy: 200, r: 20 }], view, G);
  assert.deepEqual(two.map((p) => p.id), ["a"], "b's island is walled in; a's has room above");
});

test("callouts stop at `max` placed: an anchor that has no room gives its place to the next", () => {
  const view = { w: 1440, h: 900, inset: viewInset(false, G) }, pen = { x: 200, y: 100, w: 200, h: 200 };
  const A = [{ id: "a", island: 0, sx: 300, sy: 200 }, { id: "b", island: 1, sx: 900, sy: 450 }, { id: "c", island: 2, sx: 900, sy: 700 }];
  const discs = [{ sx: 300, sy: 200, r: 20 }, { sx: 900, sy: 450, r: 30 }, { sx: 900, sy: 700, r: 30 }];
  assert.deepEqual(placeCallouts(A, [pen], discs, view, G, { max: 1 }).map((p) => p.id), ["b"], "a is walled in: b takes the one place");
  assert.deepEqual(placeCallouts(A, [pen], discs, view, G, { max: 2 }).map((p) => p.id), ["b", "c"]);
  assert.deepEqual(placeCallouts(A, [pen], discs, view, G).map((p) => p.id), ["b", "c"], "no max: every one that fits");
  assert.deepEqual(placeCallouts(A, [], discs, view, G, { max: 0 }), []);
});

test("a leader's length inside a circle: the part of the segment within it", () => {
  const c = { sx: 0, sy: 0, r: 10 };
  assert.equal(insideLength(0, 0, 20, 0, c), 10);
  assert.equal(insideLength(-20, 0, 20, 0, c), 20);
  assert.equal(insideLength(15, 0, 20, 0, c), 0);
  assert.equal(insideLength(-5, 0, 5, 0, c), 10, "wholly inside");
  assert.ok(Math.abs(insideLength(0, 5, 20, 5, c) - Math.sqrt(75)) < 1e-12);
  assert.equal(insideLength(0, 20, 5, 20, c), 0, "missing it");
  assert.equal(insideLength(3, 4, 3, 4, c), 0, "a point");
  fc.assert(fc.property(fc.double({ min: -30, max: 30, noNaN: true }), fc.double({ min: -30, max: 30, noNaN: true }), fc.double({ min: -30, max: 30, noNaN: true }), fc.double({ min: -30, max: 30, noNaN: true }), (ax, ay, bx, by) => {
    const L = insideLength(ax, ay, bx, by, c), len = Math.hypot(bx - ax, by - ay);
    let n = 0; const N = 2000;
    for (let k = 0; k < N; k++) { const t = (k + 0.5) / N; if (Math.hypot(ax + t * (bx - ax), ay + t * (by - ay)) < 10) n++; }
    assert.ok(Math.abs(L - (n / N) * len) <= len * 2 / N + 1e-9, `${L} vs ${(n / N) * len}`);
  }), RUNS);
});

test("two segments meet when they cross or touch, not when they only share their start", () => {
  assert.ok(segmentsMeet(0, 0, 10, 10, 0, 10, 10, 0));
  assert.ok(!segmentsMeet(0, 0, 10, 0, 0, 1, 10, 1));
  assert.ok(segmentsMeet(0, 0, 10, 0, 5, 0, 5, 5), "an end on the other counts");
  assert.ok(!segmentsMeet(0, 0, 10, 0, 0, 0, 0, 10), "two leaders from one mark");
  assert.ok(!segmentsMeet(0, 0, 4, 0, 5, -1, 5, 1));
});

test("callouts' boxes: right of the mark, then left, then the shortest leader around the obstacles; exact boxes", () => {
  const view = { w: 1440, h: 900, inset: viewInset(false, G) }, w = G.callout_w, h = G.callout_px, gap = G.callout_gap, step = Math.max(G.callout_step_px, Math.hypot(1440, 900) / 40), A = [{ id: "a", island: 0, sx: 700, sy: 450 }];
  // no island to bound these: the reach is the view's diagonal, the boxes a fortieth of it apart
  const right = { x: 700 + gap, y: 450 - h / 2, w, h }, left = { x: 700 - gap - w, y: 450 - h / 2, w, h };
  const at = (out) => [out[0].x, out[0].y];
  assert.deepEqual(at(placeCallouts(A, [], [], view, G)), [right.x, right.y], "right of its mark, centred on it");
  assert.deepEqual(at(placeCallouts(A, [right], [], view, G)), [left.x, left.y], "right taken: left");
  // both taken: the nearest row clear of them, `step` px at a time, its leader the shortest (here up, on the right)
  const j = Math.ceil(h / step);
  assert.deepEqual(at(placeCallouts(A, [right, left], [], view, G)), [right.x - step, 450 - h / 2 - j * step], "both taken: just above them, across the mark");
  // its own island all around the mark: the nearest box off it, the leader straight out and as short as the boxes allow
  const own = { sx: 700, sy: 450, r: 2 * gap + w - 1 }, [o] = placeCallouts(A, [], [own], view, G), len = Math.hypot(o.lx - 700, o.ly - 450);
  const fine = Math.max(G.callout_step_px, G.callout_reach * own.r / 40);
  assert.ok(!boxMeetsCircle(o, own) && len >= own.r - 1e-9 && len <= own.r + fine + gap, JSON.stringify(o));
  // a mark near its island's top: the callout goes above, not across the island
  const top = placeCallouts([{ id: "t", island: 0, sx: 700, sy: 330 }], [], [{ sx: 700, sy: 450, r: 130 }], view, G)[0];
  assert.ok(top.y + top.h <= 450 - 130 + 1e-9 && Math.hypot(top.lx - 700, top.ly - 330) <= G.callout_reach * 130, JSON.stringify(top));
  // a box never covers its own mark
  for (const c of calloutBoxes(A[0], 100, G).slice(0, 3)) assert.ok(c.len >= gap);
  // a soft circle at the mark itself (its own mark, zoomed in) does not count; one covering every near box, the nearest anyway
  assert.deepEqual(at(placeCallouts(A, [], [], view, G, { soft: [{ sx: 700, sy: 450, r: gap + 2 }] })), [right.x, right.y], "its own mark is not in its way");
  assert.deepEqual(at(placeCallouts(A, [], [], view, G, { soft: [{ sx: 701, sy: 450, r: 5000 }] })), [right.x, right.y], "no box clear of the soft circles: the nearest anyway");
  assert.deepEqual(at(placeCallouts(A, [], [], view, G, { soft: [{ sx: 760, sy: 450, r: 20 }] })), [left.x, left.y], "a soft circle on the right: the left");
  // with no island to bound it, the nearest free box anywhere in the view, here below a wall
  const [far] = placeCallouts([{ id: "a", island: 0, sx: 700, sy: 100 }], [{ x: 0, y: 0, w: 1440, h: 700 }], [], view, G);
  const coarse = Math.hypot(1440, 900) / 40;
  assert.ok(far.y >= 700 && far.y < 700 + coarse && Math.abs(far.lx - 700) <= coarse, JSON.stringify(far));
  assert.deepEqual(placeCallouts([{ id: "a", island: 0, sx: 20, sy: 100 }], [{ x: 0, y: 0, w: 1440, h: 900 }], [], view, G), [], "no free box: not placed");
});

test("callouts' boxes are nearest first, each its nearest point and length, `step` apart, out to the reach", () => {
  fc.assert(fc.property(fc.integer({ min: 0, max: 1400 }), fc.integer({ min: 0, max: 900 }), fc.double({ min: 5, max: 900, noNaN: true }), (sx, sy, reach) => {
    const out = calloutBoxes({ sx, sy }, reach, G), step = Math.max(G.callout_step_px, reach / 40);
    out.forEach((c, i) => {
      assert.deepEqual(c.e, nearestOnBox(sx, sy, c.b)); assert.equal(c.len, Math.hypot(c.e.x - sx, c.e.y - sy));
      assert.deepEqual([c.b.w, c.b.h], [G.callout_w, G.callout_px]);
      if (i) assert.ok(out[i - 1].len <= c.len);
      assert.ok(c.len >= G.callout_gap, "never over its own mark");
    });
    assert.ok(out.some((c) => c.len > reach), "out to the reach and past it");
    assert.deepEqual([out[0].b.x, out[0].b.y], [sx + G.callout_gap, sy - G.callout_px / 2], "first: right of the mark");
    assert.ok(out.length <= (2 * Math.ceil(reach / step) + 2 + Math.ceil((G.callout_w + 2 * G.callout_gap) / step)) * (2 * Math.ceil((reach + G.callout_px) / step) + 1));
  }), RUNS);
});

test("at an island's zoom the callouts sit outside its disc on the side nearest each mark, stacked without overlap", () => {
  const view = { w: 1440, h: 900, inset: viewInset(true, G, true) }, c = { sx: 760, sy: 470, r: 260 };
  fc.assert(fc.property(fc.array(fc.tuple(fc.double({ min: 0.3, max: 0.95, noNaN: true }), fc.double({ min: 0, max: 6.28, noNaN: true })), { minLength: 1, maxLength: 6 }), (ps) => {
    const anchors = ps.map(([f, a], n) => ({ id: `n${n}`, island: 0, sx: c.sx + f * c.r * Math.cos(a), sy: c.sy + f * c.r * Math.sin(a) }));
    const out = placeCallouts(anchors, [keyBox(5, view, G)], [c], view, G);
    for (const p of out) {
      const a = anchors.find((x) => x.id === p.id), side = (x, y) => Math.sign((x - c.sx) * (a.sx - c.sx) + (y - c.sy) * (a.sy - c.sy));
      assert.ok(!boxMeetsCircle(p, c), "outside the disc");
      assert.ok(side(p.lx, p.ly) > 0, "on the mark's side of the island");
      assert.ok(insideLength(p.ax, p.ay, p.lx, p.ly, c) <= G.callout_slack * (c.r - Math.hypot(a.sx - c.sx, a.sy - c.sy)) + 1e-6);
      for (const o of out) if (o !== p) assert.ok(!overlaps(p, o));
    }
  }), RUNS);
});

test("each island's anchors nearest its rim first, each island keeping its turns, in their order on a tie", () => {
  const discs = [{ sx: 0, sy: 0, r: 100 }, { sx: 500, sy: 0, r: 60 }];
  fc.assert(fc.property(fc.array(fc.tuple(fc.nat(1), fc.double({ min: 0, max: 0.99, noNaN: true }), fc.double({ min: 0, max: 6.28, noNaN: true })), { maxLength: 12 }), (ps) => {
    const as = ps.map(([k, f, t], n) => ({ id: `a${n}`, island: k, sx: discs[k].sx + f * discs[k].r * Math.cos(t), sy: discs[k].sy + f * discs[k].r * Math.sin(t) })), out = rimFirst(as, discs);
    assert.deepEqual(out.map((a) => a.island), as.map((a) => a.island), "each island keeps its turns");
    assert.deepEqual(out.map((a) => a.id).sort(), as.map((a) => a.id).sort(), "the same anchors");
    const gap = (a) => discs[a.island].r - Math.hypot(a.sx - discs[a.island].sx, a.sy - discs[a.island].sy);
    for (const k of [0, 1]) { const mine = out.filter((a) => a.island === k); mine.forEach((a, i) => { if (i) assert.ok(gap(mine[i - 1]) <= gap(a) + 1e-12); }); }
  }), RUNS);
  const one = [{ id: "x", island: 0, sx: 50, sy: 0 }, { id: "o", island: 1, sx: 500, sy: 0 }, { id: "y", island: 0, sx: 0, sy: 50 }, { id: "z", island: 0, sx: 90, sy: 0 }];
  assert.deepEqual(rimFirst(one, discs).map((a) => a.id), ["z", "o", "x", "y"]);
});

test("callouts with no island under them still keep off each other's marks and leaders", () => {
  const view = { w: 1440, h: 900, inset: viewInset(false, G) };
  const pts = fc.array(fc.tuple(fc.integer({ min: 60, max: 1380 }), fc.integer({ min: 60, max: 840 })), { minLength: 2, maxLength: 8 });
  fc.assert(fc.property(pts, (ps) => {
    const anchors = ps.map(([sx, sy], n) => ({ id: `n${n}`, island: n, sx, sy })), out = placeCallouts(anchors, [], [], view, G);
    for (const p of out) {
      anchors.forEach((o) => { if (o.id !== p.id) assert.ok(!boxMeetsCircle(p, { ...o, r: G.callout_gap }), "on another's mark"); });
      for (const o of out) if (o !== p) assert.ok(!segmentsMeet(p.ax, p.ay, p.lx, p.ly, o.ax, o.ay, o.lx, o.ly) || (p.ax === o.ax && p.ay === o.ay), "leaders cross");
    }
  }), RUNS);
  const two = placeCallouts([{ id: "a", island: 0, sx: 700, sy: 450 }, { id: "b", island: 1, sx: 700 + G.callout_gap + 20, sy: 450 }], [], [], view, G);
  assert.ok(!boxMeetsCircle(two[0], { sx: 700 + G.callout_gap + 20, sy: 450, r: G.callout_gap }));
});

test("slot 0 is centred on the origin with its name's band; slots are the same objects when asked again", () => {
  assert.deepEqual(islandSlots(1, G)[0], { x: 0, y: -G.label_world / 2 });
  assert.equal(islandSlots(5, G), islandSlots(5, G));
});

test("a label goes left when right leaves the area it must stay in; none when nothing fits it", () => {
  const one = new Map([["X", { x: 100, y: 0, depth: 0 }]]), w = new Map([["X", 30]]), SZ = { ring: 10, node: 4, gap: 2, run: 5, label_h: 10, cap: G.loop_cap };
  const inside = { x: 0, y: -50, w: 106 + 30 - 1, h: 100 };
  assert.deepEqual(placeTags(one, w, SZ).get("X"), { x: 106, y: -5, w: 30, h: 10 });
  assert.deepEqual(placeTags(one, w, SZ, [], inside).get("X"), { x: 100 - 6 - 30, y: -5, w: 30, h: 10 });
  assert.deepEqual(placeTags(one, w, SZ, [], { x: 0, y: -50, w: 136, h: 100 }).get("X"), { x: 106, y: -5, w: 30, h: 10 }, "edges included");
  assert.deepEqual(placeTags(one, w, SZ, [], { x: 85, y: -50, w: 30, h: 100 }).get("X"), { x: 85, y: 6, w: 30, h: 10 }, "too narrow either side: below");
  assert.equal(placeTags(one, w, SZ, [], { x: 85, y: -15, w: 30, h: 26 }).get("X"), null, "no room at all");
  assert.equal(placeTags(one, w, SZ, [], { x: 85, y: -16, w: 30, h: 27 }).get("X").y, -16, "above");
  const g = claimGraph("X", [{ id: "X" }], [], w, SZ), b = graphBox(g, SZ);
  assert.deepEqual(b, graphBox(g, SZ, [], 0), "no extra boxes, no padding by default");
});

test("a label keeps off every other node's link box (`hit` across half, a square), not only its drawn disc", () => {
  // B's disc (r 7) clears the first try's box (its bottom at 8, B's disc top at 9.5); B's link box (half 9) does not (top 7.5)
  const at = new Map([["A", { x: 100, y: 0, depth: 0 }], ["B", { x: 133, y: 16.5, depth: 0 }]]), w = new Map([["A", 40], ["B", 0]]);
  const SZ = { ring: 10, node: 7, gap: 6, run: 1, label_h: 16, cap: G.loop_cap };
  const first = { x: 113, y: -8, w: 40, h: 16 };
  assert.deepEqual(placeTags(at, w, SZ).get("A"), first, "without a hit box the disc decides");
  const a = placeTags(at, w, { ...SZ, hit: 9 }).get("A");
  assert.notDeepEqual(a, first);
  assert.ok(!overlaps(a, { x: 133 - 9, y: 16.5 - 9, w: 18, h: 18 }), "clear of B's link box");
});

test("a pulse ring reaches its scale times its mark, stopping short of every name", () => {
  fc.assert(fc.property(fc.integer({ min: 0, max: 1400 }), fc.integer({ min: 0, max: 900 }), fc.double({ min: 0.5, max: 12, noNaN: true }), fc.array(box, { maxLength: 6 }), (sx, sy, r, names) => {
    const reach = pulseRadius({ sx, sy }, r, names, G);
    assert.ok(reach <= G.pulse_scale * r + 1e-9);
    for (const o of names) assert.ok(!boxMeetsCircle(o, { sx, sy, r: reach - 1e-6 }) || inBox(sx, sy, o));
  }), RUNS);
  assert.equal(pulseRadius({ sx: 10, sy: 10 }, 4, [], G), G.pulse_scale * 4);
});

test("the key sits in the right column the view leaves it, above the bottom bar", () => {
  fc.assert(fc.property(views, fc.integer({ min: 1, max: 9 }), (view, rows) => {
    const k = keyBox(rows, { ...view, inset: viewInset(true, G) }, G);
    assert.ok(k.x >= view.w - viewInset(true, G).right && k.x + k.w <= view.w && k.y + k.h <= view.h - G.hud_bottom_px + 1e-9);
    assert.equal(k.h, G.key_head_px + rows * G.key_row_px);
  }), RUNS);
});

const SIZE = { ring: G.orbit_ring, node: G.claim_px, gap: G.claim_gap_px, run: G.run_px + G.focus_px, label_h: G.claim_label_px, cap: G.loop_cap };
/** A run's claims and edges: n claims, edges only from a lower id to a higher one (the support graph is acyclic). */
const graphs = fc.integer({ min: 1, max: 30 }).chain((n) => fc.tuple(fc.constant(n), fc.array(fc.tuple(fc.nat(n - 1), fc.nat(n - 1)), { maxLength: 40 }), fc.array(fc.integer({ min: 2, max: 30 }), { minLength: 30, maxLength: 30 })))
  .map(([n, pairs, chars]) => ({ claims: Array.from({ length: n }, (_, i) => ({ id: `C${i}` })), edges: pairs.filter(([a, b]) => a < b).map(([a, b]) => ({ from: `C${a}`, to: `C${b}` })),
    widths: new Map(Array.from({ length: n }, (_, i) => [`C${i}`, chars[i] * G.claim_char_px + G.claim_pad_px])) }));

test("a run's claims orbit it: every claim once, the root on the first ring, a supporter further out than what it supports, no two nodes overlapping", () => {
  fc.assert(fc.property(graphs, ({ claims, edges }) => {
    const { at, rings } = orbit("C0", claims, edges, SIZE);
    assert.deepEqual([...at.keys()].sort(), claims.map((c) => c.id).sort());
    assert.ok(Math.abs(Math.hypot(at.get("C0").x, at.get("C0").y) - rings[0]) < 1e-6 && rings[0] >= SIZE.ring);
    for (const e of edges) if (at.get(e.to).depth > at.get(e.from).depth) assert.ok(Math.hypot(at.get(e.to).x, at.get(e.to).y) > Math.hypot(at.get(e.from).x, at.get(e.from).y));
    rings.forEach((r, d) => assert.ok(r >= (d + 1) * SIZE.ring && (!d || r >= rings[d - 1] + SIZE.ring)));
    const ps = [...at.values()];
    for (let i = 0; i < ps.length; i++) for (let j = i + 1; j < ps.length; j++) assert.ok(Math.hypot(ps[i].x - ps[j].x, ps[i].y - ps[j].y) >= 2 * SIZE.node + SIZE.gap - 1e-6, `nodes ${i} and ${j}`);
    for (const p of ps) assert.ok(Math.hypot(p.x, p.y) >= SIZE.run + SIZE.node, "off the run");
  }), RUNS);
});

test("no edge crosses the run's disc: an edge whose straight line would is routed around it, from its claim to its claim (L12)", () => {
  for (const size of [SIZE, { ring: G.sheet_ring, node: G.sheet_node_px, gap: G.claim_gap_px / 2, run: G.sheet_run_px, label_h: G.sheet_label_px, cap: G.loop_cap }])
    fc.assert(fc.property(graphs, ({ claims, edges, widths }) => {
      const g = claimGraph("C0", claims, edges, widths, size), disc = { sx: 0, sy: 0, r: size.run + size.gap };
      for (const e of g.edges) {
        const pts = edgePath(g, e);
        assert.deepEqual([pts[0], pts.at(-1)], [g.at.get(e.from), g.at.get(e.to)].map((p) => ({ x: p.x, y: p.y })), "it joins its two claims");
        for (let k = 1; k < pts.length; k++) assert.ok(!segmentMeetsCircle(pts[k - 1].x, pts[k - 1].y, pts[k].x, pts[k].y, disc), `${e.from} ${e.to}`);
      }
    }), RUNS);
  // the run zoom of the demo (7 claims): C1 supports C3 across the run
  const at = (r, a) => ({ x: r * Math.cos(a), y: r * Math.sin(a) });
  const g = { at: new Map([["A", { ...at(100, 0), depth: 1 }], ["B", { ...at(100, Math.PI), depth: 1 }]]), edges: [{ from: "A", to: "B", via: [] }] };
  assert.equal(edgePath(g, g.edges[0]).length, 2, "a path is its edge's own points");
});

test("the claim graph: every edge drawn is a published edge, each once, every published edge between drawn claims drawn; no label on a node, the run, another label or the fixed boxes", () => {
  fc.assert(fc.property(graphs, ({ claims, edges, widths }) => {
    const word = { x: -40, y: SIZE.run, w: 80, h: G.word_px }, inside = { x: -700, y: -420, w: 1400, h: 840 };
    const g = claimGraph("C0", claims, [...edges, ...edges], widths, SIZE, [word], inside);
    const published = new Set(edges.map((e) => `${e.from} ${e.to}`));
    assert.equal(g.edges.length, published.size, "each published edge once (none dropped, none twice)");
    for (const e of g.edges) assert.ok(published.has(`${e.from} ${e.to}`), "only published edges");
    assert.equal(g.at.size, claims.length, "every claim drawn once");
    assert.equal(g.root, "C0");
    const placed = [...g.labels.entries()].filter(([, b]) => b);
    for (const [id, b] of placed) {
      assert.equal(b.w, widths.get(id)); assert.equal(b.h, SIZE.label_h);
      assert.ok(b.x >= inside.x && b.y >= inside.y && b.x + b.w <= inside.x + inside.w && b.y + b.h <= inside.y + inside.h, "inside the view");
      assert.ok(!overlaps(b, word) && !boxMeetsCircle(b, { sx: 0, sy: 0, r: SIZE.run }), "off the run and its word");
      for (const p of g.at.values()) assert.ok(!boxMeetsCircle(b, { sx: p.x, sy: p.y, r: SIZE.node }), `label ${id} on a node`);
      for (const [o, c] of placed) if (o !== id) assert.ok(!overlaps(b, c), `labels ${id} and ${o}`);
    }
  }), RUNS);
});

test("the claim graph's exact places: a shared supporter is one node with two edges; a label goes right, else left, below, above, else none", () => {
  const claims = ["A", "B", "C", "D"].map((id) => ({ id })), edges = [{ from: "A", to: "B" }, { from: "A", to: "C" }, { from: "B", to: "D" }, { from: "C", to: "D" }, { from: "B", to: "nope" }];
  const w = new Map(claims.map((c) => [c.id, 30])), g = claimGraph("A", claims, edges, w, SIZE);
  assert.deepEqual(g.edges.map((e) => `${e.from}>${e.to}`), ["A>B", "A>C", "B>D", "C>D"], "D drawn once, supporting B and C");
  const a = g.at.get("A"), la = g.labels.get("A");
  assert.deepEqual([la.x, la.y], [a.x + SIZE.node + SIZE.gap, a.y - SIZE.label_h / 2], "right of its node");
  const one = new Map([["X", { x: 100, y: 0, depth: 0 }]]);
  const right = placeTags(one, new Map([["X", 30]]), SIZE, [{ x: 100 + SIZE.node, y: -50, w: 100, h: 100 }]).get("X");
  assert.deepEqual([right.x, right.y], [100 - SIZE.node - SIZE.gap - 30, -SIZE.label_h / 2], "right taken: left");
  assert.equal(placeTags(one, new Map([["X", 30]]), SIZE, [{ x: 0, y: -100, w: 300, h: 200 }]).get("X"), null, "no clear place: no label");
  assert.equal(claimGraph("Z", [], null, new Map(), SIZE).root, null);
});

test("a run's attempts are a chain centred under it, one step apart, in order", () => {
  fc.assert(fc.property(fc.integer({ min: 1, max: 9 }), fc.double({ min: 10, max: 400, noNaN: true }), (n, below) => {
    const c = attemptChain(n, below, G.chain_step_px);
    assert.equal(c.length, n);
    c.forEach((p, i) => { assert.equal(p.y, below); if (i) assert.ok(Math.abs(p.x - c[i - 1].x - G.chain_step_px) < 1e-9); });
    assert.ok(Math.abs(c[0].x + c.at(-1).x) < 1e-9, "centred");
  }), RUNS);
});

test("a graph's box holds its run, nodes and labels, padded", () => {
  fc.assert(fc.property(graphs, ({ claims, edges, widths }) => {
    const g = claimGraph("C0", claims, edges, widths, SIZE), b = graphBox(g, SIZE, [], 4);
    for (const p of g.at.values()) assert.ok(p.x - SIZE.node >= b.x + 4 - 1e-9 && p.y + SIZE.node <= b.y + b.h - 4 + 1e-9);
    for (const l of g.labels.values()) if (l) assert.ok(l.x >= b.x && l.x + l.w <= b.x + b.w + 1e-9);
    assert.ok(b.x <= -SIZE.run - 4 && b.y + b.h >= SIZE.run + 4);
  }), RUNS);
});

test("the project card sits top left under the bar, a head and a row per standing, and the camera keeps its column", () => {
  fc.assert(fc.property(views, fc.integer({ min: 1, max: 9 }), (view, rows) => {
    const inset = viewInset(false, G, true), k = cardBox(rows, { ...view, inset }, G);
    assert.ok(k.x + k.w <= inset.left && k.y >= inset.top && k.h === G.card_head_px + rows * G.key_row_px);
  }), RUNS);
});

test("the arrows move to the nearest item in their direction, and stay put at an edge", () => {
  const items = [{ id: "a", x: 0, y: 0 }, { id: "b", x: 10, y: 1 }, { id: "c", x: 30, y: 0 }, { id: "d", x: 1, y: 12 }, { id: "e", x: -9, y: -2 }];
  assert.equal(nearest(items, "a", "ArrowRight"), "b");
  assert.equal(nearest(items, "b", "ArrowRight"), "c");
  assert.equal(nearest(items, "c", "ArrowRight"), "c");
  assert.equal(nearest(items, "a", "ArrowDown"), "d");
  assert.equal(nearest(items, "a", "ArrowLeft"), "e");
  assert.equal(nearest(items, null, "ArrowRight"), "a", "nothing focused: the first item");
  fc.assert(fc.property(fc.array(fc.record({ x: fc.integer({ min: -50, max: 50 }), y: fc.integer({ min: -50, max: 50 }) }), { minLength: 1, maxLength: 12 }), fc.constantFrom("ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"), (pts, key) => {
    const its = pts.map((p, i) => ({ id: `i${i}`, ...p })), to = its.find((i) => i.id === nearest(its, "i0", key));
    const [ax, ay] = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] }[key];
    if (to.id !== "i0") assert.ok((to.x - its[0].x) * ax + (to.y - its[0].y) * ay > 0, "it moves the arrow's way");
  }), RUNS);
});

test("a footprint holds its disc and the band under it; packed footprints keep the middle of every name's band off the other islands", () => {
  fc.assert(fc.property(fc.array(fc.integer({ min: 1, max: 300 }), { minLength: 2, maxLength: 16 }), fc.double({ min: 0, max: 8, noNaN: true }), (runs, extra) => {
    const discs = packCircles(runs.map((n) => footprint(0, 0, islandRadius(n, G), G).r), G.island_gap, G)
      .map((f, k) => ({ x: f.x, y: f.y - G.label_world / 2, r: islandRadius(runs[k], G) }));
    const s = G.label_px / G.label_world + extra, view = { w: 1e6, h: 1e6, inset: viewInset(false, G) }, cam = { x: 0, y: 0, s };
    const shown = discs.map((c) => ({ ...toScreen(cam, view, c.x, c.y), r: c.r * s }));
    shown.forEach((d, i) => {
      const f = footprint(d.sx, d.sy, d.r, { ...G, label_world: G.label_world * s });
      assert.ok(Math.hypot(f.x - d.sx, f.y - d.sy) + d.r <= f.r + 1e-6, "the footprint holds the disc");
      // the band's middle column under the disc is on no other island (a wider name is placed by placeLabels, which moves
      // it above or beside its disc when the band is not clear)
      const band = { x: d.sx - 1, y: d.sy + d.r, w: 2, h: G.label_px };
      shown.forEach((o, j) => { if (j !== i) assert.ok(!boxMeetsCircle(band, o), `island ${i}'s band meets island ${j}`); });
    });
  }), RUNS);
});

// ---- exact oracles for the primitives (each against a plain restatement or a dense sampling, both directions) ------------
const grid = fc.integer({ min: -40, max: 40 });
const gbox = fc.record({ x: grid, y: grid, w: fc.integer({ min: 1, max: 30 }), h: fc.integer({ min: 1, max: 30 }) });

test("boxes overlap exactly when they share an interior point (touching edges do not)", () => {
  fc.assert(fc.property(gbox, gbox, (a, b) => {
    // integer boxes: they share an interior point iff some half-integer point is inside both
    let shared = false;
    for (let x = Math.max(a.x, b.x); x < Math.min(a.x + a.w, b.x + b.w) && !shared; x++) for (let y = Math.max(a.y, b.y); y < Math.min(a.y + a.h, b.y + b.h); y++) { shared = true; break; }
    assert.equal(overlaps(a, b), shared);
    assert.equal(overlaps(a, b), overlaps(b, a));
  }), RUNS);
  assert.equal(overlaps({ x: 0, y: 0, w: 10, h: 10 }, { x: 10, y: 0, w: 5, h: 5 }), false, "edge to edge");
  assert.equal(overlaps({ x: 0, y: 0, w: 10, h: 10 }, { x: 0, y: 10, w: 5, h: 5 }), false);
  // and the other way round: each of the four strict comparisons is a boundary (make mutate found the two unchecked)
  assert.equal(overlaps({ x: 10, y: 0, w: 5, h: 5 }, { x: 0, y: 0, w: 10, h: 10 }), false, "edge to edge, from the right");
  assert.equal(overlaps({ x: 0, y: 10, w: 5, h: 5 }, { x: 0, y: 0, w: 10, h: 10 }), false, "edge to edge, from below");
  assert.equal(overlaps({ x: 0, y: 0, w: 10, h: 10 }, { x: 9, y: 9, w: 5, h: 5 }), true);
});

test("a point is in a box when it is between its edges, the edges included", () => {
  fc.assert(fc.property(gbox, grid, grid, (o, x, y) => {
    assert.equal(inBox(x, y, o), x >= o.x && x <= o.x + o.w && y >= o.y && y <= o.y + o.h);
  }), RUNS);
  const o = { x: 0, y: 0, w: 4, h: 2 };
  for (const [x, y, want] of [[0, 0, true], [4, 2, true], [4.01, 1, false], [-0.01, 1, false], [2, 2.01, false], [2, -0.01, false]]) assert.equal(inBox(x, y, o), want, `${x},${y}`);
});

test("a box is inside the view exactly when all four of its edges are within the inset rectangle", () => {
  const view = { w: 100, h: 80, inset: { top: 10, right: 20, bottom: 5, left: 3 } };
  fc.assert(fc.property(fc.record({ x: fc.integer({ min: -5, max: 100 }), y: fc.integer({ min: -5, max: 80 }), w: fc.integer({ min: 1, max: 100 }), h: fc.integer({ min: 1, max: 80 }) }), (b) => {
    assert.equal(insideView(b, view), b.x >= 3 && b.y >= 10 && b.x + b.w <= 80 && b.y + b.h <= 75);
  }), RUNS);
  for (const [b, want] of [[{ x: 3, y: 10, w: 77, h: 65 }, true], [{ x: 2, y: 10, w: 1, h: 1 }, false], [{ x: 3, y: 9, w: 1, h: 1 }, false], [{ x: 3, y: 10, w: 78, h: 1 }, false], [{ x: 3, y: 10, w: 1, h: 66 }, false]]) assert.equal(insideView(b, view), want, JSON.stringify(b));
});

test("a box meets a circle exactly when its nearest point is closer than the radius", () => {
  fc.assert(fc.property(gbox, grid, grid, fc.integer({ min: 1, max: 30 }), (b, sx, sy, r) => {
    const nx = Math.min(Math.max(sx, b.x), b.x + b.w), ny = Math.min(Math.max(sy, b.y), b.y + b.h);
    assert.equal(boxMeetsCircle(b, { sx, sy, r }), Math.hypot(nx - sx, ny - sy) < r);
  }), RUNS);
  const b = { x: 0, y: 0, w: 10, h: 10 };
  for (const [sx, sy, r, want] of [[15, 5, 5, false], [15, 5, 5.01, true], [-5, 5, 5.01, true], [5, -5, 5, false], [5, 15, 5.01, true], [13, 14, 5, false], [13, 14, 5.01, true], [5, 5, 0.1, true]]) assert.equal(boxMeetsCircle(b, { sx, sy, r }), want, `${sx},${sy},${r}`);
});

test("a segment crosses a box exactly when some point of it is in the box (sampled densely, both ways)", () => {
  const along = (x1, y1, x2, y2, o) => { for (let k = 0; k <= 4000; k++) { const t = k / 4000; if (inBox(x1 + t * (x2 - x1), y1 + t * (y2 - y1), o)) return true; } return false; };
  fc.assert(fc.property(gbox, grid, grid, grid, grid, (o, x1, y1, x2, y2) => {
    const got = crosses(x1, y1, x2, y2, o), shrunk = { x: o.x + 0.01, y: o.y + 0.01, w: o.w - 0.02, h: o.h - 0.02 }, grown = { x: o.x - 0.01, y: o.y - 0.01, w: o.w + 0.02, h: o.h + 0.02 };
    if (along(x1, y1, x2, y2, shrunk)) assert.ok(got, "a point well inside: it crosses");
    if (!along(x1, y1, x2, y2, grown)) assert.ok(!got, "no point near: it does not");
    assert.equal(crosses(x2, y2, x1, y1, o), got, "either way along");
  }), { ...RUNS, numRuns: 300 });
  const o = { x: 0, y: 0, w: 10, h: 10 };
  for (const [s, want] of [[[-5, 5, -1, 5], false], [[-5, 5, 0, 5], true], [[11, -5, 11, 15], false], [[5, -5, 5, -1], false], [[5, 11, 5, 20], false], [[-5, -5, 15, 15], true], [[-5, 6, 6, -5], true], [[-5, 4, 4, -5], false], [[2, 2, 3, 3], true], [[5, 5, 5, 5], true], [[-1, -1, -1, -1], false]]) assert.equal(crosses(...s, o), want, JSON.stringify(s));
});

test("a segment meets a circle exactly when its nearest point to the centre is closer than the radius", () => {
  fc.assert(fc.property(grid, grid, grid, grid, grid, grid, fc.integer({ min: 1, max: 20 }), (x1, y1, x2, y2, sx, sy, r) => {
    const dx = x2 - x1, dy = y2 - y1, l = dx * dx + dy * dy, t = l ? Math.max(0, Math.min(1, ((sx - x1) * dx + (sy - y1) * dy) / l)) : 0;
    const want = Math.hypot(x1 + t * dx - sx, y1 + t * dy - sy) < r;
    assert.equal(segmentMeetsCircle(x1, y1, x2, y2, { sx, sy, r }), want);
  }), { ...RUNS, numRuns: 300 });
});

test("the nearest point of a box to a point is that point clamped into the box", () => {
  fc.assert(fc.property(gbox, grid, grid, (b, x, y) => {
    assert.deepEqual(nearestOnBox(x, y, b), { x: Math.min(Math.max(x, b.x), b.x + b.w), y: Math.min(Math.max(y, b.y), b.y + b.h) });
  }), RUNS);
});

test("a flight is the cubic ease-out of its share, the scale eased in its logarithm, clamped to 0..1", () => {
  const a = { x: 0, y: 10, s: 1 }, b = { x: 100, y: -10, s: 16 };
  const mid = flyCamera(a, b, 0.5), e = 1 - 0.5 ** 3;
  assert.ok(Math.abs(mid.x - 100 * e) < 1e-9 && Math.abs(mid.y - (10 - 20 * e)) < 1e-9 && Math.abs(mid.s - 16 ** e) < 1e-9);
  assert.deepEqual(flyCamera(a, b, -1), flyCamera(a, b, 0));
  assert.deepEqual(flyCamera(a, b, 2), flyCamera(a, b, 1));
});

test("bounds hold every circle exactly; no circles, the unit box", () => {
  fc.assert(fc.property(fc.array(fc.record({ x: grid, y: grid, r: fc.integer({ min: 0, max: 9 }) }), { minLength: 1, maxLength: 9 }), (cs) => {
    assert.deepEqual(bounds(cs), { x0: Math.min(...cs.map((c) => c.x - c.r)), y0: Math.min(...cs.map((c) => c.y - c.r)), x1: Math.max(...cs.map((c) => c.x + c.r)), y1: Math.max(...cs.map((c) => c.y + c.r)) });
  }), RUNS);
  assert.deepEqual(bounds([]), { x0: -1, y0: -1, x1: 1, y1: 1 });
});

test("the island and mark sizes are their formulas", () => {
  fc.assert(fc.property(fc.integer({ min: 0, max: 5000 }), fc.double({ min: 0.01, max: 50, noNaN: true }), (n, s) => {
    assert.equal(islandRadius(n, G), G.mark_c * Math.sqrt(Math.max(1, Math.min(n, G.slot_places))) + G.island_margin);
    assert.equal(markRadius("store", s, G), Math.min(G.dot_max_px, G.dot_share * G.mark_c * s));
    assert.equal(markRadius("project", s, G), Math.min(G.glyph_max_px, G.glyph_share * G.mark_c * s));
    assert.deepEqual(footprint(3, 4, n, G), { x: 3, y: 4 + G.label_world / 2, r: n + G.label_world / 2 });
  }), RUNS);
  assert.equal(islandRadius(0, G), islandRadius(1, G), "an empty island is one place");
});

test("a name's box is its width and label_px tall, centred under its disc label_gap_px below; its width is the wider line plus padding", () => {
  fc.assert(fc.property(grid, grid, fc.integer({ min: 1, max: 60 }), fc.integer({ min: 0, max: 40 }), fc.integer({ min: 0, max: 40 }), (sx, sy, r, a, b) => {
    const w = labelWidth(a, b, G);
    assert.equal(w, Math.max(a * G.label_char_px, b * G.label_sub_char_px) + G.label_pad_px);
    assert.deepEqual(labelBox({ sx, sy, r }, w, G), { x: sx - w / 2, y: sy + r + G.label_gap_px, w, h: G.label_px });
  }), RUNS);
});

test("a name goes under its disc, else above, else right, else left; with no clear place it stays under, not clear", () => {
  const view = { w: 1000, h: 800, inset: { top: 0, right: 0, bottom: 0, left: 0 } }, d = { sx: 500, sy: 400, r: 50 }, w = 80, h = G.label_px, gap = G.label_gap_px;
  const under = { x: 460, y: 450 + gap, w, h }, above = { x: 460, y: 350 - gap - h, w, h }, right = { x: 550 + gap, y: 400 - h / 2, w, h }, left = { x: 450 - gap - w, y: 400 - h / 2, w, h };
  const at = (fixed) => placeLabels([d], [w], fixed, view, G)[0];
  assert.deepEqual(at([]), { ...under, clear: true });
  assert.deepEqual(at([under]), { ...above, clear: true });
  assert.deepEqual(at([under, above]), { ...right, clear: true });
  assert.deepEqual(at([under, above, right]), { ...left, clear: true });
  assert.deepEqual(at([under, above, right, left]), { ...under, clear: false });
  // another island's disc under it, and a name placed before it, push it too
  const two = placeLabels([d, { sx: 500, sy: 450 + gap + h / 2, r: 10 }], [w, w], [], view, G);
  assert.deepEqual(two[0], { ...above, clear: true });
  const out = { w: 1000, h: 470, inset: view.inset };
  assert.deepEqual(placeLabels([d], [w], [], out, G)[0], { ...above, clear: true }, "under it leaves the view");
});

test("the key's box is key_w_px wide at the right, a head and a row per standing, its bottom on the bottom bar", () => {
  fc.assert(fc.property(fc.integer({ min: 300, max: 3000 }), fc.integer({ min: 300, max: 2000 }), fc.integer({ min: 0, max: 12 }), fc.boolean(), (w, h, rows, open) => {
    const view = { w, h, inset: viewInset(open, G) }, k = keyBox(rows, view, G), kh = G.key_head_px + rows * G.key_row_px;
    assert.deepEqual(k, { x: w - G.hud_pad_px - G.key_w_px, y: h - G.hud_bottom_px - kh, w: G.key_w_px, h: kh });
  }), RUNS);
  assert.deepEqual(viewInset(true, G), { top: G.hud_top_px, bottom: G.hud_bottom_px, left: 0, right: G.key_w_px + 2 * G.hud_pad_px });
  assert.deepEqual(viewInset(false, G), { top: G.hud_top_px, bottom: G.hud_bottom_px, left: 0, right: 0 });
});

test("the orbit: a claim's ring is its depth; supporters split their parent's wedge evenly, in order; claims off the root's tree take the ring past it", () => {
  const ten = { ring: 10, node: 1, gap: 0, cap: G.loop_cap };
  const { at, rings } = orbit("A", [{ id: "A" }, { id: "B" }, { id: "C" }, { id: "D" }, { id: "E" }], [{ from: "A", to: "B" }, { from: "A", to: "C" }, { from: "B", to: "D" }, { from: "B", to: "nope" }], ten);
  const polar = (p) => [Math.round(Math.hypot(p.x, p.y) * 1e6) / 1e6, Math.round(Math.atan2(p.y, p.x) * 1e6) / 1e6];
  const deg = (a) => Math.round((a * 1e6 * Math.PI) / 180) / 1e6;
  assert.deepEqual(rings, [10, 20, 30, 40]);
  assert.deepEqual(polar(at.get("A")), [10, deg(90)], "the root on the first ring, at the bottom (the middle of its whole turn)");
  assert.deepEqual(polar(at.get("B")), [20, deg(0)], "B: the first half of the turn, from the top");
  assert.deepEqual(polar(at.get("C")), [20, deg(180)]);
  assert.deepEqual(polar(at.get("D")), [30, deg(0)], "D: all of B's wedge");
  assert.deepEqual([at.get("A").depth, at.get("B").depth, at.get("D").depth, at.get("E").depth], [0, 1, 2, 3]);
  assert.deepEqual(polar(at.get("E")), [40, deg(90)], "off the root's tree: the ring past its deepest");
  const flat = orbit(null, [{ id: "x" }, { id: "y" }], [], ten).at;
  assert.deepEqual([polar(flat.get("x")), polar(flat.get("y"))], [[10, deg(0)], [10, deg(180)]], "no root: all on the first ring, evenly");
  assert.equal(orbit("A", [], null, ten).at.size, 0);
  // many on one ring: the ring moves out until neighbours keep 2·node + gap between them
  const many = orbit(null, Array.from({ length: 40 }, (_, i) => ({ id: `c${i}` })), [], { ring: 10, node: 5, gap: 2, cap: G.loop_cap });
  assert.ok(Math.abs(many.rings[0] - 12 / (2 * Math.sin(Math.PI / 40))) < 1e-9);
});

test("the arrows weigh distance off their axis twice and ignore what is more than 45 degrees off", () => {
  const its = [{ id: "o", x: 0, y: 0 }, { id: "far", x: 10, y: 0 }, { id: "near-off", x: 4, y: 3 }, { id: "steep", x: 2, y: 2.5 }];
  assert.equal(nearest(its, "o", "ArrowRight"), "far", "4 + 2·3 = 10 is not under 10: ties keep the first");
  assert.equal(nearest([...its, { id: "best", x: 5, y: 1 }], "o", "ArrowRight"), "best", "5 + 2 = 7");
  assert.equal(nearest([{ id: "o", x: 0, y: 0 }, { id: "steep", x: 2, y: 2.5 }], "o", "ArrowRight"), "o", "more than 45 degrees off: nowhere to go");
  assert.equal(nearest([{ id: "o", x: 0, y: 0 }, { id: "d", x: 2, y: 2 }], "o", "ArrowRight"), "d", "exactly 45 degrees counts");
  assert.equal(nearest([{ id: "o", x: 0, y: 0 }, { id: "u", x: 0, y: -3 }], "o", "ArrowUp"), "u");
  assert.equal(nearest([], null, "ArrowUp"), null);
});

test("every counted loop stops within its budget: a budget says yes `cap` times, then no; each loop asks it", () => {
  fc.assert(fc.property(fc.nat(50), (cap) => {
    const more = budget(cap); let n = 0;
    while (more()) n++;
    assert.equal(n, cap);
    assert.equal(more(), false, "and stays spent");
  }), RUNS);
  // with a budget too small for its input, each function still returns, having done at most its budget's steps
  const tiny = { ...G, loop_cap: 3 };
  assert.ok(Array.isArray(packCircles([1, 2, 3, 4, 5], 1, tiny)));
  assert.equal(placeLabels([{ sx: 0, sy: 0, r: 1 }, { sx: 9, sy: 9, r: 1 }, { sx: 20, sy: 0, r: 1 }, { sx: 30, sy: 0, r: 1 }], [10, 10, 10, 10], [], { w: 100, h: 100, inset: viewInset(false, G) }, tiny).length, 3);
  assert.ok(calloutBoxes({ sx: 0, sy: 0 }, 900, tiny).length <= 3, "a box per step at most");
  const deep = Array.from({ length: 9 }, (_, i) => ({ id: `c${i}` })), chain = deep.slice(1).map((c, i) => ({ from: deep[i].id, to: c.id }));
  assert.equal(orbit("c0", deep, chain, { ...SIZE, cap: 4 }).rings.length, 4, "rings: at most its budget");
  assert.equal(orbit("c0", deep, chain, SIZE).rings.length, 9);
  assert.ok(G.loop_cap >= (2 * 120 + 1) ** 2, "the budget holds the largest search a view asks (a 2,600 px view's callout grid)");
});

test("callouts' exact rules: a leader around another island, the soft fallback still free, the boxes' grid and its ties", () => {
  const view = { w: 1440, h: 900, inset: viewInset(false, G) }, w = G.callout_w, h = G.callout_px, gap = G.callout_gap;
  const own = { sx: 700, sy: 450, r: 1 }, A = [{ id: "a", island: 0, sx: 700, sy: 450 }];
  // a tiny island between the mark and the right box: its leader may not cross it, so the left
  const islands = [{ ...own, r: 4 }, { sx: 703, sy: 450, r: 1.5 }];
  assert.deepEqual(placeCallouts(A, [], islands.slice(0, 1), view, G).map((c) => c.x), [700 + gap], "alone: right");
  assert.deepEqual(placeCallouts(A, [], islands, view, G).map((c) => c.x), [700 - gap - w], "another island in the way: left");
  // every box on a soft circle, the right one taken: the nearest free one still (the left)
  const [q] = placeCallouts(A, [{ x: 700 + gap, y: 450 - h / 2, w, h }], [], view, G, { soft: [{ sx: 701, sy: 450, r: 5000 }] });
  assert.deepEqual([q.x, q.y], [700 - gap - w, 450 - h / 2]);
  // the grid: x right of the mark, left of it and across it, y above and below, `step` apart; ties in the order made
  fc.assert(fc.property(fc.integer({ min: 0, max: 1400 }), fc.integer({ min: 0, max: 900 }), fc.double({ min: 5, max: 600, noNaN: true }), (sx, sy, reach) => {
    const out = calloutBoxes({ sx, sy }, reach, G), step = Math.max(G.callout_step_px, reach / 40);
    const kx = Math.ceil(reach / step), ky = Math.ceil((reach + h) / step), across = Math.ceil((w + 2 * gap) / step);
    const xs = new Set(), ys = new Set();
    for (let k = 0; k <= kx; k++) { xs.add(sx + gap + k * step); xs.add(sx - gap - w - k * step); }
    for (let k = 1; k < across; k++) xs.add(sx + gap - k * step);
    for (let j = -ky; j <= ky; j++) ys.add(sy - h / 2 + j * step);
    const all = [...xs].flatMap((x) => [...ys].map((y) => [x, y])).filter(([x, y]) => { const e = nearestOnBox(sx, sy, { x, y, w, h }); return Math.hypot(e.x - sx, e.y - sy) >= gap; });
    const key = (x, y) => `${x.toFixed(6)} ${y.toFixed(6)}`;
    assert.deepEqual(new Set(out.map((c) => key(c.b.x, c.b.y))), new Set(all.map(([x, y]) => key(x, y))));
    assert.deepEqual([out[1].b.x, out[1].b.y], [sx - gap - w, sy - h / 2], "the left box second: a tie keeps the order made");
  }), RUNS);
});

test("rim first: islands away from the origin, and ties keep their order", () => {
  const discs = [{ sx: 0, sy: 0, r: 100 }, { sx: 500, sy: 300, r: 60 }];
  assert.deepEqual(rimFirst([{ id: "p", island: 1, sx: 510, sy: 300 }, { id: "q", island: 1, sx: 550, sy: 300 }], discs).map((a) => a.id), ["q", "p"]);
  const ring = [[50, 0], [0, 50], [-50, 0], [0, -50]].map(([sx, sy], n) => ({ id: "abcd"[n], island: 0, sx, sy }));
  assert.deepEqual(rimFirst(ring, discs).map((a) => a.id), ["a", "b", "c", "d"]);
  assert.ok(Math.abs(insideLength(90, 100, 130, 100, { sx: 100, sy: 100, r: 10 }) - 20) < 1e-12, "a circle off the origin");
  assert.ok(Math.abs(insideLength(100, 80, 100, 95, { sx: 100, sy: 100, r: 10 }) - 5) < 1e-9);
});
