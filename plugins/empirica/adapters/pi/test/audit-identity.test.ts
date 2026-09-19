import { test } from "node:test";
import assert from "node:assert/strict";

import {
  identityFromSessionJsonl,
  sessionFileFromDetails,
} from "../src/audit-identity.ts";

const VERDICT = { verdict: "pass", claims: [{ claim_id: "G1", verdict: "pass" }] };
const BLOCK = "```empirica-verdict\n" + JSON.stringify(VERDICT) + "\n```";

function line(value: unknown): string {
  return JSON.stringify(value);
}

function assistant(
  content: unknown,
  provider: unknown = "amazon-bedrock-us",
  model: unknown = "us.anthropic.claude-opus-4-8",
): string {
  return line({ type: "message", message: { role: "assistant", content, provider, model } });
}

test("extracts only a one-child host session path and ignores configured model metadata", () => {
  assert.equal(sessionFileFromDetails({ results: [{
    model: "configured/wrong-model",
    sessionFile: "/host/sessions/audit.jsonl",
  }] }), "/host/sessions/audit.jsonl");
  assert.equal(sessionFileFromDetails({ results: [] }), null);
  assert.equal(sessionFileFromDetails({ results: [
    { sessionFile: "/one" }, { sessionFile: "/two" },
  ] }), null);
  assert.equal(sessionFileFromDetails({ results: [{ sessionFile: "" }] }), null);
  assert.equal(sessionFileFromDetails({ sessionFile: "/not-a-child-row" }), null);
});

test("binds the admitted verdict to the final native assistant provider and model", () => {
  const transcript = [
    line({ type: "model_change", provider: "configured-provider", modelId: "configured-model" }),
    assistant([{ type: "text", text: "I will inspect the dossier." }]),
    line({ type: "message", message: { role: "toolResult", content: "evidence" } }),
    assistant([{ type: "text", text: BLOCK }]),
  ].join("\n") + "\n";

  assert.deepEqual(identityFromSessionJsonl(transcript, VERDICT), {
    provider_id: "amazon-bedrock-us",
    model_id: "us.anthropic.claude-opus-4-8",
    observed_by: "host",
    source: "pi-child-session",
  });
});

test("fails closed when the transcript verdict differs from the admitted verdict", () => {
  const different = { ...VERDICT, verdict: "fail" };
  assert.equal(identityFromSessionJsonl(assistant(BLOCK), different), null);
});

test("fails closed when the verdict-bearing message is not the final assistant message", () => {
  const transcript = [assistant(BLOCK), assistant("later assistant output")].join("\n");
  assert.equal(identityFromSessionJsonl(transcript, VERDICT), null);
});

test("fails closed when more than one assistant message contains a verdict", () => {
  const transcript = [assistant(BLOCK), assistant(BLOCK)].join("\n");
  assert.equal(identityFromSessionJsonl(transcript, VERDICT), null);
});

test("fails closed without native provider and model facts", () => {
  assert.equal(identityFromSessionJsonl(assistant(BLOCK, null), VERDICT), null);
  assert.equal(identityFromSessionJsonl(assistant(BLOCK, "provider", ""), VERDICT), null);
  const configuredOnly = [
    line({ type: "model_change", provider: "provider", modelId: "configured-model" }),
    line({ type: "message", message: { role: "assistant", content: BLOCK } }),
  ].join("\n");
  assert.equal(identityFromSessionJsonl(configuredOnly, VERDICT), null);
});

test("fails closed on malformed or non-object JSONL records", () => {
  assert.equal(identityFromSessionJsonl(`${assistant(BLOCK)}\n{broken`, VERDICT), null);
  assert.equal(identityFromSessionJsonl(`${line([])}\n${assistant(BLOCK)}`, VERDICT), null);
});

test("accepts a string-valued final assistant content", () => {
  assert.deepEqual(identityFromSessionJsonl(assistant(BLOCK, "provider", "model"), VERDICT), {
    provider_id: "provider",
    model_id: "model",
    observed_by: "host",
    source: "pi-child-session",
  });
});
