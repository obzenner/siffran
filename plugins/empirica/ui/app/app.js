// The app: the view (VIEW.md). Every project, run and claim in the store, with their relationships, in one picture;
// the filters remove what they exclude; the side panel shows the detail of what is clicked. Page code wires the
// elements and applies the declared filters (app/filter.js); it decides nothing (E4): states come from the
// observer (contract empirica-ui-view/v1, through app/client.js). The app asks every `poll_ms` whether the store
// changed and redraws only when it did; a run's detail is fetched when it is selected. The URL keeps the data
// source, the filters and the selection.
import "../elements/index.js";
import { projectName, statesOf, t } from "../elements/core/element.js";
import { optionLabel } from "../elements/organisms/ev-filters.js";
import { claimDetail, fileDetail, projectDetail, runDetail, stepDetail } from "../elements/templates/details.js";
import { Client } from "./client.js";
import { apply, showing } from "./filter.js";
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
const STATUS = { error: "app.status.offline", missing: "app.status.missing", invalid: "app.status.invalid" };
const status = (key) => { document.getElementById("status").textContent = key ? t(key) : ""; };

const main = document.getElementById("main");
main.innerHTML = `<div class="flex h-full min-h-0 gap-xl">
  <aside class="relative w-sidebar shrink-0 overflow-y-auto" aria-label="${t("filter.label")}" data-filters><ev-filters id="filters"></ev-filters></aside>
  <section class="flex min-w-0 flex-1 flex-col gap-s" aria-label="${t("view.label")}">
    <h1 class="text-m font-semibold">${t("view.label")}</h1>
    <ev-legend id="key"></ev-legend>
    <ev-view id="view" class="block min-h-0 flex-1"></ev-view>
  </section>
</div>`;
const view = document.getElementById("view");
const filtersEl = document.getElementById("filters");
const split = document.getElementById("split");
const detail = split.querySelector("ev-detail");
split.querySelector("aside").setAttribute("aria-label", t("page.details"));

// ---- the data: the observer's view, replaced when the store changed ---------------------------------------------
let data = { projects: [] }, runs = [], byKey = new Map(), options = {};
let answered = false;  // whether the observer has given a view yet (until then, an empty view says nothing)
// The filter values on offer: those present in the data, in their declared order.
function present(f) {
  if (f.id === "project") return data.projects.map((p, i) => ({ value: p.id, label: projectName(p, i + 1) }));
  const all = f.domain ? statesOf(f.domain) : catalogue[f.values];
  const seen = new Set(f.of === "run" ? runs.map((r) => r[f.field]) : runs.flatMap((r) => r.claims.map((c) => c[f.field])));
  return all.filter((v) => seen.has(v)).map((v) => ({ value: v, label: optionLabel(f, v) }));
}
function setData(v) {
  answered = true;
  data = v;
  runs = data.projects.flatMap((p) => p.runs);
  byKey = new Map(runs.map((r) => [r.key, r]));
  const next = Object.fromEntries(FILTERS.filter((f) => !f.flag).map((f) => [f.id, present(f)]));
  // The form is rebuilt only when what it offers changed, so a poll never takes focus from it.
  if (JSON.stringify(next) !== JSON.stringify(options)) { options = next; filtersEl.data = { filters: FILTERS, options }; }
}

// ---- the URL: the filters (app/url.js); run, claim or project for the selection ---------------------------------
const choiceOf = (q) => choiceFrom(q, FILTERS, options);
function save(push = false) {
  const u = `?${params}`;
  if (push) history.pushState(null, "", u); else history.replaceState(null, "", u);
}

// ---- what is selected, and its detail ---------------------------------------------------------------------------
let shown = null;  // the filtered data the view draws
const href = (runKey, claimId) => `?${new URLSearchParams({ data: source, run: runKey, ...(claimId ? { claim: claimId } : {}) })}`;
// The panel stays open on a wide screen, so clicking only changes what it shows and nothing in the view moves
// (R6); with nothing selected it says what to click. On a narrow screen it opens only for a selection.
const wide = () => !split.hasAttribute("narrow");
function open(d) { detail.data = d; split.setAttribute("open", ""); split.querySelector("aside").scrollTop = 0; }
function idle() { detail.data = null; split.toggleAttribute("open", wide()); }
new MutationObserver(() => { if (!detail.data) idle(); }).observe(split, { attributes: true, attributeFilter: ["narrow"] });
function mark(sel) {
  for (const k of ["project", "run", "claim"]) if (sel?.[k]) view.setAttribute(k, sel[k]); else view.removeAttribute(k);
  for (const k of ["project", "run", "claim"]) if (sel?.[k]) params.set(k, sel[k]); else params.delete(k);
}
// A run's detail: shown at once with what is already known, then again when the run's document arrives (unless
// something else was selected meanwhile).
let ticket = 0;
function withDetail(r, build) {
  const mine = ++ticket, have = client.cached(r);
  open(build(have));
  if (!have) client.detail(r).then((d) => { if (d && mine === ticket) open(build(d)); }, () => status(STATUS.invalid));
}
const inView = (key) => shown.projects.some((p) => p.runs.some((r) => r.key === key));
/** Whether the view shows what `sel` names. */
function visible(sel) {
  if (!sel.run) return shown.projects.some((p) => p.id === sel.project);
  const r = shown.projects.flatMap((p) => p.runs).find((x) => x.key === sel.run);
  return !!r && (!sel.claim || r.claims.some((c) => c.id === sel.claim));
}
const selection = () => ({ project: params.get("project"), run: params.get("run"), claim: params.get("claim") });
/** Select a project, run or claim (by URL-style fields), if it is in the view; returns whether it was. */
function select(sel) {
  const r = sel.run && byKey.get(sel.run), c = sel.claim && r?.claims.find((x) => x.id === sel.claim);
  const pi = sel.project ? data.projects.findIndex((p) => p.id === sel.project) : -1;
  if (c && visible(sel)) {
    mark({ run: r.key, claim: c.id }); withDetail(r, (d) => claimDetail(r, c, href, d));
  } else if (r && !sel.claim && visible(sel)) {
    mark({ run: r.key }); withDetail(r, (d) => runDetail(r, d));
  } else if (pi >= 0 && !sel.run && visible(sel)) {
    mark({ project: sel.project }); ticket++; open(projectDetail(data.projects[pi], pi + 1));
  } else return false;
  return true;
}
function deselect() { mark(null); ticket++; idle(); }
/** What the selection's detail depends on: it is redrawn after a poll only when this changed. */
function signature(sel) {
  if (sel.run) return byKey.get(sel.run)?.version ?? null;
  const p = sel.project && data.projects.find((x) => x.id === sel.project);
  return p ? JSON.stringify([p.name, p.repo, p.runs.map((r) => r.version)]) : null;
}

