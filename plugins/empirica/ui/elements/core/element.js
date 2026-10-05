// Shared element machinery (CONVENTIONS E1-E7). Pure helpers only: no fetch, no timers, no trust logic.
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

/** Presentation for a state (S1). Unknown values map to the visible "unknown" entry, never to a default tone. */
export function stateOf(domain, value) {
  return states[domain]?.[value] ?? states.unknown;
}
/** Presentation for a history step (contract `steps[]`): its kind's icon and word, with its outcome's word and, when
 * it has one, its outcome's tone (a failed spike reads as a failure). `quiet`/`minor` are the kind's. */
export function stepOf(kind, outcome) {
  const k = stateOf("step", kind), o = outcome ? stateOf("outcome", outcome) : null;
  const word = t(`${k.copy}.label`);
  return { icon: k.icon, tone: o ? o.tone : k.tone, quiet: !!k.quiet, minor: !!k.minor, copy: k.copy,
    label: o ? t("step.with", { kind: word, outcome: t(`${o.copy}.label`) }) : word };
}

/** A project's label: its folder name, else its place in the view. */
export function projectName(p, n) { return p.name ?? t("view.project", { n }); }

/** The values of a domain, in states.json order. */
export function statesOf(domain) { return Object.keys(states[domain]); }
/** A domain's one quiet value (`quiet: true` in states.json), or `undefined`. */
export function quietOf(domain) { return statesOf(domain).find((v) => states[domain][v].quiet); }

/** Literal class sets per tone (E7: no string-built classes; Tailwind scans these literals). */
export const TONE = {
  positive: { fg: "text-tone-positive", tint: "bg-tone-positive-tint" },
  negative: { fg: "text-tone-negative", tint: "bg-tone-negative-tint" },
  caution: { fg: "text-tone-caution", tint: "bg-tone-caution-tint" },
  info: { fg: "text-tone-info", tint: "bg-tone-info-tint" },
  human: { fg: "text-tone-human", tint: "bg-tone-human-tint" },
  audit: { fg: "text-tone-audit", tint: "bg-tone-audit-tint" },
  neutral: { fg: "text-tone-neutral", tint: "bg-tone-neutral-tint" },
};

/** Shorten prose for a level-1 hint (D: ≤ 60 characters). Presentation only. */
export function clip(text, n = 48) {
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

/** Icon by meaning (V5) placed inside an SVG picture, at (x, y) with the given size in the picture's units. */
export function iconAt(meaning, x, y, size, cls = "") {
  const id = icons[meaning];
  if (!id) throw new Error(`unknown icon meaning: ${meaning}`);
  return `<svg x="${x}" y="${y}" width="${size}" height="${size}" viewBox="0 0 24 24" class="${cls}" aria-hidden="true" focusable="false"><use href="${SPRITE}#${id}"></use></svg>`;
}

const FOCUSABLE = "button, a[href], input, select, textarea, summary, [tabindex]";
const PRESENTATION = new Set(["data-hint", "data-variant", "data-size", "data-selected"]);
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
