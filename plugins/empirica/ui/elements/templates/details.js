// What the detail panel says about a project, run, claim, step or file (ev-detail data), built once for every page.
// It words facts the observer already has (contract empirica-ui-view/v1); it derives nothing (X1). A run's row
// comes from the view; its `detail` (the run document) is fetched on demand and may still be null, in which case
// the builders show what the row has. `href(selection)` gives a link that selects a run or claim (a selection of app/route.js), or null
// where links are plain text, e.g. the gallery); ids and digests stay in `technical` (D1).
import { approved } from "../core/count.js";
import catalogue from "../../catalogue.json" with { type: "json" };
import { clip, count, esc, joined, stateOf, statesOf, t, when } from "../core/element.js";

const LINK = "text-left text-s text-foreground underline decoration-subtle underline-offset-2 hover:decoration-foreground";
const label = (domain, value) => t(`${stateOf(domain, value).copy}.label`);
const chip = (domain, value) => `<ev-state domain="${esc(domain)}" value="${esc(value)}"></ev-state>`;
function link(url, text) { return url ? `<a class="${LINK}" href="${esc(url)}">${esc(text)}</a>` : `<span>${esc(text)}</span>`; }
function list(items) { return `<div class="flex flex-col items-start gap-2xs">${items.join("")}</div>`; }

/** A claim the run has, as a link to it labelled with its words (never its id); plain words where links are not offered. */
function claimLink(r, id, href) {
  const c = r.claims.find((x) => x.id === id);
  return c ? link(href?.({ kind: "claim", run: r.key, claim: c.id }), clip(c.text, 90)) : "";
}
/** A reason the run stopped short: the plugin's message, and the claim it is about. */
function residual(x, r, href) {
  return `<span class="flex flex-col items-start gap-3xs"><span>${esc(x.message)}</span>${x.claim ? claimLink(r, x.claim, href) : ""}</span>`;
}
/** The waiting items grouped by the route each waits on, in the order the routes first appear: one heading per route. */
function byRoute(waiting) {
  const groups = new Map();
  for (const w of waiting) groups.set(w.route, [...(groups.get(w.route) ?? []), w]);
  return [...groups];
}

/** A claim that waits on the human: which, and for what (its sources, its decision). */
function waitingItem(w, r, href) {
  const what = [w.records ? count("waiting.records", w.records) : "", w.decision ? t("waiting.decision") : ""].filter(Boolean);
  return `<span class="flex flex-col items-start gap-3xs">${claimLink(r, w.claim, href)}${what.map((x) => `<span class="text-xs text-muted-foreground">${x}</span>`).join("")}</span>`;
}

/** "Converged · 7 of 7 claims approved", the empty outcome, or what kept the run from being read in full. A run
 * with a diagnostic says only its state and why: its claim graph was not read, so it is neither empty nor full. */
export function outcome(r) {
  if (r.state === null) return label("diagnostic", r.diagnostic);
  if (r.diagnostic) return t("outcome.partial", { state: label("run", r.state), why: label("diagnostic", r.diagnostic) });
  return r.claims.length ? count(`outcome.${r.state}`, r.claims.length, { n: approved(r), total: r.claims.length }) : t(`outcome.empty.${r.state}`);
}

/** The breadcrumb's name for a run: its state and progress ("Active · 3 of 8"), never the generic word. A run that
 * could not be read in full says why instead. */
export function runCrumb(r) {
  if (r.state === null || r.diagnostic || !r.claims.length) return outcome(r);
  return t("crumb.run.progress", { state: label("run", r.state), n: approved(r), total: r.claims.length });
}
/** …for a step: its number and the kind of record it is, once the run's document is known. */
export function stepCrumb(n, s) {
  return s ? t("crumb.step.kind", { n, kind: label("step", s.kind) }) : t("crumb.step", { n });
}
/** …for a file: its name. */
export function fileCrumb(path) { return path.slice(path.lastIndexOf("/") + 1); }

/** A project: where it is, since when, how many runs and claims it holds, and its runs' states (counted, in
 * states.json order). An unnamed project is told apart by its first activity. */
export function projectDetail(p, href = null) {
  const claims = p.runs.reduce((k, r) => k + r.claims.length, 0);
  const states = statesOf("run").map((s) => [s, p.runs.filter((r) => r.state === s).length]).filter(([, k]) => k);
  return {
    kicker: t("kicker.project"), title: p.label,
    facts: [
      { label: t("fact.repo"), html: p.repo ? `<code class="break-all font-mono text-xs">${esc(p.repo)}</code>` : esc(t("project.repo.none")) },
      ...(p.since === null ? [] : [{ label: t("fact.since"), value: when(p.since) }]),
      { label: t("fact.runs"), value: String(p.runs.length) },
      { label: t("fact.claims"), value: String(claims) },
      ...(states.length ? [{ label: t("fact.runs.states"), html: list(states.map(([s, k]) => `<span class="flex gap-xs"><span>${esc(label("run", s))}</span><span class="tabular-nums text-muted-foreground">${k}</span></span>`)) }] : []),
    ],
    // A project is never a leaf: each of its runs is a link to it, newest first as the view lists them, told apart by
    // its outcome and when it last changed.
    parts: p.runs.length ? [{ label: t("project.runs"), html: list(p.runs.map((r) => link(href?.({ kind: "run", run: r.key }), joined(outcome(r), r.updated === null ? "" : when(r.updated))))) }] : [],
    technical: { project: p.id },
  };
}

/** A run: its outcome and goal, why it could not be fully read, its audit, people, budgets and governance; then
 * what is left, the audit's findings, its history and the files that changed since its spikes (VIEW.md). */
