// The run sheet (UI.md, The sheet): one run, in the order a person asks about it. Its standing, what is owed or how it
// ended, the audits, the claims with their proof in place, the claims it no longer stands on, what was decided, the other
// attempts side by side, and its history. Every section folds. It words what the observer published for this run (the row
// and the detail in one /api/run answer) and decides nothing; where the sheet has not arrived it says so instead of showing
// less as if there were less. The core's sentences and the session's paths appear here and nowhere else.
import enums from "../../enums.json" with { type: "json" };
import { ago, clip, count, esc, joined, num, plain, t, when } from "../core/element.js";
import { claimsHtml, droppedHtml } from "./claims.js";
import { auditHtml, chip, claimLink, fact, link, list, progress, section, sessionContextHtml, sessionHtml, whoWhere } from "./parts.js";
import { changedHtml, historyHtml } from "./trail.js";

const loading = (ctx) => (ctx.notice ? "" : `<p class="text-s text-muted-foreground">${t("detail.loading")}</p>`);

/** The notice for a sheet that did not arrive, with the way to ask again. */
export function noticeHtml(key) {
  return key ? `<div class="alert" role="alert" data-variant="destructive"><section class="flex flex-wrap items-center gap-s"><span>${t(key)}</span><button type="button" class="btn" data-variant="outline" data-size="sm" data-retry>${t("detail.retry")}</button></section></div>` : "";
}

/** The run's head: its standing, its goal, and one line of where it is (project, attempt, claims, last change), then the
 * session to copy. The project's repository and the full time are a hover away. */
function headerHtml({ row, project, attempts, now }) {
  const where = joined(
    project ? `<span title="${esc(project.repo ?? "")}" data-project>${esc(project.label)}</span>` : "",
    t("row.attempt", { at: row.generation, of: attempts.length }),
    row.state === null ? "" : progress(row),
    row.updated === null ? "" : `<span title="${esc(when(row.updated))}">${esc(ago(row.updated, now))}</span>`);
  return `<header class="flex flex-col gap-xs">
    <div class="flex items-center justify-between gap-s"><span title="${esc(row.standing === "not_ended" ? plain("now.not_ended") : "")}">${chip("standing", row.standing)}</span>
      <button type="button" class="btn" data-variant="ghost" data-size="sm" data-close aria-label="${t("sheet.close")}">${t("sheet.close")}</button></div>
    <h1 class="text-l font-semibold" tabindex="-1" data-title>${row.goal === null ? t("goal.unread") : esc(clip(row.goal, 160))}</h1>
    ${row.goal !== null && row.goal.length > 160 ? `<ev-more text="${esc(row.goal)}"></ev-more>` : ""}
    <p class="text-xs text-muted-foreground" data-where>${where}</p>
    ${row.session ? `<div class="flex flex-col gap-3xs text-xs">${sessionHtml(row.session)}${sessionContextHtml(row.session)}</div>` : ""}
  </header>`;
}

/** What is owed (an active run) or what was left (an ended one): the observer's own words, grouped, with who acts and where. */
function nowHtml(ctx) {
  const { row, detail, href } = ctx;
  const ended = row.attention === "ended";
  const left = (x) => `<span class="flex flex-col items-start gap-3xs text-s"><span>${esc(x.message)}</span>${x.claims.map((c) => claimLink(row, c, href)).join("")}</span>`;
  const owes = (x) => `<span class="flex flex-col items-start gap-3xs"><span class="text-xs font-medium text-muted-foreground">${whoWhere(x)}</span><span class="text-s">${esc(x.message)}</span>${(x.claims ?? []).map((c) => claimLink(row, c, href)).join("")}</span>`;
  let body;
  if (row.attention === null) body = `<p class="text-s">${t("now.unread")}</p>`;
  else if (!detail) body = loading(ctx);
  else if (ended) {
    const s = detail.stop, residuals = detail.residuals ?? [];
    const then = s?.basis === "unavailable" || s === null
      ? `<p class="text-s">${t("now.unavailable")}</p>`
      : residuals.length ? `<h3 class="text-s font-medium">${t("now.unresolved")}</h3>${list(residuals.map(left))}` : `<p class="text-s text-muted-foreground">${t("now.none_left")}</p>`;
    const scope = s === null ? "" : `<p class="text-xs text-muted-foreground">${joined(s.frozen ? t("stop.frozen") : "", t("stop.scope", { required: s.required.length, deferred: s.deferred.length }))}</p>`;
    const today = detail.now?.length ? `<h3 class="text-s font-medium">${t("now.unresolved_now")}</h3><p class="text-xs text-muted-foreground">${t("now.today_hint")}</p>${list(detail.now.map(left))}` : "";
    body = `<p class="text-s">${t("now.ended")}</p>${scope}${then}${today}`;
  } else {
    const owed = detail.outstanding ?? [], so_far = detail.residuals ?? [];
    body = `${owed.length ? list(owed.map(owes)) : `<p class="text-s text-muted-foreground">${t("row.nothing_owed")}</p>`}${so_far.length ? `<h3 class="text-s font-medium">${t("now.unresolved_so_far")}</h3>${list(so_far.map(left))}` : ""}`;
  }
  return section("now", t(ended ? "now.outcome" : "now.title"), body);
}

