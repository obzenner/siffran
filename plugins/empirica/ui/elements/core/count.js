// Counts over the observer's data, shared by the filters (no DOM) and the elements. A count of states the data
// already has is not a judgement (X1).

/** How many of a run's claims the plugin approved. */
export function approved(r) { return r.claims.filter((c) => c.state === "approved").length; }
