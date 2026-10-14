// Browser journeys for the UI, in headless Chrome via cdp.mjs, against the observer's own servers (T1 D: the UI by structure,
// not volume). Three modes, one file, so they share every helper:
//   make ui-browser  (default)   each journey once at fixture size (the demo fixtures, the real service's output; a live
//     store that service built; the empty store): the operator's tasks (what needs me and where; narrowing and counting;
//     one run's proof, its audits, its other attempts and why it stopped; a remembered goal; the keyboard), the standing
//     rules (URL and history, safe text, recovery, hidden tabs, motion, the narrow layout) and the Universe's journeys 1-8;
//     then the layout invariants in both colour schemes at 1440x900 and 1024x768 (nothing scrolls sideways, no overlay
//     box covers another, no heading or chip is clipped without its full title, focus stays in the window).
//   MODE=smoke  (make ui-smoke, in make verify)   one generated store of 1,000 runs: the first render, the DOM bounds
//     and a zoom's frames, and what only many runs have (a calendar of many project-days, a list longer than a page).
//   MODE=shots OUT=<dir>  (make ui-shots)   about ten deterministic PNGs of the demo: a fixed clock, no motion, both
//     schemes, written outside the repository, with their sha256.
// Each journey states how many clicks it may take; typing is not a click. Every number the page shows is compared with
// the observer's own answer. One line per check; exits 1 on any failure.
import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { withPage } from "./cdp.mjs";

const ui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CLI = path.join(ui, "..", "adapters", "observer", "cli.py");
const GENERATED = path.join(ui, "..", "tests", "v2", "serve_generated.py");
const catalogue = JSON.parse(fs.readFileSync(path.join(ui, "catalogue.json"), "utf8"));
const copy = JSON.parse(fs.readFileSync(path.join(ui, "copy", "en.json"), "utf8"));
const home = fs.mkdtempSync(path.join(os.tmpdir(), "ev-home-"));
const children = [];
function start(args, cwd = home) {
  const p = spawn("python3", args, { cwd, stdio: ["ignore", "pipe", "inherit"] });
  children.push(p);
  return new Promise((res, rej) => {
    let buf = "";
    p.stdout.on("data", (d) => { buf += d; const m = /http:\/\/127\.0\.0\.1:\d+/.exec(buf); if (m) res(m[0]); });
    setTimeout(() => rej(new Error("a server did not start")), 120000);
  });
}
const MODE = process.env.MODE ?? "journeys";
if (!["journeys", "smoke", "shots"].includes(MODE)) throw new Error(`MODE=${MODE}: journeys, smoke or shots`);
const servers = {
  journeys: () => Promise.all([start([CLI, "serve", "--port", "0", "--demo"]), start([CLI, "serve", "--port", "0", "--home", home]), start([GENERATED])]),
  smoke: async () => [null, null, null, await start([GENERATED, "--demo", "--runs", "1000"])],
  shots: async () => [await start([CLI, "serve", "--port", "0", "--demo"])],
};
const [DEMO, EMPTY, LIVE, BIG] = await servers[MODE]();
const SOURCES = [{ name: "demo", base: DEMO, data: "demo" }, { name: "generated", base: LIVE, data: "store" }];
const SIZES = ["1440x900", "1024x768"], WIDE = SIZES[0], SCHEMES = ["light", "dark"];
const fixtures = path.join(ui, "..", "vendor", "contracts", "empirica-ui-view", "v1", "fixtures");
const S = JSON.parse(fs.readFileSync(path.join(fixtures, "scenarios.json"), "utf8"));
const json = async (base, u) => (await fetch(`${base}${u}`)).json();

let failed = 0, total = 0;
function check(name, ok, got) { total++; if (!ok) failed++; console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok ? "" : `  got ${JSON.stringify(got)?.slice(0, 600)}`}`); }

/** A page for one source and size, with the helpers every journey uses; `clicks` counts what a person would click. */
async function session(src, size, fn, { wait = 0, scheme = null, clock = null, still = false } = {}) {
  await withPage(`${src.base}/app/?data=${src.data}`, size, async (P) => {
    const E = P.evalJs;
    await P.send("Page.enable");  // scripts added for new documents (the safe-text journey) need the page domain
    const media = [...(scheme ? [{ name: "prefers-color-scheme", value: scheme }] : []), ...(still ? [{ name: "prefers-reduced-motion", value: "reduce" }] : [])];
    if (media.length) await P.send("Emulation.setEmulatedMedia", { features: media });
    // a frozen clock: every "ago" and window reads one instant, so a picture is the same on every run
    if (clock !== null) await P.send("Page.addScriptToEvaluateOnNewDocument", { source: `(() => { const T = ${clock}, D = Date; globalThis.Date = class extends D { constructor(...a) { super(...(a.length ? a : [T])); } static now() { return T; } }; })();` });
    let clicks = 0;
    const A = {
      P, E, size, src, scheme: scheme ?? "light",
      go: async (query = "") => { await P.send("Page.navigate", { url: `${src.base}/app/?data=${src.data}${query}` }); await P.sleep(250); await P.waitReady(); await P.sleep(400 + wait); },
      click: async (sel) => { clicks++; const ok = await E(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return false; e.click(); return true; })()`); await P.sleep(450); return ok; },
      type: async (sel, text) => { await E(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.focus(); e.value = ${JSON.stringify(text)}; e.dispatchEvent(new Event("input", { bubbles: true })); })()`); await P.sleep(catalogue.typed_ms + 600); },
      text: (sel) => E(`document.querySelector(${JSON.stringify(sel)})?.innerText ?? ""`),
      count: (sel) => E(`document.querySelectorAll(${JSON.stringify(sel)}).length`),
      url: () => E(`location.search`),
      reset: () => { clicks = 0; },
      get clicks() { return clicks; },
    };
    if (clock !== null || media.length) await A.go("");
    A.api = (u, extra = "") => json(src.base, `${u}${u.includes("?") ? "&" : "?"}source=${src.data}${extra}`);
    A.summary = await A.api("/api/summary");
    A.rows = (await A.api("/api/runs", "&limit=200")).rows;
    A.id = (scenario) => S[scenario] && A.rows.find((r) => r.key === S[scenario])?.id;
    await P.waitReady();
    await P.sleep(wait);
    await fn(A);
    check(`no page errors, no missing copy: ${src.name} ${size}`, P.errors.length === 0 && !(await E(`document.body.innerHTML.includes("⟦")`)), P.errors);
  });
}
const num = (n) => new Intl.NumberFormat("en-US").format(n);
const digits = (s) => Number(String(s).replace(/[^\d]/g, ""));

// ---- J1 · what needs me, and where · 0 clicks ------------------------------------------------------------------------
async function inbox(A) {
  const tag = `${A.src.name} ${A.size}`, E = A.E, facets = A.summary.facets.standing;
  A.reset(); await A.go("&view=inbox");
  const groups = await E(`[...document.querySelectorAll("[data-group]")].map((g) => ({ standing: g.dataset.group, count: g.querySelector("[data-count]").innerText, rows: g.querySelectorAll("[data-row]").length }))`);
  const order = A.summary.marks.standings.filter((s) => facets[s] > 0);
  check(`inbox ${tag}: the groups are the standings the store has, most urgent first, each with the observer's count (0 clicks)`, JSON.stringify(groups.map((g) => g.standing)) === JSON.stringify(order) && groups.every((g) => digits(g.count) === facets[g.standing]), [groups, facets]);
  const rail = await E(`[...document.querySelectorAll("ev-rail a")].map((a) => a.innerText.replace(/\\s+/g, " ").trim())`);
  check(`inbox ${tag}: the rail counts every bucket and project as the summary does`, rail[0].includes(num(A.summary.coverage.runs)) && order.every((s) => rail.some((r) => r.endsWith(num(facets[s])))) && A.summary.projects.every((p) => rail.some((r) => r.startsWith(p.label) || r.includes(p.label))), [rail, order]);
  if (facets.needs_human) {
    const first = await E(`(() => { const r = document.querySelector('[data-group="needs_human"] [data-row]'); return r ? { text: r.innerText, inView: r.getBoundingClientRect().bottom <= innerHeight } : null; })()`);
    check(`inbox ${tag}: a run that needs a human is on the first screen and says what is asked`, first && first.inView && /Decide a claim|Confirm a source|Approve|Consent|Freeze/.test(first.text), first);
    // item 4: "Needs a human" is the observer's needs order, the oldest question first, never one project's runs in a block
    const keys = await E(`[...document.querySelectorAll('[data-group="needs_human"] [data-row]')].map((r) => r.dataset.row)`);
    const asked = (await A.api("/api/runs", "&standing=needs_human&limit=200")).rows, byKey = new Map(asked.map((r) => [r.key, r]));
    const times = keys.map((k) => byKey.get(k)?.asked_at ?? null), timed = times.filter((x) => x !== null);
    const several = new Set(asked.map((r) => r.project)).size > 1, projects = new Set(keys.map((k) => byKey.get(k)?.project));
    check(`inbox ${tag}: the needs rows are the oldest question first (the summary's needs order), not one project's when several have needs`,
      keys.length > 0 && JSON.stringify(keys) === JSON.stringify(A.summary.needs.slice(0, keys.length).map((n) => n.key))
      && timed.every((x, i) => i === 0 || timed[i - 1] <= x) && times.every((x, i) => x !== null || times.slice(i).every((y) => y === null))
      && (!several || keys.length < 2 || projects.size > 1), [times.slice(0, 12), [...projects]]);
  }
  const strip = await A.text("[aria-label='What the period held']");
  const month = await A.api("/api/summary", `&since=${Math.floor(Date.now() / 3_600_000) * 3600 - 30 * 86400}`);
  check(`inbox ${tag}: the strip counts what the period held, as the observer does for that window`, strip.includes(num(month.aggregates.facts.decisions)) && strip.includes(num(month.aggregates.facts.failed_audits)) && strip.includes(num(month.aggregates.facts.refutations)), [strip.slice(0, 200), month.aggregates.facts]);
}

