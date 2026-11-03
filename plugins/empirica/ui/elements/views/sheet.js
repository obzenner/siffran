// The run sheet (UI.md, The sheet): one run, in the order a person asks about it. Its standing, what is owed or how it
// ended, the audits, the claims with their proof in place, the claims it no longer stands on, what was decided, the other
// attempts side by side, and its history. Every section folds. It words what the observer published for this run (the row
// and the detail in one /api/run answer) and decides nothing; where the sheet has not arrived it says so instead of showing
// less as if there were less. The core's sentences and the session's paths appear here and nowhere else.
import enums from "../../enums.json" with { type: "json" };
import { ago, clip, count, esc, joined, num, plain, stateHtml, t, when } from "../core/element.js";
import { claimsHtml, droppedHtml } from "./claims.js";
import { auditHtml, chip, claimLink, fact, link, list, progress, section, sessionContextHtml, sessionHtml, whoWhere } from "./parts.js";
import { changedHtml, historyHtml } from "./trail.js";

const loading = (ctx) => (ctx.notice ? "" : `<p class="text-s text-muted-foreground">${t("detail.loading")}</p>`);

/** The notice for a sheet that did not arrive, with the way to ask again. */
export function noticeHtml(key) {
  return key ? `<div class="alert" role="alert" data-variant="destructive"><section class="flex flex-wrap items-center gap-s"><span>${t(key)}</span><button type="button" class="btn" data-variant="outline" data-size="sm" data-retry>${t("detail.retry")}</button></section></div>` : "";
}

/** Compact, explicitly read-only display of the observer-owned immutable backend binding. */
function spikeBackendHtml(backend) {
  if (backend === null) return "";
  if (backend.kind === "spikes_unavailable")
    return `<aside class="alert" role="note" data-spike-backend><strong>${t("spike_backend.unavailable")}</strong><span class="text-xs">${t("spike_backend.read_only")}</span></aside>`;
  const identity = (value, label) => {
    const short = `${value.slice(0, 15)}…${value.slice(-8)}`;
    return `<details class="text-xs"><summary class="cursor-pointer">${t(label)} <code class="font-mono">${esc(short)}</code></summary><ev-copy text="${esc(value)}" label="${label}"></ev-copy></details>`;
  };
  const profile = t(`spike.profile.${backend.profile_id}`);
  return `<aside class="flex min-w-0 flex-col gap-3xs rounded-m border border-border p-xs text-xs" role="note" data-spike-backend><span>${joined(t("spike_backend.qualified"), profile, t("spike_backend.read_only"), t("spike_backend.not_health"))}</span>${identity(backend.qualification_record_digest, "spike_backend.record")}${identity(backend.execution_binding_digest, "spike_backend.execution")}</aside>`;
}

/** The run's head: its standing, its goal, and one line of where it is (project, attempt, claims, last change), then the
 * session to copy. The project's repository and the full time are a hover away. */
