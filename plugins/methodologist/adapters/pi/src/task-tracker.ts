// Pi implementation of the TaskTracker port: phase progress as a live widget.
//
// ADR-32: the Pi adapter "renders phase progress using Pi capabilities rather
// than Claude task tools." Claude's TaskCreate/TaskUpdate become entries in a
// single `ctx.ui.setWidget` widget rendered below the editor. State lives in
// memory for the duration of the turn; nothing is written to disk, so there is
// no shared state under .pi or .claude and no repository runtime write.

import type { UiContext, WidgetPlacement } from "./pi-types.ts";
import type { TaskTracker } from "./ports.ts";

type TaskState = "pending" | "active" | "done";

const GLYPH: Record<TaskState, string> = {
  pending: "[ ]",
  active: "[▶]",
  done: "[x]",
};

interface TrackedTask {
  id: string;
  title: string;
  state: TaskState;
}

export class PiWidgetTaskTracker implements TaskTracker {
  private readonly ui: UiContext;
  private readonly widgetId: string;
  private readonly placement: WidgetPlacement;
  private readonly tasks: TrackedTask[] = [];
  private seq = 0;

  constructor(
    ui: UiContext,
    options?: { widgetId?: string; placement?: WidgetPlacement },
  ) {
    this.ui = ui;
    this.widgetId = options?.widgetId ?? "methodologist:phases";
    this.placement = options?.placement ?? "belowEditor";
  }

  createTask(title: string): string {
    const id = `phase-${++this.seq}`;
    this.tasks.push({ id, title, state: "pending" });
    this.render();
    return id;
  }

  startTask(taskId: string): void {
    this.transition(taskId, "active");
  }

  completeTask(taskId: string): void {
    this.transition(taskId, "done");
  }

  /** Clear the widget (e.g. at the end of a run). */
  clear(): void {
    this.tasks.length = 0;
    this.ui.setWidget(this.widgetId, undefined, { placement: this.placement });
  }

  private transition(taskId: string, state: TaskState): void {
    const task = this.tasks.find((t) => t.id === taskId);
    if (task === undefined) {
      throw new Error(`unknown task id: ${taskId}`);
    }
    task.state = state;
    this.render();
  }

  private render(): void {
    const lines = this.tasks.map((t) => `${GLYPH[t.state]} ${t.title}`);
    this.ui.setWidget(this.widgetId, lines.length > 0 ? lines : undefined, {
      placement: this.placement,
    });
  }
}