// ---- J2 · narrowing, and the numbers that go with it ----------------------------------------------------------------------
async function narrowing(A) {
  const tag = `${A.src.name} ${A.size}`, E = A.E, facets = A.summary.facets.standing;
  const bucket = A.summary.marks.standings.filter((s) => facets[s] > 0).sort((a, b) => facets[b] - facets[a])[0];
  A.reset(); await A.go("&view=inbox");
  await A.click(`ev-rail a[href*="standing=${bucket}"]`);
  check(`narrowing ${tag}: a bucket in the rail lists exactly its runs, with a chip that says why (1 click)`, A.clicks === 1 && (await A.url()).includes(`standing=${bucket}`) && digits(await A.text("ev-work h1")) === facets[bucket] && (await A.count("[data-chip]")) === 1, [await A.url(), await A.text("ev-work h1"), facets[bucket]]);
  await A.click("[data-chip]");
  check(`narrowing ${tag}: removing the chip lists every run again`, digits(await A.text("ev-work h1")) === A.summary.coverage.runs && !(await A.url()).includes("standing="), [await A.url(), await A.text("ev-work h1")]);
  const project = A.summary.projects[0];
  await A.go(`&view=work&project=${encodeURIComponent(project.id)}`);
  check(`narrowing ${tag}: a project filter lists the project's runs and says its name`, digits(await A.text("ev-work h1")) === project.runs && (await A.text("[data-chip]")).includes(project.label), [await A.text("ev-work h1"), project]);
  // each strip number is a link to the runs it counts, in the same window
  await A.go("&view=inbox");
  const facts = A.summary.aggregates.facts;
  await A.click("a[data-stat]:nth-of-type(1)");
  const runsLink = await A.url();
  const stat = await E(`[...document.querySelectorAll("a[data-stat]")].length`);
  await A.go("&view=inbox");
  const links = await E(`[...document.querySelectorAll("a[data-stat]")].map((a) => ({ label: a.innerText.replace(/\\s+/g, " ").trim(), href: a.getAttribute("href") }))`);
  const caught = links.find((l) => /failed audits?\b/.test(l.label));
  await A.click(`a[data-stat][href*="flag=failed_audit"]`);
  const n = digits(await A.text("ev-work h1"));
  check(`narrowing ${tag}: the strip's failed-audits number leads to exactly that many runs (1 click)`, !!caught && n === facts.failed_audits && stat >= 3 && /since=|view=work/.test(runsLink), [n, facts, caught, runsLink]);
  await A.go("&view=inbox");
  const week = await E(`document.querySelector('a[href*="period=7"]')?.getAttribute("href")`);
  await A.click('a[href*="period=7"]');
  const weekSummary = await A.api("/api/summary", `&since=${Math.floor(Date.now() / 3_600_000) * 3600 - 7 * 86400}`);
  check(`narrowing ${tag}: choosing 7 days counts the week as the observer does and the URL remembers it`, !!week && (await A.url()).includes("period=7") && (await A.text("[aria-label='What the period held']")).includes(num(weekSummary.aggregates.facts.decisions)), [await A.url(), weekSummary.aggregates.facts]);
}

// ---- J3 · the calendar --------------------------------------------------------------------------------------------------------
async function calendar(A) {
  const tag = `${A.src.name} ${A.size}`, E = A.E;
  A.reset(); await A.go("&view=inbox");
  const cells = await E(`[...document.querySelectorAll("[data-calendar] [data-cell]")].map((c) => ({ title: c.title, standing: c.dataset.standing, h: c.getBoundingClientRect().height, href: c.getAttribute("href") }))`);
  const urgent = cells.filter((c) => c.standing === "needs_human"), calm = cells.filter((c) => c.standing === "proven");
  check(`calendar ${tag}: runs are drawn per project and day, each cell named in words (project, day, count, standing)`, cells.length > 20 && cells.every((c) => /\d{4}/.test(c.title) && /run/.test(c.title)), cells.slice(0, 3));
  check(`calendar ${tag}: a day that needs a human is taller as well as coloured (size and colour, not colour alone)`, urgent.length > 0 && calm.length > 0 && Math.min(...urgent.map((c) => c.h)) > Math.max(...calm.map((c) => c.h)), [urgent[0], calm[0]]);
  const pick = cells.find((c) => c.standing === "needs_human");
  await A.click(`[data-cell][title="${pick.title}"]`);
  const shown = digits(await A.text("ev-work h1")), n = digits(pick.title.match(/(\d+) runs?/)?.[1] ?? 0);
  check(`calendar ${tag}: a cell leads to that project's runs since that day (1 click)`, A.clicks === 1 && (await A.url()).includes("since=") && (await A.url()).includes("project=") && shown >= n && n > 0, [await A.url(), shown, n]);
}

// ---- J4 · one run: proof, audits, attempts, why it stopped ---------------------------------------------------------------------
async function sheet(A) {
  const tag = `${A.src.name} ${A.size}`, E = A.E, P = A.P;
  const id = A.id("verification");
  A.reset(); await A.go("&view=inbox");
  await A.click(`[data-group="needs_human"] a[data-run="${id}"]`);
  const u = await A.url();
  check(`sheet ${tag}: a run opens beside the list from the first screen with one click, and the URL names it`, A.clicks === 1 && u.includes(`run=${id}`) && (await A.count("ev-sheet h1")) === 1 && (await A.text("ev-sheet")).includes("Now"), [u]);
  const detail = await A.api("/api/run", `&key=${id}`);
  const now = await A.text("ev-sheet [data-section='now']");
  check(`sheet ${tag}: it says who acts and where, in the observer's own words, and the session to copy`, detail.outstanding.every((o) => now.includes(o.message)) && /A human · when the agent next reports/.test(now) && (await A.count("ev-sheet ev-copy button")) >= 1, now.slice(0, 300));
  check(`sheet ${tag}: the list stays visible with the open run marked`, (await E(`document.querySelector('a[data-run="${id}"][aria-current]') !== null && document.querySelector("ev-inbox, ev-work").checkVisibility()`)), null);
  const claim = detail.row.claims.find((c) => c.kind === "needs-decision") ?? detail.row.claims[0];
  A.reset();
  await A.click(`summary[data-claim="${claim.id}"]`);
  check(`sheet ${tag}: a claim opens in place with its proof, the URL keeps it (1 click)`, A.clicks === 1 && (await A.url()).includes(`claim=${claim.id}`) && (await E(`document.querySelector('[data-claim-row="${claim.id}"] details').open`)) && /Evidence/.test(await A.text(`[data-claim-row="${claim.id}"]`)), await A.url());
  await P.key("Escape"); await P.sleep(350);
  const afterOne = await A.url();
  await P.key("Escape"); await P.sleep(350);
  check(`sheet ${tag}: Esc closes the claim, then the run`, !afterOne.includes("claim=") && afterOne.includes("run=") && !(await A.url()).includes("run=") && (await A.count("ev-sheet h1")) === 0, [afterOne, await A.url()]);
  await A.go(`&run=${id}&claim=${claim.id}`);
  check(`sheet ${tag}: opening the link restores the run and its claim`, (await E(`document.querySelector('[data-claim-row="${claim.id}"] details').open`)) && (await A.count("ev-sheet h1")) === 1, null);
  P.errors.length = 0;
}

async function ended(A) {
  const tag = `${A.src.name} ${A.size}`, E = A.E;
  const id = A.id("terminal-consent") ?? A.id("refuted");
  await A.go(`&run=${id}`);
  const detail = await A.api("/api/run", `&key=${id}`);
  const now = await A.text("ev-sheet [data-section='now']");
  check(`ended ${tag}: a run that stopped says it has ended and lists what it left unresolved, as recorded`, /has ended/.test(now) && detail.residuals.every((r) => now.includes(r.message)), now.slice(0, 300));
  const dropped = await A.api("/api/run", `&key=${A.id("refuted")}`);
  await A.go(`&run=${A.id("refuted")}`);
  const text = await A.text("ev-sheet [data-section='dropped']");
  check(`ended ${tag}: the claims it no longer stands on are listed with the gate's own reason`, dropped.dropped.length > 0 && dropped.dropped.every((d) => text.includes(d.text.slice(0, 30))) && /Refuted|Spike failed|Rejected|Removed/.test(text), text.slice(0, 200));
  void E;
}

async function attempts(A) {
  const tag = `${A.src.name} ${A.size}`, E = A.E, P = A.P;
  const id = A.id("audit-failed"), n = (await A.api("/api/run", `&key=${id}`)).attempts.length;
  await A.go(`&run=${id}`);
  const table = await A.text("ev-sheet [data-section='attempts']");
  const head = await E(`[...document.querySelectorAll("ev-sheet .ev-compare thead th")].length`);
  check(`attempts ${tag}: the attempts of a session sit side by side, one column each, the open one marked`, n >= 2 && head === n && /Goal/.test(table) && (await A.count("ev-sheet a[data-attempt]")) === n - 1, [n, head, table.slice(0, 200)]);
  const fits = await E(`(() => { const t = document.querySelector("ev-sheet .ev-compare"), s = document.getElementById("sheet-pane").getBoundingClientRect();
    return [...t.querySelectorAll("th, td")].every((c) => c.getBoundingClientRect().right <= s.right + 0.5) && t.getBoundingClientRect().right <= s.right + 0.5; })()`);
  check(`attempts ${tag}: the comparison fits the sheet's width, every attempt's column whole (no sideways cut)`, fits, fits);
  await A.reset();
  await A.click("ev-sheet a[data-attempt]");
  check(`attempts ${tag}: another attempt opens from the comparison (1 click)`, A.clicks === 1 && !(await A.url()).includes(`run=${id}`) && (await A.count("ev-sheet h1")) === 1, await A.url());
  await A.go(`&run=${A.id("unreadable")}`);
  check(`attempts ${tag}: a run the observer could not read opens, says it was not read, and throws nothing`, P.errors.length === 0 && /not read in full|Not read/i.test(await A.text("ev-sheet")), [P.errors, (await A.text("ev-sheet")).slice(0, 200)]);
}

async function audits(A) {
  const tag = `${A.src.name} ${A.size}`;
  // every audited fixture run (P3b: an independence the fixtures do not hold is worded in views.test.mjs)
  for (const scenario of ["audit-failed", "converged", "reaudit"]) {
    await A.go(`&run=${A.id(scenario)}`);
    const detail = await A.api("/api/run", `&key=${A.id(scenario)}`);
    const text = await A.text("ev-sheet [data-section='audit']");
    check(`audits ${tag}: every audit verdict of ${scenario} is listed with its auditor and how independent it was`, detail.audits.length > 0 && detail.audits.every((a) => a.independence !== null && text.includes(copy[`independence.${a.independence}`]) && a.findings.every((f) => text.includes(f))), text.slice(0, 300));
  }
}

// ---- J5 · finding a remembered goal -----------------------------------------------------------------------------------------------
async function find(A) {
  const tag = `${A.src.name} ${A.size}`;
  A.reset(); await A.go("&view=inbox");
  const result = await A.api("/api/search", "&q=tile");
  await A.type("#search", "tile");
  check(`find ${tag}: typing a word in the search lists the runs the observer finds, and the URL keeps it (0 clicks)`, A.clicks === 0 && digits(await A.text("ev-work h1")) === result.total && (await A.url()).includes("q=tile") && (await A.text("[data-chip]")).includes("tile"), [await A.text("ev-work h1"), result.total, await A.url()]);
  await A.click("[data-chip]");
  check(`find ${tag}: removing the search chip leaves the search`, !(await A.url()).includes("q="), await A.url());
  await A.type("#search", "zzzzqqqq");
  check(`find ${tag}: nothing found says so`, /No run matches/.test(await A.text("ev-work")), await A.text("ev-work"));
}

