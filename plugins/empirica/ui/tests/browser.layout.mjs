// Layout, balance and hints (VIEW.md "Balance"): the measurable promises, each as an in-page predicate that the checks
// use on the real page and, broken on purpose, in a negative control that must make it fail.
import catalogue from "../catalogue.json" with { type: "json" };
import { U } from "../elements/core/view-layout.js";

const PIC = catalogue.picture;
const VIEWPORTS = ["1440x900", "1280x720", "1920x1080", "1024x768", "800x600"];

/** In-page predicates (source). Each returns true when the promise holds. */
const P = {
  // Hint: beside its target (above or below, within a short gap), overlapping it horizontally.
  hint: (sel) => `(async () => { const t = document.querySelector(${JSON.stringify(sel)}); if (!t) return "no target";
    t.dispatchEvent(new PointerEvent("pointerover", { bubbles: true })); await new Promise((r) => setTimeout(r, 200));
    const h = document.getElementById("ev-hint"); if (!h.matches(":popover-open")) return "hint not shown";
    const a = h.getBoundingClientRect(), b = t.getBoundingClientRect();
    const gap = a.bottom <= b.top + 1 ? b.top - a.bottom : a.top - b.bottom;
    const ok = gap >= -1 && gap <= 24 && a.left <= b.right && a.right >= b.left;
    t.dispatchEvent(new PointerEvent("pointerout", { bubbles: true, relatedTarget: document.body })); return ok || JSON.stringify({ a, b, gap }); })()`,
  // The panel fills its column; nothing but a <pre> scrolls sideways; Close is at the inner right edge; facts align.
  panel: `(() => { const side = document.getElementById("split").lastElementChild, d = side.querySelector("ev-detail");
    for (const x of d.querySelectorAll("details")) x.open = true;
    const sr = side.getBoundingClientRect(), dr = d.getBoundingClientRect(), close = d.querySelector("[data-close]")?.getBoundingClientRect();
    if (side.scrollWidth > side.clientWidth + 1) return "panel scrolls sideways";
    if (dr.width < side.clientWidth - 2) return "detail narrower than its column: " + dr.width + " < " + side.clientWidth;
    if (!close || sr.right - close.right > 40 || sr.right - close.right < 0) return "close is not at the inner right edge";
    const over = [...d.querySelectorAll("*")].filter((e) => !e.closest("pre") && e.getBoundingClientRect().width && e.getBoundingClientRect().right > sr.right + 1);
    if (over.length) return "overflows: " + over[0].tagName;
    for (const dl of d.querySelectorAll("dl.ev-facts")) { const lefts = new Set([...dl.querySelectorAll(":scope > div > dd")].map((e) => Math.round(e.getBoundingClientRect().left))); if (lefts.size > 1) return "facts do not align"; }
    return true; })()`,
  // Scale: marks at least 8 px and growing with the text, the picture covering 60% of its area's width or height unless the text cap binds, text 11–16 px, targets at least 24 px, no sideways page scroll; the picture scrolls only at its smallest scale.
  scale: `(() => { const v = document.querySelector("ev-view"), svg = v.querySelector("svg"); if (!svg) return "no picture"; const k = svg.getScreenCTM().a, mark = 12 * k;
    const capK = ${PIC.text_px.max} / ${U.labelText}, atCap = k >= capK - 0.01;
    if (mark < ${PIC.mark_px.min} - 0.1 || mark > 12 * capK + 0.1) return "mark " + mark;
    const pic = svg.getBoundingClientRect(), box = v.getBoundingClientRect();
    if (!atCap && pic.width < 0.6 * box.width - 1 && pic.height < 0.6 * box.height - 1 && mark > ${PIC.mark_px.min} + 0.1) return "picture fills " + Math.round(100 * pic.width / box.width) + "% x " + Math.round(100 * pic.height / box.height) + "% of its area";
    for (const t of svg.querySelectorAll("text")) { const px = parseFloat(t.getAttribute("font-size")) * k; if (px < ${PIC.text_px.min} - 0.1 || px > ${PIC.text_px.max} + 0.1) return "text " + px; }
    for (const h of svg.querySelectorAll("[data-hit]")) { const r = h.getBoundingClientRect(); if (r.width < ${PIC.target_px} - 0.1 || r.height < ${PIC.target_px} - 0.1) return "target " + r.width + "x" + r.height; }
    if (document.documentElement.scrollWidth > innerWidth) return "page scrolls sideways";
    if ((v.scrollHeight > v.clientHeight + 1 || v.scrollWidth > v.clientWidth + 1) && mark > ${PIC.mark_px.min} + 0.1) return "scrolls above the smallest scale";
    return true; })()`,
  // The key: at most a fifth of the view's height, open or not.
  legend: `(() => { const l = document.querySelector("ev-legend"), s = l.closest("section"); return l.getBoundingClientRect().height <= 0.2 * s.getBoundingClientRect().height || "legend " + l.getBoundingClientRect().height + " of " + s.getBoundingClientRect().height; })()`,
  // The sidebar never scrolls; where it is a sidebar the fold's line is gone, where it is folded the line stands.
  sidebar: (wide) => `(() => { const a = document.querySelector("[data-filters]"), line = a.querySelector(".ev-fold-line");
    if (a.scrollHeight > a.clientHeight + 1) return "sidebar scrolls";
    const shown = getComputedStyle(line).display !== "none"; if (shown === ${wide}) return "fold line " + (shown ? "shown" : "hidden") + " at the wrong width";
    if (${wide} && !document.querySelector("ev-filters").getBoundingClientRect().height) return "filters hidden in the sidebar";
    return true; })()`,
  // Every drawn mark (a run's diamond, a claim's circle, outline outside) is at least mark_px.min and at least
  // mark_label_ratio times the largest text beside it.
  marks: `(() => { const svg = document.querySelector("ev-view svg"); if (!svg) return "no picture"; const k = svg.getScreenCTM().a;
    const shapes = [...svg.querySelectorAll("[data-mark] :is(path, circle)")].map((e) => e.getBoundingClientRect()).filter((r) => r.width >= 4);
    if (!shapes.length) return "no marks";
    const smallest = Math.min(...shapes.map((r) => Math.min(r.width, r.height))), biggest = Math.max(0, ...[...svg.querySelectorAll("text")].map((t) => parseFloat(t.getAttribute("font-size")) * k));
    if (smallest < ${PIC.mark_px.min} - 0.1) return "a mark is " + smallest.toFixed(1) + " px";
    if (smallest < ${PIC.mark_label_ratio} * biggest - 0.1) return "a mark is " + smallest.toFixed(1) + " px beside " + biggest.toFixed(1) + " px text";
    return true; })()`,
  // A run's whole head (diamond, count, side mark) is its target: the pointer anywhere on it reaches the run.
  head: `(() => { const m = document.querySelector('ev-view [data-mark][data-run]:not([data-claim])'); if (!m) return "no run"; const box = m.getBoundingClientRect(), t = m.querySelector("text");
    for (const b of [box, t?.getBoundingClientRect()].filter(Boolean)) { const e = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2); if (e?.closest("[data-mark]") !== m) return "the head is not the target at " + Math.round(b.x + b.width / 2) + ", " + Math.round(b.y + b.height / 2); }
    return true; })()`,
  // Packing: a project's label never sets its box's width, so projects pack in at least two columns and the picture covers
  // 60% of its area's width or height (the picture scrolls down, never sideways).
  packed: `(() => { const v = document.querySelector("ev-view"), svg = v.querySelector("svg"); if (!svg) return "no picture";
    const xs = new Set([...svg.querySelectorAll("rect.stroke-border")].map((r) => Math.round(r.getBoundingClientRect().left / 4)));
    const cols = xs.size, pic = svg.getBoundingClientRect(), box = v.getBoundingClientRect();
    if (cols < 2) return "projects pack in " + cols + " column";
    if (pic.width < 0.6 * box.width - 1 && pic.height < 0.6 * box.height - 1) return "picture covers " + Math.round(100 * pic.width / box.width) + "% x " + Math.round(100 * pic.height / box.height) + "%";
    return true; })()`,
  // A picture smaller than its box sits in the middle of it (the text cap binds), not at the top with a void below.
  centred: `(() => { const v = document.querySelector("ev-view"), svg = v.querySelector("svg"); if (!svg) return "no picture"; const a = svg.getBoundingClientRect(), b = v.getBoundingClientRect();
    if (v.scrollHeight > v.clientHeight + 1 || v.scrollWidth > v.clientWidth + 1) return true;
    const dx = Math.abs((a.left - b.left) - (b.right - a.right)), dy = Math.abs((a.top - b.top) - (b.bottom - a.bottom));
    return (dx <= 2 && dy <= 2) || "off centre by " + dx.toFixed(0) + " x " + dy.toFixed(0); })()`,
  // Focus and selection are two visuals: the focused mark has the page outline; a selected mark has its filled backing and
  // outline; a focused mark that is not selected has no selection shape; the two never look alike.
  ring: (selected) => `(() => { const m = document.activeElement; if (!m?.hasAttribute("data-mark")) return "no mark has focus"; if (m.hasAttribute("data-selected") !== ${selected}) return "wrong selection state";
    const cs = getComputedStyle(m); if (!(cs.outlineStyle === "solid" && parseFloat(cs.outlineWidth) >= 2)) return "focus has no outline: " + cs.outlineStyle;
    const ring = m.querySelector('[class*="group-data-selected"]'), rs = getComputedStyle(ring), shown = rs.display !== "none";
    if (shown !== ${selected}) return "selection shape " + (shown ? "shown by focus alone" : "missing");
    if (${selected} && (rs.fill === "none" || rs.stroke === cs.outlineColor)) return "selection looks like the focus ring: fill " + rs.fill + " stroke " + rs.stroke + " outline " + cs.outlineColor;
    return true; })()`,
  // The separator reports its width in words.
  separator: `(() => { const h = document.querySelector(".ev-split-handle"); return !!h.getAttribute("aria-valuetext") && +h.getAttribute("aria-valuenow") >= +h.getAttribute("aria-valuemin") || "no valuetext"; })()`,
};

