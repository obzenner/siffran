// What the audit child can retrieve, observed (ADR-0067). The one place a tool name becomes a retrieval kind.
//
// The auditor's `contract.tools.effectiveAllowlist` is what pi-subagents' launch preflight reports the child
// may call. Recorded at `/empirica` start and re-sent at audit launch; a divergence refuses the launch. A
// record whose kind is not covered goes to a human instead of the audit, so this table may only ever *credit*
// what a reviewed tool is known to do, and an unknown tool adds nothing.

export type RetrievalKind = "local" | "web" | "workspace";

/**
 * Reviewed tool -> kinds. `read` opens files on this machine: the workspace and any other local path.
 * `grep`, `find` and `ls` only locate files and credit nothing. No reviewed pi-subagents version grants the
 * child a web-fetch tool, and Pi children do not inherit extension tools (pi-subagents
 * `docs/extension-api.md:312-313`), so `web` appears only once a reviewed tool is added here.
 */
export const TOOL_RETRIEVAL: Readonly<Record<string, readonly RetrievalKind[]>> = {
  read: ["local", "workspace"],
};

/** The sorted, de-duplicated kinds the allowlist covers; tools not in the table add nothing. */
export function auditRetrievalFrom(effectiveAllowlist: readonly string[]): RetrievalKind[] {
  const kinds = new Set<RetrievalKind>();
  for (const tool of effectiveAllowlist)
    for (const kind of Object.hasOwn(TOOL_RETRIEVAL, tool) ? TOOL_RETRIEVAL[tool] : []) kinds.add(kind);
  return [...kinds].sort();
}