// ---- J6 · the keyboard --------------------------------------------------------------------------------------------------------------
async function keyboard(A) {
  const tag = `${A.src.name} ${A.size}`, E = A.E, P = A.P;
  await A.go("&view=work");
  await P.key("j", "KeyJ"); await P.key("j", "KeyJ"); await P.sleep(250);
  const second = await E(`document.activeElement.dataset.run ?? null`);
  const rows = await E(`[...document.querySelectorAll("ev-work [data-row] a")].map((a) => a.dataset.run)`);
  check(`keyboard ${tag}: j moves down the rows, one at a time, and focus follows`, second === rows[1], [second, rows.slice(0, 3)]);
  await P.key("k", "KeyK"); await P.sleep(200);
  check(`keyboard ${tag}: k moves back up`, (await E(`document.activeElement.dataset.run`)) === rows[0], null);
  await P.key("Enter"); await P.sleep(500);
  check(`keyboard ${tag}: Enter opens the run`, (await A.url()).includes(`run=${rows[0]}`) && (await A.count("ev-sheet h1")) === 1, await A.url());
  await P.key("Escape"); await P.sleep(300);
  check(`keyboard ${tag}: Esc closes it`, !(await A.url()).includes("run="), await A.url());
  await P.key("/", "Slash"); await P.sleep(150);
  check(`keyboard ${tag}: / goes to the search`, (await E(`document.activeElement.id`)) === "search", null);
  await E(`document.activeElement.blur()`);
  await P.key("g", "KeyG"); await P.key("n", "KeyN"); await P.sleep(500);
  check(`keyboard ${tag}: g n lists what needs a human`, (await A.url()).includes("standing=needs_human"), await A.url());
  await P.key("?", "Slash"); await P.sleep(250);
  check(`keyboard ${tag}: ? puts a word beside every glyph, and the URL keeps it`, (await A.url()).includes("words=1") && (await E(`document.body.hasAttribute("data-words")`)), await A.url());
  check(`keyboard ${tag}: typing in the search is never a command`, await (async () => { await E(`document.getElementById("search").focus()`); await P.key("j", "KeyJ"); return (await E(`document.activeElement.id`)) === "search"; })(), null);
}

// ---- J7 · standing rules ------------------------------------------------------------------------------------------------------------------
async function history(A) {
  const E = A.E, P = A.P;
  await A.go("&view=inbox");
  await A.click('ev-rail a[href*="standing="]');
  const there = await A.url();
  await A.click("ev-work a[data-run]");
  await E(`history.back()`); await P.sleep(500);
  check("history: Back after opening a run returns to the list, keeping its filter", (await A.url()) === there && (await A.count("ev-sheet h1")) === 0, [await A.url(), there]);
  await E(`history.forward()`); await P.sleep(500);
  check("history: Forward opens the run again", (await A.url()).includes("run=") && (await A.count("ev-sheet h1")) === 1, await A.url());
  await P.send("Page.reload"); await P.sleep(300); await P.waitReady(); await P.sleep(500);
  check("history: reloading restores the same place", (await A.url()).includes("run=") && (await A.count("ev-sheet h1")) === 1 && (await A.count("ev-work [data-row]")) > 0, await A.url());
}

async function safeText(A) {
  const E = A.E, P = A.P, evil = `<img src=x onerror="window.pwned=1"><b>bold</b>`;
  await P.send("Page.addScriptToEvaluateOnNewDocument", { source: `(() => { const real = window.fetch; window.fetch = async (...a) => {
    const r = await real(...a), url = String(a[0]?.url ?? a[0]);
    if (r.status !== 200 || !/\\/api\\/(runs|search|run)\\b/.test(url)) return r;
    const j = await r.clone().json(), evil = ${JSON.stringify(evil)};
    for (const row of j.rows ?? []) { row.goal = evil; row.project_label = evil; }
    if (j.row) { j.row.goal = evil; for (const c of j.row.claims ?? []) c.text = evil; }
    for (const a of j.attempts ?? []) a.goal = evil;
    return new Response(JSON.stringify(j), { status: 200, headers: r.headers });
  }; })()` });
  await A.go(`&run=${A.id("verification")}&claim=${(await A.api("/api/run", `&key=${A.id("verification")}`)).row.claims[0].id}`);
  check("safe text: markup in a goal or a claim is shown as text and never becomes an element", !(await E(`window.pwned`)) && (await E(`document.querySelector("ev-sheet h1").innerHTML.includes("<img")`)) === false && (await A.text("ev-sheet h1")).includes("<img"), await A.text("ev-sheet h1"));
}

async function recovery(A) {
  const E = A.E, P = A.P, rules = { run: 0, runAll: false, summary: false };
  await P.send("Fetch.enable", { patterns: [{ urlPattern: "*/api/*" }] });
  P.on("Fetch.requestPaused", async (p) => {
    const url = p.request.url;
    if (rules.summary && /\/api\/summary/.test(url)) return P.send("Fetch.fulfillRequest", { requestId: p.requestId, responseCode: 500, body: Buffer.from("no").toString("base64") });
    if (/\/api\/run\?/.test(url) && (rules.runAll || rules.run > 0)) { if (rules.run > 0) rules.run--; return P.send("Fetch.fulfillRequest", { requestId: p.requestId, responseCode: 500, body: Buffer.from("no").toString("base64") }); }
    return P.send("Fetch.continueRequest", { requestId: p.requestId });
  });
  rules.runAll = true;
  await A.go(`&run=${A.id("verification")}`);
  const failedAt = await A.text("ev-sheet");
  check("recovery: a sheet that failed says so and offers Retry", /reported an error/.test(failedAt) && /Retry/.test(failedAt), failedAt.slice(0, 200));
  rules.runAll = false;
  await A.click("[data-retry]"); await P.sleep(500);
  check("recovery: Retry brings the sheet in and the notice goes", !(await E(`!!document.querySelector("[data-retry]")`)) && /Now/.test(await A.text("ev-sheet")), (await A.text("ev-sheet")).slice(0, 200));
  rules.run = 1;
  await A.go(`&run=${A.id("converged")}`);
  const first = /reported an error/.test(await A.text("ev-sheet"));
  await P.sleep(2800);
  check("recovery: a failed sheet is asked again by itself, later each time, and recovers without a click", first && !(await E(`!!document.querySelector("[data-retry]")`)) && /has ended/.test(await A.text("ev-sheet")), (await A.text("ev-sheet")).slice(0, 200));
  await A.go("&view=inbox");
  const before = await A.text("#status");
  rules.summary = true;
  await P.sleep(catalogue.poll_ms + 2500);
  const during = await A.text("#status");
  check("recovery: a failed poll says so beside the last good check, and the lists stay", /reported an error/.test(during) && /Last checked/.test(during) && /Last checked/.test(before) && (await A.count("[data-row]")) > 0, [before, during]);
  rules.summary = false;
  await P.sleep(catalogue.poll_ms * 2 + 4000);
  check("recovery: when the observer answers again the notice goes", !/reported an error/.test(await A.text("#status")), await A.text("#status"));
}

async function hidden(A) {
  const E = A.E, P = A.P;
  await A.go("&view=inbox");
  const polls = () => E(`performance.getEntriesByType("resource").filter((r) => r.name.includes("/api/summary")).length`);
  await E(`Object.defineProperty(document, "hidden", { get: () => true, configurable: true })`);
  await P.sleep(catalogue.poll_ms + 1500);
  const n = await polls();
  await P.sleep(catalogue.poll_ms + 1500);
  check("hidden tab: the poll is not asked while the tab is hidden", (await polls()) === n, [n, await polls()]);
  await E(`Object.defineProperty(document, "hidden", { get: () => false, configurable: true }); document.dispatchEvent(new Event("visibilitychange"))`);
  await P.sleep(900);
  check("hidden tab: showing it again asks at once", (await polls()) === n + 1, [n, await polls()]);
}

// ---- L · layout invariants (both schemes, both sizes): nothing scrolls sideways, no overlay box covers another, no heading,
// chip or name is clipped unless its title holds it whole, and the focused element is inside the window ----------------------
const INVARIANTS = `(() => {
  const box = (e) => e.getBoundingClientRect(), seen = (e) => e.checkVisibility() && box(e).width > 0;
  const hit = (a, b) => a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
  const overlays = [...document.querySelectorAll("[data-callout], [data-island-label], [data-hl], [data-legend], [data-chip], [data-crumbs], [data-needs-pill], [data-more-needs], [data-chips], [data-key-toggle], [data-card], [data-orbit], [data-orbit-label], [data-run-word], [data-chain]")].filter(seen);
  const overlaps = [];
  overlays.forEach((a, i) => overlays.slice(i + 1).forEach((b) => { if (!a.contains(b) && !b.contains(a) && hit(box(a), box(b))) overlaps.push([a.outerHTML.slice(0, 60), b.outerHTML.slice(0, 60)]); }));
  const clipped = [...document.querySelectorAll("h1, h2, [data-chip], [data-island-label], [data-callout], [data-crumbs], [data-crumb], [data-needs-pill], [data-hl], .badge, [data-time], [data-card] span, [data-orbit-label]")].filter(seen)
    .filter((e) => !e.classList.contains("sr-only") && e.scrollWidth > e.clientWidth + 1 && !(e.title || e.closest("[title]")?.title || "").trim()).map((e) => e.outerHTML.slice(0, 80));
  // nothing in a sheet or a project card runs past its right edge: a long unbroken token (a digest, a path) wraps (L8)
  for (const pane of [...document.querySelectorAll("#sheet-pane, [data-card]")].filter(seen)) {
    const edge = box(pane).right + 0.5;
    // the text itself, not its box: a word too long for its line overflows a box that fits
    const past = [], walk = document.createTreeWalker(pane, NodeFilter.SHOW_TEXT), range = document.createRange();
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      if (!n.textContent.trim() || !n.parentElement?.checkVisibility() || n.parentElement.closest(".truncate, .sr-only, [title]")) continue;
      range.selectNodeContents(n);
      if ([...range.getClientRects()].some((r) => r.width > 0 && r.right > edge)) past.push(n.textContent.trim().slice(0, 40));
    }
    if (past.length) clipped.push(\`past the edge of \${pane.id || "a card"}: \${past.slice(0, 3).join(" | ")}\`);
  }
  // a sheet's attempts comparison fits the sheet's width: no cell past its right edge, nothing scrolled or cut sideways
  for (const table of [...document.querySelectorAll("ev-sheet .ev-compare")].filter(seen)) {
    const sheet = box(table.closest("#sheet-pane") ?? table.closest("ev-sheet"));
    const out = [...table.querySelectorAll("th, td")].filter(seen).filter((c) => box(c).right > sheet.right + 0.5 || box(c).left < sheet.left - 0.5);
    for (let e = table; e && e.id !== "sheet-pane"; e = e.parentElement) if (e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== "visible") out.push(e);
    if (box(table).right > sheet.right + 0.5 || out.length) clipped.push(\`attempts past the sheet: \${out.map((c) => c.textContent.slice(0, 30)).join(" | ")}\`);
  }
  // one line: the crumbs and a row's time never wrap (a second line would be clipped by the bar or the row)
  const line = (e) => { const cs = getComputedStyle(e), lh = parseFloat(cs.lineHeight); return Number.isFinite(lh) ? lh : 1.6 * parseFloat(cs.fontSize); };
  for (const e of [...document.querySelectorAll("[data-crumbs] > *, [data-time]")].filter(seen)) if (e.getClientRects().length > 1 || box(e).height > 1.5 * line(e)) clipped.push(\`wrapped: \${e.outerHTML.slice(0, 80)}\`);
  // alignment: in a list every row's glyph, goal, project and time share their edges, a row's parts one middle line, and
  // the chips of one bar one line
  const ragged = [], edge = (name, els, side) => { const xs = [...new Set(els.filter(seen).map((e) => Math.round(box(e)[side])))]; if (xs.length > 1) ragged.push(\`\${name} \${side}: \${xs.join(",")}\`); };
  for (const list of document.querySelectorAll("[data-rows]")) {
    const rows = [...list.querySelectorAll("a.ev-row")].filter(seen);
    edge("rows", rows, "left"); edge("rows", rows, "right");
    edge("glyphs", rows.map((r) => r.firstElementChild), "left");
    edge("goals", rows.map((r) => r.querySelector("[data-goal]")), "left");
    edge("projects", rows.map((r) => r.querySelector("[data-project]")), "left");
    edge("times", rows.map((r) => r.lastElementChild), "right");
    for (const r of rows) { const mids = [...new Set([...r.children].filter(seen).map((c) => Math.round(box(c).top + box(c).height / 2)))]; if (Math.max(...mids) - Math.min(...mids) > 1) ragged.push(\`row \${r.dataset.run} middles: \${mids.join(",")}\`); }
  }
  for (const bar of document.querySelectorAll("[data-bottom], [data-top]")) {
    const mids = [...new Set([...bar.querySelectorAll(":scope > *, :scope > div > *")].filter(seen).map((c) => Math.round(box(c).top + box(c).height / 2)))];
    if (mids.length && Math.max(...mids) - Math.min(...mids) > 1) ragged.push(\`\${bar.hasAttribute("data-top") ? "top" : "bottom"} bar middles: \${mids.join(",")}\`);
  }
  const f = document.activeElement, fb = f && f !== document.body ? box(f) : null;
  return { sideways: document.documentElement.scrollWidth > innerWidth, overlaps, clipped, ragged,
    focus: fb === null || (fb.right > 0 && fb.bottom > 0 && fb.left < innerWidth && fb.top < innerHeight) };
})()`;
async function invariants(A, where) {
  const got = await A.E(INVARIANTS);
  check(`layout ${A.size} ${A.scheme}: ${where}: nothing sideways, no overlay over another, nothing clipped, focus in the window`, !got.sideways && got.overlaps.length === 0 && got.clipped.length === 0 && got.focus, got);
  check(`layout ${A.size} ${A.scheme}: ${where}: aligned: a list's rows share their edges and columns, a row's parts one middle, a bar's chips one line`, got.ragged.length === 0, got.ragged);
}
/** The look (T2b): one family, IBM Plex Mono, for the body, loaded (not a fallback); and every calendar cell drawn in its
 * tone's solid colour, at least 3:1 against the page (WCAG 1.4.11 non-text contrast), in this scheme. */
