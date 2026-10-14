// The run sheet's divider, as pure functions (elements/atoms/ev-divider.js draws and moves it; tests/split.test.mjs). The
// sheet sits right of the main area and grows leftwards; its width is the reader's (remembered), clamped between the
// sheet's own minimum and what leaves the main area its minimum, and below both minimums the sheet takes the full width.
import catalogue from "../catalogue.json" with { type: "json" };

/** A CSS length in px: px as given, rem by the root's font size `rootPx`; "" is 0. */
export function toPx(value, rootPx) {
  const v = String(value).trim();
  return v.endsWith("rem") ? parseFloat(v) * rootPx : parseFloat(v) || 0;
}

/** The sheet's width for the reader's `want` in a row `total` px wide (main area and sheet), between `min` and the widest
 * that leaves the main area `mainMin`: `{w, max, narrow}`, `narrow` when the row holds less than both minimums (the sheet
 * then takes the full width). Before the row is laid out (`total` 0) the wish stands. */
export function splitClamp(want, total, min, mainMin) {
  if (!(total > 0)) return { w: Math.round(want), max: Math.round(want), narrow: false };
  const max = Math.max(min, total - mainMin);
  return { w: Math.round(Math.min(max, Math.max(min, want))), max, narrow: total < min + mainMin };
}

/** Where a key moves the divider from width `now`: Left and Right a `step` (Left widens the sheet), PageUp and PageDown
 * `split_page` steps, Home the narrowest (`min`), End the widest (`max`); undefined for any other key. */
export function splitKey(key, now, { min, max, step }) {
  const page = catalogue.split_page * step;
  return { ArrowLeft: now + step, ArrowRight: now - step, PageUp: now + page, PageDown: now - page, Home: min, End: max }[key];
}
