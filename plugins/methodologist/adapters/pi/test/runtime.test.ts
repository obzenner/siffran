// Production bridge/runtime integration: real shared registry + core validation.

import { test } from "node:test";
import assert from "node:assert/strict";

import { createStdioBridgeDispatch, defaultBridgeConfig } from "../src/stdio-transport.ts";
import { listMethodologiesRequest, selectMethodologyRequest } from "../src/translate.ts";

test("default bridge returns the complete ordered methodology catalog", async () => {
  const dispatch = createStdioBridgeDispatch(defaultBridgeConfig());
  const response = await dispatch(listMethodologiesRequest("runtime-catalog"));

  assert.equal(response.result.type, "MethodologyCatalog");
  if (response.result.type !== "MethodologyCatalog") return;
  assert.ok(response.result.methodologies.length >= 9);
  assert.equal(response.result.methodologies[0]?.name, "formal-reasoning");
  assert.ok(response.result.methodologies.some((entry) => entry.name === "evidence-preserving-refinement"));
  assert.ok(response.result.methodologies.every((entry) => entry.use_when.length > 0));
});

test("default bridge validates an explicit name and returns six phases", async () => {
  const dispatch = createStdioBridgeDispatch(defaultBridgeConfig());
  const response = await dispatch(
    selectMethodologyRequest(
      "Explicitly requested by the user.",
      "formal-reasoning",
      "runtime-explicit",
    ),
  );

  assert.equal(response.protocol, "methodologist/v1");
  assert.equal(response.request_id, "runtime-explicit");
  assert.equal(response.result.type, "MethodologySelected");
  if (response.result.type !== "MethodologySelected") return;
  assert.equal(response.result.methodology, "formal-reasoning");
  assert.equal(response.result.phases.length, 6);
  assert.deepEqual(
    response.result.phases.map((phase) => phase.number),
    [1, 2, 3, 4, 5, 6],
  );
});

test("default bridge rejects an unknown explicit name", async () => {
  const dispatch = createStdioBridgeDispatch(defaultBridgeConfig());
  const response = await dispatch(
    selectMethodologyRequest("Explicitly requested.", "not-a-method", "runtime-unknown"),
  );
  assert.deepEqual(response.result, { type: "Fault", code: "unknown_methodology" });
});

test("default bridge refuses deterministic auto-selection", async () => {
  const dispatch = createStdioBridgeDispatch(defaultBridgeConfig());
  const response = await dispatch(
    selectMethodologyRequest("choose by task semantics", null, "runtime-bare"),
  );
  assert.deepEqual(response.result, { type: "Fault", code: "invalid_request" });
});