async function looks(A) {
  const got = await A.E(`(async () => { await document.fonts.ready;
    const rgb = (c) => c.match(/[\\d.]+/g).slice(0, 3).map(Number), lum = (c) => { const [r, g, b] = rgb(c).map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
    const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
    const page = getComputedStyle(document.body).backgroundColor;
    const cells = [...document.querySelectorAll("[data-cell]")].map((c) => ({ standing: c.dataset.standing, bg: getComputedStyle(c).backgroundColor, r: ratio(getComputedStyle(c).backgroundColor, page) }));
    const cut = [...document.querySelectorAll("ev-rail a span.truncate")].filter((e) => e.scrollWidth > e.clientWidth + 1).map((e) => e.innerText);
    return { cut, family: getComputedStyle(document.body).fontFamily, loaded: document.fonts.check('300 12px "IBM Plex Mono"') && [...document.fonts].some((f) => f.family.replace(/"/g, "") === "IBM Plex Mono" && f.status === "loaded"), cells, page }; })()`);
  check(`look ${A.size} ${A.scheme}: the body is IBM Plex Mono, loaded`, /^"?IBM Plex Mono"?,/.test(got.family) && got.loaded, [got.family, got.loaded]);
  check(`look ${A.size} ${A.scheme}: every bucket and project in the rail shows its name in full (the mono widths)`, got.cut.length === 0, got.cut);
  const faint = got.cells.filter((c) => !(c.r >= 3));
  check(`look ${A.size} ${A.scheme}: every calendar cell is its tone's solid colour, at least 3:1 against the page (${got.cells.length} cells)`, got.cells.length > 0 && faint.length === 0, [faint.slice(0, 5), got.page]);
}
async function layoutInvariants(A) {
  await A.go(""); await A.P.sleep(400); await invariants(A, "the Universe");
  const project = A.summary.projects[0];
  await A.go(`&zoom=${project.id}`); await A.P.sleep(700); await invariants(A, "an island");
  const run = A.id("verification");
  await A.go(`&run=${run}`); await A.P.sleep(700); await invariants(A, "a run's zoom");
  await A.go("&view=inbox"); await invariants(A, "the Inbox");
  await looks(A);
  const id = A.id("verification");
  await A.go(`&view=inbox&run=${id}`); await invariants(A, "a run open");
  await A.E(`document.querySelector("[data-claim]")?.focus()`); await invariants(A, "a claim focused");
  await A.go(`&view=inbox&run=${A.id("audit-failed")}`); await invariants(A, "the attempts compared");
  await A.go("&view=work"); await invariants(A, "the work view");
}

async function layout(A) {
  const E = A.E, shown = (sel) => E(`document.querySelector(${JSON.stringify(sel)})?.checkVisibility() ?? false`);
  await A.go("&view=inbox");
  check(`layout ${A.size}: nothing open, no sheet is drawn and the page does not scroll sideways`, !(await shown("#sheet-pane")) && (await E(`document.documentElement.scrollWidth <= innerWidth`)), null);
  const id = A.id("verification");
  await A.go(`&run=${id}&claim=${(await A.api("/api/run", `&key=${id}`)).row.claims[0].id}`);
  check(`layout ${A.size}: with a run and a claim open the page does not scroll sideways and the sheet fits`, (await shown("#sheet-pane")) && (await E(`document.documentElement.scrollWidth <= innerWidth && document.getElementById("sheet-pane").scrollWidth <= document.getElementById("sheet-pane").clientWidth + 1`)), await E(`[document.documentElement.scrollWidth, innerWidth]`));
}

async function narrow(src) {
  await withPage(`${src.base}/app/?data=${src.data}&view=inbox`, "800x700", async (P) => {
    const E = P.evalJs;
    await P.waitReady(); await P.sleep(400);
    const shown = (sel) => E(`document.querySelector(${JSON.stringify(sel)})?.checkVisibility() ?? false`);
    check("narrow: below the large breakpoint the home shows alone", (await shown("#main")) && !(await shown("#sheet-pane")) && !(await shown("#rail-pane")), null);
    await E(`document.querySelector('a[data-run]').click()`); await P.sleep(500);
    check("narrow: an open run takes the whole width and hides the list", !(await shown("#main")) && (await shown("#sheet-pane")), null);
    await E(`document.querySelector("[data-close]").click()`); await P.sleep(500);
    check("narrow: Close brings the list back", (await shown("#main")) && !(await shown("#sheet-pane")), null);
  });
}

async function empty() {
  await withPage(`${EMPTY}/app/`, "1440x900", async (P) => {
    await P.waitReady(); await P.sleep(400);
    const text = await P.evalJs(`document.body.innerText`);
    check("empty store: the home says there are no runs yet, and the page has no error", /no runs yet/.test(text) && P.errors.length === 0, [P.errors, text.slice(0, 200)]);
  });
}

// ---- J7b · motion is for live state, and off when the person asks for none ---------------------------------------------------------
async function motion(A) {
  const E = A.E, P = A.P;
  const animations = () => E(`document.getAnimations().filter((a) => a.playState === "running").length`);
  await A.go("&view=work&standing=needs_human&words=1");
  check("motion: a glyph of a run still asking breathes, and a settled run's does not", (await animations()) > 0 && (await E(`document.querySelectorAll('ev-work [data-live]').length`)) > 0, await animations());
  await A.go("&view=work&standing=proven");
  check("motion: a finished run's glyph does not move", (await E(`document.querySelectorAll('ev-work [data-row]').length`)) > 0 && (await E(`document.querySelectorAll('ev-work [data-live]').length`)) === 0, null);
  await P.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await A.go("&view=work&standing=needs_human");
  await P.sleep(400);
  check("motion: with reduced motion asked for, nothing animates", (await animations()) === 0, await animations());
}

// ---- J8a · the store moves on while a long list is being read: a cursor is refused, the walk starts again ---------------------
async function stale(A) {
  const E = A.E, P = A.P, rules = { refuse: 1, refused: 0 };
  await P.send("Fetch.enable", { patterns: [{ urlPattern: "*/api/runs*" }] });
  P.on("Fetch.requestPaused", async (p) => {
    if (rules.refuse > 0 && /cursor=/.test(p.request.url)) {
      rules.refuse--; rules.refused++;
      const token = (await A.api("/api/summary")).token;
      return P.send("Fetch.fulfillRequest", { requestId: p.requestId, responseCode: 409, responseHeaders: [{ name: "Content-Type", value: "application/json" }], body: Buffer.from(JSON.stringify({ token })).toString("base64") });
    }
    return P.send("Fetch.continueRequest", { requestId: p.requestId });
  });
  await A.go("&view=work");
  const total = digits(await A.text("ev-work h1"));
  await E(`(async () => { const box = document.querySelector("[data-scroll]"); for (let i = 0; i < 400; i++) { box.scrollTop = box.scrollHeight; await new Promise((r) => setTimeout(r, 40)); if (box.scrollTop + box.clientHeight >= box.scrollHeight - 2 && document.querySelector("[data-foot]").innerText === "") break; } })()`);
  await P.sleep(800);
  const ids = await E(`[...document.querySelectorAll("ev-work [data-row] a")].map((a) => a.dataset.run)`);
  const height = await E(`document.querySelector("[data-scroll]").scrollHeight`);
  check("stale: a refused cursor starts the walk again; the list still reaches its end, no row doubled, no notice", rules.refused === 1 && new Set(ids).size === ids.length && height > total * 20 && !/hit an error|reported an error/.test(await A.text("#status")) && (await A.text("[data-foot]")) === "", [rules.refused, ids.length, height, total, await A.text("[data-foot]")]);
}

// ---- J8 · volume ----------------------------------------------------------------------------------------------------------------------------
async function volume(A) {
  const tag = `${A.src.name} ${A.size}`, E = A.E, P = A.P;
  await A.go("&view=work", { });
  const total = digits(await A.text("ev-work h1"));
  const before = await A.count("ev-work [data-row]");
  const requests = () => E(`performance.getEntriesByType("resource").filter((r) => r.name.includes("/api/runs")).length`);
  const r0 = await requests();
  await E(`(async () => { const box = document.querySelector("[data-scroll]"); for (let i = 0; i < 400; i++) { box.scrollTop = box.scrollHeight; await new Promise((r) => setTimeout(r, 40)); if (document.querySelector("[data-foot]").innerText === "" && box.scrollTop + box.clientHeight >= box.scrollHeight - 2) break; } })()`);
  await P.sleep(800);
  const nodes = await A.count("ev-work [data-row]");
  const lastId = await E(`[...document.querySelectorAll("ev-work [data-row] a")].pop().dataset.run`);
  const held = await E(`document.querySelector("[data-scroll]").scrollHeight`);
  const last = (await A.api("/api/runs", `&limit=200`)).total;
  check(`volume ${tag}: ${num(total)} runs are listed with at most ${catalogue.max_rows} row nodes, before and after scrolling to the end`, before <= catalogue.max_rows && nodes <= catalogue.max_rows && nodes > 5, [before, nodes]);
  check(`volume ${tag}: the end of the list is reachable by scrolling, and it was read in pages, not at once`, held > total * 20 && (await requests()) - r0 >= 2 && /^[0-9a-f]{12}$/.test(lastId), [held, (await requests()) - r0, lastId, last]);
  await P.send("Page.navigate", { url: `${A.src.base}/app/?data=${A.src.data}` });
  const began = Date.now();
  await P.sleep(200); await P.waitReady();
  const first = await E(`[...performance.getEntriesByType("resource")].filter((r) => r.name.includes("/api/summary")).map((r) => r.responseEnd)[0]`);
  const drawn = await E(`performance.now()`);
  check(`volume ${tag}: the first render is within a second of the summary`, drawn - first <= 1000, [first, drawn, Date.now() - began]);
  await A.go("&view=inbox");
  check(`volume ${tag}: the home draws a bounded number of rows`, (await A.count("[data-row]")) <= 12 * 9 && (await A.count("[data-cell]")) > 0, await A.count("[data-row]"));
  await A.go("&view=work");
  const t0 = Date.now();
  await E(`document.querySelector("[data-scroll]").scrollTop = 100000`); await P.sleep(300);
  check(`volume ${tag}: scrolling stays responsive`, Date.now() - t0 < 3000, Date.now() - t0);
}

