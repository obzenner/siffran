// Browser journeys for the UI, in headless Chrome via cdp.mjs, against the observer's own servers: the operator's tasks
// T1-T7 (what needs me and where; what is owed next; the proof behind each claim; why a run stopped and what was left;
// the attempts side by side; finding a remembered goal; telling same-named projects apart), each at 1440x900 and
// 1024x768, on the demo data (the fixtures the real v2 service generated) and on a live store the same service built
// (tests/v2/serve_generated.py). Each states how many clicks it may take; typing is not a click. Then the standing
// rules the earlier reviews pinned: keyboard and focus, URL and history, safe text, the empty store, error recovery
// (a failed detail is asked again, a failed poll is shown beside the last good check), polling paused in hidden tabs,
// no horizontal overflow, the stacked layout. Run: make ui-browser. One line per check; exits 1 on any failure.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { withPage } from "./cdp.mjs";

const ui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CLI = path.join(ui, "..", "adapters", "observer", "cli.py");
const GENERATED = path.join(ui, "..", "tests", "v2", "serve_generated.py");
const home = fs.mkdtempSync(path.join(os.tmpdir(), "ev-home-"));
const children = [];
function start(args, cwd = home) {
  const p = spawn("python3", args, { cwd, stdio: ["ignore", "pipe", "inherit"] });
  children.push(p);
  return new Promise((res, rej) => {
    let buf = "";
    p.stdout.on("data", (d) => { buf += d; const m = /http:\/\/127\.0\.0\.1:\d+/.exec(buf); if (m) res(m[0]); });
    setTimeout(() => rej(new Error("a server did not start")), 60000);
  });
}
const [DEMO, EMPTY, LIVE] = await Promise.all([start([CLI, "serve", "--port", "0", "--demo"]), start([CLI, "serve", "--port", "0", "--home", home]), start([GENERATED])]);
const SOURCES = [{ name: "demo", base: DEMO, data: "demo" }, { name: "generated", base: LIVE, data: "store" }];
const SIZES = ["1440x900", "1024x768"];
/** A section of the run workspace (its heading names it). */
const SEC = (name) => `section[aria-labelledby="${name}"]`;
const json = async (base, u) => (await fetch(`${base}${u}`)).json();
const fixtures = path.join(ui, "..", "vendor", "contracts", "empirica-ui-view", "v1", "fixtures");
const S = JSON.parse(fs.readFileSync(path.join(fixtures, "scenarios.json"), "utf8"));

