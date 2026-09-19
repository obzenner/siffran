import { isDeepStrictEqual } from "node:util";

import { verdictFromText } from "./audit.ts";

export interface ObservedChildIdentity {
  provider_id: string;
  model_id: string;
  observed_by: "host";
  source: "pi-child-session";
}

/**
 * Return the host-owned child session path only for the one-child foreground
 * result shape. Requested/configured model fields are deliberately ignored.
 */
export function sessionFileFromDetails(details: unknown): string | null {
  if (!details || typeof details !== "object" || Array.isArray(details)) return null;
  const results = (details as Record<string, unknown>).results;
  if (!Array.isArray(results) || results.length !== 1) return null;
  const row = results[0];
  if (!row || typeof row !== "object" || Array.isArray(row)) return null;
  const sessionFile = (row as Record<string, unknown>).sessionFile;
  return typeof sessionFile === "string" && sessionFile.trim()
    ? sessionFile : null;
}

function messageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const record = item as Record<string, unknown>;
    return record.type === "text" && typeof record.text === "string"
      ? [record.text] : [];
  }).join("\n");
}

/**
 * Bind the admitted verdict to the native provider/model on the final assistant
 * message in a complete host-generated Pi child transcript.
 *
 * Any malformed or ambiguous transcript returns null so independence remains
 * unverified. Model-change records and configured result metadata are not
 * identity evidence.
 */
export function identityFromSessionJsonl(
  jsonl: string,
  expectedVerdict: Record<string, unknown>,
): ObservedChildIdentity | null {
  const assistantMessages: Array<Record<string, unknown>> = [];
  const verdictMessages: Array<{
    message: Record<string, unknown>;
    verdict: Record<string, unknown>;
  }> = [];

  for (const line of jsonl.split("\n")) {
    if (!line.trim()) continue;
    let record: unknown;
    try {
      record = JSON.parse(line);
    } catch {
      return null;
    }
    if (!record || typeof record !== "object" || Array.isArray(record)) return null;
    const outer = record as Record<string, unknown>;
    if (outer.type !== "message") continue;
    const candidate = outer.message;
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) continue;
    const message = candidate as Record<string, unknown>;
    if (message.role !== "assistant") continue;
    assistantMessages.push(message);
    const verdict = verdictFromText(messageText(message.content));
    if (verdict) verdictMessages.push({ message, verdict });
  }

  if (assistantMessages.length === 0 || verdictMessages.length !== 1) return null;
  const observed = verdictMessages[0];
  if (observed.message !== assistantMessages.at(-1)) return null;
  if (!isDeepStrictEqual(observed.verdict, expectedVerdict)) return null;

  const provider = observed.message.provider;
  const model = observed.message.model;
  if (typeof provider !== "string" || !provider.trim()
      || typeof model !== "string" || !model.trim()) return null;
  return {
    provider_id: provider,
    model_id: model,
    observed_by: "host",
    source: "pi-child-session",
  };
}
