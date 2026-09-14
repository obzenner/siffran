// Pi extension API types — the official surface the adapter registers against.
// Retained from the Pi extension contract; only the event handlers the adapter
// actually wires are typed. No removed-feature types (tool_result, agent_settled).

export type NotifyType = "info" | "warning" | "error";

export interface UiContext {
  notify(message: string, type?: NotifyType): void;
}

export interface ExtensionContext {
  ui: UiContext;
  cwd?: string;
  sessionManager?: {
    getEntries(): Array<{ type?: string; customType?: string; data?: unknown }>;
  };
}

export interface CommandDefinition {
  description?: string;
  handler: (args: string, ctx: ExtensionContext) => Promise<void> | void;
}

export interface ResourcesDiscoverEvent {
  cwd: string;
  reason: "startup" | "reload";
}

export interface ResourcesDiscoverResult {
  skillPaths?: string[];
  promptPaths?: string[];
  themePaths?: string[];
}

export interface ToolCallEvent {
  toolName: string;
  toolCallId: string;
  input: Record<string, unknown>;
}

export interface ToolCallResult {
  block?: boolean;
  reason?: string;
  terminate?: boolean;
}

export type ToolCallHandler = (
  event: ToolCallEvent,
  ctx: ExtensionContext,
) => ToolCallResult | void | Promise<ToolCallResult | void>;

export type ResourcesDiscoverHandler = (
  event: ResourcesDiscoverEvent,
  ctx: ExtensionContext,
) => ResourcesDiscoverResult | Promise<ResourcesDiscoverResult>;

export interface CompactionPreparation {
  firstKeptEntryId: string;
  tokensBefore: number;
}

export interface ToolDefinition {
  name: string;
  label?: string;
  description: string;
  parameters?: unknown;
  execute: (
    toolCallId: string,
    params: unknown,
    signal: AbortSignal,
    onUpdate: (u: unknown) => void,
    ctx: ExtensionContext,
  ) => Promise<{ content: Array<{ type: "text"; text: string }> }>;
}

export interface ExtensionAPI {
  registerCommand(name: string, def: CommandDefinition): void;
  registerTool?(def: ToolDefinition): void;
  appendEntry?(customType: string, data?: unknown): void;
  sendMessage?(
    message: { customType: string; content: string; display?: boolean },
  ): void;
  on(event: "resources_discover", handler: ResourcesDiscoverHandler): void;
  on(event: "tool_call", handler: ToolCallHandler): void;
  on(
    event: "session_start",
    handler: (event: unknown, ctx: ExtensionContext) => unknown,
  ): void;
  on(
    event: "session_before_compact",
    handler: (
      event: { preparation: CompactionPreparation },
      ctx: ExtensionContext,
    ) => unknown,
  ): void;
  on(event: string, handler: unknown): void;
}
