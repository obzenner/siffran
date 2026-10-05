// Gallery page script (CONVENTIONS G1): renders every element in every state, and the view, on the observer's demo
// data (the plugin's own runs, served as source=demo). Page code may assemble and wire elements; it never derives
// states (E4).
import "../elements/index.js";
import { esc, projectName, statesOf, stepOf, t } from "../elements/core/element.js";
import { optionLabel } from "../elements/organisms/ev-filters.js";
import { claimDetail, fileDetail, projectDetail, runDetail, stepDetail } from "../elements/templates/details.js";
import { Client } from "../app/client.js";
import { apply } from "../app/filter.js";
import catalogue from "../catalogue.json" with { type: "json" };

const { tones: TONES } = catalogue;
const SWATCH = {
  positive: "bg-tone-positive", negative: "bg-tone-negative", caution: "bg-tone-caution", info: "bg-tone-info",
  human: "bg-tone-human", audit: "bg-tone-audit", neutral: "bg-tone-neutral",
};
const TINT = {
  positive: "bg-tone-positive-tint", negative: "bg-tone-negative-tint", caution: "bg-tone-caution-tint", info: "bg-tone-info-tint",
  human: "bg-tone-human-tint", audit: "bg-tone-audit-tint", neutral: "bg-tone-neutral-tint",
};
// The demo data, and every run's detail (the gallery shows the run with the longest history).
const client = new Client("demo");
const fx = (await client.view()).view ?? { projects: [] };
const runs = fx.projects.flatMap((p) => p.runs);
const byKey = new Map(runs.map((r) => [r.key, r]));
const details = new Map((await Promise.all(runs.map((r) => client.detail(r)))).filter(Boolean).map((d) => [d.key, d]));
const longest = [...details.values()].sort((a, b) => b.steps.length - a.steps.length)[0];
const deep = byKey.get(longest.key), deepDetail = longest;
// One changed file per freshness state, so the file row is shown in each.
const fileExamples = statesOf("file").map((state, i) => ({ path: `src/example/${["module.py", "removed.py", "locked.bin", "link", "../outside.txt"][i] ?? "file.txt"}`, state }));
// Every kind and outcome of step the demo histories hold, in states.json order.
const pairs = new Set([...details.values()].flatMap((d) => d.steps.map((s) => JSON.stringify([s.kind, s.outcome]))));
const steps = statesOf("step").flatMap((kind) => [...pairs].map((x) => JSON.parse(x)).filter(([k]) => k === kind).map(([, outcome]) => ({ kind, outcome })));

function section(key, body) {
  return `<section class="mx-auto flex max-w-page flex-col gap-l py-2xl">
    <header class="flex flex-col gap-2xs"><h2 class="text-xl font-semibold">${t(`gallery.${key}`)}</h2><p class="text-s text-muted-foreground">${t(`gallery.${key}.hint`)}</p></header>
    ${body}
  </section>`;
}
function group(key, body) {
  return `<div class="flex flex-col gap-s"><h3 class="text-xs font-medium tracking-wide text-muted-foreground uppercase">${t(key)}</h3>${body}</div>`;
}

const bar = `<div class="mx-auto flex max-w-page flex-wrap items-center justify-between gap-m">
  <span class="text-s font-semibold">${t("gallery.title")}</span>
  <div class="flex flex-wrap items-center gap-m text-s">
    <label class="flex items-center gap-xs">${t("gallery.scheme")}
      <select id="scheme" class="select" data-size="sm"><option value="light">${t("gallery.scheme.light")}</option><option value="dark">${t("gallery.scheme.dark")}</option></select></label>
    <label class="flex items-center gap-xs"><input id="grey" type="checkbox" class="input">${t("gallery.grey")}</label>
  </div>
</div>`;

const colours = section("colours", `<div class="grid grid-cols-2 gap-m sm:grid-cols-4">${TONES.map((tone) => `
  <div class="flex flex-col gap-xs rounded-l border border-border p-s">
    <div class="flex gap-2xs"><span class="h-xl flex-1 rounded-s ${SWATCH[tone]}"></span><span class="h-xl flex-1 rounded-s ${TINT[tone]}"></span></div>
    <span class="text-s">${t(`tone.${tone}`)}</span>
  </div>`).join("")}</div>`);

const stateRows = catalogue.gallery_state_domains.map((d) => group(`gallery.states.${d}`, `<div class="flex flex-col gap-s">
  <div class="flex flex-wrap gap-l">${statesOf(d).map((v) => `<ev-state domain="${d}" value="${v}"></ev-state>`).join("")}</div>
  <div class="flex flex-wrap gap-s">${statesOf(d).map((v) => `<ev-state domain="${d}" value="${v}" variant="chip"></ev-state>`).join("")}</div>
  <div class="flex flex-wrap gap-m">${statesOf(d).map((v) => `<ev-state domain="${d}" value="${v}" show="icon"></ev-state>`).join("")}</div>
</div>`)).join("");
const atoms = section("atoms", `<div class="flex flex-col gap-xl">
  ${stateRows}
  ${group("gallery.steps", `<div class="flex flex-wrap items-center gap-l">${steps.map((s, i) => `<span class="flex items-center gap-xs text-xs text-muted-foreground"><ev-step value="${s.kind}" ${s.outcome ? `outcome="${s.outcome}"` : ""} n="${i + 1}" total="${steps.length}"></ev-step>${esc(stepOf(s.kind, s.outcome).label)}</span>`).join("")}</div>`)}
  ${group("gallery.budgets", `<div class="grid max-w-prose grid-cols-3 gap-l"><ev-meter label="budget.passes" used="1" max="10"></ev-meter><ev-meter label="budget.spawns" used="0" max="1"></ev-meter><ev-meter label="budget.audit_spawns" used="2" max="2"></ev-meter></div>`)}
</div>`);

