// The app: the Universe home, the inbox on the switch's other side, the work list and the run sheet, wired to the observer's
// answers. Page code moves the place (the URL: view, zoom, highlight, filters, period, search, open run and claim), asks for
// what the place needs and hands it to the elements; it
// decides nothing about a run (contract empirica-ui-view/v1, through app/client.js). The store summary is asked every
// `poll_ms` (not while the tab is hidden, and later after a failed answer); when it changed, what is on screen is read again
// where it stands. A run's sheet is asked for when it is opened, and its request state lives in app/sheets.js.
import "../elements/index.js";
import { clip, clock, esc, has, plain, stateOf, t } from "../elements/core/element.js";
import enums from "../enums.json" with { type: "json" };
import { Keys } from "./keys.js";
import { Walk } from "./list.js";
import { Poller } from "./poll.js";
import { Sheets } from "./sheets.js";
import { Client } from "./client.js";
import { href as hrefOf, move, parse, query, same, switchTo, up, windowOf, work, write } from "./route.js";
import catalogue from "../catalogue.json" with { type: "json" };

// The colour scheme follows the system.
const dark = matchMedia("(prefers-color-scheme: dark)");
const scheme = () => document.documentElement.classList.toggle("dark", dark.matches);
scheme();
dark.addEventListener("change", scheme);

const DEFAULTS = { period: catalogue.default_period };
const base = new URLSearchParams(location.search);  // what a place does not name (the data source) stays in every link
const source = catalogue.data_sources.includes(base.get("data")) ? base.get("data") : "store";
const client = new Client(source);
const $ = (id) => document.getElementById(id);
const keys = new Keys();
const now = () => Date.now();

let place = parse(new URLSearchParams(location.search), DEFAULTS);
let summary = null;           // the store summary within the place's window
let answered = false;         // whether the observer has given a summary yet (until then, an empty home says nothing)
let loaded = new Map();       // the inbox's groups: standing -> its first rows
let walk = null;              // the work list's data, for the place's filters or search
let walkKey = "";
let status = null;
let goals = { project: null, map: new Map() };  // the goals of the zoomed island's first runs, for the Universe's words
let served = {};              // what each data source serves, by the server's word (/api/about)

const linkTo = (changes) => hrefOf(move(place, changes), base, DEFAULTS);
const placeHref = (p) => hrefOf(p, base, DEFAULTS);
/** The link to the work view filtered by `filters` (the rest of the place's window kept). */
const go = (filters) => placeHref({ ...work(place, filters), since: null });

// ---- the header ---------------------------------------------------------------------------------------------------------
document.title = t("page.title");
// the brand: the favicon's own file (the page's icon link, its one source), then the word
$("bar").innerHTML = `<div class="flex flex-wrap items-center gap-x-m gap-y-xs">
  <a class="flex items-baseline gap-xs" href="${placeHref({ ...parse(new URLSearchParams(), DEFAULTS) })}" data-home title="${t("app.readonly")}"><img class="size-m self-center" src="${esc(document.querySelector('link[rel="icon"]')?.href)}" alt=""><span class="text-s font-medium">${t("app.brand")}</span></a>
  <nav class="flex items-center gap-3xs rounded-m border border-border p-3xs text-xs" aria-label="${t("switch.label")}" id="switch"></nav>
  <input id="search" type="search" class="input max-w-prose flex-1" autocomplete="off" placeholder="${t("search.placeholder")}" aria-label="${t("search.label")}" data-size="sm">
  <span id="status" class="ml-auto text-xs text-muted-foreground" aria-live="polite"></span>
  <label class="flex items-center gap-xs text-xs text-muted-foreground">${t("app.data")}
    <select id="source" class="select" data-size="sm" aria-label="${t("app.data")}"></select></label>
  <button id="words" type="button" class="btn" data-variant="outline" data-size="sm" aria-pressed="false" title="${t("words.title")}">${t("words.button")}</button>
</div>`;

/** The data menu, each source in the words of what the server says it serves (a generated demo store is not "your store"). */
function paintSources() {
  const word = (k) => (k === "store" && served.store === "generated" ? t("app.data.generated") : t(`app.data.${k}`));
  $("source").innerHTML = catalogue.data_sources.map((k) => `<option value="${k}" ${k === source ? "selected" : ""}>${word(k)}</option>`).join("");
}
paintSources();
client.about().then((s) => { served = s; paintSources(); });
/** The header's switch, Universe ⇄ Inbox: the same place on the other side (the zoom, the highlight and the open run kept). */
function paintSwitch() {
  const side = (view) => {
    const on = place.view === view || (view === "inbox" && place.view === "work");
    return `<a class="rounded-s px-xs py-3xs ${on ? "bg-muted font-medium" : "text-muted-foreground hover:bg-muted"}" href="${placeHref(switchTo(place, view))}" ${on ? 'aria-current="page"' : ""} data-switch="${view}">${t(`switch.${view}`)}</a>`;
  };
  $("switch").innerHTML = catalogue.switch.map(side).join("");
}

