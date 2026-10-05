// The app's data client for the observer server (adapters/observer/server.py, contract empirica-ui-view/v1).
// It asks whether the store changed (ETag → 304), fetches a run's detail only when the view's version of that run
// is newer than the one it has, and reports an unreachable or refusing server as a state. A reply that is not the
// contract's JSON is not a network state: the view says `invalid`, a detail throws. No DOM. Tested in
// tests/client.test.mjs.

export class Client {
  #fetch; #source; #etag = null; #details = new Map();
  /** @param {string} source - one of catalogue.data_sources; @param {typeof fetch} fetcher */
  constructor(source, fetcher = (...a) => fetch(...a)) { this.#source = source; this.#fetch = fetcher; }

  /** `missing`: the server does not serve this source; `error`: no answer, or a failure; `invalid`: an answer
   * that is not JSON.
   * @returns {Promise<{status:"changed",view:object}|{status:"same"|"missing"|"error"|"invalid"}>} */
  async view() {
    const r = await this.#get(`/api/view?${new URLSearchParams({ source: this.#source })}`, this.#etag ? { "If-None-Match": this.#etag } : {});
    if (!r) return { status: "error" };
    if (r.status === 304) return { status: "same" };
    if (r.status === 404) return { status: "missing" };
    if (!r.ok) return { status: "error" };
    const view = await r.json().catch(() => undefined);
    if (view === undefined) return { status: "invalid" };
    this.#etag = r.headers.get("ETag");
    return { status: "changed", view };
  }

  /** The detail of `run` at its version in the view, or null when it cannot be had. */
  async detail(run) {
    const have = this.#details.get(run.key);
    if (have?.version === run.version) return have;
    const r = await this.#get(`/api/run?${new URLSearchParams({ source: this.#source, key: run.key })}`);
    if (!r?.ok) return null;
    const d = await r.json();  // a reply that is not JSON throws: a broken server, not a missing detail
    this.#details.set(run.key, d);
    return d.version === run.version ? d : null;  // the store moved on: the next view brings the new version
  }

  /** The response, or null when the server cannot be reached. */
  #get(url, headers = {}) {
    return this.#fetch(url, { headers }).catch(() => null);
  }

  /** The cached detail of `run` at its version, without asking. */
  cached(run) {
    const have = this.#details.get(run.key);
    return have?.version === run.version ? have : null;
  }
}
