// Test doubles that mock the Pi ExtensionAPI / ctx surface just enough to prove
// registration, translation, gating, and guard behaviour, with no Pi runtime
// and no network.

import type {
  CommandDefinition,
  ExtensionAPI,
  ExtensionContext,
  ResourcesDiscoverHandler,
  SlashCommandInfo,
  ToolCallHandler,
  ToolDefinition,
  ToolInfo,
  UiContext,
} from "../src/pi-types.ts";
import { defaultSubagentsPackage } from "./owner-fixture.ts";

export interface NotifyCall {
  message: string;
  type?: NotifyType;
}

type NotifyType = "info" | "warning" | "error";

/** Records ctx.ui interactions the empirica adapter makes (notify only). */
export class FakeUi implements UiContext {
  readonly notifications: NotifyCall[] = [];

  notify(message: string, type?: NotifyType): void {
    this.notifications.push({ message, type });
  }

  /** The last notification, for terse assertions. */
  last(): NotifyCall | undefined {
    return this.notifications.at(-1);
  }
}

export function fakeCtx(
  cwd = "/work/repo",
  entries: Array<{ type?: string; customType?: string; data?: unknown }> = [],
): ExtensionContext {
  return { ui: new FakeUi(), cwd,
    model: { provider: "bedrock", id: "author-model" },
    sessionManager: { getEntries: () => entries } };
}

/** Captures everything an extension registers against the ExtensionAPI. */
export class FakePi implements ExtensionAPI {
  readonly commands = new Map<string, CommandDefinition>();
  readonly handlers = new Map<string, unknown>();
  readonly tools = new Map<string, ToolDefinition>();
  readonly modelMessages: Array<{ customType: string; content: string }> = [];
  readonly userMessages: string[] = [];
  readonly userMessageOptions: Array<{ deliverAs?: "steer" | "followUp" } | undefined> = [];
  readonly entries: Array<{ customType: string; data?: unknown }> = [];
  /** Pi-subagents' tool is active unless a test removes it. */
  activeTools: string[] = ["subagent"];
  /**
   * Every `subagent` registration made by a loaded extension, in load order (default: one on-disk
   * fixture owner). Like Pi, ``getAllTools`` reports only the first registrant of a name, while each
   * registering extension still contributes its own slash command to ``getCommands``.
   */
  subagentOwners: ToolInfo[] = [defaultSubagentsPackage().tool()];
  /** Extra slash commands appended to ``getCommands`` (any source), e.g. skills or unrelated extensions. */
  extraCommands: SlashCommandInfo[] = [];
  /** Set to make ``getAllTools`` throw (the inventory itself is unavailable). */
  inventoryError: Error | null = null;
  /** Set to make ``getCommands`` throw. */
  commandsError: Error | null = null;

  /** First-wins by tool name (Pi 0.87.1 ``extensions/runner.js:370-380``); non-records pass through untouched. */
  getAllTools(): ToolInfo[] {
    if (this.inventoryError) throw this.inventoryError;
    const own: ToolInfo[] = [...this.tools.keys()].map((name) => ({ name,
      sourceInfo: { path: "<empirica>", source: "local", scope: "user", origin: "top-level" } }));
    const seen = new Set<string>();
    return [...this.subagentOwners, ...own].filter((tool) => {
      const name: unknown = (tool as { name?: unknown } | null)?.name;
      if (typeof name !== "string") return true;
      if (seen.has(name)) return false;
      seen.add(name);
      return true;
    });
  }

  /** Not deduplicated (Pi suffixes clashing names ``:n``): one ``subagents-doctor`` per registering extension. */
  getCommands(): SlashCommandInfo[] {
    if (this.commandsError) throw this.commandsError;
    const owners = this.subagentOwners.filter((tool) => tool !== null && typeof tool === "object" && "sourceInfo" in tool);
    return [
      ...owners.map((tool, index): SlashCommandInfo => ({ name: owners.length > 1 ? `subagents-doctor:${index + 1}` : "subagents-doctor",
        source: "extension", sourceInfo: tool.sourceInfo })),
      ...[...this.commands.keys()].map((name): SlashCommandInfo => ({ name, source: "extension",
        sourceInfo: { path: "<empirica>", source: "local", scope: "user", origin: "top-level" } })),
      ...this.extraCommands,
    ];
  }

  getActiveTools(): string[] {
    return [...this.activeTools, ...this.tools.keys()];
  }
  registerTool(def: ToolDefinition): void {
    this.tools.set(def.name, def);
  }
  appendEntry(customType: string, data?: unknown): void {
    this.entries.push({ customType, data });
  }
  sendMessage(message: { customType: string; content: string; display?: boolean }): void {
    this.modelMessages.push(message);
  }
  sendUserMessage(content: string,
                  options?: { deliverAs?: "steer" | "followUp" }): void {
    this.userMessages.push(content);
    this.userMessageOptions.push(options);
  }

  registerCommand(name: string, def: CommandDefinition): void {
    this.commands.set(name, def);
  }

  on(event: string, handler: unknown): void {
    this.handlers.set(event, handler);
  }

  command(name: string): CommandDefinition {
    const def = this.commands.get(name);
    if (def === undefined) throw new Error(`command not registered: ${name}`);
    return def;
  }

  resourcesDiscover(): ResourcesDiscoverHandler {
    return this.require("resources_discover") as ResourcesDiscoverHandler;
  }

  /** Fire ``session_start`` (the owner is observed here) and wait for the handler. */
  async sessionStart(ctx: ExtensionContext = fakeCtx()): Promise<void> {
    await (this.require("session_start") as (event: unknown, ctx: ExtensionContext) => unknown)({}, ctx);
  }

  toolCall(): ToolCallHandler {
    return this.require("tool_call") as ToolCallHandler;
  }

  private require(event: string): unknown {
    const handler = this.handlers.get(event);
    if (typeof handler !== "function") {
      throw new Error(`${event} handler was not registered`);
    }
    return handler;
  }
}
