// What the detail panel says about a project, run, claim, step or file (ev-detail data), built once for every page.
// It words facts the observer already has (contract empirica-ui-view/v1); it derives nothing (X1). A run's row
// comes from the view; its `detail` (the run document) is fetched on demand and may still be null, in which case
// the builders show what the row has. `href(runKey, claimId?)` gives a link that selects that run or claim (or null
// where links are plain text, e.g. the gallery); ids and digests stay in `technical` (D1).
import { approved } from "../core/count.js";
import { clip, esc, projectName, stateOf, statesOf, t } from "../core/element.js";

const LINK = "text-left text-s text-foreground underline decoration-subtle underline-offset-2 hover:decoration-foreground";
const label = (domain, value) => t(`${stateOf(domain, value).copy}.label`);
const chip = (domain, value) => `<ev-state domain="${esc(domain)}" value="${esc(value)}"></ev-state>`;
function link(url, text) { return url ? `<a class="${LINK}" href="${esc(url)}">${esc(text)}</a>` : `<span>${esc(text)}</span>`; }
function list(items) { return `<div class="flex flex-col items-start gap-2xs">${items.join("")}</div>`; }
const BUDGETS = ["passes", "spawns", "audit_spawns"];

/** "Converged · 7 of 7 claims approved", the empty outcome, or what kept the run from being read in full. A run
 * with a diagnostic says only its state and why: its claim graph was not read, so it is neither empty nor full. */
export function outcome(r) {
  if (r.state === null) return label("diagnostic", r.diagnostic);
  if (r.diagnostic) return t("outcome.partial", { state: label("run", r.state), why: label("diagnostic", r.diagnostic) });
  return r.claims.length ? t(`outcome.${r.state}`, { n: approved(r), total: r.claims.length }) : t(`outcome.empty.${r.state}`);
}

/** A project: where it is, how many runs and claims it holds, and its runs' states (counted, in states.json order). */
export function projectDetail(p, n) {
  const claims = p.runs.reduce((k, r) => k + r.claims.length, 0);
  const states = statesOf("run").map((s) => [s, p.runs.filter((r) => r.state === s).length]).filter(([, k]) => k);
  return {
    kicker: t("kicker.project"), title: projectName(p, n),
    facts: [
      { label: t("fact.repo"), html: p.repo ? `<code class="break-all font-mono text-xs">${esc(p.repo)}</code>` : esc(t("project.repo.none")) },
      { label: t("fact.runs"), value: String(p.runs.length) },
      { label: t("fact.claims"), value: String(claims) },
      ...(states.length ? [{ label: t("fact.runs.states"), html: list(states.map(([s, k]) => `<span>${esc(`${k} · ${label("run", s)}`)}</span>`)) }] : []),
    ],
    technical: { project: p.id },
  };
}

/** A run: its outcome and goal, why it could not be fully read, its audit, people, budgets and governance; then
 * what is left, the audit's findings, its history and the files that changed since its spikes (VIEW.md). */
export function runDetail(r, d = null) {
  const a = d?.audit;
  return {
    domain: r.state === null ? "diagnostic" : "run", value: r.state ?? r.diagnostic,
    kicker: t("kicker.run"), title: outcome(r), goal: d?.goal ?? null,
    facts: [
      ...(r.diagnostic && r.state !== null ? [{ label: t("fact.record"), html: chip("diagnostic", r.diagnostic) }] : []),
      ...(r.audit ? [{ label: t("fact.audit"), html: chip("audit", r.audit) }] : []),
      ...(a ? [{ label: t("fact.independence"), value: t(`independence.${a.independence}`) }] : []),
      ...(d?.author ? [{ label: t("fact.author"), value: d.author }] : []),
      ...(d?.auditor ? [{ label: t("fact.auditor"), value: d.auditor }] : []),
      ...(d?.control ? [{ label: t("fact.control"), value: `${t(`control.mode.${d.control.mode}`)} · ${t(`control.approval.${d.control.approval}`)}` }] : []),
      ...(d?.budgets ? BUDGETS.map((k) => (
        { label: t(`budget.${k}`), html: `<ev-meter label="budget.${k}" used="${d.budgets[k].used}" max="${d.budgets[k].max}"></ev-meter>` })) : []),
      ...(r.generation > 1 ? [{ label: t("fact.attempt"), value: String(r.generation) }] : []),
      ...(r.updated === null ? [] : [{ label: t("fact.when"), value: new Date(r.updated * 1000).toLocaleString() }]),
    ],
    parts: [
      ...(d?.residuals.length ? [{ label: t("fact.residuals"), html: list(d.residuals.map((x) => `<span>${esc(x.message)}</span>`)) }] : []),
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
      { label: t("fact.run"), html: link(href?.(r.key), outcome(r)) },
      { label: t("fact.gating"), value: t(c.gating ? "claim.gating.yes" : "claim.gating.no") },
      ...(own?.blocker_message ? [{ label: t("fact.blocker"), value: own.blocker_message }] : []),
      ...(target ? [{ label: t("fact.waits"), html: link(href?.(r.key, target.id), clip(target.text, 90)) }] : []),
      { label: t("fact.evidence"), html: chip("evidence", c.evidence) },
      { label: t("fact.review"), html: chip("review", c.review) },
      ...(own ? [
        { label: t("fact.research.supporting"), value: String(own.research) },
        { label: t("fact.spikes.passing"), value: String(own.spikes) },
        { label: t("fact.spikes.ever"), value: String(own.spikes_ever) },
      ] : []),
    ],
    technical: { run: r.key, claim: c.id, blocker: c.blocker, commands: own?.commands ?? null },
  };
}

/** A step of a run's history: what kind of record it is, its outcome, and the claim and command it names. */
export function stepDetail(r, s, total) {
  const about = s.claim && r.claims.find((c) => c.id === s.claim);
  return {
    domain: "step", value: s.kind, kicker: t("kicker.step", { n: s.n, total }), body: t(`${stateOf("step", s.kind).copy}.hint`),
    facts: [
      ...(s.outcome ? [{ label: t("fact.outcome"), html: chip("outcome", s.outcome) }] : []),
      ...(s.claim ? [{ label: t("fact.about"), value: about?.text ?? t("history.claim.gone") }] : []),
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
