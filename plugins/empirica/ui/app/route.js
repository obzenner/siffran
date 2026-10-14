// The URL is the state (UI.md, The URL): the view, the filters, the period, the search, the open run and its claim, and
// the words toggle. Pure: no DOM, no history. A place is a value; `href(place)` is the link to it and `parse(params)` the
// place a link holds, so Back and Forward restore exactly what a person saw.
//
//   ?view=universe|inbox|work   which screen (the home is the Universe; the Inbox is the switch's other side)
//   ?zoom=PROJECT      the Universe zoomed on one project's island (a run's zoom is its open run)
//   ?hl=FLAG           the Universe's highlight chip: the runs holding that published flag stand out
//   ?standing=&ask=&project=&host=&mode=&audit=&flag=   the published facets a list is filtered by
//   ?period=7|30|all   the value strip's window and the list's (`since` below overrides it)
//   ?since=EPOCH       runs updated since this moment (the calendar's day)
//   ?q=TEXT            the server's search (goal, claim text, project, session)
//   ?run=ID&claim=ID   the open run (its compact id) and the claim opened in it
//   ?words=1           words beside every glyph

import catalogue from "../catalogue.json" with { type: "json" };
import enums from "../enums.json" with { type: "json" };

export const FILTERS = catalogue.filters;
export const VIEWS = ["universe", "inbox", "work"];
const HOME = VIEWS[0];
export const PERIODS = catalogue.periods;

/** @returns the place a URL holds, every part defaulted */
export function parse(params, defaults = { period: catalogue.default_period }) {
  const view = VIEWS.includes(params.get("view")) ? params.get("view") : HOME;
  const period = PERIODS.includes(params.get("period")) ? params.get("period") : defaults.period;
  const since = /^\d+$/.test(params.get("since") ?? "") ? Number(params.get("since")) : null;
  const hl = enums.flag.includes(params.get("hl")) ? params.get("hl") : null;
  const place = { view, period, since, q: (params.get("q") ?? "").trim(), run: params.get("run") || null, claim: null, words: params.get("words") === "1",
    zoom: params.get("zoom") || null, hl, filters: {} };
  if (place.run) place.claim = params.get("claim") || null;
  for (const name of FILTERS) if (params.get(name)) place.filters[name] = params.get(name);
  return place;
}

/** Put `place` into `params`, leaving out what is the default so a link stays short. */
export function write(params, place, defaults = { period: catalogue.default_period }) {
  const set = (k, v) => { if (v) params.set(k, v); else params.delete(k); };
  set("view", place.view === HOME ? null : place.view);
  set("period", place.period === defaults.period ? null : place.period);
  set("since", place.since === null ? null : String(place.since));
  set("q", place.q);
  set("run", place.run);
  set("claim", place.run ? place.claim : null);
  set("words", place.words ? "1" : null);
  set("zoom", place.zoom);
  set("hl", place.hl);
  for (const name of FILTERS) set(name, place.filters[name]);
  return params;
}

/** The link to `place` from `base` (the search of the current URL, kept for what the place does not name, such as the data source). */
export function href(place, base = new URLSearchParams(), defaults) {
  const params = new URLSearchParams(base);
  for (const k of ["view", "period", "since", "q", "run", "claim", "words", "zoom", "hl", ...FILTERS]) params.delete(k);
  write(params, place, defaults);
  const text = params.toString();
  return text ? `?${text}` : "?";
}

/** `place` with some parts changed. A filter change leaves the open run; a list the person did not ask for is the work view. */
export const move = (place, changes) => ({ ...place, ...changes, filters: { ...place.filters, ...(changes.filters ?? {}) } });
/** The work view, filtered by `filters` (the rest of `place` kept), nothing open. */
export const work = (place, filters = {}) => ({ ...place, view: "work", q: "", run: null, claim: null, filters });

/** The runs query of `place`: its filters, and `since` when a calendar day was chosen. The period is not here: it is the value
 * strip's window, and a list counted in it would disagree with the rail beside it. */
export function query(place) {
  return place.since === null ? { ...place.filters } : { ...place.filters, since: String(place.since) };
}
/** The window the summary is asked within (seconds; null for all time): the chosen day, else the period. `nowMs` is the clock,
 * taken at the start of its hour (the grain the server counts a window in), so one question is asked all hour and a 304 can
 * answer it. */
export function windowOf(place, nowMs) {
  if (place.since !== null) return place.since;
  return place.period === "all" ? null : Math.floor(nowMs / 3_600_000) * 3600 - Number(place.period) * 86400;
}

export const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
/** Where Esc leads: an open claim to its run, a run to the list (in the Universe, to the island it sits on), an island to the
 * whole store. */
export const up = (place) => (place.claim ? { ...place, claim: null } : place.run ? { ...place, run: null }
  : place.view === HOME && place.zoom ? { ...place, zoom: null } : place);
/** The same place on the switch's other side: only the view changes, so the Universe's zoom, its highlight and the open run
 * come back when the person switches back (and Back restores either). */
export const switchTo = (place, view) => ({ ...place, view });
