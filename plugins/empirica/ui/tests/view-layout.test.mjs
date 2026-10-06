// VIEW.md R1–R3 as geometry, on the observer demo fixtures and on a store-shaped data set (many projects, runs without claims).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { apply } from "../app/filter.js";
import { U, attemptPath, viewLayout } from "../elements/core/view-layout.js";

const data = JSON.parse(readFileSync(new URL("../../vendor/contracts/empirica-ui-view/v1/fixtures/view.json", import.meta.url)));
const { filters } = JSON.parse(readFileSync(new URL("../catalogue.json", import.meta.url)));
const all = apply(data, {}, filters).projects;
// Store-shaped: 30 projects, most runs without claims (the real store: 30 projects, 57 runs, 39 without claims).
const many = Array.from({ length: 30 }, (_, i) => ({ id: `p${i}`, n: i + 1, runs: Array.from({ length: 1 + (i % 4) }, (_, j) => ({ key: `p${i}/r${j}/1`, previous: null, root: null, claims: [], edges: [] })) }));
many[3].runs[0] = all[0].runs[0];
const BOX = { width: 1000, height: 700 };
const inside = (a, b) => a.x >= b.x - 1e-6 && a.y >= b.y - 1e-6 && a.x + (a.w ?? 0) <= b.x + b.w + 1e-6 && a.y + (a.h ?? 0) <= b.y + b.h + 1e-6;
const apart = (a, b) => a.x + a.w <= b.x + 1e-6 || b.x + b.w <= a.x + 1e-6 || a.y + a.h <= b.y + 1e-6 || b.y + b.h <= a.y + 1e-6;

for (const [name, projects] of [["fixtures", all], ["store-shaped", many]]) {
  const L = viewLayout(projects, BOX);
  const runs = projects.flatMap((p) => p.runs);

  test(`${name}: R1 every project, run and claim has a place`, () => {
    assert.equal(L.projects.length, projects.length);
    assert.deepEqual(L.runs.map((r) => r.key), runs.map((r) => r.key));
    assert.equal(L.claims.length, runs.reduce((n, r) => n + r.claims.length, 0));
  });

  test(`${name}: R3 containment: runs inside their project, claims inside their run`, () => {
    const proj = new Map(L.projects.map((p) => [p.id, p])), run = new Map(L.runs.map((r) => [r.key, r]));
    for (const r of L.runs) assert.ok(inside(r, proj.get(r.project)), r.key);
    for (const c of L.claims) assert.ok(inside({ x: c.cx - U.claim / 2, y: c.cy - U.claim / 2, w: U.claim, h: U.claim }, run.get(c.run)), `${c.run} ${c.id}`);
  });

  test(`${name}: nothing overlaps: projects, runs, claims`, () => {
    for (const set of [L.projects, L.runs]) for (let i = 0; i < set.length; i++) for (let j = i + 1; j < set.length; j++) assert.ok(apart(set[i], set[j]), `${set[i].id ?? set[i].key} / ${set[j].id ?? set[j].key}`);
    for (let i = 0; i < L.claims.length; i++) for (let j = i + 1; j < L.claims.length; j++) {
      const a = L.claims[i], b = L.claims[j];
      assert.ok(Math.hypot(a.cx - b.cx, a.cy - b.cy) >= U.claim, `${a.run} ${a.id} / ${b.id}`);
    }
  });

  test(`${name}: R3 every support edge is a line from the supporter to the supported claim`, () => {
    assert.equal(L.supports.length, runs.reduce((n, r) => n + r.edges.length, 0));
    const at = new Map(L.claims.map((c) => [`${c.run}\u0000${c.id}`, c]));
    for (const s of L.supports) {
      const from = at.get(`${s.run}\u0000${s.supporter}`), to = at.get(`${s.run}\u0000${s.supported}`);
      assert.ok(Math.abs(s.points[0].x - from.cx) < 1e-6 && Math.abs(s.points.at(-1).x - to.cx) < 1e-6, `${s.supporter}→${s.supported}`);
    }
  });

  test(`${name}: the picture is shaped for the box (it fits at a scale, never needs scrolling)`, () => {
    const scale = Math.min(BOX.width / L.width, BOX.height / L.height);
    assert.ok(L.width * scale <= BOX.width + 1e-6 && L.height * scale <= BOX.height + 1e-6);
    assert.ok(Math.max(L.width / L.height, L.height / L.width) < 4, `aspect ${L.width}x${L.height}`);
  });
}

