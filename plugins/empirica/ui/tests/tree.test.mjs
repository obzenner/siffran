import assert from "node:assert/strict";
import { test } from "node:test";
import { supportTree } from "../elements/core/tree.js";
import { rows } from "./fixtures.mjs";

const claims = (...ids) => ids.map((id) => ({ id }));
const flat = (nodes) => nodes.flatMap((n) => [n.id, ...flat(n.children)]);

test("supporters sit beneath what they support, a claim reached twice is named again and listed once", () => {
  const tree = supportTree("A", claims("A", "B", "C", "D"), [{ from: "A", to: "B" }, { from: "A", to: "C" }, { from: "B", to: "D" }, { from: "C", to: "D" }]);
  assert.deepEqual(flat(tree), ["A", "B", "D", "C", "D"]);
  assert.equal(tree[0].children[1].children[0].again, true);
  assert.equal(tree[0].children[0].children[0].again, false);
});

test("a claim no edge reaches is still listed, and a cycle terminates", () => {
  assert.deepEqual(flat(supportTree("A", claims("A", "B", "Z"), [{ from: "A", to: "B" }])), ["A", "B", "Z"]);
  assert.deepEqual(flat(supportTree("A", claims("A", "B"), [{ from: "A", to: "B" }, { from: "B", to: "A" }])), ["A", "B", "A"]);
});

test("every claim of every real run appears in its tree", () => {
  for (const { row } of rows.filter(({ row: r }) => r.claims?.length)) assert.deepEqual(flat(supportTree(row.root, row.claims, row.edges)).filter((id, i, a) => a.indexOf(id) === i).sort(), row.claims.map((c) => c.id).sort(), row.key);
});
