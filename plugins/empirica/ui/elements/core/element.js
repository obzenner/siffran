// Shared element machinery (UI.md, Elements and layers). Pure helpers only: no fetch, no timers, no trust logic.
import copy from "../../copy/en.json" with { type: "json" };
import icons from "../../icons.json" with { type: "json" };
import states from "../../states.json" with { type: "json" };

const SPRITE = new URL("../../vendor/lucide/sprite.svg", import.meta.url).href;

/** HTML-escape any value for text or attribute context. */
export function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/** Copy catalogue lookup (C1). Unknown keys are a bug: they render loudly. */
export function t(key, vars = {}) {
  const s = copy[key];
  if (s === undefined) return `⟦${key}⟧`;
  return s.replace(/\{(\w+)\}/g, (_, k) => esc(vars[k] ?? ""));
}

/** The same lookup for text that is escaped later (an attribute built by a template that calls `esc` itself), so a
 * value is never escaped twice. */
export function plain(key, vars = {}) {
  const s = copy[key];
  return s === undefined ? `⟦${key}⟧` : s.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ""));
}

/** Presentation for a state (S1). Unknown values map to the visible "unknown" entry, never to a default tone. */
export function stateOf(domain, value) {
  return states[domain]?.[value] ?? states.unknown;
}
/** The word for a value of a state vocabulary. */
export function word(domain, value) { return t(`${stateOf(domain, value).copy}.label`); }

/** A counted phrase: `${key}.one` for exactly one thing, else `key`. `{n}` is that count unless `vars` says more. */
export function count(key, n, vars = {}) { return t(n === 1 ? `${key}.one` : key, { n, ...vars }); }
/** A moment as a clock time, in the reader's locale (`ms` since the epoch). */
export function clock(ms) { return new Intl.DateTimeFormat(undefined, { timeStyle: "medium" }).format(new Date(ms)); }
/** Whether the copy catalogue has `key`. */
export const has = (key) => copy[key] !== undefined;
/** A host's name as the people who use it say it: the catalogue's, else the id the host recorded. */
export const hostName = (id) => (has(`host.${id}`) ? t(`host.${id}`) : esc(id));
/** Parts of one line, the blank ones left out, joined with the catalogue's separator (never one typed in code). */
export function joined(...parts) { return parts.filter(Boolean).join(copy["list.join"]); }
/** A moment the observer recorded (seconds since the epoch), in the reader's locale: a date and a time. */
export function when(seconds) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(seconds * 1000));
}
const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: "auto", style: "narrow" });
/** A moment the observer recorded (seconds since the epoch) as how long ago it was ("3h ago"), against `nowMs`; the
 * catalogue's words when it has no time. A label for the reader, never an order. */
export function ago(seconds, nowMs) {
  if (typeof seconds !== "number") return t("time.unknown");
  const gap = seconds - nowMs / 1000, size = Math.abs(gap);
  if (size < 60) return t("time.now");
  if (size < 3600) return RELATIVE.format(Math.round(gap / 60), "minute");
  if (size < 86400) return RELATIVE.format(Math.round(gap / 3600), "hour");
  return RELATIVE.format(Math.round(gap / 86400), "day");
}
/** A whole number in the reader's locale ("10,000"). */
export const num = (n) => new Intl.NumberFormat().format(n);

/** One value of a contract vocabulary as glyph and word. `variant`: "full" (the word always, where a person acts or reads
 * a state alone), "glyph" (the glyph, its word in the title and for screen readers, shown beside it in words mode: the
 * `Aa` toggle), "chip" (a badge on a tint). Unknown values render as "Unknown". */
export function stateHtml(domain, value, variant = "full") {
  const s = stateOf(domain, value), tone = TONE[s.tone], label = t(`${s.copy}.label`);
  if (variant === "chip") return `<span class="badge ${tone.fg} ${tone.tint}">${icon(s.icon)}<span>${label}</span></span>`;
  const word = variant === "glyph" ? "ev-word text-xs" : "text-s font-medium";
  return `<span class="inline-flex shrink-0 items-center gap-2xs ${tone.fg}" title="${esc(plain(`${s.copy}.label`))}" ${s.live && variant === "glyph" ? "data-live" : ""}>${icon(s.icon)}<span class="${word}">${label}</span></span>`;
}

