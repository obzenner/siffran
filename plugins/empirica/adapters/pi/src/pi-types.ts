// Structural subset of the Pi (pi.dev) extension surface this adapter depends on.
//
// The real types live in `@earendil-works/pi-coding-agent` (declared as an
// optional peerDependency). The adapter is written against this hand-authored
// subset so it type-checks, tests, and installs without a network fetch of Pi;
// the shapes are structurally compatible with the documented ExtensionAPI. Only
// the capabilities this adapter actually uses are modelled here.
//
// Sources (github.com/earendil-works/pi, packages/coding-agent/docs/extensions.md
// and src/core/extensions/types.ts):
//   - pi.registerCommand(name, { description, handler })
//   - pi.on("resources_discover", h) -> { skillPaths, promptPaths, themePaths }
//   - pi.on("tool_call", h) -> ToolCallResult | void ; deny with { block, reason }
//   - pi.on("agent_settled", h) -> void  (observational; cannot veto completion)
//   - pi.sendUserMessage(text, { deliverAs })
//   - ctx.ui.notify(message, type)

export type NotifyType = "info" | "warning" | "error";

export interface UiContext {
  notify(message: string, type?: NotifyType): void;
  select?(
    title: string,
    options: string[],
    config?: { timeout?: number; signal?: AbortSignal },
  ): Promise<string | undefined>;
  setWidget?(id: string, lines: string[] | undefined): void;
  setStatus?(id: string, status: string | undefined): void;
}

export interface ExtensionContext {
  ui: UiContext;
  cwd?: string;
  isIdle?(): boolean;
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

// Pi's `tool_call` event: the tool the model is about to run, with mutable input.
export interface ToolCallEvent {
  toolName: string;
  toolCallId: string;
  input: Record<string, unknown>;
}

// Returned from a `tool_call` handler to deny the call. `block: true` blocks that
// single call; a returned `void`/`undefined` permits it. `terminate` is left to
// the host default (we never end the agent from a gate).
export interface ToolCallResult {
  block?: boolean;
  reason?: string;
  terminate?: boolean;
}

export type ToolCallHandler = (
  event: ToolCallEvent,
  ctx: ExtensionContext,
) => ToolCallResult | void | Promise<ToolCallResult | void>;

// `agent_settled` carries an empty event and returns void — it fires when Pi will
// not continue on its own. It cannot veto completion (ADR-32); a follow-up is
// enqueued through `pi.sendUserMessage`, not by returning a value.
export type AgentSettledHandler = (
  event: Record<string, never>,
  ctx: ExtensionContext,
) => void | Promise<void>;

export type ResourcesDiscoverHandler = (
  event: ResourcesDiscoverEvent,
  ctx: ExtensionContext,
) => ResourcesDiscoverResult | Promise<ResourcesDiscoverResult>;

// The delivery modes Pi's `sendUserMessage` accepts (real ExtensionAPI: no
// "nextTurn" — that is a `sendMessage` custom-message mode, not a user message).
export type MessageDelivery = "steer" | "followUp";

export interface ExtensionAPI {
  registerCommand(name: string, def: CommandDefinition): void;
  on(event: "resources_discover", handler: ResourcesDiscoverHandler): void;
  on(event: "tool_call", handler: ToolCallHandler): void;
  on(event: "agent_settled", handler: AgentSettledHandler): void;
  on(event: string, handler: (event: unknown, ctx: ExtensionContext) => unknown): void;
  /** Enqueue a user message; it always triggers a turn, and `deliverAs:
   * "followUp"` lands after the current turn's tool calls finish. Best-effort —
   * Pi may or may not start another turn. This is the real ExtensionAPI method
   * for a text nudge; `sendMessage` takes a CustomMessage object, not a string. */
  sendUserMessage?(
    text: string,
    options?: { deliverAs?: MessageDelivery },
  ): void;
}
