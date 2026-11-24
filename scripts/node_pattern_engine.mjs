#!/usr/bin/env node
/**
 * The JavaScript half of the contract-pattern parity check: reads one JSON array from stdin —
 * `[{ "pattern": string, "cases": [string, ...] }, ...]` — and answers, for every cross-runnable
 * pattern, whether `new RegExp(pattern, "u")` accepts each case (search semantics, the same reading
 * draft 2020-12 gives a schema pattern: `re.search` in Python, `.test` here).
 *
 * The contract's patterns are specified for Python `re` and JavaScript RegExp with the `u` flag,
 * code-point semantics: `.test` under `u` reads astral text as one code point and the character
 * classes see code points, not surrogate halves — the mode an ordinary RegExp gets wrong on exactly
 * the text the corpus probes (a negated blank class would read an astral character as two units).
 *
 * Output: one JSON array on stdout, `[{ "pattern", "case", "verdict" }, ...]`, in stdin order.
 */
import process from "node:process";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const requested = JSON.parse(Buffer.concat(chunks).toString("utf-8"));

const answers = [];
for (const { pattern, cases } of requested) {
  const expression = new RegExp(pattern, "u"); // never throws: the Python side compiled it first
  for (const probe of cases) {
    answers.push({ pattern, case: probe, verdict: expression.test(probe) });
  }
}
process.stdout.write(JSON.stringify(answers));
