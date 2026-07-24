#!/usr/bin/env python3
"""Committed regression suite for the empirica hooks (review finding 2.9).

Run: python3 plugins/empirica/tests/test_hooks.py   (stdlib only, no pytest dependency)
Exit 0 = all checks pass; 1 = at least one failed.

Lives in the plugin (committed, git-tracked) so anyone who clones can run it and so it
protects the distributed plugin from regression — unlike the earlier transient copy under
`.claude/`, which was git-ignored and unbackable.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
HOOKS = HERE.parent / "hooks"          # plugins/empirica/hooks
GATE = HOOKS / "convergence_gate.py"
HARNESS = HOOKS / "spike_harness.py"
RESTORE = HOOKS / "state_restore.py"
FIXTURE_GRAPH = HERE / "claims.json"

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cg = _load("convergence_gate", GATE)
budget = _load("budget", HOOKS / "budget.py")
manifest = _load("manifest", HOOKS / "manifest.py")
graph = _load("claimgraph", HOOKS / "claimgraph.py")
ev = _load("evidence", HOOKS / "evidence.py")
aud = _load("audit", HOOKS / "audit.py")
RUN_START = HOOKS / "run_start.py"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


DEFAULT_SID = "sess-test"


def write_run(claims: list[dict], sid: str = DEFAULT_SID, max_passes: int = 8,
              evidenced: bool = True) -> Path:
    """Establish a real empirica run on the claim-graph substrate (ADR-22).

    `claims` is a list of {text, confidence, kind?, blocked?} dicts; each becomes a sub-Goal
    of a top Goal, which is what makes it gating (ADR-20 P7 gates the path to the goal).

    `evidenced=True` also writes a valid Fold-1 research leaf for every claim, because most
    legacy tests are about CONFIDENCE and PASS-COUNTER behaviour, not about evidence — without
    evidence every claim would block for a second reason and those tests would stop testing
    what they were written to test. The evidence-specific tests set evidenced=False or build
    their own store. Returns the cwd (session id is DEFAULT_SID unless overridden).
    """
    d = Path(tempfile.mkdtemp())
    run = manifest.start_run(manifest.locate_run(d, sid), sid, d, max_passes=max_passes)
    nodes: dict = {"G0": {"type": "Goal", "text": "the run's goal", "confidence": 1.0}}
    edges = []
    for i, claim in enumerate(claims, start=1):
        nid = f"G{i}"
        nodes[nid] = {"type": "Goal", "text": claim["text"],
                      "confidence": claim.get("confidence", 0.0)}
        for opt in ("kind", "blocked"):
            if claim.get(opt) is not None:
                nodes[nid][opt] = claim[opt]
        edges.append({"from": "G0", "to": nid, "type": "SupportedBy"})
    graph_path = Path(run["graph_path"])
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps({"root": "G0", "nodes": nodes, "edges": edges}))
    if evidenced:
        run_dir = graph_path.parent
        # The top Goal needs evidence too — it is a gating claim like any other.
        for nid, node in nodes.items():
            ev.write_research(run_dir, f"r-{nid}", nid, node["text"],
                              source="https://docs.example/verified", kind="docs",
                              citation=f"cited source for {nid}", result="supports",
                              ts="2026-07-24T09:00:00Z")
    return d


def write_graph_run(nodes: dict, edges: list, root: str = "G0", sid: str = DEFAULT_SID,
                    max_passes: int = 8) -> Path:
    """Lower-level variant: write an ARBITRARY graph (including a deliberately malformed one)
    into a real run, with no evidence. For the fail-matrix and tamper tests."""
    d = Path(tempfile.mkdtemp())
    run = manifest.start_run(manifest.locate_run(d, sid), sid, d, max_passes=max_passes)
    graph_path = Path(run["graph_path"])
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps({"root": root, "nodes": nodes, "edges": edges}))
    return d


def run_hook(script: Path, payload: dict, cwd: Path) -> subprocess.CompletedProcess:
    """Invoke a hook exactly as Claude Code would: JSON on stdin. Block = exit 2 +
    stderr; allow = exit 0 (re-verified against live docs 2026-07-22)."""
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload), capture_output=True, text=True, cwd=str(cwd),
    )


# --- claim parsing + convergence math (unit) --------------------------------
def test_parse():
    """The committed fixture, now a claim graph: 3 claims, 2 of them sub-θ."""
    g = graph.load(FIXTURE_GRAPH)
    check("A0 the committed fixture is a valid claim graph",
          g is not None and g is not graph.CORRUPT, f"got {g}")
    gating = graph.gating_goals(g, 0.8, _ALL_OK)
    check("A1 the fixture has 3 claims plus the top goal", len(gating) == 4, f"got {gating}")
    check("A2 two claims are pending below theta", len(graph.pending(g, 0.8, _ALL_OK)) == 2,
          f"got {graph.pending(g, 0.8, _ALL_OK)}")


def test_converged_math():
    def g(*confidences):
        nodes = {"G0": {"type": "Goal", "text": "top", "confidence": 1.0}}
        edges = []
        for i, c in enumerate(confidences, start=1):
            nodes[f"G{i}"] = {"type": "Goal", "text": f"c{i}", "confidence": c}
            edges.append({"from": "G0", "to": f"G{i}", "type": "SupportedBy"})
        return graph.normalise({"root": "G0", "nodes": nodes, "edges": edges})
    check("A3 not converged when any claim < theta",
          graph.converged(g(0.9, 0.4), 0.8, _ALL_OK) is False)
    check("A4 converged when all claims >= theta",
          graph.converged(g(0.8, 0.95), 0.8, _ALL_OK) is True)
    check("A5 a lone satisfied top goal converges (vacuous)",
          graph.converged(g(), 0.8, _ALL_OK) is True)


def test_theta_guard():
    import os
    old = os.environ.get("EMPIRICA_THETA")
    os.environ["EMPIRICA_THETA"] = "not-a-number"
    check("A10 malformed THETA falls back to default", cg.theta() == cg.DEFAULT_THETA)
    os.environ["EMPIRICA_THETA"] = "1.5"
    check("A11 out-of-range THETA falls back", cg.theta() == cg.DEFAULT_THETA)
    if old is None:
        del os.environ["EMPIRICA_THETA"]
    else:
        os.environ["EMPIRICA_THETA"] = old


# --- Stop gate end-to-end (exit 2 = block, exit 0 = allow) ------------------
def test_hook_blocks_when_unconverged():
    d = write_run([{"text": "open claim", "confidence": 0.4},
                   {"text": "settled claim", "confidence": 0.95}])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("A6 gate exits 2 (block) while unconverged", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")
    check("A7 block reason on stderr names theta", "θ=0.8" in p.stderr, f"got {p.stderr!r}")


def test_hook_allows_when_converged():
    # The full chain: every claim evidenced AND a passing independent audit (ADR-20 P6).
    d = write_run([{"text": "done", "confidence": 0.9},
                   {"text": "also done", "confidence": 0.85}])
    _write_verdict(d, nonce=_spawn_auditor(d), claims_reviewed=["G0", "G1", "G2"])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("A8 gate exits 0 (allow) when converged", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r} stdout={p.stdout!r}")


def test_hook_fail_open_missing_run():
    d = Path(tempfile.mkdtemp())
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("A9 fail-open (exit 0) when there is no run at all", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")


# --- adversarial must-fixes -------------------------------------------------
def test_unscored_claim_blocks():
    """A claim with no confidence at all reads as 0.0 → blocks."""
    d = write_run([{"text": "never scored"},
                   {"text": "scored", "confidence": 0.95}])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("F1 unscored claim BLOCKS (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_out_of_range_confidence_blocks():
    # 8 (fat-finger for 0.8) is out of [0,1] → treated as 0.0 → blocks.
    d = write_run([{"text": "fat finger", "confidence": 8}])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("F2 out-of-range confidence BLOCKS (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_unreadable_graph_fails_closed():
    # Establish a run, then replace the claim graph with a directory → unreadable → the
    # active run fails CLOSED rather than fabricating convergence.
    d = Path(tempfile.mkdtemp())
    run = manifest.start_run(manifest.locate_run(d, DEFAULT_SID), DEFAULT_SID, d)
    Path(run["graph_path"]).mkdir(parents=True)
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("F3 unreadable claim graph FAILS CLOSED (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_deleted_graph_fails_closed():
    """Deleting the graph to bypass convergence must fail CLOSED (ADR-19's original hole)."""
    d = write_run([{"text": "open", "confidence": 0.1}])
    run = manifest.read_run(manifest.locate_run(d, DEFAULT_SID))
    Path(run["graph_path"]).unlink()
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("F3b deleting the claim graph of an ACTIVE run FAILS CLOSED", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_corrupt_graph_fails_closed():
    d = write_run([{"text": "open", "confidence": 0.1}])
    run = manifest.read_run(manifest.locate_run(d, DEFAULT_SID))
    Path(run["graph_path"]).write_text("{ truncated")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("F3c a CORRUPT claim graph FAILS CLOSED", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_blocked_claim_allows():
    # A genuinely unresolvable claim surfaced to the human must NOT wedge the loop.
    d = write_run([{"text": "needs a human call", "confidence": 0.2,
                    "blocked": "needs-decision"}])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("F4 blocked (surfaced) claim ALLOWS stop (exit 0)", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")
    check("F4b …but the run is reported NON-converged",
          json.loads(p.stdout).get("converged") is False, f"got {p.stdout!r}")


def test_off_path_node_does_not_block():
    """A node not on the SupportedBy path to the top goal does not gate (ADR-20 P7)."""
    d = write_graph_run(
        {"G0": {"type": "Goal", "text": "top", "confidence": 0.9},
         "ORPH": {"type": "Goal", "text": "detached", "confidence": 0.0}}, [])
    run_dir = manifest.locate_run_dir(d, DEFAULT_SID)
    ev.write_research(run_dir, "r-G0", "G0", "top", source="https://docs.example/x",
                      kind="docs", citation="cited", result="supports",
                      ts="2026-07-24T09:00:00Z")
    _write_verdict(d, nonce=_spawn_auditor(d), claims_reviewed=["G0"])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("F5 an off-path node does not block the stop", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_malformed_stdin_no_crash():
    proc = subprocess.run([sys.executable, str(GATE)], input="not json",
                          capture_output=True, text=True, cwd=str(HERE))
    check("F6 malformed stdin does not crash (exit in {0,2})", proc.returncode in (0, 2),
          f"rc={proc.returncode} stderr={proc.stderr!r}")


# --- Review fixes: gate integrity -------------------------------------------
def test_invalid_blocked_tag_still_blocks():
    # review 1.1: a made-up blocked tag must NOT bypass the gate.
    d = write_run([{"text": "sneaky", "confidence": 0.1, "blocked": "totally-made-up"}])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("G1 invalid blocked tag does NOT bypass (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_valid_blocked_tags_allow():
    for tag in ("needs-decision", "needs-data", "needs-experiment", "needs-budget"):
        d = write_run([{"text": "x", "confidence": 0.1, "blocked": tag}])
        p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
        check(f"G2 valid tag {tag} allows stop", p.returncode == 0, f"rc={p.returncode}")


def test_forged_state_field_does_not_bypass_the_gate():
    """END-TO-END of the load-bearing property: typing a verdict into the graph is inert."""
    d = write_graph_run(
        {"G0": {"type": "Goal", "text": "top", "confidence": 1.0, "state": "approved"},
         "G1": {"type": "Goal", "text": "unproven", "confidence": 0.99,
                "state": "approved"}},
        [{"from": "G0", "to": "G1", "type": "SupportedBy"}])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("G3 a typed `state: approved` does NOT satisfy the Stop gate", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")
    check("G3b the gate explains that Fold 1 is missing", "FOLD 1 MISSING" in p.stderr,
          f"got {p.stderr!r}")


def test_gate_reports_which_fold_is_missing():
    """The block message must be actionable: name the missing fold, not just a low score."""
    d = write_run([{"text": "needs a real experiment", "confidence": 0.95,
                    "kind": "needs-experiment"}])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("G6 a needs-experiment claim at ≥θ without a spike BLOCKS", p.returncode == 2,
          f"rc={p.returncode}")
    check("G6b and the gate names the missing FOLD 2", "FOLD 2 MISSING" in p.stderr,
          f"got {p.stderr!r}")


def test_legacy_run_fails_open_not_wedged():
    """A run started under the pre-ADR-22 markdown substrate must not wedge the session."""
    d = Path(tempfile.mkdtemp())
    rp = manifest.locate_run(d, DEFAULT_SID)
    manifest.start_run(rp, DEFAULT_SID, d)
    data = json.loads(rp.read_text())
    data.pop("graph_path", None)
    data["spec_path"] = str((rp.parent / "spec.md").resolve())  # the old substrate
    rp.write_text(json.dumps(data))
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("G7 a LEGACY (pre-claim-graph) run fails OPEN, never wedges", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")
    check("G7b …and is reported honestly as non-converged",
          json.loads(p.stdout).get("converged") is False, f"got {p.stdout!r}")


# --- Review fixes: budget hardening -----------------------------------------
def test_strict_coercion_rejects_bad_caps():
    check("G4 string cap → None (unbounded, not 0)", budget._int_or_none("5") is None)
    check("G5 bool cap → None", budget._int_or_none(True) is None)
    check("G14 negative cap → None", budget._int_or_none(-3) is None)
    check("G15 valid int cap kept", budget._int_or_none(5) == 5)


def test_infinity_ledger_does_not_crash():
    # review 2.5: a crafted Infinity cap must not raise; it reads as unbounded.
    d = Path(tempfile.mkdtemp())
    path = budget.locate_ledger(d, "inf")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"max_spawns": Infinity, "spawns": 0}')
    led = budget.read_ledger(path)  # must not raise OverflowError
    check("G8 Infinity ledger reads unbounded, no crash", led["max_spawns"] is None,
          f"got {led}")


def test_run_id_sanitised():
    d = Path(tempfile.mkdtemp())
    p = budget.locate_ledger(d, "../../etc/evil")
    check("G9 run_id traversal sanitised", ".." not in p.parts, f"got {p}")


# --- SpikeHarness: gate from a REAL subprocess ------------------------------
def run_harness(argv: list[str]) -> tuple[dict, int]:
    """Run in --report-only so the process exits 0 and we can parse stdout; also return
    the harness exit code for the exit-code-propagation tests."""
    proc = subprocess.run([sys.executable, str(HARNESS), "--report-only", *argv],
                          capture_output=True, text=True)
    return json.loads(proc.stdout), proc.returncode


def run_harness_exit(argv: list[str]) -> int:
    """Run WITHOUT --report-only to observe the real propagated exit code (review 2.2)."""
    proc = subprocess.run([sys.executable, str(HARNESS), *argv],
                          capture_output=True, text=True)
    return proc.returncode


def test_gate_pass():
    out, _ = run_harness(["python3", "-c", "import sys; sys.exit(0)"])
    check("B1 gate=pass on exit 0", out["gate"] == "pass", f"got {out}")


def test_gate_fail():
    out, _ = run_harness(["python3", "-c", "import sys; sys.exit(1)"])
    check("B2 gate=fail on exit 1", out["gate"] == "fail", f"got {out}")


def test_gate_is_real_not_judgment():
    out, _ = run_harness(["python3", "-c", "assert 1 == 2"])
    check("B3 real failing check → fail", out["gate"] == "fail" and out["returncode"] != 0,
          f"got {out}")


def test_gate_timeout_fails():
    out, _ = run_harness(["--timeout", "1", "python3", "-c", "import time; time.sleep(5)"])
    check("B4 timeout → gate=fail, timed_out", out["gate"] == "fail" and out["timed_out"],
          f"got {out}")


def test_harness_propagates_exit_code():
    # review 2.2: default (no --report-only) must exit with the checked command's status.
    check("B5 default exit 0 on passing check", run_harness_exit(["true"]) == 0)
    check("B6 default nonzero on failing check", run_harness_exit(["false"]) != 0)


def test_harness_launch_failure_is_fail():
    # review 1.5: an un-launchable command resolves to gate=fail, not a crash.
    out, _ = run_harness(["this-command-does-not-exist-xyzzy"])
    check("B7 launch failure → gate=fail", out["gate"] == "fail", f"got {out}")


def test_harness_large_output_bounded():
    # review 1.5: a command emitting far more than the cap must NOT OOM the harness;
    # output is drained through a bounded ring buffer and the tail stays small.
    prog = ("import sys\n"
            "chunk = 'x' * 1_000_000\n"
            "[sys.stdout.write(chunk) for _ in range(50)]\n"  # ~50 MB
            "sys.exit(0)\n")
    out, _ = run_harness(["python3", "-c", prog])
    tail_bytes = sum(len(s) for s in out["stdout_tail"])
    check("B8 huge output → gate=pass, bounded tail", out["gate"] == "pass" and tail_bytes < 100_000,
          f"gate={out['gate']} tail_bytes={tail_bytes}")


# --- SessionStart:compact re-injection --------------------------------------
def test_state_restore_reinjects_claims():
    # Restore resolves the claim graph via the manifest, so it needs a real run.
    d = write_run([{"text": "CLAIM-ONE needs work", "confidence": 0.4},
                   {"text": "CLAIM-TWO settled", "confidence": 0.9}])
    proc = subprocess.run([sys.executable, str(RESTORE)],
                          input=json.dumps({"cwd": str(d), "session_id": DEFAULT_SID}),
                          capture_output=True, text=True)
    check("C1 restore exits 0", proc.returncode == 0, f"stderr={proc.stderr!r}")
    check("C2 restore re-injects claim text", "CLAIM-ONE" in proc.stdout, f"got {proc.stdout!r}")
    check("C3 restore reports not-yet-terminal status",
          "not yet terminal" in proc.stdout, f"got {proc.stdout!r}")
    check("C3b restore marks the open claim's state", "[open]" in proc.stdout,
          f"got {proc.stdout!r}")


def test_state_restore_reports_missing_fold():
    """After compaction the agent must resume knowing WHICH evidence is still owed."""
    d = write_run([{"text": "unresearched claim", "confidence": 0.9}], evidenced=False)
    proc = subprocess.run([sys.executable, str(RESTORE)],
                          input=json.dumps({"cwd": str(d), "session_id": DEFAULT_SID}),
                          capture_output=True, text=True)
    check("C6 restore names the missing evidence fold", "FOLD 1 MISSING" in proc.stdout,
          f"got {proc.stdout!r}")


def test_state_restore_no_run_is_silent():
    # No manifest → not an empirica run → restore emits nothing (fail-open).
    d = Path(tempfile.mkdtemp())
    proc = subprocess.run([sys.executable, str(RESTORE)],
                          input=json.dumps({"cwd": str(d), "session_id": "sess-none"}),
                          capture_output=True, text=True)
    check("C4 restore silent when no run", proc.returncode == 0 and proc.stdout.strip() == "",
          f"rc={proc.returncode} stdout={proc.stdout!r}")


def test_state_restore_silent_on_terminal_run():
    # A converged/stopped run has no loop to resume — restore must not print "Resuming
    # convergence loop…" for a finished run (Copilot review PR #8).
    d = write_run([{"text": "done", "confidence": 0.9}])
    manifest.set_status(manifest.locate_run(d, DEFAULT_SID), "converged")
    proc = subprocess.run([sys.executable, str(RESTORE)],
                          input=json.dumps({"cwd": str(d), "session_id": DEFAULT_SID}),
                          capture_output=True, text=True)
    check("C5 restore silent on terminal (converged) run",
          proc.returncode == 0 and proc.stdout.strip() == "",
          f"rc={proc.returncode} stdout={proc.stdout!r}")


# --- Spawn budget (ADR-17, corrected: enforce on spawns, not tokens) --------
GATE_SPAWN = HOOKS / "spawn_gate.py"


def test_budget_math_unbounded_and_bounded():
    import math
    check("D1 unbounded cap → remaining inf",
          budget.remaining_spawns({"max_spawns": None, "spawns": 5}) == math.inf)
    check("D2 remaining = cap - spawns", budget.remaining_spawns({"max_spawns": 10, "spawns": 3}) == 7)
    check("D3 remaining floors at 0", budget.remaining_spawns({"max_spawns": 10, "spawns": 15}) == 0)


def test_reserve_spawn_atomic_increment_and_cap():
    d = Path(tempfile.mkdtemp())
    path = budget.locate_ledger(d, "r1")
    budget.write_ledger(path, {"max_spawns": 2, "spawns": 0, "run_id": "r1"})
    a1, l1 = budget.reserve_spawn(path)
    a2, l2 = budget.reserve_spawn(path)
    a3, l3 = budget.reserve_spawn(path)
    check("D4 first two spawns reserved", a1 and a2 and l2["spawns"] == 2, f"got {l2}")
    check("D5 third spawn denied at cap", a3 is False, f"got a3={a3}")
    check("D6 denied spawn does NOT increment", l3["spawns"] == 2, f"got {l3}")
    check("D7 unbounded cap always reserves",
          budget.reserve_spawn(budget.locate_ledger(d, "unb"))[0] is True)


def test_missing_ledger_fail_open():
    d = Path(tempfile.mkdtemp())
    missing = budget.read_ledger(d / "nope.json")
    check("D8 missing ledger → unbounded/zero (fail-open)",
          missing["max_spawns"] is None and missing["spawns"] == 0, f"got {missing}")


# --- The real enforcement: PreToolUse spawn gate DENIES over-cap spawns ------
def test_spawn_gate_denies_over_cap():
    d = Path(tempfile.mkdtemp())
    path = budget.locate_ledger(d)
    budget.write_ledger(path, {"max_spawns": 1, "spawns": 0, "run_id": "x"})
    # First Agent spawn: allowed (exit 0), reserves slot 1/1.
    p1 = run_hook(GATE_SPAWN, {"tool_name": "Agent", "cwd": str(d)}, d)
    # Second Agent spawn: cap reached → DENIED (exit 2 + reason on stderr).
    p2 = run_hook(GATE_SPAWN, {"tool_name": "Agent", "cwd": str(d)}, d)
    check("D9 first spawn allowed (exit 0)", p1.returncode == 0, f"rc={p1.returncode}")
    check("D10 over-cap spawn DENIED (exit 2)", p2.returncode == 2, f"rc={p2.returncode}")
    check("D11 deny reason names spawn budget", "spawn budget" in p2.stderr.lower(),
          f"got {p2.stderr!r}")


def test_spawn_gate_ignores_non_agent_tools():
    d = Path(tempfile.mkdtemp())
    budget.write_ledger(budget.locate_ledger(d), {"max_spawns": 0, "spawns": 0, "run_id": "x"})
    # Even at a 0 cap, a non-Agent tool must pass — we only gate spawns.
    p = run_hook(GATE_SPAWN, {"tool_name": "Bash", "cwd": str(d)}, d)
    check("D12 non-Agent tool not gated (exit 0)", p.returncode == 0, f"rc={p.returncode}")


def test_spawn_gate_unbounded_allows():
    d = Path(tempfile.mkdtemp())  # no ledger written → unbounded
    p = run_hook(GATE_SPAWN, {"tool_name": "Agent", "cwd": str(d)}, d)
    check("D13 no budget set → spawn allowed (exit 0)", p.returncode == 0, f"rc={p.returncode}")


# --- Convergence reporting: exhaustion never fabricates green (ADR-17) ------
def test_gate_budget_exhausted_is_non_converged():
    # No pending (all sub-θ ones are blocked: needs-budget) → gate ALLOWS but flags
    # converged:false. Budget exhaustion must never fabricate a green result (ADR-17).
    d = write_run([{"text": "resolved", "confidence": 0.9},
                   {"text": "ran out", "confidence": 0.3, "blocked": "needs-budget"}])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    out = json.loads(p.stdout)
    check("E1 budget-exhausted run ALLOWS stop (exit 0)", p.returncode == 0,
          f"rc={p.returncode}")
    check("E2 budget-exhausted run flagged converged:false", out.get("converged") is False,
          f"got {out}")
    check("E3 note names budget exhaustion", "budget" in out.get("note", "").lower(),
          f"got {out}")


def test_gate_true_convergence_flagged_true():
    # True convergence now needs the full chain: evidenced claims AND a passing independent
    # audit (ADR-20 P6). Without the audit the gate blocks — see R1.
    d = write_run([{"text": "done", "confidence": 0.9}])
    nonce = _spawn_auditor(d)
    _write_verdict(d, nonce=nonce, claims_reviewed=["G0", "G1"])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    out = json.loads(p.stdout)
    check("E4 truly converged run flagged converged:true", out.get("converged") is True,
          f"rc={p.returncode} stderr={p.stderr!r} got {out}")


def test_gate_budget_does_not_stop_healthy_loop():
    # A sub-θ claim that is NOT blocked must still block the stop, regardless of budget —
    # budget never stops a healthy loop early (ADR-17 fitness #3).
    d = write_run([{"text": "still open", "confidence": 0.3}])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("E5 healthy sub-θ loop still blocks (exit 2)", p.returncode == 2,
          f"rc={p.returncode}")


# --- Active-run manifest (ADR-19): identity, fail-closed, termination -------
def _fresh_run() -> tuple[Path, str, Path]:
    root = Path(tempfile.mkdtemp())
    sid = "sess-abc-123"
    return manifest.locate_run(root, sid), sid, root


def test_manifest_lifecycle_and_idempotent_start():
    path, sid, root = _fresh_run()
    check("M1 no manifest → None (fail-open signal)", manifest.read_run(path) is None)
    run = manifest.start_run(path, sid, root, max_passes=5)
    check("M2 start creates active run", run["status"] == "active" and run["passes"] == 0)
    manifest.record_pass(path)
    again = manifest.start_run(path, sid, root, max_passes=5)  # re-invoke mid-run
    check("M3 re-start does NOT reset passes", again["passes"] == 1, f"got {again['passes']}")


def test_manifest_run_id_stable_and_keyed():
    _, sid, root = _fresh_run()
    p1 = manifest.locate_run(root, sid)
    p2 = manifest.locate_run(root, sid)
    p3 = manifest.locate_run(root, "different-session")
    check("M4 same session+root → same path", p1 == p2)
    check("M5 different session → different path", p1 != p3)


def test_manifest_corrupt_sentinel():
    path, sid, root = _fresh_run()
    manifest.start_run(path, sid, root)
    path.write_text("{ this is not json")
    run = manifest.read_run(path)
    check("M6 corrupt active manifest → __corrupt__ sentinel", run["status"] == "__corrupt__",
          f"got {run}")


def test_manifest_variant_terminates():
    path, sid, root = _fresh_run()
    manifest.start_run(path, sid, root, max_passes=4)
    seq = [manifest.variant(manifest.read_run(path))]  # variant at passes=0 → 4
    for _ in range(4):  # tick to the cap: passes 1,2,3,4
        seq.append(manifest.variant(manifest.record_pass(path)))
    check("M7 variant strictly decreases", all(a > b for a, b in zip(seq, seq[1:])), f"seq={seq}")
    check("M8 variant bounded below by 0 and reaches 0", min(seq) == 0, f"seq={seq}")
    check("M9 at cap after max_passes ticks", manifest.at_cap(manifest.read_run(path)), f"seq={seq}")


def test_manifest_evidence_slot():
    path, sid, root = _fresh_run()
    run = manifest.start_run(path, sid, root)
    check("M10 evidence map present + empty (dormant, ADR-18)", run["evidence"] == {})


# --- Gate × manifest: fail-closed identity + pass-count termination E2E ------
def _start_and_graph(claims: list[dict], max_passes: int = 8) -> tuple[Path, str]:
    """Activate a run and write its claim graph into the run directory — a real empirica run,
    with Fold-1 evidence for every claim so these tests exercise the manifest lifecycle rather
    than re-testing evidence."""
    d = write_run(claims, sid="sess-e2e", max_passes=max_passes)
    return d, "sess-e2e"


def test_gate_active_run_missing_graph_fails_closed():
    # 1.2a: an ACTIVE run whose claim graph is absent must BLOCK (identity established).
    d = Path(tempfile.mkdtemp())
    sid = "sess-del"
    manifest.start_run(manifest.locate_run(d, sid), sid, d)
    # No claims.json written → active run + missing graph.
    p = run_hook(GATE, {"cwd": str(d), "session_id": sid}, d)
    check("M11 active run + missing claim graph → BLOCK (1.2a)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_gate_no_manifest_missing_graph_fails_open():
    # No manifest (not an empirica run) + no graph → unchanged fail-OPEN (unrelated repo safe).
    d = Path(tempfile.mkdtemp())
    p = run_hook(GATE, {"cwd": str(d), "session_id": "sess-unrelated"}, d)
    check("M12 no manifest + missing graph → fail-open (exit 0)", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_gate_corrupt_manifest_fails_closed():
    d, sid = _start_and_graph([{"text": "a", "confidence": 0.9}])
    manifest.locate_run(d, sid).write_text("{ not json")  # corrupt an active run
    p = run_hook(GATE, {"cwd": str(d), "session_id": sid}, d)
    check("M13 corrupt active manifest → BLOCK (2.5)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_graph_path_outside_run_dir_is_rejected():
    # Copilot review PR #8, carried onto the claim-graph substrate: a manifest graph_path
    # pointing outside the run directory (a corrupt or rewritten manifest aiming the gate at a
    # "converged" file elsewhere) must be ignored in favour of the canonical run-dir graph.
    # Here the run-dir graph is unconverged but the manifest points at an out-of-run
    # "converged" decoy — the gate must still BLOCK.
    d = write_run([{"text": "open", "confidence": 0.1}], sid="sess-escape")
    sid = "sess-escape"
    decoy = d / "decoy-converged.json"
    decoy.write_text(json.dumps({
        "root": "G0",
        "nodes": {"G0": {"type": "Goal", "text": "done", "confidence": 0.99}},
        "edges": []}))
    rp = manifest.locate_run(d, sid)
    data = json.loads(rp.read_text()); data["graph_path"] = str(decoy.resolve())
    rp.write_text(json.dumps(data))
    resolved = cg.graph_path_for(d, sid, manifest.read_run(rp))
    check("M13b out-of-run graph_path ignored → canonical run-dir graph",
          resolved == manifest.default_graph_path(d, sid), f"got {resolved}")
    p = run_hook(GATE, {"cwd": str(d), "session_id": sid}, d)
    check("M13c decoy cannot fabricate convergence → gate BLOCKS", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_gate_pass_counter_terminates_at_cap():
    # The real termination proof: a never-converging run stops at max_passes as
    # stopped_residual (exit 0, converged:false), not by grinding to the 8-block override.
    # With max_passes=3 the gate blocks twice, then the 3rd pass ticks the counter to the
    # cap and ALLOWS the stop honestly — the variant (max_passes−passes) reaching 0.
    d, sid = _start_and_graph([{"text": "never", "confidence": 0.1}], max_passes=3)
    passes = [run_hook(GATE, {"cwd": str(d), "session_id": sid}, d) for _ in range(3)]
    codes = [p.returncode for p in passes]
    run = manifest.read_run(manifest.locate_run(d, sid))
    check("M14 blocks below the cap then allows at it", codes == [2, 2, 0], f"got {codes}")
    out = json.loads(passes[-1].stdout)
    check("M15 at cap → converged:false", out.get("converged") is False, f"got {out}")
    check("M16 at-cap note names max_passes", "max_passes" in out.get("note", ""), f"got {out}")
    check("M17 run recorded stopped_residual", run["status"] == "stopped_residual",
          f"got {run}")


def test_gate_active_run_converges_records_status():
    d, sid = _start_and_graph([{"text": "done", "confidence": 0.9}])
    _write_verdict(d, sid=sid, nonce=_spawn_auditor(d, sid), claims_reviewed=["G0", "G1"])
    p = run_hook(GATE, {"cwd": str(d), "session_id": sid}, d)
    run = manifest.read_run(manifest.locate_run(d, sid))
    check("M18 converged active run → exit 0", p.returncode == 0, f"rc={p.returncode}")
    check("M19 converged run recorded status=converged", run["status"] == "converged",
          f"got {run}")


def test_gate_stopped_run_does_not_reblock():
    # Once a run is converged/stopped, a later Stop must NOT re-block (fail open).
    d, sid = _start_and_graph([{"text": "open", "confidence": 0.1}])
    manifest.set_status(manifest.locate_run(d, sid), "converged")
    p = run_hook(GATE, {"cwd": str(d), "session_id": sid}, d)
    check("M20 stopped run → fail-open even with a sub-θ claim (exit 0)", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_run_start_hook_creates_manifest():
    d = Path(tempfile.mkdtemp())
    sid = "sess-runstart"
    proc = subprocess.run([sys.executable, str(RUN_START)],
                          input=json.dumps({"session_id": sid, "cwd": str(d),
                                            "command_name": "empirica:empirica"}),
                          capture_output=True, text=True, cwd=str(d))
    run = manifest.read_run(manifest.locate_run(d, sid))
    check("M21 run_start exits 0", proc.returncode == 0, f"stderr={proc.stderr!r}")
    check("M22 run_start created an active manifest", run is not None and run["status"] == "active",
          f"got {run}")
    check("M22b manifest graph_path is inside the run directory (not the repo)",
          run and run["graph_path"] == str((manifest.locate_run_dir(d, sid)
                                           / "claims.json").resolve()),
          f"got {run and run.get('graph_path')}")


def test_run_start_no_session_id_is_noop():
    # No session id → no run identity → no manifest (gate then fails open). Must not crash.
    d = Path(tempfile.mkdtemp())
    proc = subprocess.run([sys.executable, str(RUN_START)],
                          input=json.dumps({"cwd": str(d)}), capture_output=True, text=True, cwd=str(d))
    check("M23 run_start without session_id → exit 0, no crash", proc.returncode == 0,
          f"rc={proc.returncode} stderr={proc.stderr!r}")


def test_run_start_with_real_captured_payload():
    # Regression for the dogfood bug: unit tests bypass Claude Code's matcher, so a broken
    # matcher stayed green while the plugin was runtime-inert. We cannot test the harness's
    # matcher engine here, but we CAN prove run_start.py handles the REAL UserPromptExpansion
    # payload shape (captured live in session c7477410-…): session_id + cwd present alongside
    # command_name/command_args/prompt/expansion_type/command_source etc. → manifest created.
    d = Path(tempfile.mkdtemp())
    sid = "c7477410-ea2d-4960-bfb6-df1e6f39900c"
    payload = {
        "session_id": sid, "cwd": str(d),
        "transcript_path": "/x.jsonl", "prompt_id": "p1", "permission_mode": "bypassPermissions",
        "hook_event_name": "UserPromptExpansion", "expansion_type": "slash_command",
        "command_name": "empirica:empirica", "command_args": "design something",
        "command_source": "plugin", "prompt": "/empirica:empirica design something",
    }
    proc = subprocess.run([sys.executable, str(RUN_START)], input=json.dumps(payload),
                          capture_output=True, text=True, cwd=str(d))
    run = manifest.read_run(manifest.locate_run(d, sid))
    check("M24 real UserPromptExpansion payload → exit 0", proc.returncode == 0,
          f"rc={proc.returncode} stderr={proc.stderr!r}")
    check("M25 real payload creates active manifest", run is not None and run["status"] == "active",
          f"got {run}")


def test_hooks_json_matcher_is_regex_for_namespaced_command():
    # Root-cause guard: the runtime command name is the PLUGIN-NAMESPACED "empirica:empirica",
    # not "empirica". A bare-letters matcher is exact-matched by Claude Code and never fires.
    # The matcher must contain the ":" (making it an unanchored JS regex) AND match the
    # namespaced name. This pins the fix so a future edit back to "empirica" fails here.
    import re as _re
    cfg = json.loads((HOOKS / "hooks.json").read_text())
    matchers = [g.get("matcher") for g in cfg["hooks"].get("UserPromptExpansion", [])]
    check("M26 UserPromptExpansion group exists", len(matchers) >= 1, f"got {matchers}")
    m = matchers[0] if matchers else ""
    check("M27 matcher is a regex (contains ':'), not a bare exact string", ":" in m, f"got {m!r}")
    check("M28 matcher actually matches 'empirica:empirica'",
          bool(_re.search(m, "empirica:empirica")), f"matcher={m!r}")


# --- Claim graph: the anti-forgery core (ADR-20/22) -------------------------
# Every check below is an ATTACK — "the model tries to converge without doing the work."
# A PASS means the attack failed. Ported from the spike at .claude/spike-claimgraph, where
# the schema was falsified before it became production code (36/36 attacks repelled).
TH = 0.8
# Evidence oracles standing in for evidence.py's two-fold verdict.
_ALL_OK = lambda nid, why: True    # noqa: E731 — every claim fully evidenced
_NONE_OK = lambda nid, why: False  # noqa: E731 — no claim evidenced at all


def _cgraph(nodes: dict, edges: list, root: str = "G0") -> dict:
    return {"root": root, "nodes": nodes, "edges": edges}


def _cgoal(text: str = "claim", confidence: float = 0.0, **kw) -> dict:
    return {"type": "Goal", "text": text, "confidence": confidence, **kw}


def _write_graph(obj) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "claims.json"
    p.write_text(json.dumps(obj) if isinstance(obj, (dict, list)) else str(obj))
    return p


def test_claim_state_cannot_be_forged():
    """The load-bearing property: a typed verdict is inert; state is derived."""
    g = graph.normalise(_cgraph({"G0": _cgoal(confidence=0.0, state="approved")}, []))
    check("N1 a typed `state: approved` is INERT (still open)",
          graph.state_of(g, "G0", TH, _NONE_OK) == "open")
    g2 = graph.normalise(_cgraph({"G0": _cgoal(confidence=1.0)}, []))
    check("N2 confidence 1.0 with NO evidence is NOT approved",
          graph.state_of(g2, "G0", TH, _NONE_OK) == "open")
    check("N3 confidence 1.0 WITH evidence is approved",
          graph.state_of(g2, "G0", TH, _ALL_OK) == "approved")
    check("N4 an unwired evidence oracle fails CLOSED (nothing approves)",
          graph.state_of(g2, "G0", TH, None) == "open")


def test_headline_self_attestation_attack():
    """The exact hole this whole build exists to close: an agent types its own confidence
    numbers, consulted no external source, never ran the spike harness."""
    g = graph.normalise(_cgraph(
        {"G0": _cgoal("build the thing", confidence=0.95),
         "G1": _cgoal("library X supports Y", confidence=0.9, kind="needs-data"),
         "G2": _cgoal("the approach performs", confidence=0.9, kind="needs-experiment")},
        [{"from": "G0", "to": "G1", "type": "SupportedBy"},
         {"from": "G0", "to": "G2", "type": "SupportedBy"}]))
    check("N5 self-attested confidences with zero evidence do NOT converge",
          graph.converged(g, TH, _NONE_OK) is False,
          f"pending={graph.pending(g, TH, _NONE_OK)}")
    check("N6 and every claim is reported open, not just the leaves",
          set(graph.pending(g, TH, _NONE_OK)) == {"G0", "G1", "G2"})


def test_discard_requires_validating_refutation():
    """'Discard everything' would be the cheapest bypass of all — it must cost evidence."""
    g = graph.normalise(_cgraph({"G0": _cgoal(confidence=0.0)}, []))
    check("N7 no refutation ref → cannot be discarded",
          graph.state_of(g, "G0", TH, _NONE_OK) == "open")
    g2 = graph.normalise(_cgraph({"G0": _cgoal(confidence=0.0, refuted_by="ev-1")}, []))
    check("N8 a refutation ref whose evidence does NOT validate is refused",
          graph.state_of(g2, "G0", TH, _NONE_OK) == "open")
    check("N9 a refutation ref backed by valid evidence → discarded",
          graph.state_of(g2, "G0", TH, _ALL_OK) == "discarded")


def test_discarded_subtree_is_pruned():
    g = graph.normalise(_cgraph(
        {"G0": _cgoal(confidence=1.0), "G1": _cgoal(refuted_by="ev-1"),
         "G2": _cgoal(confidence=0.0)},
        [{"from": "G0", "to": "G1", "type": "SupportedBy"},
         {"from": "G1", "to": "G2", "type": "SupportedBy"}]))
    gating = graph.gating_goals(g, TH, _ALL_OK)
    check("N10 a discarded node stops gating", "G1" not in gating, f"{gating}")
    check("N11 its children are pruned too (ADR-20 P3)", "G2" not in gating, f"{gating}")
    g2 = graph.normalise(_cgraph(
        {"G0": _cgoal(confidence=1.0), "G1": _cgoal(confidence=0.0),
         "G2": _cgoal(confidence=0.0)},
        [{"from": "G0", "to": "G1", "type": "SupportedBy"},
         {"from": "G1", "to": "G2", "type": "SupportedBy"}]))
    check("N12 WITHOUT a refutation, descendants still gate (pruning isn't a free pass)",
          set(graph.pending(g2, TH, _ALL_OK)) == {"G1", "G2"},
          f"{graph.pending(g2, TH, _ALL_OK)}")


def test_cyclic_graph_is_rejected():
    """GSN requires a DAG: a goal may not support itself, directly or indirectly. Circular
    reasoning is a malformed argument, so it fails CLOSED rather than being gated on."""
    two_cycle = graph.normalise(_cgraph(
        {"G0": _cgoal(confidence=1.0), "G1": _cgoal(confidence=0.0)},
        [{"from": "G0", "to": "G1", "type": "SupportedBy"},
         {"from": "G1", "to": "G0", "type": "SupportedBy"}]))
    check("N13 a 2-cycle is CORRUPT (circular reasoning)", two_cycle is graph.CORRUPT,
          f"got {two_cycle}")
    self_loop = graph.normalise(_cgraph(
        {"G0": _cgoal(confidence=1.0)},
        [{"from": "G0", "to": "G0", "type": "SupportedBy"}]))
    check("N13b a self-loop is CORRUPT", self_loop is graph.CORRUPT, f"got {self_loop}")
    long_cycle = graph.normalise(_cgraph(
        {"G0": _cgoal(confidence=1.0), "G1": _cgoal(), "G2": _cgoal(), "G3": _cgoal()},
        [{"from": "G0", "to": "G1", "type": "SupportedBy"},
         {"from": "G1", "to": "G2", "type": "SupportedBy"},
         {"from": "G2", "to": "G3", "type": "SupportedBy"},
         {"from": "G3", "to": "G1", "type": "SupportedBy"}]))
    check("N13c an INDIRECT cycle is CORRUPT", long_cycle is graph.CORRUPT, f"got {long_cycle}")
    # A diamond is NOT a cycle — two goals may share a supporting sub-goal.
    diamond = graph.normalise(_cgraph(
        {"G0": _cgoal(confidence=1.0), "G1": _cgoal(confidence=1.0),
         "G2": _cgoal(confidence=1.0), "G3": _cgoal(confidence=1.0)},
        [{"from": "G0", "to": "G1", "type": "SupportedBy"},
         {"from": "G0", "to": "G2", "type": "SupportedBy"},
         {"from": "G1", "to": "G3", "type": "SupportedBy"},
         {"from": "G2", "to": "G3", "type": "SupportedBy"}]))
    check("N13d a DIAMOND (shared sub-goal) is legal, not a cycle",
          diamond is not graph.CORRUPT and graph.converged(diamond, TH, _ALL_OK) is True,
          f"got {diamond}")


def test_refuting_the_root_is_not_convergence():
    """REGRESSION — a real bypass found by adversarial review.

    Refute the top goal with one cheap citation: the whole tree prunes, nothing is pending, and
    "nothing pending" used to read as CONVERGED — a vacuous green with zero work done.
    Refuting the intent is a legitimate finding, but it is a residual for the human.
    """
    run = Path(tempfile.mkdtemp())
    g = graph.normalise(_cgraph(
        {"G0": _cgoal("the whole intent", confidence=0.0, refuted_by="ev-x"),
         "G1": _cgoal("real work nobody did", confidence=0.0)},
        [{"from": "G0", "to": "G1", "type": "SupportedBy"}]))
    ev.write_research(run, "ev-x", "G0", "the whole intent", source="https://x", kind="docs",
                      citation="the source says this is impossible", result="refutes",
                      ts="2026-07-24T10:00:00Z")
    oracle = ev.oracle(run, g)
    check("N32 a refuted root IS detected as such",
          graph.root_is_refuted(g, TH, oracle) is True)
    check("N33 a refuted root prunes everything (so 'nothing pending' is misleading)",
          graph.pending(g, TH, oracle) == [], f"{graph.pending(g, TH, oracle)}")
    check("N34 …but the run is NOT converged (the bypass is closed)",
          graph.converged(g, TH, oracle) is False)

    # End-to-end through the real Stop gate: allowed to stop, but reported non-converged.
    d = write_graph_run(
        {"G0": {"type": "Goal", "text": "the whole intent", "confidence": 0.0,
                "refuted_by": "ev-x"},
         "G1": {"type": "Goal", "text": "real work nobody did", "confidence": 0.0}},
        [{"from": "G0", "to": "G1", "type": "SupportedBy"}])
    rd = manifest.locate_run_dir(d, DEFAULT_SID)
    ev.write_research(rd, "ev-x", "G0", "the whole intent", source="https://x", kind="docs",
                      citation="the source says this is impossible", result="refutes",
                      ts="2026-07-24T10:00:00Z")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("N35 the gate allows the stop on a refuted intent (no pointless looping)",
          p.returncode == 0, f"rc={p.returncode} stderr={p.stderr!r}")
    out = json.loads(p.stdout)
    check("N36 …and reports converged:false", out.get("converged") is False, f"got {out}")
    check("N37 …naming the refuted top goal", "TOP GOAL" in out.get("note", ""), f"got {out}")
    check("N38 …and never satisfies convergence without an audit either",
          out.get("audit") != "passed", f"got {out}")


def test_off_path_nodes_do_not_gate():
    g2 = graph.normalise(_cgraph(
        {"G0": _cgoal(confidence=1.0), "ORPH": _cgoal(confidence=0.0)}, []))
    check("N14 an off-path node does not gate (ADR-20 P7: path to the goal)",
          graph.converged(g2, TH, _ALL_OK) is True, f"{graph.pending(g2, TH, _ALL_OK)}")


def test_claim_confidence_coercion():
    """Absence of proof must never read as proof — every malformed confidence → 0.0, or the
    whole graph is corrupt. Same guard manifest.py applies to its counters."""
    for label, literal in [("Infinity", "Infinity"), ("NaN", "NaN"), ("string", '"0.99"'),
                           ("bool", "true"), ("negative", "-1"), ("over 1", "2"),
                           ("null", "null")]:
        raw = ('{"root":"G0","nodes":{"G0":{"type":"Goal","confidence":%s}},"edges":[]}'
               % literal)
        g = graph.load(_write_graph(raw))
        if g is graph.CORRUPT:
            check(f"N15 {label} confidence → corrupt (fail closed)", True)
            continue
        got = g["nodes"]["G0"]["confidence"]
        check(f"N15 {label} confidence → 0.0 (blocks)", got == 0.0, f"got {got}")


def test_illegal_gsn_edges_are_corrupt():
    """A malformed argument is not a weak argument."""
    check("N16 Goal SupportedBy Context is rejected",
          graph.normalise(_cgraph(
              {"G0": _cgoal(), "C1": {"type": "Context", "text": "repo"}},
              [{"from": "G0", "to": "C1", "type": "SupportedBy"}])) is graph.CORRUPT)
    check("N17 Goal InContextOf Context is legal",
          graph.normalise(_cgraph(
              {"G0": _cgoal(confidence=1.0), "C1": {"type": "Context", "text": "repo"}},
              [{"from": "G0", "to": "C1", "type": "InContextOf"}])) is not graph.CORRUPT)
    check("N18 an unknown node type is rejected",
          graph.normalise(_cgraph({"G0": {"type": "Vibe"}}, [])) is graph.CORRUPT)
    check("N19 an unknown edge type is rejected",
          graph.normalise(_cgraph({"G0": _cgoal(), "G1": _cgoal()},
                                  [{"from": "G0", "to": "G1", "type": "Because"}]))
          is graph.CORRUPT)
    check("N19b Strategy SupportedBy Solution is rejected (not in GSN's permitted list)",
          graph.normalise(_cgraph(
              {"G0": _cgoal(confidence=1.0), "S1": {"type": "Strategy", "text": "how"},
               "SOL": {"type": "Solution", "text": "ev"}},
              [{"from": "G0", "to": "S1", "type": "SupportedBy"},
               {"from": "S1", "to": "SOL", "type": "SupportedBy"}])) is graph.CORRUPT)
    check("N19c Strategy SupportedBy Goal IS permitted",
          graph.normalise(_cgraph(
              {"G0": _cgoal(confidence=1.0), "S1": {"type": "Strategy", "text": "how"},
               "G1": _cgoal(confidence=1.0)},
              [{"from": "G0", "to": "S1", "type": "SupportedBy"},
               {"from": "S1", "to": "G1", "type": "SupportedBy"}])) is not graph.CORRUPT)
    check("N20 an edge to a non-existent node is rejected",
          graph.normalise(_cgraph({"G0": _cgoal()},
                                  [{"from": "G0", "to": "GHOST", "type": "SupportedBy"}]))
          is graph.CORRUPT)


def test_claim_graph_fail_matrix():
    """The ADR-19 fail directions, preserved on the new substrate: missing ≠ corrupt."""
    check("N21 a MISSING graph is None (the fail-OPEN signal)",
          graph.load(Path(tempfile.mkdtemp()) / "nope.json") is None)
    check("N22 a truncated graph is CORRUPT (fail-CLOSED)",
          graph.load(_write_graph("{not json")) is graph.CORRUPT)
    check("N23 a graph with no root is CORRUPT",
          graph.load(_write_graph({"nodes": {"G0": _cgoal()}, "edges": []})) is graph.CORRUPT)
    check("N24 a root naming a missing node is CORRUPT",
          graph.load(_write_graph({"root": "GHOST", "nodes": {"G0": _cgoal()},
                                   "edges": []})) is graph.CORRUPT)
    check("N25 a JSON list instead of an object is CORRUPT",
          graph.load(_write_graph([1, 2])) is graph.CORRUPT)
    check("N26 an empty-nodes graph is CORRUPT (no top Goal to argue)",
          graph.load(_write_graph({"root": "G0", "nodes": {}, "edges": []})) is graph.CORRUPT)


def test_claim_blocked_tag_hygiene():
    """Carried over from the markdown gate: an invented residual tag cannot bypass."""
    g = graph.normalise(_cgraph(
        {"G0": _cgoal(confidence=1.0), "G1": _cgoal(confidence=0.0, blocked="made-up")},
        [{"from": "G0", "to": "G1", "type": "SupportedBy"}]))
    check("N27 an invented blocked tag does NOT stop gating",
          "G1" in graph.pending(g, TH, _NONE_OK), f"{graph.pending(g, TH, _NONE_OK)}")
    g2 = graph.normalise(_cgraph(
        {"G0": _cgoal(confidence=1.0), "G1": _cgoal(confidence=0.0, blocked="needs-decision")},
        [{"from": "G0", "to": "G1", "type": "SupportedBy"}]))
    check("N28 a valid residual tag surfaces to the human and stops gating",
          graph.state_of(g2, "G1", TH, _NONE_OK) == "blocked")
    check("N29 a blocked residual is reported, not silently dropped",
          graph.blocked_residuals(g2, TH, _NONE_OK) == ["G1"])


def test_claim_graph_roundtrip():
    g = graph.normalise(_cgraph(
        {"G0": _cgoal("top", confidence=0.9), "S1": {"type": "Solution", "text": "ev"}},
        [{"from": "G0", "to": "S1", "type": "SupportedBy"}]))
    p = Path(tempfile.mkdtemp()) / "claims.json"
    graph.save(p, g)
    back = graph.load(p)
    check("N30 save→load roundtrips the graph", back == g, f"got {back}")
    check("N31 the graph's home is claims.json in the run dir",
          graph.default_graph_path(Path("/tmp/run")).name == "claims.json")


# --- Two-fold evidence binding (ADR-20 P3, ADR-21 M2) -----------------------
# The attacks here are the ones the whole build exists to stop: approve a claim with no
# research at all, forge a spike, edit the tree after a green spike, back-fill a citation
# after the fact, reword a claim to inherit someone else's evidence.
TS1, TS2, TS3 = "2026-07-24T10:00:00Z", "2026-07-24T11:00:00Z", "2026-07-24T12:00:00Z"


def _ev_run() -> Path:
    return Path(tempfile.mkdtemp())


def _ev_graph(kind: str | None = None, text: str = "library X supports Y") -> dict:
    return graph.normalise(_cgraph({"G0": _cgoal(text, confidence=0.9, kind=kind)}, []))


def _research(run_dir: Path, g: dict, *, result: str = "supports", ts: str = TS1,
              eid: str = "r1", claim: str = "G0", **kw) -> Path:
    node = g["nodes"][claim]
    return ev.write_research(run_dir, eid, claim, node["text"],
                             source=kw.get("source", "https://docs.example/x"),
                             kind=kw.get("kind", "docs"),
                             citation=kw.get("citation", "§4.2 states X supports Y"),
                             result=result, ts=ts)


def _spike(run_dir: Path, g: dict, *, gate: str = "pass", ts: str = TS2, eid: str = "s1",
           claim: str = "G0", files: list | None = None) -> Path:
    """Record a Fold-2 spike leaf.

    `files` defaults to a real bound file rather than [] — an EMPTY list makes the spike's
    tamper-evidence vacuous, so it cannot approve a claim (see test_spike_with_no_files_cannot
    _approve). Every Fold-2 test used to pass [] implicitly, which meant they were all exercising
    the vacuous case; pass `files=[]` explicitly when that is the behaviour under test.
    """
    node = g["nodes"][claim]
    if files is None:
        bound = run_dir / "spiked_impl.py"
        bound.write_text("# the file this spike ran against\n")
        files = [bound]
    return ev.write_spike(run_dir, eid, claim, node["text"], cmd=["pytest", "-q"], gate=gate,
                          result_hash="deadbeef", files=files, ts=ts)


def test_fold1_is_mandatory_for_every_claim():
    """The headline failure: confidence typed high, no external source ever consulted."""
    run, g = _ev_run(), _ev_graph()
    ok = ev.oracle(run, g)("G0", "approve")
    check("P1 no evidence at all → NOT approvable", ok is False)
    check("P2 the reason names the MISSING FOLD 1, not a generic failure",
          "FOLD 1 MISSING" in ev.explain(run, g, "G0"), ev.explain(run, g, "G0"))
    _research(run, g)
    check("P3 with a research citation → approvable",
          ev.oracle(run, g)("G0", "approve") is True)
    check("P4 and the claim graph now converges",
          graph.converged(g, TH, ev.oracle(run, g)) is True)


def test_needs_experiment_also_requires_fold2():
    run, g = _ev_run(), _ev_graph(kind="needs-experiment")
    _research(run, g)
    check("P5 research alone does NOT approve a needs-experiment claim",
          ev.oracle(run, g)("G0", "approve") is False)
    check("P6 the reason names the missing FOLD 2",
          "FOLD 2 MISSING" in ev.explain(run, g, "G0"), ev.explain(run, g, "G0"))
    _spike(run, g)
    check("P7 research + passing spike → approvable",
          ev.oracle(run, g)("G0", "approve") is True)


def test_failing_spike_never_approves():
    run, g = _ev_run(), _ev_graph(kind="needs-experiment")
    _research(run, g)
    _spike(run, g, gate="fail")
    check("P8 a FAILING spike does not approve the claim",
          ev.oracle(run, g)("G0", "approve") is False)
    check("P9 a failing spike REFUTES the claim (→ discardable)",
          ev.oracle(run, g)("G0", "refute") is True)


def test_fold2_presupposes_fold1():
    """Spike first, back-fill the citation afterwards — must not count as research-first."""
    run, g = _ev_run(), _ev_graph(kind="needs-experiment")
    _spike(run, g, ts=TS1)          # spike ran first...
    _research(run, g, ts=TS3)       # ...citation invented afterwards
    check("P10 a spike that PREDATES its research is an order violation",
          ev.oracle(run, g)("G0", "approve") is False)
    check("P11 the reason says research comes first",
          "ORDER VIOLATION" in ev.explain(run, g, "G0"), ev.explain(run, g, "G0"))
    run2, g2 = _ev_run(), _ev_graph(kind="needs-experiment")
    _research(run2, g2, ts=TS1)
    _spike(run2, g2, ts=TS2)
    check("P12 research → spike, in that order, is accepted",
          ev.oracle(run2, g2)("G0", "approve") is True)


def test_stale_spike_is_rejected():
    """A green spike over a tree that changed afterwards tested a different program."""
    run, g = _ev_run(), _ev_graph(kind="needs-experiment")
    target = run / "impl.py"
    target.write_text("def f(): return 1\n")
    _research(run, g)
    _spike(run, g, files=[target])
    check("P13 a fresh passing spike over unchanged files approves",
          ev.oracle(run, g)("G0", "approve") is True)
    target.write_text("def f(): return 2\n")  # edit AFTER the green spike
    check("P14 editing the tree after the spike INVALIDATES it",
          ev.oracle(run, g)("G0", "approve") is False)
    check("P15 the reason says the spike is stale",
          "FOLD 2 STALE" in ev.explain(run, g, "G0"), ev.explain(run, g, "G0"))
    target.unlink()
    check("P16 deleting a spiked file also invalidates (absent ≠ unchanged)",
          ev.oracle(run, g)("G0", "approve") is False)


def test_rewording_a_claim_breaks_its_binding():
    run, g = _ev_run(), _ev_graph()
    _research(run, g)
    check("P17 evidence binds to the claim as worded", ev.oracle(run, g)("G0", "approve") is True)
    reworded = _ev_graph(text="library X supports Z (totally different claim)")
    check("P18 rewording the claim does NOT inherit the old evidence",
          ev.oracle(run, reworded)("G0", "approve") is False)


def test_forged_evidence_is_rejected():
    """A model with Write can drop files in the evidence dir — they must not validate."""
    run, g = _ev_run(), _ev_graph()
    ev.evidence_dir(run).mkdir(parents=True, exist_ok=True)
    forgeries = {
        "not-a-statement": {"claim": "G0", "verdict": "approved"},
        "wrong-type": {"_type": "https://example/Statement/v1", "subject": [],
                       "predicateType": ev.PREDICATE_RESEARCH, "predicate": {"ts": TS1}},
        "no-digest": {"_type": ev.STATEMENT_TYPE, "subject": [{"name": "G0", "digest": {}}],
                      "predicateType": ev.PREDICATE_RESEARCH,
                      "predicate": {"fold": "research", "kind": "docs", "source": "s",
                                    "citation": "c", "result": "supports", "ts": TS1}},
        "bogus-digest": {"_type": ev.STATEMENT_TYPE,
                         "subject": [{"name": "G0", "digest": {"sha256": "not-a-hash"}}],
                         "predicateType": ev.PREDICATE_RESEARCH,
                         "predicate": {"fold": "research", "kind": "docs", "source": "s",
                                       "citation": "c", "result": "supports", "ts": TS1}},
        "made-up-kind": {"_type": ev.STATEMENT_TYPE,
                         "subject": [{"name": "G0",
                                      "digest": {"sha256": ev.claim_digest(g["nodes"]["G0"]["text"])}}],
                         "predicateType": ev.PREDICATE_RESEARCH,
                         "predicate": {"fold": "research", "kind": "vibes", "source": "s",
                                       "citation": "c", "result": "supports", "ts": TS1}},
        "empty-citation": {"_type": ev.STATEMENT_TYPE,
                           "subject": [{"name": "G0",
                                        "digest": {"sha256": ev.claim_digest(g["nodes"]["G0"]["text"])}}],
                           "predicateType": ev.PREDICATE_RESEARCH,
                           "predicate": {"fold": "research", "kind": "docs", "source": "s",
                                         "citation": "   ", "result": "supports", "ts": TS1}},
    }
    for name, obj in forgeries.items():
        (ev.evidence_dir(run) / f"{name}.json").write_text(json.dumps(obj))
    (ev.evidence_dir(run) / "truncated.json").write_text("{nope")
    check("P19 no forged/malformed leaf validates", ev.read_leaves(run) == [],
          f"got {ev.read_leaves(run)}")
    check("P20 and the claim is therefore not approvable",
          ev.oracle(run, g)("G0", "approve") is False)


def test_needs_decision_is_never_agent_approvable():
    run, g = _ev_run(), _ev_graph(kind="needs-decision")
    _research(run, g)
    check("P21 a needs-decision claim is NOT approvable even with research",
          ev.oracle(run, g)("G0", "approve") is False)
    check("P22 the reason directs it to the human",
          "human" in ev.explain(run, g, "G0"), ev.explain(run, g, "G0"))


def test_discard_needs_refuting_evidence():
    run, g = _ev_run(), _ev_graph()
    check("P23 a claim with no evidence cannot be discarded",
          ev.oracle(run, g)("G0", "refute") is False)
    _research(run, g, result="supports")
    check("P24 SUPPORTING research does not license a discard",
          ev.oracle(run, g)("G0", "refute") is False)
    run2, g2 = _ev_run(), _ev_graph()
    _research(run2, g2, result="refutes")
    check("P25 REFUTING research licenses a discard",
          ev.oracle(run2, g2)("G0", "refute") is True)


def test_evidence_write_validation():
    run, g = _ev_run(), _ev_graph()
    for label, kw in [("bad kind", {"kind": "telepathy"}),
                      ("bad result", {"result": "maybe"}),
                      ("empty citation", {"citation": ""}),
                      ("empty source", {"source": ""})]:
        try:
            _research(run, g, **kw)
            ok = False
        except ValueError:
            ok = True
        check(f"P26 write_research rejects {label}", ok)
    try:
        _spike(run, g, gate="probably")
        ok = False
    except ValueError:
        ok = True
    check("P27 write_spike rejects a gate value that isn't pass/fail", ok)


def test_evidence_hashing_properties():
    check("P28 command hash distinguishes argv boundaries",
          ev.command_digest(["a b"]) != ev.command_digest(["a", "b"]))
    check("P29 files digest is order-independent",
          ev.files_digest([Path("/tmp/a"), Path("/tmp/b")])
          == ev.files_digest([Path("/tmp/b"), Path("/tmp/a")]))
    check("P30 empty file set still hashes deterministically",
          ev.files_digest([]) == ev.files_digest([]))


def test_harness_is_the_sole_writer_of_fold2():
    """END-TO-END: the Fold-2 record is written from a REAL subprocess exit code.

    This is the property that makes the spike record unforgeable. The test drives the harness
    as a subprocess — exactly as the model would via Bash — and then checks that the claim
    graph's verdict changed because a real command ran, not because anything was asserted.
    """
    run = Path(tempfile.mkdtemp())
    g = graph.normalise(_cgraph(
        {"G0": _cgoal("the check passes", confidence=0.9, kind="needs-experiment")}, []))
    graph.save(graph.default_graph_path(run), g)
    _research(run, g, ts=TS1)
    check("Q1 research alone leaves a needs-experiment claim open",
          ev.oracle(run, g)("G0", "approve") is False)

    # --file binds the tree the check ran against. Without it the record's tamper-evidence is
    # vacuous and (correctly) cannot approve — see Q4b.
    bound = run / "checked.py"
    bound.write_text("# the file under test\n")
    proc = subprocess.run(
        [sys.executable, str(HARNESS), "--claim", "G0", "--run-dir", str(run),
         "--ts", TS2, "--file", str(bound), sys.executable, "-c", "print('ok')"],
        capture_output=True, text=True)
    out = json.loads(proc.stdout)
    check("Q2 the harness reports it recorded the evidence",
          out.get("evidence", {}).get("recorded") is True, f"{out.get('evidence')}")
    check("Q3 the gate value came from the real exit code", out["gate"] == "pass")
    check("Q4 a real passing spike over BOUND files approves the claim",
          ev.oracle(run, g)("G0", "approve") is True)
    bound.write_text("# edited after the green spike\n")
    check("Q4b …and editing that file afterwards revokes the approval",
          ev.oracle(run, g)("G0", "approve") is False)

    # And the negative: a genuinely FAILING command must not produce an approving record.
    run2 = Path(tempfile.mkdtemp())
    graph.save(graph.default_graph_path(run2), g)
    _research(run2, g, ts=TS1)
    subprocess.run(
        [sys.executable, str(HARNESS), "--claim", "G0", "--run-dir", str(run2),
         "--ts", TS2, sys.executable, "-c", "import sys; sys.exit(1)"],
        capture_output=True, text=True)
    check("Q5 a FAILING command records gate=fail and does not approve",
          ev.oracle(run2, g)("G0", "approve") is False)
    check("Q6 …and that failing spike refutes the claim",
          ev.oracle(run2, g)("G0", "refute") is True)


def test_harness_refuses_to_bind_evidence_to_an_unknown_claim():
    """A caller cannot invent a claim id to attach a green spike to."""
    run = Path(tempfile.mkdtemp())
    g = graph.normalise(_cgraph({"G0": _cgoal("real claim", confidence=0.9)}, []))
    graph.save(graph.default_graph_path(run), g)
    proc = subprocess.run(
        [sys.executable, str(HARNESS), "--claim", "GHOST", "--run-dir", str(run),
         "--ts", TS2, sys.executable, "-c", "print('ok')"],
        capture_output=True, text=True)
    out = json.loads(proc.stdout)
    check("Q7 binding a spike to a non-existent claim is refused",
          out.get("evidence", {}).get("recorded") is False, f"{out.get('evidence')}")
    check("Q8 no evidence leaf was written", ev.read_leaves(run) == [])
    check("Q9 the harness still reported the command's real verdict", out["gate"] == "pass")


def test_harness_without_evidence_flags_is_unchanged():
    """Back-compat: the plain gate still works and records nothing."""
    proc = subprocess.run(
        [sys.executable, str(HARNESS), sys.executable, "-c", "print('hi')"],
        capture_output=True, text=True)
    out = json.loads(proc.stdout)
    check("Q10 a plain run has no evidence block", "evidence" not in out, f"{out.keys()}")
    check("Q11 a plain run still gates on the exit code", out["gate"] == "pass")


# --- Independent audit (ADR-20 P6, ADR-21 M3) -------------------------------
# The attack surface here: skip the audit entirely, write a verdict with no spawn behind it,
# pass a run while reviewing only one claim, or reuse a verdict from a different run.
GATE_SPAWN_HOOK = HOOKS / "spawn_gate.py"


def _converged_run(sid: str = DEFAULT_SID) -> Path:
    """A run whose claim graph is fully converged and evidenced — so the ONLY thing that can
    still block it is the missing independent audit."""
    return write_run([{"text": "settled claim", "confidence": 0.9}], sid=sid)


def test_converged_graph_still_blocks_without_audit():
    """The P6 gate: a converged claim graph is NOT sufficient to report convergence."""
    d = _converged_run()
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("R1 a converged graph with NO audit still BLOCKS", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")
    check("R2 the block message names the independent audit",
          "independent audit" in p.stderr, f"got {p.stderr!r}")


def test_auditor_spawn_issues_a_ticket():
    """The harness-proven half: the PreToolUse hook records that an auditor was spawned."""
    d = _converged_run()
    run_dir = manifest.locate_run_dir(d, DEFAULT_SID)
    p = run_hook(GATE_SPAWN_HOOK, {"tool_name": "Agent", "cwd": str(d),
                                   "session_id": DEFAULT_SID,
                                   "tool_input": {"subagent_type": "empirica-auditor"}}, d)
    check("R3 spawning the auditor is allowed", p.returncode == 0, f"rc={p.returncode}")
    tickets = aud._read_tickets(aud.tickets_path(run_dir))
    check("R4 the spawn gate recorded an audit ticket", len(tickets) == 1, f"got {tickets}")
    d2 = _converged_run(sid="sess-other")
    run_hook(GATE_SPAWN_HOOK, {"tool_name": "Agent", "cwd": str(d2),
                               "session_id": "sess-other",
                               "tool_input": {"subagent_type": "general-purpose"}}, d2)
    check("R5 a non-auditor spawn issues NO ticket",
          aud._read_tickets(aud.tickets_path(manifest.locate_run_dir(d2, "sess-other"))) == [],
          "a general-purpose spawn must not count as an audit")


def _spawn_auditor(d: Path, sid: str = DEFAULT_SID) -> str:
    run_hook(GATE_SPAWN_HOOK, {"tool_name": "Agent", "cwd": str(d), "session_id": sid,
                               "tool_input": {"subagent_type": "empirica-auditor"}}, d)
    tickets = aud._read_tickets(aud.tickets_path(manifest.locate_run_dir(d, sid)))
    return tickets[-1]["nonce"] if tickets else ""


def _write_verdict(d: Path, sid: str = DEFAULT_SID, **kw) -> None:
    run_dir = manifest.locate_run_dir(d, sid)
    body = {"verdict": "pass", "nonce": kw.get("nonce", ""), "auditor": "empirica-auditor",
            "claims_reviewed": kw.get("claims_reviewed", ["G0", "G1"]),
            "findings": kw.get("findings", []), "ts": "2026-07-24T13:00:00Z"}
    body.update({k: v for k, v in kw.items() if k in ("verdict",)})
    aud.verdict_path(run_dir).write_text(json.dumps(body))


def test_full_audit_chain_allows_convergence():
    """The happy path, end to end: spawn → verdict with the right nonce → converged."""
    d = _converged_run()
    nonce = _spawn_auditor(d)
    _write_verdict(d, nonce=nonce)
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("R6 a passing audit bound to a real spawn ALLOWS convergence", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")
    out = json.loads(p.stdout)
    check("R7 the run reports converged:true", out.get("converged") is True, f"got {out}")
    check("R8 …and records that the audit passed", out.get("audit") == "passed", f"got {out}")


def test_verdict_without_a_spawn_is_refused():
    """The forgery that matters most: the author writes a verdict having spawned nobody."""
    d = _converged_run()
    _write_verdict(d, nonce=aud.spawn_nonce("whatever", 1))
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("R9 a verdict with NO auditor spawn behind it is refused", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_verdict_with_wrong_nonce_is_refused():
    d = _converged_run()
    _spawn_auditor(d)
    _write_verdict(d, nonce="deadbeefdeadbeefdeadbeefdeadbeef")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("R10 a verdict whose nonce matches no spawn is refused", p.returncode == 2,
          f"rc={p.returncode}")
    check("R11 the reason names the nonce mismatch", "nonce" in p.stderr, f"got {p.stderr!r}")


def test_failing_audit_blocks():
    d = _converged_run()
    nonce = _spawn_auditor(d)
    _write_verdict(d, nonce=nonce, verdict="fail",
                   findings=["G1's citation 404s — the source does not exist"])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("R12 a FAILING audit blocks convergence", p.returncode == 2, f"rc={p.returncode}")
    check("R13 the auditor's finding is surfaced to the agent",
          "404" in p.stderr, f"got {p.stderr!r}")


def test_partial_audit_is_refused():
    """An auditor cannot pass a run by reviewing one claim and ignoring the rest."""
    d = write_run([{"text": "claim one", "confidence": 0.9},
                   {"text": "claim two", "confidence": 0.9}])
    nonce = _spawn_auditor(d)
    _write_verdict(d, nonce=nonce, claims_reviewed=["G1"])  # G0 and G2 unreviewed
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("R14 a verdict that skipped approved claims is refused", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")
    check("R15 the reason says which claims went unreviewed",
          "did not review" in p.stderr, f"got {p.stderr!r}")


def test_malformed_verdict_reads_as_absent():
    d = _converged_run()
    _spawn_auditor(d)
    aud.verdict_path(manifest.locate_run_dir(d, DEFAULT_SID)).write_text("{ truncated")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("R16 a malformed verdict blocks (reads as absent)", p.returncode == 2,
          f"rc={p.returncode}")
    check("R17 the audit check itself reports no readable verdict",
          aud.read_verdict(manifest.locate_run_dir(d, DEFAULT_SID)) is None)


def test_residual_stop_does_not_require_an_audit():
    """A run that stops with residuals is not claiming convergence — requiring an audit there
    would wedge a run that has honestly given up."""
    d = write_run([{"text": "human call needed", "confidence": 0.2,
                    "blocked": "needs-decision"}])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("R18 a residual stop is allowed with NO audit", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")
    check("R19 …and is honestly reported non-converged",
          json.loads(p.stdout).get("converged") is False, f"got {p.stdout!r}")


def test_audit_gate_terminates_at_cap():
    """An agent that never produces a passing audit must still terminate (ADR-19 variant)."""
    d = write_run([{"text": "settled", "confidence": 0.9}], max_passes=2)
    codes = [run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d).returncode
             for _ in range(2)]
    run = manifest.read_run(manifest.locate_run(d, DEFAULT_SID))
    check("R20 the audit gate blocks then terminates at max_passes", codes == [2, 0],
          f"got {codes}")
    check("R21 an un-audited run terminates as stopped_residual, never converged",
          run["status"] == "stopped_residual", f"got {run}")


def test_stale_audit_is_rejected():
    """REGRESSION — the ticket's `pass` field was written and validated but never COMPARED, so
    an audit from pass 1 could certify a graph that kept changing for seven more passes."""
    tickets = [{"nonce": "a" * 32, "pass": 1}]
    run = Path(tempfile.mkdtemp())
    aud.tickets_path(run).parent.mkdir(parents=True, exist_ok=True)
    aud.tickets_path(run).write_text(json.dumps({"tickets": tickets}))
    aud.verdict_path(run).write_text(json.dumps(
        {"verdict": "pass", "nonce": "a" * 32, "auditor": "empirica-auditor",
         "claims_reviewed": ["G0"], "findings": [], "ts": "2026-07-24T13:00:00Z"}))
    ok, reason = aud.check(run, ["G0"], 1)
    check("R35 an audit at the CURRENT pass is accepted", ok is True, reason)
    ok2, reason2 = aud.check(run, ["G0"], 4)
    check("R36 an audit from an EARLIER pass is rejected as stale", ok2 is False, reason2)
    check("R37 the reason says the graph changed since review", "STALE" in reason2, reason2)
    ok3, _ = aud.check(run, ["G0"])
    check("R38 omitting current_pass keeps the old behaviour (back-compat)", ok3 is True)


def test_legacy_shape_cannot_free_a_blocking_run():
    """REGRESSION — the legacy escape hatch was an exploit.

    `is_legacy` is a pure manifest-SHAPE test. It used to be evaluated before the graph loaded,
    so dropping `graph_path` and adding `spec_path` let any actively-blocking run walk free —
    defeating the fail-closed gate. A run that HAS a claim graph must be judged on that graph
    regardless of manifest shape. Reproduced live, then found again by an independent doc audit.
    """
    d = write_run([{"text": "unfinished work", "confidence": 0.1}], sid="sess-legacy-x")
    sid = "sess-legacy-x"
    p = run_hook(GATE, {"cwd": str(d), "session_id": sid}, d)
    check("G11 an honest run with an open claim BLOCKS", p.returncode == 2, f"rc={p.returncode}")
    rp = manifest.locate_run(d, sid)
    data = json.loads(rp.read_text())
    data.pop("graph_path", None)
    data["spec_path"] = str(rp.parent / "spec.md")   # forge a legacy-looking manifest
    rp.write_text(json.dumps(data))
    p2 = run_hook(GATE, {"cwd": str(d), "session_id": sid}, d)
    check("G12 downgrading the manifest to 'legacy' does NOT free the run", p2.returncode == 2,
          f"rc={p2.returncode} stdout={p2.stdout!r}")
    # And a genuine legacy run — legacy shape AND no graph — still fails OPEN, never wedges.
    d3 = Path(tempfile.mkdtemp())
    rp3 = manifest.locate_run(d3, DEFAULT_SID)
    manifest.start_run(rp3, DEFAULT_SID, d3)
    data3 = json.loads(rp3.read_text())
    data3.pop("graph_path", None)
    data3["spec_path"] = str(rp3.parent / "spec.md")
    rp3.write_text(json.dumps(data3))
    p3 = run_hook(GATE, {"cwd": str(d3), "session_id": DEFAULT_SID}, d3)
    check("G13 a GENUINE legacy run (no graph) still fails OPEN", p3.returncode == 0,
          f"rc={p3.returncode} stderr={p3.stderr!r}")


def test_route_announcement_is_recorded():
    """REGRESSION — `manifest.stamp_route` had NO production caller, so `route_ts` was
    permanently None and the whole P1 check could never fire. The skill now records the
    announcement through route_stamp.py --announce-route."""
    d = write_run([{"text": "c", "confidence": 0.1}], sid="sess-announce")
    sid = "sess-announce"
    rp = manifest.locate_run(d, sid)
    check("S21 route_ts starts unset", manifest.read_run(rp)["route_ts"] is None)
    p = subprocess.run([sys.executable, str(STAMP), "--announce-route", "--session", sid,
                        "--ts", "2026-07-24T10:00:00Z", "--cwd", str(d)],
                       capture_output=True, text=True, cwd=str(d))
    check("S22 --announce-route exits 0", p.returncode == 0, f"rc={p.returncode} {p.stderr!r}")
    check("S23 the route announcement is recorded",
          manifest.read_run(rp)["route_ts"] == "2026-07-24T10:00:00Z",
          f"got {manifest.read_run(rp)['route_ts']}")
    # First write wins — a later announcement cannot backdate the commitment.
    subprocess.run([sys.executable, str(STAMP), "--announce-route", "--session", sid,
                    "--ts", "2026-07-24T08:00:00Z", "--cwd", str(d)],
                   capture_output=True, text=True, cwd=str(d))
    check("S24 the announcement cannot be backdated",
          manifest.read_run(rp)["route_ts"] == "2026-07-24T10:00:00Z")
    # And the real ordering check now has both stamps to compare.
    manifest.stamp_first_tool(rp, "2026-07-24T11:00:00Z")
    ok, reason = manifest.route_before_investigation(manifest.read_run(rp))
    check("S25 announce-then-investigate passes the P1 check", ok is True, reason)
    check("S26 SKILL.md tells the agent to record the announcement",
          "--announce-route" in (HOOKS.parent / "skills/empirica/SKILL.md").read_text(),
          "the skill must call route_stamp.py --announce-route or route_ts stays None")


def test_spawn_ledger_is_keyed_to_the_run():
    """REGRESSION — the spawn gate read a shared `default/` ledger, so a max_spawns written
    where the docs say (the run dir) was never read and the cap silently did not apply."""
    d = Path(tempfile.mkdtemp())
    sid = "sess-ledger"
    manifest.start_run(manifest.locate_run(d, sid), sid, d)
    run_dir = manifest.locate_run_dir(d, sid)
    # Write the cap exactly where SKILL.md tells the user to write it.
    budget.write_ledger(run_dir / "budget.json", {"max_spawns": 1, "spawns": 1})
    p = run_hook(GATE_SPAWN_HOOK, {"tool_name": "Agent", "cwd": str(d), "session_id": sid,
                                   "tool_input": {"subagent_type": "general-purpose"}}, d)
    check("D14 a cap written in the RUN DIR is actually enforced", p.returncode == 2,
          f"rc={p.returncode} — the gate read a different ledger path")
    check("D15 the denial names the budget", "budget" in p.stderr.lower(), f"{p.stderr!r}")
    # Sanity: the derived path is the run dir, not `default/`.
    check("D16 locate_ledger(run_id) resolves inside the run directory",
          budget.locate_ledger(d, manifest.run_id(sid, d)) == run_dir / "budget.json",
          f"got {budget.locate_ledger(d, manifest.run_id(sid, d))}")


def test_p1_violation_survives_a_passing_audit():
    """REGRESSION — a passing audit must not LAUNDER a route-before-investigate violation.

    The P1 check used to run only in the audit-failure branch, so a run that investigated first
    and then got a passing (or rubber-stamped) audit converged with no mention of it — falsifying
    ADR-20 fitness function 3 in the one case that matters. Found by independent coverage review.
    """
    d = write_run([{"text": "settled", "confidence": 0.9}])
    rp = manifest.locate_run(d, DEFAULT_SID)
    manifest.stamp_first_tool(rp, "2026-07-24T10:00:00Z")   # investigated first...
    manifest.stamp_route(rp, "2026-07-24T12:00:00Z")        # ...routed afterwards
    _write_verdict(d, nonce=_spawn_auditor(d), claims_reviewed=["G0", "G1"])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("R31 the stop is allowed (P1 is a coarse signal, not fatal)", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")
    out = json.loads(p.stdout)
    check("R32 the P1 violation is REPORTED in the result, not swallowed",
          "p1_violation" in out, f"got {out}")
    check("R33 …and the note says the route was retroactive",
          "retroactive" in out.get("note", "") or "retroactive" in out.get("p1_violation", ""),
          f"got {out}")
    # Control: a correctly-ordered run must NOT be flagged.
    d2 = write_run([{"text": "settled", "confidence": 0.9}], sid="sess-p1ok")
    rp2 = manifest.locate_run(d2, "sess-p1ok")
    manifest.stamp_route(rp2, "2026-07-24T10:00:00Z")
    manifest.stamp_first_tool(rp2, "2026-07-24T11:00:00Z")
    _write_verdict(d2, sid="sess-p1ok", nonce=_spawn_auditor(d2, "sess-p1ok"),
                   claims_reviewed=["G0", "G1"])
    p2 = run_hook(GATE, {"cwd": str(d2), "session_id": "sess-p1ok"}, d2)
    out2 = json.loads(p2.stdout)
    check("R34 a correctly-routed run is NOT flagged (no false positive)",
          "p1_violation" not in out2 and out2.get("converged") is True, f"got {out2}")


def test_spike_with_no_files_cannot_approve():
    """REGRESSION — a spike binding no files has vacuous tamper-evidence.

    files_digest([]) is a constant, so a no-`--file` spike's files_hash matches forever no matter
    what changes on disk. That silently voided Fold-2 staleness detection for the claim. Such a
    spike must not approve. Found by independent coverage review.
    """
    run, g = _ev_run(), _ev_graph(kind="needs-experiment")
    _research(run, g, ts=TS1)
    _spike(run, g, ts=TS2, files=[])  # green, but binds nothing
    check("P31 a passing spike that binds NO files does not approve the claim",
          ev.oracle(run, g)("G0", "approve") is False)
    check("P32 the reason says the tamper-evidence is unbound, not merely stale",
          "FOLD 2 UNBOUND" in ev.explain(run, g, "G0"), ev.explain(run, g, "G0"))
    # Control: the same spike WITH a file binding does approve, and still goes stale on edit.
    run2, g2 = _ev_run(), _ev_graph(kind="needs-experiment")
    target = run2 / "impl.py"
    target.write_text("v=1\n")
    _research(run2, g2, ts=TS1)
    _spike(run2, g2, ts=TS2, files=[target])
    check("P33 the same spike WITH a file binding approves",
          ev.oracle(run2, g2)("G0", "approve") is True)
    target.write_text("v=2\n")
    check("P34 …and still detects the tree changing afterwards",
          ev.oracle(run2, g2)("G0", "approve") is False)


def test_auditor_is_spawned_by_its_plugin_scoped_name():
    """DEADLOCK GUARD. A plugin-provided subagent resolves ONLY under its plugin-scoped name;
    the bare name raises "Agent type not found". If SKILL.md instructs the bare form, the spawn
    fails, no audit ticket is written, and NO run can ever converge. This is the same namespacing
    trap that once made the UserPromptExpansion matcher silently never fire (M26-M28).

    Verified against live docs + an observed probe: a structurally identical plugin agent
    resolved as `plugin-dev:agent-creator` and failed as bare `agent-creator`.
    """
    skill = (HOOKS.parent / "skills/empirica/SKILL.md").read_text()
    import re as _re
    calls = _re.findall(r'subagent_type=["\']([^"\']+)["\']', skill)
    check("R25 SKILL.md tells the agent how to spawn the auditor", bool(calls),
          "no spawn call found")
    for call in calls:
        check(f"R26 spawn call {call!r} uses the plugin-scoped name",
              call.startswith("empirica:"),
              f"{call!r} is unscoped — a real spawn would fail and the run would deadlock")
    # The ticket detector must accept the scoped form, or the corrected call still writes no
    # ticket. Both forms are tolerated deliberately (substring match).
    check("R27 the ticket detector accepts the scoped name",
          aud.AUDITOR_AGENT in "empirica:empirica-auditor")
    check("R28 …and still accepts a bare name (detector is not the constraint)",
          aud.AUDITOR_AGENT in "empirica-auditor")
    # And the agent file that name must resolve to actually exists, with a matching frontmatter
    # name — a scoped call pointing at a missing definition deadlocks just as hard.
    auditor = HOOKS.parent / "agents" / "empirica-auditor.md"
    check("R29 the auditor definition exists at agents/empirica-auditor.md", auditor.exists())
    check("R30 its frontmatter name matches the spawned name",
          "\nname: empirica-auditor\n" in auditor.read_text(),
          "frontmatter name must equal the unscoped part of the spawn name")


def test_agent_definitions_pin_tiers_not_ids_in_logic():
    """ADR-23 fitness #3: model IDs appear only in agent definitions (config), never in the
    workflow logic (hooks/skill)."""
    agents_dir = HOOKS.parent / "agents"
    defs = sorted(agents_dir.glob("*.md"))
    check("R22 the three ADR-23 role agents ship with the plugin", len(defs) == 3,
          f"got {[d.name for d in defs]}")
    for d in defs:
        text = d.read_text()
        check(f"R23 {d.stem} declares a model tier", "\nmodel:" in text, "missing model: frontmatter")
    # No concrete model id anywhere in the hooks or the skill.
    leaked = []
    for path in list(HOOKS.glob("*.py")) + [HOOKS.parent / "skills/empirica/SKILL.md"]:
        body = path.read_text()
        for token in ("claude-opus", "claude-sonnet", "claude-haiku", "claude-fable",
                      "us.anthropic", "eu.anthropic"):
            if token in body:
                leaked.append(f"{path.name}:{token}")
    check("R24 no concrete model ID leaks into hooks or the skill", leaked == [], f"{leaked}")


# --- Phase machine + route-before-investigate (ADR-21 M1, ADR-20 P1) --------
STAMP = HOOKS / "route_stamp.py"


def test_phase_machine_records_phases():
    d = Path(tempfile.mkdtemp())
    rp = manifest.locate_run(d, DEFAULT_SID)
    run = manifest.start_run(rp, DEFAULT_SID, d)
    check("S1 a run starts at phase `route`", run["phase"] == "route", f"got {run['phase']}")
    manifest.set_phase(rp, "resolve")
    check("S2 the phase is recorded", manifest.read_run(rp)["phase"] == "resolve")
    try:
        manifest.set_phase(rp, "vibing")
        ok = False
    except ValueError:
        ok = True
    check("S3 an invalid phase is rejected", ok)
    check("S4 phase survives as a record, not a permission",
          manifest.read_run(rp)["phase"] == "resolve")


def test_route_stamp_hook_records_first_investigation():
    d = write_run([{"text": "c", "confidence": 0.1}])
    rp = manifest.locate_run(d, DEFAULT_SID)
    p = run_hook(STAMP, {"tool_name": "Read", "cwd": str(d), "session_id": DEFAULT_SID,
                         "timestamp": "2026-07-24T10:00:00Z"}, d)
    check("S5 the stamp hook always exits 0 (observe, never deny)", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")
    check("S6 the first investigative tool call is stamped",
          manifest.read_run(rp)["first_tool_ts"] == "2026-07-24T10:00:00Z",
          f"got {manifest.read_run(rp)}")
    # A later call must NOT overwrite it — the stamp marks the genuine START.
    run_hook(STAMP, {"tool_name": "Bash", "cwd": str(d), "session_id": DEFAULT_SID,
                     "timestamp": "2026-07-24T18:00:00Z"}, d)
    check("S7 a later tool call cannot push the stamp later",
          manifest.read_run(rp)["first_tool_ts"] == "2026-07-24T10:00:00Z",
          f"got {manifest.read_run(rp)['first_tool_ts']}")


def test_route_stamp_ignores_non_investigative_tools():
    d = write_run([{"text": "c", "confidence": 0.1}])
    rp = manifest.locate_run(d, DEFAULT_SID)
    for tool in ("Write", "Edit", "TaskCreate"):
        run_hook(STAMP, {"tool_name": tool, "cwd": str(d), "session_id": DEFAULT_SID,
                         "timestamp": "2026-07-24T10:00:00Z"}, d)
    check("S8 writing/editing is not investigation → no stamp",
          manifest.read_run(rp)["first_tool_ts"] is None,
          f"got {manifest.read_run(rp)['first_tool_ts']}")


def test_route_stamp_is_a_noop_outside_a_run():
    d = Path(tempfile.mkdtemp())  # no manifest at all
    p = run_hook(STAMP, {"tool_name": "Read", "cwd": str(d), "session_id": "sess-unrelated",
                         "timestamp": "2026-07-24T10:00:00Z"}, d)
    check("S9 the stamp hook is silent and harmless outside an empirica run",
          p.returncode == 0 and p.stdout.strip() == "", f"rc={p.returncode} out={p.stdout!r}")


def test_route_before_investigation_verdict():
    d = Path(tempfile.mkdtemp())
    rp = manifest.locate_run(d, DEFAULT_SID)
    manifest.start_run(rp, DEFAULT_SID, d)
    ok, _ = manifest.route_before_investigation(manifest.read_run(rp))
    check("S10 nothing investigated yet → no violation", ok is True)

    manifest.stamp_route(rp, "2026-07-24T10:00:00Z")
    manifest.stamp_first_tool(rp, "2026-07-24T11:00:00Z")
    ok, reason = manifest.route_before_investigation(manifest.read_run(rp))
    check("S11 route BEFORE investigation → ok", ok is True, reason)

    # The inversion: investigate first, announce the route afterwards.
    d2 = Path(tempfile.mkdtemp())
    rp2 = manifest.locate_run(d2, DEFAULT_SID)
    manifest.start_run(rp2, DEFAULT_SID, d2)
    manifest.stamp_first_tool(rp2, "2026-07-24T10:00:00Z")
    manifest.stamp_route(rp2, "2026-07-24T12:00:00Z")
    ok, reason = manifest.route_before_investigation(manifest.read_run(rp2))
    check("S12 route AFTER investigation → VIOLATION", ok is False, reason)
    check("S13 the reason says the route was retroactive", "retroactive" in reason, reason)

    # Investigated with no route announced at all.
    d3 = Path(tempfile.mkdtemp())
    rp3 = manifest.locate_run(d3, DEFAULT_SID)
    manifest.start_run(rp3, DEFAULT_SID, d3)
    manifest.stamp_first_tool(rp3, "2026-07-24T10:00:00Z")
    ok, reason = manifest.route_before_investigation(manifest.read_run(rp3))
    check("S14 investigating with NO route announced → VIOLATION", ok is False, reason)


def test_route_stamp_first_write_wins_on_route_too():
    """A run cannot re-stamp an earlier route to make a bad ordering look good."""
    d = Path(tempfile.mkdtemp())
    rp = manifest.locate_run(d, DEFAULT_SID)
    manifest.start_run(rp, DEFAULT_SID, d)
    manifest.stamp_route(rp, "2026-07-24T12:00:00Z")
    manifest.stamp_route(rp, "2026-07-24T08:00:00Z")  # try to backdate
    check("S15 the route stamp cannot be backdated",
          manifest.read_run(rp)["route_ts"] == "2026-07-24T12:00:00Z",
          f"got {manifest.read_run(rp)['route_ts']}")


def test_gate_surfaces_p1_violation_to_the_auditor():
    d = write_run([{"text": "settled", "confidence": 0.9}])
    rp = manifest.locate_run(d, DEFAULT_SID)
    manifest.stamp_first_tool(rp, "2026-07-24T10:00:00Z")
    manifest.stamp_route(rp, "2026-07-24T12:00:00Z")  # investigated first
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("S16 the gate flags the P1 violation for the auditor",
          "P1" in p.stderr and "retroactive" in p.stderr, f"got {p.stderr!r}")


def test_hooks_json_registers_the_stamp_hook():
    cfg = json.loads((HOOKS / "hooks.json").read_text())
    pre = cfg["hooks"].get("PreToolUse", [])
    matchers = [g.get("matcher", "") for g in pre]
    check("S17 the stamp hook is registered on PreToolUse",
          any("Read" in m and "Bash" in m for m in matchers), f"got {matchers}")
    check("S18 the spawn gate is still registered on Agent",
          any(m == "Agent" for m in matchers), f"got {matchers}")
    import re as _re
    for group in pre:
        for h in group.get("hooks", []):
            script = h["args"][0].rsplit("/", 1)[-1]
            check(f"S19 {script} referenced by hooks.json exists",
                  (HOOKS / script).exists(), f"missing {script}")
    check("S20 the stamp matcher is a regex alternation over investigative tools",
          any(_re.search(m, "WebFetch") for m in matchers if "|" in m), f"got {matchers}")


def main() -> int:
    for t in [test_parse, test_converged_math, test_theta_guard,
              test_hook_blocks_when_unconverged, test_hook_allows_when_converged,
              test_hook_fail_open_missing_run, test_unscored_claim_blocks,
              test_out_of_range_confidence_blocks, test_unreadable_graph_fails_closed,
              test_deleted_graph_fails_closed, test_corrupt_graph_fails_closed,
              test_blocked_claim_allows, test_off_path_node_does_not_block,
              test_malformed_stdin_no_crash,
              test_invalid_blocked_tag_still_blocks, test_valid_blocked_tags_allow,
              test_forged_state_field_does_not_bypass_the_gate,
              test_gate_reports_which_fold_is_missing, test_legacy_run_fails_open_not_wedged, test_strict_coercion_rejects_bad_caps,
              test_infinity_ledger_does_not_crash, test_run_id_sanitised,
              test_gate_pass, test_gate_fail,
              test_gate_is_real_not_judgment, test_gate_timeout_fails,
              test_harness_propagates_exit_code, test_harness_launch_failure_is_fail,
              test_harness_large_output_bounded,
              test_state_restore_reinjects_claims,
              test_state_restore_reports_missing_fold, test_state_restore_no_run_is_silent,
              test_state_restore_silent_on_terminal_run,
              test_budget_math_unbounded_and_bounded, test_reserve_spawn_atomic_increment_and_cap,
              test_missing_ledger_fail_open, test_spawn_gate_denies_over_cap,
              test_spawn_gate_ignores_non_agent_tools, test_spawn_gate_unbounded_allows,
              test_gate_budget_exhausted_is_non_converged, test_gate_true_convergence_flagged_true,
              test_gate_budget_does_not_stop_healthy_loop,
              test_manifest_lifecycle_and_idempotent_start, test_manifest_run_id_stable_and_keyed,
              test_manifest_corrupt_sentinel, test_manifest_variant_terminates,
              test_manifest_evidence_slot, test_gate_active_run_missing_graph_fails_closed,
              test_gate_no_manifest_missing_graph_fails_open, test_gate_corrupt_manifest_fails_closed,
              test_graph_path_outside_run_dir_is_rejected,
              test_gate_pass_counter_terminates_at_cap, test_gate_active_run_converges_records_status,
              test_gate_stopped_run_does_not_reblock, test_run_start_hook_creates_manifest,
              test_run_start_no_session_id_is_noop, test_run_start_with_real_captured_payload,
              test_hooks_json_matcher_is_regex_for_namespaced_command,
              test_claim_state_cannot_be_forged, test_headline_self_attestation_attack,
              test_discard_requires_validating_refutation, test_discarded_subtree_is_pruned,
              test_cyclic_graph_is_rejected, test_refuting_the_root_is_not_convergence, test_off_path_nodes_do_not_gate, test_claim_confidence_coercion,
              test_illegal_gsn_edges_are_corrupt, test_claim_graph_fail_matrix,
              test_claim_blocked_tag_hygiene, test_claim_graph_roundtrip,
              test_fold1_is_mandatory_for_every_claim, test_needs_experiment_also_requires_fold2,
              test_failing_spike_never_approves, test_fold2_presupposes_fold1,
              test_stale_spike_is_rejected, test_rewording_a_claim_breaks_its_binding,
              test_forged_evidence_is_rejected, test_needs_decision_is_never_agent_approvable,
              test_discard_needs_refuting_evidence, test_evidence_write_validation,
              test_evidence_hashing_properties, test_harness_is_the_sole_writer_of_fold2,
              test_harness_refuses_to_bind_evidence_to_an_unknown_claim,
              test_harness_without_evidence_flags_is_unchanged,
              test_converged_graph_still_blocks_without_audit,
              test_auditor_spawn_issues_a_ticket, test_full_audit_chain_allows_convergence,
              test_verdict_without_a_spawn_is_refused, test_verdict_with_wrong_nonce_is_refused,
              test_failing_audit_blocks, test_partial_audit_is_refused,
              test_malformed_verdict_reads_as_absent,
              test_residual_stop_does_not_require_an_audit, test_audit_gate_terminates_at_cap,
              test_stale_audit_is_rejected, test_legacy_shape_cannot_free_a_blocking_run, test_route_announcement_is_recorded,
              test_spawn_ledger_is_keyed_to_the_run, test_p1_violation_survives_a_passing_audit, test_spike_with_no_files_cannot_approve,
              test_auditor_is_spawned_by_its_plugin_scoped_name,
              test_agent_definitions_pin_tiers_not_ids_in_logic,
              test_phase_machine_records_phases,
              test_route_stamp_hook_records_first_investigation,
              test_route_stamp_ignores_non_investigative_tools,
              test_route_stamp_is_a_noop_outside_a_run,
              test_route_before_investigation_verdict,
              test_route_stamp_first_write_wins_on_route_too,
              test_gate_surfaces_p1_violation_to_the_auditor,
              test_hooks_json_registers_the_stamp_hook]:
        t()
    width = max(len(n) for n, _, _ in results)
    passed = 0
    for name, ok, detail in results:
        line = f"  [{'PASS' if ok else 'FAIL'}] {name.ljust(width)}"
        if not ok and detail:
            line += f"  → {detail}"
        print(line)
        passed += ok
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