/** Every audit verdict as the observer serves them, newest first: the verdict, who audited and how independent they were
 * when it was recorded, and what it found. The row's summary (whether the latest still covers the argument, a re-audit
 * pending) is said once: on the latest verdict when it is that verdict, else as its own line (no verdict read yet). */
function auditsHtml({ row, detail, now }) {
  const verdicts = detail ? detail.audits : undefined;
  const notes = (a) => a ? [a.current === false ? t("audit.not_current") : "", a.pending ? t(a.verdict === "none" ? "audit.first_pending" : "audit.reaudit_pending") : ""]
    .filter(Boolean).map((m) => `<span class="text-xs text-muted-foreground">${m}</span>`).join("") : "";
  const lead = row.audit ? `<p class="flex flex-wrap items-center gap-xs text-s">${auditHtml(row.audit)}${row.audit.independence ? `<span class="text-xs text-muted-foreground">${t(`independence.${row.audit.independence}`)}</span>` : ""}</p>` : "";
  if (verdicts === undefined) return `${lead}${detail ? "" : `<p class="text-s text-muted-foreground">${t("detail.loading")}</p>`}`;
  if (verdicts === null) return `${lead}<p class="text-s text-muted-foreground">${t("audits.unread")}</p>`;
  if (!verdicts.length) return lead || `<p class="text-s text-muted-foreground">${t("audits.none")}</p>`;
  const same = row.audit?.verdict === verdicts[0].verdict;
  const one = (a, i) => `<span class="flex flex-col gap-3xs" data-audit>
    <span class="flex flex-wrap items-center gap-xs">${chip("audit", a.verdict)}<span class="text-xs text-muted-foreground">${joined(a.auditor ? esc(a.auditor) : t("audits.auditor_unknown"), a.independence ? t(`independence.${a.independence}`) : t("audits.independence_unknown"), ago(a.at, now))}</span>${i === 0 && same ? notes(row.audit) : ""}</span>
    ${a.findings.length ? list(a.findings.map((f) => `<span class="text-s">${esc(f)}</span>`)) : `<span class="text-xs text-muted-foreground">${t("audits.no_findings")}</span>`}</span>`;
  return `${same ? "" : lead}${list(verdicts.map(one))}`;
}

/** What a decision is attached to, in words: the claim (a link to it in this sheet), the source, or the plan. */
function decidedHtml(d, { row, href }) {
  if (d.claim) return `<span class="text-xs" data-decision-claim="${esc(d.claim)}">${claimLink(row, d.claim, href) || esc(d.claim)}${d.source ? ` · <code class="break-all font-mono">${esc(d.source)}</code>` : ""}</span>`;
  if (d.source) return `<code class="break-all font-mono text-xs">${esc(d.source)}</code>`;
  return d.plan === null ? "" : `<span class="text-xs text-muted-foreground">${t("decision.plan", { plan: d.plan })}</span>`;
}

/** Every answer a human gave: what was asked, the answer and its effect as the core records it, and what it decided. */
function decisionsHtml(decisions, ctx) {
  if (decisions === null) return `<p class="text-s text-muted-foreground">${t("decisions.unread")}</p>`;
  if (!decisions.length) return `<p class="text-s text-muted-foreground">${t("decisions.none")}</p>`;
  return list(decisions.map((d) => `<span class="flex flex-col gap-3xs" data-decision><span class="flex flex-wrap items-center gap-xs"><span class="text-s font-medium">${t(`ask.${d.asked}`)}</span>${chip("answer", d.answer)}<span class="text-xs text-muted-foreground">${joined(t(`effect.${d.effect}`), ago(d.at, ctx.now))}</span></span>${decidedHtml(d, ctx)}</span>`));
}

/** The attempts at this run side by side: only published values are set next to each other; whether an attempt is a retry of
 * the same work is not claimed, a changed goal is said to be changed. */