let failed = 0, total = 0;
function check(name, ok, got) { total++; if (!ok) failed++; console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok ? "" : `  got ${JSON.stringify(got)?.slice(0, 600)}`}`); }

/** A page for one source and size, with the helpers every journey uses; `clicks` counts what a person would click. */
async function session(src, size, fn) {
  const url = `${src.base}/app/?data=${src.data}`;
  await withPage(url, size, async (P) => {
    const E = P.evalJs;
    let clicks = 0;
    const A = {
      P, E, size, src,
      go: async (query = "") => { await P.send("Page.navigate", { url: `${src.base}/app/?data=${src.data}${query}` }); await P.sleep(250); await P.waitReady(); await P.sleep(250); },
      click: async (sel) => { clicks++; const ok = await E(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return false; e.click(); return true; })()`); await P.sleep(350); return ok; },
      type: async (sel, text) => { await E(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.value = ${JSON.stringify(text)}; e.dispatchEvent(new Event("input", { bubbles: true })); })()`); await P.sleep(200); },
      text: (sel) => E(`document.querySelector(${JSON.stringify(sel)})?.innerText ?? ""`),
      reset: () => { clicks = 0; },
      get clicks() { return clicks; },
    };
    A.view = await json(src.base, `/api/view?source=${src.data}`);
    A.rows = A.view.projects.flatMap((p) => p.runs.map((row) => ({ row, project: p })));
    A.row = (scenario) => A.rows.find((x) => x.row.key === S[scenario]);
    A.detail = (scenario) => json(src.base, `/api/run?source=${src.data}&key=${encodeURIComponent(S[scenario])}`);
    A.run = (scenario, extra = "") => `&run=${encodeURIComponent(S[scenario])}${extra}`;
    A.rowSel = (scenario) => `ev-runs li[data-row="${S[scenario]}"]`;
    await P.waitReady();
    await fn(A);
    check(`no page errors, no missing copy: ${src.name} ${size}`, P.errors.length === 0 && !(await E(`document.body.innerHTML.includes("⟦")`)), P.errors);
  });
}

// ---- the operator's tasks ------------------------------------------------------------------------------------------
async function tasks(A) {
  const tag = `${A.src.name} ${A.size}`;
  const E = A.E;

  // T1 · what needs me, and where · 0 clicks: the first screen
  A.reset(); await A.go("");
  const v = A.row("verification"), want = v.row.next;
  const t1 = await E(`(() => { const li = document.querySelector('${A.rowSel("verification")}'); const b = li.getBoundingClientRect();
    return { first: document.querySelector("ev-runs h2").innerText.trim(), text: li.innerText, inView: b.top >= 0 && b.bottom <= innerHeight, copy: !!li.querySelector("ev-copy button"), value: li.querySelector("ev-copy code")?.textContent }; })()`);
  check(`T1 ${tag}: the Needs a human group is first and its run says who acts, where, what, and in which session (0 clicks)`,
    t1.first.startsWith("Needs a human") && t1.inView && /A human · when the agent next reports/.test(t1.text) && t1.text.includes(want.message) && t1.text.includes(v.row.session.label) && t1.value === v.row.session.locator && t1.copy && A.clicks === 0, t1);

  // T2 · stuck, or what comes next · 0 clicks for the row, 1 for the run
  const owing = A.rows.filter((x) => x.row.next);
  const t2 = await E(`(() => [...document.querySelectorAll("ev-runs li[data-row]")].map((li) => [li.dataset.row, li.innerText]))()`);
  const shown = new Map(t2);
  check(`T2 ${tag}: every run that owes something shows what, on its row (0 clicks)`, owing.length >= 5 && owing.every((x) => shown.get(x.row.key)?.includes(x.row.next.message)), owing.filter((x) => !shown.get(x.row.key)?.includes(x.row.next.message)).map((x) => x.row.key));
  const auditing = A.row("pending-reaudit").row;
  check(`T2 ${tag}: a run waiting on the auditor says the auditor, not a human`, /The auditor/.test(shown.get(auditing.key)) && !/A human ·/.test(shown.get(auditing.key)), shown.get(auditing.key));
  check(`T2 ${tag}: the ended runs say nothing is owed by listing no next step`, A.rows.filter((x) => x.row.attention === "ended").every((x) => !/Nothing owed|A human ·|The agent ·/.test(shown.get(x.row.key))), null);
  await A.click(`${A.rowSel("verification")} a[data-run]`);
  const d = await A.detail("verification"), now = await A.text(SEC("now"));
  check(`T2 ${tag}: opening the run lists each obligation with its message (1 click)`, A.clicks === 1 && d.outstanding.every((o) => now.includes(o.message)), now);

  // T3 · the proof behind a claim · 2 clicks from the list
  A.reset(); await A.go("");
  await A.click(`${A.rowSel("verification")} a[data-run]`);
  const claims = d.claims.filter((c) => c.records.some((r) => r.class === "observed" && r.status === "pending"));
  await A.click(`summary[data-claim="${claims[0].id}"]`);
  const rec = claims[0].records.find((r) => r.class === "observed" && r.status === "pending"), body = await A.text(SEC("claims"));
  check(`T3 ${tag}: a claim opens in place to its source, quote, kind of evidence, why no spike, and who it waits on (2 clicks)`,
    A.clicks === 2 && [rec.source, rec.quote, "An observation", rec.spike_unavailable, "Not confirmed yet"].every((x) => body.includes(x)) && /A human · when the agent next reports/.test(body), body);
  A.reset(); await A.go("");
  await A.click(`${A.rowSel("converged")} a[data-run]`);
  const cd = await A.detail("converged"), spiked = cd.claims.find((c) => c.proof.length);
  await A.click(`summary[data-claim="${spiked.id}"]`);
  const proof = await A.text(SEC("claims")), head = await A.text("#workspace header");
  check(`T3 ${tag}: the spike command, the assurance, the review and the independence are together (2 clicks)`,
    A.clicks === 2 && proof.includes(spiked.proof[0].command) && proof.includes("not kept") && /Assurance/.test(proof) && /Review/.test(proof) && /Independent of the author/.test(proof + head) && /Audit passed/.test(head), proof);

  // T4 · why it stopped and what was left · 1 click
  A.reset(); await A.go("");
  await A.click(`${A.rowSel("decision")} a[data-run]`);
  const sd = await A.detail("decision"), outcome = await A.text(SEC("now"));
  check(`T4 ${tag}: a stopped run says it ended and lists what was unresolved, in the observer's words (1 click)`,
    A.clicks === 1 && /^Outcome/.test(outcome) && outcome.includes("This run has ended") && /Unresolved when it stopped/.test(outcome) && sd.residuals.length > 0 && sd.residuals.every((r) => outcome.includes(r.message)), outcome);
  A.reset(); await A.go("");
  await A.click(`${A.rowSel("frozen")} a[data-run]`);
  const fz = A.row("frozen").row, claimsText = await A.text(SEC("claims"));
  check(`T4 ${tag}: a run stopped by freezing marks the claims left out of scope Deferred (1 click)`, A.clicks === 1 && fz.claims.some((c) => !c.gating) && /Deferred/.test(claimsText) && /Required/.test(claimsText), claimsText);

  // T5 · attempts side by side · 1 click to the run, 1 to the previous attempt
  A.reset(); await A.go("");
  const latest = A.row("attempts").row;
  await A.click(`${A.rowSel("attempts")} a[data-run]`);
  const at = await A.text(SEC("attempts"));
  const cols = await E(`document.querySelectorAll(${JSON.stringify(`${SEC("attempts")} thead th`)}).length`);
  check(`T5 ${tag}: the attempts are named 1, 2, 3 and set side by side, the changed goal said (1 click)`, A.clicks === 1 && [1, 2, 3].every((n) => at.includes(`Attempt ${n}`)) && cols === 3 && /goal changed/.test(at), at);
  await A.click(`${SEC("attempts")} a[data-attempt]`);
  const prev = A.rows.find((x) => x.row.key === latest.previous).row;
  check(`T5 ${tag}: Previous attempt opens the one before (2 clicks)`, A.clicks === 2 && (await A.text("#workspace h1")).startsWith(prev.goal.slice(0, 30)) && (await E("location.search")).includes(encodeURIComponent(prev.key)), await E("location.search"));

  // T6 · find a remembered goal · typing, 0 clicks
  A.reset(); await A.go("");
  const total = await E(`document.querySelectorAll("ev-runs li[data-row]").length`);
  const budget = A.row("budget").row;
  await A.type("#search", "NIGHTLY import");
  const hits = await E(`[...document.querySelectorAll("ev-runs li[data-row]")].map((li) => li.dataset.row)`);
  const mentions = (x) => `${x.row.goal} ${x.project.label} ${(x.row.claims ?? []).map((c) => c.text).join(" ")}`.toLowerCase();
  check(`T6 ${tag}: typing part of a goal leaves that run, and only runs that mention it (0 clicks)`, hits.includes(budget.key) && hits.length < total && hits.every((k) => mentions(A.rows.find((x) => x.row.key === k)).includes("nightly") && mentions(A.rows.find((x) => x.row.key === k)).includes("import")) && A.clicks === 0, hits);
  const other = A.rows.find((x) => x.row.claims?.length && !x.row.goal.toLowerCase().includes(x.row.claims[0].text.toLowerCase().slice(0, 12)));
  await A.type("#search", other.row.claims[0].text);
  check(`T6 ${tag}: a claim's text finds its run`, (await E(`[...document.querySelectorAll("ev-runs li[data-row]")].map((li) => li.dataset.row)`)).includes(other.row.key), null);
  await A.type("#search", "zzzzqqqq");
  check(`T6 ${tag}: no match says so, and says how many runs could not be searched by claim text`, /No run matches/.test(await A.text("ev-runs")) && /Claim text of \d+ unread runs was not searched/.test(await A.text("#coverage")), await A.text("#coverage"));
  await A.type("#search", "");
  check(`T6 ${tag}: clearing the search brings every run back`, (await E(`document.querySelectorAll("ev-runs li[data-row]").length`)) === total, null);

  // T7 · same-named projects · 0 clicks
  A.reset(); await A.go("");
  const north = A.row("same-name-north"), south = A.row("same-name-south");
  const lines = await E(`(() => ({ n: document.querySelector('${A.rowSel("same-name-north")} a').innerText, s: document.querySelector('${A.rowSel("same-name-south")} a').innerText }))()`);
  check(`T7 ${tag}: two projects of one name are told apart by their labels on the list (0 clicks)`, north.project.label !== south.project.label && lines.n.includes(north.project.label) && lines.s.includes(south.project.label) && !lines.n.includes(south.project.label + " ·"), { north: north.project.label, south: south.project.label });
}

