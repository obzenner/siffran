// The views, built from the real-service fixtures: every run's row and sheet render, every message the observer published for
// a run is on its sheet, a state is its word, markup in a published string stays text, and the calendar and the rail say what the
// summary counts. Strings in, strings out: no DOM.
import assert from "node:assert/strict";
import { test } from "vitest";
globalThis.HTMLElement ??= class {};  // the builders live beside the custom elements, which extend the DOM base
const { calendarCells, calendarDays, calendarHtml, groupsHtml, railHtml, stripHtml } = await import("../elements/views/inbox.js");
const { rowHtml } = await import("../elements/views/rows.js");
const { sheetHtml } = await import("../elements/views/sheet.js");
const { byKey, line, rows, scenarios, sheets, summary } = await import("./fixtures.mjs");
const { visible, wordsIn } = await import("./words.mjs");
const budget = JSON.parse((await import("node:fs")).readFileSync(new URL("./word-budget.json", import.meta.url), "utf8"));

const href = (changes) => `?x=${encodeURIComponent(JSON.stringify(changes))}`;
const now = Date.now();
const ctx = (s, extra = {}) => ({ row: s.row, project: summary.projects.find((p) => p.id === s.row.project) ?? null, detail: s, attempts: s.attempts, notice: null, claim: null, now, href, ...extra });
const place = { view: "inbox", period: "30", since: null, q: "", run: null, claim: null, words: false, filters: {} };
// <ev-more text="…"> holds its words in an attribute; they are on the page
const text = (html) => html.replace(/ text="([^"]*)"/g, ">$1<").replace(/<[^>]+>/g, " ").replace(/&#39;/g, "'").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ");

