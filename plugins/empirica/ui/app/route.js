// The selection: a run, and optionally one of its claims. These are the only two things the URL selects
// (`?run=KEY&claim=ID`, beside the data source and the search). Pure: no DOM, no history.

export const NONE = Object.freeze({ run: null, claim: null });

/** The selection a URL holds; a claim without a run is no selection. */
export function parse(params) {
  const run = params.get("run") || null;
  return run ? { run, claim: params.get("claim") || null } : NONE;
}
/** Put `sel` into `params` (the data source and the search stay). */
export function write(params, sel) {
  for (const [k, v] of Object.entries({ run: sel.run, claim: sel.claim })) if (v) params.set(k, v); else params.delete(k);
  return params;
}
export const same = (a, b) => a.run === b.run && a.claim === b.claim;
/** `sel` as far as the data holds it: a run that is gone is no selection, a claim that is gone leaves its run. */
export function resolve(sel, byKey) {
  const row = sel.run && byKey.get(sel.run);
  if (!row) return NONE;
  return sel.claim && !(row.claims ?? []).some((c) => c.id === sel.claim) ? { run: sel.run, claim: null } : sel;
}
/** Where Esc leads: a claim to its run, a run to the list. */
export const up = (sel) => (sel.claim ? { run: sel.run, claim: null } : NONE);