// ---- U · the Universe, the home: journeys 1-8 through the map, at the fixtures, 1k and 10k ---------------------------------
const U = "ev-universe";
/** The project id of run `id` in the summary's marks. */
const projectOf = (A, id) => A.summary.projects[A.summary.marks.project[A.summary.marks.id.indexOf(id)]]?.id;
/** The frames drawn while the camera flew, after `fly`: how many, their mean time (ms) and the fewest marks one drew. */
async function flight(A, fly) {
  await A.E(`document.querySelector("${U}").timings.length = 0`);
  await fly();
  await A.P.sleep(700);
  return A.E(`(() => { const f = document.querySelector("${U}").timings.filter((t) => t.flying); return { n: f.length, mean: f.reduce((s, t) => s + t.ms, 0) / Math.max(1, f.length), marks: Math.min(...f.map((t) => t.marks)) }; })()`);
}
/** A flight's frames: in the journeys (make ui-browser) by structure only, frames drawn in the air and each with marks on
 * it, never milliseconds (a loaded machine draws fewer, slower frames); the frame budget (16 ms) is the smoke run's
 * (make ui-smoke, in make verify only). */
function frames(A, what, f) {
  if (MODE === "smoke") check(`${what}: its frames average at most 16 ms`, f.n > 0 && f.mean <= 16, f);
  else check(`${what}: the camera flew, every frame in the air drawing marks`, f.n > 0 && f.marks > 0, f);
}

/** Wait until the observer has read every run of `base` (a large demo store is indexed in the background). */
async function settled(base) {
  for (let i = 0; i < 600; i++) { if ((await json(base, "/api/summary?source=store")).coverage.pending === 0) return; await new Promise((r) => setTimeout(r, 200)); }
}

