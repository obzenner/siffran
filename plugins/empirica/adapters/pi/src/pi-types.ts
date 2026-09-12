export type NotifyType = "info" | "warning" | "error";
export interface UiContext { notify(message: string, type?: NotifyType): void; select?(title: string, options: string[], config?: { timeout?: number; signal?: AbortSignal }): Promise<string | undefined>; setWidget?(id: string, lines: string[] | undefined): void; setStatus?(id: string, status: string | undefined): void; }
export interface ExtensionContext { ui: UiContext; cwd?: string; isIdle?(): boolean; sessionManager?: { getEntries(): Array<{ type?: string; customType?: string; data?: unknown }> }; }
export interface CommandDefinition { description?: string; handler: (args: string, ctx: ExtensionContext) => Promise<void> | void; }
export interface ResourcesDiscoverEvent { cwd: string; reason: "startup" | "reload"; }
export interface ResourcesDiscoverResult { skillPaths?: string[]; promptPaths?: string[]; themePaths?: string[]; }
export interface ToolCallEvent { toolName: string; toolCallId: string; input: Record<string, unknown>; }
export interface ToolCallResult { block?: boolean; reason?: string; terminate?: boolean; }
/** Pi emits the completed child output through tool_result; its exact payload is
 * versioned by Pi, so the adapter deliberately accepts the common content/result
 * shapes while retaining the toolCallId correlation. */
export interface ToolResultEvent { toolCallId: string; toolName?: string; isError?: boolean; error?: unknown; result?: unknown; content?: unknown; }
export type ToolCallHandler = (event: ToolCallEvent, ctx: ExtensionContext) => ToolCallResult | void | Promise<ToolCallResult | void>;
export type AgentSettledHandler = (event: Record<string, never>, ctx: ExtensionContext) => void | Promise<void>;
export type ResourcesDiscoverHandler = (event: ResourcesDiscoverEvent, ctx: ExtensionContext) => ResourcesDiscoverResult | Promise<ResourcesDiscoverResult>;
export type MessageDelivery = "steer" | "followUp";
export interface ToolDefinition { name: string; label?: string; description: string; parameters?: unknown; execute: (toolCallId: string, params: unknown, signal: AbortSignal, onUpdate: (u: unknown) => void, ctx: ExtensionContext) => Promise<{ content: Array<{type: "text"; text: string}>; details?: unknown }>; }
export interface CompactionPreparation { firstKeptEntryId: string; tokensBefore: number; }
export interface ExtensionAPI {
 registerCommand(name: string, def: CommandDefinition): void;
 registerTool?(def: ToolDefinition): void;
 appendEntry?(customType: string, data?: unknown): void;
 sendMessage?(message: { customType: string; content: string; display?: boolean }, options?: { deliverAs?: MessageDelivery }): void;
 on(event: "tool_result", handler: (event: ToolResultEvent, ctx: ExtensionContext) => unknown): void;
 on(event: "resources_discover", handler: ResourcesDiscoverHandler): void;
 on(event: "tool_call", handler: ToolCallHandler): void;
 on(event: "agent_settled", handler: AgentSettledHandler): void;
 on(event: "session_start", handler: (event: unknown, ctx: ExtensionContext) => unknown): void;
 on(event: "session_before_compact", handler: (event: { preparation: CompactionPreparation }, ctx: ExtensionContext) => unknown): void;
 on(event: "before_agent_start", handler: (event: unknown, ctx: ExtensionContext) => unknown): void;
 on(event: string, handler: (event: unknown, ctx: ExtensionContext) => unknown): void;
 sendUserMessage?(text: string, options?: { deliverAs?: MessageDelivery }): void;
}
