// Pi extension API types — the official surface the adapter registers against.
// Retained from the Pi extension contract; only the event handlers the adapter
// actually wires are typed, including the foreground subagent result boundary.

export type NotifyType = "info" | "warning" | "error";

export interface UiTheme {
  fg(color: "accent" | "dim" | "warning", text: string): string;
}

export interface UiContext {
  notify(message: string, type?: NotifyType): void;
  custom?<T>(factory: (
    tui: { requestRender(): void }, theme: UiTheme, keybindings: unknown,
    done: (result: T) => void,
  ) => { render(width: number): string[]; invalidate(): void; handleInput(data: string): void }): Promise<T>;
  select?(title: string, options: string[], opts?: { timeout?: number; signal?: AbortSignal }): Promise<string | undefined>;
  confirm?(title: string, message: string, opts?: { timeout?: number; signal?: AbortSignal }): Promise<boolean>;
  input?(title: string, placeholder?: string, opts?: { timeout?: number; signal?: AbortSignal }): Promise<string | undefined>;
}

export interface ExtensionContext {
  ui: UiContext;
  hasUI?: boolean;
  mode?: "tui" | "rpc" | "print" | "json";
  cwd?: string;
  model?: { id: string; provider: string };
  modelRegistry?: {
    getError?(): string | undefined;
    getAvailable(): Array<{ id: string; provider: string }>;
  };
  sessionManager?: {
    getEntries(): Array<{ type?: string; customType?: string; data?: unknown }>;
  };
  isIdle?(): boolean;
}

export interface CommandDefinition {
  description?: string;
  /** Pi declares ``Promise<void>`` in 1.0.0 and 1.1.0; a handler that returns nothing is not accepted. */
  handler: (args: string, ctx: ExtensionContext) => Promise<void>;
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

export interface ToolResultEvent {
  toolCallId: string;
  toolName?: string;
  /** The only failure signal: no Pi version from 1.0.0 to 1.1.0 declares or emits `tool_result.error`. */
  isError?: boolean;
  details?: unknown;
  content?: unknown;
  /** Pi (1.0.0 and later): the machine-readable result; a handler that redacts `content` must replace this too. */
  structuredContent?: unknown;
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

/** A draft the adapter appends at a boundary (Pi `CustomMessageEntryDraft`, 1.0.0 and later). */
export interface CustomMessageDraft {
  type: "custom_message";
  customType: string;
  content: string;
  display: boolean;
}

/** Pi `AgentBeforeSettleEvent` (1.0.0 and later): the entries about to be persisted and whether Pi already continues. */
export interface BoundaryEvent {
  type?: "agent_before_settle";
  entries: Array<{ type: string }>;
  continue: boolean;
}

/** Pi `BoundaryResult`: entries to persist in order, and `continue: true` to ensure one more model request. */
export interface BoundaryResult {
  entries?: Array<{ type: string }>;
  continue?: boolean;
}

export interface CompactionPreparation {
  firstKeptEntryId: string;
  tokensBefore: number;
}

export interface ToolDefinition {
  name: string;
  /** Required by Pi (``types.d.ts:443`` in 1.0.0). */
  label: string;
  description: string;
  /** Required by Pi (``:451`` in 1.0.0): the tool's parameter schema. */
  parameters: Record<string, unknown>;
  /**
   * Pi declares ``signal: AbortSignal | undefined`` and ``onUpdate`` optional in 1.0.0 (``types.d.ts:492``) and 1.1.0;
   * a tool must not assume either is present.
   */
  execute: (
    toolCallId: string,
    params: unknown,
    signal: AbortSignal | undefined,
    onUpdate: ((partial: { content: Array<{ type: "text"; text: string }>; details: unknown }) => void) | undefined,
    ctx: ExtensionContext,
  ) => Promise<{ content: Array<{ type: "text"; text: string }>; details: unknown }>;
}

/**
 * Where Pi loaded a resource from (mirror of ``SourceInfo`` in Pi's ``dist/core/source-info.d.ts``,
 * Pi 1.0.0 lines 4-10). Only the fields the owner resolver reads are mirrored.
 */
export interface SourceInfo {
  path: string;
  source: string;
  scope: "user" | "project" | "temporary";
  origin: "package" | "top-level";
  baseDir?: string;
}

/**
 * A registered tool with its provenance: the subset of Pi's ``ToolInfo`` the owner resolver reads
 * (Pi 1.0.0 declares ``getAllTools()`` at ``dist/core/extensions/types.d.ts:1239``, a ``Pick<ToolDefinition, "name" | ...> &
 * { sourceInfo: SourceInfo }``).
 */
export interface ToolInfo {
  name: string;
  sourceInfo: SourceInfo;
  /** The tool's own parameter schema (Pi 1.1.0 ``ToolInfo.parameters``); read only to admit an unreviewed pi-subagents. */
  parameters?: unknown;
}

/**
 * One slash command Pi reports (mirror of ``SlashCommandInfo`` in Pi's ``dist/core/slash-commands.d.ts``
 * lines 3-8 — identical in Pi 1.0.0 and 1.1.0). Extension commands carry the registering
 * extension's ``sourceInfo``; unlike tools, Pi does not deduplicate them by name (clashes get a ``:n``
 * suffix), so every loaded extension's commands are visible.
 */
export interface SlashCommandInfo {
  name: string;
  description?: string;
  source: "extension" | "prompt" | "skill";
  sourceInfo: SourceInfo;
}

export interface ExtensionAPI {
  registerCommand(name: string, def: CommandDefinition): void;
  registerTool?(def: ToolDefinition): void;
  /** Names of the tools the model can call now (Pi ``pi.getActiveTools()``). */
  getActiveTools?(): string[];
  /**
   * Every tool registered so far, with the extension that registered it (Pi
   * ``pi.getAllTools()``; Pi 1.0.0 ``dist/core/extensions/types.d.ts:1239``). A snapshot: it cannot
   * show tools registered by extensions that load later, so the owner is resolved at ``session_start``.
   */
  getAllTools(): ToolInfo[];
  /**
   * Every slash command (extension, prompt template, skill) with its provenance (Pi
   * ``pi.getCommands()``: ``dist/core/extensions/types.d.ts:1248`` in 1.0.0). Used to see a second loaded pi-subagents copy that ``getAllTools()``
   * hides by first-wins name deduplication (Pi ``extensions/runner.js``, first registrant wins).
   */
  getCommands(): SlashCommandInfo[];
  appendEntry?(customType: string, data?: unknown): void;
  sendMessage?(
    message: { customType: string; content: string; display: boolean },
  ): void;
  sendUserMessage(content: string, options?: { deliverAs?: "steer" | "followUp" }): void;
  on(event: "tool_result", handler: (event: ToolResultEvent, ctx: ExtensionContext) => unknown): void;
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
  on(
    event: "agent_before_settle",
    handler: (event: BoundaryEvent, ctx: ExtensionContext) => BoundaryResult | void | Promise<BoundaryResult | void>,
  ): void;
  on(event: string, handler: unknown): void;
}
