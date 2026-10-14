// The UI over schema-derived data (T1 B): every row and sheet the contract allows renders, with no hole, missing copy or
// exception; a run's standing word is its row's `standing` and nothing else (the UI never computes a domain fact); and
// every enumeration value the contract declares is one these arbitraries produce. Fixed seed, bounded runs: a failure
// shrinks to a minimal document and prints its seed and path.
import assert from "node:assert/strict";
import { test } from "vitest";
import fc from "fast-check";
globalThis.HTMLElement ??= class {};  // the builders live beside the custom elements, which extend the DOM base
const { rowHtml } = await import("../elements/views/rows.js");
const { sheetHtml } = await import("../elements/views/sheet.js");
const { stateHtml } = await import("../elements/core/element.js");
const { arbitraries, declared } = await import("./schema-arbitrary.mjs");
const { line } = await import("./fixtures.mjs");

const RUNS = { seed: 20261013, numRuns: 80 };
const HOLE = /⟦|undefined|NaN|\[object/;
const now = Date.UTC(2026, 9, 13, 12);
const A = arbitraries(), FULL = arbitraries({ full: true });
const STANDINGS = declared()["row.schema.json#/$defs/row/properties/standing"];
// The glyph a row leads with: the standing's own markup, exactly.
const lead = (html) => html.match(/<a [^>]*>\s*(<span class="inline-flex[^]*?<\/span><\/span>)/)?.[1];
const rowCtx = { href: (id) => `?run=${id}`, now, open: null, here: null };
// A sheet ties its facts to its row in ways a schema cannot say (scripts/validate_ui_view.py detail_problems): these are
// those ties, imposed on a schema-derived detail, and nothing else (the UI reads a sheet the observer published).
const DERIVED = ["audit", "claims", "edges", "attention", "progress", "next"];
// row_problems: a partial row carries no derived fact, and a full one all of them (else it is partial); claim ids are
// unique; the root is one of them, null exactly when there are none; every edge stays among them.
function coherentRow(row) {
  if (row.diagnostic === null && DERIVED.some((f) => row[f] === null && f !== "next")) row.diagnostic = "history-missing";
  if (row.diagnostic !== null) { for (const f of DERIVED) row[f] = null; row.root = null; return row; }
  const ids = new Set();
  row.claims = row.claims.filter((c) => !ids.has(c.id) && ids.add(c.id));
  row.root = row.claims[0]?.id ?? null;
  row.edges = row.edges.filter((e) => ids.has(e.from) && ids.has(e.to));
  return row;
}
function coherent(d) {
  const row = d.row;
  if (d.governance === null) row.diagnostic = "unreadable";  // governance is null exactly when the state was not decoded
  else if (row.diagnostic === "unreadable") d.governance = null;
  coherentRow(row);
  const full = row.diagnostic === null;
  for (const f of ["outstanding", "stale", "steps"]) d[f] = full ? d[f] ?? [] : null;
  d.audit = full ? d.audit ?? { findings: [] } : null;
  if (!full) { d.claims = null; row.claims = row.claims ?? null; }
  else {  // the sheet's claims are the row's, in order
    const n = Math.min(row.claims?.length ?? 0, d.claims?.length ?? 0);
    row.claims = (row.claims ?? []).slice(0, n); d.claims = (d.claims ?? []).slice(0, n).map((c, i) => ({ ...c, id: row.claims[i].id }));
    coherentRow(row);
  }
  if (!(full && row.state !== "active")) d.stop = null;  // a stop exactly for a run read in full that has ended
  else if (d.stop === null) row.state = "active";
  if (d.stop) { d.stop.status = row.state; const ids = d.claims.map((c) => c.id); d.stop.required = ids; d.stop.deferred = []; }
  d.residuals = full && (d.stop === null || d.stop.basis === "recorded") ? d.residuals ?? [] : null;
  if (!(d.stop && d.stop.basis === "recorded")) d.now = null;
  if (d.governance && (d.governance.proposed !== null) !== d.governance.proposal_pending) d.governance.proposal_pending = d.governance.proposed !== null;
  d.attempts = [...d.attempts.filter((a) => a.generation !== row.generation), row].sort((a, b) => a.generation - b.generation);
  d.key = row.key; d.version = row.version; d.goal = row.goal;
  return d;
}
const sheetCtx = (detail, extra = {}) => ({ row: detail.row, project: null, detail, attempts: detail.attempts ?? [detail.row], notice: null, claim: null, now, href: () => "?", ...extra });

test("every generated row renders, and leads with its standing's glyph", () => {
  fc.assert(fc.property(A.row, (r) => {
    const html = rowHtml(line(r), rowCtx);
    assert.doesNotMatch(html, HOLE);
    assert.equal(lead(html), stateHtml("standing", r.standing, "glyph"));
  }), RUNS);
});

test("a row's standing word comes from its standing alone: any other field, changed, leaves it; the standing, changed, moves it", () => {
  fc.assert(fc.property(A.row, A.row, fc.constantFrom(...STANDINGS), (r, other, standing) => {
    // every field but the standing from another row: the word is the same
    const swapped = { ...other, standing: r.standing };
    assert.equal(lead(rowHtml(line(swapped), rowCtx)), lead(rowHtml(line(r), rowCtx)));
    // the standing alone changed: the word is the new standing's
    assert.equal(lead(rowHtml(line({ ...r, standing }), rowCtx)), stateHtml("standing", standing, "glyph"));
  }), RUNS);
});

test("every generated sheet renders without a hole, missing copy or exception, with and without its detail and a claim open", () => {
  fc.assert(fc.property(fc.oneof(A.run, FULL.run).map(coherent), (d) => {
    const claim = d.row.claims?.[0]?.id ?? null;
    for (const html of [sheetHtml(sheetCtx(d)), sheetHtml(sheetCtx(d, { claim })), sheetHtml(sheetCtx(d, { detail: null }))]) {
      assert.doesNotMatch(html, HOLE);
      // the sheet's standing is its row's: the header leads with it, whatever else the sheet holds
      assert.equal(html.match(/<span title="[^"]*">(<span class="inline-flex[^]*?<\/span><\/span>)<\/span>/)?.[1], stateHtml("standing", d.row.standing, "full"));
    }
  }), { ...RUNS, numRuns: 40 });
});

test("every enumeration value the contract declares is produced by the arbitraries", () => {
  const seen = {};
  const full = arbitraries({ full: true, seen }), any = arbitraries({ seen });
  for (const a of [full, any]) for (const k of ["summary", "rows", "page", "run"]) fc.sample(a[k], { seed: RUNS.seed, numRuns: 40 });
  const missing = Object.entries(declared()).flatMap(([at, values]) => values.filter((v) => !seen[at]?.has(v)).map((v) => `${at} ${JSON.stringify(v)}`));
  assert.deepEqual(missing, []);
  assert.ok(Object.keys(declared()).length > 40);
});