test("R3 next attempts: one line per run whose previous attempt is in the view", () => {
  const prev = all.flatMap((p) => p.runs).filter((r) => r.previous);
  assert.equal(viewLayout(all, BOX).attempts.length, prev.length);
  assert.ok(prev.length >= 1);
});

test("where even the smallest scale does not fit, the rows stay within the width and only the height overflows", () => {
  const small = { width: 500, height: 300 }, minScale = 0.67;
  const L = viewLayout(many, small, () => 30, minScale);
  assert.ok(L.width * minScale <= small.width + 1e-6, `${L.width} wide at ${minScale}`);
  assert.ok(L.height * minScale > small.height, "this data really does overflow");
});

test("deterministic", () => assert.deepEqual(viewLayout(all, BOX), viewLayout(all, BOX)));
test("nothing to show", () => assert.deepEqual(viewLayout([], BOX).runs, []));

// Q3b-9: the dashed line between runs stays out of the diamonds, the claims under them and the project's label.
const bezier = (d) => {
  const n = d.match(/-?\d+(\.\d+)?/g).map(Number), [x0, y0, x1, y1, x2, y2, x3, y3] = n;
  return Array.from({ length: 41 }, (_, i) => { const t = i / 40, u = 1 - t; return { t, x: u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3, y: u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3 }; });
};
const R = 6;
const diamond = (c, p) => Math.abs(p.x - c.x) + Math.abs(p.y - c.y) < R - 0.01;
test("stacked runs: the line runs down the margin, through neither diamond", () => {
  for (const [a, b] of [[{ x: 100, y: 60 }, { x: 100, y: 140 }], [{ x: 100, y: 140 }, { x: 100, y: 60 }], [{ x: 100, y: 60 }, { x: 120, y: 140 }]]) {
    const pts = bezier(attemptPath(a, b, R));
    assert.ok(pts.every((p) => !diamond(a, p) && !diamond(b, p)), "clear of both diamonds");
    if (a.x === b.x) assert.ok(pts.slice(2, -2).every((p) => p.x < a.x - R - 1), "in the margin left of the column");
  }
});
test("side by side: the line stays below the label band, above the heads", () => {
  const labelBottom = 16, headTop = labelBottom + U.pad + 2;  // a project's first row of heads sits under the label and the padding
  const a = { x: 30, y: headTop + 8 }, b = { x: 130, y: headTop + 8 };
  const pts = bezier(attemptPath(a, b, R));
  assert.ok(pts.every((p) => p.y > labelBottom), `peak ${Math.min(...pts.map((p) => p.y))} is below the label band`);
  assert.ok(pts.slice(1, -1).every((p) => !diamond(a, p) && !diamond(b, p)));
});
test("negative control: the old fixed bow would climb through a stacked pair's upper diamond", () => {
  const a = { x: 100, y: 60 }, b = { x: 100, y: 140 };
  const old = bezier(`M${a.x},${a.y} C${a.x},${Math.min(a.y, b.y) - 14} ${b.x},${Math.min(a.y, b.y) - 14} ${b.x},${b.y}`);
  assert.ok(old.slice(2, -2).some((p) => diamond(a, p) || Math.abs(p.x - a.x) < R), "the old route is caught by the same predicate");
});

import { wrapLabel } from "../elements/core/view-layout.js";
test("a long project label wraps at its separators within the cap, and never sets a box wider than the cap", () => {
  assert.deepEqual(wrapLabel("Unnamed project · since 23 Sep 19:49 · 2 runs"), ["Unnamed project", "since 23 Sep 19:49 · 2 runs"]);
  assert.deepEqual(wrapLabel("atlas · 6 runs"), ["atlas · 6 runs"]);
  const long = wrapLabel("x".repeat(80) + " · 3 runs");
  assert.ok(long.length <= 2 && long.every((l) => l.length <= 28) && long[0].endsWith("…"));
});
test("a box is as wide as its marks and its capped label, not its full label", () => {
  const p = (n) => ({ id: n, n: 1, runs: [{ key: n + "r", previous: null, root: null, claims: [], edges: [] }] });
  const wide = viewLayout([p("a")], BOX, () => ({ chars: 200, lines: 1 })).projects[0].w, capped = viewLayout([p("a")], BOX, () => ({ chars: 28, lines: 2 })).projects[0];
  assert.ok(capped.w < wide && capped.lines === 2);
});
