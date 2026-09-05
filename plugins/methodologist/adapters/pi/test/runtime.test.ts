// Production bridge/runtime integration: real shared registry + core validation.

import { test } from "node:test";
import assert from "node:assert/strict";

import { createStdioBridgeDispatch, defaultBridgeConfig } from "../src/stdio-transport.ts";
import { selectMethodologyRequest } from "../src/translate.ts";

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