$("source").addEventListener("change", (e) => { location.search = new URLSearchParams({ data: e.target.value }); });

// ---- what the place needs ---------------------------------------------------------------------------------------------------
/** The runs query of the work view; the home's groups are every run (as the rail counts them). */
const windowQuery = () => (place.view === "work" ? query(place) : {});
const asked = () => windowOf(place, now());

async function loadSummary() {
  const r = await client.summary(asked());
  status = r.status === "changed" || r.status === "same" ? null : r.status;
  if (r.status === "changed" || r.status === "same") {
    const changed = r.status === "changed" || summary === null || summary.token !== r.summary.token;
    summary = r.summary; answered = true;
    return changed;
  }
  return false;
}

async function loadGroups() {
  if (!summary) return;
  const mine = summary.token, want = summary.marks.standings.filter((s) => (summary.facets.standing[s] ?? 0) > 0);
  const results = await Promise.all(want.map(async (s) => {
    const r = await client.runs({ ...windowQuery(), standing: s }, null, s === catalogue.needs_standing ? catalogue.needs_cap : catalogue.group_cap);
    return [s, r.status === "ok" ? r.page.rows : []];
  }));
  if (summary?.token === mine && place.view === "inbox") { loaded = new Map(results); paintMain(); }
}

/** The goals of the Universe's zoomed island (its first page of runs), so its marks and mirror have words, not only shapes. */
async function loadGoals() {
  const project = universeProject();
  if (!project || goals.project === project) return;
  goals = { project, map: new Map() };
  const r = await client.runs({ project }, null, catalogue.universe.mirror_cap);
  if (r.status === "ok" && goals.project === project) { goals.map = new Map(r.page.rows.map((x) => [x.id, x.goal])); paintMain(); }
}
/** The project the Universe is zoomed on: the place's zoom, or the open run's project. */
function universeProject() {
  if (place.view !== "universe" || !summary) return null;
  const i = place.run ? summary.marks.id.indexOf(place.run) : -1;
  return i >= 0 ? summary.projects[summary.marks.project[i]].id : place.zoom;
}

/** The work list for the place: a search, or the filtered runs. A new list is begun whenever either changes. */
function ensureWalk(force = false) {
  const key = JSON.stringify([place.q, place.q ? null : windowQuery()]);
  if (walk && key === walkKey && !force) return;
  const reread = walk && key === walkKey ? Math.max(walk.rows.length, 1) : 1;
  if (key !== walkKey) walk?.dispose();
  const next = key === walkKey ? walk : new Walk({
    page: catalogue.page,
    request: (cursor) => (place.q ? client.search(place.q, cursor, catalogue.page) : client.runs(windowQuery(), cursor, catalogue.page)),
  });
  walkKey = key; walk = next;
  next.start(reread).then(() => { paintMain(); });
  paintMain();
}

// ---- painting ---------------------------------------------------------------------------------------------------------------
const sheets = new Sheets({ client, retries: catalogue.retries, base: catalogue.retry_ms, cap: catalogue.backoff_cap_ms,
  hidden: () => document.hidden, onChange: () => paintSheet() });
const NOTICE = { missing: "detail.missing", offline: "detail.offline", error: "detail.error", invalid: "detail.invalid" };

const rowCtx = () => ({ href: (id) => linkTo({ run: id, claim: null }), now: now(), open: place.run, here: here });
let here = null;  // the row the keyboard is on

