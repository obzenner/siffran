// The navigation graph of the UI (VIEW.md "Navigation"), walked end to end: every edge of catalogue.json `route.edges`
// is taken in a fresh page, by the action it names, and after it the same things must hold wherever it leads:
//   • the URL names exactly the destination (and keeps the data source), and the panel and the view agree with it;
//   • a user's move adds exactly one history entry; Back returns to where it came from, Forward comes back;
//   • a reload restores the destination;
//   • the panel always has a way out: the close button, and above a run a breadcrumb link to the level above.
// Then negative controls: each assertion is run against a page broken in exactly the way it guards, and must fail.
import catalogue from "../catalogue.json" with { type: "json" };
import states from "../states.json" with { type: "json" };

import { PARAMS, markedOf, parse, write } from "../app/route.js";

/** The contexts the edges run in, found in the demo data (not named: whichever runs have the needed facts). */
function contexts(fx, details) {
  const runs = fx.projects.flatMap((p) => p.runs);
  const byKey = new Map(runs.map((r) => [r.key, r]));
  const rich = runs.find((r) => details[r.key].stale.length && details[r.key].steps.some((s) => s.claim));
  const d = details[rich.key], step = d.steps.find((s) => s.claim && rich.claims.some((c) => c.id === s.claim));
  // The first step the history lists as a row (bookkeeping steps fold into one quiet line).
  const row = d.steps.find((s) => !states.step[s.kind].quiet);
  const base = { project: rich.project, run: rich.key, claim: rich.claims[0].id, step: row.n, file: d.stale[0].path };
  const residual = runs.find((r) => details[r.key].residuals.some((x) => x.claim && r.claims.some((c) => c.id === x.claim)));
  const blocked = runs.flatMap((r) => r.claims.filter((c) => c.blocker_claim && c.blocker_claim !== c.id).map((c) => [r, c]))[0];
  return {
    base,
    stepClaim: { ...base, step: step.n, target: step.claim },
    residual: { ...base, project: residual.project, run: residual.key, claim: rich.claims[0].id, target: details[residual.key].residuals.find((x) => x.claim).claim },
    blocked: { ...base, project: blocked[0].project, run: blocked[0].key, claim: blocked[1].id, target: blocked[1].blocker_claim },
    byKey,
  };
}
/** The selection a level names in a context, and its query: both written by the app's own route.js, not a copy of it. */
const selOf = (level, c) => ({ kind: level, project: c.project, run: c.run, claim: c.claim, step: c.step, file: c.file });
const queryOf = (level, c) => { const q = new URLSearchParams(); write(q, selOf(level, c)); return q.toString(); };

/** In-page predicates, as source: one definition for the walk and for its controls. `want` is {level, c}. */
const PRED = {
  // The URL names exactly the destination.
  url: (want) => `(() => { const q = new URLSearchParams(location.search), w = new URLSearchParams(${JSON.stringify(queryOf(want.level, want.c))});
    const names = ${JSON.stringify(Object.values(PARAMS))};
    return q.get("data") === "demo" && q.get("f.project") === ${JSON.stringify(want.c.project)} && names.every((n) => q.get(n) === w.get(n)); })()`,
  // The panel says where it is; the view marks the same thing.
  panel: (want) => `(() => { const d = document.querySelector("#split ev-detail"), sel = document.querySelector("ev-view [data-selected]");
    const level = ${JSON.stringify(want.level)}, c = ${JSON.stringify(want.c)};
    if (level === "none") return !!d.querySelector("[data-empty]") && !sel;
    const here = d.querySelector('nav [aria-current="page"]');
    // A crumb is named by a human fact, never the generic level (U3): a run by its state and progress, a step by its
    // number and kind, a file by its name.
    const text = here?.textContent ?? "";
    const named = { run: /^[^·]+(?: · [^·]+)? · \\d+ of \\d+$/.test(text), claim: text === "Claim", step: text.startsWith("Step " + c.step + " · ") || text === "Step " + c.step,
      file: text === String(c.file).split("/").pop() }[level];
    const panelOk = level === "project" ? !d.querySelector("nav") && !!d.querySelector("[data-panel-title]") : named;
    const d2 = sel?.dataset ?? {};
    const markOk = level === "project" ? d2.project === c.project && !d2.run : d2.run === c.run && (level === "claim" ? d2.claim === c.claim : !d2.claim);
    return panelOk && markOk; })()`,
  // A way out: close, and a keyboard-reachable link to the level above a run.
  exits: (want) => `(() => { const d = document.querySelector("#split ev-detail");
    if (${JSON.stringify(want.level)} === "none") return true;
    if (!d.querySelector("[data-close]")) return false;
    if (${JSON.stringify(want.level)} === "project") return true;
    const up = ${JSON.stringify(catalogue.route.parent[want.level])};
    return [...d.querySelectorAll("nav a[href]")].some((a) => { const q = new URLSearchParams(a.getAttribute("href")); if (a.tabIndex < 0) return false; a.focus(); if (document.activeElement !== a) return false; return (up === "project" ? q.has("project") && !q.has("run") : q.has("run") && !["claim", "step", "file"].some((k) => q.has(k))); }); })()`,
};
const ALL = ["url", "panel", "exits"];

