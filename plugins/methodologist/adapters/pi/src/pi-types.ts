// Structural subset of the Pi (pi.dev) extension surface this adapter depends on.
//
// The real types live in `@earendil-works/pi-coding-agent` (declared as a
// peerDependency). The adapter is written against this hand-authored subset so
// it type-checks, tests, and installs without a network fetch of Pi; the shapes
// are structurally compatible with the documented ExtensionAPI. Only the
// capabilities this adapter actually uses are modelled here — a smaller surface
// is a smaller thing to keep in sync.
//
// Sources (Pi docs, packages/coding-agent/docs/extensions.md):
//   - pi.registerCommand(name, { description, handler })
//   - pi.on("resources_discover", handler) -> { skillPaths, promptPaths, themePaths }
//   - ctx.ui.select(title, options, config?) -> Promise<string | undefined>
//   - ctx.ui.setWidget(id, content, { placement })
//   - ctx.ui.notify(message, level)

export type NotifyLevel = "info" | "warning" | "error";

export type WidgetPlacement = "aboveEditor" | "belowEditor";

export interface UiContext {
  notify(message: string, level?: NotifyLevel): void;
  select(
    title: string,
    options: string[],
    config?: { timeout?: number; signal?: AbortSignal },
  ): Promise<string | undefined>;
  setWidget(
    id: string,
    content: string[] | undefined,
    options?: { placement?: WidgetPlacement },
  ): void;
  setStatus?(key: string, text: string | undefined): void;
  confirm?(title: string, message: string): Promise<boolean>;
}

export interface ExtensionContext {
  ui: UiContext;
  cwd?: string;
}

export interface CommandDefinition {
  description: string;
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

export type ResourcesDiscoverHandler = (
  event: ResourcesDiscoverEvent,
  ctx: ExtensionContext,
) => ResourcesDiscoverResult | Promise<ResourcesDiscoverResult>;

export interface ExtensionAPI {
  registerCommand(name: string, def: CommandDefinition): void;
  on(event: "resources_discover", handler: ResourcesDiscoverHandler): void;
  on(event: string, handler: (event: unknown, ctx: ExtensionContext) => unknown): void;
}
