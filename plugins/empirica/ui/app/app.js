// The app: the view (VIEW.md). Every project, run and claim in the store, with their relationships, in one picture;
// the filters remove what they exclude; the side panel shows the detail of what is clicked. Page code wires the
// elements and applies the declared filters (app/filter.js); it decides nothing (E4): states come from the
// observer (contract empirica-ui-view/v1, through app/client.js). The app asks every `poll_ms` whether the store
// changed and redraws only when it did; a run's detail is fetched when it is selected. The URL keeps the data
// source, the filters and the selection (app/route.js: one value, moved only by `navigate`).
import "../elements/index.js";
import { PRIORITY, layer } from "../elements/core/dismiss.js";
import { optionLabel, quietOf, statesOf, t } from "../elements/core/element.js";
import { claimDetail, fileCrumb, fileDetail, projectDetail, runCrumb, runDetail, stepCrumb, stepDetail } from "../elements/templates/details.js";
import { Client } from "./client.js";
import { apply, showing } from "./filter.js";
import { markedOf, parentOf, parse, path, same, write } from "./route.js";
import { choiceFrom, writeChoice } from "./url.js";
import catalogue from "../catalogue.json" with { type: "json" };

// The colour scheme follows the system (the gallery has its own switch to show both).
const dark = matchMedia("(prefers-color-scheme: dark)");
const scheme = () => document.documentElement.classList.toggle("dark", dark.matches);
scheme();
dark.addEventListener("change", scheme);

const params = new URLSearchParams(location.search);
const source = catalogue.data_sources.includes(params.get("data")) ? params.get("data") : "store";
const client = new Client(source);
const FILTERS = catalogue.filters;

document.title = t("page.title.view");
document.getElementById("bar").innerHTML = `<div class="flex flex-wrap items-center justify-between gap-m">
  <span class="text-s font-semibold text-foreground">${t("app.title")}</span>
  <div class="flex items-center gap-l">
    <span id="status" class="text-xs text-muted-foreground" aria-live="polite"></span>
    <label class="flex items-center gap-xs text-xs text-muted-foreground">${t("app.data")}
      <select id="source" class="select" data-size="sm">${catalogue.data_sources.map((k) => `<option value="${k}" ${k === source ? "selected" : ""}>${t(`app.data.${k}`)}</option>`).join("")}</select></label>
  </div>
</div>`;
document.getElementById("source").addEventListener("change", (e) => { location.search = new URLSearchParams({ data: e.target.value }); });
// What the header says for each answer the client can give (none for a good one).
const STATUS = { offline: "app.status.offline", error: "app.status.error", missing: "app.status.missing", invalid: "app.status.invalid", page: "app.status.page" };
const status = (key) => { document.getElementById("status").textContent = key ? t(key) : ""; };

const main = document.getElementById("main");
// Filters: a sidebar from the wide breakpoint (its fold is open and its line gone: app.css), else one line above the picture.
main.innerHTML = `<div class="flex h-full min-h-0 flex-col gap-s wide:flex-row wide:gap-xl">
  <aside class="relative shrink-0 wide:w-sidebar wide:overflow-y-auto" aria-label="${t("filter.label")}" data-filters>
    <div class="ev-fold" id="fold">
      <button type="button" class="ev-fold-line text-xs font-medium text-foreground underline underline-offset-4" id="fold-line" aria-expanded="false" aria-controls="filters"></button>
      <div class="ev-fold-body"><ev-filters id="filters"></ev-filters></div>
    </div>
  </aside>
  <section class="flex min-h-0 min-w-0 flex-1 flex-col gap-s" aria-label="${t("view.label")}">
    <h1 class="text-m font-semibold">${t("view.label")}</h1>
    <ev-legend id="key"></ev-legend>
    <ev-view id="view" class="min-h-0 flex-1 overflow-auto"></ev-view>
  </section>
</div>`;
document.getElementById("fold-line").addEventListener("click", (e) => {
  const open = document.getElementById("fold").toggleAttribute("data-open");
  e.currentTarget.setAttribute("aria-expanded", String(open));
});
const view = document.getElementById("view");
const filtersEl = document.getElementById("filters");
const split = document.getElementById("split");
const detail = split.querySelector("ev-detail");
split.lastElementChild.setAttribute("aria-label", t("page.details"));