/** Where focus must be after leaving `left` (a level in a context): the mark of the nearest level above it that has one
 * (a step or a file has none: its run does), matched by data attributes; or, with the picture hidden behind the panel
 * (`stays`), the panel's title. */
const FOCUS = (left, byKey, stays = false) => {
  const m = markedOf(selOf(left.level, left.c), byKey);
  return `(() => { const a = document.activeElement, d = a?.dataset ?? {};
    if (${JSON.stringify(stays)}) return !!a?.closest("#split ev-detail") && a.hasAttribute("data-panel-title");
    const m = ${JSON.stringify(m)};
    return !!m && a?.hasAttribute("data-mark") && d.project === (m.kind === "project" ? m.project : d.project) && (m.kind === "project" ? !d.run : d.run === m.run) && (m.kind === "claim" ? d.claim === m.claim : m.kind === "run" ? !d.claim : true); })()`;
};

export async function walk({ B, fx, details, withPage, check, same }) {
  const c = contexts(fx, details);
  const APP = (q) => `${B}/app/?data=demo${q ? `&${q}` : ""}`;
  // Every edge runs with a filter in force, so Back, Forward and reload must carry it too.
  const FILTER = (x) => `f.project=${encodeURIComponent(x.project)}`;
  const holds = async (E, want) => { for (const name of ALL) if (!(await E(PRED[name](want)))) return name; return null; };

  // --- how each action is taken -------------------------------------------------------------------------------
  const markSel = (level, x) => ({
    project: `ev-view [data-mark][data-project="${x.project}"]`, run: `ev-view [data-mark][data-run="${x.run}"]:not([data-claim])`,
    claim: `ev-view [data-mark][data-run="${x.run}"][data-claim="${x.claim}"]`,
  }[level]);
  const click = (sel) => `(() => { const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return false; el.dispatchEvent(new MouseEvent("click", { bubbles: true })); return true; })()`;
  // Keyboard activation: focus the control (it must be focusable) and press Enter.
  const focusOf = (sel) => `(() => { const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return false; el.focus(); return document.activeElement === el; })()`;
  // The breadcrumb link to a level, found by what it names (a project crumb names the project alone).
  const crumbSel = (to) => to === "project" ? "#split ev-detail nav a[href*='project='][href]:not([href*='run='])" : "#split ev-detail nav a[href*='run=']:not([href*='claim=']):not([href*='step=']):not([href*='file='])";
  const ACT = {
    mark: (e, x) => markSel(e.to, x),
    "link-run": (e, x) => `#split ev-detail :is(dl, section) a[href*='run=${encodeURIComponent(x.run)}']:not([href*='claim=']):not([href*='step=']):not([href*='file='])`,
    "link-blocker": () => "#split ev-detail :is(dl, section) a[href*='claim=']",
    "link-claim": () => "#split ev-detail :is(dl, section) a[href*='claim=']",
    "row-step": (e, x) => `#split ev-history button[data-step="${x.step}"]`,
    "row-file": () => "#split ev-detail ev-file button",
    "crumb-up": (e) => crumbSel(e.to),
  };
  // Where each edge starts and ends. A level's query comes from its context; links lead to the context's `target`.
  function plan(e) {
    let from = c.base, to = c.base;
    if (e.action === "link-blocker") { from = c.blocked; to = { ...c.blocked, claim: c.blocked.target }; }
    else if (e.action === "link-claim" && e.from === "run") { from = c.residual; to = { ...c.residual, claim: c.residual.target }; }
    else if (e.action === "link-claim" && e.from === "step") { from = c.stepClaim; to = { ...c.stepClaim, claim: c.stepClaim.target }; }
    else if (e.action === "link-run" || (e.action === "mark" && e.from === "claim")) { to = c.base; }
    return { from, to };
  }

  let edges = 0;
  for (const e of catalogue.route.edges) {
    const { from, to } = plan(e);
    const name = `route: ${e.from} —${e.action}→ ${e.to}`;
    await withPage(APP(`${queryOf(e.from, from)}${queryOf(e.from, from) ? "&" : ""}${FILTER(from)}`), "1500x900", async (P) => {
      const E = P.evalJs;
      await P.waitReady(); await P.sleep(300);
      const start = { level: e.from, c: from }, end = { level: e.to, c: to };
      const problems = [];
      const b0 = await holds(E, start); if (b0) problems.push(`start: ${b0}`);
      const len0 = await E("history.length"), startSearch = await E("location.search");
      let ok = true;
      if (e.action === "esc") { await E(`document.querySelector("#split ev-detail [data-panel-title]").focus()`); await P.key("Escape"); }
      else {
        const sel = e.action === "close" ? "#split ev-detail [data-close]" : ACT[e.action](e, e.action === "mark" ? to : from);
        ok = await E(focusOf(sel));
        if (ok) await P.key("Enter");
      }
      await P.sleep(400);
      if (!ok) problems.push("the control to take was not there");
      // Leaving a layer by Esc or Close hands focus back to the mark just left (a step or a file: its run's mark). Read
      // before the arrival predicates, which move focus themselves (they tab through the breadcrumb).
      if (e.action === "close" || e.action === "esc") {
        const focused = await E(FOCUS(start, c.byKey)); if (!focused) problems.push(`focus after ${e.action}: ${await E("JSON.stringify(document.activeElement?.tagName + ' ' + JSON.stringify(document.activeElement?.dataset))")}`);
      }
      const b1 = await holds(E, end); if (b1) problems.push(`arrival: ${b1}`);
      const endSearch = await E("location.search");
      const delta = (await E("history.length")) - len0;
      if (delta !== 1) problems.push(`history entries added: ${delta}`);
      // Back, Forward
      await E("history.back()"); await P.sleep(350);
      const back = await holds(E, start); if (back || (await E("location.search")) !== startSearch) problems.push(`back: ${back ?? "url"}`);
      await E("history.forward()"); await P.sleep(350);
      const fwd = await holds(E, end); if (fwd || (await E("location.search")) !== endSearch) problems.push(`forward: ${fwd ?? "url"}`);
      // Reload restores the destination
      await P.send("Page.reload"); await P.sleep(400); await P.waitReady(); await P.sleep(300);
      const rel = await holds(E, end); if (rel || (await E("location.search")) !== endSearch) problems.push(`reload: ${rel ?? "url"}`);
      check(name, problems.length === 0, problems);
      edges++;
    });
  }
  check(`route: all ${catalogue.route.edges.length} declared edges walked`, edges === catalogue.route.edges.length);
  // Every declared level has an edge in and an edge out, so no level is a dead end by declaration.
  const levels = catalogue.route.levels;
  check("route: every level is entered and left by a declared edge", levels.every((l) => catalogue.route.edges.some((e) => e.to === l) && catalogue.route.edges.some((e) => e.from === l)));
  check("route: every level above a run has a declared way up (crumb, Esc, close)", levels.every((l) => ["crumb-up", "esc", "close"].every((a) => catalogue.route.edges.some((e) => e.from === l && e.action === a) || (a === "crumb-up" && l === "project"))));

  // --- narrow: Esc and Close from every level, focus (the picture is under the panel until the last layer closes) ---
  for (const e of catalogue.route.edges.filter((x) => x.action === "close" || x.action === "esc")) {
    const from = c.base;
    await withPage(APP(`${queryOf(e.from, from)}&${FILTER(from)}`), "900x800", async (P) => {
      const E = P.evalJs; await P.waitReady(); await P.sleep(300);
      if (e.action === "esc") { await E(`document.querySelector("#split ev-detail [data-panel-title]").focus()`); await P.key("Escape"); }
      else { await E(`document.querySelector("#split ev-detail [data-close]").focus()`); await P.key("Enter"); }
      const stays = e.to !== "none";
      let ok = false;
      for (let i = 0; i < 25 && !ok; i++) { await P.sleep(100); ok = await E(FOCUS({ level: e.from, c: from }, c.byKey, stays)); }
      check(`route: narrow, ${e.action} from ${e.from}: focus ${stays ? "stays in the panel, on its title" : "returns to the mark just left"}`, ok,
        await E("JSON.stringify(document.activeElement?.tagName + ' ' + JSON.stringify(document.activeElement?.dataset))"));
    });
  }
  // control: focus lost to the page after Close (the old behaviour for a step or a file) is caught by the same predicate
  await withPage(APP(`${queryOf("step", c.base)}&${FILTER(c.base)}`), "1500x900", async (P) => {
    const E = P.evalJs; await P.waitReady(); await P.sleep(300);
    await E(`document.querySelector("#split ev-detail [data-close]").focus()`); await P.key("Enter"); await P.sleep(400);
    const good = await E(FOCUS({ level: "step", c: c.base }, c.byKey));
    await E("document.activeElement.blur()");
    check("route control: focus on the body after closing a step is caught", good === true && (await E(FOCUS({ level: "step", c: c.base }, c.byKey))) === false);
  });

  // --- the controls are found, not listed: crawl every control in the panel and hold it to the declared edges --------
  await discover({ B, fx, details, withPage, check, c, APP, FILTER, click });

  // --- relationships are drawn, never navigable (Q3b-9) -----------------------------------------------------------
  const LINES = {
    inert: `(() => { const lines = [...document.querySelectorAll("ev-view path[data-supports], ev-view path[data-attempt]")];
      return lines.length > 0 && lines.every((l) => getComputedStyle(l).pointerEvents === "none" && !l.hasAttribute("tabindex") && !l.hasAttribute("role") && l.closest('[aria-hidden="true"]')); })()`,
    // Each next-attempt line, sampled along its length, stays out of every mark (the two runs' own diamonds included,
    // its ends aside) and never lies over something that would answer a click.
    clear: `(() => { const V = document.querySelector("ev-view"), svg = V.querySelector("svg"), m = svg.getScreenCTM();
      const shapes = [...V.querySelectorAll("[data-mark]")].flatMap((g) => [...g.querySelectorAll("path, circle, text")].filter((s) => s.getBoundingClientRect().width).map((s) => ({ g, r: s.getBoundingClientRect() })));
      const lines = [...V.querySelectorAll("path[data-attempt]")];
      return lines.length > 0 && lines.every((l) => { const n = l.getTotalLength();
        for (let i = 2; i <= 38; i++) { const p = l.getPointAtLength(n * i / 40), x = m.a * p.x + m.e, y = m.d * p.y + m.f;
          if (shapes.some(({ r }) => x > r.left && x < r.right && y > r.top && y < r.bottom)) return false;
          const hit = document.elementFromPoint(x, y); if (hit && !hit.hasAttribute("data-hit") && hit.closest("[data-mark]")) return false; }
        return true; }); })()`,
  };
  await withPage(APP(""), "1500x900", async (P) => {
    const E = P.evalJs; await P.waitReady(); await P.sleep(400);
    check("route: support and next-attempt lines are inert: no pointer, no focus, no role", await E(LINES.inert));
    check("route: next-attempt lines cross no mark and no label", await E(LINES.clear));
  });
  await withPage(APP(""), "1500x900", async (P) => {
    const E = P.evalJs; await P.waitReady(); await P.sleep(400);
    await E(`document.querySelector("ev-view path[data-attempt]").style.pointerEvents = "auto"`);
    const inert = await E(LINES.inert);
    await E(`(() => { const l = document.querySelector("ev-view path[data-attempt]"), c = document.querySelector("ev-view [data-mark][data-claim] circle:not(.hidden)").getBBox();
      l.setAttribute("d", "M" + (c.x + c.width / 2 - 20) + "," + (c.y + c.height / 2) + " L" + (c.x + c.width / 2 + 20) + "," + (c.y + c.height / 2)); })()`);
    check("route control: a line that takes pointer events, and one that runs through a mark, are both caught", inert === false && (await E(LINES.clear)) === false);
  });

  // --- a move that changes nothing adds nothing; a poll never pushes ---------------------------------------------
  await withPage(APP(""), "1500x900", async (P) => {
    const E = P.evalJs; await P.waitReady(); await P.sleep(300);
    await E(click(markSel("run", c.base))); await P.sleep(200);
    const n = await E("history.length");
    await E(click(markSel("run", c.base))); await P.sleep(200);
    check("route: selecting what is already selected adds no history entry", (await E("history.length")) === n);
    await P.sleep(3600);
    check("route: a poll adds no history entry", (await E("history.length")) === n);
  });

  // --- links keep the filters ------------------------------------------------------------------------------------
  await withPage(APP(`run=${encodeURIComponent(c.residual.run)}&f.project=${encodeURIComponent(c.residual.project)}`), "1500x900", async (P) => {
    const E = P.evalJs; await P.waitReady(); await P.sleep(300);
    const hrefs = await E(`[...document.querySelectorAll("#split ev-detail a[href]")].map((a) => a.getAttribute("href"))`);
    check("route: every link in the panel keeps the filters in force", hrefs.length >= 2 && hrefs.every((h) => new URLSearchParams(h).get("f.project") === c.residual.project), hrefs);
    await E(click("#split ev-detail section a[href*='claim=']")); await P.sleep(300);
    check("route: following a link keeps the filters in the URL and the view", await E(`new URLSearchParams(location.search).get("f.project") === ${JSON.stringify(c.residual.project)} && document.querySelectorAll("ev-view [data-mark][data-project]").length === 1`));
  });

  // --- narrow: leaving step by step keeps the picture, and the last step puts focus on the mark ------------------
  const total = fx.projects.length + fx.projects.flatMap((p) => p.runs).length + fx.projects.flatMap((p) => p.runs.flatMap((r) => r.claims)).length;
  for (const [how, query] of [["restored from the URL", queryOf("run", c.base)], ["opened by a click", ""]]) {
    await withPage(APP(query), "900x800", async (P) => {
      const E = P.evalJs; await P.waitReady(); await P.sleep(300);
      if (!query) { await E(click(markSel("run", c.base))); await P.sleep(300); }
      check(`route: narrow, a selection ${how} opens the panel over the view`, await E(`document.getElementById("split").hasAttribute("open") && document.getElementById("split").hasAttribute("narrow")`));
      await E(`document.querySelector("#split ev-detail [data-panel-title]").focus()`); await P.key("Escape"); await P.sleep(300);
      check(`route: narrow (${how}), Esc goes up to the project and the panel stays`, await E(`new URLSearchParams(location.search).has("project") && !new URLSearchParams(location.search).has("run") && document.getElementById("split").hasAttribute("open")`));
      await E(`document.querySelector("#split ev-detail [data-panel-title]").focus()`); await P.key("Escape"); await P.sleep(300);
      // Focus follows once the view has its width back (a few frames).
      for (let i = 0; i < 20 && !(await E(`document.activeElement?.dataset?.project === ${JSON.stringify(c.base.project)}`)); i++) await P.sleep(100);
      check(`route: narrow (${how}), the last Esc closes the panel, the whole picture is drawn, and focus is on the mark just left`,
        await E(`!document.getElementById("split").hasAttribute("open") && document.querySelectorAll("ev-view [data-mark]").length === ${total} && document.activeElement?.dataset?.project === ${JSON.stringify(c.base.project)}`), await E(`[document.getElementById("split").hasAttribute("open"), document.querySelectorAll("ev-view [data-mark]").length, JSON.stringify(document.activeElement?.dataset)]`));
    });
  }

  // --- one Esc, one layer ------------------------------------------------------------------------------------------
  await withPage(APP(queryOf("claim", c.base)), "1500x900", async (P) => {
    const E = P.evalJs; await P.waitReady(); await P.sleep(300);
    await E(`document.querySelector(${JSON.stringify(markSel("claim", c.base))}).focus()`); await P.sleep(200);
    const hint = await E(`document.querySelector("ev-hint").matches(":popover-open")`);
    await P.key("Escape"); await P.sleep(200);
    check("route: with a hint over a selection, the first Esc closes the hint only", hint && !(await E(`document.querySelector("ev-hint").matches(":popover-open")`)) && (await E("location.search")).includes("claim="));
    await E(`document.querySelector("#split ev-detail [data-close]").focus()`);
    await P.key("Escape"); await P.sleep(200);
    check("route: an Esc from a control inside the panel still leaves one level", !(await E("location.search")).includes("claim=") && (await E("location.search")).includes("run="));
    await E(`document.querySelector("ev-filters select")?.focus()`);
    await P.key("Escape"); await P.sleep(200);
    check("route: an Esc made in a select is left to the select", (await E("location.search")).includes("run="));
  });

  // --- negative controls: each assertion fails against the page broken the way it guards --------------------------
  const controls = [
    ["the URL names a level that is not selected", { level: "step", c: c.base }, `history.replaceState(null, "", location.search + "&claim=${encodeURIComponent(c.base.claim)}")`, "url"],
    ["the panel loses its breadcrumb", { level: "step", c: c.base }, `document.querySelector("#split ev-detail nav").remove()`, "panel"],
    ["the panel loses its link up", { level: "step", c: c.base }, `document.querySelectorAll("#split ev-detail nav a").forEach((a) => a.tabIndex = -1)`, "exits"],
    ["the panel loses its close button", { level: "run", c: c.base }, `document.querySelector("#split ev-detail [data-close]").remove()`, "exits"],
    ["the view marks another level than the panel shows", { level: "run", c: c.base }, `document.querySelector("ev-view").setAttribute("claim", ${JSON.stringify(c.base.claim)})`, "panel"],
    ["the view marks nothing", { level: "run", c: c.base }, `document.querySelector("ev-view").removeAttribute("run"); document.querySelector("ev-view").removeAttribute("project")`, "panel"],
  ];
  for (const [what, want, mutate, pred] of controls) {
    await withPage(APP(`${queryOf(want.level, want.c)}&${FILTER(want.c)}`), "1500x900", async (P) => {
      const E = P.evalJs; await P.waitReady(); await P.sleep(300);
      const before = await E(PRED[pred](want));
      await E(mutate); await P.sleep(150);
      check(`route control: ${what} is caught by "${pred}"`, before === true && (await E(PRED[pred](want))) === false);
    });
  }
}

