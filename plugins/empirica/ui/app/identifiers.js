// Identifiers (PR 17 §4): one atom for machine values. A digest, an er2: handle, a run key, a path, a session id or a
// source URL is not prose: the eye reads a short form, the exact value rides in the chip's `title` and on Copy, and
// every place that shows one uses these helpers, never its own local shortening. Pure: strings in, strings out; the
// caps live in catalogue.json (`identifier`) so the shape is one declared thing, not a number in a view. This module
// imports no element and no DOM base (a node process with no DOM — the journeys runner — may import it), so it owns
// the one escape of the values it shows; the caps say how long a value may grow, never what may pass through.
import catalogue from "../catalogue.json" with { type: "json" };

const IDENT = catalogue.identifier;
const SCHEME = "sha256:";
const DIGEST = /^sha256:[0-9a-f]+$/;
const ORIGIN = /^https?:\/\/[^/]+/;
/** A sha256 token inside prose (PR 17 §4): a full digest, or one already shortened (twelve or more hex and an
 * ellipsis), is the one machine value the helper turns into the atom. */
const TOKEN = /sha256:[0-9a-f]{12,64}(?:…[0-9a-f]+)?/g;

/** Escape for the values and attributes this module builds (the element layer's own `esc` needs no DOM either, but
 * importing it would pull the element base in; the rule is one escape here, tested with the helpers). */
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/**
 * The short form of a machine identifier: the prefix that says what it is always kept — `sha256:` for a digest, a
 * URL's origin for an address — then its head and its tail with the middle elided, per the catalogue's caps. A value
 * already shorter than its caps is returned exactly as given (no double ellipsis). Presentation only: the caller is
 * expected to carry the exact value in the chip's `title` and on Copy.
 */
export function shortIdentifier(value) {
  const s = String(value ?? "");
  if (s.slice(0, SCHEME.length) === SCHEME && DIGEST.test(s)) {
    const body = s.slice(SCHEME.length);
    if (body.length <= IDENT.digest_head + IDENT.digest_tail + 1) return s;
    return `${SCHEME}${body.slice(0, IDENT.digest_head)}…${body.slice(-IDENT.digest_tail)}`;
  }
  const url = s.match(ORIGIN);
  if (url) {
    if (s.length <= IDENT.url_max) return s;
    return `${url[0]}…${s.slice(-IDENT.url_tail)}`;
  }
  if (s.length <= IDENT.max) return s;
  return `${s.slice(0, IDENT.head)}…${s.slice(-IDENT.tail)}`;
}

/**
 * One identifier chip (the atom, without its Copy): the short form read by the eye, the exact value in its `title`,
 * the one `ev-id` class that styles it — mono, a muted chip, never the prose colour (PR 17 §4).
 */
export function identifierChip(value) {
  return `<code class="ev-id" title="${esc(value)}" data-id>${esc(shortIdentifier(value))}</code>`;
}

/**
 * Prose with its sha256 tokens turned into copyable identifier atoms (PR 17 §4): formatting only — every other
 * character is escaped exactly as the element layer's `esc` always did, and the words around a token are untouched. A
 * digest inside a sentence (an audit finding) renders as the chip with a quiet Copy (revealed on hover or focus, so a
 * list of findings is not a list of buttons), carrying the token's own exact value; a token with no atoms around it is
 * the whole string, escaped, unchanged.
 */
export function proseHtml(text) {
  const s = String(text ?? "");
  if (s.match(TOKEN) === null) return esc(s);
  const out = [];
  let at = 0;
  for (const m of s.matchAll(TOKEN)) {
    out.push(esc(s.slice(at, m.index)));
    out.push(`<ev-copy text="${esc(m[0])}" quiet=""></ev-copy>`);
    at = m.index + m[0].length;
  }
  out.push(esc(s.slice(at)));
  return out.join("");
}