// ---- the data: the observer's view, replaced when the store changed ---------------------------------------------
let data = { projects: [] }, runs = [], byKey = new Map(), options = {};
let answered = false;  // whether the observer has given a view yet (until then, an empty view says nothing)
// The filter values on offer: those present in the data, in their declared order.
function present(f) {
  if (f.options === "projects") return data.projects.map((p) => ({ value: p.id, label: p.label }));
  const all = f.domain ? statesOf(f.domain) : catalogue[f.values];
  const seen = new Set(f.of === "run" ? runs.map((r) => r[f.field]) : runs.flatMap((r) => r.claims.map((c) => c[f.field])));
  return all.filter((v) => seen.has(v)).map((v) => ({ value: v, label: optionLabel(f, v) }));
}
function setData(v) {
  answered = true;
  data = v;
  runs = data.projects.flatMap((p) => p.runs);
  byKey = new Map(runs.map((r) => [r.key, r]));
  const next = Object.fromEntries(FILTERS.filter((f) => f.control !== "flag").map((f) => [f.id, present(f)]));
  // The form is rebuilt only when what it offers changed, so a poll never takes focus from it.
  if (JSON.stringify(next) !== JSON.stringify(options)) { options = next; filtersEl.data = { filters: FILTERS, options }; }
}

// ---- the URL: the data source, the filters (app/url.js) and the selection (app/route.js) ------------------------
const choiceOf = (q) => choiceFrom(q, FILTERS, options);
function save(push = false) {
  const u = `?${params}`;
  if (push) history.pushState(null, "", u); else history.replaceState(null, "", u);
}

// ---- what is selected, and its detail -----------------------------------------------------------------------------
// The selection is one value (route.js); `navigate` is the only way it changes, whatever moved it: a click on a mark,
// a link or breadcrumb in the panel, Esc, the close button, Back or a reload. A user's move pushes one history entry
// (Back returns to where you were); a poll, a restore or a correction only replaces the current one.
let shown = null;  // the filtered data the view draws
let current = { kind: "none" };  // what the panel shows
let painted = null;  // the selection whose content the panel holds (its scroll resets when this changes)
let wantFocus = false;  // a move that leaves nothing else to hold focus puts it on the panel's title
const inShown = (key) => shown.projects.flatMap((p) => p.runs).find((r) => r.key === key);
/** Whether the view shows what `sel` names. */
function visible(sel) {
  if (sel.kind === "none") return true;
  if (sel.kind === "project") return shown.projects.some((p) => p.id === sel.project);
  const r = inShown(sel.run);
  return !!r && (sel.kind !== "claim" || r.claims.some((c) => c.id === sel.claim));
}
/** A link to `sel`: the data source and the filters stay, unless `sel` is outside what they leave in the view. */
function href(sel) {
  const q = new URLSearchParams(params);
  if (!visible(sel)) writeChoice(q, {}, FILTERS);
  write(q, sel);
  return `?${q}`;
}
/** `sel` as far as it exists in the data: a claim or run that is gone falls back to what is left of it. */
function resolve(sel) {
  if (sel.kind === "project") return data.projects.some((p) => p.id === sel.project) ? sel : { kind: "none" };
  if (sel.kind === "none") return sel;
  const r = byKey.get(sel.run);
  if (!r) return { kind: "none" };
  return sel.kind === "claim" && !r.claims.some((c) => c.id === sel.claim) ? { kind: "run", run: sel.run } : sel;
}
/** What the selection's detail depends on: it is redrawn after a poll only when this changed. */
function signature(sel) {
  if (sel.kind === "none") return null;
  if (sel.kind !== "project") return byKey.get(sel.run)?.version ?? null;
  const p = data.projects.find((x) => x.id === sel.project);
  return p ? JSON.stringify([p.name, p.repo, p.label, p.runs.map((r) => r.version)]) : null;
}

const crumbText = (sel) => {
  if (sel.kind === "project") return data.projects.find((p) => p.id === sel.project)?.label ?? "";
  const r = byKey.get(sel.run);
  if (sel.kind === "run") return r ? runCrumb(r) : t("crumb.run");
  if (sel.kind === "step") return stepCrumb(sel.step, r && client.cached(r)?.steps.find((x) => x.n === sel.step));
  if (sel.kind === "file") return fileCrumb(sel.file);
  return t("crumb.claim");
};
/** The panel's place: the levels above as links, then itself. The top level needs none. */
function crumbs(sel) {
  const chain = path(sel, byKey);
  return chain.length < 2 ? undefined : chain.map((p, i) => ({ label: crumbText(p), ...(i < chain.length - 1 ? { href: href(p) } : {}) }));
}

