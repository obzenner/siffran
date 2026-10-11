// Asking the observer again and again, politely: not while the tab is hidden (it asks once when the tab is shown
// again), and after a failed answer twice as late each time, up to a cap. Timers and visibility are given, so the
// loop is tested with a fake clock (tests/poll.test.mjs).

/** The wait after `failures` failed answers in a row: `base` doubled each time, never more than `cap`. */
export const backoff = (failures, base, cap) => Math.min(cap, base * 2 ** failures);

export class Poller {
  #timer = null; #failures = 0; #running = false; #stopped = true;
  /** @param {{tick:()=>Promise<boolean>, interval:number, cap:number, hidden:()=>boolean, setTimer?:Function, clearTimer?:Function}} o
   * `tick` asks once and says whether the answer was good. */
  constructor(o) { this.o = { setTimer: (...a) => setTimeout(...a), clearTimer: (t) => clearTimeout(t), ...o }; }
  start() { this.#stopped = false; this.#schedule(); }
  stop() { this.#stopped = true; this.o.clearTimer(this.#timer); this.#timer = null; }
  /** The tab was shown again: ask now, unless a question is already out. */
  wake() { if (!this.#stopped && !this.#running && this.#timer === null) this.#run(); }
  #schedule() {
    if (this.#stopped || this.o.hidden()) return;
    this.#timer = this.o.setTimer(() => { this.#timer = null; this.#run(); }, backoff(this.#failures, this.o.interval, this.o.cap));
  }
  async #run() {
    if (this.#stopped) return;
    if (this.o.hidden()) return;  // nothing is asked while hidden; wake() resumes
    this.o.clearTimer(this.#timer); this.#timer = null;
    this.#running = true;
    try { this.#failures = (await this.o.tick()) ? 0 : this.#failures + 1; } catch { this.#failures++; } finally { this.#running = false; }
    this.#schedule();
  }
}
