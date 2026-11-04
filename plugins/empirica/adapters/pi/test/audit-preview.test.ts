// pi-subagents 0.77.0 (CHANGELOG #2774): a foreground result's `content` shows only the first 2,000 characters of a long
// child report, followed by the path of the file that holds all of it (`capInlineOutput`, src/runs/shared/single-output.js).
// `details.results[0].finalOutput` stays the child's whole output in inline mode (src/runs/foreground/execution.js, the
// assignment of `finalOutput` to `fullOutput`). These tests run on previews produced by the real package's own
// function (test/fixtures/result-preview-<version>.json, written by scripts/capture_pi_preflight_fixture.mjs).
//
// The rule they hold: the verdict is read from the exact `finalOutput`, never from a preview, and never from a file the
// preview points to.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { childNotTerminal, reportText, verdictFromText } from "../src/audit.ts";
import type { ToolResultEvent } from "../src/pi-types.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));

interface Preview { full: string; capped: string }
interface PreviewFixture { version: string; cases: Record<"long_verdict" | "long_verdict_saved_output" | "early_verdict_long_tail" | "short_verdict", Preview> }

const FIXTURES: PreviewFixture[] = readdirSync(path.join(HERE, "fixtures")).filter((name) => /^result-preview-.*\.json$/.test(name)).sort()
  .map((name) => JSON.parse(readFileSync(path.join(HERE, "fixtures", name), "utf8")) as PreviewFixture)
  .map((fixture) => JSON.parse(JSON.stringify(fixture).replaceAll("{{ARTIFACT}}", "/run/artifacts/auditor_output.md")));

const event = (content: string, rows?: unknown[]): ToolResultEvent => ({
  toolCallId: "audit", content: [{ type: "text", text: content }], ...(rows === undefined ? {} : { details: { mode: "single", results: rows } }),
});

test("previews were captured from 0.77.0 through 0.84.0, each by the package's own function", () => {
  assert.deepEqual(FIXTURES.map((fixture) => fixture.version),
    ["0.77.0", "0.78.0", "0.79.0", "0.79.1", "0.79.2", "0.80.0", "0.81.0", "0.82.0", "0.82.1", "0.83.0", "0.83.1", "0.84.0"]);
});

test("a long auditor report is previewed in content; the verdict is read in full from finalOutput", () => {
  for (const fixture of FIXTURES) {
    for (const name of ["long_verdict", "long_verdict_saved_output"] as const) {
      const { full, capped } = fixture.cases[name];
      assert.ok(capped.length < full.length, `${fixture.version} ${name}: the content is a preview`);
      assert.match(capped, /Read (?:it|this file) if needed\./, `${fixture.version} ${name}: it points to a file`);
      assert.ok(verdictFromText(full), `${fixture.version} ${name}: the whole output carries one readable verdict`);
      const admitted = event(capped, [{ agent: "empirica.empirica-auditor", exitCode: 0, finalOutput: full }]);
      assert.equal(reportText(admitted), full, `${fixture.version} ${name}`);
      assert.deepEqual(verdictFromText(reportText(admitted)), verdictFromText(full), `${fixture.version} ${name}`);
    }
  }
});

test("a preview is never a verdict source: no rows, or a finalOutput that is itself a preview, give no text", () => {
  for (const fixture of FIXTURES) {
    for (const name of ["long_verdict", "long_verdict_saved_output", "early_verdict_long_tail"] as const) {
      const { capped } = fixture.cases[name];
      const label = `${fixture.version} ${name}`;
      assert.equal(reportText(event(capped)), "", `${label}: content alone`);
      assert.equal(verdictFromText(reportText(event(capped))), null, label);
      assert.equal(reportText(event(capped, [{ finalOutput: capped }])), "", `${label}: finalOutput that is a preview`);
      assert.equal(reportText({ toolCallId: "audit", content: capped }), "", `${label}: string content`);
    }
  }
});

test("a complete fence in the first 2,000 characters of a long report is not admitted from the preview", () => {
  for (const fixture of FIXTURES) {
    const { full, capped } = fixture.cases.early_verdict_long_tail;
    assert.ok(verdictFromText(capped), `${fixture.version}: the preview alone would read as one verdict (the hazard)`);
    assert.equal(verdictFromText(full), null, `${fixture.version}: the whole output has a second fence and is ambiguous`);
    assert.equal(verdictFromText(reportText(event(capped))), null, fixture.version);
    assert.equal(verdictFromText(reportText(event(capped, [{ finalOutput: full }]))), null, fixture.version);
  }
});

test("a report that fits is not previewed and is read from content or finalOutput as before", () => {
  for (const fixture of FIXTURES) {
    const { full, capped } = fixture.cases.short_verdict;
    assert.equal(capped, full, fixture.version);
    assert.equal(reportText(event(capped)), full);
    assert.ok(verdictFromText(reportText(event(capped))));
  }
});

test("a complete report that quotes a preview pointer before its verdict is read whole (only a trailing pointer marks a preview)", () => {
  const { full } = FIXTURES.at(-1)!.cases.short_verdict;
  const quoted = "The evidence tool result was cut:\n…\nFull output: /tmp/x.md (9 KB, 200 lines). Read it if needed.\n\n" + full;
  assert.ok(verdictFromText(full), "the control report carries one verdict");
  assert.equal(reportText(event(quoted)), quoted);
  assert.deepEqual(verdictFromText(reportText(event(quoted))), verdictFromText(full));
  assert.equal(reportText(event("x", [{ finalOutput: quoted }])), quoted, "finalOutput");
});

test("the pointer in a preview is only text: the adapter reads no file it names", () => {
  const source = readFileSync(path.join(HERE, "..", "src", "audit.ts"), "utf8");
  assert.doesNotMatch(source, /node:fs|readFileSync|statSync/);
  const forged = event("x".repeat(100) + "\n…\nFull output: /etc/hosts (1 B, 1 line). Read it if needed.");
  assert.equal(reportText(forged), "");
});

test("the interrupted, detached and stopped rows are still not terminal, whatever the finalOutput holds", () => {
  for (const flag of ["interrupted", "detached", "stopped"]) {
    const { full } = FIXTURES.at(-1)!.cases.long_verdict;
    assert.equal(childNotTerminal(event("preview", [{ finalOutput: full, [flag]: true }])), true, flag);
  }
  assert.equal(childNotTerminal(event("preview", [{ finalOutput: FIXTURES.at(-1)!.cases.long_verdict.full }])), false);
});