// ---- the standing rules, on the demo at 1440x900 -------------------------------------------------------------------------
async function standing(A) {
  const E = A.E, P = A.P;
  // keyboard and focus
  await A.go("");
  await E(`document.querySelector('${A.rowSel("verification")} a[data-run]').focus()`);
  await P.key("Enter"); await P.sleep(400);
  check("keyboard: Enter on a run opens it and focus moves to its title", (await E("location.search")).includes("run=") && (await E(`document.activeElement.hasAttribute("data-title")`)), await E("document.activeElement.outerHTML.slice(0,80)"));
  await P.key("Escape"); await P.sleep(300);
  check("keyboard: Esc leaves the run and focus returns to its row", !(await E("location.search")).includes("run=") && (await E(`document.activeElement.dataset.run`)) === S.verification, await E("document.activeElement.outerHTML.slice(0,80)"));
  await A.go(A.run("verification"));
  await E(`document.querySelector('summary[data-claim="C5"]').focus()`);
  await P.key("Enter"); await P.sleep(400);
  check("keyboard: Enter on a claim opens it in place and the URL names it", (await E("location.search")).includes("claim=C5") && (await E(`document.querySelector('summary[data-claim="C5"]').closest("details").open`)), await E("location.search"));
  await P.key("Escape"); await P.sleep(300);
  check("keyboard: Esc closes the claim and focus returns to it", !(await E("location.search")).includes("claim=") && (await E(`document.activeElement.dataset.claim`)) === "C5", await E("document.activeElement.outerHTML.slice(0,80)"));

  // URL, history, reload
  await A.go(A.run("verification"));
  await A.click('summary[data-claim="C1"]'); await A.click('summary[data-claim="C5"]');
  const open = () => E(`[...document.querySelectorAll('section[aria-labelledby=claims] details[open]')].map((d) => d.querySelector("summary").dataset.claim)`);
  check("history: one claim open at a time", JSON.stringify(await open()) === '["C5"]', await open());
  await E("history.back()"); await P.sleep(400);
  check("history: Back restores the previous claim", JSON.stringify(await open()) === '["C1"]', await open());
  await E("history.forward()"); await P.sleep(400);
  check("history: Forward reopens the next", JSON.stringify(await open()) === '["C5"]', await open());
  await P.send("Page.reload"); await P.sleep(300); await P.waitReady(); await P.sleep(300);
  check("history: a reload restores the run and the claim", JSON.stringify(await open()) === '["C5"]', await open());
  await A.go(`&run=nope%2Fnone%2F1&claim=C0`);
  check("history: a run that is not there is no selection", (await E(`document.querySelector("ev-run").textContent`)).includes("Select a run") && !(await E("location.search")).includes("run="), await E("location.search"));

  // layout: no horizontal overflow, with a claim open
  await A.go(A.run("verification", "&claim=C6"));
  check(`layout: no horizontal overflow with a claim open at ${A.size}`, await E(`document.documentElement.scrollWidth <= innerWidth && document.querySelector("#workspace").scrollWidth <= document.querySelector("#workspace").clientWidth`), await E("[document.documentElement.scrollWidth, innerWidth]"));
}

