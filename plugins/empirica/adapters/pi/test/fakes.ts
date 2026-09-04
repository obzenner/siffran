// Test doubles that mock the Pi ExtensionAPI / ctx surface just enough to prove
// registration, translation, gating, and follow-up behaviour, with no Pi runtime
// and no network.

import type {
  AgentSettledHandler,
  CommandDefinition,
  ExtensionAPI,
  ExtensionContext,
  MessageDelivery,
  NotifyType,
  ResourcesDiscoverHandler,
  ToolCallHandler,
  UiContext,
} from "../src/pi-types.ts";

export interface NotifyCall {
  message: string;
  type?: NotifyType;
}

export interface SentMessage {
  text: string;
  deliverAs?: MessageDelivery;
  triggerTurn?: boolean;
}

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

export function fakeCtx(cwd = "/work/repo"): ExtensionContext {
  return { ui: new FakeUi(), cwd };
}

/** Captures everything an extension registers against the ExtensionAPI. */
export class FakePi implements ExtensionAPI {
  readonly commands = new Map<string, CommandDefinition>();
  readonly handlers = new Map<string, unknown>();
  readonly sentMessages: SentMessage[] = [];

  registerCommand(name: string, def: CommandDefinition): void {
    this.commands.set(name, def);
  }

  on(event: string, handler: unknown): void {
    this.handlers.set(event, handler);
  }

  sendMessage(
    text: string,
    options?: { deliverAs?: MessageDelivery; triggerTurn?: boolean },
  ): void {
    this.sentMessages.push({ text, ...options });
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

  agentSettled(): AgentSettledHandler {
    return this.require("agent_settled") as AgentSettledHandler;
  }

  private require(event: string): unknown {
    const handler = this.handlers.get(event);
    if (typeof handler !== "function") {
      throw new Error(`${event} handler was not registered`);
    }
    return handler;
  }
}