test("every run's sheet renders without a missing word, a hole or an exception, with and without its detail and its claim", () => {
  for (const [key, s] of Object.entries(sheets)) {
    const claim = s.row.claims?.[0]?.id ?? null;
    for (const html of [sheetHtml(ctx(s, { claim })), sheetHtml(ctx(s)), sheetHtml(ctx(s, { detail: null, project: null })), sheetHtml(ctx(s, { notice: "detail.offline" }))]) {
      assert.doesNotMatch(html, /⟦|undefined|NaN|\[object/, key);
    }
  }
});
test("what the observer published for a run is on its sheet: the goal, what is owed, what it left, each audit's findings, each dropped claim", () => {
  for (const [key, s] of Object.entries(sheets)) {
    const t = text(sheetHtml(ctx(s)));
    for (const o of s.outstanding ?? []) assert.ok(t.includes(o.message), `${key}: ${o.message}`);
    for (const r of s.residuals ?? []) assert.ok(t.includes(r.message), `${key}: ${r.message}`);
    for (const a of s.audits ?? []) for (const f of a.findings) assert.ok(t.includes(f), `${key}: ${f}`);
    for (const d of s.dropped ?? []) assert.ok(t.includes(d.text.slice(0, 40)), `${key}: dropped ${d.claim}`);
    if (s.row.goal) assert.ok(t.includes(s.row.goal.slice(0, 40)), key);
  }
});
test("an open claim shows everything the sheet holds about it: its sources, its decision, its spikes", () => {
  let seen = 0;
  for (const s of Object.values(sheets)) for (const own of s.claims ?? []) {
    const t = text(sheetHtml(ctx(s, { claim: own.id })));
    for (const r of own.records) { seen++; assert.ok(t.includes(r.source.slice(0, 40)) && t.includes(r.quote.slice(0, 30)), `${s.key} ${own.id}`); }
    for (const p of own.proof) assert.ok(t.includes(p.command.slice(0, 30)), `${s.key} ${own.id}`);
    if (own.decision?.answer) assert.ok(t.includes(own.decision.answer.slice(0, 30)));
  }
  assert.ok(seen > 5);
});
test("an unread run says it was not read; it is never an empty or a zero run", () => {
  const t = text(sheetHtml(ctx(sheets[scenarios.unreadable])));
  assert.match(t, /not read in full|Not read/i); assert.doesNotMatch(t, /No claims yet|No audit has been recorded/);
});
test("markup in a goal, a claim, a project or a session is text", () => {
  const evil = `<img src=x onerror="1">`;
  const s = structuredClone(sheets[scenarios.verification]);
  s.row.goal = evil; s.row.claims[0].text = evil; s.row.session.label = evil;
  const html = sheetHtml(ctx(s, { claim: s.row.claims[0].id, project: { id: "p", label: evil, repo: evil, runs: 1 } }));
  assert.doesNotMatch(html, /<img/);
  const r = rowHtml({ ...line(s.row), goal: evil, project_label: evil, session: evil }, { href: () => "?", now, open: null, here: null });
  assert.doesNotMatch(r, /<img/);
});
test("a row is a link carrying the run's compact id, its glyph and goal; only a run filed under Needs a human shows its ask", () => {
  // every fixture ask is a human's pending action (P3c); an ask a host cannot show yet (if_available) is the quiet case
  const ask = rows.find((r) => r.standing === "needs_human"), quiet = { ...ask, standing: "not_ended", attention: "not_ended" };
  const c = { href: (id) => `?run=${id}`, now, open: ask.id, here: null };
  const html = rowHtml(line(ask), c);
  assert.match(html, new RegExp(`href="\\?run=${ask.id}"`)); assert.match(html, /aria-current="true"/); assert.match(html, /Needs a human/);
  assert.match(html, /Decide a claim|Confirm a source|Approve|Consent|Freeze/);
  assert.ok(quiet && !/bg-tone-human-tint/.test(rowHtml(line(quiet), { ...c, open: null })), "no ask chip on a run that is not filed under Needs a human");
  assert.match(html, /bg-tone-human-tint/);
  assert.doesNotMatch(rowHtml(line(rows.find((r) => r.goal === null)), c), /null|undefined/);
});
test("the rail counts what the summary counts, and leaves out a bucket with no runs", () => {
  const html = text(railHtml(summary, place, () => "?"));
  for (const s of summary.marks.standings) assert.equal(/(Needs a human|Audit pending|Changed since audit|Not ended|Proven|Stopped · residual|Stopped · budget|Stopped · frozen|Unreadable)/.test(html), true);
  assert.ok(html.includes(String(summary.coverage.runs)));
  for (const p of summary.projects) assert.ok(html.includes(p.label));
  const none = railHtml({ ...summary, facets: { ...summary.facets, standing: { not_ended: 3 } } }, place, () => "?");
  assert.doesNotMatch(text(none), /Proven/);
});
test("the strip counts the period's facts and says what is in no count", () => {
  const t = text(stripHtml(summary, place, { go: () => "?", periodHref: () => "?" }));
  assert.ok(t.includes(String(summary.aggregates.facts.failed_audits)) && t.includes(String(summary.aggregates.facts.decisions)));
  assert.match(t, new RegExp(`${summary.aggregates.not_read} not read in full`));
});
test("the calendar draws the observer's cells on the viewer's civil days, counting and ranking nothing itself (SOL-01)", () => {
  const days = calendarDays(Date.UTC(2026, 9, 12, 12), 10, "UTC");
  assert.equal(days.length, 10);
  const cal = { tz: "UTC", project: [0, 0, 1], day: [days[8].day, days[9].day, days[8].day], start: [11, 22, 11], runs: [2, 7, 1], standing: [1, 5, 3] };
  const cells = calendarCells(cal);
  assert.deepEqual([cells.size, cells.get(`0:${days[8].day}`), cells.get(`1:${days[8].day}`)], [3, { n: 2, standing: 1, start: 11 }, { n: 1, standing: 3, start: 11 }]);
  const s = { ...structuredClone(summary), calendar: cal }, ids = s.projects.map((p) => p.id);
  const html = calendarHtml(s, place, { nowMs: Date.UTC(2026, 9, 12, 12), go: (f, since) => `?p=${f.project}&since=${since}` });
  const links = [...html.matchAll(/href="\?p=([^&]+)&amp;since=(\d+)"[^>]*title="([^"]*)"[^>]*data-standing="(\w+)"/g)].map((m) => [m[1], m[2], m[4]]);
  assert.deepEqual(links.sort(), [[ids[0], "11", s.marks.standings[1]], [ids[0], "22", s.marks.standings[5]], [ids[1], "11", s.marks.standings[3]]].sort(),
    "each served cell once, linked to its served day start, in its served standing");
  assert.match(html, /7 runs/);
});
test("the calendar's days are civil days across a change of offset, in the zone the summary was asked in: one day each, consecutive (AST-3)", () => {
  // the zone is an argument, never the process's: the same instant gives each zone its own dates
  for (const [tz, now, want] of [["America/New_York", "2026-03-09T12:00:00-04:00", ["2026-03-07", "2026-03-08", "2026-03-09"]],
    ["America/New_York", "2026-11-02T12:00:00-05:00", ["2026-10-31", "2026-11-01", "2026-11-02"]], ["Asia/Kathmandu", "2026-03-09T00:20:00+05:45", ["2026-03-07", "2026-03-08", "2026-03-09"]],
    ["UTC", "2026-03-09T00:20:00+05:45", ["2026-03-06", "2026-03-07", "2026-03-08"]], ["Pacific/Kiritimati", "2026-03-08T23:00:00Z", ["2026-03-07", "2026-03-08", "2026-03-09"]]]) {
    const days = calendarDays(new Date(now).getTime(), 3, tz);
    assert.deepEqual(days.map((d) => d.day), want, `${tz} ${now}`);
    assert.deepEqual(days.map((d) => new Date(d.ms).toISOString().slice(0, 10)), want, "each label names its own date");
  }
  const cal = { tz: "Asia/Kathmandu", project: [0], day: ["2026-03-09"], start: [1], runs: [1], standing: [1] };
  const html = calendarHtml({ ...structuredClone(summary), calendar: cal }, place, { nowMs: new Date("2026-03-09T00:20:00+05:45").getTime(), go: () => "?x" });
  assert.equal([...html.matchAll(/data-cell/g)].length, 1, "the caller passes the zone it sent: today's cell is drawn");
});
test("the runs with no time are one number, the observer's, in the strip and on the calendar alike", () => {
  // the fixtures hold a run that could not be read (no time, but not a run with no recorded time) beside the read ones
  const s = structuredClone(summary);
  s.aggregates.untimed_runs = 3;
  const nowMs = Math.max(...s.marks.updated.filter(Boolean)) * 1000;
  const cal = text(calendarHtml(s, place, { nowMs, go: () => "?" })), strip = text(stripHtml(s, place, { go: () => "?", periodHref: () => "?" }));
  assert.ok(s.marks.updated.filter((u) => u === null).length !== 3, "the marks alone would give another number");
  assert.match(cal, /\b3 runs with no time/); assert.match(strip, /\b3 with no recorded time/);
});

test("a calendar cell is named in words and a day that needs a human is drawn taller", () => {
  const html = calendarHtml(summary, place, { nowMs: Math.max(...summary.marks.updated.filter(Boolean)) * 1000, go: (f, since) => `?p=${f.project}&since=${since}` });
  assert.match(html, /data-cell/); assert.match(html, /title="[^"]*\d{4}[^"]*run/);
  assert.match(html, /h-m/); assert.doesNotMatch(html, /⟦/);
});
test("the groups follow the summary's order and cap, and a group says how many it has", () => {
  const loaded = new Map(summary.marks.standings.map((s) => [s, rows.filter((r) => r.standing === s).slice(0, 2).map(line)]));
  const html = groupsHtml(summary, loaded, { href: (id) => `?run=${id}`, all: (s) => `?standing=${s}`, now, open: null, here: null, empty: "list.empty.demo" });
  const order = [...html.matchAll(/data-group="(\w+)"/g)].map((m) => m[1]);
  assert.deepEqual(order, summary.marks.standings.filter((s) => summary.facets.standing[s] > 0));
  assert.match(html, /data-all/);
  assert.match(groupsHtml({ ...summary, facets: { standing: {} } }, new Map(), { empty: "list.empty.demo" }), /demo data has no runs/);
  void byKey;
});

// ---- P3 · structure and nesting, as the contract and the core hold them -------------------------------------------------
const claimRows = (html) => [...html.matchAll(/<li[^>]*data-claim-row="([^"]+)"([^>]*)>/g)].map((m) => ({ id: m[1], parent: /data-parent="([^"]+)"/.exec(m[2])?.[1] ?? null }));
test("the claims are the support graph: each listed once, under the claim it supports, from the root (no flat list beside a tree)", () => {
  let nested = 0;
  for (const [key, s] of Object.entries(sheets)) {
    if (!s.row.claims?.length) continue;
    const html = sheetHtml(ctx(s));
    const listed = claimRows(html);
    assert.deepEqual(listed.map((c) => c.id).sort(), s.row.claims.map((c) => c.id).sort(), `${key}: every claim once`);
    assert.equal(listed[0].id, s.row.root ?? listed[0].id, `${key}: the root first`);
    for (const c of listed) {
      if (c.parent === null) continue;
      nested++;
      assert.ok(s.row.edges.some((e) => e.from === c.parent && e.to === c.id), `${key}: ${c.id} is listed under ${c.parent}, which it supports`);
    }
    for (const e of s.row.edges) assert.ok(listed.some((c) => c.id === e.to && c.parent !== null), `${key}: ${e.to} is under a claim it supports`);
    assert.doesNotMatch(html, /data-support/, `${key}: no second, separate support list`);
  }
  assert.ok(nested >= 10);
});
test("a decision is attached to what it decides: the claim by its words (a link to it), the source, or the plan; never a bare id", () => {
  const s = sheets[scenarios.verification];
  const html = sheetHtml(ctx(s));
  const section = /data-section="decisions"[\s\S]*?<\/details>/.exec(html)[0];
  for (const d of s.decisions.filter((x) => x.claim)) {
    const c = s.row.claims.find((x) => x.id === d.claim);
    assert.ok(section.includes(`data-decision-claim="${d.claim}"`) && visible(section).includes(c.text.slice(0, 30)), d.claim);
  }
  assert.doesNotMatch(visible(section), /\bC\d+\b/, "no claim id is shown as if it were words");
  const open = sheetHtml(ctx(s, { claim: "C3" }));
  const claim = /data-claim-row="C3"[\s\S]*?<\/details>/.exec(open)[0];
  assert.match(claim, /data-claim-decision/, "and the claim it decides lists the decision");
  const configured = Object.values(sheets).find((x) => x.decisions?.some((d) => d.plan !== null));
  assert.match(visible(sheetHtml(ctx(configured))), /Plan 1/);
});
test("the core's sentences are on the sheet once each, not echoed (Now names a blocker; the claim line does not repeat it)", () => {
  for (const [key, s] of Object.entries(sheets)) {
    const t = visible(sheetHtml(ctx(s)));
    const said = new Set([...(s.outstanding ?? []), ...(s.residuals ?? []), ...(s.now ?? [])].map((x) => x.message));
    for (const message of said) {
      const times = t.split(message).length - 1;
      assert.ok(times >= 1, `${key}: ${message} is on the sheet`);
      assert.ok(times <= [...(s.outstanding ?? []), ...(s.residuals ?? []), ...(s.now ?? [])].filter((x) => x.message === message).length, `${key}: "${message}" said ${times} times`);
    }
    assert.doesNotMatch(t, /Something waits on a human; each item says where it is asked/, key);
  }
});
test("the strip's runs are the observer's total, never a sum the page makes", () => {
  const s = structuredClone(summary); s.aggregates.total = 4321;
  assert.match(visible(stripHtml(s, place, { go: () => "?", periodHref: () => "?" })), /4,321 runs/);
});
test("the rail lists the projects by name, the way a person looks one up", () => {
  const html = railHtml(summary, place, (f) => `?project=${f.project ?? ""}`);
  const order = [...html.matchAll(/\?project=([^"&]+)"/g)].map((m) => m[1]).filter(Boolean);
  const labels = order.map((id) => summary.projects.find((p) => p.id === id).label);
  assert.deepEqual(labels, [...labels].sort((a, b) => a.localeCompare(b)));
});
test("WORD BUDGET: the inbox and the sheets at the fixed fixtures hold no more than 10% over their budget (tests/word-budget.json)", () => {
  const nowMs = Math.max(...summary.marks.updated.filter(Boolean)) * 1000 + 3600e3;
  const all = { ...place, period: "all" };
  const loaded = new Map(summary.marks.standings.map((s) => [s, rows.filter((r) => r.standing === s).slice(0, 5).map(line)]));
  const c = { href: (id) => `?run=${id}`, all: () => "?", now: nowMs, open: null, here: null, empty: "list.empty.demo" };
  const inbox = [railHtml(summary, all, () => "?"), stripHtml(summary, all, { go: () => "?", periodHref: () => "?" }),
    calendarHtml(summary, all, { nowMs, go: () => "?" }), groupsHtml(summary, loaded, c)].reduce((n, h) => n + wordsIn(visible(h)), 0);
  let sheet = 0;
  for (const s of Object.values(sheets)) sheet += wordsIn(visible(sheetHtml(ctx(s, { now: nowMs }))));
  for (const [name, got] of Object.entries({ inbox, sheet })) {
    assert.ok(got <= budget[name] * 1.1, `${name}: ${got} words, budget ${budget[name]} (+10%); trim words, or raise the budget in a reviewed change`);
  }
  assert.ok(budget.inbox < budget.p2.inbox && budget.sheet < budget.p2.sheet, "the budget is below what P2 said");
});

const copy = JSON.parse((await import("node:fs")).readFileSync(new URL("../copy/en.json", import.meta.url), "utf8"));
// P3b: the fixtures are the minimal selection, so three facts the journeys read from runs no longer in them are held here,
// on a fixture sheet changed in that one fact (the real service still produces each: tests/v2/test_observer_stateful.py).
test("an audit's independence is worded on the sheet, for every value the contract declares", () => {
  const base = sheets[scenarios.converged];
  for (const independence of ["distinct", "mixed", "same_model", "unverified", "human_owned"]) {
    const s = structuredClone(base);
    s.row.audit.independence = independence; for (const a of s.audits) a.independence = independence;
    const t = text(sheetHtml(ctx(s)));
    assert.ok(base.audits.length > 0 && t.includes(copy[`independence.${independence}`]), independence);
  }
});
test("every count the strip says is in the number's own form: 1 revision, 1 audit passed, 1 human decision (L2, L4)", () => {
  const s = structuredClone(summary), a = s.aggregates;
  a.by.audit.passed = 1; a.by.audit.none = 1; a.facts.decisions = 1; a.facts.failed_audits = 1; a.facts.refutations = 1;
  for (const k of Object.keys(a.decisions_by)) a.decisions_by[k].facts = 1;
  const t = text(stripHtml(s, place, { go: () => "?", periodHref: () => "?" }));
  for (const word of ["1 audit passed", "1 run not audited", "human decision", "failed audit", "claim refuted", "1 revision", "1 configuration"])
    assert.ok(t.includes(word), `${word}: ${t}`);
  for (const wrong of ["1 audits", "1 runs", "1 revisions", "1 configurations", "1 human decisions", "1 failed audits"]) assert.ok(!t.includes(wrong), wrong);
});
test("the audit section says its latest verdict once, then every verdict as served, newest first (L9, L13)", () => {
  const base = sheets[scenarios.converged];
  const audit = (html) => html.slice(html.indexOf('data-section="audit"'), html.indexOf("</details>", html.indexOf('data-section="audit"')));
  const one = structuredClone(base);
  one.audits = [{ ...base.audits[0], verdict: "passed" }]; one.row.audit = { ...one.row.audit, verdict: "passed", current: true, pending: false };
  const passed = copy["audit.passed.label"];
  assert.equal(text(audit(sheetHtml(ctx(one)))).split(passed).length - 1, 1, "one audit: its verdict once, not a summary and a record");
  const two = structuredClone(one);
  two.audits = [{ ...one.audits[0], at: 200, findings: ["the newest finding"] }, { ...one.audits[0], verdict: "failed", at: 100, findings: ["the older finding"] }];
  const t = text(audit(sheetHtml(ctx(two))));
  assert.ok(t.indexOf("the newest finding") < t.indexOf("the older finding"), "as served: newest first");
  assert.equal(t.split(passed).length - 1, 1, "the latest verdict once");
  const stale = structuredClone(one); stale.row.audit = { ...stale.row.audit, current: false, pending: true };
  const st = text(audit(sheetHtml(ctx(stale))));
  assert.ok(st.includes(copy["audit.not_current"]) && st.includes(copy["audit.reaudit_pending"]) && st.split(passed).length - 1 === 1, "the summary's notes stay, on the latest verdict");
});
test("a human asked only if the host finds a dialog is said so, and the session's context is a closed disclosure", () => {
  const s = structuredClone(sheets[scenarios.verification]);
  for (const o of s.outstanding) if (o.who === "human") o.where = "if_available";
  s.row.next = { ...s.row.next, where: "if_available" };
  const html = sheetHtml(ctx(s));
  assert.match(text(html), /if the host finds a dialog/);
  assert.match(html, /<details class="text-xs"><summary[^>]*data-session-context/);
});
test("an attempt that could not be read is 'Not read' in the comparison, never 'None'", () => {
  const s = structuredClone(sheets[scenarios["audit-failed"]]), unread = sheets[scenarios.unreadable].row;
  s.attempts[0] = { ...structuredClone(unread), key: s.attempts[0].key, id: s.attempts[0].id, project: s.row.project, run: s.row.run, generation: s.attempts[0].generation };
  const html = sheetHtml(ctx(s));
  const compare = text(html.slice(html.indexOf("ev-compare")));
  assert.match(compare, /Not read/); assert.doesNotMatch(compare, /None recorded/);
});

test("the sheet's claims start with the run's graph, drawn small from geometry's claimGraph: every claim once, every published edge once, each a link", async () => {
  const { claimGraph } = await import("../app/geometry.js");
  const catalogue = (await import("../catalogue.json", { with: { type: "json" } })).default, G = catalogue.universe;
  for (const [key, s] of Object.entries(sheets)) {
    const claims = (s.row.claims ?? []).slice(0, G.claims_cap);
    if (!claims.length) continue;
    const html = sheetHtml(ctx(s, { claim: claims[0].id }));
    const svg = /<svg [^>]*data-claim-graph>[\s\S]*?<\/svg>/.exec(html)?.[0];
    assert.ok(svg, key);
    const drawnIds = [...svg.matchAll(/data-graph-claim="([^"]+)"/g)].map((m) => m[1]);
    assert.deepEqual(drawnIds, claims.map((c) => c.id), `${key}: every claim once`);
    const g = claimGraph(s.row.root, claims, s.row.edges, new Map(), { ring: G.sheet_ring, node: G.sheet_node_px, gap: G.claim_gap_px / 2, run: G.sheet_run_px, label_h: G.sheet_label_px, cap: G.loop_cap });
    const edges = [...svg.matchAll(/data-edge="([^"]+)"/g)].map((m) => m[1]);
    assert.deepEqual(edges, g.edges.map((e) => `${e.from} ${e.to}`), `${key}: the published edges`);
    assert.equal(new Set(edges).size, new Set((s.row.edges ?? []).map((e) => `${e.from} ${e.to}`)).size, `${key}: none dropped`);
    for (const c of claims) assert.match(svg, new RegExp(`<a href="[^"]+" data-graph-claim="${c.id}"`), `${c.id} a link`);
    assert.ok(html.indexOf(svg) < html.indexOf("data-claim-row"), "above the list");
  }
});