async function safeText(A) {
  const E = A.E, P = A.P;
  const view = structuredClone(A.view);
  const row = view.projects.flatMap((p) => p.runs).find((r) => r.key === S.verification);
  const bad = "<img src=x onerror=\"window.__pwned=1\"> & <script>window.__pwned=2</script>";
  row.goal = bad; row.claims[0].text = bad; view.projects.find((p) => p.id === row.project).label = bad;
  view.token = "sha256:" + "0".repeat(64);
  await P.send("Fetch.enable", { patterns: [{ urlPattern: "*/api/view*" }] });
  P.on("Fetch.requestPaused", async (p) => {
    await P.send("Fetch.fulfillRequest", { requestId: p.requestId, responseCode: 200, responseHeaders: [{ name: "Content-Type", value: "application/json" }, { name: "ETag", value: `"${view.token}"` }], body: Buffer.from(JSON.stringify(view)).toString("base64") });
  });
  await A.go(`&run=${encodeURIComponent(S.verification)}`);
  const r = await E(`({ pwned: window.__pwned ?? null, imgs: document.querySelectorAll("ev-runs img, ev-run img, #bar img").length, scripts: document.querySelectorAll("ev-runs script, ev-run script").length, text: document.querySelector("ev-runs").innerText.includes("<img src=x") && document.querySelector("#workspace h1").innerText.includes("<img src=x") })`);
  check("safe text: markup in a goal, a claim and a project label is shown as text, never run", r.pwned === null && r.imgs === 0 && r.scripts === 0 && r.text, r);
}

