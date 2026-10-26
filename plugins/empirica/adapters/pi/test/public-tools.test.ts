// The closed vendored-schema readers (public-tools.ts). Each one admits exactly the schema shapes it
// can read faithfully and refuses every other shape by throwing, so a derived contract value can never
// load from a schema it does not understand.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { schemaBoolean, schemaBranches, schemaPattern } from "../src/public-tools.ts";

const DEFS = JSON.parse(readFileSync(join(import.meta.dirname, "..", "..", "..", "vendor", "contracts", "empirica", "v2",
  "response.schema.json"), "utf8")).$defs;

test("schemaPattern reads the vendored digest pattern exactly", () => {
  const pattern = schemaPattern(DEFS.digest256);
  assert.equal(pattern.source, DEFS.digest256.pattern);
  assert.equal(pattern.flags, "");
  assert.equal(pattern.test(`sha256:${"a".repeat(64)}`), true);
  assert.equal(pattern.test(`xsha256:${"a".repeat(64)}`), false);
});

test("schemaPattern refuses every shape it cannot read as one anchored pattern", () => {
  for (const [label, schema] of [
    ["no pattern", { type: "string" }],
    ["non-string pattern", { type: "string", pattern: 7 }],
    ["unanchored pattern", { type: "string", pattern: "sha256:[0-9a-f]{64}" }],
    ["unanchored end", { type: "string", pattern: "^sha256:[0-9a-f]{64}" }],
    ["escaped end anchor", { type: "string", pattern: "^sha256:\\$" }],
    ["top-level alternation", { type: "string", pattern: "^a|b$" }],
    ["format keyword", { type: "string", pattern: "^a$", format: "uri" }],
    ["enum keyword", { type: "string", pattern: "^a$", enum: ["a"] }],
    ["not a string type", { type: "integer", pattern: "^1$" }],
    ["missing", undefined],
  ] as const)
    assert.throws(() => schemaPattern(schema as never), /not a closed anchored string pattern/, label);
});

test("schemaBranches refuses anything but a nonempty oneOf with its own message", () => {
  assert.equal(schemaBranches(DEFS.spikeBackend).length, DEFS.spikeBackend.oneOf.length);
  for (const [label, schema] of [
    ["missing oneOf", { anyOf: DEFS.spikeBackend.oneOf }],
    ["empty oneOf", { oneOf: [] }],
    ["non-array oneOf", { oneOf: { kind: "qualified" } }],
    ["missing", undefined],
  ] as const)
    assert.throws(() => schemaBranches(schema as never), /not a closed oneOf/, label);
});

test("schemaBoolean reads only a boolean const", () => {
  assert.equal(schemaBoolean(DEFS.allowConverged.properties.converged), true);
  assert.equal(schemaBoolean(DEFS.allowNonConverged.properties.converged), false);
  for (const [label, schema] of [
    ["string const", { const: "true" }],
    ["no const", { type: "boolean" }],
    ["missing", undefined],
  ] as const)
    assert.throws(() => schemaBoolean(schema as never), /not a boolean const/, label);
});
