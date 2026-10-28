// The app's data client for the observer server (adapters/observer/server.py, contract empirica-ui-view/v1).
// It asks for the store summary with its ETag (304 when nothing changed), in the viewer's time zone (`tz`), for pages of rows (`/api/runs`, `/api/search`)
// and for one run's sheet (`/api/run`, with its ETag). It reports every outcome as a value: nothing it returns throws. A
// reply that is not the contract (not JSON, the wrong `contract`, a missing required part) is `invalid`, never trusted.
// One request per run's sheet is ever in flight. A page asked with a cursor from another published view is `stale`
// (the server's 409): the caller starts the walk again. No DOM. Tested in tests/client.test.mjs.

import enums from "../enums.json" with { type: "json" };

const CONTRACT = "empirica-ui-view/v1";
const isObject = (x) => x !== null && typeof x === "object" && !Array.isArray(x);
const PATTERNS = Object.fromEntries(Object.entries(enums.patterns).map(([name, p]) => [name, new RegExp(p)]));
/** Whether `v` fits one field of a generated form (scripts/enums.mjs FORMS): its const, a value of its list, or a string
 * of its pattern. A form of any other shape fits nothing, so a form this check does not know refuses, never admits. */
const fits = (form, v) => ("const" in form ? v === form.const
  : "list" in form ? Array.isArray(enums[form.list]) && enums[form.list].includes(v)
    : "pattern" in form ? typeof v === "string" && PATTERNS[form.pattern]?.test(v) === true : false);
/** Whether `x` is exactly one generated form of `forms`: null where the contract allows it, else the form its `kind`
 * names, with exactly that form's keys (the generator checked that every one is required and no other is allowed). */
const exactly = (forms, x) => (x === null ? forms.nullable === true : isObject(x) && Object.hasOwn(forms.kinds, x.kind)
  && Object.keys(x).length === Object.keys(forms.kinds[x.kind]).length
  && Object.entries(forms.kinds[x.kind]).every(([field, form]) => Object.hasOwn(x, field) && fits(form, x[field])));
const validSpikeBackend = (x) => exactly(enums.spike_backend, x);

/** Whether `v` has a summary's required shape. The schema (summary.schema.json) is checked in the build; this is the cheap check at the boundary. */
export const validSummary = (v) => isObject(v) && v.contract === CONTRACT && typeof v.token === "string" && isObject(v.coverage)
  && isObject(v.facets) && isObject(v.aggregates) && isObject(v.marks) && isObject(v.calendar) && Array.isArray(v.projects) && Array.isArray(v.needs);
/** Whether `v` has a page's required shape. */
export const validPage = (v) => isObject(v) && v.contract === CONTRACT && typeof v.token === "string" && Array.isArray(v.rows)
  && typeof v.total === "number" && (v.next === null || typeof v.next === "string") && v.rows.every((r) => isObject(r) && typeof r.key === "string" && typeof r.id === "string");
/** Whether `v` has a sheet's required shape: a run's detail, its own row and every attempt's. */
export const validSheet = (v) => isObject(v) && v.contract === CONTRACT && typeof v.key === "string" && typeof v.version === "string"
  && validSpikeBackend(v.spike_backend) && isObject(v.row) && Array.isArray(v.attempts) && v.attempts.every((a) => isObject(a) && typeof a.key === "string");

export class Client {
  #fetch; #source; #now; #tz; #summaries = new Map(); #sheets = new Map(); #flying = new Map(); #checked = null;
  /** @param {string} source - one of catalogue.data_sources; @param {typeof fetch} fetcher; `tz`: the viewer's IANA time
   * zone, whose civil days the observer counts the calendar in (the browser's own by default). */
  constructor(source, fetcher = (...a) => fetch(...a), now = () => Date.now(), tz = Intl.DateTimeFormat().resolvedOptions().timeZone) {
    this.#source = source; this.#fetch = fetcher; this.#now = now; this.#tz = tz;
  }