async function recovery(A) {
  const E = A.E, P = A.P;
  const rules = { run: 0, view: 0, runAll: false, viewAll: false };
  await P.send("Fetch.enable", { patterns: [{ urlPattern: "*/api/*" }] });
  P.on("Fetch.requestPaused", async (p) => {
    const kind = p.request.url.includes("/api/run") ? "run" : "view";
    if (rules[kind] > 0) { rules[kind]--; return P.send("Fetch.fulfillRequest", { requestId: p.requestId, responseCode: 500, body: Buffer.from("no").toString("base64") }); }
    if (rules[`${kind}All`]) return P.send("Fetch.fulfillRequest", { requestId: p.requestId, responseCode: 500, body: Buffer.from("no").toString("base64") });
    return P.send("Fetch.continueRequest", { requestId: p.requestId });
  });
  // a failed detail is said in the workspace, with Retry; the button asks again at once
  rules.runAll = true;
  await A.go(A.run("verification"));
  const failedAt = await A.text("#workspace");
  check("recovery: a detail that failed says so, keeps what the list knows, and offers Retry", /reported an error/.test(failedAt) && /Retry/.test(failedAt) && (await A.text("#workspace h1")).length > 0, failedAt.slice(0, 200));
  rules.runAll = false;
  await A.click("[data-retry]"); await P.sleep(400);
  const okAt = await A.text(SEC("now"));
  check("recovery: Retry brings the details in and the notice goes", (await E(`!document.querySelector("[data-retry]")`)) && /Waiting for a human to confirm/.test(okAt), okAt.slice(0, 200));
  // and it asks again by itself, later each time
  rules.run = 1;
  await A.go(A.run("converged"));
  const first = /reported an error/.test(await A.text("#workspace"));
  await P.sleep(2200);
  check("recovery: a failed detail is asked again by itself and recovers without a click", first && !(await E(`!!document.querySelector("[data-retry]")`)) && /This run has ended/.test(await A.text(SEC("now"))), await A.text("#workspace"));
  // a failed poll is shown beside the last good check; the list stays
  await A.go("");
  const before = await A.text("#status");
  rules.viewAll = true;
  await P.sleep(3800);
  const during = await A.text("#status");
  check("recovery: a failed poll says so beside the last good check, and the list stays", /reported an error/.test(during) && /Last checked/.test(during) && /Last checked/.test(before) && (await E(`document.querySelectorAll("ev-runs li[data-row]").length`)) > 0, [before, during]);
  rules.viewAll = false;
  await P.sleep(7500);
  check("recovery: when the observer answers again the notice goes", !/reported an error/.test(await A.text("#status")), await A.text("#status"));
}

