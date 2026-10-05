// Counts over the observer's data, shared by the filters (no DOM) and the elements. A count of states the data
// already has is not a judgement (X1).

/** Whether a claim reads as settled (CONVENTIONS V3): its state and its review are each their domain's quiet value
 * (`quiet(domain)`, states.json). An approved claim that no passing audit reviewed is not settled yet. */
export function settled(c, quiet) { return c.state === quiet("claim") && c.review === quiet("review"); }

/** How many of a run's claims the plugin approved. */
export function approved(r) { return r.claims.filter((c) => c.state === "approved").length; }
