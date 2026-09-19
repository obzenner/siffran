import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { execFileSync } from "node:child_process";

import { createEmpiricaExtension, DEFAULT_SKILLS_DIR } from "../src/index.ts";
import { createStdioBridgeDispatch, defaultBridgeConfig } from "../src/stdio-transport.ts";
import { createPrivateIngress } from "../src/private-transport.ts";
import { FakePi, FakeUi, fakeCtx } from "./fakes.ts";
import type { ToolResultEvent } from "../src/pi-types.ts";

test("Pi public tools and injected foreground observations satisfy adapter conformance", async () => {
  const root = mkdtempSync(join(tmpdir(), "empirica-pi-reach-"));
  execFileSync("git", ["init", "-q", root]);
  writeFileSync(join(root, "probe.py"), "print('reachable')\n");
  const previous = {
    home: process.env.EMPIRICA_HOME,
    repo: process.env.EMPIRICA_REPO_DIR,
    cwd: process.cwd(),
  };
  process.env.EMPIRICA_HOME = join(root, "state");
  process.env.EMPIRICA_REPO_DIR = root;
  process.chdir(root);
  try {
    const config = defaultBridgeConfig();
    config.cwd = root;
    config.env = { ...process.env };
    const pi = new FakePi();
    createEmpiricaExtension({
      dispatch: createStdioBridgeDispatch(config),
      privateIngress: createPrivateIngress(),
      deriveSelector: () => ({ project: "pi-reach-project", session: "pi-reach-session" }),
      resolveAuditContract: async () => ({
        agentFilePath: resolve(DEFAULT_SKILLS_DIR, "..", "agents", "pi", "empirica-auditor.md"),
        model: "bedrock/auditor-model",
      }),
    })(pi);
    const ctx = fakeCtx(root);
    ctx.model = { provider: "bedrock", id: "author-model" };
    await pi.command("empirica").handler("prove the Pi host path", ctx);

    const execute = async (name: string, params: unknown) =>
      pi.tools.get(name)!.execute("tool", params, new AbortController().signal, () => {}, ctx);
    const observe = async (action: Record<string, unknown>) =>
      execute("empirica_observe", { action });

    await observe({ kind: "route", reason: "route first" });
    await observe({ kind: "investigate" });
    await observe({ kind: "graph", payload: {
      root: "G0",
      claims: [{ id: "G0", text: "Pi can drive v2.", gating: true,
                 kind: "needs-experiment" }],
      edges: [],
    }});
    await observe({ kind: "research", claim_id: "G0", source_kind: "code",
                    result: "supports", payload: { source_ref: "probe.py" } });
    await observe({ kind: "spike_request", claim_id: "G0",
                    command: "python3 probe.py", dependent_files: ["probe.py"] });
    await observe({ kind: "freeze" });

    const auditInput: Record<string, unknown> = {
      agent: "empirica.empirica-auditor", task: "audit",
    };
    const toolCall = pi.handlers.get("tool_call") as
      (event: unknown, context: typeof ctx) => Promise<unknown>;
    const admission = await toolCall({
      toolName: "subagent", toolCallId: "pi-audit-1", input: auditInput,
    }, ctx);
    assert.equal(admission, undefined);
    assert.equal(auditInput.async, false);
    assert.match(String(auditInput.task), /AUDIT DOSSIER/);

    const argumentResult = await execute("empirica_read", { operation: "GetArgument" });
    const argument = (argumentResult.details as { argument: Record<string, unknown> }).argument;
    const claims = argument.claims as Array<Record<string, unknown>>;
    const verdict = {
      verdict: "pass",
      findings: ["Pi positive host trace"],
      argument_digest: argument.argument_digest,
      goal_digest: argument.goal_digest,
      frozen_scope_digest: argument.frozen_scope_digest,
      deferred_scope_digest: argument.deferred_scope_digest,
      reviewed_claims: claims.filter((claim) => claim.gating && claim.state === "approved")
        .map((claim) => ({ claim_id: claim.claim_id, evidence_digest: claim.evidence_digest })),
      scope_review: "pass",
    };
    const nativeSession = join(root, "audit-session.jsonl");
    writeFileSync(nativeSession, JSON.stringify({
      type: "message",
      message: {
        role: "assistant",
        content: [{ type: "text", text: "```empirica-verdict\n" +
          JSON.stringify(verdict) + "\n```" }],
        provider: "bedrock",
        model: "native-auditor-model",
      },
    }) + "\n");
    const event: ToolResultEvent = {
      toolCallId: "pi-audit-1", toolName: "subagent",
      content: "```empirica-verdict\n" + JSON.stringify(verdict) + "\n```",
      details: { results: [{
        model: "bedrock/configured-auditor-model", sessionFile: nativeSession,
      }] },
    };
    const toolResult = pi.handlers.get("tool_result") as
      (event: ToolResultEvent, context: typeof ctx) => Promise<unknown>;
    await toolResult(event, ctx);
    assert.doesNotMatch(String(event.content), /```empirica-verdict/);

    const final = await execute("report_convergence", {});
    const result = final.details as { type: string; converged: boolean; run: { status: string } };
    assert.equal(result.type, "Allow");
    assert.equal(result.converged, true);
    assert.equal(result.run.status, "converged");
  } finally {
    process.chdir(previous.cwd);
    if (previous.home === undefined) delete process.env.EMPIRICA_HOME;
    else process.env.EMPIRICA_HOME = previous.home;
    if (previous.repo === undefined) delete process.env.EMPIRICA_REPO_DIR;
    else process.env.EMPIRICA_REPO_DIR = previous.repo;
  }
});
