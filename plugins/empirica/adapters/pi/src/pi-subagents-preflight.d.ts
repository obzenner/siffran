declare module "pi-subagents/preflight" {
  export function resolveSubagentLaunchContract(input: Record<string, unknown>): Promise<
    { ok: true; contract: {
      agent: { filePath: string };
      model?: string;
      modelCandidates: string[];
    }} | { ok: false; message: string }
  >;
}