const molecules = section("molecules", `<div class="flex flex-col gap-xl">
  ${group("gallery.legend", `<ev-legend id="g-key"></ev-legend>`)}
  <div class="grid gap-xl md:grid-cols-2">
    ${group("gallery.files", `<div id="m-files" class="flex flex-col"></div>`)}
    ${group("goal.label", `<ev-goal text="${esc(deepDetail.goal)}"></ev-goal>`)}
  </div>
</div>`);

const organisms = section("organisms", `<div class="flex flex-col gap-xl">
  ${group("gallery.history", `<div data-run-key="${esc(deep.key)}"><ev-steps id="o-steps"></ev-steps></div>`)}
  ${group("gallery.history.list", `<div data-run-key="${esc(deep.key)}" class="max-w-prose"><ev-history id="o-history"></ev-history></div>`)}
  ${group("gallery.view", `<div class="flex aspect-video w-full gap-xl rounded-l border border-border p-l"><div class="w-sidebar shrink-0 overflow-y-auto"><ev-filters id="g-filters"></ev-filters></div><ev-view id="g-view" class="block min-w-0 flex-1"></ev-view></div>`)}
</div>`);

document.getElementById("bar").innerHTML = bar;
document.getElementById("main").innerHTML = colours + atoms + molecules + organisms;
// The key over every value of each domain (the app shows only the values present, with real counts).
const every = (domain) => statesOf(domain).map((value, i) => ({ value, n: i + 1 }));
document.getElementById("g-key").data = { runs: every("run"), audits: every("audit"), diagnostics: every("diagnostic"), claims: every("claim"), gating: 3, supports: 7, attempts: 1 };
for (const f of fileExamples) { const el = document.createElement("ev-file"); el.data = f; document.getElementById("m-files").append(el); }
document.getElementById("o-steps").data = deepDetail.steps;
document.getElementById("o-history").data = { steps: deepDetail.steps, claims: deep.claims };
// The view with its filters, on the fixtures; every filter value is offered here.
const gv = document.getElementById("g-view"), gf = document.getElementById("g-filters");
const options = Object.fromEntries(catalogue.filters.filter((f) => !f.flag).map((f) => [f.id,
  f.id === "project" ? fx.projects.map((p, i) => ({ value: p.id, label: projectName(p, i + 1) }))
    : (f.domain ? statesOf(f.domain) : catalogue[f.values]).map((v) => ({ value: v, label: optionLabel(f, v) }))]));
gf.data = { filters: catalogue.filters, options };
function show(choice) { const v = apply(fx, choice, catalogue.filters); gv.data = v; gf.choice = choice; gf.counts = v.counts; }
gf.addEventListener("ev-filter", (e) => show(e.detail.choice));
show({});

// Detail panel: the shared builders (elements/templates/details.js) word facts already in the demo data. Links
// to other runs are plain text here (href null).
const split = document.getElementById("split");
const detail = split.querySelector("ev-detail");
function open(d) { detail.data = d; split.setAttribute("open", ""); }
function clear() {
  for (const el of document.querySelectorAll("ev-steps, ev-history")) el.removeAttribute("selected");
  for (const k of ["project", "run", "claim"]) gv.removeAttribute(k);
}
document.addEventListener("ev-open", (e) => {
  const d = e.detail;
  if (d.type === "file") { const f = fileExamples.find((x) => x.path === d.path); if (f) open(fileDetail(f)); return; }
  clear();
  if (d.type === "project") {
    const i = fx.projects.findIndex((p) => p.id === d.id);
    gv.setAttribute("project", d.id); open(projectDetail(fx.projects[i], i + 1));
  } else if (d.type === "run") {
    gv.setAttribute("run", d.run); open(runDetail(byKey.get(d.run), details.get(d.run) ?? null));
  } else if (d.type === "claim") {
    const r = byKey.get(d.run);
    gv.setAttribute("run", d.run); gv.setAttribute("claim", d.id); open(claimDetail(r, r.claims.find((c) => c.id === d.id), null, details.get(d.run) ?? null));
  }
});
// Steps are numbered per run: an event is resolved against the run that owns the strip it came from (the nearest
// data-run-key, or the run whose detail the panel shows). Atom examples belong to no run and open nothing.
document.addEventListener("ev-select", (e) => {
  const owner = e.target.closest("[data-run-key]");
  const r = owner ? byKey.get(owner.dataset.runKey) : byKey.get(gv.getAttribute("run"));
  const d = r && details.get(r.key), s = d?.steps.find((x) => x.n === e.detail.step);
  if (!s) return;
  if (owner) { clear(); for (const el of owner.querySelectorAll("ev-steps, ev-history")) el.setAttribute("selected", String(s.n)); }
  open(stepDetail(r, s, d.steps.length));
});

// Gallery controls.
const root = document.documentElement;
document.getElementById("scheme").addEventListener("change", (e) => root.classList.toggle("dark", e.target.value === "dark"));
document.getElementById("grey").addEventListener("change", (e) => root.classList.toggle("ev-grey", e.target.checked));

// Closing the panel clears every selection it was showing.
split.addEventListener("ev-panel", (e) => { if (!e.detail.open) clear(); });
document.title = t("page.title.gallery");
split.querySelector("aside").setAttribute("aria-label", t("page.details"));
// Signals headless checks (G2) that the gallery has rendered.
document.body.dataset.ready = "1";
