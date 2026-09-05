// Test doubles that mock the Pi ExtensionAPI / ctx surface just enough to prove
// registration and translation, with no Pi runtime and no network.

import type {
  CommandDefinition,
  ExtensionAPI,
  NotifyLevel,
  ResourcesDiscoverHandler,
  ToolDefinition,
  UiContext,
  WidgetPlacement,
} from "../src/pi-types.ts";

export interface WidgetCall {
  id: string;
  content: string[] | undefined;
  placement?: WidgetPlacement;
}

export interface NotifyCall {
  message: string;
  level?: NotifyLevel;
}

export interface SelectCall {
  title: string;
  options: string[];
}

/** Records ctx.ui interactions; `selectAnswers` are dequeued by select(). */
export class FakeUi implements UiContext {
  readonly widgets: WidgetCall[] = [];
  readonly notifications: NotifyCall[] = [];
  readonly selects: SelectCall[] = [];
  private readonly selectAnswers: (string | undefined)[];

  constructor(selectAnswers: (string | undefined)[] = []) {
    this.selectAnswers = [...selectAnswers];
  }

  notify(message: string, level?: NotifyLevel): void {
    this.notifications.push({ message, level });
  }

  async select(title: string, options: string[]): Promise<string | undefined> {
    this.selects.push({ title, options });
    return this.selectAnswers.shift();
  }

  setWidget(
    id: string,
    content: string[] | undefined,
    options?: { placement?: WidgetPlacement },
  ): void {
    this.widgets.push({ id, content, placement: options?.placement });
  }

  /** Latest non-cleared widget content, for asserting the rendered phase list. */
  lastWidgetLines(): string[] | undefined {
    return this.widgets.at(-1)?.content;
  }
}

/** Captures everything an extension registers against the ExtensionAPI. */
export class FakePi implements ExtensionAPI {
  readonly commands = new Map<string, CommandDefinition>();
  readonly tools = new Map<string, ToolDefinition>();
  readonly sentUserMessages: string[] = [];
  readonly handlers = new Map<string, unknown>();

  registerCommand(name: string, def: CommandDefinition): void {
    this.commands.set(name, def);
  }

  registerTool(def: ToolDefinition): void {
    this.tools.set(def.name, def);
  }

  sendUserMessage(content: string): void {
    this.sentUserMessages.push(content);
  }

  on(event: string, handler: unknown): void {
    this.handlers.set(event, handler);
  }

  resourcesDiscover(): ResourcesDiscoverHandler {
    const handler = this.handlers.get("resources_discover");
    if (typeof handler !== "function") {
      throw new Error("resources_discover handler was not registered");
    }
    return handler as ResourcesDiscoverHandler;
  }
}
