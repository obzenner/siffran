// Test doubles that mock the Pi ExtensionAPI / ctx surface just enough to prove
// registration, translation, gating, and guard behaviour, with no Pi runtime
// and no network.

import type {
  CommandDefinition,
  ExtensionAPI,
  ExtensionContext,
  ResourcesDiscoverHandler,
  ToolCallHandler,
  ToolDefinition,
  UiContext,
} from "../src/pi-types.ts";

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
  return { ui: new FakeUi(), cwd, sessionManager: { getEntries: () => entries } };
}

/** Captures everything an extension registers against the ExtensionAPI. */
export class FakePi implements ExtensionAPI {
  readonly commands = new Map<string, CommandDefinition>();
  readonly handlers = new Map<string, unknown>();
  readonly tools = new Map<string, ToolDefinition>();
  readonly modelMessages: Array<{ customType: string; content: string }> = [];
  readonly entries: Array<{ customType: string; data?: unknown }> = [];

  registerTool(def: ToolDefinition): void {
    this.tools.set(def.name, def);
  }
  appendEntry(customType: string, data?: unknown): void {
    this.entries.push({ customType, data });
  }
  sendMessage(message: { customType: string; content: string; display?: boolean }): void {
    this.modelMessages.push(message);
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