/** Literal class sets per tone (E7: no string-built classes; Tailwind scans these literals). */
export const TONE = {
  positive: { fg: "text-tone-positive", tint: "bg-tone-positive-tint", solid: "bg-tone-positive" },
  negative: { fg: "text-tone-negative", tint: "bg-tone-negative-tint", solid: "bg-tone-negative" },
  caution: { fg: "text-tone-caution", tint: "bg-tone-caution-tint", solid: "bg-tone-caution" },
  info: { fg: "text-tone-info", tint: "bg-tone-info-tint", solid: "bg-tone-info" },
  human: { fg: "text-tone-human", tint: "bg-tone-human-tint", solid: "bg-tone-human" },
  audit: { fg: "text-tone-audit", tint: "bg-tone-audit-tint", solid: "bg-tone-audit" },
  neutral: { fg: "text-tone-neutral", tint: "bg-tone-neutral-tint", solid: "bg-tone-neutral" },
};

/** Shorten prose to `n` characters. Presentation only: the full text is always one click away. */
export function clip(text, n = 90) {
  const s = String(text ?? "").replace(/\s+/g, " ").trim();
  return s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s;
}

/** Icon by meaning (V5). Size classes are literal tokens. */
const ICON_SIZE = { xs: "size-s", s: "size-m", m: "size-l" };
export function icon(meaning, size = "s") {
  const id = icons[meaning];
  if (!id) throw new Error(`unknown icon meaning: ${meaning}`);
  return `<svg class="${ICON_SIZE[size]} shrink-0" aria-hidden="true" focusable="false"><use href="${SPRITE}#${id}"></use></svg>`;
}

const FOCUSABLE = "button, a[href], input, select, textarea, summary, [tabindex]";
const PRESENTATION = new Set(["data-variant", "data-size", "data-selected"]);
/** `tag[data-x="v"]…` from a node's identifying data-* attributes, or "" when it has none. */
function ident(n) {
  const ids = [...n.attributes].filter((x) => x.name.startsWith("data-") && !PRESENTATION.has(x.name));
  return ids.length ? n.localName + ids.map((x) => `[${x.name}="${CSS.escape(x.value)}"]`).join("") : "";
}
/** Where focus was, owner-qualified: the identified ancestors inside `root` (e.g. the run), then the control
 * itself by its own identity, else by its position among the focusables of its nearest identified owner. */
function focusKey(root, el) {
  const scope = [];
  let owner = root;
  for (let n = el.parentElement; n && n !== root; n = n.parentElement) {
    const s = ident(n);
    if (s) { scope.unshift(s); if (owner === root) owner = n; }
  }
  return { scope: scope.join(" "), own: ident(el), i: [...owner.querySelectorAll(FOCUSABLE)].indexOf(el) };
}
function refocus(root, k) {
  const owner = k.scope ? root.querySelector(k.scope) : root;
  if (!owner) return;
  (k.own ? owner.querySelector(k.own) : owner.querySelectorAll(FOCUSABLE)[k.i])?.focus();
}

/** Base class: light DOM, re-render on attribute change or .data assignment (E3). Subclasses attach their own
 * listeners once, in the constructor, so reconnecting an element never duplicates them. */
export class EvElement extends HTMLElement {
  #data = undefined;
  set data(v) { this.#data = v; if (this.isConnected) this.update(); }
  get data() { return this.#data; }
  connectedCallback() { this.update(); }
  attributeChangedCallback() { if (this.isConnected) this.update(); }
  /** Re-render, let the subclass fill in child data (populate), then give focus back to the equivalent control. */
  update() {
    const a = document.activeElement;
    const keep = a && a !== this && this.contains(a) ? focusKey(this, a) : null;
    this.innerHTML = this.render();
    this.populate();
    if (keep) refocus(this, keep);
  }
  /** Hand structured data to child elements after render (they render synchronously on assignment). */
  populate() {}
  /** @returns {string} */ render() { return ""; }
  /** Bubbling, composed event out (E5). */
  emit(name, detail) { this.dispatchEvent(new CustomEvent(name, { bubbles: true, composed: true, detail })); }
}

export function define(tag, cls) {
  if (!customElements.get(tag)) customElements.define(tag, cls);
}