function attemptsHtml({ attempts, row, href, now }) {
  if (attempts.length < 2) return `<p class="text-s text-muted-foreground">${t("attempts.only")}</p>`;
  const head = attempts.map((a) => `<th scope="col" class="text-left">${a.key === row.key ? `<span aria-current="true">${t("row.attempt.short", { at: a.generation })}</span>` : link(href({ run: a.id, claim: null }), t("row.attempt.short", { at: a.generation }), "data-attempt")}</th>`).join("");
  const cell = (html) => `<td class="align-top">${html}</td>`;
  const rows = [
    [t("compare.goal"), attempts.map((a, i) => a.goal === null ? t("compare.unread") : `${esc(clip(a.goal, 100))}${i && attempts[i - 1].goal !== null && attempts[i - 1].goal !== a.goal ? ` <span class="text-xs font-medium">${t("compare.goal_changed")}</span>` : ""}`)],
    [t("compare.standing"), attempts.map((a) => chip("standing", a.standing))],
    [t("compare.audit"), attempts.map((a) => a.audit === null ? t("compare.unread") : auditHtml(a.audit))],
    [t("compare.progress"), attempts.map((a) => progress(a) || t("compare.unread"))],
    [t("compare.updated"), attempts.map((a) => ago(a.updated, now))],
  ];
  return `<div><table class="table ev-compare w-full"><caption class="sr-only">${t("compare.caption")}</caption><thead><tr><td></td>${head}</tr></thead><tbody>${rows.map(([label, cells]) => `<tr><th scope="row" class="text-left font-medium">${label}</th>${cells.map(cell).join("")}</tr>`).join("")}</tbody></table></div>`;
}

/** Governance and the audit's setting, as labelled facts: the configuration in force, what is proposed only while a proposal waits. */
function governanceHtml({ detail }) {
  if (!detail?.governance) return "";
  const g = detail.governance;
  const meters = enums.budgets.map((k) => fact(t(`budget.${k}`), `<ev-meter label="budget.${k}" show="amount" used="${g.ceilings[k].used}" max="${g.ceilings[k].max}"></ev-meter>`));
  const facts = [
    fact(t("fact.control"), joined(t(`mode.${g.mode}`), t(`approval.${g.approval}`))),
    ...meters,
    ...(g.proposed ? [fact(t("fact.proposed"), esc(enums.budgets.map((k) => `${t(`budget.${k}`)} ${g.proposed[k]}`).join(", ")))] : []),
    ...(g.interactions ? [fact(t("fact.interactions"), t("interactions.left", g.interactions))] : []),
    ...(detail.author ? [fact(t("fact.author"), esc(detail.author))] : []),
    ...(detail.auditor ? [fact(t("fact.auditor"), esc(detail.auditor))] : []),
  ];
  return section("governance", t("governance.title"), `<dl class="ev-facts flex flex-col gap-xs">${facts.join("")}</dl>`, { open: false });
}

/** The sheet for the open run. `ctx`: row, project (the summary's), detail (the sheet's detail or null), attempts (rows), notice
 * (copy key or null), claim (the open claim id or null), now (ms), href(changes) -> the link to the place with those changes. */
export function sheetHtml(ctx) {
  const { row, detail } = ctx;
  const claims = row.claims === null ? `<p class="text-s">${t("claims.unread")}</p>` : claimsHtml(ctx);
  const dropped = detail ? detail.dropped : undefined;
  const changed = changedHtml(ctx);
  return `<article class="flex flex-col gap-s" aria-label="${t("sheet.label")}">
    ${headerHtml(ctx)}
    ${noticeHtml(ctx.notice)}
    ${nowHtml(ctx)}
    ${section("audit", t("audit.title"), auditsHtml(ctx), { count: detail?.audits ? num(detail.audits.length) : null })}
    ${section("claims", t("claims.title"), claims, { count: row.claims ? num(row.claims.length) : null })}
    ${section("dropped", t("dropped.title"), dropped === undefined ? loading(ctx) : droppedHtml(dropped, ctx.now), { count: dropped ? num(dropped.length) : null, open: Boolean(dropped?.length) })}
    ${section("decisions", t("decisions.title"), detail ? decisionsHtml(detail.decisions, ctx) : loading(ctx), { count: detail?.decisions ? num(detail.decisions.length) : null, open: false })}
    ${section("attempts", t("attempts.title"), attemptsHtml(ctx), { count: ctx.attempts.length > 1 ? num(ctx.attempts.length) : null, open: ctx.attempts.length > 1 })}
    ${detail?.steps ? section("history", t("history.title"), `${changed}${historyHtml(ctx)}`, { count: count("history.count", detail.steps.length), open: false }) : ""}
    ${governanceHtml(ctx)}
  </article>`;
}