/** Serve `view` as the observer's view (requests intercepted), so a page can show a shape the demo does not have. */
async function serveView(Pg, view) {
  Pg.on("Fetch.requestPaused", ({ requestId, request }) => {
    if (!request.url.includes("/api/view")) return Pg.send("Fetch.continueRequest", { requestId });
    const body = Buffer.from(JSON.stringify(view)).toString("base64");
    Pg.send("Fetch.fulfillRequest", { requestId, responseCode: 200, body, responseHeaders: [{ name: "Content-Type", value: "application/json" }, { name: "ETag", value: `"${view.token}"` }] });
  });
  await Pg.send("Fetch.enable", { patterns: [{ urlPattern: "*/api/view*" }] });
}
/** ~60 runs: the demo repeated with new ids, as a busy store looks. */
function dense(fx) {
  const projects = [];
  for (let i = 0; i < 5; i++) for (const p of fx.projects) {
    const id = `${p.id}-c${i}`, key = (k) => k.replace(p.id, id);
    projects.push({ ...p, id, name: p.name ? `${p.name} ${i + 1}` : null, label: p.name ? `${p.name} ${i + 1}` : `Unnamed project ${i + 1}`, runs: p.runs.map((r) => ({ ...r, key: key(r.key), project: id, previous: r.previous ? key(r.previous) : null })) });
  }
  return { ...fx, token: `sha256:${"d".repeat(64)}`, projects };
}
/** The owner's real store: dozens of unnamed projects, each told apart by its first activity, 1 to 4 runs, nothing read in full. */
function storeShaped(fx) {
  const runs = fx.projects.flatMap((p) => p.runs).map((r) => ({ ...r, claims: [], edges: [], previous: null }));
  const projects = Array.from({ length: 30 }, (_, i) => {
    const id = `store-${i}`, n = (i % 4) + 1;
    return { ...fx.projects.find((p) => p.name === null), id, name: null, label: `Unnamed project · since 23 Sep ${String(8 + (i % 14)).padStart(2, "0")}:${String((i * 7) % 60).padStart(2, "0")}`,
      runs: Array.from({ length: n }, (_, k) => { const r = runs[(i + k) % runs.length]; return { ...r, key: `${r.key}-s${i}-${k}`, project: id }; }) };
  });
  return { ...fx, token: `sha256:${"f".repeat(64)}`, projects };
}
const one = (fx, key) => { const p = fx.projects.find((x) => x.runs.some((r) => r.key === key)); return { ...fx, token: `sha256:${"e".repeat(64)}`, projects: [{ ...p, runs: [{ ...p.runs.find((r) => r.key === key), previous: null }] }] }; };

