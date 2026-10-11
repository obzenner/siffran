// The list row and the run workspace, built from the real-service fixtures: every run renders, every message the observer
// published for it is on the page, and nothing is invented (a state is its word, an unread run is not an empty one).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
globalThis.HTMLElement ??= class {};  // the builders live beside the custom elements, which extend the DOM base
const { rowHtml } = await import("../elements/templates/runs.js");
const { runHtml } = await import("../elements/templates/run.js");
const { details, projectOf, row, rows, byKey, scenarios } = await import("./fixtures.mjs");
const { attempts } = await import("../app/search.js");

const href = (sel) => `?run=${encodeURIComponent(sel.run ?? "")}${sel.claim ? `&claim=${sel.claim}` : ""}`;
const rowOf = (r) => rowHtml({ row: r, project: projectOf.get(r.key) }, { href, selected: false, attempts: attempts(projectOf.get(r.key), r, byKey).length });
const spaceless = (s) => s.replace(/<[^>]*>/g, " ").replace(/&amp;/g, "&").replace(/&#39;/g, "'").replace(/\s+/g, " ");
const space = (html) => spaceless(html);
const ctx = (r, over = {}) => {
  const family = attempts(projectOf.get(r.key), r, byKey);
  return { row: r, project: projectOf.get(r.key), detail: details[r.key] ?? null, notice: null, claim: null, href,
    attempts: family.map((a) => ({ row: a, detail: details[a.key] ?? null })), ...over };
};
const clean = (html, what) => { for (const bad of ["⟦", "undefined", "NaN", "[object"]) assert.ok(!html.includes(bad), `${what}: ${bad}`); };

test("every run has a row naming its goal, project, and what it owes next, without a gap in the copy", () => {
  for (const { row: r } of rows) {
    const html = rowOf(r), text = space(html);
    clean(html, r.key);
    assert.ok(html.includes(`data-run="${r.key}"`));
    if (r.goal !== null) assert.ok(text.includes(r.goal.slice(0, 40)), `${r.key} goal`);
    if (r.next) assert.ok(text.includes(r.next.message), `${r.key} next`);
    if (r.attention === "ended") assert.ok(!text.includes("Nothing owed"), r.key);
  }
});

test("a run that was not read says so instead of showing nothing, and offers no attempt number", () => {
  const unread = rows.filter(({ row: r }) => r.attention === null);
  assert.ok(unread.length >= 2);
  for (const { row: r } of unread) {
    const text = space(rowOf(r));
    assert.ok(text.includes("not read in full"), r.key);
    assert.ok(!/Attempt \d/.test(text), r.key);
  }
});

test("T1: the run waiting for a human names who acts, where, the message and the session with its locator", () => {
  const r = row("verification"), text = space(rowOf(r));
  assert.match(text, /A human · when the agent next reports/);
  assert.ok(text.includes(r.next.message));
  assert.ok(text.includes("atlas docs session") && rowOf(r).includes(`text="${r.session.locator}"`));
});

test("a session the host recorded nothing for reads as unknown, never as a blank", () => {
  const r = rows.find(({ row: x }) => x.session && x.session.locator === null).row;
  assert.ok(space(rowOf(r)).includes("unknown (its host recorded none)"));
});

test("text from the store is escaped everywhere it is shown", () => {
  const r = structuredClone(row("verification"));
  r.goal = "<img src=x onerror=alert(1)> & more";
  r.claims[0].text = "<script>bad()</script>";
  const html = rowOf(r) + runHtml(ctx(r, { detail: details[r.key] }));
  assert.ok(!html.includes("<img src=x") && !html.includes("<script>bad"));
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt; &amp; more"));
});

test("every workspace renders; an active run lists what it owes, an ended one what it left, both in the observer's words", () => {
  for (const { row: r } of rows) {
    const d = details[r.key];
    const html = runHtml(ctx(r, { detail: d ?? null })), text = space(html);
    clean(html, r.key);
    if (!d) continue;
    for (const o of d.outstanding ?? []) assert.ok(text.includes(o.message), `${r.key}: ${o.message}`);
    for (const x of d.residuals ?? []) assert.ok(text.includes(x.message), `${r.key}: ${x.message}`);
    assert.equal(text.includes("Unresolved when it stopped"), r.attention === "ended" && d.residuals?.length > 0, r.key);
    assert.equal(html.includes('id="now"'), true);
    assert.equal(text.includes("This run has ended"), r.attention === "ended", r.key);
  }
});

test("T4: a stopped run says it ended, lists what was unanswered, and marks out-of-scope claims Deferred", () => {
  const stopped = row("decision"), text = space(runHtml(ctx(stopped)));
  assert.match(text, /This run has ended\. Nothing more will be asked in it\./);
  assert.match(text, /Unresolved when it stopped/);
  const frozen = row("frozen"), t2 = space(runHtml(ctx(frozen)));
  assert.equal(/Deferred/.test(t2), frozen.claims.some((c) => !c.gating));
  assert.equal(/Required/.test(t2), frozen.claims.some((c) => c.gating));
});

test("proposed ceilings are shown only while a proposal waits on the human", () => {
  for (const { row: r } of rows) {
    const g = details[r.key]?.governance;
    if (!g) continue;
    assert.equal(space(runHtml(ctx(r))).includes("Proposed ceilings"), g.proposed !== null, r.key);
  }
  assert.ok(Object.values(details).some((d) => d.governance?.proposed), "control: one run has a proposal waiting");
});

test("only the selected claim is open, with its proof; the others are one line each", () => {
  const r = row("verification");
  const html = runHtml(ctx(r, { claim: "C6" }));
  assert.equal((html.match(/<details open/g) ?? []).length, 1 + 1, "the claim, and the history disclosure");
  assert.ok(html.includes('data-claim="C6"'));
  assert.equal((html.match(/<ev-record data-record=/g) ?? []).length, details[r.key].claims.find((c) => c.id === "C6").records.length, "its records are elements the workspace fills");
  assert.ok(!runHtml(ctx(r)).includes('<ev-record'), "no claim open, no records");
  assert.ok(html.includes(`data-href="${href({ run: r.key, claim: null })}"`), "the open claim's summary closes it");
});

test("T3: an open claim shows its assurance, review, evidence, spike results and the audit's independence together", () => {
  const r = row("converged"), d = details[r.key];
  const spiked = d.claims.find((c) => c.proof.length);
  const text = space(runHtml(ctx(r, { claim: spiked.id })));
  const p = spiked.proof[0];
  assert.ok(text.includes(p.command) && text.includes(p.files[0].path), "the command and the files it bound");
  assert.ok(text.includes("Passed") && text.includes(`exit ${p.exit_code}`) && text.includes("not kept"), "how it ended, and that the output is not kept");
  assert.ok(text.includes("Assurance") && text.includes("Review") && text.includes("Evidence"));
  assert.ok(text.includes("Independent of the author"));
});

test("T5: the attempts list names each attempt, links the others, and flags a changed goal", () => {
  const latest = row("attempts"), html = runHtml(ctx(latest)), text = space(html);
  for (const n of [1, 2, 3]) assert.ok(text.includes(`Attempt ${n}`), n);
  assert.ok(html.includes("data-attempt"));
  assert.match(text, /goal changed/);
  const alone = space(runHtml(ctx(row("converged"))));
  assert.ok(alone.includes("only attempt"));
});

test("a stale audit reads as a pass that no longer covers the argument, with its pending re-audit, never as a failure", () => {
  const stale = rowOf(row("reaudit"));
  assert.ok(stale.includes('<ev-state domain="audit" value="passed">') && !stale.includes('value="failed"'));
  assert.match(space(stale), /no longer covers the current argument/);
  const pending = rowOf(row("pending-reaudit"));
  assert.ok(pending.includes('value="passed"') && !pending.includes("no longer covers"));
  assert.match(space(pending), /re-audit pending/);
});

test("only a pass the core says no longer covers the argument reads so; a run with no verdict never does", () => {
  for (const { row: r } of rows.filter(({ row: x }) => x.audit)) assert.equal(/no longer covers/.test(space(rowOf(r))), r.audit.current === false, r.key);
  assert.ok(rows.some(({ row: x }) => x.audit?.current === false) && rows.some(({ row: x }) => x.audit?.current === null));
});

test("a run whose claims were not read says so in place of its claims, not 'no claims'", () => {
  const r = row("history-unavailable"), text = space(runHtml(ctx(r)));
  assert.match(text, /Claims not read/);
  assert.ok(!/declared no claims/.test(text));
  assert.ok(scenarios["history-unavailable"]);
});

test("a document that did not arrive says so, offers Retry, and the claim proof says it is unavailable", () => {
  const r = row("verification"), html = runHtml(ctx(r, { detail: null, notice: "detail.offline", claim: "C1" }));
  assert.ok(html.includes("data-retry") && space(html).includes("can't be reached"));
  assert.ok(space(html).includes("not available"));
  assert.ok(!space(runHtml(ctx(r, { detail: null }))).includes("can't be reached"));
});

// ---- U3: what the core published is said as it is: historical, current, unread, failed, folded --------------------------

test("F3: a stop says its scope and what it left, and today's evaluation sits under its own heading, only when it differs", () => {
  const drifted = row("stopped-drift"), plain = row("budget");
  const text = space(runHtml(ctx(drifted))), calm = space(runHtml(ctx(plain)));
  assert.ok(details[drifted.key].now.length && details[plain.key].now === null);
  assert.match(text, /Required: \d+ · Deferred: \d+/);
  assert.ok(text.includes("Unresolved against the workspace now") && text.includes("Judged against the files as they are now"));
  assert.ok(text.indexOf("Unresolved against the workspace now") > text.indexOf("Unresolved when it stopped") || !text.includes("Unresolved when it stopped"));
  assert.ok(!calm.includes("Unresolved against the workspace now"));
  for (const m of details[drifted.key].now) assert.ok(text.includes(m.message));
});

test("F3: a stop whose record cannot hold what it left says it is unknown, never 'no unresolved obligation'", () => {
  const r = row("stopped-drift"), d = structuredClone(details[r.key]);
  d.stop.basis = "unavailable"; d.residuals = null; d.now = null;
  const text = space(runHtml(ctx(r, { detail: d })));
  assert.ok(text.includes("does not hold what it left unresolved") && !text.includes("No unresolved obligation"));
});

test("F3: a frozen stop names that it was frozen, with its required and deferred counts", () => {
  const r = row("frozen"), text = space(runHtml(ctx(r)));
  assert.ok(text.includes("Frozen to a scope before it stopped.") && /Required: 1 · Deferred: 1/.test(text));
});

test("F4: a spike's result is its command, how it ended, the files it was bound to and whether it still holds, and says the output is not kept", () => {
  const r = row("stale"), d = details[r.key];
  const claim = d.claims.find((c) => c.proof.some((p) => p.state === "stale"));
  const text = space(runHtml(ctx(r, { claim: claim.id })));
  assert.ok(text.includes("a file it was bound to has changed since") && text.includes("The output of these commands is not kept."));
  for (const p of claim.proof) assert.ok(text.includes(p.command) && p.files.every((f) => text.includes(f.path)));
});

test("F6: an attempt that was not read is 'Not read' in every cell it cannot fill, never 'None' or 'Loading', and a goal is compared only when both are known", () => {
  const r = row("attempt-unread-sibling"), family = attempts(projectOf.get(r.key), r, byKey);
  assert.ok(family.some((a) => a.diagnostic === "unreadable") && family.some((a) => a.diagnostic === null));
  const text = space(runHtml(ctx(r, { attempts: family.map((a) => ({ row: a, detail: details[a.key] ?? null, request: details[a.key] ? null : "ok" })) })));
  const cells = text.slice(text.indexOf("The attempts side by side"));
  assert.ok(!cells.includes("Loading") && !cells.includes("goal changed") && !cells.includes("None recorded"));
  assert.ok((cells.match(/Not read/g) ?? []).length >= 4, "its goal, governance, audit and unresolved cells");
  // a known goal after an unknown one is not a changed goal
  const after = attempts(projectOf.get(row("attempts").key), row("attempts"), byKey).map((a, i) => ({ row: i === 0 ? { ...a, goal: null } : a, detail: details[a.key] }));
  const changed = (a) => (space(runHtml(ctx(row("attempts"), { attempts: a }))).match(/goal changed/g) ?? []).length;
  assert.deepEqual([changed(attempts(projectOf.get(row("attempts").key), row("attempts"), byKey).map((x) => ({ row: x, detail: details[x.key] }))), changed(after)], [2, 1]);
});

test("F6/F7: a sibling whose document is pending is Loading, one that failed is 'Could not be loaded' with a Retry for the family", () => {
  const r = row("attempts"), family = attempts(projectOf.get(r.key), r, byKey);
  const asked = (request) => ctx(r, { attempts: family.map((a) => ({ row: a, detail: a.key === r.key ? details[a.key] : null, request: a.key === r.key ? null : request })) });
  const pending = runHtml(asked("pending")), failed = runHtml(asked("error"));
  assert.ok(space(pending).includes("Loading") && !pending.includes('data-retry="family"'));
  assert.ok(space(failed).includes("Could not be loaded") && failed.includes('data-retry="family"') && !space(failed).includes("Loading"));
});

test("F10: steps that only record how the run was kept are folded away, numbered as they were, and the human's consent stays in view", () => {
  const r = row("terminal-consent"), d = details[r.key], html = runHtml(ctx(r));
  const bookkeeping = d.steps.filter((s) => s.bookkeeping), main = html.slice(0, html.indexOf("bookkeeping step"));
  assert.ok(bookkeeping.length && html.includes(`${bookkeeping.length} bookkeeping step`));
  assert.ok(!main.includes('domain="step" value="observation_basis"') && html.includes('domain="step" value="observation_basis"'), "only in the fold");
  assert.ok(space(main).includes("Human consent to end the run"));
});

test("F9: nothing the observer publishes about a past event or a future ask names the viewer", () => {
  const copy = JSON.parse(readFileSync(new URL("../copy/en.json", import.meta.url), "utf8"));
  const personal = Object.entries(copy).filter(([k, v]) => /\byou(r)?\b/i.test(v) && !/^(app\.|list\.empty\.)/.test(k));
  assert.deepEqual(personal, []);
});

test("U4 H5: each spike's gate and every bound file's full SHA-256 are in the page, in a disclosure that is not part of the compact line", () => {
  const r = row("stale"), d = details[r.key];
  const proofs = d.claims.flatMap((c) => c.proof.map((p) => ({ claim: c.id, p }))).filter(({ p }) => p.files.length);
  assert.ok(proofs.length);
  for (const { claim, p } of proofs) {
    const html = runHtml(ctx(r, { claim }));
    const shown = html.slice(html.indexOf("data-bindings"));
    assert.ok(html.includes("data-bindings") && /<details class="text-xs"><summary[^>]*data-bindings/.test(html));
    for (const f of p.files) assert.ok(shown.includes(f.sha256) && f.sha256.length >= 64, f.path);
    assert.ok(space(shown).includes(`Gate: ${p.gate === "pass" ? "Passed" : "Failed"}`));
    assert.ok(!/<details[^>]*\sopen/.test(shown.slice(0, shown.indexOf("</details>"))), "closed until asked for");
  }
});

test("U4 H2: a run that needs a human names the human's question, and the agent's work beside it is still listed first", () => {
  const r = row("parallel-human"), d = details[r.key];
  assert.equal(r.attention, "needs_you");
  assert.equal(r.next.who, "human");
  assert.equal(d.outstanding[0].who, "agent");
  const text = space(rowOf(r));
  assert.ok(text.includes(r.next.message) && !text.includes("after the steps above"));
});

test("U5 V1: a human who may be asked if the host finds a dialog is told as that, never as a definite question", () => {
  const r = row("unobserved-surface"), d = details[r.key];
  assert.equal(r.attention, "not_ended");
  assert.deepEqual([r.next.who, r.next.where], ["human", "if_available"]);
  const rowText = space(rowOf(r)), page = space(runHtml(ctx(r, { claim: "C0" })));
  assert.ok(rowText.includes("if the host finds a dialog"), rowText);
  assert.ok(page.includes("if the host finds a dialog") && d.claims[0].decision.where === "if_available");
  assert.ok(!/needs a human/i.test(rowText) || !rowText.includes("Needs a human"), "and it is not listed among the questions a human is owed");
});

test("U5 V4: what the host recorded of the session is in a closed disclosure, unknown staying unknown, and says nothing of liveness", () => {
  const r = row("unobserved-surface"), html = runHtml(ctx(r)), text = space(html);
  assert.equal(r.session.interactive, null);
  assert.ok(/<details class="text-xs"><summary[^>]*data-session-context/.test(html) && !/<details[^>]*\sopen[^>]*><summary[^>]*data-session-context/.test(html));
  assert.ok(text.includes("Interactive: its host did not say.") && text.includes("not recorded"));
  const known = row("verification"), shown = space(runHtml(ctx(known)));
  assert.ok(shown.includes(known.session.interactive ? "Interactive: its host recorded a person" : "Not interactive") || known.session.interactive === null);
  assert.ok(!text.includes("is running") || text.includes("still running"), "no liveness is claimed");
});