function headerHtml({ row, project, attempts, detail, now }) {
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
    ${detail ? spikeBackendHtml(detail.spike_backend) : ""}
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

/** Which document a decision link names, resolved over the sheet's own rows (PR 15, amendment A8): the link's id
 * is the bound document's digest or its own short form - the compact hex a link writes - and every row that shares
 * that document (a batch answers several asks against one binding) is named by it. Two distinct documents sharing
 * the short form expand none: the decisions section opens and no row is claimed (an ambiguous link names nothing).
 * A link that names no document on the sheet does the same. */
const pointed = (ctx, decisions) => {
  if (!ctx.decision) return null;
  const named = /^sha256:[0-9a-f]+$/.test(ctx.decision) ? ctx.decision : `sha256:${ctx.decision}`;
  if (!/^sha256:[0-9a-f]{12,64}$/.test(named)) return null;
  const documents = [...new Set(decisions.filter((d) => d.document?.digest?.startsWith(named))
    .map((d) => d.document.digest))];
  return documents.length === 1 ? documents[0] : null;
};

/** The fact rows a document body names: its discriminator, its goal, the plan revision and the proposal it binds. */
function documentFacts(body) {
  const parts = [];
  if (body.question !== undefined) parts.push(fact(t("document.question"), t(`document.question.${body.question}`)));
  if (body.goal !== undefined) parts.push(fact(t("document.goal"), `<ev-more text="${body.goal}"></ev-more>`));
  if (body.rationale) parts.push(fact(t("document.rationale"), `<ev-more text="${body.rationale}"></ev-more>`));
  if (body.plan_revision !== undefined) parts.push(fact(t("document.plan"), num(body.plan_revision)));
  if (body.proposal_digest !== undefined) parts.push(fact(t("document.proposal"), `<code class="break-all font-mono text-xs">${body.proposal_digest}</code>`));
  if (body.run_id !== undefined) parts.push(fact(t("document.run_id"), `<code class="break-all font-mono text-xs">${body.run_id}</code>`));
  if (body.key !== undefined) parts.push(fact(t("document.key"), `<code class="break-all font-mono text-xs">${body.key}</code>`));
  return parts.join("");
}

/** The ceilings as the document showed them, with the amendment's was-values when it confirmed one. */
function budgetRows(body) {
  const rows = body.budgets ?? [];
  return `<ul class="flex flex-col gap-3xs">${rows.map((row) => {
    const was = body.amendment ? body.amendment.budgets[row.key] : null;
    return `<li class="flex flex-wrap items-baseline gap-xs"><span class="text-s">${row.label}</span><span class="text-xs text-muted-foreground">${joined(num(row.value),
      was ? t("document.budget.was", { was: num(was.value) }) : "")}</span></li>`;
  }).join("")}</ul>`;
}

/** What the human read in the document: only the members the core renders, each with its own label (the core
 * display-escaped the text at render; no re-escaping here, and nothing derived - ADR-0072's exact bytes). */
function documentBodyHtml(body) {
  const common = documentFacts(body);
  if (body.kind === "configuration")
    return `<div class="flex flex-col gap-s">${common ? `<dl class="flex flex-col gap-3xs">${common}</dl>` : ""}${body.budgets ? budgetRows(body) : ""}</div>`;
  const parts = [];
  if (body.terminal) {
    parts.push(fact(t("document.terminal"), stateHtml("run", body.terminal.status)));
    if (body.terminal.residuals?.length) parts.push(fact(t("document.residuals"), list(body.terminal.residuals.map((r) => `<span class="text-s"><ev-more text="${r.required}"></ev-more></span>`))));
  }
  if (body.claims?.length) parts.push(fact(t("document.claims"), list(body.claims.map((c) => `<span class="text-s">${c.text}</span>`))));
  if (body.records?.length) parts.push(fact(t("document.records"), list(body.records.map((r) =>
    `<span class="flex flex-col gap-3xs"><span class="text-s font-medium">${r.claim_text}</span>`
    + `<code class="break-all font-mono text-xs">${r.source_ref}</code>`
    + (r.citation ? `<blockquote class="border-l-2 border-border pl-s"><ev-more text="${r.citation}"></ev-more></blockquote>` : "")
    + `<span class="text-xs text-muted-foreground">${t("document.record.verified_as", { class: r.verification_class })}</span></span>`))));
  return `<div class="flex flex-col gap-s">${common ? `<dl class="flex flex-col gap-3-xs">${common}</dl>` : ""}${parts.join("") ? `<dl class="flex flex-col gap-3xs">${parts.join("")}</dl>` : ""}</div>`;
}

/** The decision's immutable document, as the run's own store reads it now (PR 15, operator finding F2): the honest
 * status first (a body only beside `verified` - the contract couples them), the digest and the absolute path a reader
 * can ask for, and - only when the store verified the bytes against the digest - the very form the host showed the
 * human, worded by the document's own members. A decision link (`?decision=`) naming this digest starts the
 * disclosure open, so a link finds the form it linked to. */
function documentHtml(d, ctx, pointed_at) {
  const doc = d.document;
  if (doc === null) return "";
  const open = pointed_at !== null && doc.digest === pointed_at;
  const contents = doc.body === null
    ? `<p class="text-s text-muted-foreground">${t(`document.${doc.status}.note`)}</p>`
    : documentBodyHtml(doc.body);
  return `<details class="text-xs" data-decision-document ${open ? "open" : ""}>
    <summary class="cursor-pointer break-all text-muted-foreground">${stateHtml("document", doc.status, "glyph")} <span>${t("decision.document")}</span></summary>
    <div class="flex min-w-0 flex-col gap-3xs pt-xs">${contents}
      <div class="pt-3xs text-muted-foreground">${t("decision.document.footer")}</div>
      <div class="flex flex-wrap gap-s"><ev-copy text="${doc.digest}" label="decision.document.digest"></ev-copy><ev-copy text="${doc.path}" label="decision.document.path"></ev-copy></div>
    </div>
  </details>`;
}

/** Every answer a human gave: what was asked, the answer and its effect as the core records it, what it decided,
 * and - what the human was shown against it - the decision's immutable document beside the row (PR 15). */
function decisionsHtml(decisions, ctx) {
  if (decisions === null) return `<p class="text-s text-muted-foreground">${t("decisions.unread")}</p>`;
  if (!decisions.length) return `<p class="text-s text-muted-foreground">${t("decisions.none")}</p>`;
  const pointed_at = pointed(ctx, decisions);
  return list(decisions.map((d) => `<span class="flex flex-col gap-3xs" data-decision ${pointed_at !== null && d.document?.digest === pointed_at ? "data-decision-match" : ""}><span class="flex flex-wrap items-center gap-xs"><span class="text-s font-medium">${t(`ask.${d.asked}`)}</span>${chip("answer", d.answer)}<span class="text-xs text-muted-foreground">${joined(t(`effect.${d.effect}`), ago(d.at, ctx.now))}</span></span>${decidedHtml(d, ctx)}${documentHtml(d, ctx, pointed_at)}</span>`));
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
    ${section("decisions", t("decisions.title"), detail ? decisionsHtml(detail.decisions, ctx) : loading(ctx), { count: detail?.decisions ? num(detail.decisions.length) : null, open: Boolean(ctx.decision) })}
    ${section("attempts", t("attempts.title"), attemptsHtml(ctx), { count: ctx.attempts.length > 1 ? num(ctx.attempts.length) : null, open: ctx.attempts.length > 1 })}
    ${detail?.steps ? section("history", t("history.title"), `${changed}${historyHtml(ctx)}`, { count: count("history.count", detail.steps.length), open: false }) : ""}
    ${governanceHtml(ctx)}
  </article>`;
}
