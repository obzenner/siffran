// The keyboard (UI.md, Keyboard): a key is a command, or nothing. Pure, with the one piece of memory a two-key command needs
// (`g` then `n`). Typing in a field is never a command, except Esc; it and a modifier chord also drop an armed `g`.
//
//   j / k   the next / previous row        /   the search          Enter   open the row (a link does this itself)
//   g n     go to what needs a human       Esc close the claim, then the sheet      ?   words beside every glyph

export class Keys {
  #g = false;
  /** @returns {"next"|"previous"|"search"|"needs"|"close"|"words"|null} */
  command(e) {
    const typing = e.target?.closest?.("input, select, textarea, [contenteditable]");
    if (e.key === "Escape") { this.#g = false; return "close"; }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) { this.#g = false; return null; }
    if (this.#g) { this.#g = false; return e.key === "n" ? "needs" : null; }
    switch (e.key) {
      case "j": return "next";
      case "k": return "previous";
      case "/": return "search";
      case "?": return "words";
      case "g": this.#g = true; return null;
      default: return null;
    }
  }
}
