// What happy-dom lacks for the custom elements, given by the tests (tests/elements.test.mjs, tests/app.test.mjs): a canvas
// whose 2D context records every call and assignment (the Universe is checked by what it is told to draw, never by pixels),
// Path2D, a layout (happy-dom lays nothing out: a canvas, a scroll box and a row are given their sizes), the colour
// variables, and the animation frames as a queue the test runs. The headless-Chrome journeys stay the proof of real
// rendering; this is unit reach. Nothing here reads a clock: no test waits on time.

/** Every call and assignment made on any canvas's 2D context, in order: [name, ...args] or ["=prop", value]. */
export const drawn = [];
export class Path2D { constructor(d = "") { this.d = d; } }
/** The animation frames asked for and not yet run. */
export const frames = [];
/** Run the frames asked for so far (not the ones they ask for in turn), each at time `now`. */
export function flush(now = 0) { for (const fn of frames.splice(0)) fn(now); }

/** A colour per variable the Universe reads, distinct, so a fill names what it was for. */
export const colours = new Map();

/** Install the stubs into happy-dom's window. `size` is the canvas's box; `row` a list row's height; `view` a scroll box's. */
export function install({ catalogue, size = { w: 1440, h: 900 }, row = 30, view = 300, reduced = true } = {}) {
  globalThis.Path2D = Path2D;
  globalThis.requestAnimationFrame = (fn) => frames.push(fn);
  globalThis.cancelAnimationFrame = () => {};
  const media = (query) => ({ matches: reduced && /reduced-motion: reduce/.test(query), media: query, addEventListener() {}, removeEventListener() {} });
  globalThis.matchMedia = media;
  window.matchMedia = media;
  const ctx = new Proxy({}, {
    get(_, name) {
      if (name === "createRadialGradient") return (...a) => { drawn.push([name, ...a]); return { addColorStop: (...s) => drawn.push(["addColorStop", ...s]) }; };
      return (...a) => { drawn.push([name, ...a]); };
    },
    set(_, name, value) { drawn.push([`=${String(name)}`, Array.isArray(value) ? [...value] : value]); return true; },
  });
  HTMLCanvasElement.prototype.getContext = () => ctx;
  HTMLCanvasElement.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, x: 0, y: 0, width: size.w, height: size.h, right: size.w, bottom: size.h });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get() { return this.hasAttribute("data-row") ? row : 0; } });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get() { return this.hasAttribute("data-scroll") ? view : 0; } });
  const root = document.documentElement.style;
  const hex = (n) => `#${n.toString(16).padStart(6, "0")}`;
  let n = 0x100;
  const set = (name) => { const c = hex(n += 0x10); colours.set(name, c); root.setProperty(name, c); };
  for (const k of catalogue.tones) set(`--tone-${k}`);
  for (let k = 1; k <= catalogue.universe.island_hues; k++) set(`--island-${k}`);
  set("--ring"); set("--background");
  root.setProperty("--duration-fly", "200ms"); root.setProperty("--duration-pulse", "1000ms"); root.setProperty("--duration-ring", "1000ms");
}