async function hidden(A) {
  const E = A.E, P = A.P;
  await A.go("");
  const polls = () => E(`performance.getEntriesByType("resource").filter((r) => r.name.includes("/api/view")).length`);
  await E(`Object.defineProperty(document, "hidden", { get: () => true, configurable: true })`);
  await P.sleep(3600);
  const n = await polls();
  await P.sleep(3600);
  check("hidden tab: the poll is not asked while the tab is hidden", (await polls()) === n, [n, await polls()]);
  await E(`Object.defineProperty(document, "hidden", { get: () => false, configurable: true }); document.dispatchEvent(new Event("visibilitychange"))`);
  await P.sleep(900);
  check("hidden tab: showing it again asks at once", (await polls()) === n + 1, [n, await polls()]);
}

/** F8: nothing selected, the list has the width and there is no workspace; selected, a column beside the workspace. */
async function layout(A) {
  const E = A.E, w = (sel) => E(`document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect().width`), shown = (sel) => E(`document.querySelector(${JSON.stringify(sel)}).checkVisibility()`);
  await A.go("");
  const [list, inner] = [await w("#list"), await E(`document.querySelector("ev-runs").getBoundingClientRect().width`)];
  check(`layout ${A.size}: nothing selected, the list takes the width and no empty workspace is drawn`, !(await shown("#workspace")) && list >= (await E("innerWidth")) - 1 && inner > 400, { list, inner });
  await A.go(A.run("verification"));
  const [side, rest] = [await w("#list"), await w("#workspace")];
  check(`layout ${A.size}: a selected run puts the list in a column beside its workspace`, (await shown("#workspace")) && side < 450 && rest > side, { side, rest });
}

/** U4 H4: attempts side by side with long goals and residuals (a qualification run's are paragraphs, one with a word no space
 * breaks): no cell's content is painted outside its cell, so nothing overprints the next attempt. */
async function compare(A) {
  const E = A.E, P = A.P;
  const family = A.row("attempts").row.run;
  const long = `${"Rebuild the thumbnail service so that every image is served from the content delivery network and keep the cache warm. ".repeat(3)}${"x".repeat(90)}`;
  await P.send("Page.addScriptToEvaluateOnNewDocument", { source: `(() => { const real = window.fetch; window.fetch = async (...a) => {
    const r = await real(...a), url = String(a[0]?.url ?? a[0]);
    if (r.status !== 200 || !/\\/api\\/(view|run)/.test(url)) return r;
    const j = await r.clone().json();
    for (const p of j.projects ?? []) for (const x of p.runs) if (x.run === ${JSON.stringify(family)}) x.goal = ${JSON.stringify(long)};
    if (j.key && j.key.includes(${JSON.stringify(family)})) { j.goal = ${JSON.stringify(long)}; if (j.residuals) j.residuals = [{ code: "audit.required", claims: [], message: ${JSON.stringify(long)} }]; }
    return new Response(JSON.stringify(j), { status: 200, headers: r.headers });
  }; })()` });
  await A.go(A.run("attempts"));
  await P.sleep(900);
  const painted = await E(`[...document.querySelectorAll(".ev-compare td")].filter((td) => td.textContent.trim() !== "").map((td) => { const cell = td.getBoundingClientRect(), text = document.createRange(); text.selectNodeContents(td); const ink = text.getBoundingClientRect(); return { width: Math.round(cell.width), over: Math.round(Math.max(0, ink.right - cell.right, cell.left - ink.left)), wrapped: getComputedStyle(td).whiteSpace } })`);
  check(`compare ${A.size}: long goals and residuals wrap inside their cells; no attempt's text is painted through the next`, painted.length >= 6 && painted.every((c) => c.over <= 1 && c.wrapped === "normal") && (await E(`document.documentElement.scrollWidth <= innerWidth`)), painted.filter((c) => c.over > 1 || c.wrapped !== "normal"));
}

