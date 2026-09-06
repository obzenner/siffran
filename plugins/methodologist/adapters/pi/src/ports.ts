// TypeScript mirror of the Methodologist core ports (plugins/methodologist/core/ports.py).
//
// A port is the abstract seam the host fills (ADR-30). The core names neither
// Pi nor these interfaces; it depends only on the capability. This adapter
// supplies concrete Pi implementations (see task-tracker.ts, human-port.ts).

/** Records phase progress as trackable tasks (SKILL.md Step 2 / Step 3). */
export interface TaskTracker {
  /** Create a task and return an opaque id the tracker understands. */
  createTask(title: string): string;
  /** Mark a task in progress. */
  startTask(taskId: string): void;
  /** Mark a task complete. */
  completeTask(taskId: string): void;
  /** Remove the progress widget entirely (e.g. when no trackable progress exists). */
  clear(): void;
}

/**
 * Asks the human when the methodology cannot proceed on its own.
 *
 * Mirrors the core's two moments that require a person: an ambiguous selection
 * the human must resolve (`choose`), and an open question the run cannot answer
 * from evidence (`ask`).
 */
export interface HumanPort {
  /** Ask the human to pick one option; resolve to the chosen one. */
  choose(prompt: string, options: string[]): Promise<string>;
  /** Ask the human an open question; resolve to their free-text answer. */
  ask(prompt: string): Promise<string>;
}

/** Raised when a required human decision is dismissed (cancelled / timed out). */
export class HumanDismissed extends Error {
  constructor(prompt: string) {
    super(`human decision dismissed without a choice: ${prompt}`);
    this.name = "HumanDismissed";
  }
}

/** Raised when a port operation has no honest mapping on the current host. */
export class UnsupportedByHost extends Error {
  constructor(message: string) {
    super(message);
    this.name = "UnsupportedByHost";
  }
}
