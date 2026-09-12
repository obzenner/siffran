import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { createEmpiricaExtension } from "../src/index.ts";
import { createStdioBridgeDispatch } from "../src/stdio-transport.ts";
import { FakePi, FakeUi } from "./fakes.ts";

const py = process.env.EMPIRICA_PYTHON ?? "python3";
let pythonAvailable = true;
try { execFileSync(py, ["--version"], { stdio: "ignore" }); } catch { pythonAvailable = false; }

const envelope = (result: any) => ({ protocol: "empirica/v1", request_id: "test", result });
const hash = async (text: string) => {
  const crypto = await import("node:crypto");
  return crypto.createHash("sha256").update(text).digest("hex");
};

// P-5 deliberately uses the production stdio process and filesystem adapters, rather than
// an in-process fake dispatch. It is skipped only when the configured Python executable is absent.
test("P-5 real Pi bridge preserves the model-visible contract lifecycle", { skip: !pythonAvailable ? "python3 is missing" : false }, async () => {
  const home = mkdtempSync(join(tmpdir(), "empirica-pi-home-"));
  const cwd = mkdtempSync(join(tmpdir(), "empirica-pi-repo-"));
  execFileSync("git", ["init", "-q", cwd]);
  const bridge = createStdioBridgeDispatch({ command: py, args: [join(process.cwd(), "bridge.py")], cwd,
    env: { ...process.env, EMPIRICA_HOME: home, EMPIRICA_MAX_IDLE_STOPS: "10" }, timeoutMs: 10_000 });
  const requests: any[] = [];
  const dispatch = async (request: any) => { requests.push(request); return bridge(request); };
  const pi = new FakePi();
  createEmpiricaExtension({ dispatch, deriveSelector: () => ({ project: "p5", session: "live" }), startRunOptions: { maxSpawns: 1 } })(pi);
  const ctx = { ui: new FakeUi(), cwd };
  await pi.command("empirica").handler("--multi-provider retry the bridge", ctx);
  assert.equal(pi.modelMessages.length, 1, (ctx.ui as FakeUi).last()?.message ?? "no notification");
  assert.match(pi.modelMessages[0].content, /Goal: retry the bridge/);
  assert.match(pi.modelMessages[0].content, /multi_provider/);
  assert.match(pi.modelMessages[0].content, /no contract yet/);
  const handle = (await bridge(requests[0]) as any).result.run.id;
  // The extension's opaque handle is the authoritative one (and is intentionally not inferred).
  const status = await pi.tools.get("empirica_status")!.execute("s", {}, new AbortController().signal, () => {}, ctx);
  assert.match(status.content[0].text, /retry the bridge/);
  assert.match(status.content[0].text, /no contract yet/);
  await assert.rejects(() => pi.tools.get("report_convergence")!.execute("r", {}, new AbortController().signal, () => {}, ctx), /claim graph is missing|fail closed/);

  const graph = { root: "G0", nodes: {
    G0: { type: "Goal", text: "bridge retry is bounded", kind: "needs-data", confidence: 0.0, blocked: null, refuted_by: null },
    C1: { type: "Goal", text: "bridge retries preserve request identity", kind: "needs-data", confidence: 0.0, blocked: null, refuted_by: null },
  }, edges: [{ from: "G0", to: "C1", type: "SupportedBy" }] };
  const knowledge = pi.tools.get("empirica_knowledge")!;
  let out = await knowledge.execute("k", { kind: "route", reason: "multi-provider route" }, new AbortController().signal, () => {}, ctx);
  out = await knowledge.execute("k", { kind: "graph", graph }, new AbortController().signal, () => {}, ctx);
  assert.match(out.content[0].text, /empirica\/G0 \[require\]/);
  assert.match(out.content[0].text, /\[observed: null\]/);
  for (const [id, text] of [["G0", graph.nodes.G0.text], ["C1", graph.nodes.C1.text]] as const) {
    const statement = { _type: "https://in-toto.io/Statement/v1", subject: [{ name: id, digest: { sha256: await hash(text) } }], predicateType: "https://empirica.dev/attestation/research/v1", predicate: { fold: "research", kind: "runtime", source: "bridge test", citation: "live bridge", result: "supports", ts: "2026-09-12T00:00:00Z" } };
    out = await knowledge.execute("k", { kind: "evidence_leaf", evidence_id: `research-${id}`, statement, verdicts: { approve: { ok: true, reason: "test" }, refute: { ok: false, reason: "test" } } }, new AbortController().signal, () => {}, ctx);
  }
  assert.match(out.content[0].text, /\[observed: pass\]/);
  const revised = structuredClone(graph); revised.nodes.G0.confidence = 0.9; revised.nodes.C1.confidence = 0.9;
  out = await knowledge.execute("k", { kind: "graph", graph: revised }, new AbortController().signal, () => {}, ctx);
  assert.doesNotMatch(out.content[0].text, /\[retired@/);
  const contract = (out.details as any).run.contract;
  const audit = contract.obligations.filter((o: any) => o.id.startsWith("empirica/audit/"));
  assert.equal(audit.length, 1);
  assert.equal(audit[0].witnesses[0].kind, "judgment");
  // P-2/P-7 as the model sees them: every claim satisfied, the audit the ONLY residual.
  assert.deepEqual(contract.verdict.satisfied.sort(), ["empirica/C1", "empirica/G0"]);
  assert.deepEqual(contract.verdict.residual, [audit[0].id]);
  assert.match(out.content[0].text, /empirica\/audit\//);

  const gate = pi.toolCall();
  assert.equal(await gate({ toolName: "subagent", toolCallId: "list", input: { action: "list" } }, ctx), undefined);
  const spawnInput: any = { agent: "empirica:empirica-auditor", task: "audit the approved claims" };
  assert.equal((await gate({ toolName: "subagent", toolCallId: "spawn", input: spawnInput }, ctx)), undefined);
  // E: the child task carries rubric + dossier + nonce + output contract; the AUTHOR never sees the nonce.
  assert.match(spawnInput.task, /empirica-verdict/);
  assert.match(spawnInput.task, /Fold-1 citations are REAL/);            // rubric
  assert.match(spawnInput.task, /bridge retries preserve request identity/); // dossier names every claim
  assert.match(spawnInput.task, /claim_digest=/);
  const nonce = /Your nonce: ([0-9a-f]+)/.exec(spawnInput.task)?.[1];
  assert.ok(nonce, "nonce injected into the child task");
  assert.equal(spawnInput.async, false, "auditor launch is forced foreground so the verdict returns in the tool result");
  for (const m of pi.modelMessages) assert.doesNotMatch(m.content, new RegExp(nonce!));
  assert.match(pi.modelMessages.at(-1)!.content, /verdict is recorded by the host/);

  // P-8/P-3: a second spawn while the first is out is over budget (max_spawns=1) and the denial carries the contract.
  const denied = await gate({ toolName: "subagent", toolCallId: "spawn-2", input: { agent: "empirica:empirica-auditor", task: "again" } }, ctx);
  assert.equal(denied?.block, true);
  assert.match(denied?.reason ?? "", /Obligation contract/); // P-3: denial carries the rendered contract

  // D: the child (played by the test) builds its verdict ONLY from GetArgument — the digests it is
  // given must be the ones coverage_check recomputes, or this whole design is theatre.
  const argument = ((await dispatch({ protocol: "empirica/v1", request_id: "arg", command: { type: "GetArgument", run_id: handle } })).result as any).run.argument;
  assert.ok(argument, "GetArgument returns an argument");
  assert.equal(argument.tickets.some((t: any) => "nonce" in t), false, "tickets never expose the nonce");
  assert.deepEqual(argument.claims.map((c: any) => c.id), ["C1", "G0"]);
  assert.ok(argument.claims.every((c: any) => c.evidence.length >= 1), "every claim lists its evidence leaves");
  const verdict = { verdict: "pass", nonce, auditor: "test-auditor", argument_digest: argument.argument_digest,
    claims_reviewed: argument.claims.map((c: any) => ({ claim_id: c.id, claim_digest: c.claim_digest, evidence_digest: c.evidence_digest })),
    findings: [], ts: "2026-09-12T00:00:00Z" };
  const toolResult = pi.handlers.get("tool_result") as any;
  const resultEvent: any = { toolCallId: "spawn", content: [{ type: "text", text: "Audit done.\n```empirica-verdict\n" + JSON.stringify(verdict) + "\n```" }] };
  await toolResult(resultEvent, ctx);
  const appended = JSON.stringify(resultEvent.content);
  assert.match(appended, /host recorded the auditor's verdict \(pass\)/);
  assert.doesNotMatch(appended, new RegExp(nonce!));
  // The audit obligation is now satisfied and the run may converge.
  const converged = await pi.tools.get("report_convergence")!.execute("r2", {}, new AbortController().signal, () => {}, ctx);
  assert.match(converged.content[0].text, /empirica\/audit\//);
  assert.equal((converged.details as any).run.contract.verdict.residual.length, 0, "no residual obligation after a valid audit");
  const compact = await (pi.handlers.get("session_before_compact") as any)({ preparation: { firstKeptEntryId: "e", tokensBefore: 10 } }, ctx);
  assert.match(compact.compaction.summary, /retry the bridge/); // run.goal survives compaction
  assert.match(compact.compaction.summary, /Obligation contract/);
  assert.match(compact.compaction.summary, /empirica\/audit\//);
  assert.equal(typeof compact.compaction.details.contract, "object");
  const restored = await dispatch({ protocol: "empirica/v1", request_id: "restore", command: { type: "GetRun", run_id: handle } });
  const restoredAgain = await dispatch({ protocol: "empirica/v1", request_id: "restore-2", command: { type: "GetRun", run_id: handle } });
  assert.equal((restored.result as any).run.revision, (restoredAgain.result as any).run.revision);
  // Operational revision moves with reserve/ticket/stop-attempt (they are state), but the CONTRACT
  // revision must not: ticketing, denial, the convergence check and compaction change no obligation.
  assert.equal((restored.result as any).run.contract.revision, contract.revision);
  assert.equal((restored.result as any).run.goal, "retry the bridge");
});