/** The filters in force as chips, each a link that removes it. */
function chips() {
  if (place.q) return [{ label: t("work.search", { q: place.q }), href: placeHref({ ...place, q: "", run: null }) }];
  return [...Object.entries(place.filters).map(([name, value]) => ({ label: t("work.filter", { name: t(`filter.${name}`), value: filterWord(name, value) }), href: placeHref({ ...place, filters: Object.fromEntries(Object.entries(place.filters).filter(([k]) => k !== name)), run: null }) })),
    ...(place.since !== null ? [{ label: t("work.since", { when: new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(place.since * 1000)) }), href: placeHref({ ...place, since: null, run: null }) }] : [])];
}
/** A filter's value as the person reads it: the contract's word for it, or the value itself when the catalogue has none. */
const filterWord = (name, value) => {
  if (name === "project") return summary?.projects.find((p) => p.id === value)?.label ?? value;
  if (name === "standing" || name === "audit") return plain(`${stateOf(name, value).copy}.label`);
  if (name === "host") return has(`host.${value}`) ? plain(`host.${value}`) : value;
  if (name === "ask") return enums.ask.includes(value) ? plain(`ask.${value}`) : value;
  if (name === "mode") return enums.mode.includes(value) ? plain(`mode.${value}`) : value;
  return enums.flag.includes(value) ? plain(`flag.${value}`) : value;
};

function paintMain() {
  const inbox = place.view === "inbox", universe = place.view === "universe";
  $("inbox").hidden = !inbox; $("work").hidden = place.view !== "work"; $("universe").hidden = !universe;
  $("main").classList.toggle("overflow-y-auto", !universe);
  paintSwitch();
  if (universe) {
    $("universe").data = summary ? { summary, place, sheet: place.run ? sheets.state(place.run).sheet : null, goals: goals.map, since: asked(), nowMs: now(),
      href: (changes) => linkTo(changes), list: (project) => go({ project }), needsAll: go({ standing: catalogue.needs_standing }),
      empty: answered ? `list.empty.${source}` : "list.empty.offline" } : null;
  } else if (inbox) {
    $("inbox").data = summary ? { summary, place, loaded, ctx: { ...rowCtx(), all: (s) => go({ standing: s }) }, go, nowMs: now(), empty: answered ? `list.empty.${source}` : "list.empty.offline",
      periodHref: (key) => placeHref({ ...place, period: key, since: null, run: null }),
      statGo: (filters) => placeHref({ ...work(place, filters), since: windowOf(place, now()) }),
      calendarGo: (filters, since) => placeHref({ ...work(place, filters), since }) } : null;
  } else if (walk) {
    $("work").data = { walk, chips: chips(), ctx: rowCtx(), empty: place.q ? "list.empty.search" : "list.empty.filter" };
  }
  $("rail").data = summary ? { summary, place, go } : null;
  const c = summary?.coverage;
  const unread = c ? c.unreadable + c.partial : 0;
  $("status").textContent = [status ? t(`app.status.${status}`) : "", unread ? t("coverage.unread", { n: unread }) : "", client.checked === null ? "" : t("app.checked", { at: clock(client.checked) })].filter(Boolean).join(" · ");
}

let paintedSheet = "";
function paintSheet() {
  const open = place.run !== null;
  document.body.toggleAttribute("data-sheet", open);
  if (!open) { $("sheet").data = null; paintedSheet = ""; document.title = t("page.title"); return; }
  const s = sheets.state(place.run);
  const notice = s.status === "failed" ? NOTICE[s.failure] : null;
  const sig = JSON.stringify([place.run, place.claim, s.status, s.failure, s.sheet?.version, s.sheet?.attempts.map((a) => a.version)]);
  if (sig === paintedSheet) return;
  paintedSheet = sig;
  if (!s.sheet) { $("sheet").data = { pending: true, notice }; return; }
  if (place.view === "universe") paintMain();
  const { row, attempts } = s.sheet;
  const project = summary?.projects.find((p) => p.id === row.project) ?? null;
  $("sheet").data = { row, project, detail: s.sheet, attempts, notice, claim: place.claim, now: now(), href: (changes) => linkTo(changes) };
  document.title = plain("page.title.run", { goal: clip(row.goal ?? "", 60) });
}

function paintAll() {
  document.body.dataset.view = place.view;
  document.body.toggleAttribute("data-words", place.words);
  $("words").setAttribute("aria-pressed", String(place.words));
  if (document.activeElement !== $("search")) $("search").value = place.q;
  paintMain();
  paintSheet();
}

// ---- moving -----------------------------------------------------------------------------------------------------------------
const save = (push) => {
  const params = new URLSearchParams(base);
  write(params, place, DEFAULTS);
  const url = params.toString() ? `?${params}` : location.pathname;
  if (push) history.pushState(null, "", url); else history.replaceState(null, "", url);
};

