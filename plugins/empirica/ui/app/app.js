// The app: a list of runs under the core's attention groups, and one run's workspace beside it. Page code wires the
// elements to the observer's answers and moves the selection (a run, and optionally one of its claims: the URL's
// only selections); it decides nothing about a run (contract empirica-ui-view/v1, through app/client.js).
// The app asks every `poll_ms` whether the store changed (not while the tab is hidden, and later after a failed
// answer) and redraws only what changed; a run's document (the selected one's and its other attempts') is fetched when
// the run is selected, and its request state lives in app/details.js, which asks again when it fails and rests while hidden.
import "../elements/index.js";
import { clip, clock, plain, t } from "../elements/core/element.js";
import { backoff, Poller } from "./poll.js";
import { NONE, parse, resolve, same, up, write } from "./route.js";
import { attempts as attemptsOf, groups, search } from "./search.js";
import { Client } from "./client.js";
import { Details } from "./details.js";
import catalogue from "../catalogue.json" with { type: "json" };

// The colour scheme follows the system.
const dark = matchMedia("(prefers-color-scheme: dark)");
const scheme = () => document.documentElement.classList.toggle("dark", dark.matches);
scheme();
dark.addEventListener("change", scheme);

const params = new URLSearchParams(location.search);
const source = catalogue.data_sources.includes(params.get("data")) ? params.get("data") : "store";
const client = new Client(source);
const $ = (id) => document.getElementById(id);

document.title = t("page.title");
$("bar").innerHTML = `<div class="flex flex-wrap items-center justify-between gap-x-l gap-y-xs">
  <div class="flex flex-col gap-3xs"><span class="text-s font-semibold">${t("app.title")}</span><span class="text-xs text-muted-foreground">${t("app.readonly")}</span></div>
  <div class="flex flex-wrap items-center gap-l">
    <span id="status" class="text-xs text-muted-foreground" aria-live="polite"></span>
    <label class="flex items-center gap-xs text-xs text-muted-foreground">${t("app.data")}
      <select id="source" class="select" data-size="sm">${catalogue.data_sources.map((k) => `<option value="${k}" ${k === source ? "selected" : ""}>${t(`app.data.${k}`)}</option>`).join("")}</select></label>
  </div>
</div>`;
$("source").addEventListener("change", (e) => { location.search = new URLSearchParams({ data: e.target.value }); });
$("list-title").textContent = t("list.title");
$("search").placeholder = t("search.placeholder");
$("search").setAttribute("aria-label", t("search.label"));
$("search").value = params.get("q") ?? "";

// ---- the data: the observer's view, replaced when the store changed ------------------------------------------------
let data = { projects: [], coverage: null }, byKey = new Map(), projectOf = new Map();
let answered = false;  // whether the observer has given a view yet (until then, an empty list says nothing)
function setData(view) {
  answered = true;
  data = view;
  const rows = view.projects.flatMap((p) => p.runs.map((r) => [r, p]));
  byKey = new Map(rows.map(([r]) => [r.key, r]));
  projectOf = new Map(rows.map(([r, p]) => [r.key, p]));
}
const attemptsFor = (row) => attemptsOf(projectOf.get(row.key), row, byKey);

// ---- the URL -------------------------------------------------------------------------------------------------------
let current = NONE;  // the selection
function href(sel) {
  const q = new URLSearchParams(params);
  write(q, sel);
  return `?${q}`;
}
const save = (push) => { const u = `?${params}`; if (push) history.pushState(null, "", u); else history.replaceState(null, "", u); };

// ---- the list --------------------------------------------------------------------------------------------------------
function paintList() {
  const q = $("search").value;
  const found = search(data.projects, q);
  $("runs").data = {
    groups: groups(found.entries), href, selected: current.run, attemptsOf: ({ row }) => attemptsFor(row).length,
    empty: !answered ? "list.empty.offline" : data.projects.length ? "list.empty.search" : `list.empty.${source}`,
  };
  const c = data.coverage, unread = c ? c.unreadable + c.partial : 0;
  $("coverage").textContent = [unread ? t("coverage.unread", { n: unread }) : "", found.unsearched ? t("coverage.unsearched", { n: found.unsearched }) : ""].filter(Boolean).join(" · ");
}

// ---- the workspace ---------------------------------------------------------------------------------------------------
let painted = null;
const NOTICE = { missing: "detail.missing", offline: "detail.offline", error: "detail.error", invalid: "detail.invalid" };
const details = new Details({ client, retries: catalogue.retries, base: catalogue.retry_ms, cap: catalogue.backoff_cap_ms,
  hidden: () => document.hidden, onChange: () => { if (current.run !== null) paintRun(); } });