async function universe(A) {
  const tag = `${A.src.name} ${A.size}`, E = A.E, P = A.P, s = A.summary, facets = s.facets.standing;
  // J1 · what needs me, and where · 0 clicks
  A.reset(); await A.go("");
  check(`universe ${tag}: the home is the Universe, every island drawn and listed in its mirror (0 clicks)`, (await E(`document.body.dataset.view`)) === "universe" && (await A.count("[data-mirror-island]")) === s.projects.length && (await A.count("[data-island-label]")) === Math.min(s.projects.length, catalogue.universe.labels), [await A.count("[data-mirror-island]"), s.projects.length]);
  // zoomed out by default: the store's camera is app/geometry.js fitView on the islands, wired as declared: every island
  // (with its name's band) inside the part no bar or key covers, fit_margin_px clear, centred there, one axis filled to the
  // margin, and the islands' box at least 70% of the view's short side: at fixture size too, a few islands fill the view
  const G = catalogue.universe, fit = await E(`(() => { const u = document.querySelector("${U}"), b = u.getBoundingClientRect(), [x0, y0, x1, y1, s] = (u.dataset.fit ?? "").split(" ").map(Number);
    return { x0, y0, x1, y1, s, w: b.width, h: b.height, key: !!document.querySelector("${U} [data-legend]")?.checkVisibility() }; })()`);
  const free = { l: G.fit_margin_px, t: G.hud_top_px + G.fit_margin_px, r: fit.w - (fit.key ? G.key_w_px + 2 * G.hud_pad_px : 0) - G.fit_margin_px, b: fit.h - G.hud_bottom_px - G.fit_margin_px };
  const inside = fit.x0 >= free.l - 1 && fit.y0 >= free.t - 1 && fit.x1 <= free.r + 1 && fit.y1 <= free.b + 1;
  const centred = Math.abs((fit.x0 + fit.x1) - (free.l + free.r)) <= 2 && Math.abs((fit.y0 + fit.y1) - (free.t + free.b)) <= 2;
  const filled = (Math.abs(fit.x1 - fit.x0 - (free.r - free.l)) <= 1 || Math.abs(fit.y1 - fit.y0 - (free.b - free.t)) <= 1) && Math.max(fit.x1 - fit.x0, fit.y1 - fit.y0) >= 0.7 * Math.min(fit.w, fit.h);
  check(`universe ${tag}: the store fills the view: every island inside the free part a margin clear, centred, one axis filled, the box at least 70% of the short side`, inside && centred && filled, [fit, free, { inside, centred, filled }]);
  const calls = await A.count("[data-callout]"), more = await A.text("[data-more-needs]");
  const needs = facets.needs_human ?? 0;
  check(`universe ${tag}: at most ${catalogue.universe.callouts} callouts say what is asked, then how many more need a human (the observer's count less those drawn)`, calls >= 1 && calls <= Math.min(catalogue.universe.callouts, needs) && (needs <= calls ? more === "" : digits(more) === needs - calls) && (calls === 0 || /Decide a claim|Confirm a source|Approve|Consent|Freeze/.test(await A.text("[data-callout]"))), [calls, more, needs]);
  const boxes = await E(`(() => { const r = (e) => { const b = e.getBoundingClientRect(); return { x: b.left, y: b.top, w: b.width, h: b.height }; };
    return { calls: [...document.querySelectorAll("[data-callout]")].map(r), names: [...document.querySelectorAll("[data-island-label]")].map(r),
      leaders: [...document.querySelectorAll("[data-leaders] line")].map((l) => ({ x: +l.getAttribute("x2"), y: +l.getAttribute("y2") })),
      stage: r(document.querySelector("${U} [data-stage]")) }; })()`);
  const hit = (a, b) => a.x < b.x + b.w - 0.5 && b.x < a.x + a.w - 0.5 && a.y < b.y + b.h - 0.5 && b.y < a.y + a.h - 0.5;
  const clash = boxes.calls.flatMap((a, i) => [...boxes.calls.slice(i + 1).filter((b) => hit(a, b)).map(() => `callout ${i} on a callout`), ...boxes.names.filter((n) => hit(a, n)).map(() => `callout ${i} on a name`)]);
  const led = boxes.leaders.length === boxes.calls.length && boxes.leaders.every((l, i) => { const b = boxes.calls[i], x = l.x + boxes.stage.x, y = l.y + boxes.stage.y; return x >= b.x - 1 && x <= b.x + b.w + 1 && y >= b.y - 1 && y <= b.y + b.h + 1; });
  check(`universe ${tag}: no callout overlaps another or covers an island's name, and each has a leader line to its mark`, clash.length === 0 && led, [clash, boxes.leaders.length, boxes.calls.length]);
  // a leader crosses no island's name and no other callout (Liang-Barsky, as app/geometry.js draws them)
  const lines = await E(`[...document.querySelectorAll("[data-leaders] line")].map((l) => ["x1", "y1", "x2", "y2"].map((k) => +l.getAttribute(k)))`);
  const cut = (x1, y1, x2, y2, b) => { let t0 = 0, t1 = 1; const dx = x2 - x1, dy = y2 - y1;
    for (const [p, q] of [[-dx, x1 - b.x - 1], [dx, b.x + b.w - 1 - x1], [-dy, y1 - b.y - 1], [dy, b.y + b.h - 1 - y1]]) { if (p === 0) { if (q < 0) return false; } else { const r = q / p; if (p < 0) t0 = Math.max(t0, r); else t1 = Math.min(t1, r); } }
    return t0 < t1; };
  const local = (b) => ({ ...b, x: b.x - boxes.stage.x, y: b.y - boxes.stage.y });
  const crossed = lines.flatMap(([x1, y1, x2, y2], i) => [...boxes.names.map(local).filter((n) => cut(x1, y1, x2, y2, n)).map(() => `leader ${i} over a name`),
    ...boxes.calls.map(local).filter((c, j) => j !== i && cut(x1, y1, x2, y2, c)).map(() => `leader ${i} over a callout`)]);
  check(`universe ${tag}: no leader line crosses an island's name or another callout`, crossed.length === 0, crossed);
  await leaderRule(A, `universe ${tag}: the store`);
  const asking = [...s.projects].sort((p, q) => q.needs - p.needs)[0];
  if (asking?.needs) { await A.go(`&zoom=${asking.id}`); await A.P.sleep(700); await leaderRule(A, `universe ${tag}: ${asking.label}'s zoom`); await A.go(""); await A.P.sleep(400); }
  // each island's name says its runs and how many need a human, the observer's counts
  const labelled = await E(`Object.fromEntries([...document.querySelectorAll("[data-island-label]")].map((e) => [e.dataset.islandLabel, e.innerText.replace(/\\s+/g, " ")]))`);
  const wrong = s.projects.filter((p) => labelled[p.id] !== undefined && !(labelled[p.id].includes(p.runs.toLocaleString("en")) && (p.needs ? labelled[p.id].includes(`${p.needs.toLocaleString("en")} ${p.needs === 1 ? "needs" : "need"} you`) : !/needs? you/.test(labelled[p.id]))));
  check(`universe ${tag}: each island's name says its runs and how many of them need a human, by the observer's count`, wrong.length === 0 && s.projects.reduce((n, p) => n + p.needs, 0) === needs, [wrong.map((p) => [p.label, labelled[p.id], p.runs, p.needs])]);
  check(`universe ${tag}: the needs pill says the observer's count and lists them`, needs === 0 ? (await A.count("[data-needs-pill]")) === 0 : digits(await A.text("[data-needs-pill]")) === needs, await A.text("[data-needs-pill]"));
  const calledIds = await E(`[...document.querySelectorAll("[data-callout]")].map((e) => e.dataset.callout)`);
  const islandsCalled = new Set(calledIds.map((id) => projectOf(A, id))), islandsAsking = new Set(s.needs.map((n) => projectOf(A, n.id)));
  check(`universe ${tag}: the callouts are spread across the islands that need a human, never all on one while others wait`, islandsCalled.size === Math.min(calledIds.length, islandsAsking.size), [islandsCalled.size, calledIds.length, islandsAsking.size]);
  check(`universe ${tag}: the marks are drawn at a readable size at the store's zoom (at least ${catalogue.universe.min_dot_px} px)`, Number(await E(`document.querySelector("${U}").dataset.markPx`)) >= catalogue.universe.min_dot_px, await E(`document.querySelector("${U}").dataset.markPx`));
  check(`universe ${tag}: at most 1,500 DOM nodes at the store's zoom`, (await E(`document.querySelectorAll("*").length`)) <= 1500, await E(`document.querySelectorAll("*").length`));
  if (calls) {
    const first = await E(`document.querySelector("[data-callout]").dataset.callout`);
    await A.click("[data-callout]");
    check(`universe ${tag}: a callout opens its run's sheet, the camera on it (1 click)`, A.clicks === 1 && (await A.url()).includes(`run=${first}`) && (await A.count("ev-sheet h1")) === 1 && (await A.text("[data-crumb='run']")) !== "", await A.url());
    A.reset(); await A.go("");
    if (needs > calls) {
      await A.click("[data-more-needs]");
      check(`universe ${tag}: "+N more" lists every run that needs a human (1 click)`, A.clicks === 1 && (await A.url()).includes("standing=needs_human") && digits(await A.text("ev-work h1")) === needs, [await A.url(), await A.text("ev-work h1")]);
    }
  }
  // J5 · one project's state: an island's name is its runs in the list (1 click); its disc is the zoom
  const big = [...s.projects].sort((a, b) => b.runs - a.runs)[0];
  A.reset(); await A.go("");
  await A.click(`[data-island-label="${big.id}"]`);
  check(`universe ${tag}: an island's name opens the work view of its project, with the observer's count (1 click)`, A.clicks === 1 && (await A.url()).includes("view=work") && digits(await A.text("ev-work h1")) === big.runs, [await A.url(), await A.text("ev-work h1"), big.runs]);
  A.reset(); await A.go("");
  const zoomFly = await flight(A, () => A.click(`[data-mirror-island="${big.id}"]`));
  const shownRuns = await A.count("[data-mirror-run]");
  check(`universe ${tag}: an island zooms in; the mirror lists its runs (all of them up to ${catalogue.universe.mirror_cap}), then all of them in the list`, (await A.url()).includes(`zoom=${big.id}`) && shownRuns === Math.min(big.runs, catalogue.universe.mirror_cap) && ((await A.count("[data-mirror-rest]")) === (big.runs > catalogue.universe.mirror_cap ? 1 : 0)), [shownRuns, big.runs]);
  frames(A, `universe ${tag}: a zoom`, zoomFly);
  check(`universe ${tag}: zoomed on an island, the marks are drawn larger, at a readable size`, Number(await E(`document.querySelector("${U}").dataset.markPx`)) >= catalogue.universe.min_glyph_px, await E(`document.querySelector("${U}").dataset.markPx`));
  check(`universe ${tag}: at most 1,500 DOM nodes zoomed on an island`, (await E(`document.querySelectorAll("*").length`)) <= 1500, await E(`document.querySelectorAll("*").length`));
  const words = await E(`document.querySelector("[data-mirror-run]")?.innerText ?? ""`);
  check(`universe ${tag}: each mark in the mirror says its standing in words`, Object.keys(facets).some((k) => words.includes(k === "needs_human" ? "Needs a human" : "")) || /Proven|Not ended|Stopped|Audit pending|Changed since audit|Needs a human|Unreadable/.test(words), words);
  // J2 · can I trust run X: island, then the run (2 clicks): its claims in orbit, its proof in the sheet
  const run = await E(`document.querySelector("[data-mirror-run]").dataset.mirrorRun`);
  await A.click(`[data-mirror-run="${run}"]`);
  const detail = await A.api("/api/run", `&key=${run}`);
  check(`universe ${tag}: a run opens from its island with its claims in orbit and its audit in the sheet (2 clicks)`, A.clicks === 2 && (await A.url()).includes(`run=${run}`) && (await A.count("[data-orbit]")) === Math.min(detail.row.claims?.length ?? 0, catalogue.universe.claims_cap) && /Audit/.test(await A.text("ev-sheet")), [await A.count("[data-orbit]"), detail.row.claims?.length]);
  // note B: the run's zoom is a node-link graph (every claim once, every published SupportedBy edge drawn), and the sheet's
  // Claims section draws the same graph small
  const drawnClaims = (detail.row.claims ?? []).slice(0, catalogue.universe.claims_cap).map((c) => c.id), ids = new Set(drawnClaims);
  const published = new Set((detail.row.edges ?? []).filter((e) => ids.has(e.from) && ids.has(e.to)).map((e) => `${e.from} ${e.to}`));
  const graph = await E(`({ nodes: [...document.querySelectorAll("${U} [data-orbit]")].map((a) => a.dataset.orbit), edges: [...document.querySelectorAll("${U} [data-graph] [data-edge]")].map((l) => l.dataset.edge),
    word: document.querySelector("${U} [data-run-word]")?.innerText ?? "", sheet: [...document.querySelectorAll("ev-sheet [data-claim-graph] [data-graph-claim]")].map((a) => a.dataset.graphClaim) })`);
  check(`universe ${tag}: the run's zoom draws its claims as a graph, each once with every published edge, its standing in words, and the sheet draws the same graph`,
    JSON.stringify(graph.nodes) === JSON.stringify(drawnClaims) && graph.edges.length === published.size && graph.edges.every((e) => published.has(e)) && graph.word.length > 0
    && (drawnClaims.length === 0 || JSON.stringify(graph.sheet) === JSON.stringify(drawnClaims)), [graph, [...published]]);
  if ((detail.row.claims ?? []).length) {
    const c = detail.row.claims[0].id;
    await A.click(`[data-orbit="${c}"]`);
    check(`universe ${tag}: a claim in orbit opens in the sheet (3 clicks)`, A.clicks === 3 && (await A.url()).includes(`claim=${c}`) && (await E(`document.querySelector('[data-claim-row="${c}"] details').open`)), await A.url());
  }
  if ((detail.row.claims ?? []).length) { await P.key("Escape"); await P.sleep(300); }
  await P.key("Escape"); await P.sleep(300);
  const outOne = await A.url();
  await P.key("Escape"); await P.sleep(600);
  check(`universe ${tag}: Esc steps out: the claim, the run, then the island`, outOne.includes(`zoom=${big.id}`) && !outOne.includes("run=") && !(await A.url()).includes("zoom="), [outOne, await A.url()]);
  await A.go(`&zoom=${big.id}`);
  const outFly = await flight(A, () => A.click(`[data-crumb="store"]`));
  frames(A, `universe ${tag}: zooming back out`, outFly);
  // J4 · what the audits caught: a chip lights the runs that hold it, with the observer's count (1 click)
  A.reset(); await A.go("");
  const month = await A.api("/api/summary", `&since=${Math.floor(Date.now() / 3_600_000) * 3600 - Number(catalogue.default_period) * 86400}`);
  const caught = month.aggregates.runs.failed_audits;
  await A.click(`[data-hl="failed_audit"]`);
  check(`universe ${tag}: the failed-audit chip highlights here, with the observer's number, and the URL keeps it (1 click)`, A.clicks === 1 && (await A.url()).includes("hl=failed_audit") && (await E(`document.querySelector('[data-hl="failed_audit"]').getAttribute("aria-pressed")`)) === "true" && digits(await A.text(`[data-hl="failed_audit"]`)) === caught, [await A.url(), await A.text(`[data-hl="failed_audit"]`), caught]);
  // J6 · find a remembered goal: typing is the search, from the map (0 clicks)
  const word = (A.rows.find((r) => r.goal)?.goal ?? "tile").split(/\W+/).find((w) => w.length > 4) ?? "tile";
  A.reset(); await A.go("");
  const found = await A.api("/api/search", `&q=${encodeURIComponent(word)}`);
  await A.type("#search", word);
  check(`universe ${tag}: a remembered word typed on the map lists what the observer finds (0 clicks)`, A.clicks === 0 && digits(await A.text("ev-work h1")) === found.total && found.total > 0, [word, await A.text("ev-work h1"), found.total]);
  // the keyboard: arrows move between islands, Enter zooms, then between marks, Enter opens; the live region names each
  await A.go("");
  await E(`document.querySelector("${U} canvas").focus()`);
  await P.key("ArrowRight"); await P.sleep(200);
  const said = await A.text(`${U} [data-said]`);
  await P.key("Enter"); await P.sleep(700);
  const zoomed = await A.url();
  await E(`document.querySelector("${U} canvas").focus()`);
  await P.key("ArrowDown"); await P.sleep(200); await P.key("Enter"); await P.sleep(700);
  check(`universe ${tag}: arrows move between islands and marks, the focus said in words, Enter opens each`, /runs/.test(said) && zoomed.includes("zoom=") && (await A.url()).includes("run=") && s.projects.some((p) => said.startsWith(p.label)), [said, zoomed, await A.url()]);
  // the key: it starts folded to its button, opens by it, hides by its ×, comes back, never in the URL; while open the map
  // leaves its column free
  await A.go("");
  const folded = (await A.count("[data-legend]")) === 0 && (await A.count("[data-bottom] [data-key-toggle]")) === 1;
  await A.click("[data-bottom] [data-key-toggle]");
  const keyed = await E(`(() => { const k = document.querySelector("[data-legend]")?.getBoundingClientRect(), names = [...document.querySelectorAll("[data-island-label], [data-callout]")].map((e) => e.getBoundingClientRect());
    return { rows: document.querySelectorAll("[data-key-row]").length, clear: !!k && names.every((b) => b.right <= k.left + 0.5 || b.bottom <= k.top + 0.5 || b.top >= k.bottom - 0.5) }; })()`);
  const before = await A.url();
  await A.click("[data-legend] [data-key-toggle]");
  const hidden = (await A.count("[data-legend]")) === 0 && (await A.count("[data-bottom] [data-key-toggle]")) === 1 && (await A.url()) === before;
  await A.click("[data-bottom] [data-key-toggle]");
  check(`universe ${tag}: the key starts folded to its button, lists each standing the store holds, keeps clear of every name and callout, hides and comes back, the URL unchanged`, folded && keyed.rows === Object.values(facets).filter((n) => n > 0).length && keyed.clear && hidden && (await A.count("[data-legend]")) === 1 && (await A.url()) === before, [folded, keyed, hidden]);
  // the bars: the crumb and the period chips on the left edge, the pill on the right; the chips one row, centred on one line
  const bars = await E(`(() => { const r = (s) => document.querySelector(s)?.getBoundingClientRect(), stage = r("${U} [data-stage]"), crumb = r("[data-crumbs]"), chips = r("[data-chips]"), pill = r("[data-needs-pill]");
    const mids = [...document.querySelectorAll("[data-chips], [data-hl]")].map((e) => { const b = e.getBoundingClientRect(); return Math.round(b.top + b.height / 2); });
    return { left: Math.abs(crumb.left - chips.left) <= 0.5, right: !pill || Math.abs(stage.right - pill.right - (crumb.left - stage.left)) <= 0.5, row: new Set(mids).size === 1 }; })()`);
  check(`universe ${tag}: the bars line up: the crumb over the period, the pill as far from the right as the crumb from the left, the chips on one line`, bars.left && bars.right && bars.row, bars);
  // first render after the summary
  await P.send("Page.navigate", { url: `${A.src.base}/app/?data=${A.src.data}` });
  await P.sleep(200); await P.waitReady();
  const at = await E(`[...performance.getEntriesByType("resource")].filter((r) => r.name.includes("/api/summary")).map((r) => r.responseEnd)[0]`);
  const drawn = await E(`(() => { const t = document.querySelector("${U}").timings; return t.length ? performance.now() : null; })()`);
  if (MODE === "smoke") check(`universe ${tag}: the map is drawn within a second of the summary`, drawn !== null && drawn - at <= 1000 + 400, [at, drawn]);
  else check(`universe ${tag}: the map is drawn once the summary is there`, drawn !== null && at !== undefined, [at, drawn]);
  P.errors.length = 0;
}

/** P5b item 2, as app/geometry.js placeCallouts promises: each leader crosses no other island's disc, leaves its own
 * island the short way (inside it at most callout_slack times the shortest way out) and is at most callout_reach times its
 * island's radius long; zoomed on an island each callout is off its disc, on its mark's side. */