/** Move to `to`. A person's move adds one history entry (unless it changes nothing); a poll, a restore or a correction replaces. */
async function navigate(to, { push = true } = {}) {
  const before = place;
  place = to;
  const reload = asked() !== windowOf(before, now());
  save(push && !same(before, place));
  paintAll();
  if (reload) { await loadSummary(); paintAll(); }
  if (place.view === "inbox") loadGroups(); else if (place.view === "work") ensureWalk(); else loadGoals();
  if (place.run) { sheets.keep(place.run); sheets.want(place.run); paintSheet(); } else sheets.keep(null);
}

// ---- events -----------------------------------------------------------------------------------------------------------------
document.addEventListener("click", (e) => {
  if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const summaryEl = e.target.closest("summary[data-claim]");
  if (summaryEl) {  // a claim opens in place by selecting it, so the URL and Back follow
    e.preventDefault();
    navigate(parse(new URLSearchParams(summaryEl.dataset.href.slice(1)), DEFAULTS), { push: true });
    return;
  }
  if (e.target.closest("[data-close]")) { navigate({ ...place, run: null, claim: null }); $("main").querySelector("a[data-run]")?.focus(); return; }
  if (e.target.closest("[data-retry]")) { sheets.retry(place.run); return; }
  if (e.target.closest("[data-retry-list]")) { ensureWalk(true); return; }
  if (e.target.closest("#words")) { navigate({ ...place, words: !place.words }); return; }
  const a = e.target.closest('a[href^="?"]');
  if (!a) return;
  e.preventDefault();
  const to = parse(new URLSearchParams(a.getAttribute("href")), DEFAULTS);
  here = a.dataset.run ?? here;
  navigate(to);
});

document.addEventListener("keydown", (e) => {
  const cmd = keys.command(e);
  if (!cmd) return;
  if (cmd === "close") {
    if (e.defaultPrevented || e.target.closest?.("input")?.value) return;
    const to = up(place);
    if (!same(to, place)) navigate(to);
    return;
  }
  e.preventDefault();
  if (cmd === "search") $("search").focus();
  else if (cmd === "words") navigate({ ...place, words: !place.words });
  else if (cmd === "needs") navigate({ ...work(place, { standing: catalogue.needs_standing }), since: null });
  else step(cmd === "next" ? 1 : -1);
});

/** Move the keyboard to the neighbouring row of what is shown. */
function step(by) {
  if (place.view === "universe") return;  // the Universe's arrows move on its canvas
  const shown = place.view === "work" ? (walk?.rows ?? []) : [...loaded.values()].flat();
  if (!shown.length) return;
  const at = shown.findIndex((r) => r.id === here);
  const next = Math.min(shown.length - 1, Math.max(0, at < 0 ? 0 : at + by));
  here = shown[next].id;
  paintMain();
  const link = place.view === "work" ? $("work").reveal(next) : $("inbox").querySelector(`a[data-run="${here}"]`);
  if (link) { link.tabIndex = 0; link.focus(); }
  if (place.view === "work" && next >= shown.length - catalogue.overscan) walk.more().then(paintMain);
}

$("universe").addEventListener("ev-navigate", (e) => navigate(parse(new URLSearchParams(e.detail.slice(1)), DEFAULTS)));
$("work").addEventListener("ev-near-end", (e) => { if (e.detail) walk.more().then(paintMain); });
let typed = 0;
$("search").addEventListener("input", (e) => {
  clearTimeout(typed);
  typed = setTimeout(() => {
    const q = e.target.value.trim();
    navigate(q ? { ...work(place, {}), q, since: null } : { ...place, q: "" }, { push: false });
  }, catalogue.typed_ms);
});
window.addEventListener("popstate", () => navigate(parse(new URLSearchParams(location.search), DEFAULTS), { push: false }));

// ---- polling ------------------------------------------------------------------------------------------------------------------
async function tick() {
  try {
    const changed = await loadSummary();
    paintMain();
    if (changed) {
      if (place.view === "inbox") loadGroups(); else if (place.view === "work" && walk) { walk.dispose(); walkKey = ""; ensureWalk(); }
      else if (place.view === "universe") { goals = { project: null, map: goals.map }; loadGoals(); }
      if (place.run) sheets.refresh(place.run);
    }
    return status === null;
  } catch (e) {
    console.error(e);
    status = "page";  // a bug in drawing, not in the observer's answer; the last good view stays on screen
    return false;
  }
}
const poller = new Poller({ tick, interval: catalogue.poll_ms, cap: catalogue.backoff_cap_ms, hidden: () => document.hidden });
document.addEventListener("visibilitychange", () => { if (!document.hidden) { poller.wake(); sheets.wake(); } });

await loadSummary();
await navigate(place, { push: false });
poller.start();
document.body.dataset.ready = "1";
