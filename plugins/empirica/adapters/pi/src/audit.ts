import { createHash } from "node:crypto";

import type { ToolResultEvent } from "./pi-types.ts";

const BLOCK = /```empirica-verdict\s*\n([\s\S]*?)\n```/g;
const OPENER = /```empirica-verdict\b/g;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);

/** The `details.results` rows of a pi-subagents result: `null` when the event has none, `[]` when it is not an array. */
function resultRows(event: ToolResultEvent): unknown[] | null {
  if (!isRecord(event.details) || !("results" in event.details)) return null;
  const results = event.details.results;
  return Array.isArray(results) ? results : [];
}

export function resultText(event: ToolResultEvent): string {
  let value = event.content ?? event.details ?? "";
  const rows = resultRows(event);
  if (rows) {
    if (rows.length !== 1 || !isRecord(rows[0])) return "";
    const finalOutput = rows[0].finalOutput;
    if (typeof finalOutput !== "string" || !finalOutput) return "";
    value = finalOutput;
  }
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map((item) => {
    if (item && typeof item === "object" && "text" in item)
      return String((item as { text?: unknown }).text ?? "");
    return JSON.stringify(item);
  }).join("\n");
  return JSON.stringify(value);
}

export function verdictFromText(text: string): Record<string, unknown> | null {
  // Same rule as the Python parser: exactly one opener, and it yields the single match, so an
  // unclosed draft fence can never borrow a later fence's body.
  const openers = [...text.matchAll(OPENER)];
  const matches = [...text.matchAll(BLOCK)];
  if (openers.length !== 1 || matches.length !== 1 || matches[0].index !== openers[0].index)
    return null;
  try {
    const value = JSON.parse(matches[0][1]);
    return value && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown> : null;
  } catch { return null; }
}

function redact(value: unknown): unknown {
  if (typeof value === "string")
    return value.includes("```empirica-verdict")
      ? "[empirica-verdict recorded by host]" : value;
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === "object")
    return Object.fromEntries(Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => [key, redact(item)]));
  return value;
}

/** Redact synchronously before the caller performs any await. */
export function redactVerdict(event: ToolResultEvent): void {
  event.content = [{ type: "text", text: "[empirica-verdict recorded by host]" }];
  event.details = redact(event.details);
  // Pi (1.0.0 and later) carries a machine-readable copy of the result next to `content` and tells handlers that redact
  // `content` to replace it as well (Pi 1.1.0 `types.d.ts`, `ToolResultEvent.structuredContent`). Nothing here
  // consumes it, so it is dropped. Pi 1.1.0's own runner (`runner.js`) also drops it when a handler returns
  // `content` without it; the delete stays because the supported range includes earlier Pi versions and other
  // extensions see this event before Pi applies that rule.
  delete event.structuredContent;
}

/**
 * True when pi-subagents reports a child that is paused, detached or stopped: resumable, not finished. Its result
 * row carries `interrupted`, `detached` or `stopped` (exit 0, no `isError`) and may hold a complete fence in
 * `finalOutput`. Only a completed child yields a verdict and only a completed child is `completed`.
 */
export function childNotTerminal(event: ToolResultEvent): boolean {
  return (resultRows(event) ?? []).some((row) => isRecord(row)
    && ["interrupted", "detached", "stopped"].some((flag) => row[flag] === true));
}

export function lifecycleEvent(
  state: string,
  nativeId: string | null,
  resultDigest: string | null = null,
): Record<string, unknown> {
  const facts = { state, native_id: nativeId, result_digest: resultDigest };
  const fingerprint = "sha256:" + createHash("sha256")
    .update(JSON.stringify(facts, Object.keys(facts).sort()))
    .digest("hex");
  return { ...facts, fingerprint };
}

export function resultDigest(text: string): string {
  return "sha256:" + createHash("sha256").update(text).digest("hex");
}
