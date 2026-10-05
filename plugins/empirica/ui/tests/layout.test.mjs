// The graph layout (elements/core/layout.js), over the sample graphs in graphs.json.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { layout, overlaps, edgePath } from "../elements/core/layout.js";

const fx = JSON.parse(readFileSync(new URL("./graphs.json", import.meta.url)));

test("no two node boxes overlap on every fixture graph (incl. 24-claim stress)", () => {
  for (const [name, g] of Object.entries(fx)) {
    const r = layout(g.nodes.map((id) => ({ id })), g.edges, { root: g.root });
    assert.deepEqual(overlaps(r), [], name);
    assert.equal(Object.keys(r.nodes).length, g.nodes.length, name);
  }
});

test("barycentre ordering never crosses more than input order, and strictly less on a scrambled DAG", () => {
  const nodes = ["R", "A", "B", "C", "D", "a1", "a2", "b1", "b2", "c1", "d1"].map((id) => ({ id }));
  // scrambled: children listed in an order that crosses parents
  const edges = [["R","A"],["R","B"],["R","C"],["R","D"],["D","a1"],["C","a2"],["B","b1"],["A","b2"],["D","b1"],["A","c1"],["C","d1"],["B","a1"]].map(([from, to]) => ({ from, to }));
  const naive = layout(nodes, edges, { sweeps: 0 }), good = layout(nodes, edges);
  assert.ok(good.crossings <= naive.crossings);
  assert.ok(good.crossings < naive.crossings, `${good.crossings} < ${naive.crossings}`);
  assert.deepEqual(overlaps(good), []);
});

test("random DAGs: never worse than naive, no overlap, layers respect edge direction", () => {
  let s = 12345; const rnd = () => (s = (s * 1664525 + 1013904223) % 4294967296) / 4294967296;
  let strictly = 0;
  for (let t = 0; t < 60; t++) {
    const n = 5 + Math.floor(rnd() * 28), nodes = Array.from({ length: n }, (_, i) => ({ id: "n" + i }));
    const edges = [];
    for (let i = 1; i < n; i++) { edges.push({ from: "n" + Math.floor(rnd() * i), to: "n" + i }); if (rnd() < 0.4) edges.push({ from: "n" + Math.floor(rnd() * i), to: "n" + i }); }
    const g = layout(nodes, edges), base = layout(nodes, edges, { sweeps: 0 });
    assert.ok(g.crossings <= base.crossings);
    if (g.crossings < base.crossings) strictly++;
    assert.deepEqual(overlaps(g), []);
    for (const e of g.edges) assert.ok(g.nodes[e.to].layer > g.nodes[e.from].layer);
  }
  assert.ok(strictly > 0, "sweeps should help on at least some random DAGs");
});

test("cycles and bad input never throw; cycle edge is reported", () => {
  const r = layout([{ id: "a" }, { id: "b" }, { id: "c" }], [{ from: "a", to: "b" }, { from: "b", to: "c" }, { from: "c", to: "a" }, { from: "a", to: "zzz" }, { from: "a", to: "a" }], { root: "a" });
  assert.equal(r.reversed.length, 1);
  assert.deepEqual(overlaps(r), []);
  assert.equal(layout([], []).width, 32);
});

test("long edges get routed through intermediate layers; paths are well-formed", () => {
  const r = layout(["a", "b", "c"].map((id) => ({ id })), [{ from: "a", to: "b" }, { from: "b", to: "c" }, { from: "a", to: "c" }]);
  const long = r.edges.find((e) => e.from === "a" && e.to === "c");
  assert.equal(long.points.length, 3);
  assert.match(edgePath(long.points), /^M[\d.-]+,[\d.-]+( C[\d., -]+)+$/);
});

test("24-claim stress fixture: barycentre sweeps remove crossings that input order has", () => {
  const g = fx["synth-many-claims"], r = layout(g.nodes.map((id) => ({ id })), g.edges, { root: g.root }), base = layout(g.nodes.map((id) => ({ id })), g.edges, { sweeps: 0 });
  assert.ok(base.crossings > 0 && r.crossings < base.crossings, `${r.crossings} < ${base.crossings}`);
});
