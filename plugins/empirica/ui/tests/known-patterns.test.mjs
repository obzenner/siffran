// A known-pattern regression guard, NOT a proof that the UI holds no domain decision. The patterns below are the
// decisions earlier versions took in JavaScript (UI slop ledger L01-L27, and what the reviews found since); none may come
// back in the same spelling. What proves that the observer says what the gate and the author view say is
// tests/v2/test_observer_agreement.py (test_INVARIANT_the_observer_says_what_the_gate_and_author_view_say), which runs
// the real service; a new spelling of an old decision is not caught here, and is not claimed to be.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]));
const code = ["app", "elements"].flatMap((d) => walk(path.join(root, d))).filter((f) => f.endsWith(".js") || f.endsWith(".html"))
  .map((f) => [path.relative(root, f), fs.readFileSync(f, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[\s;{}(),])\/\/[^\n]*/g, "$1")]);

const FORBIDDEN = [
  [/settled|isFinal|finality/i, "finality of a claim is the core's `assurance`"],
  [/\.blocker\s*(!=|==)=?\s*null|\.blocker\s*\?/, "whether a claim blocks is the core's blocker and attention"],
  [/\.state\s*===?\s*["'](approved|open|blocked|discarded)["']/, "a claim's standing is read, not derived"],
  [/(verdict|audit)\s*===?\s*["']failed["']\s*\|\||current\s*===?\s*false\s*\?\s*["']failed/, "a stale pass is not a failure"],
  [/claims\.length\s*===?\s*0|!\s*\w+\.claims\.length\s*\)\s*return\s+0/, "unread claims are not zero claims"],
  [/\bmtime\b|\.updated\s*[-<>]/, "attempt order is the observer's families, not time"],
  [/\.filter\([^)]*\.gating\b/, "scope is the core's `progress` and `gating`, not recounted"],
  [/\?\?\s*route\b|\boutcome\s*\?\?/, "a decision's standing is its `status`"],
];

test("no source under app/ or elements/ decides a domain fact", () => {
  for (const [pattern, why] of FORBIDDEN) for (const [file, src] of code) assert.ok(!pattern.test(src), `${file}: ${why}`);
});

test("the patterns do fire (a negative control)", () => {
  assert.ok(FORBIDDEN.every(([p]) => p instanceof RegExp));
  assert.ok(FORBIDDEN[0][0].test("const settled = x") && FORBIDDEN[2][0].test('c.state === "approved"') && FORBIDDEN[5][0].test("a.updated - b.updated"));
});
