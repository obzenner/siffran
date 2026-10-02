import { test } from "node:test";
import assert from "node:assert/strict";

import { redactVerdict, resultText } from "../src/audit.ts";
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

test("replaces JSON-escaped workflow rendering before returning it to the author", () => {
  const event = {
    toolCallId: "audit",
    content: [{ type: "text", text: JSON.stringify({ output: BLOCK, finalOutput: BLOCK }) }],
    details: {
      results: [{ finalOutput: BLOCK }],
      mission: { summary: JSON.stringify({ output: BLOCK }).slice(0, -8) },
    },
  };
  redactVerdict(event);
  assert.deepEqual(event.content, [
    { type: "text", text: "[empirica-verdict recorded by host]" },
  ]);
  assert.doesNotMatch(JSON.stringify(event.details), /```empirica-verdict/);
});

test("selects the exact child finalOutput instead of duplicated workflow rendering", () => {
  assert.equal(resultText({
    toolCallId: "audit",
    content: [{ type: "text", text: `${BLOCK}\n${BLOCK}` }],
    details: { results: [{ finalOutput: BLOCK }] },
  }), BLOCK);
});

test("rejects ambiguous or malformed structured child result rows", () => {
  assert.equal(resultText({
    toolCallId: "audit", content: BLOCK,
    details: { results: [{ finalOutput: BLOCK }, { finalOutput: BLOCK }] },
  }), "");
  assert.equal(resultText({
    toolCallId: "audit", content: BLOCK,
    details: { results: [{ output: BLOCK }] },
  }), "");
});

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

test("fails closed when fallback attempts served more than one model", () => {
  const transcript = [
    assistant("first attempt", "provider", "model-a"),
    assistant(BLOCK, "provider", "model-b"),
  ].join("\n");
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

test("fails closed when an earlier assistant attempt lacks native provider or model", () => {
  const earlierWithoutModel = line({ type: "message", message: {
    role: "assistant", content: "first attempt", provider: "provider" } });
  assert.equal(identityFromSessionJsonl(
    [earlierWithoutModel, assistant(BLOCK, "provider", "model-b")].join("\n"), VERDICT), null);
  const earlierWithoutProvider = line({ type: "message", message: {
    role: "assistant", content: "first attempt", model: "model-b" } });
  assert.equal(identityFromSessionJsonl(
    [earlierWithoutProvider, assistant(BLOCK, "provider", "model-b")].join("\n"), VERDICT), null);
});

test("fails closed when an earlier assistant attempt has a blank provider or model", () => {
  for (const [provider, model] of [["  ", "model-b"], ["provider", " "], [7, "model-b"], ["provider", null]]) {
    assert.equal(identityFromSessionJsonl(
      [assistant("first attempt", provider, model), assistant(BLOCK, "provider", "model-b")].join("\n"),
      VERDICT), null, JSON.stringify([provider, model]));
  }
});

test("ignores host-synthesised error rows but never binds a verdict to one", () => {
  const synthetic = line({ type: "message", message: {
    role: "assistant", model: "<synthetic>", content: "provider error" } });
  assert.deepEqual(
    identityFromSessionJsonl([synthetic, assistant(BLOCK, "provider", "model-b")].join("\n"), VERDICT),
    { provider_id: "provider", model_id: "model-b", observed_by: "host", source: "pi-child-session" });
  const syntheticVerdict = line({ type: "message", message: {
    role: "assistant", provider: "provider", model: "<synthetic>", content: BLOCK } });
  assert.equal(identityFromSessionJsonl(
    [assistant("first attempt", "provider", "model-b"), syntheticVerdict].join("\n"), VERDICT), null);
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

// --- Pi 1.0 session format (docs/session-format.md, session version 3) -----------------------
// Rows below are shaped after the 1.0.0 docs: a session header, a leading system message, assistant
// messages that also record the `thinkingLevel` requested for that response, `model_change` and
// `thinking_level_change` entries, a `usage` entry, and the virtual-model router state stored as a
// `custom` entry. Only the final verdict-bearing assistant message's own provider/model is identity.

const pi1Row = (id: string, message: Record<string, unknown>): string =>
  line({ type: "message", id, parentId: null, timestamp: "2026-01-01T00:00:00.000Z", message });
const pi1Assistant = (id: string, text: string, extra: Record<string, unknown> = {}): string =>
  pi1Row(id, { role: "assistant", content: [{ type: "text", text }], api: "anthropic-messages",
    provider: "anthropic", model: "claude-sonnet-4-5", usage: {}, stopReason: "stop", thinkingLevel: "high",
    timestamp: 1_767_225_600_000, ...extra });
const PI1_HEADER = line({ type: "session", version: 3, id: "s1", timestamp: "2026-01-01T00:00:00.000Z", cwd: "/p" });
const PI1_EXPECTED = { provider_id: "anthropic", model_id: "claude-sonnet-4-5", observed_by: "host", source: "pi-child-session" };

test("a Pi 1.0 child transcript binds identity to the final assistant row, ignoring thinkingLevel", () => {
  const transcript = [
    PI1_HEADER,
    pi1Row("a0", { role: "system", content: "", sections: { preamble: "p" }, toolsAdded: [], timestamp: 1 }),
    line({ type: "model_change", id: "m1", parentId: "a0", timestamp: "t", provider: "openai", modelId: "gpt-4o" }),
    line({ type: "thinking_level_change", id: "t1", parentId: "m1", timestamp: "t", thinkingLevel: "low" }),
    pi1Row("u1", { role: "user", content: "audit", timestamp: 2 }),
    pi1Assistant("a1", "inspecting", { thinkingLevel: "low" }),
    line({ type: "usage", id: "us1", parentId: "a1", timestamp: "t", kind: "cache_warm", provider: "openai", model: "gpt-4o", usage: {} }),
    line({ type: "custom", id: "c1", parentId: "a1", timestamp: "t", customType: "pi.virtual-model-state",
      data: { provider: "openai", modelId: "gpt-4o", state: {} } }),
    pi1Assistant("a2", BLOCK, { thinkingLevel: "xhigh" }),
    // A selection recorded after the answer is still only a selection.
    line({ type: "model_change", id: "m2", parentId: "a2", timestamp: "t", provider: "google", modelId: "gemini" }),
  ].join("\n") + "\n";
  assert.deepEqual(identityFromSessionJsonl(transcript, VERDICT), PI1_EXPECTED);
});

test("a Pi 1.0 model_change or virtual-model state cannot supply or replace identity", () => {
  const onlySelections = [
    PI1_HEADER,
    line({ type: "model_change", id: "m1", parentId: null, timestamp: "t", provider: "anthropic", modelId: "claude-sonnet-4-5" }),
    line({ type: "custom", id: "c1", parentId: "m1", timestamp: "t", customType: "pi.virtual-model-state",
      data: { provider: "anthropic", modelId: "claude-sonnet-4-5", state: {} } }),
  ].join("\n") + "\n";
  assert.equal(identityFromSessionJsonl(onlySelections, VERDICT), null, "no assistant row, no identity");
  const stripped = [PI1_HEADER, pi1Assistant("a1", BLOCK, { provider: undefined }),
    line({ type: "model_change", id: "m1", parentId: "a1", timestamp: "t", provider: "anthropic", modelId: "claude-sonnet-4-5" }),
  ].join("\n") + "\n";
  assert.equal(identityFromSessionJsonl(stripped, VERDICT), null, "a selection cannot fill a missing native provider");
  const blankModel = [PI1_HEADER, pi1Assistant("a1", BLOCK, { model: " " })].join("\n") + "\n";
  assert.equal(identityFromSessionJsonl(blankModel, VERDICT), null);
});

test("a Pi 1.0 transcript whose assistant rows name two physical models is not an identity", () => {
  // thinkingLevel differing between rows is not a second identity; a second provider/model is.
  const sameModelTwoLevels = [PI1_HEADER, pi1Assistant("a1", "x", { thinkingLevel: "low" }),
    pi1Assistant("a2", BLOCK, { thinkingLevel: "max" })].join("\n") + "\n";
  assert.deepEqual(identityFromSessionJsonl(sameModelTwoLevels, VERDICT), PI1_EXPECTED);
  const routed = [PI1_HEADER, pi1Assistant("a1", "x", { provider: "openai", model: "gpt-4o" }),
    pi1Assistant("a2", BLOCK)].join("\n") + "\n";
  assert.equal(identityFromSessionJsonl(routed, VERDICT), null);
});
