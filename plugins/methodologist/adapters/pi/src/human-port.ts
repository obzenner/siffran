// Pi implementation of the HumanPort port: human choice via ctx.ui.select.
//
// The core surfaces a human decision only when a selection is genuinely
// ambiguous. On Pi that maps cleanly to `ctx.ui.select`, which returns the
// chosen option or `undefined` on cancel/timeout.

import type { UiContext } from "./pi-types.ts";
import { HumanDismissed, UnsupportedByHost, type HumanPort } from "./ports.ts";

export class PiHumanPort implements HumanPort {
  private readonly ui: UiContext;
  private readonly selectConfig?: { timeout?: number; signal?: AbortSignal };

  constructor(
    ui: UiContext,
    selectConfig?: { timeout?: number; signal?: AbortSignal },
  ) {
    this.ui = ui;
    this.selectConfig = selectConfig;
  }

  async choose(prompt: string, options: string[]): Promise<string> {
    const picked = await this.ui.select(prompt, options, this.selectConfig);
    if (picked === undefined) {
      throw new HumanDismissed(prompt);
    }
    return picked;
  }

  async ask(_prompt: string): Promise<string> {
    // ADR-30 requires host capability gaps to be reported honestly rather than
    // faked. Pi's ctx.ui offers selection and confirmation, but no free-text
    // input primitive this adapter can rely on. The methodologist/v1 contract
    // never routes an open question through this port (HumanDecisionRequired is
    // always a choice among candidates), so `ask` stays an explicit unsupported
    // seam instead of returning a fabricated answer.
    throw new UnsupportedByHost(
      "HumanPort.ask (free-text input) is not available via Pi's ctx.ui; " +
        "the methodologist/v1 contract routes human decisions through choose().",
    );
  }
}