/** U5 V1/V4: a host that has not looked for a dialog is conditional, not a question a human is owed; its session context is a closed disclosure. */
async function unknown(A) {
  const E = A.E;
  await A.go(A.run("unobserved-surface"));
  const now = await A.text(SEC("now"));
  check(`unobserved ${A.size}: the workspace says a human is asked only if the host finds a dialog`, /if the host finds a dialog/.test(now), now.slice(0, 300));
  const group = await E(`document.querySelector(${JSON.stringify(A.rowSel("unobserved-surface"))})?.closest("section")?.getAttribute("aria-labelledby") ?? null`);
  check(`unobserved ${A.size}: the list does not put it among the runs that need a human`, group === "group-not_ended", group);
  check(`unobserved ${A.size}: what the host recorded of the session is a closed disclosure that says it did not say`, await E(`(() => { const d = document.querySelector("[data-session-context]")?.closest("details"); return !!d && !d.open && d.textContent.includes("did not say") })()`), null);
}

/** F6: a run the observer could not read opens without an exception, says "Not read", and sits in a family beside readable attempts. */
async function unread(A) {
  const E = A.E, P = A.P;
  await A.go(A.run("unreadable"));
  const text = await A.text("#workspace");
  check("unreadable: opening a run the observer could not read shows what is known and says so, without an exception", P.errors.length === 0 && /not read in full/i.test(text) && (await A.text("#workspace h1")).length > 0, [P.errors, text.slice(0, 200)]);
  await A.go(A.run("attempt-unread-sibling"));
  const compare = await A.text(SEC("attempts"));
  check("unreadable: the attempts table says 'Not read' for the attempt it could not read, never 'None' or 'Loading'", /Not read/.test(compare) && !/Loading/.test(compare) && !/None recorded/.test(compare) && !/goal changed/.test(compare), compare);
  const other = await E(`[...document.querySelectorAll('${SEC("attempts")} a[data-attempt]')].map((a) => a.getAttribute("href"))`);
  await E(`document.querySelector('${SEC("attempts")} a[data-attempt]').click()`); await P.sleep(500);
  check("unreadable: opening the unreadable attempt from its sibling throws nothing and still lists the readable one", other.length === 1 && P.errors.length === 0 && /Attempt 1/.test(await A.text(SEC("attempts"))), [P.errors, other]);
}