// The panel stays open on a wide screen, so clicking only changes what it shows and nothing in the view moves
// (R6); with nothing selected it says what to click. On a narrow screen it opens only for a selection.
const wide = () => !split.hasAttribute("narrow");
const panelTitle = () => detail.querySelector("[data-panel-title]");
function open(sel, d) {
  detail.data = { ...d, crumbs: crumbs(sel) };
  split.setAttribute("open", "");
  if (!same(sel, painted)) split.lastElementChild.scrollTop = 0;
  painted = sel;
  const a = document.activeElement;
  if (wantFocus && (!a || a === document.body || detail.contains(a))) panelTitle()?.focus({ preventScroll: true });
}
function idle() { detail.data = null; painted = null; split.toggleAttribute("open", wide()); }
new MutationObserver(() => { if (!detail.data) idle(); }).observe(split, { attributes: true, attributeFilter: ["narrow"] });
function mark(sel) {
  const at = { project: sel.kind === "project" ? sel.project : null, run: sel.run ?? null, claim: sel.kind === "claim" ? sel.claim : null };
  for (const [k, v] of Object.entries(at)) if (v) view.setAttribute(k, v); else view.removeAttribute(k);
}
// A run's detail: shown at once with what is already known, then again when the run's document arrives (unless
// something else was selected meanwhile).
let ticket = 0;
// A failed detail keeps the row's facts and says so inside the panel; the header is for the view poll alone.
const NOTICE = { missing: "detail.missing", offline: "detail.offline", error: "detail.error", invalid: "detail.invalid" };
/** Whether the run's document holds what a step or file selection names. */
const holds = (sel, d) => sel.kind === "step" ? d.steps.some((s) => s.n === sel.step) : sel.kind !== "file" || d.stale.some((f) => f.path === sel.file);
/** The panel for `sel` from the run's document `d` (null until it arrives). A step or file that is not known yet is
 * only its place and its name. */
function content(sel, r, d) {
  if (sel.kind === "claim") return claimDetail(r, r.claims.find((c) => c.id === sel.claim), href, d);
  if (sel.kind === "step") {
    const s = d?.steps.find((x) => x.n === sel.step);
    return s ? stepDetail(r, s, d.steps.length, href) : { kicker: crumbText(sel), facts: [] };
  }
  if (sel.kind === "file") {
    const f = d?.stale.find((x) => x.path === sel.file);
    return f ? fileDetail(f) : { kicker: crumbText(sel), facts: [] };
  }
  return runDetail(r, d, href);
}
function withDetail(sel, r) {
  const mine = ++ticket, have = client.cached(r);
  if (have && !holds(sel, have)) return navigate(parentOf(sel, byKey), { push: false, reveal: false });
  open(sel, content(sel, r, have));
  if (have) { wantFocus = false; return; }
  client.detail(r).then((res) => {
    if (mine !== ticket) return;
    if (res.status === "ok" && !holds(sel, res.detail)) return navigate(parentOf(sel, byKey), { push: false, reveal: false });
    open(sel, res.status === "ok" ? content(sel, r, res.detail) : { ...content(sel, r, null), notice: NOTICE[res.status] });
    wantFocus = false;
  });
}
function show(sel) {
  mark(sel);
  if (sel.kind === "none") { ticket++; idle(); return; }
  if (sel.kind === "project") { ticket++; open(sel, projectDetail(data.projects.find((p) => p.id === sel.project), href)); wantFocus = false; return; }
  withDetail(sel, byKey.get(sel.run));
}

const MARKS = {
  project: (s) => `[data-mark][data-project="${CSS.escape(s.project)}"]`,
  run: (s) => `[data-mark][data-run="${CSS.escape(s.run)}"]:not([data-claim])`,
  claim: (s) => `[data-mark][data-run="${CSS.escape(s.run)}"][data-claim="${CSS.escape(s.claim)}"]`,
};
/** Focus goes where the user was: the mark of what was left (found by what it is, not by the node). Where the view is
 * hidden behind the panel, it goes to the panel's title; where the view is still being drawn (it takes its size from
 * the page after the panel closes), the mark is waited for, up to 20 frames. */
function refocus(from, tries = 20) {
  // A step or a file has no mark in the view: focus goes to the nearest level above it that has (its run).
  const left = markedOf(from, byKey) ?? from;
  const el = MARKS[left.kind] && view.querySelector(MARKS[left.kind](left));
  if (el?.checkVisibility({ checkVisibilityCSS: true })) { el.focus({ preventScroll: true }); return; }
  const hidden = split.hasAttribute("open") && split.hasAttribute("narrow");
  const a = document.activeElement;
  if (!hidden && tries > 0 && (!a || a === document.body)) { requestAnimationFrame(() => refocus(from, tries - 1)); return; }
  wantFocus = true;
  panelTitle()?.focus({ preventScroll: true });
}
/** Move the selection. `push`: a user's move adds a history entry (unless it changes nothing); `reveal`: a target
 * outside the filtered view clears the filters (a link does), else it is dropped (a poll, a restore); `focus`: "panel"
 * puts focus on the panel's title, "mark" on the mark of `left`. */
