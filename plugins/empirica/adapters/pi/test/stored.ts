// Stored goldens and fixtures hold "<contract-digest>" where the public contract's digest belongs (G10): any edit of the
// contract changes it, and it used to be copied into every file. A reader puts the live digest in when it LOADS the file,
// before the renderer validates or compares, so a response that carries a wrong digest still fails.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import * as path from "node:path";

export const CONTRACT_DIGEST_PLACEHOLDER = "<contract-digest>";

const CONTRACT = path.resolve(import.meta.dirname, "..", "..", "..", "..", "..", "contracts", "empirica", "v2",
  "public-contract.json");

// The same canonical JSON as core/canonical.py: sorted keys, compact, literal UTF-8.
function canonical(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (value !== null && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return "{" + Object.keys(object).sort().map((key) => JSON.stringify(key) + ":" + canonical(object[key])).join(",") + "}";
  }
  return JSON.stringify(value);
}

/** The digest of the public contract in the repository, computed the way the Python core computes it. */
export function liveContractDigest(): string {
  return "sha256:" + createHash("sha256").update(canonical(JSON.parse(readFileSync(CONTRACT, "utf8"))), "utf8").digest("hex");
}

/** A stored file's text with the live digest where the placeholder stands; parse it with JSON.parse. */
export function readStoredText(file: string, encoding: BufferEncoding, digest: string = liveContractDigest()): string {
  return readFileSync(file, encoding).replaceAll(CONTRACT_DIGEST_PLACEHOLDER, digest);
}