/** F7: a sibling attempt's document is a request like any other: a failure is shown, asked again, and rests while the tab is hidden. */
async function siblings(A) {
  const E = A.E, P = A.P;
  const mine = S.attempts;
  const family = A.rows.filter((x) => x.row.run === A.row("attempts").row.run).map((x) => x.row.key).filter((k) => k !== mine);
  const rules = { fail: false };
  await P.send("Fetch.enable", { patterns: [{ urlPattern: "*/api/run*" }] });
  P.on("Fetch.requestPaused", async (p) => {
    const other = family.some((k) => decodeURIComponent(p.request.url).includes(k));
    if (rules.fail && other) return P.send("Fetch.fulfillRequest", { requestId: p.requestId, responseCode: 500, body: Buffer.from("no").toString("base64") });
    return P.send("Fetch.continueRequest", { requestId: p.requestId });
  });
  const asks = () => E(`performance.getEntriesByType("resource").filter((r) => r.name.includes("/api/run")).length`);
  rules.fail = true;
  await A.go(A.run("attempts"));
  await P.sleep(1500);
  const text = await A.text(SEC("attempts"));
  check("siblings: an attempt's document that failed is said so in the comparison, with Retry, while the selected run is fine", /Could not be loaded/.test(text) && !/Loading/.test(text) && (await E(`!!document.querySelector('[data-retry="family"]')`)) && !(await E(`!!document.querySelector('[role=alert] [data-retry]')`)), text);
  await E(`Object.defineProperty(document, "hidden", { get: () => true, configurable: true })`);
  await P.sleep(300);
  const n = await asks();
  await P.sleep(6000);
  check("siblings: while the tab is hidden no sibling is asked again", (await asks()) === n, [n, await asks()]);
  rules.fail = false;
  await E(`Object.defineProperty(document, "hidden", { get: () => false, configurable: true }); document.dispatchEvent(new Event("visibilitychange"))`);
  await P.sleep(1500);
  const after = await A.text(SEC("attempts"));
  check("siblings: showing the tab again asks what came due, and the comparison fills in", !/Could not be loaded/.test(after) && !/Loading/.test(after), after);
  rules.fail = true;
  await A.go(A.run("attempts"));
  await P.sleep(300);
  rules.fail = false;
  await A.click('[data-retry="family"]'); await P.sleep(600);
  check("siblings: Retry asks at once and the comparison fills in", !/Could not be loaded/.test(await A.text(SEC("attempts"))), await A.text(SEC("attempts")));
}

async function stacked(src) {
  await withPage(`${src.base}/app/?data=${src.data}`, "800x700", async (P) => {
    const E = P.evalJs;
    await P.waitReady(); await P.sleep(300);
    const shown = (sel) => E(`document.querySelector(${JSON.stringify(sel)}).checkVisibility()`);
    check("stacked: below the list breakpoint the list shows alone", (await shown("#list")) && !(await shown("#workspace")), null);
    await E(`document.querySelector('ev-runs a[data-run="${S.verification}"]').click()`); await P.sleep(400);
    check("stacked: a selected run replaces the list and offers Back to all runs", !(await shown("#list")) && (await shown("#workspace")) && (await shown("[data-back]")), null);
    await E(`document.querySelector("[data-back]").click()`); await P.sleep(400);
    check("stacked: Back returns to the list with focus on the run's row", (await shown("#list")) && (await E(`document.activeElement.dataset.run`)) === S.verification, await E("document.activeElement.outerHTML.slice(0,80)"));
  });
}

async function empty() {
  await withPage(`${EMPTY}/app/`, "1440x900", async (P) => {
    await P.waitReady(); await P.sleep(300);
    check("empty store: the list says there are no runs yet, and the page has no error", /no runs yet/.test(await P.evalJs(`document.querySelector("ev-runs").innerText`)) && P.errors.length === 0, P.errors);
  });
}

try {
  for (const src of SOURCES) for (const size of SIZES) await session(src, size, tasks);
  await session(SOURCES[0], "1440x900", standing);
  await session(SOURCES[0], "1024x768", async (A) => { await A.go(A.run("verification", "&claim=C6")); check("layout: no horizontal overflow with a claim open at 1024x768", await A.E(`document.documentElement.scrollWidth <= innerWidth`), null); });
  await session(SOURCES[0], "1440x900", safeText);
  await session(SOURCES[0], "1440x900", recovery);
  await session(SOURCES[0], "1440x900", hidden);
  for (const size of SIZES) await session(SOURCES[0], size, layout);
  for (const size of SIZES) await session(SOURCES[0], size, compare);
  for (const size of SIZES) await session(SOURCES[0], size, unknown);
  await session(SOURCES[0], "1440x900", unread);
  await session(SOURCES[0], "1024x768", unread);
  await session(SOURCES[0], "1440x900", siblings);
  await stacked(SOURCES[0]);
  await empty();
} finally {
  for (const p of children) p.kill();
  fs.rmSync(home, { recursive: true, force: true });
}
console.log(`${total - failed} of ${total} checks passed`);
process.exit(failed ? 1 : 0);