function navigate(to, { push = true, reveal = true, focus = null, left = null } = {}) {
  let sel = resolve(to);
  if (!visible(sel)) {
    if (reveal) filter({}); else sel = { kind: "none" };
  }
  const moved = !same(sel, current);
  current = sel;
  wantFocus = focus === "panel";
  show(sel);
  write(params, sel);
  save(push && moved);
  if (focus === "mark" && left) refocus(left);
}

view.addEventListener("ev-open", (e) => {
  const d = e.detail;
  navigate(d.type === "claim" ? { kind: "claim", run: d.run, claim: d.id } : d.type === "run" ? { kind: "run", run: d.run } : { kind: "project", project: d.id });
});
// Inside the panel: a run's history step, one of its changed files, a link (another run, claim or level above).
detail.addEventListener("ev-select", (e) => { if (current.run) navigate({ kind: "step", run: current.run, step: e.detail.step }, { focus: "panel" }); });
detail.addEventListener("ev-open", (e) => { if (current.run && e.detail.type === "file") navigate({ kind: "file", run: current.run, file: e.detail.path }, { focus: "panel" }); });
detail.addEventListener("click", (e) => {
  const a = e.target.closest('a[href^="?"]');
  if (!a || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  navigate(parse(new URLSearchParams(a.getAttribute("href"))), { focus: "panel" });
});
// The close button leaves the whole selection; Esc leaves one level (one layer at a time: core/dismiss.js). Focus
// goes back to the mark of what was left.
detail.addEventListener("ev-close", (e) => { e.stopPropagation(); navigate({ kind: "none" }, { focus: "mark", left: current }); });
layer({ priority: PRIORITY.selection, open: () => current.kind !== "none", close: () => navigate(parentOf(current, byKey), { focus: "mark", left: current }) });
split.addEventListener("ev-panel", (e) => {
  if (e.detail.open) return;
  if (current.kind !== "none") navigate({ kind: "none" }, { focus: "mark", left: current }); else idle();
});

// ---- the filters ------------------------------------------------------------------------------------------------
let choice = {};
function filter(next) {
  choice = next;
  shown = apply(data, choice, FILTERS);
  // An empty store says so; an empty filter result says that.
  view.data = { ...shown, empty: runs.length ? "view.empty" : answered ? `view.empty.${source}` : "view.empty.offline" };
  document.getElementById("key").data = showing(shown.projects, statesOf, quietOf);
  filtersEl.choice = choice;
  filtersEl.counts = shown.counts;
  document.getElementById("fold-line").textContent = t("filters.fold", { n: Object.keys(choice).length });
  writeChoice(params, choice, FILTERS);
}
filtersEl.addEventListener("ev-filter", (e) => {
  filter(e.detail.choice);
  // A selection the filters removed goes with it (R5).
  if (!visible(current)) navigate({ kind: "none" }, { push: false }); else save();
});
window.addEventListener("popstate", () => {
  for (const k of [...params.keys()]) params.delete(k);
  for (const [k, v] of new URLSearchParams(location.search)) params.set(k, v);
  filter(choiceOf(params));
  navigate(parse(params), { push: false, reveal: false });
});

// ---- polling ----------------------------------------------------------------------------------------------------
/** A changed store: new data, the same filters (minus values no longer on offer), the selection kept if it is
 * still there; its detail is redrawn only if what it shows changed. */
function refresh(v) {
  const sel = current, before = signature(sel);
  setData(v);
  filter(choiceOf(params));
  // Unchanged and still showing: the panel stays as it is (its scroll, an open step); the view keeps the mark. Else
  // it is redrawn, or dropped if it left the store; a poll never adds a history entry.
  if (!(visible(sel) && signature(sel) === before)) navigate(sel, { push: false, reveal: false }); else save();
}
async function poll() {
  try {
    const r = await client.view();  // reports every outcome as a value; it never throws
    status(STATUS[r.status] ?? null);
    if (r.status === "changed") refresh(r.view);
  } catch (e) {
    console.error(e);
    status(STATUS.page);  // a bug in drawing, not in the observer's answer; the last good view stays on screen
  } finally {
    setTimeout(poll, catalogue.poll_ms);
  }
}

const first = await client.view();
status(STATUS[first.status] ?? null);
if (first.status === "changed") setData(first.view);
filter(choiceOf(params));
navigate(parse(params), { push: false, reveal: false });
setTimeout(poll, catalogue.poll_ms);
document.body.dataset.ready = "1";
