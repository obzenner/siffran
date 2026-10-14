// The request state of the open run's sheet. A sheet is idle (not asked), pending, ok, or failed (and why); a failure is asked
// again a few times, later each time, by this module and nowhere else, and not at all while the tab is hidden (what came due
// then is asked when the tab is shown again). A manual retry asks at once. No DOM; timers and visibility are given, so it is
// tested with a fake clock (tests/sheets.test.mjs).
import { backoff } from "./poll.js";

export class Sheets {
  #states = new Map();  // run id -> { status, sheet, failure, tries, timer, due }
  /** @param {{client:{sheet:Function}, retries:number, base:number, cap:number, hidden:()=>boolean, onChange:()=>void,
   *  setTimer?:Function, clearTimer?:Function}} o */
  constructor(o) { this.o = { setTimer: (...a) => setTimeout(...a), clearTimer: (t) => clearTimeout(t), ...o }; }

  /** The state of `id`'s sheet: `{status:"idle"|"pending"|"ok"|"failed", sheet?, failure?}`. A sheet held from before stays shown while it is asked again. */
  state(id) {
    const s = this.#states.get(id);
    return s ? { status: s.status, sheet: s.sheet, failure: s.failure } : { status: "idle" };
  }

  /** Ask for `id`'s sheet unless it is here, being asked for, or waiting for its retry. */
  want(id) {
    const s = this.#states.get(id);
    if (s && (s.status === "pending" || s.status === "ok" || s.timer !== null || s.due || s.tries > this.o.retries)) return;
    this.#ask(id, s?.tries ?? 0);
  }

  /** The store changed: ask again, keeping what is shown (the answer is a 304 when nothing about this run moved). */
  refresh(id) {
    const s = this.#states.get(id);
    if (s && s.status === "ok") this.#ask(id, 0);
  }

  /** The operator asked again: at once, whatever the tab and the retries so far. */
  retry(id) { this.#clear(id); this.#ask(id, 0); }

  /** Forget every run but `id`. */
  keep(id) {
    for (const k of [...this.#states.keys()]) if (k !== id) { this.#clear(k); this.#states.delete(k); }
  }

  /** The tab was shown: ask what came due while it was hidden. */
  wake() {
    if (this.o.hidden()) return;
    for (const [id, s] of this.#states) if (s.due) { s.due = false; this.#ask(id, s.tries); }
  }

  #clear(id) {
    const s = this.#states.get(id);
    if (s?.timer) this.o.clearTimer(s.timer);
    if (s) { s.timer = null; s.due = false; }
  }

  async #ask(id, tries) {
    this.#clear(id);
    const before = this.#states.get(id);
    const mine = { status: "pending", sheet: before?.sheet ?? null, failure: null, tries, timer: null, due: false };
    this.#states.set(id, mine);
    this.o.onChange();
    const res = await this.o.client.sheet(id);
    if (this.#states.get(id) !== mine) return;  // asked again, or forgotten, meanwhile
    if (res.status === "ok") { mine.status = "ok"; mine.sheet = res.sheet; this.o.onChange(); return; }
    mine.status = "failed"; mine.failure = res.status;
    mine.tries = tries + 1;
    if (mine.tries <= this.o.retries && res.status !== "missing") {
      mine.timer = this.o.setTimer(() => {
        mine.timer = null;
        if (this.o.hidden()) mine.due = true; else this.#ask(id, mine.tries);
      }, backoff(tries, this.o.base, this.o.cap));
    }
    this.o.onChange();
  }
}
