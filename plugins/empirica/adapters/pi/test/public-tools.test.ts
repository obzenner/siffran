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

test("schemaPattern accepts the absolute-lookahead anchor and its end is absolute", () => {
  const pattern = schemaPattern({ type: "string", pattern: "^sha256:[0-9a-f]{64}(?![\\s\\S])" });
  assert.equal(pattern.test(`sha256:${"a".repeat(64)}`), true);
  // a trailing newline is the one byte Python's `re.search` `$` would admit before — JavaScript's
  // `$` (no `m` flag) already refuses it; the absolute lookahead refuses it in both engines.
  assert.equal(pattern.test(`sha256:${"a".repeat(64)}\n`), false);
  assert.equal(pattern.test(`xsha256:${"a".repeat(64)}`), false);
});

test("schemaPattern treats x- extension members as nonsemantic (ADR-0075 annotations)", () => {
  const pattern = schemaPattern({
    type: "string", pattern: "^sha256:[0-9a-f]{64}(?![\\s\\S])",
    "x-core-validator": "core.freshness.validate_digest256",
  });
  assert.equal(pattern.test(`sha256:${"a".repeat(64)}`), true);
});

test("schemaPattern refuses the class escapes the engines read differently", () => {
  // The seed from the E6c review (blocking 2): `^sha256:[0-9a-f]{63}[^\d](?![\s\S])` admits
  // `sha256:` + 63 `a` + the Arabic-Indic digit U+0661 in JavaScript (`\d` is ASCII-only there)
  // and refuses it in Python (`\d` is Unicode-wide), so the pattern is wider than the shared word
  // this loader may serve — the subset this page accepts never includes the class escapes.
  for (const [label, pattern] of [
    ["a complemented digit class", "^sha256:[0-9a-f]{63}[^\\d](?![\\s\\S])"],
    ["a digit class", "^x[\\d]$"],
    ["a word class", "^x\\w+$"],
    ["a space class", "^x\\s+$"],
    ["a negated space class", "^x\\S+$"],
    ["a word boundary", "^x\\b$"],
  ])
    assert.throws(() => schemaPattern({ type: "string", pattern }), /not a closed anchored string pattern/, label);
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