export async function layoutChecks({ B, fx, details, withPage, check, resize, keys }) {
  const APP = (q = "") => `${B}/app/?data=demo${q ? `&${q}` : ""}`;
  const q = (o) => Object.entries(o).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  const E = (Pg) => Pg.evalJs;

  // --- the picture, the key, the sidebar at every viewport, on three shapes of data ---------------------------------
  const shapes = [["demo", null], ["dense (60 runs)", dense(fx)], ["one run", one(fx, keys.STALE)]];
  for (const [shape, view] of shapes) for (const vp of VIEWPORTS) {
    await withPage(APP(), vp, async (Pg) => {
      if (view) await serveView(Pg, view);
      await Pg.waitReady(); await Pg.sleep(view ? 1200 : 400);
      const [w] = vp.split("x").map(Number), wide = w >= 1440;
      check(`balance ${shape} @${vp}: marks ≥ 11 px, text 11–16 px, targets ≥ 24 px, the picture fills its area`, (await Pg.evalJs(P.scale)) === true, await Pg.evalJs(P.scale));
      check(`balance ${shape} @${vp}: every mark, claim circles included, is ≥ 11 px and ≥ 0.9 × its text`, (await Pg.evalJs(P.marks)) === true, await Pg.evalJs(P.marks));
      check(`balance ${shape} @${vp}: the whole head of a run is its target`, (await Pg.evalJs(P.head)) === true, await Pg.evalJs(P.head));
      check(`balance ${shape} @${vp}: a picture smaller than its box is centred`, (await Pg.evalJs(P.centred)) === true, await Pg.evalJs(P.centred));
      check(`balance ${shape} @${vp}: the key is at most a fifth of the view`, (await Pg.evalJs(P.legend)) === true, await Pg.evalJs(P.legend));
      await Pg.evalJs(`document.querySelector("ev-legend summary").click()`); await Pg.sleep(100);
      check(`balance ${shape} @${vp}: and so it is open (a layer over the picture)`, (await Pg.evalJs(P.legend)) === true && await Pg.evalJs(`document.querySelector("ev-legend details").open`));
      check(`balance ${shape} @${vp}: the sidebar never scrolls, and is a sidebar only from 1440 px`, (await Pg.evalJs(P.sidebar(wide))) === true, await Pg.evalJs(P.sidebar(wide)));
    });
  }

  // --- the real store's shape: long unique labels must not collapse the packing --------------------------------------
  for (const vp of ["1440x900", "1920x1080"]) await withPage(APP(), vp, async (Pg) => {
    await serveView(Pg, storeShaped(fx)); await Pg.send("Page.reload");
    await Pg.waitReady(); await Pg.sleep(1200);
    check(`packing store-shaped @${vp}: the page shows the store-shaped view (30 projects)`, (await Pg.evalJs(`document.querySelectorAll("ev-view [data-mark][data-project]").length`)) === 30);
    check(`packing store-shaped @${vp}: at least two columns, the picture covers 60% of its area`, (await Pg.evalJs(P.packed)) === true, await Pg.evalJs(P.packed));
    check(`packing store-shaped @${vp}: no project box is wider than its marks plus a wrapped label`, await Pg.evalJs(`(() => { const w = [...document.querySelectorAll("ev-view svg rect.stroke-border")].map((r) => +r.getAttribute("width")); const vb = document.querySelector("ev-view svg").viewBox.baseVal; return Math.max(...w) <= ${U.labelCap * U.char + 2 * U.pad} + ${U.headW * 4 + U.runGap * 4} && Math.max(...w) < vb.width * 0.75; })()`));
    if (vp === "1440x900") {
      const before = await Pg.evalJs(P.packed);
      await Pg.evalJs(`document.querySelector("ev-view svg").setAttribute("width", "100"); document.querySelector("ev-view svg").setAttribute("height", "100")`); await Pg.sleep(100);
      check(`packing control: a picture that covers little of its area is caught by "packed"`, before === true && (await Pg.evalJs(P.packed)) !== true, [before, await Pg.evalJs(P.packed)]);
    }
  });
  await withPage(APP(), "1440x900", async (Pg) => {
    await serveView(Pg, (() => { const s = storeShaped(fx); return { ...s, token: `sha256:${"9".repeat(64)}`, projects: s.projects.slice(0, 1) }; })()); await Pg.send("Page.reload");
    await Pg.waitReady(); await Pg.sleep(1000);
    check(`packing control: a single project (one column) is caught by "packed"`, (await Pg.evalJs(P.packed)) !== true, await Pg.evalJs(P.packed));
  });

  // --- 200 % zoom (720 × 450 CSS px at a pixel ratio of 2), both data shapes ------------------------------------------
  for (const [shape, view] of shapes.slice(0, 2)) await withPage(APP(), "720x450", async (Pg) => {
    await Pg.send("Emulation.setDeviceMetricsOverride", { width: 720, height: 450, deviceScaleFactor: 2, mobile: false });
    if (view) await serveView(Pg, view);
    await Pg.waitReady(); await Pg.sleep(view ? 1200 : 500);
    check(`balance ${shape} @200%: marks ≥ 11 px (claim circles too) and ≥ 0.9 × their text`, (await Pg.evalJs(P.marks)) === true, await Pg.evalJs(P.marks));
    check(`balance ${shape} @200%: text 11–16 px, targets ≥ 24 px`, (await Pg.evalJs(P.scale)) === true, await Pg.evalJs(P.scale));
  });

  // --- columns ---------------------------------------------------------------------------------------------------
  for (const [vp, panel] of [["1440x900", 446], ["1920x1080", 448], ["1280x720", 397], ["1100x800", 341]]) {
    await withPage(APP(q({ run: keys.STALE })), vp, async (Pg) => {
      await Pg.waitReady(); await Pg.sleep(400);
      const [w] = vp.split("x").map(Number);
      const got = await Pg.evalJs(`Math.round(document.getElementById("split").lastElementChild.getBoundingClientRect().width)`);
      check(`columns @${vp}: the panel is clamp(20rem, 31vw, 28rem) wide (${panel} px)`, Math.abs(got - panel) <= 8, got);
      const centre = await Pg.evalJs(`Math.round(document.querySelector("ev-view").getBoundingClientRect().width)`);
      check(`columns @${vp}: the picture keeps at least 40rem beside the panel`, w < 1024 || centre >= 630, centre);
    });
  }
  for (const vp of ["800x600", "1024x768"]) await withPage(APP(q({ run: keys.STALE })), vp, async (Pg) => {
    await Pg.waitReady(); await Pg.sleep(400);
    check(`columns @${vp}: at 1024 px and below the detail takes the full width`, await Pg.evalJs(`document.getElementById("split").hasAttribute("narrow") && Math.abs(document.getElementById("split").lastElementChild.getBoundingClientRect().width - innerWidth) < 2`));
  });
  await withPage(APP(), "1280x720", async (Pg) => {
    await Pg.waitReady(); await Pg.sleep(300);
    const line = `document.querySelector("[data-filters] .ev-fold-line").textContent.trim()`;
    check("columns: folded filters say how many are set", (await Pg.evalJs(line)) === "Filters · 0 set" && !(await Pg.evalJs(`document.querySelector("ev-filters").getBoundingClientRect().height > 0`)));
    await Pg.evalJs(`document.querySelector("[data-filters] .ev-fold-line").click()`); await Pg.sleep(150);
    await Pg.evalJs(`(() => { const x = document.querySelector('ev-filters input[data-filter="run-state"]'); x.checked = true; x.dispatchEvent(new Event("change", { bubbles: true })); })()`); await Pg.sleep(200);
    check("columns: and open on click, and the count follows a choice", (await Pg.evalJs(line)) === "Filters · 1 set" && await Pg.evalJs(`document.querySelector("ev-filters").getBoundingClientRect().height > 0`));
  });

  // --- the panel fills its column, for every kind of detail, with the technical details open --------------------------
  const d = details[keys.STALE], row = d.steps.find((s) => s.kind !== "bookkeeping") ?? d.steps[0];
  const kinds = [
    ["project", q({ project: fx.projects.find((p) => p.runs.some((r) => r.key === keys.STALE)).id })], ["run", q({ run: keys.STALE })],
    ["claim", q({ run: keys.STALE, claim: fx.projects.flatMap((p) => p.runs).find((r) => r.key === keys.STALE).claims[0].id })],
    ["step", q({ run: keys.STALE, step: row.n })], ["file", q({ run: keys.STALE, file: d.stale[0].path })],
    ["run with records", q({ run: keys.VERIFY })], ["claim with records", q({ run: keys.VERIFY, claim: details[keys.VERIFY].claims.find((c) => c.records.length).id })],
  ];
  for (const [kind, query] of kinds) {
    await withPage(APP(query), "1440x900", async (Pg) => {
      await Pg.waitReady(); await Pg.sleep(500);
      check(`panel: the ${kind} detail fills its column, nothing overflows, facts align`, (await Pg.evalJs(P.panel)) === true, await Pg.evalJs(P.panel));
      if (kind === "run with records") check("panel: each budget is named once", await Pg.evalJs(`(() => { const lines = document.querySelector("#split ev-detail").innerText.split("\\n").map((x) => x.trim()); return ["Passes", "Spawns", "Audit spawns"].every((n) => lines.filter((l) => l === n).length === 1); })()`));
    });
  }

  // --- hints sit at their target, at DPR 1 and 2, with the panel scrolled -------------------------------------------
  for (const dpr of [1, 2]) {
    await withPage(APP(q({ run: keys.STALE })), "1440x900", async (Pg) => {
      await Pg.send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: dpr, mobile: false });
      await Pg.waitReady(); await Pg.sleep(500);
      await Pg.evalJs(`document.getElementById("split").lastElementChild.scrollTop = 30`);
      const project = `ev-view [data-mark][data-project]`, run = `ev-view [data-mark][data-run="${keys.STALE}"]:not([data-claim])`, claim = `ev-view [data-mark][data-run="${keys.STALE}"][data-claim]`;
      for (const [what, sel] of [["project", project], ["run", run], ["claim", claim], ["an HTML chip in the panel", "#split ev-detail [data-hint]"]]) {
        const r = await Pg.evalJs(P.hint(sel));
        check(`hint @DPR${dpr}: on ${what} it sits at its target`, r === true, r);
      }
    });
  }

  // --- one ring, the separator's keys, the page's furniture ------------------------------------------------------------
  await withPage(APP(q({ run: keys.STALE })), "1440x900", async (Pg) => {
    await Pg.waitReady(); await Pg.sleep(400);
    await Pg.evalJs(`document.querySelector('ev-view [data-mark][data-run="${keys.STALE}"][data-claim]').focus()`); await Pg.key("Enter"); await Pg.sleep(250);
    await Pg.key("Tab"); await Pg.key("Tab", "Tab"); await Pg.evalJs(`document.querySelector('ev-view [data-mark][data-selected]').focus()`); await Pg.sleep(100);
    check("one ring: a focused, selected mark has the focus outline and a different selection backing", (await Pg.evalJs(P.ring(true))) === true, await Pg.evalJs(P.ring(true)));
    await Pg.evalJs(`document.querySelector('ev-view [data-mark][data-project]').focus()`); await Pg.sleep(100);
    check("one ring: a focused mark that is not selected has the focus outline and no selection shape", (await Pg.evalJs(P.ring(false))) === true, await Pg.evalJs(P.ring(false)));
    await Pg.evalJs(`document.querySelector(".ev-split-handle").focus()`);
    const now = () => Pg.evalJs(`+document.querySelector(".ev-split-handle").getAttribute("aria-valuenow")`);
    const bound = (n) => Pg.evalJs(`+document.querySelector(".ev-split-handle").getAttribute("aria-value${n}")`);
    await Pg.key("Home"); await Pg.sleep(100); const atMin = await now();
    await Pg.key("PageUp"); await Pg.sleep(100); const up = await now();
    await Pg.key("End"); await Pg.sleep(100); const atMax = await now();
    await Pg.key("PageDown"); await Pg.sleep(100); const down = await now();
    check("separator: Home, End, PageUp and PageDown move it, within its bounds, and it says its width", atMin === (await bound("min")) && up > atMin && atMax === (await bound("max")) && down < atMax && (await Pg.evalJs(P.separator)) === true, [atMin, up, atMax, down]);
    check("page: an icon, and no script the page does not use", await Pg.evalJs(`!!document.querySelector('link[rel="icon"]') && ![...document.scripts].some((s) => s.src.includes("tabs"))`));
  });

  // --- negative controls: each predicate fails on a page broken the way it guards -------------------------------------
  const controls = [
    ["a hint anchored away from its target", APP(q({ run: keys.STALE })), `document.querySelector(".ev-hint-anchor").style.setProperty("left", "0px")`, "hint",
      async (Pg) => { const sel = `ev-view [data-mark][data-run="${keys.STALE}"]:not([data-claim])`;
        return Pg.evalJs(`(async () => { const t = document.querySelector(${JSON.stringify(sel)}); t.dispatchEvent(new PointerEvent("pointerover", { bubbles: true })); await new Promise((r) => setTimeout(r, 200));
          const a = document.querySelector(".ev-hint-anchor"); a.style.setProperty("left", "0px"); a.style.setProperty("top", "0px"); await new Promise((r) => setTimeout(r, 100));
          const h = document.getElementById("ev-hint").getBoundingClientRect(), b = t.getBoundingClientRect(); return !(h.left <= b.right && h.right >= b.left && Math.abs(h.bottom - b.top) < 24); })()`); }],
    ["a panel that is only as wide as its content", APP(q({ run: keys.STALE })), `document.querySelector("#split ev-detail").style.setProperty("flex", "none"); document.querySelector("#split ev-detail").style.setProperty("width", "6rem")`, "panel"],
    ["text that is not 11–16 px", APP(), `document.querySelectorAll("ev-view svg text").forEach((t) => t.setAttribute("font-size", "2"))`, "scale"],
    ["a key that takes the picture's room", APP(), `document.querySelector("ev-legend summary").click(); document.querySelector("ev-legend details > div").className = "flex flex-col"`, "legend"],
    ["a sidebar that scrolls", APP(), `document.querySelector("[data-filters]").style.setProperty("height", "60px"); document.querySelector("[data-filters]").style.setProperty("overflow", "hidden")`, "sidebar1440"],
    ["a separator that does not say its width", APP(q({ run: keys.STALE })), `document.querySelector(".ev-split-handle").removeAttribute("aria-valuetext")`, "separator"],
  ];
  // The two visuals: focus alone must not draw the selection shape, and the selection must not look like the focus ring.
  const rings = [
    ["a selection shape drawn by focus alone", false, `document.querySelector('ev-view [data-mark][data-project]').focus(); document.querySelector('ev-view [data-mark][data-project] [class*="group-data-selected"]').style.setProperty("display", "inline")`],
    ["a selection drawn in the focus ring's own colour and unfilled", true, `(() => { const m = document.querySelector("ev-view [data-mark][data-selected]"); m.focus(); const r = m.querySelector('[class*="group-data-selected"]'); r.style.setProperty("fill", "none"); })()`],
    ["a focus with no outline", true, `(() => { const m = document.querySelector("ev-view [data-mark][data-selected]"); m.focus(); m.style.setProperty("outline", "none", "important"); })()`],
  ];
  for (const [what, selected, setup] of rings) await withPage(APP(q({ run: keys.STALE })), "1440x900", async (Pg) => {
    await Pg.waitReady(); await Pg.sleep(400);
    if (selected) await Pg.evalJs(`document.querySelector("ev-view [data-mark][data-selected]").focus()`); else await Pg.evalJs(`document.querySelector('ev-view [data-mark][data-project]').focus()`);
    await Pg.sleep(100);
    const before = await Pg.evalJs(P.ring(selected));
    await Pg.evalJs(setup); await Pg.sleep(100);
    check(`balance control: ${what} is caught by "ring"`, before === true && (await Pg.evalJs(P.ring(selected))) !== true, [before, await Pg.evalJs(P.ring(selected))]);
  });
  // A picture pushed to the top of its box with a void below, and marks drawn smaller than 11 px, are caught.
  await withPage(APP(), "1920x1080", async (Pg) => {
    await Pg.waitReady(); await Pg.sleep(500);
    const before = await Pg.evalJs(P.centred);
    await Pg.evalJs(`document.querySelector("ev-view svg").style.setProperty("margin", "0 auto auto")`); await Pg.sleep(100);
    check(`balance control: a picture stuck to the top of its box is caught by "centred"`, before === true && (await Pg.evalJs(P.centred)) !== true, [before, await Pg.evalJs(P.centred)]);
    const head = await Pg.evalJs(P.head);
    await Pg.evalJs(`document.querySelectorAll('ev-view [data-mark][data-run]:not([data-claim]) [data-hit]').forEach((h) => h.setAttribute("width", "4"))`); await Pg.sleep(100);
    check(`balance control: a run target that is only the diamond is caught by "head"`, head === true && (await Pg.evalJs(P.head)) !== true, [head, await Pg.evalJs(P.head)]);
    const marks = await Pg.evalJs(P.marks);
    await Pg.evalJs(`(() => { const s = document.querySelector("ev-view svg"); s.setAttribute("width", s.getAttribute("width") / 2); s.setAttribute("height", s.getAttribute("height") / 2); })()`); await Pg.sleep(100);
    check(`balance control: marks drawn at half size are caught by "marks"`, marks === true && (await Pg.evalJs(P.marks)) !== true, [marks, await Pg.evalJs(P.marks)]);
  });
  for (const [what, url, mutate, pred, custom] of controls) {
    await withPage(url, "1440x900", async (Pg) => {
      await Pg.waitReady(); await Pg.sleep(400);
      if (custom) { check(`balance control: ${what} is caught by "${pred}"`, (await custom(Pg)) === true); return; }
      const src = pred === "sidebar1440" ? P.sidebar(true) : P[pred];
      const before = await Pg.evalJs(src);
      await Pg.evalJs(mutate); await Pg.sleep(150);
      const after = await Pg.evalJs(src);
      check(`balance control: ${what} is caught by "${pred}"`, (pred === "ring" ? true : before === true) && after !== true, [before, after]);
    });
  }
}