  /** When the observer last gave a good answer (an unchanged one counts), in ms since the epoch; null before the first. A
   * failed answer does not move it, so the page can say how old what it shows is. */
  get checked() { return this.#checked; }

  /** The summary within `since` (seconds; null: all time). `same` carries the summary the caller already has (its ETag
   * matched). `missing`: the server does not serve this source; `offline`: no answer; `error`: a 5xx; `invalid`: not the contract.
   * @returns {Promise<{status:"changed"|"same",summary:object}|{status:"missing"|"offline"|"error"|"invalid"}>} */
  async summary(since = null) {
    const key = String(since), have = this.#summaries.get(key);
    const r = await this.#get(this.#url("/api/summary", { ...(since === null ? {} : { since }), ...(this.#tz ? { tz: this.#tz } : {}) }), have ? { "If-None-Match": have.etag } : {});
    if (!r) return { status: "offline" };
    if (r.status === 304 && have) { this.#checked = this.#now(); return { status: "same", summary: have.summary }; }
    const failed = this.#failure(r);
    if (failed) return failed;
    const summary = await r.json().catch(() => undefined);
    if (!validSummary(summary)) return { status: "invalid" };
    this.#summaries.set(key, { etag: r.headers.get("ETag"), summary });
    this.#checked = this.#now();
    return { status: have && have.summary.token === summary.token ? "same" : "changed", summary };
  }

  /** One page of rows for `filters` (the facets, and `since`), after `cursor`. `stale`: the cursor belongs to a published
   * view the store has moved on from (`token` is the current one): walk again from the first page.
   * @returns {Promise<{status:"ok",page:object}|{status:"stale",token:string}|{status:"missing"|"offline"|"error"|"invalid"|"refused"}>} */
  runs(filters = {}, cursor = null, limit = null) { return this.#page("/api/runs", { ...filters, ...(cursor ? { cursor } : {}), ...(limit ? { limit } : {}) }); }
  /** The same for the server's search: the runs whose goal, claim text, project or session holds every word of `q`. */
  search(q, cursor = null, limit = null) { return this.#page("/api/search", { q, ...(cursor ? { cursor } : {}), ...(limit ? { limit } : {}) }); }

  async #page(path, query) {
    const r = await this.#get(this.#url(path, query));
    if (!r) return { status: "offline" };
    if (r.status === 409) {
      const body = await r.json().catch(() => undefined);
      return isObject(body) && typeof body.token === "string" ? { status: "stale", token: body.token } : { status: "invalid" };
    }
    const failed = this.#failure(r);
    if (failed) return failed;
    const page = await r.json().catch(() => undefined);
    return validPage(page) ? { status: "ok", page } : { status: "invalid" };
  }

  /** The sheet of the run with compact id (or key) `id`: its detail, its row and every attempt's row. The server's ETag
   * keeps a revisit to one cheap question. Requests for one run are shared.
   * @returns {Promise<{status:"ok",sheet:object}|{status:"missing"|"offline"|"error"|"invalid"}>} */
  sheet(id) {
    if (!this.#flying.has(id)) this.#flying.set(id, this.#fetchSheet(id).finally(() => this.#flying.delete(id)));
    return this.#flying.get(id);
  }

  async #fetchSheet(id) {
    const have = this.#sheets.get(id);
    const r = await this.#get(this.#url("/api/run", { key: id }), have ? { "If-None-Match": have.etag } : {});
    if (!r) return { status: "offline" };
    if (r.status === 304 && have) { this.#checked = this.#now(); return { status: "ok", sheet: have.sheet }; }
    if (r.status === 404) { this.#sheets.delete(id); return { status: "missing" }; }
    const failed = this.#failure(r);
    if (failed) return failed;
    const sheet = await r.json().catch(() => undefined);
    if (!validSheet(sheet)) return { status: "invalid" };
    this.#sheets.set(id, { etag: r.headers.get("ETag"), sheet });
    return { status: "ok", sheet };
  }

  /** What each data source serves, by the server's word (`/api/about`: `store` is "store" or "generated", `demo` is
   * "fixtures"), or {} when it does not say or cannot be reached. It names the sources; it is not a summary. */
  async about() {
    const r = await this.#get("/api/about");
    const body = r?.ok ? await r.json().catch(() => undefined) : undefined;
    return isObject(body?.sources) ? body.sources : {};
  }

  #url(path, query) {
    const params = new URLSearchParams({ source: this.#source });
    for (const [k, v] of Object.entries(query)) params.set(k, v);
    return `${path}?${params}`;
  }
  /** A reply that is not a good one, as a value. */
  #failure(r) {
    if (r.status === 404) return { status: "missing" };
    if (r.status === 400) return { status: "refused" };
    return r.ok ? null : { status: "error" };
  }
  /** The response, or null when the server cannot be reached. A response that carries no data (a 304, an error) has its
   * body read and dropped: an unread body leaves the browser reporting the request as cancelled (`net::ERR_ABORTED`). */
  async #get(url, headers = {}) {
    const r = await this.#fetch(url, { headers }).catch(() => null);
    if (r && (r.status === 304 || (!r.ok && r.status !== 409))) await r.arrayBuffer?.().catch(() => {});
    return r;
  }
}
