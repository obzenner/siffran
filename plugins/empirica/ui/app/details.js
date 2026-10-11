// The request state of every run document the page asks for: the selected run's and its sibling attempts' alike.
// A document is idle (not asked), pending, ok, or failed (and why); a failure is asked again a few times, later each
// time, by this module and nowhere else, and not at all while the tab is hidden (what came due then is asked when the
// tab is shown again). A manual retry asks at once. No DOM; timers and visibility are given, so it is tested with a
// fake clock (tests/details.test.mjs).
import { backoff } from "./poll.js";

export class Details {
  #states = new Map();  // run key -> { version, status, failure, tries, timer, due }
  /** @param {{client:{cached:Function,detail:Function}, retries:number, base:number, cap:number, hidden:()=>boolean,
   *  onChange:()=>void, setTimer?:Function, clearTimer?:Function}} o */
  constructor(o) { this.o = { setTimer: (...a) => setTimeout(...a), clearTimer: (t) => clearTimeout(t), ...o }; }

  /** The state of `row`'s document at its version: `{status:"idle"|"pending"|"ok"|"failed", detail?, failure?}`. */
  state(row) {
    const have = this.o.client.cached(row);
    if (have) return { status: "ok", detail: have };
    const s = this.#states.get(row.key);
    if (!s || s.version !== row.version) return { status: "idle" };
    return { status: s.status, failure: s.failure };
  }

  /** Ask for `row`'s document unless it is here, being asked for, or waiting for its retry. */
  want(row) {
    if (this.o.client.cached(row)) return;
    const s = this.#states.get(row.key);
    if (s && s.version === row.version && (s.status === "pending" || s.timer !== null || s.due || s.tries > this.o.retries)) return;
    this.#ask(row, s && s.version === row.version ? s.tries : 0);
  }

  /** The operator asked again: at once, whatever the tab and the retries so far. */
  retry(row) { this.#clear(row.key); this.#ask(row, 0); }

  /** Forget the runs that are not in `keys`. */
  keep(keys) {
    const live = new Set(keys);
    for (const k of [...this.#states.keys()]) if (!live.has(k)) { this.#clear(k); this.#states.delete(k); }
  }

  /** The tab was shown: ask what came due while it was hidden. */
  wake() {
    if (this.o.hidden()) return;
    for (const [key, s] of this.#states) if (s.due) { s.due = false; this.#ask({ key, version: s.version }, s.tries); }
  }

  #clear(key) {
    const s = this.#states.get(key);
    if (s?.timer) this.o.clearTimer(s.timer);
    if (s) { s.timer = null; s.due = false; }
  }

  async #ask(row, tries) {
    this.#clear(row.key);
    const mine = { version: row.version, status: "pending", failure: null, tries, timer: null, due: false };
    this.#states.set(row.key, mine);
    this.o.onChange();
    const res = await this.o.client.detail(row);
    if (this.#states.get(row.key) !== mine) return;  // asked again, or forgotten, meanwhile
    if (res.status === "ok") { mine.status = "ok"; this.o.onChange(); return; }
    mine.status = "failed"; mine.failure = res.status;
    mine.tries = tries + 1;
    if (mine.tries <= this.o.retries && res.status !== "missing") {
      mine.timer = this.o.setTimer(() => {
        mine.timer = null;
        if (this.o.hidden()) mine.due = true; else this.#ask(row, mine.tries);
      }, backoff(tries, this.o.base, this.o.cap));
    }
    this.o.onChange();
  }
}
