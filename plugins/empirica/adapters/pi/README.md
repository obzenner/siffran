# Empirica Pi adapter

This extension translates Pi lifecycle events into the `empirica/v1` bridge. `/empirica` parses ADR-28 leading mode flags, persists and re-injects the opaque run handle, and exposes the deterministic obligation contract to the model. The `report_convergence` custom tool is enforced at `tool_call`; `empirica_status` and `empirica_knowledge` are model-callable transport operations. Spawn interception is configured by tool name and fails closed on bridge errors; a spawn outside Pi's event stream is necessarily un-gated.

Compaction uses `session_before_compact` custom summaries (where available) and retains contract JSON in extension session entries. Pi cannot veto completion: enforcement exists only when the report tool is invoked. Runtime claims about blocked-reason model visibility and follow-up turn reliability remain UNVERIFIED pending a live spike.
