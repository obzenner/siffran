// The view's marks (VIEW.md R2/R4; CONVENTIONS V3/V4), as SVG, drawn by the view and by its key so they cannot
// differ. Shape carries the kind of thing (diamond = run, circle = claim); the tint carries its state.
import { settled } from "./count.js";
import { TONE, iconAt, quietOf, stateOf } from "./element.js";

// Literal classes per tone (E7).
const FILL = {
  positive: "fill-tone-positive-tint", negative: "fill-tone-negative-tint", caution: "fill-tone-caution-tint", info: "fill-tone-info-tint",
  human: "fill-tone-human-tint", audit: "fill-tone-audit-tint", neutral: "fill-tone-neutral-tint",
};
const OUTLINE = { plain: "stroke-subtle", gating: "stroke-muted-foreground" };
export const LINE = "fill-none stroke-muted-foreground";
export const RING = "hidden fill-none stroke-ring group-data-selected:inline group-focus-visible:inline";
export const R = 6;  // a run mark's and a claim mark's half size

/** A run: a diamond with its state's tint and glyph, centred on (cx, cy). */
export function runMark(cx, cy, state) {
  const s = stateOf("run", state);
  return `<path d="M${cx},${cy - R} L${cx + R},${cy} L${cx},${cy + R} L${cx - R},${cy} Z" class="${FILL[s.tone]} ${OUTLINE.plain}" stroke-width="1"></path>${iconAt(s.icon, cx - 4, cy - 4, 8, TONE[s.tone].fg)}`;
}
/** The glyph a claim carries: its state's, unless the state is the quiet one; then its review's, unless the claim is
 * settled (count.js), when it carries none. */
export function claimGlyph(claim) {
  const s = stateOf("claim", claim.state);
  return !s.quiet ? s : settled(claim, quietOf) ? null : stateOf("review", claim.review);
}
/** A claim ({state, review, gating}): a circle with its state's tint and its glyph (claimGlyph); gating = thick outline. */
export function claimMark(cx, cy, claim) {
  const s = stateOf("claim", claim.state), g = claimGlyph(claim);
  return `<circle cx="${cx}" cy="${cy}" r="${R - (claim.gating ? 1 : 0.5)}" class="${FILL[s.tone]} ${claim.gating ? OUTLINE.gating : OUTLINE.plain}" stroke-width="${claim.gating ? 2 : 1}"></circle>${g ? iconAt(g.icon, cx - 4, cy - 4, 8, TONE[g.tone].fg) : ""}`;
}
/** A run's audit: its state's icon and tone, 10 units across, top left at (x, y). */
export function auditMark(x, y, state) {
  const s = stateOf("audit", state);
  return iconAt(s.icon, x, y, 10, TONE[s.tone].fg);
}
/** Why a run could not be read in full (contract `diagnostic`), drawn where its audit's mark would be. */
export function diagnosticMark(x, y, diagnostic) {
  const s = stateOf("diagnostic", diagnostic);
  return iconAt(s.icon, x, y, 10, TONE[s.tone].fg);
}
/** A run's mark beside its diamond: why it was not fully read if so, else its audit's, else nothing. */
export function runSideMark(x, y, run) {
  return run.diagnostic ? diagnosticMark(x, y, run.diagnostic) : run.audit ? auditMark(x, y, run.audit) : "";
}
/** The arrowhead the view's lines end in (one per picture). */
export function arrowDef(id) {
  return `<defs><marker id="${id}" viewBox="0 0 6 6" refX="6" refY="3" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L6,3 L0,6 Z" class="fill-muted-foreground"></path></marker></defs>`;
}
export const SUPPORTS = { width: 0.75 }, ATTEMPT = { width: 1, dash: "3 2" };