// ---- discovery (R3 m1) --------------------------------------------------------------------------------------------
// The edge table above is what the app is held to; this is what keeps the table honest. In each of a set of selections
// (every level, with the links the demo has) it takes EVERY focusable thing the panel shows and every mark in the view,
// classifies it by what it is (not by a name from the table), takes it, reads where the URL went, and requires the move
// (from level, kind of control, to level) to be a declared edge. A control the table has no word for fails, and so does
// a move the table does not hold. Disclosures (a native <details> toggle) move nothing and are declared `inert`.
const KINDS = { mark: "mark", link: "link", row: "row", crumb: "crumb", close: "close" };
const declared = (edges) => new Set(edges.map((e) => `${e.from}|${e.action.split("-")[0].replace("crumb", "crumb")}|${e.to}`));
/** In-page: the panel's controls, each with the kind it is classified as (null: not understood). */
const CONTROLS = `(() => [...document.querySelectorAll("#split ev-detail :is(a[href], button, summary, select, input, textarea, [tabindex]:not([tabindex='-1']))")]
  .filter((el) => el.checkVisibility() && el.tabIndex >= 0 || el.tagName === "SUMMARY")
  .map((el, i) => ({ i, kind: el.matches("summary, [data-more]") ? "disclosure" : el.matches("[data-hint]") ? "hint" : el.matches("[data-close]") ? "close" : el.matches("nav a") ? "crumb"
    : el.matches('a[href^="?"]') ? "link" : el.matches("ev-history button, ev-steps button, ev-file button") ? "row" : null,
    text: (el.textContent || el.getAttribute("aria-label") || el.tagName).trim().slice(0, 40) })))()`;