export function runDetail(r, d = null, href = null) {
  const a = d?.audit;
  return {
    domain: r.state === null ? "diagnostic" : "run", value: r.state ?? r.diagnostic,
    kicker: t("kicker.run"), title: outcome(r), goal: d?.goal ?? null,
    facts: [
      ...(r.audit ? [{ label: t("fact.audit"), html: chip("audit", r.audit) }] : []),
      ...(a && r.audit && stateOf("audit", r.audit).verdict ? [{ label: t("fact.independence"), value: t(`independence.${a.independence}`) }] : []),
      ...(d?.author ? [{ label: t("fact.author"), value: d.author }] : []),
      ...(d?.auditor ? [{ label: t("fact.auditor"), value: d.auditor }] : []),
      ...(d?.control ? [{ label: t("fact.control"), value: joined(t(`control.mode.${d.control.mode}`), t(`control.approval.${d.control.approval}`)) }] : []),
      ...(d?.budgets ? catalogue.budgets.map((k) => (
        { label: t(`budget.${k}`), html: `<ev-meter label="budget.${k}" show="amount" used="${d.budgets[k].used}" max="${d.budgets[k].max}"></ev-meter>` })) : []),
      ...(r.generation > 1 ? [{ label: t("fact.attempt"), value: String(r.generation) }] : []),
      ...(r.updated === null ? [] : [{ label: t("fact.when"), value: when(r.updated) }]),
    ],
    parts: [
      ...byRoute(d?.waiting ?? []).map(([route, items]) => ({ label: label("route", route), html: list(items.map((w) => waitingItem(w, r, href))) })),
      ...(d?.residuals.length ? [{ label: t("fact.residuals"), html: list(d.residuals.map((x) => residual(x, r, href))) }] : []),
      ...(a?.findings.length ? [{ label: t("fact.findings"), html: list(a.findings.map((x) => `<span>${esc(x)}</span>`)) }] : []),
      ...(d?.steps.length ? [{ label: t("fact.history"), children: [{ tag: "ev-history", data: { steps: d.steps, claims: r.claims } }] }] : []),
      ...(d?.stale.length ? [{ label: t("run.files"), children: d.stale.map((f) => ({ tag: "ev-file", data: f })) }] : []),
    ],
    technical: { run: r.key, project: r.project, version: r.version },
  };
}

/** A claim: its text and state, what blocks it (the plugin's words), its evidence and its review. */
export function claimDetail(r, c, href, d = null) {
  const own = d?.claims.find((x) => x.id === c.id);
  const target = c.blocker_claim && c.blocker_claim !== c.id ? r.claims.find((x) => x.id === c.blocker_claim) : null;
  return {
    domain: "claim", value: c.state, kicker: t("kicker.claim", { kind: t(`kind.${c.kind}`) }), body: c.text,
    facts: [
      { label: t("fact.run"), html: link(href?.({ kind: "run", run: r.key }), outcome(r)) },
      { label: t("fact.gating"), value: t(c.gating ? "claim.gating.yes" : "claim.gating.no") },
      ...(own?.blocker_message ? [{ label: t("fact.blocker"), value: own.blocker_message }] : []),
      ...(target ? [{ label: t("fact.waits"), html: link(href?.({ kind: "claim", run: r.key, claim: target.id }), clip(target.text, 90)) }] : []),
      { label: t("fact.evidence"), html: chip("evidence", c.evidence) },
      { label: t("fact.review"), html: chip("review", c.review) },
      ...(own?.decision ? [
        { label: t("fact.decision"), html: chip("decision", own.decision.outcome ?? own.decision.route) },
        ...(own.decision.answer ? [{ label: t("fact.answer"), html: `<ev-more text="${esc(own.decision.answer)}"></ev-more>` }] : []),
      ] : []),
      // Counts only when there is something to count: "No evidence" already says none (U2).
      ...(own ? [["fact.research.supporting", own.research], ["fact.spikes.passing", own.spikes], ["fact.spikes.ever", own.spikes_ever]]
        .filter(([, n]) => n > 0).map(([key, n]) => ({ label: t(key), value: String(n) })) : []),
    ],
    parts: own?.records.length ? [{ label: t("fact.sources"), children: own.records.map((data) => ({ tag: "ev-record", data })) }] : [],
    technical: { run: r.key, claim: c.id, blocker: c.blocker, commands: own?.commands ?? null },
  };
}

/** A step of a run's history: what kind of record it is, its outcome, and the claim and command it names. */
export function stepDetail(r, s, total, href = null) {
  const about = s.claim && r.claims.find((c) => c.id === s.claim);
  return {
    domain: "step", value: s.kind, kicker: t("kicker.step", { n: s.n, total }), body: t(`${stateOf("step", s.kind).copy}.hint`),
    facts: [
      ...(s.outcome ? [{ label: t("fact.outcome"), html: chip("outcome", s.outcome) }] : []),
      ...(s.claim ? [{ label: t("fact.about"), html: about ? link(href?.({ kind: "claim", run: r.key, claim: about.id }), clip(about.text, 90)) : esc(t("history.claim.gone")) }] : []),
      ...(s.command ? [{ label: t("fact.command"), html: `<code class="break-all font-mono text-xs">${esc(s.command)}</code>` }] : []),
    ],
    technical: { run: r.key, ...s },
  };
}

/** A file that changed since a run's spikes ran on it. */
export function fileDetail(f) {
  const i = f.path.lastIndexOf("/");
  return {
    domain: "file", value: f.state, kicker: t("kicker.file"), title: f.path.slice(i + 1), mono: true,
    body: t(`${stateOf("file", f.state).copy}.hint`),
    facts: [{ label: t("fact.folder"), html: `<code class="break-all font-mono text-xs">${esc(i < 0 ? t("file.root") : f.path.slice(0, i + 1))}</code>` }],
    technical: { path: f.path, state: f.state },
  };
}