view.addEventListener("ev-open", (e) => {
  const d = e.detail;
  select(d.type === "claim" ? { run: d.run, claim: d.id } : d.type === "run" ? { run: d.run } : { project: d.id });
  save();
});
// Inside the panel: a run's history step, one of its changed files, or a link to another run or claim.
detail.addEventListener("ev-select", (e) => {
  const r = byKey.get(params.get("run")), d = r && client.cached(r), s = d?.steps.find((x) => x.n === e.detail.step);
  if (s) { ticket++; open(stepDetail(r, s, d.steps.length)); }
});
detail.addEventListener("ev-open", (e) => {
  const r = byKey.get(params.get("run")), f = r && client.cached(r)?.stale.find((x) => x.path === e.detail.path);
  if (e.detail.type === "file" && f) { ticket++; open(fileDetail(f)); }
});
detail.addEventListener("click", (e) => {
  const a = e.target.closest('a[href^="?"]');
  if (!a) return;
  const q = new URLSearchParams(a.getAttribute("href"));
  e.preventDefault();
  if (!inView(q.get("run"))) filter({});  // the link leads out of the filtered view: show everything again
  if (select({ run: q.get("run"), claim: q.get("claim") })) save(true);
});
// Esc or the close button: the selection goes, the panel stays (wide) and focus goes back to the mark it was about
// when it was in the panel.
split.addEventListener("ev-panel", (e) => {
  if (e.detail.open) return;
  const back = view.querySelector("[data-selected]"), a = document.activeElement;
  const lost = !a || a === document.body || split.lastElementChild.contains(a);
  if (detail.data) { deselect(); save(); } else idle();
  if (lost) back?.focus();
});

// ---- the filters ------------------------------------------------------------------------------------------------
let choice = {};
function filter(next) {
  choice = next;
  shown = apply(data, choice, FILTERS);
  // An empty store says so; an empty filter result says that.
  view.data = { ...shown, empty: runs.length ? "view.empty" : answered ? `view.empty.${source}` : "view.empty.offline" };
  document.getElementById("key").data = showing(shown.projects, statesOf);
  filtersEl.choice = choice;
  filtersEl.counts = shown.counts;
  writeChoice(params, choice, FILTERS);
}
filtersEl.addEventListener("ev-filter", (e) => {
  filter(e.detail.choice);
  // A selection the filters removed goes with it (R5).
  const sel = selection();
  if ((sel.project || sel.run) && !select(sel)) deselect();
  save();
});
window.addEventListener("popstate", () => {
  for (const k of [...params.keys()]) params.delete(k);
  for (const [k, v] of new URLSearchParams(location.search)) params.set(k, v);
  filter(choiceOf(params));
  if (!select(selection())) deselect();
});

// ---- polling ----------------------------------------------------------------------------------------------------
/** A changed store: new data, the same filters (minus values no longer on offer), the selection kept if it is
 * still there; its detail is redrawn only if what it shows changed. */
function refresh(v) {
  const sel = selection(), before = signature(sel);
  setData(v);
  filter(choiceOf(params));
  if (sel.project || sel.run) {
    // Unchanged and still showing: the panel stays as it is (its scroll, an open step); the view keeps the mark.
    if (!(visible(sel) && signature(sel) === before) && !select(sel)) deselect();
  }
  save();
}
async function poll() {
  const r = await client.view();
  status(STATUS[r.status] ?? null);
  if (r.status === "changed") refresh(r.view);
  setTimeout(poll, catalogue.poll_ms);
}

const first = await client.view();
status(STATUS[first.status] ?? null);
if (first.status === "changed") setData(first.view);
filter(choiceOf(params));
const sel0 = selection();
if (!(sel0.project || sel0.run) || !select(sel0)) deselect();
save();
setTimeout(poll, catalogue.poll_ms);
document.body.dataset.ready = "1";
