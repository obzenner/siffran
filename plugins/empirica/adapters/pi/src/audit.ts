import { createHash } from "node:crypto";

import type { ToolResultEvent } from "./pi-types.ts";

const BLOCK = /```empirica-verdict\s*\n([\s\S]*?)\n```/g;

export function resultText(event: ToolResultEvent): string {
  let value = event.content ?? event.details ?? event.error ?? "";
  if (event.details && typeof event.details === "object" && !Array.isArray(event.details)
      && "results" in event.details) {
    const results = (event.details as { results?: unknown }).results;
    if (!Array.isArray(results) || results.length !== 1) return "";
    const row = results[0];
    if (!row || typeof row !== "object" || Array.isArray(row)) return "";
    const finalOutput = (row as { finalOutput?: unknown }).finalOutput;
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
  const matches = [...text.matchAll(BLOCK)];
  if (matches.length !== 1) return null;
  try {
    const value = JSON.parse(matches[0][1]);
    return value && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown> : null;
  } catch { return null; }
}

function redact(value: unknown): unknown {
  if (typeof value === "string")
    return value.replace(/```empirica-verdict\s*\n[\s\S]*?\n```/g,
      "[empirica-verdict recorded by host]");
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
  event.error = redact(event.error);
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
