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

test("schemaPattern serves the one bounded word its only caller guards", () => {
  // The digest guard is schemaPattern's only caller, so the page serves exactly the word that
  // caller needs: `^`, a literal prefix of [a-z0-9:] characters, one [0-9a-f] class with a fixed
  // positive count, and the one closed end — the absolute-end lookahead; `$` never loads.
  // The grammar is positive and bounded — no escape class, no dot,
  // no group, no quantifier but the one fixed count, no alternation — so both engines read every
  // admitted pattern identically.
  for (const pattern of ["^sha256:[0-9a-f]{32}(?![\\s\\S])", "^[0-9a-f]{8}(?![\\s\\S])", "^sha256:[0-9a-f]{64}(?![\\s\\S])"])
    assert.doesNotThrow(() => schemaPattern({ type: "string", pattern }));
});

test("schemaPattern refuses three cross-engine seeds — a positive grammar, not a blacklist", () => {
  // All three seeds serve the two engines different words and
  // refuse at load here — the digests themselves were never wrong, the loader was too wide.
  // The first rests a `(?!.)` on the Unicode line separator U+2028: JavaScript's dot never
  // matches it (the `u` flag changes nothing), Python's does, so the pattern accepts a value in
  // one engine that the other refuses. The second, `^\q`, is an identity escape JavaScript
  // compiles (it matches `q`) and Python cannot compile at all. The third, the dollar-anchored
  // digest, is the one the schema admits wider than the page: JSON Schema's `pattern` is Python
  // `re.search`, whose `$` matches before a final line feed, while JavaScript's `RegExp.test`
  // with an unescaped `$` refuses that value — so a `$` anchor can never load, only the
  // absolute-end lookahead may close the word.
  assert.throws(() => schemaPattern({
    type: "string", pattern: "^sha256:[0-9a-f]{63}(?!.)[\u2028](?![\\s\\S])",
    "x-core-validator": "core.freshness.validate_digest256",
  }), /not a closed anchored string pattern/);
  assert.throws(() => schemaPattern({ type: "string", pattern: "^\\q(?![\\s\\S])" }),
    /not a closed anchored string pattern/);
  assert.throws(() => schemaPattern({
    type: "string", pattern: "^sha256:[0-9a-f]{64}$",
    "x-core-validator": "core.freshness.validate_digest256",
  }), /not a closed anchored string pattern/);
});

test("schemaPattern refuses every shape outside the bounded grammar", () => {
  for (const [label, pattern] of [
    ["a class escape", "^sha256:[\\d]+$"],
    ["a word escape beside the class", "^sha256:\\w[0-9a-f]{4}$"],
    ["a dot", "^sha256:.$"],
    ["a non-hex literal prefix", "^sha256![0-9a-f]{4}$"],
    ["an uppercase literal prefix", "^SHA256:[0-9a-f]{4}$"],
    ["an uppercase class", "^sha256:[0-9A-F]{64}$"],
    ["a complemented class", "^sha256:[^0-9a-f]{4}$"],
    ["two classes", "^sha256:[0-9a-f]{32}[0-9a-f]{32}$"],
    ["a ranged count", "^sha256:[0-9a-f]{2,4}$"],
    ["an open count", "^sha256:[0-9a-f]{4,}$"],
    ["a star count", "^sha256:[0-9a-f]*$"],
    ["a zero count", "^sha256:[0-9a-f]{0}$"],
    ["no class at all", "^sha256$"],
    ["a group", "^sha256:([0-9a-f]){4}$"],
    ["an inline lookahead that is not the closed end atom", "^sha256:(?=[0-9])[0-9a-f]{4}$"],
    ["an absolute-end atom twice", "^sha256:[0-9a-f]{4}(?![\\s\\S])(?![\\s\\S])"],
  ] as const)
    assert.throws(() => schemaPattern({ type: "string", pattern }), /not a closed anchored string pattern/, label);
});

test("schemaPattern refuses every shape it cannot read as one anchored pattern", () => {
  for (const [label, schema] of [
    ["no pattern", { type: "string" }],
    ["non-string pattern", { type: "string", pattern: 7 }],
    ["unanchored pattern", { type: "string", pattern: "sha256:[0-9a-f]{64}" }],
    ["unanchored end", { type: "string", pattern: "^sha256:[0-9a-f]{64}" }],
    ["escaped end anchor", { type: "string", pattern: "^sha256:[0-9a-f]{64}\\$" }],
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
