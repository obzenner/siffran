// VIEW.md R1–R3 as geometry, on the observer demo fixtures and on a store-shaped data set (many projects, runs without claims).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { apply } from "../app/filter.js";
import { U, viewLayout } from "../elements/core/view-layout.js";

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

test("deterministic", () => assert.deepEqual(viewLayout(all, BOX), viewLayout(all, BOX)));
test("nothing to show", () => assert.deepEqual(viewLayout([], BOX).runs, []));