async function leaderRule(A, what) {
  const G = catalogue.universe, got = await A.E(`(() => { const u = document.querySelector("${U}"), d = u.drawn();
    return { islands: d.islands, marks: d.marks, lines: [...u.querySelectorAll("[data-leaders] line")].map((l) => ["x1", "y1", "x2", "y2"].map((k) => +l.getAttribute(k))),
      calls: [...u.querySelectorAll("[data-callout]")].map((e) => ({ id: e.dataset.callout, x: +e.dataset.x, y: +e.dataset.y, w: +e.dataset.w, h: +e.dataset.h })) }; })()`);
  const discs = got.islands.map((t) => { const [id, sx, sy, r] = t.split(" "); return { id, sx: +sx, sy: +sy, r: +r }; });
  const inside = (x1, y1, x2, y2, c) => { const dx = x2 - x1, dy = y2 - y1, A2 = dx * dx + dy * dy, fx = x1 - c.sx, fy = y1 - c.sy, B = fx * dx + fy * dy, C = fx * fx + fy * fy - c.r * c.r, D = B * B - A2 * C;
    if (A2 === 0 || D <= 0) return 0; const t0 = Math.max(0, (-B - Math.sqrt(D)) / A2), t1 = Math.min(1, (-B + Math.sqrt(D)) / A2); return t1 > t0 ? (t1 - t0) * Math.sqrt(A2) : 0; };
  const bad = got.lines.flatMap(([x1, y1, x2, y2], i) => {
    const call = got.calls[i], own = discs.find((c) => c.id === projectOf(A, call.id)), out = [];
    if (!own) return [`${call.id}: no island`];
    if (Math.hypot(x2 - x1, y2 - y1) > G.callout_reach * own.r + 1) out.push(`${call.id}: leader ${Math.round(Math.hypot(x2 - x1, y2 - y1))} px > ${G.callout_reach}·${Math.round(own.r)}`);
    if (inside(x1, y1, x2, y2, own) > G.callout_slack * Math.max(0, own.r - Math.hypot(x1 - own.sx, y1 - own.sy)) + 1) out.push(`${call.id}: across its own island`);
    for (const c of discs) if (c !== own && inside(x1, y1, x2, y2, { ...c, r: c.r - 1 }) > 0) out.push(`${call.id}: across ${c.id}`);
    const nx = Math.min(Math.max(own.sx, call.x), call.x + call.w), ny = Math.min(Math.max(own.sy, call.y), call.y + call.h);
    if (Math.hypot(nx - own.sx, ny - own.sy) < own.r - 1) out.push(`${call.id}: its box on its island`);
    return out;
  });
  check(`${what}: every callout's leader is short, leaves its island straight and crosses no other island (${got.lines.length} drawn)`, bad.length === 0, bad);
}

// note A · a reload lays the map out exactly as before, at the store, on an island and on a run: the overlay's boxes and the
// geometry drawn (the store's box and scale, each mark's screen place), not pixels; and a load never flies
// ---- a poll redraws only what changed: the same nodes, no mutation when nothing changed, one text node when one count did ----
async function pollStable(A) {
  const E = A.E, P = A.P, run = A.id("audit-failed");
  const rules = { bump: null };
  await P.send("Fetch.enable", { patterns: [{ urlPattern: "*/api/summary*" }] });
  P.on("Fetch.requestPaused", async (p) => {
    if (!rules.bump) return P.send("Fetch.continueRequest", { requestId: p.requestId });
    // one project's run count changes (and so the token): what a store gives when one run is added to it
    const body = await (await fetch(p.request.url)).json();
    body.projects.find((x) => x.id === rules.bump).runs += 1000; body.token = `${body.token}-bumped`;
    return P.send("Fetch.fulfillRequest", { requestId: p.requestId, responseCode: 200, responseHeaders: [{ name: "Content-Type", value: "application/json" }], body: Buffer.from(JSON.stringify(body)).toString("base64") });
  });
  const polls = () => E(`performance.getEntriesByType("resource").filter((r) => r.name.includes("/api/summary")).length`);
  /** Ask once, now (a shown tab polls at once), and wait for its answer and what it draws. */
  const poll = async () => { const n = await polls(); await E(`document.dispatchEvent(new Event("visibilitychange"))`); for (let i = 0; i < 40 && (await polls()) === n; i++) await P.sleep(50); await P.sleep(700); };
  const watch = (sels) => E(`(() => { window.__refs = {}; window.__recs = {};
    for (const sel of ${JSON.stringify(sels)}) { const root = document.querySelector(sel); window.__refs[sel] = [...root.querySelectorAll("*")]; window.__recs[sel] = [];
      new MutationObserver((rs) => window.__recs[sel].push(...rs.map((r) => ({ type: r.type, node: r.target.nodeName, attr: r.attributeName, text: r.type === "characterData" ? r.target.data : [...r.addedNodes].map((n) => n.textContent).join("") })))).observe(root, { subtree: true, childList: true, attributes: true, characterData: true }); }
    return true; })()`);
  const seen = () => E(`Object.fromEntries(Object.keys(window.__refs).map((sel) => { const now = [...document.querySelector(sel).querySelectorAll("*")];
    return [sel, { kept: now.length === window.__refs[sel].length && now.every((n, i) => n === window.__refs[sel][i]), nodes: now.length, recs: window.__recs[sel] }]; }))`);
  await A.go(`&view=inbox&run=${run}`);
  const project = A.summary.projects.find((x) => x.runs > 0);
  await E(`document.getElementById("sheet-pane").scrollTop = 120; document.querySelector('#rail a[href*="project"]').focus()`);
  const focused = await E(`document.activeElement.getAttribute("href")`), top = await E(`document.getElementById("sheet-pane").scrollTop`);
  await watch(["#rail", "#sheet", "#inbox"]);
  await poll();
  const same = await seen();
  check("poll-stable: a poll that changed nothing keeps every node of the rail, the open sheet and the inbox, and records no mutation",
    Object.values(same).every((x) => x.kept && x.nodes > 5 && x.recs.length === 0), same);
  rules.bump = project.id;
  await watch(["#rail", "#sheet"]);
  await poll();
  rules.bump = null;
  const bumped = await seen(), want = num(project.runs + 1000);
  const railRecs = bumped["#rail"].recs;
  check("poll-stable: a poll that changed one project's count changes that text alone: every node kept, the sheet untouched, focus and scroll kept",
    bumped["#rail"].kept && bumped["#sheet"].kept && bumped["#sheet"].recs.length === 0 && railRecs.length > 0 && railRecs.every((r) => (r.type === "characterData" || r.type === "childList") && r.text === want)
      && (await E(`document.activeElement.getAttribute("href")`)) === focused && (await E(`document.getElementById("sheet-pane").scrollTop`)) === top, [bumped, want, focused, top]);
  // the work list: its rows are the same nodes after a poll that changed nothing
  await A.go("&view=work");
  await watch(["#work [data-window]", "#rail"]);
  await poll();
  const work = await seen();
  check("poll-stable: the work list's rows are the same nodes after a poll that changed nothing, and nothing in it is touched",
    Object.values(work).every((x) => x.kept && x.recs.length === 0) && work["#work [data-window]"].nodes > 5, work);
  await P.send("Fetch.disable");
}

// ---- the run sheet's divider: drag, keys, a reload remembers, narrow is the whole width -------------------------------
async function divider(A) {
  const E = A.E, P = A.P, run = A.id("audit-failed");
  const state = () => E(`(() => { const d = document.querySelector("ev-divider"), s = document.getElementById("sheet-pane").getBoundingClientRect(), c = document.querySelector("ev-universe canvas");
    return { now: +d.getAttribute("aria-valuenow"), min: +d.getAttribute("aria-valuemin"), max: +d.getAttribute("aria-valuemax"), role: d.getAttribute("role"), sheet: Math.round(s.width), shown: getComputedStyle(d).display !== "none",
      x: d.getBoundingClientRect().x + d.getBoundingClientRect().width / 2, y: d.getBoundingClientRect().y + 200, main: getComputedStyle(document.getElementById("main")).display, stored: +localStorage.getItem("empirica.sheet-width"),
      canvas: c && c.getBoundingClientRect().width ? Math.abs(c.width / Math.min(2, devicePixelRatio || 1) - c.getBoundingClientRect().width) <= 1 && Math.round(c.getBoundingClientRect().right) <= Math.round(d.getBoundingClientRect().left) + 1 : null }; })()`);
  const mouse = async (type, x, y) => P.send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1, pointerType: "mouse" });
  // the token lengths, as the page resolves them (registered lengths compute to px)
  const len = (name) => E(`Math.round(parseFloat(getComputedStyle(document.body).getPropertyValue("${name}")))`);
  const step = await len("--ev-split-step"), sheetW = await len("--ev-sheet");
  for (const [where, q] of [["the inbox", `&view=inbox&run=${run}`], ["the universe", `&run=${run}`]]) {
    await E(`localStorage.removeItem("empirica.sheet-width")`); await A.go(q);
    const a = await state();
    await mouse("mousePressed", a.x, a.y); await mouse("mouseMoved", a.x - 60, a.y); await mouse("mouseMoved", a.x - 120, a.y); await mouse("mouseReleased", a.x - 120, a.y); await P.sleep(300);
    const dragged = await state();
    check(`divider ${where}: a separator with its width; dragging it 120 px left widens the sheet 120 px, remembered${where === "the universe" ? ", and the map refits beside it" : ""}`,
      a.role === "separator" && a.shown && a.now === a.sheet && Math.abs(dragged.now - a.now - 120) <= 2 && dragged.sheet === dragged.now && dragged.stored === dragged.now && (where === "the inbox" || dragged.canvas === true), [a, dragged]);
    await E(`document.querySelector("ev-divider").focus()`);
    await P.key("ArrowLeft"); const left = await state(); await P.key("ArrowRight"); await P.key("ArrowRight"); const right = await state();
    await P.key("Home"); const home = await state(); await P.key("End"); const end = await state(); await P.key("PageDown"); const page = await state();
    check(`divider ${where}: Left widens a step, Right narrows one, Home its narrowest, End its widest (the main area keeps its minimum), PageDown four steps`,
      left.now === dragged.now + step && right.now === dragged.now - step && home.now === home.min && end.now === end.max && page.now === end.max - catalogue.split_page * step && page.sheet === page.now, [step, dragged.now, left.now, right.now, home, end.now, page.now]);
    await P.send("Page.reload"); await P.sleep(300); await P.waitReady(); await P.sleep(600);
    const reloaded = await state();
    check(`divider ${where}: a reload keeps the width chosen`, reloaded.now === page.now && reloaded.sheet === page.now, [page.now, reloaded]);
    await E(`document.querySelector("ev-divider").dispatchEvent(new MouseEvent("dblclick", { bubbles: true }))`); await P.sleep(200);
    const reset = await state();
    check(`divider ${where}: a double-click resets it to the sheet's width`, reset.now === sheetW && reset.sheet === reset.now, [reset, sheetW]);
  }
  await P.send("Emulation.setDeviceMetricsOverride", { width: 800, height: 700, deviceScaleFactor: 1, mobile: false }); await P.sleep(500);
  const narrow = await state(), vw = await E(`innerWidth`);
  check("divider: narrower than both minimums the sheet takes the full width and the divider is not drawn; the width chosen is kept", narrow.sheet === vw && narrow.main === "none" && !narrow.shown && narrow.stored === sheetW, narrow);
  const [w, h] = A.size.split("x").map(Number);
  await P.send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false }); await P.sleep(500);
  const back = await state();
  check("divider: wide again, the sheet is back at the width chosen beside the main area", back.shown && back.sheet === back.stored && back.main !== "none", back);
}