export async function discover({ fx, withPage, check, c, APP, FILTER, click }) {
  const decl = declared(catalogue.route.edges);
  const inert = new Set(catalogue.route.inert);
  const problems = [], seen = new Set();
  const start = [["none", c.base], ["project", c.base], ["run", c.base], ["claim", c.base], ["step", c.base], ["file", c.base], ["run", c.residual], ["claim", c.blocked]];
  const levelOfSearch = (s) => parse(new URLSearchParams(s)).kind;
  async function crawl(level, x, seed = null) {
    await withPage(APP(`${queryOf(level, x)}&${FILTER(x)}`), "1500x900", async (P) => {
      const E = P.evalJs; await P.waitReady(); await P.sleep(300);
      if (seed) await E(seed);
      const home = await E("location.search");
      const take = async (what, pick, drawn = null) => {
        await E(pick); await P.sleep(220);
        const after = levelOfSearch(await E("location.search"));
        if (after !== level || (await E("location.search")) !== home) {
          // A mark takes you to the thing it draws, from wherever you are: declared from "none" for each level it can draw.
          const key = what === "mark" ? `none|mark|${after}` : `${level}|${what}|${after}`; seen.add(`${level}|${what}|${after}`);
          if (!decl.has(key)) problems.push(`${level} —${what}→ ${after} is not declared`);
          if (what === "mark" && after !== drawn) problems.push(`a ${drawn} mark led to ${after}`);
          await E("history.back()"); await P.sleep(260);
        }
      };
      const list = await E(CONTROLS);
      for (const k of list) {
        if (k.kind === null) { problems.push(`${level}: a control the table has no word for: "${k.text}"`); continue; }
        if (k.kind === "disclosure" || k.kind === "hint") { if (!inert.has(k.kind)) problems.push(`${level}: undeclared ${k.kind}`); continue; }
        const pick = `(() => { const el = [...document.querySelectorAll("#split ev-detail :is(a[href], button, summary, select, input, textarea, [tabindex]:not([tabindex='-1']))")].filter((el) => el.checkVisibility() && el.tabIndex >= 0 || el.tagName === "SUMMARY")[${k.i}]; el?.click(); })()`;
        await take(k.kind, pick);
      }
      // every mark in the view, from this selection
      const drawn = await E(`[...document.querySelectorAll("ev-view [data-mark]")].map((m) => m.hasAttribute("data-claim") ? "claim" : m.hasAttribute("data-run") ? "run" : "project")`);
      for (let i = 0; i < drawn.length; i++) {
        await take("mark", `document.querySelectorAll("ev-view [data-mark]")[${i}].dispatchEvent(new MouseEvent("click", { bubbles: true }))`, drawn[i]);
      }
    });
  }
  for (const [level, x] of start) await crawl(level, x);
  check("route: every control in the panel and every mark in the view makes a move the table declares", problems.length === 0, problems.slice(0, 6));
  check("route: the crawl took every kind of control the table names", ["mark", "link", "row", "crumb", "close"].every((k) => [...seen].some((s) => s.split("|")[1] === k)), [...seen].length);
  // control: a panel control the table does not know (a button that jumps to a project from a claim), and an unclassified one
  const bad = [];
  await withPage(APP(`${queryOf("claim", c.base)}&${FILTER(c.base)}`), "1500x900", async (P) => {
    const E = P.evalJs; await P.waitReady(); await P.sleep(300);
    await E(`(() => { const d = document.querySelector("#split ev-detail"), a = document.createElement("a"); a.href = "?project=" + ${JSON.stringify(encodeURIComponent(c.base.project))}; a.textContent = "seeded"; d.querySelector("dl, section").append(a); const b = document.createElement("button"); b.textContent = "seeded button"; d.append(b); })()`);
    const list = await E(CONTROLS);
    if (list.some((k) => k.kind === null)) bad.push("unclassified");
    const i = list.findIndex((k) => k.text === "seeded");
    await E(`[...document.querySelectorAll("#split ev-detail :is(a[href], button, summary, select, input, textarea, [tabindex]:not([tabindex='-1']))")].filter((el) => el.checkVisibility() && el.tabIndex >= 0 || el.tagName === "SUMMARY")[${i}].click()`); await P.sleep(300);
    if (!decl.has(`claim|link|${levelOfSearch(await E("location.search"))}`)) bad.push("undeclared move");
  });
  check("route control: an undeclared move and a control of no known kind are both caught by the crawl", bad.includes("unclassified") && bad.includes("undeclared move"), bad);
}
