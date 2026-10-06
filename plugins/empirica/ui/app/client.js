// The app's data client for the observer server (adapters/observer/server.py, contract empirica-ui-view/v1).
// It asks whether the store changed (ETag → 304), fetches a run's detail only when the view's version of that run
// is newer than the one it has, and reports every outcome as a value: nothing it returns throws. A reply that is
// not the contract (not JSON, the wrong `contract`, a missing required part) is `invalid`, never trusted. One
// request per (run, version) is ever in flight, and the cache keeps only the runs the current view lists.
// No DOM. Tested in tests/client.test.mjs.

const CONTRACT = "empirica-ui-view/v1";
const isObject = (x) => x !== null && typeof x === "object" && !Array.isArray(x);
const ARRAYS = { run: ["edges", "claims"], detail: ["residuals", "waiting", "stale", "steps", "claims"] };

/** Whether `v` has the view's required shape: its discriminator, token, and every project with its runs and their
 * claims as lists. The schema (view.schema.json) is checked in the build; this is the cheap check at the boundary. */
export function validView(v) {
  return isObject(v) && v.contract === CONTRACT && typeof v.token === "string" && Array.isArray(v.projects)
    && v.projects.every((p) => isObject(p) && typeof p.id === "string" && typeof p.label === "string" && Array.isArray(p.runs)
      && p.runs.every((r) => isObject(r) && typeof r.key === "string" && typeof r.version === "string"
        && ARRAYS.run.every((k) => Array.isArray(r[k]))));
}
/** Whether `d` has a run document's required shape. */
export function validDetail(d) {
  return isObject(d) && d.contract === CONTRACT && typeof d.key === "string" && typeof d.version === "string"
    && ARRAYS.detail.every((k) => Array.isArray(d[k]));
}

export class Client {
  #fetch; #source; #etag = null; #details = new Map(); #flying = new Map();
  /** @param {string} source - one of catalogue.data_sources; @param {typeof fetch} fetcher */
  constructor(source, fetcher = (...a) => fetch(...a)) { this.#source = source; this.#fetch = fetcher; }

  /** `missing`: the server does not serve this source; `offline`: no answer at all; `error`: the server answered
   * with a failure (a 5xx); `invalid`: an answer that is not the view contract.
   * @returns {Promise<{status:"changed",view:object}|{status:"same"|"missing"|"offline"|"error"|"invalid"}>} */
  async view() {
    const r = await this.#get(`/api/view?${new URLSearchParams({ source: this.#source })}`, this.#etag ? { "If-None-Match": this.#etag } : {});
    if (!r) return { status: "offline" };
    if (r.status === 304) return { status: "same" };
    if (r.status === 404) return { status: "missing" };
    if (!r.ok) return { status: "error" };
    const view = await r.json().catch(() => undefined);
    if (!validView(view)) return { status: "invalid" };
    this.#etag = r.headers.get("ETag");
    this.prune(view.projects.flatMap((p) => p.runs.map((x) => x.key)));
    return { status: "changed", view };
  }

  /** The detail of `run` at its version in the view. `missing`: the server has no such run (any more) or it has
   * moved on (the next view brings the new version); `offline`: no answer; `error`: the server answered with a
   * failure; `invalid`: an answer that is not a run document. Requests for one (run, version) are shared.
   * @returns {Promise<{status:"ok",detail:object}|{status:"missing"|"offline"|"error"|"invalid"}>} */
  detail(run) {
    const have = this.cached(run);
    if (have) return Promise.resolve({ status: "ok", detail: have });
    const id = `${run.key}@${run.version}`;
    if (!this.#flying.has(id)) this.#flying.set(id, this.#fetchDetail(run).finally(() => this.#flying.delete(id)));
    return this.#flying.get(id);
  }

  async #fetchDetail(run) {
    const r = await this.#get(`/api/run?${new URLSearchParams({ source: this.#source, key: run.key })}`);
    if (!r) return { status: "offline" };
    if (r.status === 404) return { status: "missing" };
    if (!r.ok) return { status: "error" };
    const d = await r.json().catch(() => undefined);
    if (!validDetail(d) || d.key !== run.key) return { status: "invalid" };
    if (d.version !== run.version) return { status: "missing" };
    this.#details.set(run.key, d);
    return { status: "ok", detail: d };
  }

  /** The response, or null when the server cannot be reached. A response that carries no data (a 304, an error)
   * has its body read and dropped: an unread body leaves the browser reporting the request as cancelled
   * (`net::ERR_ABORTED`), once per poll. */
  async #get(url, headers = {}) {
    const r = await this.#fetch(url, { headers }).catch(() => null);
    if (r && (r.status === 304 || !r.ok)) await r.arrayBuffer?.().catch(() => {});
    return r;
  }

  /** The cached detail of `run` at its version, without asking. */
  cached(run) {
    const have = this.#details.get(run.key);
    return have?.version === run.version ? have : null;
  }

  /** Forget every cached detail whose run is not among `keys` (the runs of the view just received). */
  prune(keys) {
    const live = new Set(keys);
    for (const key of this.#details.keys()) if (!live.has(key)) this.#details.delete(key);
  }
}