async function universeReload(A) {
  const E = A.E, P = A.P, project = A.summary.projects.find((p) => p.runs > 1) ?? A.summary.projects[0], run = A.id("verification");
  const snap = () => E(`(() => { const u = document.querySelector("${U}");
    return JSON.stringify({ drawn: u.drawn(), flights: u.dataset.flights ?? "0", boxes: [...u.querySelectorAll("[data-overlay] [data-x]")].map((e) => [e.dataset.callout ?? e.dataset.islandLabel ?? e.dataset.orbit ?? e.dataset.orbitLabel ?? e.dataset.card ?? e.dataset.chain ?? "", e.dataset.x, e.dataset.y, e.dataset.w ?? "", e.dataset.h ?? ""]) }); })()`);
  for (const [where, q] of [["the store", ""], ["an island", `&zoom=${project.id}`], ["a run", `&run=${run}`]]) {
    await A.go(q); await P.sleep(600);
    const before = await snap();
    await P.send("Page.reload"); await P.sleep(300); await P.waitReady(); await P.sleep(900);
    const after = await snap(), b = JSON.parse(before), a = JSON.parse(after);
    check(`universe reload: ${where}: a reload draws the same map (the overlay's boxes and the drawn geometry identical), and the load did not fly`, before === after && a.flights === "0" && a.drawn.marks.length > 0, [b.drawn.fit, a.drawn.fit, a.flights, b.boxes.length, a.boxes.length]);
  }
}

// J3, J7 · a dropped claim and the attempts, reached through the map (the fixtures hold both)
async function universeRuns(A) {
  const tag = `${A.src.name} ${A.size}`;
  for (const [scenario, section, test] of [["refuted", "dropped", (t, d) => d.dropped.length > 0 && d.dropped.every((x) => t.includes(x.text.slice(0, 30)))], ["audit-failed", "attempts", (t) => /Goal/.test(t)]]) {
    const id = A.id(scenario), project = projectOf(A, id);
    A.reset(); await A.go("");
    await A.click(`[data-mirror-island="${project}"]`);
    await A.click(`[data-mirror-run="${id}"]`);
    const d = await A.api("/api/run", `&key=${id}`), text = await A.text(`ev-sheet [data-section='${section}']`);
    check(`universe ${tag}: ${scenario === "refuted" ? "why a claim was dropped" : "the attempts side by side"} is two clicks from the map (island, run)`, A.clicks === 2 && test(text, d), [A.clicks, text.slice(0, 200)]);
  }
}

// J8 · Back and Forward keep the place across the switch
async function universeHistory(A) {
  const E = A.E, P = A.P, s = A.summary, back = async () => { await E(`history.back()`); await P.sleep(600); }, forward = async () => { await E(`history.forward()`); await P.sleep(600); };
  const project = s.projects.find((p) => p.runs > 1) ?? s.projects[0];
  await A.go("");
  await A.click(`[data-mirror-island="${project.id}"]`);
  await A.click("[data-mirror-run]");
  const there = await A.url();
  await A.click(`[data-switch="inbox"]`);
  const inbox = await A.url();
  check("universe history: the switch goes to the Inbox with the same place (the run still open)", (await E(`document.body.dataset.view`)) === "inbox" && inbox.includes("view=inbox") && inbox.includes("run=") && (await A.count("ev-sheet h1")) === 1, inbox);
  await back();
  check("universe history: Back returns to the Universe, zoomed and with the run open, as it was", (await A.url()) === there && (await E(`document.body.dataset.view`)) === "universe" && (await A.count("[data-orbit]")) >= 0 && (await A.text("[data-crumb='project']")) === project.label, [await A.url(), there]);
  await back();
  check("universe history: Back again closes the run, still on the island", (await A.url()).includes(`zoom=${project.id}`) && !(await A.url()).includes("run="), await A.url());
  await forward(); await forward();
  check("universe history: Forward twice is the Inbox again", (await A.url()) === inbox && (await E(`document.body.dataset.view`)) === "inbox", await A.url());
  await A.click(`[data-switch="universe"]`);
  check("universe history: switching back restores the Universe's zoom and run", (await A.url()) === there.replace("&view=universe", ""), [await A.url(), there]);
}

// motion: the camera flies only when motion is allowed; under reduced motion it is there at once and nothing moves
async function universeMotion(A) {
  const E = A.E, P = A.P, project = A.summary.projects[0];
  await A.go("");
  check("universe motion: the map says its motion is full when nothing asks for less", (await E(`document.querySelector("${U}").dataset.motion`)) === "full", null);
  // what began, counted by the map itself (dataset.flights), not how many frames a loaded machine managed to draw
  const flights = () => E(`Number(document.querySelector("${U}").dataset.flights ?? 0)`);
  const f0 = await flights(), full = await flight(A, () => A.click(`[data-mirror-island="${project.id}"]`)), flew = (await flights()) - f0;
  await P.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await A.go("");
  const s0 = await flights(), still = await flight(A, () => A.click(`[data-mirror-island="${project.id}"]`)), jumped = (await flights()) - s0;
  await P.sleep(400);
  const idle = await E(`(() => { const t = document.querySelector("${U}").timings.length; return new Promise((r) => setTimeout(() => r(document.querySelector("${U}").timings.length - t), 600)); })()`);
  check("universe motion: the camera flies with motion, and jumps (no flight, no frame in the air) under reduced motion, nothing redrawn after", flew >= 1 && jumped === 0 && still.n === 0 && idle === 0 && (await E(`document.querySelector("${U}").dataset.motion`)) === "reduced", [flew, jumped, full, still, idle]);
}

// ---- S · deterministic pictures (make ui-shots): the demo, a fixed clock, no motion, both schemes ----------------------------
const FROZEN = Date.UTC(2026, 9, 13, 12, 0, 0);
const SHOTS = [["universe", ""], ["island", "&zoom=*"], ["inbox", "&view=inbox"], ["work", "&view=work"], ["run", "&view=inbox&run=*verification"],
  ["claim", "&view=inbox&run=*verification&claim=*"], ["attempts", "&view=inbox&run=*audit-failed"], ["orbit", "&run=*verification"]];
async function shots() {
  const out = path.resolve(process.env.OUT || path.join(os.tmpdir(), "empirica-ui-shots"));
  if (out.startsWith(path.resolve(ui, "..", "..", "..") + path.sep)) throw new Error(`OUT=${out}: pictures are written outside the repository`);
  fs.mkdirSync(out, { recursive: true });
  const crypto = await import("node:crypto");
  const plan = [...SHOTS.map(([n, q]) => [n, q, "light", WIDE]), ["universe", "", "dark", WIDE], ["run", "&view=inbox&run=*verification", "dark", WIDE], ["orbit", "&run=*verification", "dark", WIDE], ["inbox", "&view=inbox", "light", SIZES[1]]];
  for (const [name, query, scheme, size] of plan) {
    await session(SOURCES[0], size, async (A) => {
      const run = (sc) => A.id(sc), claimOf = async (id) => (await A.api("/api/run", `&key=${id}`)).row.claims[0]?.id;
      let q = query.replace("&zoom=*", `&zoom=${A.summary.projects[0].id}`);
      const id = run(query.includes("*verification") ? "verification" : "audit-failed");
      q = q.replace(/run=\*[a-z-]+/, `run=${id}`);
      if (q.includes("claim=*")) q = q.replace("claim=*", `claim=${await claimOf(id)}`);
      await A.go(q); await A.P.sleep(900);
      const file = path.join(out, `${name}-${scheme}-${size}.png`);
      await A.P.shot(file);
      const sum = crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex").slice(0, 16);
      check(`shot ${path.basename(file)} ${sum}`, fs.statSync(file).size > 1000, fs.statSync(file).size);
    }, { scheme, clock: FROZEN, still: true });
  }
  console.log(`pictures in ${out}`);
}

// ONLY=inbox,volume runs just those groups (for a mutation check); all of them otherwise.
const ONLY = process.env.ONLY ? new Set(process.env.ONLY.split(",")) : null;
const demo = (fn) => () => session(SOURCES[0], WIDE, fn);
const JOURNEYS = {
  inbox: demo(inbox),
  liveInbox: () => session(SOURCES[1], WIDE, inbox),
  narrowing: demo(narrowing),
  sheet: demo(sheet),
  ended: demo(ended),
  attempts: demo(attempts),
  audits: demo(audits),
  find: demo(find),
  keyboard: demo(keyboard),
  history: demo(history),
  safe: demo(safeText),
  recovery: demo(recovery),
  hidden: demo(hidden),
  motion: demo(motion),
  empty,
  universe: demo(universe),
  universeRuns: demo(universeRuns),
  universeHistory: () => session(SOURCES[1], WIDE, universeHistory),
  universeReload: () => session(SOURCES[0], WIDE, universeReload),
  universeMotion: demo(universeMotion),
  divider: demo(divider),
  pollStable: demo(pollStable),
  layout: async () => {
    for (const size of SIZES) { await session(SOURCES[0], size, layout); for (const scheme of SCHEMES) await session(SOURCES[0], size, layoutInvariants, { scheme }); }
    await narrow(SOURCES[0]);
  },
};
const SMOKE = {
  volume: async () => { await settled(BIG); await session({ name: "1k", base: BIG, data: "store" }, WIDE, volume); },
  universe: () => session({ name: "1k", base: BIG, data: "store" }, WIDE, universe, { wait: 800 }),
  // what only a store of many runs has: a calendar of many project-days, and a list longer than one page
  calendar: () => session({ name: "1k", base: BIG, data: "store" }, WIDE, calendar),
  stale: () => session({ name: "1k", base: BIG, data: "store" }, WIDE, stale),
};
const GROUPS = { journeys: JOURNEYS, smoke: SMOKE, shots: { shots } }[MODE];
// Each journey (a group above) ends within JOURNEY_LIMIT_S or fails the run with its name: a hang is a bug to fix, not to
// wait out. The shots are one group of many pages, bounded by each page's own guard (cdp.mjs) instead.
const JOURNEY_LIMIT_S = 60;
const within = (name, work) => { let t; return Promise.race([work.finally(() => clearTimeout(t)), new Promise((_, rej) => { t = setTimeout(() => rej(new Error(`journey ${name} timed out after ${JOURNEY_LIMIT_S} s`)), JOURNEY_LIMIT_S * 1000); })]); };
try {
  for (const [name, run] of Object.entries(GROUPS)) if (!ONLY || ONLY.has(name)) await (MODE === "shots" ? run() : within(name, run()));
} finally {
  // after a timeout the journey's Chrome is still a child: kill every child by its exact pid (pgrep -P this process)
  try { for (const pid of execFileSync("pgrep", ["-P", String(process.pid)], { encoding: "utf8" }).split(/\s+/).filter(Boolean)) try { process.kill(Number(pid), "SIGKILL"); } catch {} } catch {}
  for (const p of children) p.kill();
  fs.rmSync(home, { recursive: true, force: true });
}
console.log(`${total - failed} of ${total} checks passed`);
process.exit(failed ? 1 : 0);
