---
description: Open the Empirica observer UI — every run in your store, read-only, served on 127.0.0.1
---

Start the Empirica observer UI. Run exactly this command with the Bash tool, in the background
(`run_in_background: true`), and do nothing else:

```
python3 "${CLAUDE_PLUGIN_ROOT}/adapters/observer/cli.py" serve --open
```

It reads the Empirica store and this repository's recorded run history, writes nothing, and serves
the UI on 127.0.0.1 only. When its output shows the address (`Empirica observer: http://127.0.0.1:…`),
tell the user that address in one line. If it fails, show its error in one line. Do not read, change
or explain any run: the UI is for the user to look at.
