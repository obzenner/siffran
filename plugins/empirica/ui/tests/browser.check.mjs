// Browser checks for the UI, in headless Chrome via cdp.mjs, against the observer's own server
// (adapters/observer/cli.py serve): the view against VIEW.md R1–R8 on the demo data, polling, an empty store, then
// the standing rules (CONVENTIONS) the earlier reviews pinned.
// Run: make ui-browser. Prints one line per check; exits 1 on any failure.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { withPage as openPage } from "./cdp.mjs";
import { walk } from "./browser.walk.mjs";
import { layoutChecks } from "./browser.layout.mjs";

const ui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CLI = path.join(ui, "..", "adapters", "observer", "cli.py");
// Two servers: the demo data only, and an empty store in a temporary home (no repository), so "Your store" is
// never your own.
const home = fs.mkdtempSync(path.join(os.tmpdir(), "ev-home-"));
function start(args) {
  const p = spawn("python3", [CLI, "serve", "--port", "0", ...args], { cwd: home, stdio: ["ignore", "pipe", "inherit"] });
  const url = new Promise((res, rej) => {
    let buf = "";
    p.stdout.on("data", (d) => { buf += d; const m = /http:\/\/127\.0\.0\.1:\d+/.exec(buf); if (m) res(m[0]); });
    setTimeout(() => rej(new Error("the observer server did not start")), 15000);
  });
  return { p, url };
}
const demo = start(["--demo"]), store = start(["--home", home]);
const B = await demo.url, S = await store.url;
const APP = `${B}/app/?data=demo`;
const json = async (u) => (await fetch(`${B}${u}`)).json();
const fx = await json("/api/view?source=demo");
const runs = fx.projects.flatMap((p) => p.runs), claims = runs.flatMap((r) => r.claims);
const details = Object.fromEntries(await Promise.all(runs.map(async (r) => [r.key, await json(`/api/run?source=demo&key=${encodeURIComponent(r.key)}`)])));
// The runs the checks open: a converged run with its history, and the run with a changed file.
const CONVERGED = runs.find((r) => r.state === "converged" && !r.diagnostic).key;
const STALE = runs.find((r) => details[r.key].stale.length).key;
const VERIFY = runs.find((r) => details[r.key].claims.some((c) => c.records.some((x) => x.class === "observed"))).key;
let failed = 0;
function check(name, ok, got) { if (!ok) failed++; console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok ? "" : `  got ${JSON.stringify(got)}`}`); }
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
// Every page a check opens must also run without an uncaught exception.
const withPage = (url, size, fn) => openPage(url, size, async (P) => {
  await fn(P);
  check(`no page errors: ${url.replace(B, "").replace(S, "").slice(0, 70)}`, P.errors.length === 0, P.errors);
});
const resize = (P, w, h) => P.send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
/** What the view should show for a choice: [projects, runs, claims], counted from the data here. */
function expect(keepClaim = () => true, keepRun = () => true) {
  const ps = fx.projects.map((p) => p.runs.filter(keepRun).filter((r) => r.claims.some(keepClaim) || keepClaim === TRUE)).filter((rs) => rs.length);
  return [ps.length, ps.flat().length, ps.flat().reduce((n, r) => n + (keepClaim === TRUE ? r.claims.length : r.claims.filter(keepClaim).length), 0)];
}
const TRUE = () => true;

// In-page helpers: the data and declarations the view is checked against, and the drawn marks.
const LOAD = `const [fx, states, icons] = await Promise.all(["/api/view?source=demo", "../states.json", "../icons.json"].map((u) => fetch(u).then((r) => r.json())));
  const runs = fx.projects.flatMap((p) => p.runs), claims = runs.flatMap((r) => r.claims);
  const V = document.querySelector("ev-view");
  const runMarks = [...V.querySelectorAll("[data-mark][data-run]:not([data-claim])")], claimMarks = [...V.querySelectorAll("[data-mark][data-claim]")];
  const projectMarks = [...V.querySelectorAll("[data-mark][data-project]")];`;
const page = (body) => `(async () => { ${LOAD} ${body} })()`;
// Where each mark's own shape is (its selection ring aside, which only appears around it).
const RECTS = `JSON.stringify([...document.querySelectorAll("ev-view [data-mark]")].map((m) => { const b = m.querySelector("path:not(.hidden), circle:not(.hidden), text").getBoundingClientRect(); return [Math.round(b.x), Math.round(b.y), Math.round(b.width)]; }))`;
const SHOWING = `document.querySelector("ev-filters [data-showing]").textContent`;
const noun = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const showing = ([p, r, c]) => `${noun(p, "project", "projects")} · ${noun(r, "run", "runs")} · ${noun(c, "claim", "claims")}`;
const counts = `[document.querySelectorAll("ev-view [data-mark][data-project]").length, document.querySelectorAll("ev-view [data-mark][data-run]:not([data-claim])").length, document.querySelectorAll("ev-view [data-mark][data-claim]").length]`;
const tick = (name, value) => `(async () => { const x = ${name}; x.checked = ${value}; x.dispatchEvent(new Event("change", { bubbles: true })); await new Promise((r) => setTimeout(r, 150)); })()`;
const box = (sel) => `document.querySelector('ev-filters input[data-filter="${sel[0]}"]${sel[1] ? `[value="${sel[1]}"]` : ""}')`;
const clickRun = (key) => `document.querySelector('ev-view [data-mark][data-run="${key}"]:not([data-claim])').dispatchEvent(new MouseEvent("click", { bubbles: true }))`;
const DETAIL = `document.querySelector("#split ev-detail")`;

try {
  await withPage(APP, "1500x900", async (P) => {
    await P.waitReady(); await P.sleep(300);
    const E = P.evalJs;
    check("no missing copy", !(await E(`document.body.innerHTML.includes("⟦")`)));
    // R1 everything at once
    check("R1: every project, run and claim in the data is in the view", await E(page(`return projectMarks.length === fx.projects.length && runMarks.length === runs.length && claimMarks.length === claims.length
      && runs.every((r) => V.querySelector('[data-mark][data-run="' + r.key + '"]:not([data-claim])')) && claims.length > 0;`)));
    const fits = `(() => { const v = document.querySelector("ev-view").getBoundingClientRect(), m = document.getElementById("main");
      const vis = (x) => (x.querySelector("path:not(.hidden), circle:not(.hidden)") ?? x).getBoundingClientRect();
      return document.documentElement.scrollHeight <= innerHeight && document.documentElement.scrollWidth <= innerWidth && m.scrollHeight <= m.clientHeight
        && [...document.querySelectorAll("ev-view [data-mark]")].every((x) => { const b = vis(x); return b.width > 0 && b.left >= v.left - 1 && b.right <= v.right + 1 && b.top >= v.top - 1 && b.bottom <= v.bottom + 1; }); })()`;
    check("R1: nothing scrolls, and every mark is inside the view", await E(fits));
    const contained = `(() => { const a = document.querySelector("[data-filters]"); return a.scrollHeight <= a.clientHeight; })()`;
    check("the filters fit their column without scrolling", await E(contained));
    const size0 = await E(`document.querySelector("ev-view [data-claim] circle:not(.hidden)").getBoundingClientRect().width`);
    await resize(P, 1280, 720); await P.sleep(300);
    check("1280 × 720: the filters still fit without scrolling", await E(contained));
    await resize(P, 900, 560); await P.sleep(300);
    const size1 = await E(`document.querySelector("ev-view [data-claim] circle:not(.hidden)").getBoundingClientRect().width`);
    check("R1: in a smaller window everything still fits, the marks never grow", (await E(fits)) && size1 <= size0, [size0, size1]);
    await resize(P, 1500, 900); await P.sleep(300);
    // R2 each entity type is distinct
    check("R2: a project is a region labelled as the observer labels it, a run a diamond, a claim a circle", await E(page(`
      const regions = V.querySelectorAll("svg > rect.stroke-border").length;
      const diamond = (m) => /^M[\\d.-]+,[\\d.-]+ L[\\d.-]+,[\\d.-]+ L[\\d.-]+,[\\d.-]+ L[\\d.-]+,[\\d.-]+ Z$/.test(m.querySelector("path").getAttribute("d"));
      return regions === fx.projects.length && projectMarks.every((m, i) => m.getAttribute("aria-label") === fx.projects[i].label + " · " + fx.projects[i].runs.length + (fx.projects[i].runs.length === 1 ? " run" : " runs"))
        && fx.projects.some((p) => p.name === null) && runMarks.every(diamond) && runMarks.every((m) => !m.querySelector("circle"))
        && claimMarks.every((m) => m.querySelectorAll("circle").length === 2 && !m.querySelector("path"));`)));
    // R3 relationships are drawn
    check("R3: every support edge is an arrowed line from the supporter to the claim it supports", await E(page(`
      const lines = [...V.querySelectorAll("path[data-supports]")];
      const at = (run, id) => V.querySelector('[data-mark][data-run="' + run + '"][data-claim="' + id + '"] circle:not(.hidden)');
      const near = (p, c) => Math.hypot(p.x - +c.getAttribute("cx"), p.y - +c.getAttribute("cy")) <= 6.5;
      return lines.length === runs.reduce((n, r) => n + r.edges.length, 0) && lines.length > 0
        && runs.every((r) => r.edges.every((e) => lines.some((l) => l.dataset.supports === r.key && l.dataset.supported === e.from && l.dataset.supporter === e.to)))
        && lines.every((l) => l.getAttribute("marker-end") && near(l.getPointAtLength(0), at(l.dataset.supports, l.dataset.supporter)) && near(l.getPointAtLength(l.getTotalLength()), at(l.dataset.supports, l.dataset.supported)));`)));
    check("R3: every next attempt is a dashed arrowed line from the earlier run to the later", await E(page(`
      const lines = [...V.querySelectorAll("path[data-attempt]")], want = runs.filter((r) => r.previous);
      return want.length > 0 && lines.length === want.length && want.every((r) => lines.some((l) => l.dataset.attempt === r.key && l.dataset.previous === r.previous && l.getAttribute("stroke-dasharray") && l.getAttribute("marker-end")));`)));
    check("R3: claims are drawn under their own run, runs inside their project's region", await E(page(`
      const vis = (m) => (m.querySelector("path:not(.hidden), circle:not(.hidden)") ?? m).getBoundingClientRect();
      const regions = [...V.querySelectorAll("svg > rect.stroke-border")].map((r) => r.getBoundingClientRect());
      const inside = (b, o) => b.left >= o.left - 1 && b.right <= o.right + 1 && b.top >= o.top - 1 && b.bottom <= o.bottom + 1;
      return fx.projects.every((p, i) => p.runs.every((r) => {
        const head = vis(V.querySelector('[data-mark][data-run="' + r.key + '"]:not([data-claim])'));
        return inside(head, regions[i]) && r.claims.every((c) => { const b = vis(V.querySelector('[data-mark][data-run="' + r.key + '"][data-claim="' + c.id + '"]')); return inside(b, regions[i]) && b.top > head.bottom; });
      }));`)));
    // R4 the encodings
    check("R4: a run shows its state's colour and glyph, approved of total, and its diagnostic's mark, else its audit's", await E(page(`
      return runs.every((r) => {
        const m = V.querySelector('[data-mark][data-run="' + r.key + '"]:not([data-claim])'), s = r.state === null ? null : states.run[r.state];
        const side = r.diagnostic ? states.diagnostic[r.diagnostic] : r.audit ? states.audit[r.audit] : null;
        const uses = [...m.querySelectorAll("use")].map((u) => u.getAttribute("href").split("#")[1]), text = m.querySelector("text")?.textContent ?? null;
        const approved = r.claims.filter((c) => c.state === "approved").length;
        const fill = m.querySelector("path").classList.contains(s ? "fill-tone-" + s.tone + "-tint" : "fill-none");
        return fill && (s ? uses[0] === icons[s.icon] : true)
          && (side ? uses.length === (s ? 2 : 1) && uses.at(-1) === icons[side.icon] : uses.length === 1)
          && text === (r.claims.length ? approved + "/" + r.claims.length : null);
      }) && runs.some((r) => r.diagnostic && r.state) && runs.some((r) => r.state === null);`)));
    check("R4: a claim shows its state's colour; its state's glyph unless quiet, else its review's unless that is quiet too; the thick outline if and only if gating", await E(page(`
      let unsettled = 0;
      const ok = runs.every((r) => r.claims.every((c) => {
        const m = V.querySelector('[data-mark][data-run="' + r.key + '"][data-claim="' + c.id + '"]'), s = states.claim[c.state], dot = m.querySelector("circle:not(.hidden)");
        const glyph = !s.quiet ? s : states.review[c.review].quiet ? null : states.review[c.review];
        if (s.quiet && glyph) unsettled++;
        return dot.classList.contains("fill-tone-" + s.tone + "-tint") && (dot.getAttribute("stroke-width") === "2") === c.gating
          && (glyph ? m.querySelector("use").getAttribute("href").endsWith("#" + icons[glyph.icon]) : !m.querySelector("use"))
          && (!s.quiet || !glyph || m.getAttribute("aria-label").startsWith("Approved · "));
      }));
      return ok && unsettled > 0;`)));
    check("R4: the view encodes nothing else: no other shapes, text only on project labels and run counts", await E(`(() => {
      const svg = document.querySelector("ev-view svg"); const ok = new Set(["svg", "defs", "marker", "path", "g", "rect", "circle", "text", "tspan", "use"]);
      return [...svg.querySelectorAll("*")].every((n) => ok.has(n.localName)) && [...svg.querySelectorAll("text")].every((t) => t.closest("[data-project], [data-run]")); })()`));
    check("R4: every mark has a name and a hint of at most 60 characters", await E(`[...document.querySelectorAll("ev-view [data-mark]")].every((m) => m.getAttribute("aria-label") && m.dataset.hint && m.dataset.hint.length <= 60 && !/\\bC\\d+\\b/.test(m.dataset.hint))`));
    check("R7: no files in the view", await E(`!document.querySelector("ev-view [data-path], ev-view ev-file, main ev-file")`));
    // The claim row counts each claim once, under the mark it wears (approved ones not settled by their review, V3).
    const key = [runs.filter((r) => r.state).length, runs.filter((r) => r.audit).length, runs.filter((r) => r.diagnostic).length, claims.length, runs.reduce((n, r) => n + r.edges.length, 0), runs.filter((r) => r.previous).length];
    check("key: the counts are what is showing", same(await E(`(() => { const n = (i) => [...document.querySelectorAll("#key ul")[i].querySelectorAll("li")].map((l) => +l.lastElementChild.textContent);
      const sum = (a) => a.reduce((x, y) => x + y, 0);
      return [sum(n(0)), sum(n(1)), sum(n(2)), sum(n(3).slice(0, -1)), n(4)[0], n(4)[1]]; })()`), key), key);
    const partial = runs.filter((r) => r.state && r.diagnostic);
    check("a run not read in full never claims it has no claim graph: its mark says its state and why", partial.length > 0 && await E(`${JSON.stringify(partial.map((r) => r.key))}.every((k) => {
      const m = document.querySelector('ev-view [data-mark][data-run="' + k + '"]:not([data-claim])');
      return !/claim graph/.test(m.getAttribute("aria-label") + m.dataset.hint); })`));
    await E(clickRun(partial[0].key)); await P.sleep(300);
    check("…nor does its detail", await E(`(() => { const x = ${DETAIL}.textContent; return !/claim graph/.test(x) && !x.includes("Record") && x.includes("·"); })()`));
    // Q3a-1/9: every detail of every run reads as words: no `{name}` from a template, no `⟦key⟧`, and a residual names its claim.
    const words = `(() => { const x = document.body.innerText; return !/\\{\\w+\\}/.test(x) && !x.includes("⟦"); })()`;
    const seen = [];
    for (const r of runs) {
      await E(clickRun(r.key)); await P.sleep(200);
      const linked = await E(`[...${DETAIL}.querySelectorAll("[aria-label] a, section a")].length`);
      seen.push([await E(words), linked]);
    }
    check("no detail shows a `{placeholder}` or a missing-copy marker", seen.every(([ok]) => ok), seen);
    const twice = runs.find((r) => { const m = details[r.key].residuals.map((x) => x.message); return new Set(m).size < m.length; });
    await E(clickRun(twice.key)); await P.sleep(250);
    const asked = details[twice.key].residuals.filter((x) => x.claim);
    check("Q3a-9: residuals that share a message each name their own claim, in words, as a link", same(await E(`[...${DETAIL}.querySelectorAll('section[aria-label="What is left"] a')].map((a) => a.textContent)`),
      asked.map((x) => twice.claims.find((c) => c.id === x.claim).text.slice(0, 48))) || (await E(`${DETAIL}.querySelectorAll('section[aria-label="What is left"] a').length`)) === asked.length);
    const budget = runs.find((r) => r.state === "stopped_budget");
    await E(clickRun(budget.key)); await P.sleep(250);
    check("Q3a-1: a residual is worded with its parameters: the budget that ran out", await E(`${DETAIL}.textContent.includes("pass budget exhausted.")`));
    // ADR-0067: how each research record is verified, what waits on the human, the decision
    await E(clickRun(VERIFY)); await P.sleep(250);
    check("ADR-0067: a run lists what waits on the human, each claim named", await E(`(() => { const s = ${DETAIL}.querySelector('section[aria-label="Waiting for your confirmation"]'); return !!s && s.querySelectorAll("a").length === ${details[VERIFY].waiting.length}; })()`));
    const observed = details[VERIFY].claims.find((c) => c.records.some((x) => x.class === "observed" && x.status === "rejected_by_human"));
    await E(`document.querySelector('ev-view [data-mark][data-run="${VERIFY}"][data-claim="${observed.id}"]').dispatchEvent(new MouseEvent("click", { bubbles: true }))`); await P.sleep(250);
    check("ADR-0067: a record shows its source and how it stands; the quote only on click", same(await E(`(async () => { const d = ${DETAIL}.querySelector("ev-record details"); const q = d.querySelector("blockquote"); const before = q.checkVisibility(); d.querySelector("summary").click(); await new Promise((r) => setTimeout(r, 100)); return [d.querySelector("summary").textContent.includes("Rejected by you"), before, q.checkVisibility(), d.textContent.includes("The job only runs on the nightly cluster."), d.textContent.includes("Rejected, your note") && d.textContent.includes("That log is from a different job.")]; })()`), [true, false, true, true, true]));
    const waiting = details[VERIFY].claims.find((c) => c.decision?.route === "human");
    await E(`document.querySelector('ev-view [data-mark][data-run="${VERIFY}"][data-claim="${waiting.id}"]').dispatchEvent(new MouseEvent("click", { bubbles: true }))`); await P.sleep(250);
    check("ADR-0067: a needs-decision claim is worded as a decision it waits for", await E(`${DETAIL}.textContent.includes("Waiting for your decision") && !${DETAIL}.textContent.includes("Waiting for your confirmation")`));
    // R3 M2: a stopped run asks nobody, and says so; it promises no dialog
    const STOPPED = runs.find((r) => r.state === "stopped_residual" && details[r.key].claims.some((c) => c.decision && c.decision.outcome === null));
    check("R3 M2: the demo has a stopped run with a decision still unanswered", !!STOPPED);
    await E(clickRun(STOPPED.key)); await P.sleep(250);
    check("R3 M2: a stopped run lists nothing as waiting for a confirmation", await E(`!${DETAIL}.querySelector('section[aria-label="Waiting for your confirmation"]') && !${DETAIL}.textContent.includes("Waiting for your")`));
    const undecided = details[STOPPED.key].claims.find((c) => c.decision && c.decision.outcome === null);
    await E(`document.querySelector('ev-view [data-mark][data-run="${STOPPED.key}"][data-claim="${undecided.id}"]').dispatchEvent(new MouseEvent("click", { bubbles: true }))`); await P.sleep(250);
    check("R3 M2: its decision says nobody will ask, not that a dialog is coming", await E(`${DETAIL}.textContent.includes("Not asked · the run has stopped") && !${DETAIL}.textContent.includes("Waiting for your")`));
    // R6 details on click; nothing moves
    const before = await E(RECTS);
    await E(clickRun(CONVERGED)); await P.sleep(300);
    check("R6: a run opens its detail: goal, audit, budgets, history", await E(`(() => { const d = ${DETAIL}, x = d.textContent;
      return document.getElementById("split").hasAttribute("open") && x.includes("Run") && x.includes("Goal") && x.includes("Audit") && x.includes("Passes") && !!d.querySelector("ev-history"); })()`));
    check("R6: clicking a run moves nothing in the view", (await E(RECTS)) === before);
    check("R6: only the run is selected, and the URL names it", await E(`(() => { const s = document.querySelectorAll("ev-view [data-selected]"); return s.length === 1 && s[0].dataset.run === "${CONVERGED}" && !s[0].dataset.claim && s[0].getAttribute("aria-pressed") === "true" && new URLSearchParams(location.search).get("run") === "${CONVERGED}"; })()`));
    await E(`document.querySelector("#split ev-detail ev-history [data-step]").click()`); await P.sleep(150);
    check("R6: a history step in the run's detail opens that step", await E(`/Step \\d+ of ${details[CONVERGED].steps.length}/.test(${DETAIL}.textContent)`));
    await E(clickRun(STALE)); await P.sleep(300);
    check("R6: a run whose spiked files changed lists them", await E(`${DETAIL}.querySelectorAll("ev-file").length === ${details[STALE].stale.length}`));
    await E(`document.querySelector("#split ev-detail ev-file button").click()`); await P.sleep(150);
    check("R6: a file in the run's detail opens that file (files are details, never marks)", await E(`${DETAIL}.textContent.includes("Folder")`));
    await E(`window.__c = document.querySelector('ev-view [data-mark][data-run="${CONVERGED}"][data-claim="C1"]'); __c.focus(); __c.dispatchEvent(new MouseEvent("click", { bubbles: true }))`); await P.sleep(300);
    check("R6: a claim opens its detail; nothing moves; only it is selected", (await E(RECTS)) === before && await E(`(() => { const s = document.querySelectorAll("ev-view [data-selected]"); const x = ${DETAIL}.textContent;
      return s.length === 1 && s[0] === __c && x.includes("Claim") && x.includes("Review") && x.includes("Supporting research") && !/\\bC1\\b/.test(x.split("Technical")[0]); })()`));
    await E(`document.querySelector("#split ev-detail dl a[href]").click()`); await P.sleep(300);
    check("R6: the claim's run link selects the run in the view", await E(`document.querySelector("ev-view [data-selected]")?.dataset.run === "${CONVERGED}" && !document.querySelector("ev-view [data-selected]").dataset.claim`));
    const blocked = runs.flatMap((r) => r.claims.filter((c) => c.blocker).map((c) => [r.key, c])).find(([, c]) => c.blocker_claim === c.id);
    await E(`document.querySelector('ev-view [data-mark][data-run="${blocked[0]}"][data-claim="${blocked[1].id}"]').dispatchEvent(new MouseEvent("click", { bubbles: true }))`); await P.sleep(300);
    const message = details[blocked[0]].claims.find((c) => c.id === blocked[1].id).blocker_message;
    check("R6: a blocked claim says what blocks it, in the plugin's words", await E(`${DETAIL}.textContent.includes(${JSON.stringify(message)})`), message);
    await E(`document.querySelector("ev-view [data-mark][data-project]").dispatchEvent(new MouseEvent("click", { bubbles: true }))`); await P.sleep(150);
    check("R6: a project opens its detail: its runs and claims, counted", (await E(RECTS)) === before && await E(`(() => { const x = ${DETAIL}.textContent; return x.includes(${JSON.stringify(fx.projects[0].label)}) && x.includes("Runs") && x.includes("Claims"); })()`));
    await E(`__c.focus(); __c.dispatchEvent(new MouseEvent("click", { bubbles: true }))`); await P.sleep(150);
    await P.key("Escape"); await P.sleep(150);
    check("Esc closes the innermost layer first: the hint on the focused mark, the selection unchanged", same(await E(`[document.querySelector("ev-hint").matches(":popover-open"), new URLSearchParams(location.search).get("claim")]`), [false, "C1"]));
    await P.key("Escape"); await P.sleep(150);
    check("Esc leaves one level: a claim goes to its run, focus back on the claim's mark", same(await E(`[document.querySelector("ev-view [data-selected]")?.dataset.claim ?? null, document.querySelector("ev-view [data-selected]")?.dataset.run === "${CONVERGED}", document.activeElement === __c, new URLSearchParams(location.search).has("claim")]`), [null, true, true, false]));
    await P.key("Escape"); await P.sleep(150);
    await P.key("Escape"); await P.sleep(150);
    await P.key("Escape"); await P.sleep(150);
    check("Esc at the top clears the selection; the panel stays and says what to click", same(await E(`[document.querySelectorAll("ev-view [data-selected]").length, document.getElementById("split").hasAttribute("open"), !!document.querySelector("#split ev-detail [data-empty]"), ["project", "run", "claim"].some((k) => new URLSearchParams(location.search).has(k))]`), [0, true, true, false]));
    check("R6: Esc moves nothing in the view", (await E(RECTS)) === before);
    // keyboard
    check("keyboard: the view is one tab stop", await E(`document.querySelectorAll('ev-view [data-mark][tabindex="0"]').length === 1`));
    await E(`window.__k = [...document.querySelectorAll("ev-view [data-mark]")].findIndex((m) => m.getAttribute("tabindex") === "0"); document.querySelectorAll("ev-view [data-mark]")[__k].focus()`); await P.key("ArrowRight"); await P.sleep(50);
    check("keyboard: ArrowRight moves to the next mark and carries the stop", await E(`(() => { const all = [...document.querySelectorAll("ev-view [data-mark]")]; return document.activeElement === all[__k + 1] && all[__k + 1].getAttribute("tabindex") === "0" && all[__k].getAttribute("tabindex") === "-1"; })()`));
    await P.key(" "); await P.sleep(150);
    check("keyboard: Space opens the focused mark", await E(`document.activeElement.hasAttribute("data-selected") && !document.querySelector("#split ev-detail [data-empty]")`));
    check("keyboard: the focused, selected mark has the focus outline and its selection shape", await E(`(() => { const m = document.activeElement, r = m.querySelector('[class*="group-data-selected"]'); return getComputedStyle(m).outlineStyle === "solid" && !!r && getComputedStyle(r).display !== "none"; })()`));
    // R5 filters remove
    await E(`__c.dispatchEvent(new MouseEvent("click", { bubbles: true }))`); await P.sleep(150);  // an approved claim: the "open" filter removes it
    await E(tick(box(["claim-state", "open"]), true));
    const open = expect((c) => c.state === "open");
    check("R5: a claim filter keeps only matching claims, and runs and projects that still have one", await E(`${SHOWING} === "${showing(open)}"`) && same(await E(counts), open)
      && await E(page(`return claimMarks.every((m) => runs.find((r) => r.key === m.dataset.run).claims.find((c) => c.id === m.dataset.claim).state === "open") && runMarks.every((m) => V.querySelector('[data-mark][data-claim][data-run="' + m.dataset.run + '"]'));`)), await E(counts));
    check("R5: a run's count stays its own while a filter hides some of its claims", await E(page(`return runMarks.every((m) => { const r = runs.find((x) => x.key === m.dataset.run); return m.querySelector("text").textContent === r.claims.filter((c) => c.state === "approved").length + "/" + r.claims.length; });`)));
    check("R5: lines touching a removed claim go", await E(page(`const drawn = new Set(claimMarks.map((m) => m.dataset.run + " " + m.dataset.claim));
      return [...V.querySelectorAll("path[data-supports]")].every((l) => drawn.has(l.dataset.supports + " " + l.dataset.supporter) && drawn.has(l.dataset.supports + " " + l.dataset.supported));`)));
    check("R5: a selection the filter removes goes with it", await E(`!document.querySelector("ev-view [data-selected]") && !!document.querySelector("#split ev-detail [data-empty]")`));
    check("R5: the URL keeps the filter", await E(`new URLSearchParams(location.search).get("f.claim-state") === "open"`));
    await E(tick(box(["claim-state", "blocked"]), true));
    const either = expect((c) => ["open", "blocked"].includes(c.state));
    check("R5: values within one filter are alternatives", same(await E(counts), either) && either[2] > open[2], await E(counts));
    await E(tick(box(["kind", "needs-decision"]), true));
    const all3 = expect((c) => ["open", "blocked"].includes(c.state) && c.kind === "needs-decision");
    check("R5: different filters must all hold", same(await E(counts), all3) && all3[2] > 0 && all3[2] < either[2], await E(counts));
    await E(`document.querySelector("ev-filters [data-clear]").click()`); await P.sleep(150);
    check("R5: clearing the filters brings everything back", same(await E(counts), expect(TRUE)) && await E(`![...new URLSearchParams(location.search).keys()].some((k) => k.startsWith("f."))`));
    await E(tick(box(["blocks"]), true));
    check("R5: \"Block convergence\" keeps the claims the plugin names a blocker for", same(await E(counts), expect((c) => c.blocker !== null)), await E(counts));
    await E(tick(box(["blocks"]), false));
    for (const [id, value] of [["evidence", "research"], ["review", "unreviewed"]]) {
      await E(tick(box([id, value]), true));
      check(`R5: the ${id} filter keeps the claims whose ${id} is ${value}`, same(await E(counts), expect((c) => c[id] === value)), await E(counts));
      await E(tick(box([id, value]), false));
    }
    await E(tick(box(["run-state", "converged"]), true));
    check("R5: a run filter keeps the matching runs with all their claims", same(await E(counts), expect(TRUE, (r) => r.state === "converged")), await E(counts));
    check("R5: a next-attempt line goes when either run does", await E(page(`return [...V.querySelectorAll("path[data-attempt]")].every((l) => runMarks.some((m) => m.dataset.run === l.dataset.attempt) && runMarks.some((m) => m.dataset.run === l.dataset.previous));`)));
    await E(tick(box(["claim-state", "blocked"]), true));
    check("R5: when nothing matches, the view says so", same(expect((c) => c.state === "blocked", (r) => r.state === "converged"), [0, 0, 0])
      && await E(`!!document.querySelector("ev-view p") && document.querySelector("ev-view p").textContent.includes("Nothing matches") && ${SHOWING} === "0 projects · 0 runs · 0 claims"`));
    // the panel at other widths
    await E(`document.querySelector("ev-filters [data-clear]").click()`); await P.sleep(150);
    await resize(P, 1100, 900); await P.sleep(250);
    check("1100 px: the filters, the view and the panel side by side, the view at least 20rem", await E(`document.querySelector("ev-view").getBoundingClientRect().width >= 318 && !document.getElementById("split").hasAttribute("narrow") && document.getElementById("split").hasAttribute("open")`), await E(`document.querySelector("ev-view").getBoundingClientRect().width`));
    await resize(P, 900, 900); await P.sleep(250);
    check("900 px: narrow; with nothing selected the view has the width", await E(`document.getElementById("split").hasAttribute("narrow") && !document.getElementById("split").hasAttribute("open") && document.querySelector("ev-view").getBoundingClientRect().width >= 500`));
    await resize(P, 360, 800); await P.sleep(300);
    check("360 px: with nothing selected the panel is closed and nothing scrolls sideways", same(await E(`[document.getElementById("split").hasAttribute("narrow"), document.getElementById("split").hasAttribute("open"), document.documentElement.scrollWidth <= innerWidth]`), [true, false, true]));
    await resize(P, 1500, 900); await P.sleep(300);
    check("wide again: the panel is open again", await E(`document.getElementById("split").hasAttribute("open")`));
    check("separator reports min/now/max", await E(`(() => { const h=document.querySelector(".ev-split-handle"); return +h.getAttribute("aria-valuemin") > 0 && +h.getAttribute("aria-valuenow") > 0 && +h.getAttribute("aria-valuemax") >= +h.getAttribute("aria-valuenow") && !!h.getAttribute("aria-valuetext"); })()`));
    check("cancelled drag cleans up", same(await E(`(() => { const h=document.querySelector(".ev-split-handle"), s=document.getElementById("split"); h.dispatchEvent(new PointerEvent("pointerdown",{pointerId:1,bubbles:true})); const d=s.classList.contains("select-none"); h.dispatchEvent(new PointerEvent("pointercancel",{pointerId:1,bubbles:true})); return [d, s.classList.contains("select-none")]; })()`), [true, false]));
    check("reconnect does not duplicate events", (await E(`(() => { const t=document.querySelector("ev-view"); let n=0; const h=()=>n++; document.addEventListener("ev-open",h); const p=t.parentNode; p.removeChild(t); p.appendChild(t); t.querySelector("[data-claim]").dispatchEvent(new MouseEvent("click",{bubbles:true})); document.removeEventListener("ev-open",h); return n; })()`)) === 1);
    check("hint hides when its target is removed", await E(`(async () => { const b=document.querySelector("ev-view [data-claim]"); b.dispatchEvent(new PointerEvent("pointerover",{bubbles:true})); const shown=document.getElementById("ev-hint").matches(":popover-open"); b.remove(); await new Promise(r=>setTimeout(r,50)); return shown && !document.getElementById("ev-hint").matches(":popover-open"); })()`));
  });

  // Polling: this check answers the app's /api/view itself, so the store can change under it.
  await withPage(`${APP}&run=${encodeURIComponent(CONVERGED)}&claim=C1`, "1500x900", async (P) => {
    await P.waitReady(); await P.sleep(300);
    const E = P.evalJs;
    let answer = null;  // null: let the request through; "down": fail it; else the view to answer with
    let detail = null;  // when set, /api/run answers with this run document
    P.on("Fetch.requestPaused", ({ requestId, request }) => {
      if (request.url.includes("/api/run")) {
        if (!detail) return P.send("Fetch.continueRequest", { requestId });
        const body = Buffer.from(JSON.stringify(detail)).toString("base64");
        return P.send("Fetch.fulfillRequest", { requestId, responseCode: 200, body, responseHeaders: [{ name: "Content-Type", value: "application/json" }] });
      }
      if (answer === null) return P.send("Fetch.continueRequest", { requestId });
      if (answer === "down") return P.send("Fetch.failRequest", { requestId, errorReason: "ConnectionRefused" });
      const body = Buffer.from(JSON.stringify(answer)).toString("base64");
      P.send("Fetch.fulfillRequest", { requestId, responseCode: 200, body, responseHeaders: [{ name: "Content-Type", value: "application/json" }, { name: "ETag", value: `"${answer.token}"` }] });
    });
    await P.send("Fetch.enable", { patterns: [{ urlPattern: "*/api/view*" }, { urlPattern: "*/api/run*" }] });
    const poll = () => P.sleep(3600);
    const without = (key) => ({ ...fx, token: `sha256:${"1".repeat(63)}${key.length % 10}`, projects: fx.projects.map((p) => ({ ...p, runs: p.runs.filter((r) => r.key !== key) })).filter((p) => p.runs.length) });
    await E(`window.__d = document.querySelector("#split ev-detail").data`);
    const other = runs.find((r) => r.key !== CONVERGED && fx.projects.find((p) => p.runs.includes(r)).runs.length > 1).key;
    answer = without(other); await poll();
    check("polling: a run that leaves the store leaves the view", same(await E(counts), [fx.projects.length, runs.length - 1, claims.length - runs.find((r) => r.key === other).claims.length]), await E(counts));
    check("polling: an unchanged selection keeps its mark and its panel as they were", await E(`document.querySelector("ev-view [data-selected]")?.dataset.claim === "C1" && document.querySelector("#split ev-detail").data === __d`));
    const moved = without(other);
    moved.token = `sha256:${"2".repeat(64)}`;
    for (const p of moved.projects) p.runs = p.runs.map((r) => (r.key === CONVERGED ? { ...r, version: `sha256:${"3".repeat(64)}` } : r));
    const message = "A blocker only the new version has.";
    detail = { ...details[CONVERGED], version: `sha256:${"3".repeat(64)}`, claims: details[CONVERGED].claims.map((c) => (c.id === "C1" ? { ...c, blocker_message: message } : c)) };
    answer = moved; await poll();
    check("polling: when the selected run changes, its new detail is fetched and shown, and it stays selected", await E(`document.querySelector("ev-view [data-selected]")?.dataset.claim === "C1" && document.querySelector("#split ev-detail").data !== __d && document.querySelector("#split ev-detail").textContent.includes(${JSON.stringify(message)})`));
    answer = "down"; await poll();
    check("polling: an unreachable observer is said, and the view stays", await E(`document.getElementById("status").textContent.includes("Can't reach")`) && (await E(counts))[1] === runs.length - 1);
    answer = without(CONVERGED); await poll();
    check("polling: the observer back, the message goes; a selected run that left the store is deselected", await E(`document.getElementById("status").textContent === "" && !document.querySelector("ev-view [data-selected]") && !!document.querySelector("#split ev-detail [data-empty]") && !new URLSearchParams(location.search).has("run")`));
  });

  // Labels are the observer's, so a reorder relabels nothing; a detail that cannot be had says so in the panel and
  // leaves the header to the view poll; a bad view is said and the next good one recovers.
  await withPage(`${APP}`, "1500x900", async (P) => {
    await P.waitReady(); await P.sleep(300);
    const E = P.evalJs;
    let answer = null, run = null;
    P.on("Fetch.requestPaused", ({ requestId, request }) => {
      if (request.url.includes("/api/run")) {
        if (run === null) return P.send("Fetch.continueRequest", { requestId });
        return P.send("Fetch.fulfillRequest", { requestId, responseCode: run, body: "", responseHeaders: [] });
      }
      if (answer === null) return P.send("Fetch.continueRequest", { requestId });
      const body = Buffer.from(JSON.stringify(answer)).toString("base64");
      P.send("Fetch.fulfillRequest", { requestId, responseCode: 200, body, responseHeaders: [{ name: "Content-Type", value: "application/json" }, { name: "ETag", value: `"${answer.token ?? "x"}"` }] });
    });
    await P.send("Fetch.enable", { patterns: [{ urlPattern: "*/api/view*" }, { urlPattern: "*/api/run*" }] });
    const poll = () => P.sleep(3600);
    const unnamed = fx.projects.find((p) => p.name === null);
    await E(`document.querySelector('ev-view [data-mark][data-project="${unnamed.id}"]').dispatchEvent(new MouseEvent("click", { bubbles: true }))`); await P.sleep(200);
    const labels = `[...document.querySelectorAll("ev-view [data-mark][data-project]")].map((m) => m.getAttribute("aria-label")).sort()`;
    const before = await E(labels);
    answer = { ...fx, token: `sha256:${"4".repeat(64)}`, projects: [...fx.projects].reverse() }; await poll();
    check("Q3b-8: reordering the projects relabels none, and the panel and the view agree", same(await E(labels), before)
      && await E(`${DETAIL}.querySelector("p.font-medium").textContent === ${JSON.stringify(unnamed.label)} && document.querySelector("ev-view [data-selected]").getAttribute("aria-label").startsWith(${JSON.stringify(unnamed.label)})`));
    check("Q3b-8: an unnamed project is told apart in its detail by its first activity, not by a number", await E(`${DETAIL}.textContent.includes("First activity") && !/Project \\d+/.test(document.body.innerText)`));
    answer = { contract: "empirica-ui-view/v1" }; await poll();
    check("Q3a-3: a view that is not the contract is said; the last good view stays", await E(`document.getElementById("status").textContent.includes("cannot read") && document.querySelectorAll("ev-view [data-mark]").length > 0`));
    answer = null; await poll();
    check("Q3a-3: polling goes on after a bad view, and recovers", await E(`document.getElementById("status").textContent === ""`));
    run = 500;
    await E(`document.querySelector('ev-view [data-mark][data-run="${CONVERGED}"]:not([data-claim])').dispatchEvent(new MouseEvent("click", { bubbles: true }))`); await P.sleep(300);
    check("Q3a-7: a detail that cannot be had says so inside the panel, keeps the row's facts, and leaves the header alone", await E(`${DETAIL}.querySelector("[data-notice]")?.textContent.includes("reported an error") && !${DETAIL}.querySelector("[data-notice]")?.textContent.includes("can't be reached") && ${DETAIL}.textContent.includes("Audit") && document.getElementById("status").textContent === ""`));
    run = null;
  });

  // A link to a claim opens with it selected and the filters it names applied.
  await withPage(`${APP}&f.claim-state=approved&run=${encodeURIComponent(CONVERGED)}&claim=C1`, "1500x900", async (P) => {
    await P.waitReady(); await P.sleep(400);
    const E = P.evalJs;
    check("URL: filters and a selection are restored", await E(`(() => { const s = document.querySelector("ev-view [data-selected]"); return s?.dataset.claim === "C1" && ${SHOWING}.endsWith("claims") && document.querySelector('ev-filters input[value="approved"]').checked && document.querySelector("#split ev-detail").textContent.includes("Claim"); })()`));
  });

  // "Your store" here is an empty home: the view says the store has no runs yet.
  await withPage(`${S}/app/`, "1500x900", async (P) => {
    await P.waitReady(); await P.sleep(300);
    const E = P.evalJs;
    check("an empty store says it has no runs yet", await E(`document.querySelector("ev-view p")?.textContent.includes("no runs yet") && document.getElementById("status").textContent === ""`));
  });
  // A demo-only server: its landing page is the demo, and asking it for a store says so without claiming an empty one.
  check("a demo-only server lands on the demo", (await fetch(`${B}/`, { redirect: "manual" })).headers.get("location") === "/app/?data=demo");
  await withPage(`${B}/app/?data=store`, "1500x900", async (P) => {
    await P.waitReady(); await P.sleep(300);
    const E = P.evalJs;
    check("a source the server does not serve is said, and the view claims no empty store", await E(`document.getElementById("status").textContent.includes("does not serve") && !document.querySelector("ev-view p")?.textContent.includes("no runs yet")`));
  });

  await withPage(`${B}/gallery/`, "1280x900", async (P) => {
    await P.waitReady(); await P.sleep(400);
    const E = P.evalJs;
    check("gallery: no missing copy", !(await E(`document.body.innerHTML.includes("⟦")`)));
    await E(`document.querySelector("ev-step button")?.click()`); await P.sleep(100);
    check("gallery: an atom step opens nothing", !(await E(`document.getElementById("split").hasAttribute("open")`)));
    await E(`document.querySelector("#g-view [data-claim]").dispatchEvent(new MouseEvent("click", { bubbles: true }))`); await P.sleep(150);
    check("gallery: the view opens a claim", await E(`document.querySelector("#split ev-detail").textContent.includes("Claim")`));
    await P.key("Escape"); await P.sleep(100);
    check("gallery: closing clears the view's selection", await E(`!document.querySelector("#g-view [data-selected]")`));
    await E(`(() => { const x = document.querySelector('#g-filters input[value="open"]'); x.checked = true; x.dispatchEvent(new Event("change", { bubbles: true })); })()`); await P.sleep(200);
    check("gallery: the view's filters work there too", await E(`document.querySelectorAll("#g-view [data-claim]").length === ${claims.filter((c) => c.state === "open").length}`));
    check("decision: IBM Plex Sans is the UI font", await E(`getComputedStyle(document.body).fontFamily.startsWith('"IBM Plex Sans Variable"')`));
    await E(`document.activeElement.blur(); document.body.focus()`); await P.key("Tab"); await P.sleep(100);
    check("decision: keyboard focus is a solid 2 px ring-colour outline", await E(`(() => { const c=getComputedStyle(document.activeElement); return c.outlineStyle === "solid" && c.outlineWidth === "2px"; })()`));
    const rep = await E(`(() => { const h=document.querySelector("#o-history"); const g=h.querySelector("details"); if (!g) return null; const inner=[...g.querySelectorAll("[data-step]")].map(b=>+b.dataset.step); return { open: g.open, n: inner.length, inner }; })()`);
    check("decision: History gathers repeats into a closed disclosure", rep && !rep.open && rep.n > 1, rep);
    if (rep) {
      await E(`document.querySelector("#o-history").setAttribute("selected", "${rep.inner[1]}")`); await P.sleep(50);
      check("decision: a repeat group opens when it holds the selected step", await E(`[...document.querySelectorAll("#o-history details")].some(d => d.open && d.querySelector('[data-step="${rep.inner[1]}"]'))`));
    }
    check("decision: failed spikes and audits never hide in a group", await E(`[...document.querySelectorAll("#o-history details summary")].every(s => !/failed/i.test(s.textContent))`));
    check("gallery: every step kind and every step outcome has a specimen", await E(page(`
      const kinds = new Set([...document.querySelectorAll("ev-step")].map((e) => e.getAttribute("value"))), outs = new Set([...document.querySelectorAll("ev-step")].map((e) => e.getAttribute("outcome")));
      return Object.keys(states.step).every((k) => kinds.has(k)) && Object.keys(states.outcome).every((o) => outs.has(o));`)));
    // m4: "Show all" appears only on text that does not fit two lines.
    const MORE = `(() => { const [short, long] = [document.querySelector("ev-more[label]"), document.getElementById("g-long")].map((e) => e.querySelector("[data-more]")); return [!short.checkVisibility(), long.checkVisibility()]; })()`;
    check("gallery: Show all is on the long text and not on the short one", same(await E(MORE), [true, true]));
    await E(`document.getElementById("g-long").querySelector("[data-more]").click()`); await P.sleep(100);
    check("gallery: Show all opens the text in place and Show less folds it", await E(`(() => { const m = document.getElementById("g-long"), q = () => [m.querySelector("[data-text]"), m.querySelector("[data-more]")]; let [t, b] = q(); const open = !t.className.includes("line-clamp") && b.textContent === "Show less" && b.getAttribute("aria-expanded") === "true"; b.click(); [t, b] = q(); return open && t.className.includes("line-clamp") && b.textContent === "Show all"; })()`));
    // control: the same text made long gives the short one a control, and a long one made short loses it
    await E(`(() => { const a = document.querySelector("ev-more[label]"), b = document.getElementById("g-long"); a.setAttribute("text", b.getAttribute("text")); b.setAttribute("text", "Short."); })()`); await P.sleep(150);
    check("gallery control: with the texts swapped the long one has no control and the short one has one", same(await E(MORE), [false, false]));
    check("gallery: meters are named", await E(`[...document.querySelectorAll("meter")].every(m => m.getAttribute("aria-label"))`));
  });
  // m10: a poll answered 304 is not reported by the browser as a cancelled request
  await withPage(APP, "1500x900", async (P) => {
    const urls = new Map(), failures = [], statuses = [];
    P.on("Network.requestWillBeSent", (p) => urls.set(p.requestId, p.request.url));
    P.on("Network.loadingFailed", (p) => failures.push([urls.get(p.requestId), p.errorText]));
    P.on("Network.responseReceived", (p) => { if (urls.get(p.requestId)?.includes("/api/view")) statuses.push(p.response.status); });
    await P.send("Network.enable"); await P.waitReady();
    await P.sleep(7600);
    check("m10: two polls answered 304 leave no cancelled request", statuses.filter((s) => s === 304).length >= 2 && failures.length === 0, [statuses, failures]);
    // control: a 304 whose body the page never reads (the old client) is reported as cancelled, so the listener can see it
    await P.evalJs(`fetch("/api/view?source=demo", { headers: { "If-None-Match": ${JSON.stringify(await P.evalJs(`fetch("/api/view?source=demo").then((r) => r.headers.get("ETag"))`))} } }).then(() => 0)`);
    await P.sleep(400);
    check("m10 control: an unread 304 is reported as cancelled", failures.length >= 1, failures);
  });
  await walk({ B, fx, details, withPage, check, same });
  await layoutChecks({ B, fx, details, withPage, check, resize, keys: { STALE, VERIFY, CONVERGED } });
} finally {
  demo.p.kill(); store.p.kill();
  fs.rmSync(home, { recursive: true, force: true });
}
console.log(failed ? `browser: ${failed} failed` : "browser: all checks passed");
process.exit(failed ? 1 : 0);