function context() {
  const row = byKey.get(current.run);
  const mine = details.state(row);
  return { row, project: projectOf.get(row.key), detail: mine.detail ?? null, notice: mine.status === "failed" ? NOTICE[mine.failure] : null,
    claim: current.claim, href,
    attempts: attemptsFor(row).map((r) => { const s = details.state(r); return { row: r, detail: s.detail ?? null, request: s.status === "failed" ? s.failure : s.status === "ok" ? null : s.status }; }) };
}
/** What the workspace shows depends on this; it is redrawn only when it changed (a poll that changes nothing leaves it alone). */
const signature = () => {
  const c = context();
  return JSON.stringify([c.row.version, c.notice, c.claim, !!c.detail, c.attempts.map((a) => [a.row.key, a.row.version, !!a.detail, a.request])]);
};
function paintRun() {
  if (current.run === null) { $("run").data = null; painted = null; document.title = t("page.title"); return; }
  const sig = signature();
  if (sig === painted) return;
  painted = sig;
  const c = context();
  $("run").data = c;
  document.title = plain("page.title.run", { goal: clip(c.row.goal ?? "", 60) });
}
/** Show the selection: the workspace for a run (asking for its document and its other attempts', whatever is not here), the list alone for none. */
function show() {
  document.body.toggleAttribute("data-selected", current.run !== null);
  paintList();
  painted = null;
  paintRun();
  wantAll();
}
function wantAll() {
  if (current.run === null) return;
  const row = byKey.get(current.run);
  for (const r of [row, ...attemptsFor(row).filter((x) => x.key !== row.key)]) details.want(r);
}
/** Focus goes where the user was: a run's row, a claim's summary, the workspace's title. */
function focusOn(what) {
  const el = what === "title" ? $("run").querySelector("[data-title]")
    : what.claim ? $("run").querySelector(`summary[data-claim="${CSS.escape(what.claim)}"]`)
      : $("runs").querySelector(`a[data-run="${CSS.escape(what.run)}"]`);
  (el?.checkVisibility() ? el : $("run").querySelector("[data-title]"))?.focus({ preventScroll: false });
}
/** Move the selection. A user's move adds one history entry (unless it changes nothing); a poll, a restore or a correction replaces. */
function navigate(to, { push = true, focus = null } = {}) {
  const sel = resolve(to, byKey);
  const moved = !same(sel, current);
  const left = current;
  current = sel;
  write(params, sel);
  show();
  save(push && moved);
  if (focus === "title") focusOn("title");
  else if (focus === "left") focusOn(left.claim && sel.run === left.run ? left : { run: left.run });
}

// ---- events ----------------------------------------------------------------------------------------------------------
document.addEventListener("click", (e) => {
  if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const summary = e.target.closest("summary[data-claim]");
  if (summary) {  // a claim opens in place by selecting it, so the URL and Back follow
    e.preventDefault();
    navigate(parse(new URLSearchParams(summary.dataset.href.slice(1))), { focus: null });
    focusOn({ claim: summary.dataset.claim });
    return;
  }
  const again = e.target.closest("[data-retry]");
  if (again) {  // the selected run's document, or every other attempt's that failed
    const row = byKey.get(current.run);
    for (const r of again.dataset.retry === "family" ? attemptsFor(row).filter((x) => x.key !== row.key && details.state(x).status === "failed") : [row]) details.retry(r);
    return;
  }
  const a = e.target.closest('a[href^="?"]');
  if (!a) return;
  e.preventDefault();
  const to = parse(new URLSearchParams(a.getAttribute("href")));
  const back = a.hasAttribute("data-back"), left = current;
  navigate(to, { focus: back ? null : "title" });
  if (back) focusOn({ run: left.run });
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || e.defaultPrevented || current.run === null || e.target.closest("input, select, textarea")) return;
  navigate(up(current), { focus: "left" });
});
$("search").addEventListener("input", (e) => {
  if (e.target.value) params.set("q", e.target.value); else params.delete("q");
  save(false);
  paintList();
});
window.addEventListener("popstate", () => {
  for (const k of [...params.keys()]) params.delete(k);
  for (const [k, v] of new URLSearchParams(location.search)) params.set(k, v);
  $("search").value = params.get("q") ?? "";
  navigate(parse(params), { push: false });
});

// ---- polling ---------------------------------------------------------------------------------------------------------
const STATUS = { offline: "app.status.offline", error: "app.status.error", missing: "app.status.missing", invalid: "app.status.invalid", page: "app.status.page" };
/** What the header says: when the observer last answered, and if the last ask failed, that it did. */
function status(key) {
  const checked = client.checked === null ? "" : t("app.checked", { at: clock(client.checked) });
  $("status").textContent = [key ? t(key) : "", checked].filter(Boolean).join(" · ");
}
/** A changed store: the new rows, the same search and selection (a selection that left the store falls back). */
function refresh(view) {
  setData(view);
  const sel = resolve(current, byKey);
  if (!same(sel, current)) { navigate(sel, { push: false }); return; }
  details.keep(byKey.keys());
  paintList();
  paintRun();
  wantAll();
}
async function tick() {
  try {
    const r = await client.view();  // reports every outcome as a value; it never throws
    status(STATUS[r.status] ?? null);
    if (r.status === "changed") refresh(r.view);
    return r.status === "changed" || r.status === "same";
  } catch (e) {
    console.error(e);
    status(STATUS.page);  // a bug in drawing, not in the observer's answer; the last good view stays on screen
    return false;
  }
}
const poller = new Poller({ tick, interval: catalogue.poll_ms, cap: catalogue.backoff_cap_ms, hidden: () => document.hidden });
document.addEventListener("visibilitychange", () => { if (!document.hidden) { poller.wake(); details.wake(); } });

const first = await client.view();
status(STATUS[first.status] ?? null);
if (first.status === "changed") setData(first.view);
current = resolve(parse(params), byKey);
write(params, current);
show();
save(false);
poller.start();
document.body.dataset.ready = "1";
