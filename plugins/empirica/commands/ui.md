---
description: Open the Empirica observer UI — every run in your store, read-only, served on this machine only
---

Start the Empirica observer UI. Run exactly this command with the Bash tool, in the background
(`run_in_background: true`), and do nothing else:

```
python3 "${CLAUDE_PLUGIN_ROOT}/adapters/observer/cli.py" serve --open
```

It reads the Empirica store and this repository's recorded run history, writes nothing, and serves
the UI on this machine only (127.0.0.1, under the name `empirica.localhost`). When its output shows the
address (`Empirica observer: http://empirica.localhost:…`), tell the user that address in one line, and the
`127.0.0.1` address it prints under it for a browser that does not resolve `*.localhost`. If it fails, show its error in one line. Do not read, change
or explain any run: the UI is for the user to look at.
