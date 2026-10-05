// The view's filter choice in the URL: f.<filter>=a,b, a flag as f.<filter>=1. Pure; tested in tests/url.test.mjs.

/** The choice a URL query names, keeping only the values on offer (`options`: filter id → [{value}]). */
export function choiceFrom(q, filters, options) {
  const choice = {};
  for (const f of filters) {
    const v = q.get(`f.${f.id}`);
    if (v === null) continue;
    if (f.flag) { if (v === "1") choice[f.id] = true; continue; }
    const ok = new Set((options[f.id] ?? []).map((o) => o.value)), vs = v.split(",").filter((x) => ok.has(x));
    if (vs.length) choice[f.id] = vs;
  }
  return choice;
}

/** Write `choice` into the query `q`, removing the filters it does not set. */
export function writeChoice(q, choice, filters) {
  for (const f of filters) {
    const v = choice[f.id];
    if (v === true) q.set(`f.${f.id}`, "1"); else if (v?.length) q.set(`f.${f.id}`, v.join(",")); else q.delete(`f.${f.id}`);
  }
}
