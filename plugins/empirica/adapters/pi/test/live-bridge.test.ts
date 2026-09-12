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
  assert.match(spawnInput.task, /nonce/);
  assert.match(pi.modelMessages.at(-1)!.content, /nonce/);
  const denied = await gate({ toolName: "subagent", toolCallId: "spawn-2", input: { agent: "empirica:empirica-auditor", task: "again" } }, ctx);
  assert.equal(denied?.block, true);
  assert.match(denied?.reason ?? "", /Obligation contract/); // P-3: denial carries the rendered contract
  // P-3 + P-7: the convergence denial names the audit in prose AND lists it as an obligation.
  await assert.rejects(() => pi.tools.get("report_convergence")!.execute("r", {}, new AbortController().signal, () => {}, ctx),
    (err: Error) => { assert.match(err.message, /audit/); assert.match(err.message, /empirica\/audit\//); assert.match(err.message, /Obligation contract/); return true; });

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
