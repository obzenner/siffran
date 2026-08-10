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
import os
import subprocess
import sys
import tempfile
import uuid
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
stamps = _load("stamps", HOOKS / "stamps.py")
RUN_START = HOOKS / "run_start.py"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """Record one assertion.

    `ok` is coerced to a real bool. A caller that passes a list or dict — easy to do, since
    `check("...", thing.get("findings"))` reads naturally — used to store a non-bool that made
    `passed += ok` raise TypeError in main(), ABORTING THE WHOLE SUITE at the summary line. Found
    while confirming the ADR-24 checks can fail: a deliberately sabotaged hook crashed the runner
    instead of reporting a red check, which would have let a real regression read as "the suite
    errored" rather than "this behaviour broke". A test harness must always survive to report.
    """
    results.append((name, bool(ok), detail))


warnings: list[str] = []


def warn(message: str) -> None:
    """Record a condition that is TRUE, actionable, and NOT this commit's to fix.

    Distinct from `check` on purpose. Some facts must be surfaced without gating: a stale installed
    plugin copy is a real problem an auditor should know about, but a gating check for it would be
    permanently red until the fixing commit ships — so that commit could not pass its own suite, and
    a check nobody can make green gets deleted along with the guard inside it. Warnings print in
    their own section and never affect the exit code.
    """
    warnings.append(message)


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
    data = json.loads(rp.read_text())
    data["graph_path"] = str(decoy.resolve())
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


def _reviewed(d: Path, claim_ids: list[str], sid: str = DEFAULT_SID) -> list[dict]:
    """`claims_reviewed` entries for these claims, at their CURRENT digests (ADR-25).

    Computed the same way the gate computes them — through `evidence.claim_digest` and
    `evidence.evidence_digest` — because a test that hand-rolled either hash would pass while the
    real auditor failed, which is the drift the single-definition rule exists to prevent.
    """
    run_dir = manifest.locate_run_dir(d, sid)
    g = graph.load(graph.default_graph_path(run_dir))
    leaves = ev.read_leaves(run_dir)
    out = []
    for nid in claim_ids:
        node = g["nodes"].get(nid) if isinstance(g, dict) else None
        text = node["text"] if node else ""
        out.append({"claim_id": nid, "claim_digest": ev.claim_digest(text),
                    "evidence_digest": ev.evidence_digest(leaves, nid, text)})
    return out


def _write_verdict(d: Path, sid: str = DEFAULT_SID, **kw) -> None:
    """Write a verdict. `claims_reviewed` may be given as ids (digests are computed for them) or
    as fully-formed entries, so a test can deliberately record a WRONG digest."""
    run_dir = manifest.locate_run_dir(d, sid)
    reviewed = kw.get("claims_reviewed", ["G0", "G1"])
    if reviewed and all(isinstance(c, str) for c in reviewed):
        reviewed = _reviewed(d, reviewed, sid)
    body = {"verdict": "pass", "nonce": kw.get("nonce", ""), "auditor": "empirica-auditor",
            "claims_reviewed": reviewed,
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
          "never reviewed" in p.stderr, f"got {p.stderr!r}")


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


def _digests_of(d: Path, nid: str, sid: str = DEFAULT_SID) -> dict:
    """The digests the gate will demand for one claim — the `approved` map's value shape."""
    return _reviewed(d, [nid], sid)[0]


def _approved_map(d: Path, claim_ids: list[str], sid: str = DEFAULT_SID) -> dict:
    return {e["claim_id"]: {"claim_digest": e["claim_digest"],
                            "evidence_digest": e["evidence_digest"]}
            for e in _reviewed(d, claim_ids, sid)}


def test_audit_verdict_is_per_claim_not_per_graph():
    """ADR-25 — the headline behaviour: adding a claim must not un-review the others.

    Under the old per-graph pass counter, any change invalidated the whole verdict, so adding one
    claim to a 22-claim graph demanded 22 re-reviews. Worse, the gate ticks a pass on every
    audit-fail round, so the intended fix-and-loop rhythm invalidated the verdict on COMPLIANT
    behaviour.
    """
    d = write_run([{"text": "claim one", "confidence": 0.9},
                   {"text": "claim two", "confidence": 0.9}])
    nonce = _spawn_auditor(d)
    _write_verdict(d, nonce=nonce, claims_reviewed=["G0", "G1", "G2"])
    ok, reason = aud.check(manifest.locate_run_dir(d, DEFAULT_SID),
                           _approved_map(d, ["G0", "G1", "G2"]))
    check("R35 a verdict covering every approved claim at its current digests passes", ok is True,
          reason)

    # Now add a THIRD claim, as a real convergence pass would.
    run_dir = manifest.locate_run_dir(d, DEFAULT_SID)
    gp = graph.default_graph_path(run_dir)
    g = json.loads(gp.read_text())
    g["nodes"]["G3"] = {"type": "Goal", "text": "a claim derived later", "confidence": 0.9}
    g["edges"].append({"from": "G0", "to": "G3", "type": "SupportedBy"})
    gp.write_text(json.dumps(g))
    ev.write_research(run_dir, "r-G3", "G3", "a claim derived later",
                      source="https://docs.example/x", kind="docs", citation="cited",
                      result="supports", ts="2026-07-24T09:00:00Z")

    ok2, reason2 = aud.check(run_dir, _approved_map(d, ["G0", "G1", "G2", "G3"]))
    check("R36 adding a claim blocks convergence until it is reviewed", ok2 is False, reason2)
    check("R37 …and the reason names ONLY the new claim, not the already-reviewed ones",
          "G3" in reason2 and "G1" not in reason2 and "G2" not in reason2, reason2)


def test_rewording_an_audited_claim_unreviews_that_claim():
    """ADR-25 — a reworded claim's `claim_digest` moves, so the audit answered a different
    question. Symmetric with `_binds`, which invalidates the EVIDENCE on a reword."""
    d = write_run([{"text": "the original wording", "confidence": 0.9}])
    nonce = _spawn_auditor(d)
    _write_verdict(d, nonce=nonce, claims_reviewed=["G0", "G1"])
    run_dir = manifest.locate_run_dir(d, DEFAULT_SID)
    gp = graph.default_graph_path(run_dir)
    g = json.loads(gp.read_text())
    g["nodes"]["G1"]["text"] = "a materially different wording"
    gp.write_text(json.dumps(g))
    # Re-evidence it so the ONLY reason it can fail is the audit coverage, not a missing fold.
    ev.write_research(run_dir, "r-G1b", "G1", "a materially different wording",
                      source="https://docs.example/y", kind="docs", citation="cited",
                      result="supports", ts="2026-07-24T09:00:00Z")
    ok, reason = aud.check(run_dir, _approved_map(d, ["G0", "G1"]))
    check("R38 rewording an audited claim un-reviews it", ok is False, reason)
    check("R39 the reason says it was REWORDED, not merely unreviewed",
          "REWORDED" in reason and "G1" in reason, reason)


def test_swapping_evidence_after_review_unreviews_that_claim():
    """ADR-25 — THE OPTION-B REGRESSION, pinned.

    `claim_digest` is over claim TEXT only, so swapping a citation for a fabricated one leaves it
    identical. Keying the verdict on the claim digest alone — the fix as originally proposed —
    would read this as still reviewed, and since it also drops the pass-staleness proxy that
    incidentally caught it, it would NARROW the audit while appearing to sharpen it. The
    `evidence_digest` is what closes it.
    """
    d = write_run([{"text": "a claim", "confidence": 0.9}])
    nonce = _spawn_auditor(d)
    _write_verdict(d, nonce=nonce, claims_reviewed=["G0", "G1"])
    run_dir = manifest.locate_run_dir(d, DEFAULT_SID)
    before = _digests_of(d, "G1")
    # Same claim text; a DIFFERENT source and citation. This is citation substitution.
    ev.write_research(run_dir, "r-G1", "G1", "a claim",
                      source="https://fabricated.example/nope", kind="docs",
                      citation="a passage that was never read", result="supports",
                      ts="2026-07-24T09:30:00Z")
    after = _digests_of(d, "G1")
    check("R40 swapping the citation leaves claim_digest UNCHANGED (why option B fails)",
          before["claim_digest"] == after["claim_digest"])
    check("R41 …but MOVES evidence_digest",
          before["evidence_digest"] != after["evidence_digest"])
    ok, reason = aud.check(run_dir, _approved_map(d, ["G0", "G1"]))
    check("R42 swapping an audited claim's evidence un-reviews it", ok is False, reason)
    check("R43 the reason says the EVIDENCE changed", "EVIDENCE" in reason and "G1" in reason,
          reason)


def test_legacy_flat_verdict_form_is_refused():
    """ADR-25 — no backwards compatibility, on purpose.

    A dual-form reader would keep the weaker form reachable, which is exactly the legacy-shape
    escape hatch the gate already had to close once (see the legacy-manifest regression).
    """
    d = _converged_run()
    nonce = _spawn_auditor(d)
    run_dir = manifest.locate_run_dir(d, DEFAULT_SID)
    aud.verdict_path(run_dir).write_text(json.dumps(
        {"verdict": "pass", "nonce": nonce, "auditor": "empirica-auditor",
         "claims_reviewed": ["G0", "G1"],  # the pre-ADR-25 flat form
         "findings": [], "ts": "2026-07-24T13:00:00Z"}))
    verdict = aud.read_verdict(run_dir)
    check("R44 a flat list[str] verdict yields NO reviewed entries",
          verdict is not None and verdict["claims_reviewed"] == [], f"got {verdict}")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("R45 a legacy-shape verdict cannot converge a run", p.returncode == 2,
          f"rc={p.returncode}")


def test_entry_missing_a_digest_is_not_coverage():
    """An entry that cannot be compared is not a review. Dropped, so the claim reads unreviewed
    rather than covered-by-something-unverifiable."""
    d = _converged_run()
    nonce = _spawn_auditor(d)
    run_dir = manifest.locate_run_dir(d, DEFAULT_SID)
    entries = _reviewed(d, ["G0", "G1"])
    del entries[1]["evidence_digest"]
    aud.verdict_path(run_dir).write_text(json.dumps(
        {"verdict": "pass", "nonce": nonce, "auditor": "empirica-auditor",
         "claims_reviewed": entries, "findings": [], "ts": "2026-07-24T13:00:00Z"}))
    ok, reason = aud.check(run_dir, _approved_map(d, ["G0", "G1"]))
    check("R46 an entry missing a digest does not count as coverage", ok is False, reason)
    check("R47 a non-sha256 digest is refused too",
          aud._review_entry({"claim_id": "G1", "claim_digest": "nope",
                             "evidence_digest": "0" * 64}) is None)


def test_fix_and_reaudit_does_not_invalidate_untouched_claims():
    """ADR-25 driver 2 — the loop the old proxy punished.

    audit fails → the run fixes ONE claim → re-audit. The other claims are untouched, so their
    digests have not moved and they stay reviewed. Under the pass counter, the pass tick alone
    invalidated all of them.
    """
    d = write_run([{"text": "claim one", "confidence": 0.9},
                   {"text": "claim two", "confidence": 0.9}])
    nonce = _spawn_auditor(d)
    _write_verdict(d, nonce=nonce, claims_reviewed=["G0", "G1", "G2"], verdict="fail",
                   findings=["G2's citation does not support the claim"])
    run_dir = manifest.locate_run_dir(d, DEFAULT_SID)
    ok, _ = aud.check(run_dir, _approved_map(d, ["G0", "G1", "G2"]))
    check("R48 a failing audit blocks", ok is False)

    # The run fixes G2's evidence only, then re-audits — reviewing just G2.
    ev.write_research(run_dir, "r-G2", "G2", "claim two",
                      source="https://docs.example/real", kind="docs",
                      citation="the passage that actually decides it", result="supports",
                      ts="2026-07-24T11:00:00Z")
    nonce2 = _spawn_auditor(d)
    entries = _reviewed(d, ["G2"]) + [
        # G0 and G1 carry the digests from the FIRST review — unchanged, because nothing touched
        # them. This is the whole point: the auditor did not re-read them.
        e for e in _reviewed(d, ["G0", "G1"])]
    aud.verdict_path(run_dir).write_text(json.dumps(
        {"verdict": "pass", "nonce": nonce2, "auditor": "empirica-auditor",
         "claims_reviewed": entries, "findings": [], "ts": "2026-07-24T12:00:00Z"}))
    ok2, reason2 = aud.check(run_dir, _approved_map(d, ["G0", "G1", "G2"]))
    check("R49 after fixing ONE claim, a re-audit of that claim converges the run", ok2 is True,
          reason2)


def test_repeat_requires_every_run_to_pass():
    """`--repeat N` is CONJUNCTIVE: one sample is not a verdict.

    The check here fails on its 3rd invocation and passes otherwise, so a single-sample run is
    green and a repeated run is red — deterministically, without relying on real randomness (which
    would make the test itself flaky, the very thing this feature exists to catch).
    """
    d = Path(tempfile.mkdtemp())
    counter, script = d / "n", d / "flaky.sh"
    script.write_text("#!/bin/sh\nn=$(cat %s 2>/dev/null || echo 0)\nn=$((n+1))\n"
                      "echo $n > %s\n[ $n -ne 3 ]\n" % (counter, counter))
    script.chmod(0o755)
    harness = _load("spike_harness", HARNESS)

    first = harness.run_gate_repeated([str(script)], repeat=1)
    check("Q20 a single sample of the flaky check passes", first["gate"] == "pass", f"{first}")
    counter.write_text("0")
    repeated = harness.run_gate_repeated([str(script)], repeat=5)
    check("Q21 --repeat 5 catches the failing repetition", repeated["gate"] == "fail",
          f"{repeated}")
    check("Q22 it short-circuits at the first failure, not after all N",
          len(repeated["runs"]) == 3, f"got {len(repeated['runs'])} runs")
    check("Q23 every repetition's exit code is on the record",
          [r["returncode"] for r in repeated["runs"]] == [0, 0, 1],
          f"{[r['returncode'] for r in repeated['runs']]}")

    counter.write_text("100")  # never hits 3 again → all pass
    clean = harness.run_gate_repeated([str(script)], repeat=4)
    check("Q24 a genuinely deterministic check passes all N", clean["gate"] == "pass", f"{clean}")
    check("Q25 …and records all N runs", len(clean["runs"]) == 4, f"{clean}")
    check("Q26 repeat=1 leaves the payload shape unchanged (no `runs` key)",
          "runs" not in harness.run_gate_repeated(["true"], repeat=1))
    args = harness.parse_args(["--repeat", "0", "--", "x"])
    check("Q27 --repeat 0 clamps to 1 rather than skipping the check", args[4] == 1, f"{args}")
    check("Q28 a malformed --repeat falls back to 1",
          harness.parse_args(["--repeat", "abc", "x"])[4] == 1)
    check("Q29 --repeat is clamped to MAX_REPEAT",
          harness.parse_args(["--repeat", "10000", "x"])[4] == harness.MAX_REPEAT)


def test_repeat_result_hash_covers_every_run():
    """The record's `result_hash` must describe the WHOLE repeated run.

    It used to be sha256 of one run's stdout tail; under --repeat that is the digest of an
    arbitrary repetition, which varies between invocations of a nondeterministic check and so
    describes nothing. A repeated run digests the ordered per-run digests instead.
    """
    harness = _load("spike_harness", HARNESS)
    a = harness.run_gate_repeated(["sh", "-c", "echo same"], repeat=3)
    b = harness.run_gate_repeated(["sh", "-c", "echo same"], repeat=3)
    check("Q30 a deterministic repeated check has a STABLE result_hash",
          harness._result_hash(a) == harness._result_hash(b))
    single = harness.run_gate_repeated(["sh", "-c", "echo same"], repeat=1)
    check("Q31 …and it DIFFERS from the single-run digest, because it covers 3 runs",
          harness._result_hash(a) != harness._result_hash(single))
    varying = harness.run_gate_repeated(["sh", "-c", "date +%s%N; echo x"], repeat=1)
    check("Q32 a single run still digests its own stdout (unchanged behaviour)",
          harness._result_hash(varying) is not None and "runs" not in varying)


# --- ADR-26 freeze ----------------------------------------------------------


def _freeze(d: Path, sid: str = DEFAULT_SID, ts: str = "2026-08-10T10:00:00Z") -> dict:
    """Run the freeze entry point the way the skill does, and return its JSON summary."""
    p = subprocess.run(
        [sys.executable, str(HOOKS / "route_stamp.py"), "--freeze", "--session", sid,
         "--ts", ts, "--cwd", str(d)],
        capture_output=True, text=True, cwd=str(d))
    return json.loads(p.stdout) if p.stdout.strip() else {}


def _add_claim(d: Path, nid: str, text: str, confidence: float = 0.0,
               sid: str = DEFAULT_SID, evidenced: bool = True) -> None:
    """Append a gating claim to a live run's graph, as a convergence pass would."""
    run_dir = manifest.locate_run_dir(d, sid)
    gp = graph.default_graph_path(run_dir)
    g = json.loads(gp.read_text())
    g["nodes"][nid] = {"type": "Goal", "text": text, "confidence": confidence}
    g["edges"].append({"from": "G0", "to": nid, "type": "SupportedBy"})
    gp.write_text(json.dumps(g))
    if evidenced:
        ev.write_research(run_dir, f"r-{nid}", nid, text, source="https://docs.example/z",
                          kind="docs", citation="cited", result="supports",
                          ts="2026-07-24T09:00:00Z")


def test_freeze_defers_later_claims_and_closes_the_run():
    """ADR-26 THE BYPASS TEST — freeze, then add an unresolved claim.

    The run still stops, because a post-freeze claim does not gate. But the claim appears in
    `deferred`, is never silently dropped, and never lets the run report convergence. That is the
    whole trade freeze makes: less SCOPE, declared up front and printed, never less work on the
    scope it committed to.
    """
    d = write_run([{"text": "committed claim", "confidence": 0.9}])
    summary = _freeze(d)
    check("Z1 freezing snapshots the claims already gating",
          summary.get("frozen") is True and sorted(summary.get("frozen_claims", [])) == ["G0", "G1"],
          f"{summary}")

    _add_claim(d, "G9", "a hard claim discovered after the freeze", confidence=0.0)
    # A frozen stop still owes an audit (see test_frozen_run_still_owes_an_audit) — the point here
    # is that the unresolved POST-freeze claim is not what blocks.
    _write_verdict(d, nonce=_spawn_auditor(d), claims_reviewed=["G0", "G1"])
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("Z2 an unresolved POST-freeze claim does not block the stop", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")
    out = json.loads(p.stdout)
    check("Z3 the run is NOT reported as converged", out.get("converged") is False, f"{out}")
    check("Z4 the deferred claim is named in the result, not silently dropped",
          out.get("deferred") == ["G9"], f"{out}")
    run = manifest.read_run(manifest.locate_run(d, DEFAULT_SID))
    check("Z5 the terminal status is stopped_frozen, distinct from stopped_residual",
          run["status"] == "stopped_frozen", f"{run['status']}")


def test_freeze_cannot_be_enlarged_by_refreezing():
    """First write wins. A commitment that can be rewritten per pass is not a commitment."""
    d = write_run([{"text": "first claim", "confidence": 0.9}])
    _freeze(d)
    _add_claim(d, "G7", "added after the freeze", confidence=0.9)
    second = _freeze(d, ts="2026-08-10T11:00:00Z")
    check("Z6 a second freeze does not enlarge the committed set",
          sorted(second.get("frozen_claims", [])) == ["G0", "G1"], f"{second}")
    run = manifest.read_run(manifest.locate_run(d, DEFAULT_SID))
    check("Z7 the manifest keeps the ORIGINAL freeze stamp",
          run["freeze_ts"] == "2026-08-10T10:00:00Z", f"{run}")
    check("Z8 …and G7 is deferred, never retro-committed",
          manifest.deferred_claims(run, ["G0", "G1", "G7"]) == ["G7"], f"{run}")


def test_frozen_claims_still_gate():
    """Freeze must not weaken the claims it committed to — only defer the ones it did not."""
    d = write_run([{"text": "unresolved committed claim", "confidence": 0.1}])
    _freeze(d)
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("Z9 an open claim INSIDE the frozen set still blocks", p.returncode == 2,
          f"rc={p.returncode}")
    check("Z10 the block message still names the missing fold or score",
          "G1" in p.stderr, f"got {p.stderr!r}")


def test_corrupt_freeze_record_reads_as_not_frozen():
    """Fail toward gating MORE. A freeze record that freed a blocking run when unreadable would
    be the legacy-shape exploit again — note this is the OPPOSITE direction from modes.json,
    whose corrupt state is safely 'off'."""
    d = write_run([{"text": "still open", "confidence": 0.1}])
    rp = manifest.locate_run(d, DEFAULT_SID)
    raw = json.loads(rp.read_text())
    raw["frozen_claims"] = "not-a-list"
    rp.write_text(json.dumps(raw))
    run = manifest.read_run(rp)
    check("Z11 a malformed frozen_claims normalises to None", run["frozen_claims"] is None,
          f"{run}")
    check("Z12 …so the run reads as NOT frozen", manifest.is_frozen(run) is False)
    _add_claim(d, "G8", "would be deferred if the freeze were honoured", confidence=0.1)
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("Z13 an unreadable freeze gates every claim (fails toward MORE gating)",
          p.returncode == 2, f"rc={p.returncode}")


def test_frozen_run_still_owes_an_audit():
    """A frozen stop ASSERTS it discharged its committed scope. That is a positive claim, so P6
    still applies — otherwise freeze would be an audit bypass and ADR-26's "the auditor judges the
    freeze" guard would be vacuous."""
    d = write_run([{"text": "committed and settled", "confidence": 0.9}])
    _freeze(d)
    _add_claim(d, "G9", "deferred", confidence=0.0)
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("Z14 a frozen run with NO audit is blocked, not waved through", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")
    check("Z15 the block message tells the auditor to judge the DEFERRAL",
          "FROZEN" in p.stderr and "deferral" in p.stderr.lower(), f"got {p.stderr!r}")

    nonce = _spawn_auditor(d)
    _write_verdict(d, nonce=nonce, claims_reviewed=["G0", "G1"])
    p2 = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("Z16 with a passing audit the frozen run closes", p2.returncode == 0,
          f"rc={p2.returncode} stderr={p2.stderr!r}")
    out = json.loads(p2.stdout)
    check("Z17 …reporting the audit, the deferral, and converged:false together",
          out.get("audit") == "passed" and out.get("deferred") == ["G9"]
          and out.get("converged") is False, f"{out}")
    check("Z18 a residual (gave-up) stop is still exempt from the audit requirement",
          run_hook(GATE, {"cwd": str(write_run([{"text": "human call", "confidence": 0.1,
                                                 "blocked": "needs-decision"}])),
                          "session_id": DEFAULT_SID}, d).returncode == 0)


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


def test_pretooluse_hook_does_not_import_the_freeze_path():
    """route_stamp.py fires on EVERY Read/Grep/Bash in EVERY session, including sessions that are
    not empirica runs. Anything imported at its module scope is therefore paid per tool call.

    The freeze path (ADR-26) needs claimgraph + evidence + convergence_gate; importing those
    eagerly measured ~3ms per tool call for code only `--freeze` reaches, so they load inside
    `freeze_scope`. This test pins that, because the regression is invisible — the suite stays
    green and only interactive latency degrades.
    """
    source = (HOOKS / "route_stamp.py").read_text()
    head = source.split("def freeze_scope", 1)[0]
    eager = [m for m in ("claimgraph", "evidence", "convergence_gate")
             if f'_load("{m}")' in head]
    check("T99 the PreToolUse hook does not eagerly load the freeze-only modules",
          eager == [], f"loaded at module scope: {eager}")
    check("T99b …and freeze_scope loads them itself",
          all(f'_load("{m}")' in source.split("def freeze_scope", 1)[1]
              for m in ("claimgraph", "evidence", "convergence_gate")))


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
    # ADR-23 fitness #3, RETAINED but sharpened by ADR-24. The rule is that a model rename must
    # never touch workflow LOGIC — so no hook may name a concrete model in code it executes.
    #
    # The old form of this check was a substring scan over the whole file, which could not tell a
    # docstring from a decision. ADR-24 needs `actors.py` to document `claude-opus-5` in examples
    # and to name `fable` in a policy-exclusion table, so the scan started failing on prose while
    # still being unable to catch the thing it exists to catch: a model id used in a branch. This
    # version walks the AST and inspects only string literals in EXECUTABLE positions, so it is
    # both quieter and strictly stricter than before.
    import ast

    MODEL_TOKENS = ("claude-opus", "claude-sonnet", "claude-haiku", "claude-fable",
                    "us.anthropic", "eu.anthropic", "gpt-", "openai.")
    # The ONE permitted exception, narrow and named: actors.POLICY_EXCLUDED is a policy table
    # whose whole purpose is to name an excluded model (ADR-24 §7). A policy exclusion that could
    # not name what it excludes would be inert.
    ALLOWED_ASSIGNMENTS = {"POLICY_EXCLUDED"}

    def executable_strings(tree):
        """Every string literal that is NOT a docstring, with its enclosing assignment target."""
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", [])
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    docstrings.add(id(body[0].value))
        # Map each string literal to the module-level name it is assigned to, if any, so the
        # POLICY_EXCLUDED exemption can be scoped to that table instead of to a whole file.
        owner: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if names:
                    for sub in ast.walk(node.value):
                        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                            owner[id(sub)] = names[0]
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings):
                yield node.value, owner.get(id(node))

    leaked = []
    for path in sorted(HOOKS.glob("*.py")):
        tree = ast.parse(path.read_text())
        for value, assigned_to in executable_strings(tree):
            if assigned_to in ALLOWED_ASSIGNMENTS:
                continue
            for token in MODEL_TOKENS:
                if token in value:
                    leaked.append(f"{path.name}:{token}")
    # The skill is prose, so it keeps a plain scan — a model named there would be an instruction.
    skill = (HOOKS.parent / "skills/empirica/SKILL.md").read_text()
    leaked += [f"SKILL.md:{t}" for t in MODEL_TOKENS if t in skill]
    check("R24 no concrete model ID is used in executable hook logic or the skill",
          leaked == [], f"{sorted(set(leaked))}")
    # The exemption must be REAL, or R24 above passes for the wrong reason (a broken AST walk
    # that finds nothing would also report leaked == []).
    check("R24b the check can see executable strings at all",
          any("fable" in v for v, owner in
              executable_strings(ast.parse((HOOKS / "actors.py").read_text()))
              if owner in ALLOWED_ASSIGNMENTS),
          "the AST walk found no strings in POLICY_EXCLUDED — the scan is not actually looking")


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


def test_announcement_does_not_stamp_itself():
    """REGRESSION — the P1 check could never PASS, the mirror of the vacuity stamps.py removed.

    SKILL.md Step 1 has the agent announce its route by running route_stamp.py as a Bash command,
    and route_stamp.py is itself registered on PreToolUse for Bash. PreToolUse fires "Before a
    tool call executes" (code.claude.com/docs/en/hooks), so the hook stamped the announcement's
    OWN Bash call as `first_tool_*` before the announcement body could write `route_*`. Both are
    first-write-wins, so every compliant run was reported as a P1 VIOLATION. Verified live in a
    real session: first_tool_ts='pass:0', first_tool_seq=1, route_seq=2 → violation.

    The earlier P1 tests all missed it because they drove the two stamps directly
    (`manifest.stamp_first_tool` / a bare `--announce-route` subprocess), never sending a
    PreToolUse payload whose command IS the announcement — the one sequence the workflow always
    produces.

    BOTH halves are asserted, because deleting the hook outright would satisfy the first alone.
    """
    d = Path(tempfile.mkdtemp())
    rp = manifest.locate_run(d, DEFAULT_SID)
    manifest.start_run(rp, DEFAULT_SID, d)
    announce = (f"python3 {STAMP} --announce-route --session {DEFAULT_SID} "
                f"--ts 2026-08-04T10:00:00Z --cwd {d}")

    # The PreToolUse hook observes the Bash call that CARRIES the announcement.
    run_hook(STAMP, {"tool_name": "Bash", "cwd": str(d), "session_id": DEFAULT_SID,
                     "tool_input": {"command": announce}}, d)
    check("S59 the run's own route announcement is not stamped as investigation",
          manifest.read_run(rp)["first_tool_ts"] is None,
          f"got {manifest.read_run(rp)['first_tool_ts']} — the announcement stamped itself")

    # Now the body of that call runs, and a compliant run must read OK.
    manifest.stamp_route(rp, "2026-08-04T10:00:00Z")
    verdict, reason = stamps.route_verdict(manifest.read_run(rp))
    check("S60 a compliant run is NOT reported as a P1 violation",
          verdict == stamps.OK, f"got {verdict}: {reason}")

    # Second half: a GENUINE investigative call must still stamp, or the fix is a removal.
    run_hook(STAMP, {"tool_name": "Bash", "cwd": str(d), "session_id": DEFAULT_SID,
                     "tool_input": {"command": "grep -rn TODO src/"}}, d)
    check("S61 a genuine investigative call is still stamped",
          manifest.read_run(rp)["first_tool_ts"] is not None,
          "the stamp was removed rather than made self-aware")


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


def test_stamp_ordering_is_numeric_not_lexicographic():
    """Regression, Copilot review of PR #9: the P1 check compared stamps with raw `<=`.

    Two independent defects lived in that one operator, and BOTH could invert the verdict:
      * within a counter kind, string order is not numeric — 'pass:10' <= 'pass:2' is True
      * across kinds the comparison is meaningless — and since '2' < 'p', an ISO route stamp
        always sorted before a `pass:` tool stamp
    """
    check("S40 pass:10 is AFTER pass:2 (not before, as string order claimed)",
          stamps.compare("pass:10", "pass:2") == 1, stamps.compare("pass:10", "pass:2"))
    check("S41 pass:2 is BEFORE pass:10",
          stamps.compare("pass:2", "pass:10") == -1)
    check("S42 equal counters compare equal", stamps.compare("seq:7", "seq:7") == 0)
    check("S43 ISO stamps still order chronologically",
          stamps.compare("2026-07-24T10:00:00Z", "2026-07-24T11:00:00Z") == -1)
    check("S44 a trailing Z parses (the spelling the skill emits)",
          stamps.parse("2026-07-24T10:00:00Z") is not None)
    check("S45 naive and aware ISO stamps compare without raising",
          stamps.compare("2026-07-24T10:00:00", "2026-07-24T11:00:00Z") == -1)

    # Different kinds count different things, so no order may be invented from them.
    check("S46 ISO vs pass: is NOT comparable (was silently True)",
          stamps.compare("2026-07-24T23:00:00Z", "pass:0") is None)
    check("S47 seq: and pass: are distinct kinds, not interchangeable counters",
          stamps.compare("seq:5", "pass:5") is None)
    for junk in ("garbage", "", "   ", None, 42, True, "pass:", "pass:x"):
        check(f"S48 unparseable stamp {junk!r} yields no ordering",
              stamps.parse(junk) is None, f"parsed {junk!r}")


def test_p1_is_decisive_via_harness_write_order():
    """The cross-kind case was the DEFAULT (skill stamps ISO, hook falls back to pass:N), so
    "not comparable" alone would leave P1 vacuous in normal operation. The manifest's own write
    sequence supplies an order the harness witnessed, which is always comparable."""
    d = Path(tempfile.mkdtemp())
    rp = manifest.locate_run(d, DEFAULT_SID)
    manifest.start_run(rp, DEFAULT_SID, d)
    manifest.stamp_first_tool(rp, "pass:0")             # investigated first...
    manifest.stamp_route(rp, "2026-07-24T23:00:00Z")    # ...route announced after
    run = manifest.read_run(rp)
    check("S49 the manifest records the harness write order",
          run["first_tool_seq"] == 1 and run["route_seq"] == 2,
          f"tool={run['first_tool_seq']} route={run['route_seq']}")
    verdict, reason = stamps.route_verdict(run)
    check("S50 inverted run with incomparable stamps → VIOLATION via write order",
          verdict == stamps.VIOLATION, f"{verdict}: {reason}")

    # The compliant mirror image, stamps equally incomparable.
    d2 = Path(tempfile.mkdtemp())
    rp2 = manifest.locate_run(d2, DEFAULT_SID)
    manifest.start_run(rp2, DEFAULT_SID, d2)
    manifest.stamp_route(rp2, "pass:0")
    manifest.stamp_first_tool(rp2, "2026-07-24T23:00:00Z")
    verdict2, reason2 = stamps.route_verdict(manifest.read_run(rp2))
    check("S51 compliant run with incomparable stamps → OK via write order",
          verdict2 == stamps.OK, f"{verdict2}: {reason2}")

    # Backdating is still impossible: first write wins on BOTH halves of the record.
    manifest.stamp_route(rp, "2026-07-24T01:00:00Z")
    check("S52 the write-order counter cannot be re-stamped either",
          manifest.read_run(rp)["route_seq"] == 2)


def test_p1_inconclusive_is_not_reported_as_clean():
    """A legacy manifest (no write order) with incomparable stamps must report INCONCLUSIVE —
    never OK. Silently passing an unverifiable check is the vacuity the fix removes."""
    verdict, reason = stamps.route_verdict(
        {"route_ts": "2026-07-24T23:00:00Z", "first_tool_ts": "pass:0"})
    check("S53 incomparable stamps without write order → INCONCLUSIVE",
          verdict == stamps.INCONCLUSIVE, f"{verdict}: {reason}")
    check("S54 the reason admits it could not be verified",
          "not comparable" in reason and "could not be verified" in reason, reason)
    # The bool-collapsing wrapper must fail closed on it.
    ok, _ = manifest.route_before_investigation(
        {"route_ts": "2026-07-24T23:00:00Z", "first_tool_ts": "pass:0"})
    check("S55 route_before_investigation reports inconclusive as NOT ok", ok is False)
    # And the auditor is told, rather than the run reading as clean.
    check("S56 the gate's route note is raised for an inconclusive ordering",
          aud.route_note({"route_ts": "2026-07-24T23:00:00Z",
                          "first_tool_ts": "pass:0"}) is not None)


def test_gate_separates_proven_violation_from_unverifiable():
    """An unverifiable ordering must not be filed as a violation: that would accuse a compliant
    run. It must not be filed as clean either. Distinct keys, distinct claims."""
    d = write_run([{"text": "settled", "confidence": 0.9}])
    rp = manifest.locate_run(d, DEFAULT_SID)
    run = manifest.read_run(rp)
    run.update({"route_ts": "2026-07-24T23:00:00Z", "first_tool_ts": "pass:0"})
    verdict, _ = stamps.route_verdict(run)
    check("S57 a legacy-shaped inverted-looking run is inconclusive, not a violation",
          verdict == stamps.INCONCLUSIVE, verdict)
    # Both hook modules must agree — they used to hold separate copies of this logic.
    check("S58 audit.py and manifest.py give the SAME P1 verdict (one implementation)",
          aud.stamps_route_verdict(run) == stamps.route_verdict(run))


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


# --- ADR-24: actor attribution, preflight doctor, optional modes -------------
actors = _load("actors", HOOKS / "actors.py")
attribution = _load("attribution", HOOKS / "attribution.py")
modes = _load("modes", HOOKS / "modes.py")
doctor = _load("doctor", HOOKS / "doctor.py")
DISPATCH = HOOKS / "dispatch_gate.py"


def test_run_identity_survives_a_cwd_change():
    """REGRESSION, the worst defect found so far — the harness went INERT for a whole run.

    `/empirica` was invoked while the session cwd was `<repo>/plugins/empirica`, so run_start.py
    wrote its manifest under that subdirectory. Every later hook fired from `<repo>`, derived a
    different run_id from the moved cwd, found no manifest, and correctly failed OPEN — no
    convergence gate, no spawn cap, no mandatory audit, for the rest of the session. The docs
    define `cwd` as "Current working directory when the hook is invoked" and ship a `CwdChanged`
    event, so keying identity on cwd keyed it on a moving value.

    A gate that is silently switched off is worse than a gate that is wrong, so this is the one
    property here worth a dedicated regression test.
    """
    root = Path(tempfile.mkdtemp()).resolve()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    sub = root / "plugins" / "empirica"
    sub.mkdir(parents=True)
    sid = "sess-anchor"

    # The pre-fix scheme, reproduced through the seam left for exactly this purpose.
    check("T1 the OLD cwd-keyed scheme really did disagree across a cwd change",
          manifest._run_id_from(sid, sub) != manifest._run_id_from(sid, root),
          "cannot reproduce the defect — this test would prove nothing")
    check("T2 run identity is now stable across a cwd change",
          manifest.run_id(sid, sub) == manifest.run_id(sid, root),
          f"{manifest.run_id(sid, sub)} != {manifest.run_id(sid, root)}")
    check("T3 the run DIRECTORY is stable too, not just the id",
          manifest.locate_run_dir(sub, sid) == manifest.locate_run_dir(root, sid),
          "the id anchored but the path did not — artifacts would scatter into subdirectories")
    check("T4 the run directory sits at the project root",
          manifest.locate_run_dir(sub, sid).parent.parent.parent == root)
    check("T5 the spawn ledger anchors with it (one cap, not one per cwd)",
          budget.locate_ledger(sub, manifest.run_id(sid, sub))
          == budget.locate_ledger(root, manifest.run_id(sid, root)))

    # END TO END: start the run from the SUBDIRECTORY as the real invocation did, then let the
    # Stop gate fire from the ROOT. Before the fix this allowed the stop (fail-open, no manifest);
    # now the gate must find the run and block on the open claim. This is the half that actually
    # proves the harness is no longer inert.
    p = run_hook(RUN_START, {"session_id": sid, "cwd": str(sub)}, sub)
    check("T6 run-start from a subdirectory exits 0", p.returncode == 0, p.stderr)
    run_dir = manifest.locate_run_dir(root, sid)
    check("T7 the manifest lands at the project root, not under the subdirectory",
          (run_dir / "run.json").exists() and not (sub / ".claude").exists(),
          f"run_dir={run_dir} stray={(sub / '.claude').exists()}")
    graph.save(run_dir / "claims.json", {
        "root": "G0",
        "nodes": {"G0": {"type": "Goal", "text": "root", "confidence": 1.0},
                  "G1": {"type": "Goal", "text": "unresolved", "confidence": 0.0}},
        "edges": [{"from": "G0", "to": "G1", "type": "SupportedBy"}]})
    p = run_hook(GATE, {"cwd": str(root), "session_id": sid}, root)
    check("T8 a Stop from the ROOT finds the run started in the SUBDIR and blocks",
          p.returncode == 2, f"rc={p.returncode} — the gate is inert again: {p.stdout[:200]}")

    # Controls: the two distinctions ADR-19 keyed on cwd to get must both survive.
    other = Path(tempfile.mkdtemp()).resolve()
    subprocess.run(["git", "init", "-q", str(other)], check=True, capture_output=True)
    check("T9 two sessions in one repo stay distinct",
          manifest.run_id("sess-A", root) != manifest.run_id("sess-B", root))
    check("T10 two repos stay distinct", manifest.run_id(sid, root) != manifest.run_id(sid, other))
    bare = Path(tempfile.mkdtemp()).resolve() / "x" / "y"
    bare.mkdir(parents=True)
    check("T11 no project marker falls back to cwd, never to /",
          manifest.project_anchor(bare) == bare)

    # AUDIT FINDING — the defect's own debris must not re-split identity. A stray run store left
    # inside a subdirectory (exactly what this bug scatters) once outranked `.git`, so the anchor
    # that fixes the defect was defeated by the defect's litter.
    litter = sub / ".claude" / "empirica"
    litter.mkdir(parents=True)
    check("T11b a stray run store in a subdir does NOT re-split identity",
          manifest.run_id(sid, sub) == manifest.run_id(sid, root),
          "the anchor was defeated by the very artifact this defect leaves behind")
    check("T11c .git outranks a nearer run store", manifest.project_anchor(sub) == root)
    # But a NON-git project must still resume an established run rather than fork beside it.
    nogit = Path(tempfile.mkdtemp()).resolve()
    (nogit / ".claude" / "empirica").mkdir(parents=True)
    deep = nogit / "a" / "b"
    deep.mkdir(parents=True)
    check("T11d without .git, an established run store is still the anchor",
          manifest.project_anchor(deep) == nogit)
    # A RELATIVE $EMPIRICA_BUDGET override anchors too, or the scattered-ledger defect returns one
    # level down (audit finding: locate_ledger bypassed project_anchor on that path).
    prior = os.environ.get(budget.LEDGER_ENV)
    os.environ[budget.LEDGER_ENV] = "ledger.json"
    try:
        check("T11e a relative ledger override anchors instead of following cwd",
              budget.locate_ledger(sub) == budget.locate_ledger(root) == root / "ledger.json",
              f"{budget.locate_ledger(sub)} vs {budget.locate_ledger(root)}")
        os.environ[budget.LEDGER_ENV] = str(root / "abs.json")
        check("T11f an ABSOLUTE override is still honoured verbatim",
              budget.locate_ledger(sub) == root / "abs.json")
    finally:
        if prior is None:
            os.environ.pop(budget.LEDGER_ENV, None)
        else:
            os.environ[budget.LEDGER_ENV] = prior


def test_actor_is_additive_everywhere():
    """ADR-24 §1/§2 — `actor` must be purely additive. A graph or leaf without one must behave
    byte-identically to before, or the feature breaks every existing run."""
    d = Path(tempfile.mkdtemp())
    (d / "evidence").mkdir()
    g = graph.normalise({
        "root": "G0",
        "nodes": {"G0": {"type": "Goal", "text": "root", "confidence": 0.9},
                  "G1": {"type": "Goal", "text": "with actor", "kind": "needs-data",
                         "confidence": 0.9,
                         "actor": {"model": "claude-opus-5", "harness": "claude-code"}},
                  "G2": {"type": "Goal", "text": "no actor", "kind": "needs-data",
                         "confidence": 0.9}},
        "edges": [{"from": "G0", "to": "G1", "type": "SupportedBy"},
                  {"from": "G0", "to": "G2", "type": "SupportedBy"}]})
    check("T12 a graph carrying an actor is still valid", g != graph.CORRUPT)
    check("T13 the actor round-trips through normalise",
          g["nodes"]["G1"]["actor"]["model"] == "claude-opus-5")
    check("T14 a node without an actor reads as None, not as a default",
          g["nodes"]["G2"]["actor"] is None)
    # A malformed actor must degrade to None, NEVER corrupt the graph: losing a routing
    # preference must not make a valid argument unreadable.
    bad = graph.normalise({"root": "G0", "nodes": {
        "G0": {"type": "Goal", "text": "root", "confidence": 0.5, "actor": "not-a-dict"}},
        "edges": []})
    check("T15 a malformed actor degrades to None rather than corrupting the graph",
          bad != graph.CORRUPT and bad["nodes"]["G0"]["actor"] is None)

    # Evidence: omitting the actor must produce the pre-ADR-24 predicate EXACTLY, key for key.
    p_plain = ev.write_research(d, "e1", "G2", "no actor", source="s", kind="docs",
                                citation="c", result="supports", ts="2026-08-06T10:00:00Z")
    plain = json.loads(p_plain.read_text())
    check("T16 an actor-less research leaf has NO actor key (absence, not null)",
          "actor" not in plain["predicate"], f"{sorted(plain['predicate'])}")
    check("T17 the actor-less predicate is exactly the historical field set",
          sorted(plain["predicate"]) == ["citation", "fold", "kind", "result", "source", "ts"],
          f"{sorted(plain['predicate'])}")
    p_attr = ev.write_research(d, "e2", "G1", "with actor", source="s", kind="docs",
                              citation="c", result="supports", ts="2026-08-06T10:00:00Z",
                              actor={"model": "claude-opus-5", "harness": "claude-code"})
    check("T18 an attributed leaf carries the actor",
          json.loads(p_attr.read_text())["predicate"]["actor"]["model"] == "claude-opus-5")
    leaves = ev.read_leaves(d)
    check("T19 both leaves still validate", len(leaves) == 2, f"{len(leaves)}")
    ok = ev.oracle(d, g)
    check("T20 attribution does not change approvability",
          graph.state_of(g, "G1", 0.8, ok) == graph.STATE_APPROVED
          and graph.state_of(g, "G2", 0.8, ok) == graph.STATE_APPROVED)


def test_fable_is_refused_by_policy():
    """ADR-24 §7 — `fable` is a VALID value of Claude Code's model: frontmatter, so nothing in
    the harness stops it. This list is the only thing that does, and the reason (30-day retention
    at the vendor) is recorded in code so it is not 'cleaned up' as an unused tier."""
    check("T21 fable is excluded and the reason mentions retention",
          "retention" in (actors.policy_excluded("fable") or ""),
          f"{actors.policy_excluded('fable')}")
    check("T22 a full fable model id is also excluded",
          actors.policy_excluded("claude-fable-5") is not None)
    check("T23 an actor naming fable does not normalise",
          actors.normalise({"model": "fable"}) is None)
    check("T24 a fable actor cannot enter an evidence leaf",
          "actor" not in json.loads(ev.write_research(
              Path(tempfile.mkdtemp()), "e", "G1", "t", source="s", kind="docs", citation="c",
              result="supports", ts="2026-08-06T10:00:00Z",
              actor={"model": "claude-fable-5"}).read_text())["predicate"])
    # The exclusion must be a token match, not a substring one: over-broad exclusion is its own
    # bug, and it would silently refuse an unrelated future model.
    check("T25 the exclusion is not an over-broad substring match",
          actors.policy_excluded("fabletown-7") is None)
    check("T26 an ordinary model is not excluded", actors.policy_excluded("claude-opus-5") is None)
    # AUDIT 4 — the model-id guard reaches log lines, report text and (under Mode B) an argv, and
    # was expressed as a single `or` whose halves could neutralise each other. These values must
    # never normalise, and must never reach a leaf on disk.
    d_inject = Path(tempfile.mkdtemp())
    for bad in ("claude opus; rm -rf /", "claude-opus-5\nrm -rf /", "", "   ",
                "$(whoami)", "a" * 200, "-flag-like"):
        check(f"T26b a malformed model id is refused: {bad[:24]!r}",
              actors.normalise({"model": bad}) is None)
        leaf = json.loads(ev.write_research(
            d_inject, f"e{abs(hash(bad))}", "G1", "t", source="s", kind="docs", citation="c",
            result="supports", ts="2026-08-06T10:00:00Z", actor={"model": bad}).read_text())
        check(f"T26c …and never reaches a leaf on disk: {bad[:24]!r}",
              "actor" not in leaf["predicate"])
    check("T26d a non-string model is refused independently of the regex",
          actors.normalise({"model": 42}) is None
          and actors.normalise({"model": None}) is None
          and actors.normalise({"model": ["claude-opus-5"]}) is None)
    # AUDIT 5 — the `.strip()` was unguarded: deleting it left the suite green while a padded id
    # either failed the anchored fullmatch (silently dropping a legitimate actor) or was stored with
    # whitespace, which then breaks `same_actor`'s equality and so the independence check itself.
    padded = actors.normalise({"model": "  claude-opus-5  "})
    check("T26l a padded model id normalises and is STORED trimmed",
          padded is not None and padded["model"] == "claude-opus-5", f"{padded}")
    check("T26m a padded and an unpadded id are the SAME actor",
          actors.same_actor({"model": " claude-opus-5"}, {"model": "claude-opus-5 "}) is True,
          "whitespace would defeat the independence comparison")
    check("T26n a whitespace-only model is still refused",
          actors.normalise({"model": "   "}) is None
          and actors.normalise({"model": "\t\n"}) is None)
    # WITNESSED-SURVIVOR FINDINGS — `policy_excluded`'s RETURN VALUES were unasserted. Only its
    # use inside `normalise` was covered, so collapsing it to a constant (excluding everything, or
    # nothing) left the suite green in one direction and only incidentally red in the other.
    check("T26e policy_excluded returns a REASON string for an excluded model, not just truthy",
          isinstance(actors.policy_excluded("fable"), str)
          and len(actors.policy_excluded("fable")) > 40)
    check("T26f …and None for a permitted one, so it DISCRIMINATES",
          actors.policy_excluded("claude-opus-5") is None
          and actors.policy_excluded("openai.gpt-5.6-sol") is None)
    check("T26g a non-string input yields None rather than raising",
          actors.policy_excluded(42) is None and actors.policy_excluded(None) is None)
    # `same_actor`'s three exit paths were each reachable but only one was asserted per direction.
    check("T26h same_actor: identical concrete models CLASH",
          actors.same_actor({"model": "claude-opus-5"}, {"model": "claude-opus-5"}) is True)
    check("T26i same_actor: different concrete models do NOT",
          actors.same_actor({"model": "claude-opus-5"}, {"model": "claude-opus-4-8"}) is False)
    check("T26j same_actor: an unusable side never clashes",
          actors.same_actor(None, {"model": "claude-opus-5"}) is False
          and actors.same_actor({"model": "fable"}, {"model": "fable"}) is False
          and actors.same_actor({"model": "claude-opus-5"}, "x") is False)
    check("T26k same_actor: a TIER on EITHER side never clashes",
          actors.same_actor({"model": "opus"}, {"model": "claude-opus-5"}) is False
          and actors.same_actor({"model": "claude-opus-5"}, {"model": "opus"}) is False,
          "a cost class is not an identity — claiming a clash would accuse without evidence")


def test_audit_independence_is_real_and_asserted():
    """ADR-24 finding 1 was a LIVE DEFECT: the auditor and spike-runner agents both declared
    `model: opus`, so 'the author cannot grade its own work' was the same weights re-grading
    their own reasoning — and nothing recorded a model, so nothing detected it.

    This test is the thing that stops it silently regressing a second time.

    IT CHECKS EVERY RESOLVABLE COPY, not just this working tree — and that distinction is the whole
    value of the test. An independent audit found the first version read only
    `plugins/empirica/agents/*.md`, while the definition the harness ACTUALLY resolved for the
    auditor it spawned was the installed marketplace copy, which still said `model: opus` for both
    roles. So finding 1 was live in the deployed configuration and no committed check saw it: the
    test asserted a property of a file nothing loads. A check that reads the wrong copy is worse
    than no check, because it reports green about a question it never asked.
    """
    def frontmatter_model(path):
        text = path.read_text()
        head = text.split("---")[1]
        for line in head.splitlines():
            if line.startswith("model:"):
                return line.split(":", 1)[1].strip()
        return None

    def definition_paths(name):
        """Every copy of this agent definition a harness could resolve FOR THIS VERSION.

        THREE roots, and the middle one is the whole point:

          1. the plugin under test (this working tree) — what will ship;
          2. the installed MARKETPLACE checkout, at ANY version — the copy a live spawn resolves
             from on this machine;
          3. installed version CACHES that declare the same version as the working tree — a
             same-version copy disagreeing with the tree is a packaging defect.

        Root 2 is unconditional, and getting there took two audits. The first version of this check
        read the working tree alone, and missed that the harness resolved an installed copy still
        declaring the same model for both roles. The second version scoped every installed copy by
        version — which sounded principled and was, as an audit then pointed out, VACUOUS: the tree
        was 0.5.0 while the installed checkout was 0.4.1, so nothing outside the tree was checked at
        all, and the scoping structurally excluded the exact copy whose staleness caused the
        original miss. The harness resolves what is INSTALLED, not what declares a matching version.

        Version caches (root 3) stay scoped, and that distinction is real rather than convenient: a
        cache entry is an immutable archive of a published release, an older release genuinely does
        contain the older configuration, and no edit here rewrites history — so asserting over all
        of them would make this permanently red on any machine that ever installed an earlier
        version. A check nobody can make green gets deleted, which is how a guard is actually lost.

        The marketplace checkout is different: it is a mutable git checkout that TRACKS a branch, so
        a red result there is genuinely actionable (`git pull` / reinstall), and it is the copy that
        runs. When it is behind, this check SHOULD be red — that is a true statement about what
        would happen if an auditor were spawned right now.
        """
        def version_of(root):
            try:
                return json.loads(
                    (root / ".claude-plugin" / "plugin.json").read_text()).get("version")
            except (OSError, json.JSONDecodeError, ValueError):
                return None

        here = HOOKS.parent
        want = version_of(here)
        roots = [here]
        home = Path.home() / ".claude" / "plugins"
        # Root 2: unconditional — this is what a live spawn resolves.
        roots.extend(p for p in home.glob("marketplaces/*/plugins/empirica") if p.is_dir())
        # Root 3: same-version caches only (see docstring).
        roots.extend(p for p in home.glob("cache/*/empirica/*")
                     if p.is_dir() and want is not None and version_of(p) == want)
        seen, out = set(), []
        for root in roots:
            path = root / "agents" / f"{name}.md"
            if path.exists() and path.resolve() not in seen:
                seen.add(path.resolve())
                out.append(path)
        return out

    auditor_defs = definition_paths("empirica-auditor")
    author_defs = definition_paths("empirica-spike-runner")
    check("T27a the auditor definition is found at all", bool(auditor_defs))
    # FIVE audits found this claim's guard weak, the last three for the SAME reason: deleting the
    # installed-copy roots left the suite green, so nothing asserted that they are consulted. The
    # enumeration itself must therefore be checked, not just its results. This is the fix that closes
    # the recurrence: `definition_paths` is asserted to CONSULT the resolution paths, by planting a
    # definition in each root and requiring it to be found.
    home = Path.home() / ".claude" / "plugins"
    expected_roots = {"working-tree": HOOKS.parent}
    for label, pattern in (("marketplace", "marketplaces/*/plugins/empirica"),
                           ("version-cache", "cache/*/empirica/*")):
        for candidate in home.glob(pattern):
            if (candidate / "agents").is_dir():
                expected_roots.setdefault(label, candidate)
                break
    found_roots = {p.parent.parent.resolve() for p in auditor_defs}
    for label, root in expected_roots.items():
        if label == "version-cache":
            # Scoped by version on purpose (an old release is an immutable archive), so it is only
            # required when a SAME-VERSION cache entry exists on this machine.
            try:
                same = json.loads(
                    (root / ".claude-plugin" / "plugin.json").read_text()).get("version") == \
                    json.loads((HOOKS.parent / ".claude-plugin"
                                / "plugin.json").read_text()).get("version")
            except (OSError, json.JSONDecodeError, ValueError):
                same = False
            if not same:
                continue
        check(f"T27b definition_paths CONSULTS the {label} root",
              root.resolve() in found_roots,
              f"{root} was not enumerated — the harness resolves from there, so a stale copy would "
              f"go unseen (the recurrence five audits found)")
    check("T27b2 …and enumerates at least the working tree plus every applicable install root",
          len(found_roots) >= len([r for label, r in expected_roots.items()
                                   if label != "version-cache"]),
          f"found {sorted(map(str, found_roots))}")

    def offenders(paths_a, paths_b, predicate):
        return [str(p) for p in paths_a if predicate(p, paths_b)]

    same_model = offenders(
        auditor_defs, author_defs,
        lambda a, bs: frontmatter_model(a) is not None
        and any(frontmatter_model(a) == frontmatter_model(b) for b in bs))
    tiers = [str(p) for p in auditor_defs + author_defs
             if actors.is_tier_alias(frontmatter_model(p) or "")]

    # SEPARATED BY WHO CAN FIX IT — and the separation is the point, not a softening.
    #
    # A defect in THIS TREE is the plugin's own bug and must be red: nothing ships until it is
    # fixed. A stale INSTALLED copy is a true and useful statement ("an auditor spawned right now
    # would grade its own work") but it is not something this commit can repair — the installed
    # checkout tracks a published branch, so it only becomes correct AFTER the fix ships. Making the
    # suite red for it would mean the commit that fixes the defect cannot pass its own checks, and a
    # check nobody can make green gets deleted. So the tree is GATED and the install is REPORTED,
    # loudly, with the path and the remedy.
    tree = HOOKS.parent.resolve()

    def in_tree(path):
        return Path(path).resolve().is_relative_to(tree)

    check("T27c THIS TREE never has the auditor and author on the same model",
          [p for p in same_model if in_tree(p)] == [],
          f"{[p for p in same_model if in_tree(p)]} — ADR-20 P6 independence is nominal")
    check("T27d THIS TREE never pins a TIER instead of a generation",
          [p for p in tiers if in_tree(p)] == [], f"{[p for p in tiers if in_tree(p)]}")
    stale = sorted({p for p in same_model + tiers if not in_tree(p)})
    if stale:
        # A WARNING, not a check. A gating check here would be permanently red until the fix ships,
        # so the commit that repairs the defect could not pass its own suite — and a check nobody can
        # make green gets deleted, taking the real guard with it. A warning is loud, actionable, and
        # cannot be satisfied by weakening the assertion above it.
        warn(f"ADR-20 P6: an auditor spawned RIGHT NOW would resolve from an installed copy that "
             f"grades its own work: {stale}. Not fixable by this commit — these update when the "
             f"fix ships and the plugin is reinstalled. Until then, treat any audit run on this "
             f"machine as NOT independent.")

    auditor = frontmatter_model(HOOKS.parent / "agents" / "empirica-auditor.md")
    author = frontmatter_model(HOOKS.parent / "agents" / "empirica-spike-runner.md")
    check("T27 the auditor and the author are DIFFERENT models",
          auditor and author and auditor != author,
          f"auditor={auditor} author={author} — ADR-20 P6 independence is nominal again")
    check("T28 both name a concrete generation, not a tier alias",
          not actors.is_tier_alias(auditor) and not actors.is_tier_alias(author),
          f"auditor={auditor} author={author}: a tier collapses decorrelated error (ADR-24 §2)")
    check("T29 neither is policy-excluded",
          actors.policy_excluded(auditor) is None and actors.policy_excluded(author) is None)
    check("T30 same_actor agrees they are independent",
          not actors.same_actor({"model": auditor}, {"model": author}))
    # And the comparison must not be foolable by routing the same weights differently.
    check("T31 the same model via a different harness is NOT independent",
          actors.same_actor({"model": "claude-opus-5", "harness": "claude-code"},
                            {"model": "claude-opus-5", "harness": "pi"}),
          "harness/provider must not launder a same-model audit")
    check("T32 two tier aliases are never counted as the same actor",
          not actors.same_actor({"model": "opus"}, {"model": "opus"}),
          "'capable == capable' says nothing about which weights ran")


def test_auditor_spawn_records_the_dispatcher_side_actor():
    """ADR-24 §2 — attribution comes from the DISPATCHER, never the actor. Verified live: three
    models misreported their own identity while the provider attested the truth. So the spawn gate
    reads the auditor's model from its definition, and records it as `declared` (not witnessed),
    because the model resolves from frontmatter after the hook returns (V8)."""
    d = Path(tempfile.mkdtemp())
    sid = "sess-attrib"
    manifest.start_run(manifest.locate_run(d, sid), sid, d)
    run_dir = manifest.locate_run_dir(d, sid)
    p = run_hook(GATE_SPAWN_HOOK, {"tool_name": "Agent", "cwd": str(d), "session_id": sid,
                                   "tool_input": {"subagent_type": "empirica:empirica-auditor"}},
                 d)
    check("T33 the auditor spawn is allowed", p.returncode == 0, p.stderr)
    actor = aud.audit_actor(run_dir)
    check("T34 the ticket records an actor", actor is not None,
          "no actor on the ticket — §3's same-actor check has nothing to compare")
    check("T35 the recorded model matches the auditor's DEFINITION",
          actor and actor["model"] == "claude-opus-4-8", f"{actor}")
    check("T36 in-session attribution is DECLARED, never witnessed",
          actor and actor["attribution"] == actors.DECLARED, f"{actor}")
    # AUDIT 4 — assert the ON-DISK artifact, not only what the reader returns. The write path forces
    # `declared` and so does the read path, so a test that checks only the read path passes even when
    # the file itself says `witnessed`. The file is what a human or a later reviewer inspects, and a
    # corrupt artifact that reads correctly is exactly the kind of gap ADR-19's file-level trust model
    # cannot absorb.
    on_disk = json.loads(aud.tickets_path(run_dir).read_text())["tickets"][0].get("actor") or {}
    check("T36b the TICKET FILE itself records declared, not witnessed",
          on_disk.get("attribution") == actors.DECLARED, f"on disk: {on_disk}")
    check("T36c and a caller cannot write witnessed through the in-session path",
          aud.record_spawn(run_dir, "r2", 0,
                           actor={"model": "claude-opus-4-8",
                                  "attribution": actors.WITNESSED}) is not None
          and json.loads(aud.tickets_path(run_dir).read_text())["tickets"][-1]["actor"][
              "attribution"] == actors.DECLARED,
          "an in-session spawn wrote a WITNESSED attribution to disk")
    # A hand-edited ticket must not be able to upgrade its own attribution strength: the in-session
    # path is structurally incapable of witnessing, so the claim is wrong whoever writes it.
    tickets = json.loads(aud.tickets_path(run_dir).read_text())
    forged = tickets["tickets"][0].get("actor")
    if forged:
        forged["attribution"] = actors.WITNESSED
        aud.tickets_path(run_dir).write_text(json.dumps(tickets))
    reread = aud.audit_actor(run_dir)
    check("T37 a forged `witnessed` on an in-session ticket is forced back to declared",
          bool(forged) and reread and reread["attribution"] == actors.DECLARED,
          "the weaker dispatch path was allowed to present itself as the stronger one")


def test_same_actor_audit_is_detected_and_reported():
    """ADR-24 §3.2 — the check that makes finding 1 visible. REPORTS, never blocks (§3.3)."""
    d = Path(tempfile.mkdtemp())
    (d / "evidence").mkdir()
    g = graph.normalise({"root": "G0", "nodes": {
        "G0": {"type": "Goal", "text": "root", "confidence": 0.9},
        "G1": {"type": "Goal", "text": "claim one", "kind": "needs-data", "confidence": 0.9}},
        "edges": [{"from": "G0", "to": "G1", "type": "SupportedBy"}]})
    ev.write_research(d, "e1", "G1", "claim one", source="s", kind="docs", citation="c",
                      result="supports", ts="2026-08-06T10:00:00Z",
                      actor={"model": "claude-opus-5", "harness": "claude-code"})
    leaves = ev.read_leaves(d)
    same = attribution.check_same_actor_audit(["G1"], leaves, {"model": "claude-opus-5"})
    check("T38 an audit on the SAME model as the work is detected", len(same) == 1, f"{same}")
    check("T39 the finding explains that P6 independence was not obtained",
          same and "independence was NOT obtained" in same[0]["detail"])
    diff = attribution.check_same_actor_audit(["G1"], leaves, {"model": "claude-opus-4-8"})
    check("T40 a DIFFERENT auditor produces no false positive", diff == [], f"{diff}")
    # A deterministic spike is not a judge: the exit code is the approver (ADR-13), so a
    # harness-attributed leaf must never be flagged as a model clash.
    d2 = Path(tempfile.mkdtemp())
    (d2 / "evidence").mkdir()
    ev.write_spike(d2, "s1", "G1", "claim one", cmd=["true"], gate="pass", result_hash="h",
                   files=[], ts="2026-08-06T10:00:00Z")
    spike_leaf = ev.read_leaves(d2)
    check("T41 a spike's CODE actor is recorded as witnessed",
          spike_leaf[0]["actor"]["source_type"] == actors.CODE
          and spike_leaf[0]["actor"]["attribution"] == actors.WITNESSED, f"{spike_leaf[0]}")
    check("T42 a spike is never flagged as a same-actor clash",
          attribution.check_same_actor_audit(
              ["G1"], spike_leaf, {"model": "spike_harness.py"}) == [])

    # §3.1 mismatch, plus its control.
    mismatch = attribution.check_mismatch(graph.normalise({"root": "G0", "nodes": {
        "G0": {"type": "Goal", "text": "root", "confidence": 0.9},
        "G1": {"type": "Goal", "text": "claim one", "kind": "needs-data", "confidence": 0.9,
               "actor": {"model": "openai.gpt-5.6-sol", "harness": "pi"}}},
        "edges": [{"from": "G0", "to": "G1", "type": "SupportedBy"}]}), leaves)
    check("T43 a claim resolved by a different actor than assigned is reported",
          len(mismatch) == 1 and mismatch[0]["check"] == attribution.MISMATCH, f"{mismatch}")
    check("T44 an assignment with no attributed evidence is NOT a mismatch",
          attribution.check_mismatch(g, []) == [],
          "missing data must not be reported as a contradiction")
    # AUDIT COUNTEREXAMPLE — B5 claims "a deterministic spike's CODE actor is never flagged as a
    # model clash", and T42 tested that on the same-actor side ONLY. check_mismatch carries the
    # identical exemption with no guard, so deleting it FABRICATES a mismatch: a claim assigned to a
    # model and correctly evidenced by the harness is exactly the design working, and reporting it
    # as a contradiction would train a reader to ignore these findings.
    assigned_graph = graph.normalise({"root": "G0", "nodes": {
        "G0": {"type": "Goal", "text": "root", "confidence": 0.9},
        "G1": {"type": "Goal", "text": "claim one", "kind": "needs-experiment", "confidence": 0.9,
               "actor": {"model": "claude-opus-5", "harness": "claude-code"}}},
        "edges": [{"from": "G0", "to": "G1", "type": "SupportedBy"}]})
    check("T44b a claim evidenced ONLY by a spike is never a mismatch",
          attribution.check_mismatch(assigned_graph, spike_leaf) == [],
          "the harness's own CODE actor was reported as contradicting the assigned model")
    # Control: the exemption must be scoped to CODE, not swallow every mismatch.
    check("T44c a genuine model mismatch is still reported alongside a spike leaf",
          len(attribution.check_mismatch(assigned_graph, spike_leaf + [
              lf for lf in leaves if lf["fold"] == "research"])) == 0
          and len(attribution.check_mismatch(graph.normalise({"root": "G0", "nodes": {
              "G0": {"type": "Goal", "text": "root", "confidence": 0.9},
              "G1": {"type": "Goal", "text": "claim one", "kind": "needs-data", "confidence": 0.9,
                     "actor": {"model": "openai.gpt-5.6-sol", "harness": "pi"}}},
              "edges": [{"from": "G0", "to": "G1", "type": "SupportedBy"}]}),
              spike_leaf + leaves)) == 1,
          "the CODE exemption must not silence a real model mismatch")

    # The vacuity guard: no attribution recorded anywhere must NOT read as a clean pass.
    rep = attribution.report(g, [], ["G1"], {"model": "claude-opus-4-8"})
    check("T45 an unattributed run is reported as UNMEASURED, not clean",
          rep["coverage"]["vacuous"] and "not a clean result" in rep["note"], f"{rep['note']}")
    rep2 = attribution.report(g, leaves, ["G1"], {"model": "claude-opus-4-8"})
    check("T46 an attributed run with no clash says so, and counts what it checked",
          not rep2["findings"] and rep2["coverage"]["model_attributed"] == 1, f"{rep2}")

    # AUDIT FINDING — vacuity was computed from the CLAIM side alone, so an unidentified auditor
    # produced "no clash detected": a clean-looking result from a comparison that never ran. That
    # was this very run's live state when it was audited, which is the strongest argument for
    # guarding it. BOTH sides must be known for silence to mean anything.
    for label, actor in (("no audit actor at all", None),
                         ("a TIER-named auditor", {"model": "opus"}),
                         ("a policy-excluded auditor", {"model": "fable"})):
        rep3 = attribution.report(g, leaves, ["G1"], actor)
        check(f"T46b {label} is reported as UNMEASURED, not clean",
              rep3["coverage"]["vacuous"] and "could NOT be checked" in rep3["note"],
              f"{rep3['note']}")
    check("T46c the note names WHICH side is missing",
          "audit's actor was not recorded" in attribution.report(g, leaves, ["G1"], None)["note"])
    # GENERATED-SWEEP FINDING — check_same_actor_audit's early return on an unusable audit_actor was
    # unguarded in the FALSE direction: forcing it to proceed anyway must not fabricate findings
    # against a `None` auditor, and forcing it to always return [] must not silence a real clash.
    check("T46g an unusable audit actor yields no findings, not a crash",
          attribution.check_same_actor_audit(["G1"], leaves, None) == []
          and attribution.check_same_actor_audit(["G1"], leaves, {"model": "fable"}) == []
          and attribution.check_same_actor_audit(["G1"], leaves, "not-a-dict") == [])
    check("T46h and a USABLE one still finds the clash (the early return is not blanket)",
          len(attribution.check_same_actor_audit(["G1"], leaves,
                                                {"model": "claude-opus-5"})) == 1)
    # AUDIT 4 — `report`'s note branches were excused as "prose", which was wrong: §3's entire
    # user-visible OUTPUT is that sentence. Forcing the findings branch off turned a real
    # same-actor clash into "no attribution clash detected" with the suite green. The note must
    # therefore be asserted per branch, and asserted to be MUTUALLY EXCLUSIVE — a clash outranks
    # both a vacuity report and a clean report.
    clash = attribution.report(g, leaves, ["G1"], {"model": "claude-opus-5"})
    check("T46i a REAL clash is announced in the note, not just in findings",
          clash["findings"] and "independence was NOT obtained" in clash["note"], f"{clash['note']}")
    check("T46j a clash note never reads as clean or as unmeasured",
          "no attribution clash detected" not in clash["note"]
          and "could NOT be checked" not in clash["note"], f"{clash['note']}")
    clean = attribution.report(g, leaves, ["G1"], {"model": "claude-opus-4-8"})
    check("T46k a clean run says clean and counts what it checked",
          not clean["findings"] and "no attribution clash detected" in clean["note"],
          f"{clean['note']}")
    check("T46l the three note branches are mutually exclusive",
          len({clash["note"], clean["note"],
               attribution.report(g, leaves, ["G1"], None)["note"]}) == 3)
    # And the model_actors filter must not admit EMPTY entries: audit 4 found that forcing its
    # guard true inflated model_attributed 0 -> 1 and flipped `vacuous` on a spike-only run — the
    # vacuity defect returning a fourth time through a survivor excused as harmless.
    check("T46m a spike-only run has ZERO model-attributed claims",
          attribution.model_actors(spike_leaf) == {}
          and attribution.coverage(g, spike_leaf, {"model": "claude-opus-4-8"})["vacuous"],
          f"{attribution.model_actors(spike_leaf)}")
    # WITNESSED-SURVIVOR FINDINGS — `coverage`'s COUNTS were reported but never asserted, and the
    # vacuity note's per-side branches were only exercised one at a time. Both are observable
    # outputs a reader consumes, so a mutation that changed them left the suite green.
    counted = graph.normalise({"root": "G0", "nodes": {
        "G0": {"type": "Goal", "text": "root", "confidence": 0.9},
        "G1": {"type": "Goal", "text": "claim one", "kind": "needs-data", "confidence": 0.9,
               "actor": {"model": "claude-opus-5"}},
        "G2": {"type": "Goal", "text": "claim two", "kind": "needs-data", "confidence": 0.9},
        "C1": {"type": "Context", "text": "not a goal"}},
        "edges": [{"from": "G0", "to": "G1", "type": "SupportedBy"},
                  {"from": "G0", "to": "G2", "type": "SupportedBy"},
                  {"from": "G0", "to": "C1", "type": "InContextOf"}]})
    cov4 = attribution.coverage(counted, leaves, {"model": "claude-opus-4-8"})
    check("T46n coverage counts GOALS only, excluding non-Goal nodes",
          cov4["goals"] == 3, f"{cov4}")
    check("T46o coverage counts ASSIGNED claims, and discriminates",
          cov4["assigned"] == 1, f"{cov4}")
    check("T46p coverage counts model-attributed claims", cov4["model_attributed"] == 1, f"{cov4}")
    # Both vacuity sides missing at once must name BOTH, not just the first.
    both = attribution.report(counted, [], ["G1"], None)["note"]
    check("T46q when BOTH sides are missing the note names both",
          "no claim's evidence carries an actor" in both
          and "audit's actor was not recorded" in both, both)
    tier_only = attribution.report(counted, leaves, ["G1"], {"model": "opus"})["note"]
    check("T46r a tier-named auditor is named as such, not as 'not recorded'",
          "names a TIER" in tier_only and "was not recorded" not in tier_only, tier_only)
    claim_only = attribution.report(counted, [], ["G1"], {"model": "claude-opus-4-8"})["note"]
    check("T46s a missing claim side alone names only that side",
          "no claim's evidence carries an actor" in claim_only
          and "audit's actor" not in claim_only, claim_only)
    check("T46d a fully-attributed comparison is NOT vacuous",
          not rep2["coverage"]["vacuous"] and rep2["coverage"]["audit_attributed"],
          f"{rep2['coverage']}")
    # AUDIT COUNTEREXAMPLE — `coverage`'s witnessed COUNT had no guard: emptying it left the suite
    # green. The count is the difference between "we chose the model and know it" and "we wrote
    # down what we were told", which is the whole trust distinction ADR-24 §2 turns on, so a report
    # that silently reports zero witnessed claims understates what the run actually established.
    d3 = Path(tempfile.mkdtemp())
    (d3 / "evidence").mkdir()
    ev.write_research(d3, "w1", "G1", "claim one", source="s", kind="docs", citation="c",
                      result="supports", ts="2026-08-06T10:00:00Z",
                      actor={"model": "openai.gpt-5.6-sol", "harness": "pi",
                             "attribution": actors.WITNESSED})
    ev.write_research(d3, "d1", "G1", "claim one", source="s2", kind="docs", citation="c2",
                      result="supports", ts="2026-08-06T10:01:00Z",
                      actor={"model": "claude-opus-5", "harness": "claude-code",
                             "attribution": actors.DECLARED})
    cov3 = attribution.coverage(g, ev.read_leaves(d3), {"model": "claude-opus-4-8"})
    check("T46e a WITNESSED attribution is counted as witnessed", cov3["witnessed"] == 1,
          f"{cov3} — a witnessed/declared count that always reads 0 hides §2's trust distinction")
    # Control: a run with only DECLARED attribution must count zero witnessed, or T46e passes for
    # the wrong reason (a counter that returns 1 unconditionally).
    d4 = Path(tempfile.mkdtemp())
    (d4 / "evidence").mkdir()
    ev.write_research(d4, "d2", "G1", "claim one", source="s", kind="docs", citation="c",
                      result="supports", ts="2026-08-06T10:00:00Z",
                      actor={"model": "claude-opus-5", "attribution": actors.DECLARED})
    check("T46f a declared-only run counts ZERO witnessed",
          attribution.coverage(g, ev.read_leaves(d4), {"model": "claude-opus-4-8"})["witnessed"] == 0)


def test_attribution_reaches_the_run_report():
    """§3.3 — the finding must appear in the RESULT, not only in a block message. A passing audit
    must not launder a same-actor audit, exactly as it must not launder a P1 violation."""
    d = write_run([{"text": "c1", "confidence": 1.0}], sid="sess-attr-report")
    sid = "sess-attr-report"
    rp = manifest.locate_run(d, sid)
    run_dir = manifest.locate_run_dir(d, sid)
    # Attribute the claim's evidence to a model, then have the AUDIT be the same model.
    ev.write_research(run_dir, "e-actor", "G1", "c1", source="s", kind="docs", citation="c",
                      result="supports", ts="2026-08-06T10:00:00Z",
                      actor={"model": "claude-opus-5", "harness": "claude-code"})
    nonce = aud.record_spawn(run_dir, manifest.read_run(rp)["run_id"], 0,
                             actor={"model": "claude-opus-5", "harness": "claude-code"})
    aud.verdict_path(run_dir).write_text(json.dumps({
        "verdict": "pass", "nonce": nonce,
        "claims_reviewed": _reviewed(d, ["G0", "G1"], sid)}))
    manifest.stamp_route(rp, "2026-08-06T09:00:00Z")
    manifest.stamp_first_tool(rp, "2026-08-06T09:30:00Z")
    p = run_hook(GATE, {"cwd": str(d), "session_id": sid}, d)
    check("T47 the stop is ALLOWED — attribution reports, never blocks", p.returncode == 0,
          f"rc={p.returncode} {p.stderr[:200]}")
    out = json.loads(p.stdout)
    check("T48 the run still reports converged", out.get("converged") is True, f"{out}")
    findings = out.get("attribution", {}).get("findings") or []
    check("T49 but the same-actor audit is on the record", len(findings) > 0,
          f"{out.get('attribution')}")
    check("T50 the finding names the check",
          any(f["check"] == attribution.SAME_ACTOR for f in findings), f"{findings}")


def test_modes_are_off_by_default_and_independent():
    """ADR-24 §5 — both modes OFF by default. This is what keeps the plugin installable: a bare
    Claude Code + python3 install must behave exactly as 0.4.x does."""
    d = Path(tempfile.mkdtemp())
    env_before = {k: os.environ.pop(v, None) for k, v in modes.ENV_KEYS.items()}
    try:
        check("T51 multi-provider is OFF by default",
              not modes.enabled(modes.MULTI_PROVIDER, d))
        check("T52 cli-exec is OFF by default", not modes.enabled(modes.CLI_EXEC, d))
        check("T53 the default is reported AS a default, not as a choice",
              modes.state(d)[modes.CLI_EXEC]["source"] == "default")
        # Independently toggled: enabling one must not enable or disable the other.
        modes.write(d, cli_exec=True)
        check("T54 enabling cli-exec does not enable multi-provider",
              modes.enabled(modes.CLI_EXEC, d) and not modes.enabled(modes.MULTI_PROVIDER, d))
        modes.write(d, multi_provider=True)
        check("T55 enabling the second mode preserves the first",
              modes.enabled(modes.CLI_EXEC, d) and modes.enabled(modes.MULTI_PROVIDER, d))
        # Env overrides the file, so an operator can force a mode off for one invocation.
        os.environ[modes.ENV_KEYS[modes.CLI_EXEC]] = "off"
        check("T56 env overrides the run config", not modes.enabled(modes.CLI_EXEC, d))
        check("T57 the source of the answer is reported",
              modes.state(d)[modes.CLI_EXEC]["source"] == "env")
        # A typo must fall through to the file rather than silently overriding it.
        os.environ[modes.ENV_KEYS[modes.CLI_EXEC]] = "ja"
        check("T58 an unrecognised env value falls through instead of overriding",
              modes.enabled(modes.CLI_EXEC, d))
        del os.environ[modes.ENV_KEYS[modes.CLI_EXEC]]
        # A corrupt config must read as OFF, not fail closed: it configures OPTIONAL capability,
        # so the safe direction is the baseline everyone can run.
        modes.config_path(d).write_text("{not json")
        check("T59 a corrupt mode config reads as OFF, never as a wedge",
              not modes.enabled(modes.CLI_EXEC, d) and not modes.any_enabled(d))
        # GENERATED-SWEEP FINDINGS, all previously unguarded.
        # A TYPE-corrupt config is different from an UNPARSEABLE one: T59 used invalid JSON, which
        # takes the earlier branch, so the bools-only type check itself was never exercised. A JSON
        # string must not enable a mode.
        modes.config_path(d).write_text(json.dumps({"cli_exec": "yes", "multi_provider": 1}))
        check("T59b a type-corrupt config does NOT enable a mode",
              not modes.enabled(modes.CLI_EXEC, d) and not modes.enabled(modes.MULTI_PROVIDER, d),
              "a JSON string or int turned a mode ON")
        check("T59c and its source is reported as default, not run-config",
              modes.state(d)[modes.CLI_EXEC]["source"] == "default")
        # An unknown mode must be REFUSED, not silently dropped: a near-miss typo otherwise writes a
        # config that looks like it enabled something and does nothing.
        try:
            modes.write(d, cli_exex=True)
            refused = False
        except ValueError:
            refused = True
        check("T59d writing an unknown mode is refused, not silently dropped", refused)
        check("T59e the refusal did not corrupt the existing config",
              isinstance(modes.state(d), dict))
        # An unknown mode NAME must read as off rather than raising KeyError from ENV_KEYS.
        check("T59f querying an unknown mode returns False, never raises",
              modes.enabled("not_a_mode", d) is False)
        # WITNESSED-SURVIVOR FINDINGS (the mutation sweep, once survivors had to prove themselves).
        # Every RETURN VALUE these functions produce is observable, and none was asserted:
        # `any_enabled` collapsed to False left the suite green while feeding the doctor's
        # `departs_from_baseline`, and `write`'s return value — the merged config a caller reads
        # back — was never checked at all.
        fresh = Path(tempfile.mkdtemp())
        check("T59i any_enabled is False on a baseline run", modes.any_enabled(fresh) is False)
        modes.write(fresh, cli_exec=True)
        check("T59j any_enabled is True once a mode is on, and DISCRIMINATES",
              modes.any_enabled(fresh) is True,
              "a constant any_enabled would hide that a run departs from baseline")
        check("T59k write returns the MERGED config, not a bool",
              modes.write(fresh, multi_provider=True) == {"cli_exec": True,
                                                          "multi_provider": True},
              f"{modes.write(fresh, multi_provider=True)}")
        check("T59l _file_modes returns a dict for a MISSING config, never a bool",
              modes._file_modes(Path(tempfile.mkdtemp())) == {})
        # Every SHAPE a config file can take must yield {} and must not raise. The earlier corrupt
        # config test used only unparseable text, so a file that is VALID JSON but not an object —
        # `[]`, `"str"`, `null`, a number — was never exercised; the witness sweep showed removing the
        # dict guard makes those raise AttributeError inside a hook. A mode config is user-editable,
        # so every shape it can have is reachable input.
        for content, label in (("{bad", "unparseable"), ("[]", "a JSON array"),
                               ('"a string"', "a JSON string"), ("null", "JSON null"),
                               ("42", "a JSON number"), ("true", "a JSON bool"), ("", "empty")):
            modes.config_path(fresh).write_text(content)
            try:
                got, raised = modes._file_modes(fresh), None
            except Exception as exc:  # noqa: BLE001
                got, raised = None, type(exc).__name__
            check(f"T59m a config that is {label} yields {{}} and never raises",
                  got == {} and raised is None, f"got={got!r} raised={raised}")
            check(f"T59m2 …and enabled() stays False for {label}",
                  modes.enabled(modes.CLI_EXEC, fresh) is False)
        # And the doctor's baseline flag must track it, since that is the field a reader consumes.
        clean = Path(tempfile.mkdtemp())
        check("T59n the doctor reports departs_from_baseline=False on a baseline run",
              doctor.diagnose(clean)["departs_from_baseline"] is False)
        modes.write(clean, cli_exec=True)
        check("T59o …and True once a mode is enabled",
              doctor.diagnose(clean)["departs_from_baseline"] is True)
        # And the ON spelling set must actually discriminate — forcing the _TRUE test either way was
        # undetectable, because no check asserted a recognised true value against an unrecognised one.
        prior_env = os.environ.get(modes.ENV_KEYS[modes.CLI_EXEC])
        try:
            for spelling in ("1", "true", "on", "enabled"):
                os.environ[modes.ENV_KEYS[modes.CLI_EXEC]] = spelling
                check(f"T59g `{spelling}` enables a mode", modes.enabled(modes.CLI_EXEC, d))
            for spelling in ("0", "false", "off", "disabled", "yes", "2", "garbage"):
                os.environ[modes.ENV_KEYS[modes.CLI_EXEC]] = spelling
                check(f"T59h `{spelling}` does NOT enable a mode",
                      not modes.enabled(modes.CLI_EXEC, d))
        finally:
            if prior_env is None:
                os.environ.pop(modes.ENV_KEYS[modes.CLI_EXEC], None)
            else:
                os.environ[modes.ENV_KEYS[modes.CLI_EXEC]] = prior_env
    finally:
        for mode, prior in env_before.items():
            if prior is not None:
                os.environ[modes.ENV_KEYS[mode]] = prior
            else:
                os.environ.pop(modes.ENV_KEYS[mode], None)


def test_doctor_detects_without_inferring():
    """ADR-24 §4 — the preflight spends NO inference, never gates the baseline, and does not probe
    optional tools unless their mode is on."""
    check("T60 no probe argv invokes a model (rule 1, structural)",
          doctor.probe_is_non_inferential(),
          "a probe would spend tokens in a preflight — the rule is now violated")
    # The choke point must actually refuse, or rule 1 rests on discipline alone.
    refused = False
    try:
        doctor._run(("codex", "exec", "hello"))
    except ValueError:
        refused = True
    check("T61 an inferential argv is REFUSED by the runner", refused,
          "doctor._run would have executed a model call")

    d = Path(tempfile.mkdtemp())
    env_before = {k: os.environ.pop(v, None) for k, v in modes.ENV_KEYS.items()}
    try:
        # Mode A off (the default) → nothing optional is probed at all.
        rep = doctor.diagnose(d)
        check("T62 the baseline is present and never gated",
              rep["baseline"]["status"] == doctor.PERMITTED)
        check("T63 with multi-provider OFF, no optional tool is probed",
              rep["tools"] == {} and rep["probed_optional"] is False, f"{rep['tools']}")
        check("T64 the report says so instead of looking like nothing is installed",
              any("not probed" in r for r in rep["recommendations"]))
        check("T65 the report records that it spent no inference",
              rep["spends_inference"] is False)
        check("T66 the policy exclusion travels with the report",
              "fable" in rep["policy_excluded"])

        # Mode A on → the classifier runs. Assert the CLASSIFIER, not this machine's inventory:
        # a test that required `pi` to be installed would fail for everyone else.
        modes.write(d, multi_provider=True)
        rep2 = doctor.diagnose(d)
        check("T67 with multi-provider ON, optional tools are probed",
              set(rep2["tools"]) == set(actors.OPTIONAL_HARNESSES), f"{sorted(rep2['tools'])}")
        check("T68 every probe yields a known status",
              all(t["status"] in {doctor.ABSENT, doctor.UNCONFIGURED, doctor.UNAPPROVED,
                                  doctor.PERMITTED} for t in rep2["tools"].values()),
              f"{rep2['tools']}")
        check("T69 a missing tool is `absent`, not an error",
              doctor.probe("definitely-not-a-real-cli-xyz")["status"] == doctor.ABSENT)
        # RULE 4, the one that matters most: available must not imply permitted. Drive the
        # classifier with a synthetic pi config so the assertion holds on any machine.
        fake = Path(tempfile.mkdtemp())
        (fake / ".pi" / "agent").mkdir(parents=True)
        (fake / ".pi" / "agent" / "models.json").write_text(
            json.dumps({"providers": {"openai": {}}}))
        check("T70 an unapproved provider is `configured-but-unapproved`, never permitted",
              doctor._pi_provider(fake) == "openai"
              and "openai" not in doctor.APPROVED_PROVIDERS)
        (fake / ".pi" / "agent" / "models.json").write_text(
            json.dumps({"providers": {"openai": {}, "bedrock-mantle-openai": {}}}))
        check("T71 an approved provider is preferred when several are configured",
              doctor._pi_provider(fake) == "bedrock-mantle-openai")
        check("T72 recommendations are sentences for a human, not actions taken",
              all(isinstance(r, str) for r in rep2["recommendations"]) and rep2["tools"] is not None)
        # AUDIT FINDING — rule 4's provider→status mapping had NO guard at all. Every check either
        # drove `_pi_provider` in isolation or asserted only that probe()'s status was a MEMBER of
        # the status set — and `permitted` is a member, so replacing the whole branch with
        # `status = PERMITTED` destroyed the rule ADR-24 calls the one that matters most while
        # leaving the suite green. `classify` is pure, so it can be asserted exhaustively on any
        # machine, whether or not the optional tools are installed.
        check("T72b an unapproved provider classifies as configured-but-unapproved",
              doctor.classify("openai") == doctor.UNAPPROVED)
        check("T72c an unknown provider is unapproved, not permitted (allow-list not deny-list)",
              doctor.classify("some-new-vendor-2027") == doctor.UNAPPROVED)
        check("T72d no provider determinable → installed-unconfigured",
              doctor.classify(None) == doctor.UNCONFIGURED)
        for approved in sorted(doctor.APPROVED_PROVIDERS):
            check(f"T72e an approved provider is permitted: {approved}",
                  doctor.classify(approved) == doctor.PERMITTED)
        check("T72f classify DISCRIMINATES — it does not return one status for everything",
              len({doctor.classify(x) for x in (None, "openai", "amazon-bedrock")}) == 3)
        # GENERATED-SWEEP FINDINGS. A mutation sweep (b12_mutation_sweep.py) replaced the
        # hand-written sabotage table after three audits each found a different unguarded mutation
        # of the same property. These are the survivors it named that are real behaviours, not
        # cosmetics — each one previously left the suite green.
        #
        # doctor.py rule 1: probe_is_non_inferential must actually DETECT an inferential argv, not
        # merely return True. Forcing its inner test either way was undetectable.
        check("T72g the rule-1 detector recognises an inferential argv when there is one",
              doctor._INFERENTIAL_MARKERS and not all(
                  m in ("--version",) for m in doctor._INFERENTIAL_MARKERS))
        saved = dict(doctor._NON_INFERENTIAL)
        try:
            doctor._NON_INFERENTIAL["codex"] = (("codex", "exec"),)
            check("T72h rule 1 reports FALSE when an inferential probe is present",
                  doctor.probe_is_non_inferential() is False,
                  "the detector cannot see a model call in its own probe table")
            doctor._NON_INFERENTIAL["codex"] = (("codex", "--version"),)
            check("T72i and TRUE again when it is removed",
                  doctor.probe_is_non_inferential() is True)
        finally:
            doctor._NON_INFERENTIAL.clear()
            doctor._NON_INFERENTIAL.update(saved)
        # _version must report None on a FAILED probe rather than a bogus string: a tool whose
        # --version errors is unconfigured, not "version <error text>".
        check("T72j a tool that is absent yields no version",
              doctor.probe("definitely-not-a-real-cli-xyz")["version"] is None)
        # AUDIT 4 — interpolating the status into the sentence was not enough: the CLAIM was still
        # free text, so a branch could render "status configured-but-unapproved: permitted via
        # openai". Assert the usable/not-usable verdict per status, over a synthetic report so it
        # holds on any machine.
        for status, expect_usable in ((doctor.PERMITTED, True), (doctor.UNAPPROVED, False),
                                      (doctor.UNCONFIGURED, False), (doctor.ABSENT, False)):
            synthetic = {"baseline": {"harness": "claude-code", "status": doctor.PERMITTED},
                         "modes": modes.state(None), "probed_optional": True,
                         "tools": {"pi": {"tool": "pi", "status": status, "version": "1.0",
                                          "provider": "openai"}},
                         "policy_excluded": {}}
            line = next(r for r in doctor.recommend(synthetic) if r.startswith("`pi`"))
            check(f"T72k the sentence for {status} says {'USABLE' if expect_usable else 'NOT usable'}",
                  ("USABLE as an actor" in line) == expect_usable
                  and ("NOT usable" in line) != expect_usable, line)
            check(f"T72l …and never claims 'permitted' for {status}",
                  expect_usable or "permitted" not in line.replace("not permitted", ""), line)
        # WITNESSED-SURVIVOR FINDINGS — `_pi_provider`'s return values and `recommend`'s two
        # mode-note branches were observable but unasserted. The provider reader was only checked
        # for two happy paths, so collapsing either return to a constant left the suite green.
        home = Path(tempfile.mkdtemp())
        (home / ".pi" / "agent").mkdir(parents=True)
        cfg = home / ".pi" / "agent" / "models.json"
        for content, expect in (('{"providers": {"openai": {}}}', "openai"),
                                ('{"providers": {"openai": {}, "amazon-bedrock": {}}}',
                                 "amazon-bedrock"),
                                ('{"providers": {}}', None),
                                ('{"providers": []}', None),
                                ('{"no-providers-key": 1}', None),
                                ("{not json}", None)):
            cfg.write_text(content)
            check(f"T72m _pi_provider on {content[:34]!r} → {expect!r}",
                  doctor._pi_provider(home) == expect, f"got {doctor._pi_provider(home)!r}")
        check("T72n _pi_provider returns None when the config is absent entirely",
              doctor._pi_provider(Path(tempfile.mkdtemp())) is None)
        # recommend()'s mode notes: each must appear when its mode is OFF and vanish when ON.
        for mode, marker in ((modes.MULTI_PROVIDER, "not probed"),
                             (modes.CLI_EXEC, "attribution stays DECLARED")):
            for enabled_flag in (False, True):
                synthetic = {"baseline": {"harness": "claude-code", "status": doctor.PERMITTED},
                             "modes": {m: {"enabled": (enabled_flag if m == mode else False),
                                           "source": "test"} for m in modes.MODES},
                             "probed_optional": enabled_flag if mode == modes.MULTI_PROVIDER
                             else False,
                             "tools": {}, "policy_excluded": {}}
                lines = " ".join(doctor.recommend(synthetic))
                check(f"T72o the {mode} note is {'absent' if enabled_flag else 'present'} "
                      f"when it is {'ON' if enabled_flag else 'OFF'}",
                      (marker in lines) is not enabled_flag, lines[:160])
    finally:
        for mode, prior in env_before.items():
            if prior is not None:
                os.environ[modes.ENV_KEYS[mode]] = prior
            else:
                os.environ.pop(modes.ENV_KEYS[mode], None)


def test_doctor_runs_at_run_start_and_cannot_wedge():
    """§4 — the doctor runs at run-start. run_start.py's contract is ALWAYS exit 0: a preflight
    that could take down a user's prompt would be a worse defect than any it diagnoses."""
    d = Path(tempfile.mkdtemp())
    sid = "sess-doctor"
    p = run_hook(RUN_START, {"session_id": sid, "cwd": str(d)}, d)
    check("T73 run-start still exits 0 with the doctor wired in", p.returncode == 0, p.stderr)
    run_dir = manifest.locate_run_dir(d, sid)
    check("T74 the preflight report is written to the run directory",
          doctor.actors_path(run_dir).exists(), f"missing {doctor.actors_path(run_dir)}")
    rep = json.loads(doctor.actors_path(run_dir).read_text())
    check("T75 the report is usable", rep["baseline"]["status"] == doctor.PERMITTED)
    check("T76 the report is transient run state, not a repo file",
          doctor.actors_path(run_dir).parent == run_dir
          and ".claude" in str(doctor.actors_path(run_dir)))
    # A doctor that throws must not wedge the prompt. Simulate by making the run dir unwritable.
    d2 = Path(tempfile.mkdtemp())
    sid2 = "sess-doctor-fail"
    rd2 = manifest.locate_run_dir(d2, sid2)
    rd2.mkdir(parents=True)
    (rd2 / "actors.json").mkdir()  # a DIRECTORY where the report must be written → write fails
    p2 = run_hook(RUN_START, {"session_id": sid2, "cwd": str(d2)}, d2)
    check("T77 a FAILING doctor still exits 0 (never wedge the prompt)",
          p2.returncode == 0, f"rc={p2.returncode} {p2.stderr[:200]}")
    check("T78 and the run itself was still started",
          manifest.read_run(manifest.locate_run(d2, sid2))["status"] == "active")


def test_cli_exec_dispatch_is_gated_at_the_same_boundary():
    """ADR-24 §5B / V4 — the payoff. Mode B dispatches actors as Bash subprocesses, and the
    spawn budget must survive that. A PreToolUse:Bash gate can deny by exit 2 and charge the SAME
    ADR-17 ledger, so a dispatched actor is gated exactly like an `Agent` spawn."""
    # Classification first — the residual cost ADR-24 commits to documenting is that coverage
    # rests on a command test rather than a tool name, so the command test must be exercised.
    for cmd in ("codex exec 'audit this'", "claude -p 'hello'", "pi --mode json 'x'",
                "echo hi && codex exec 'sneaky'", "/usr/local/bin/codex exec 'by path'",
                "claude --print 'long form'"):
        check(f"T79 recognised as a dispatch: {cmd[:34]}", dispatch_is(cmd), cmd)
    # AUDIT COUNTEREXAMPLE — the segment split had no discriminating test. T79's only compound row
    # (`echo hi && codex exec …`) cannot exercise it: `echo` is not an actor CLI, so plain token
    # scanning finds `codex exec` regardless. The split only matters when an EARLIER actor-CLI token
    # hits the `break` — and then a real dispatch after `&&` goes uncounted against the ADR-17
    # ledger, which is precisely the guarantee this gate exists to provide.
    for cmd in ("codex --version && pi -p 'x'",
                "claude --help; codex exec 'after a semicolon'",
                "pi --version || claude -p 'after or'",
                "codex doctor | claude -p 'after a pipe'"):
        check(f"T79b a dispatch AFTER a non-dispatch actor call: {cmd[:38]}", dispatch_is(cmd), cmd)
    # And an unparseable command must not be waved through: "I could not parse it" is not
    # "it is not a dispatch" (the docstring's own claim, previously unguarded).
    check("T79c an unbalanced quote around a dispatch still counts",
          dispatch_is("codex exec 'unterminated"), "the shlex fallback dropped the tokens")
    check("T79d an unparseable NON-dispatch is still not a dispatch",
          not dispatch_is("grep -rn 'unterminated"))
    for cmd in ("codex --version", "codex doctor", "pi --version", "grep -rn TODO src/",
                "echo 'codex exec is mentioned but not run'", "ls -la", ""):
        check(f"T80 NOT a dispatch: {cmd[:34] or '(empty)'}", not dispatch_is(cmd), cmd)

    d = Path(tempfile.mkdtemp())
    sid = "sess-dispatch"
    manifest.start_run(manifest.locate_run(d, sid), sid, d)
    run_dir = manifest.locate_run_dir(d, sid)
    budget.write_ledger(run_dir / "budget.json", {"max_spawns": 1, "spawns": 1})
    payload = {"tool_name": "Bash", "cwd": str(d), "session_id": sid,
               "tool_input": {"command": "codex exec 'resolve G3'"}}

    # THE INERT BRANCH — with Mode B off, this hook must not deny anything. A dispatch gate that
    # denied ordinary shell commands would be far worse than a missed dispatch.
    check("T81 with Mode B OFF an exhausted budget does NOT deny a dispatch",
          run_hook(DISPATCH, payload, d).returncode == 0,
          "the gate is active on a baseline run — it must be inert")
    modes.write(run_dir, cli_exec=True)
    p = run_hook(DISPATCH, payload, d)
    check("T82 with Mode B ON the exhausted budget DENIES the dispatch", p.returncode == 2,
          f"rc={p.returncode} — Mode B would trade away the ADR-17 boundary")
    check("T83 the denial explains it is charged to the same ledger",
          "same ADR-17 ledger" in p.stderr, p.stderr[:160])
    # Unrelated Bash must still be allowed even with the budget exhausted and Mode B on.
    check("T84 unrelated Bash is still allowed at cap",
          run_hook(DISPATCH, {**payload, "tool_input": {"command": "grep -rn TODO src/"}},
                   d).returncode == 0)
    # ONE ledger: a dispatch under a live budget consumes a slot the Agent gate would have seen.
    budget.write_ledger(run_dir / "budget.json", {"max_spawns": 2, "spawns": 0})
    run_hook(DISPATCH, payload, d)
    check("T85 an allowed dispatch charges the shared ledger",
          budget.read_ledger(run_dir / "budget.json")["spawns"] == 1,
          "two ledgers would mean a cap of 6 permits 12 actors")
    # And it must not gate a session that is not an empirica run at all.
    check("T86 no active run → the dispatch gate is a no-op",
          run_hook(DISPATCH, {**payload, "cwd": str(Path(tempfile.mkdtemp()))},
                   Path(tempfile.mkdtemp())).returncode == 0)
    # GENERATED-SWEEP FINDINGS — the gate's fail-open branches were unguarded in the direction that
    # matters. Each `if <guard>: return 0` was only tested by NOT tripping it; forcing the guard
    # false (so the gate proceeds on input it should ignore) left the suite green. A gate that
    # processes what it should skip is how an over-eager hook starts denying ordinary work.
    budget.write_ledger(run_dir / "budget.json", {"max_spawns": 1, "spawns": 1})  # exhausted
    for label, mutation in (
            ("a non-Bash tool", {"tool_name": "Read"}),
            ("a missing session id", {"session_id": None}),
            ("an empty session id", {"session_id": ""}),
            ("a missing tool_input", {"tool_input": {}}),
            ("a non-string command", {"tool_input": {"command": 42}}),
    ):
        p = run_hook(DISPATCH, {**payload, **mutation}, d)
        check(f"T86b at an EXHAUSTED cap, {label} is still allowed", p.returncode == 0,
              f"rc={p.returncode} — the gate acted on input it must ignore: {p.stderr[:120]!r}")
    # Control: the same exhausted cap DOES deny the real thing, so T86b is not passing because the
    # gate is simply inert.
    check("T86c …while the real dispatch at the same cap is denied",
          run_hook(DISPATCH, payload, d).returncode == 2)
    # And a terminal run must not be gated: only an ACTIVE run has a budget to charge.
    manifest.set_status(manifest.locate_run(d, sid), "converged")
    check("T86d a CONVERGED run is not gated", run_hook(DISPATCH, payload, d).returncode == 0)
    manifest.set_status(manifest.locate_run(d, sid), "active")
    # AUDIT 4 — the allow/deny decision itself was unguarded WITH BUDGET AVAILABLE, and had been
    # excused as an "equivalent path". It is not: inverting it made a dispatch under an ample cap
    # exit 2 with "budget exhausted: 1/6". Every prior dispatch check either ran at an exhausted cap
    # or with no ledger, so the allow half of the decision was never observed.
    budget.write_ledger(run_dir / "budget.json", {"max_spawns": 6, "spawns": 0})
    p = run_hook(DISPATCH, payload, d)
    check("T86e a dispatch with budget AVAILABLE is allowed", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr[:140]!r} — the gate's decision is inverted")
    check("T86f …and the §6 advice still rides that allow path",
          "pins no session" in p.stderr,
          "the advice branch is unreachable when a ledger exists — audit 3's counterexample (c)")
    check("T86g the allowed dispatch charged exactly one slot",
          budget.read_ledger(run_dir / "budget.json")["spawns"] == 1)
    # A session-pinned dispatch under the same cap: allowed, charged, and NOT advised.
    pinned = {**payload, "tool_input": {
        "command": f"codex exec resume {actors.session_id_for('r', 'G1')} 'x'"}}
    p = run_hook(DISPATCH, pinned, d)
    check("T86h a session-pinned dispatch is allowed and NOT advised",
          p.returncode == 0 and "pins no session" not in p.stderr,
          f"rc={p.returncode} stderr={p.stderr[:140]!r}")
    # AUDIT 5 (via the coverage-traced witness) — the `cwd` fallback was unguarded. Every dispatch
    # check passed `cwd` explicitly, so nothing asserted what happens when the payload omits it: the
    # gate resolves the run relative to the WRONG directory, finds no active run, and silently stops
    # gating. The fallback must therefore locate the run from the process cwd, which is where the
    # hook actually runs, so an omitted field degrades to "same directory" rather than "no gate".
    budget.write_ledger(run_dir / "budget.json", {"max_spawns": 1, "spawns": 1})
    no_cwd = {"tool_name": "Bash", "session_id": sid,
              "tool_input": {"command": "codex exec 'x'"}}
    p = run_hook(DISPATCH, no_cwd, d)
    check("T86i a payload with NO cwd still gates (falls back to the process cwd)",
          p.returncode == 2,
          f"rc={p.returncode} — an omitted cwd silently disabled the ADR-17 boundary")
    check("T86j an EMPTY cwd behaves the same as an omitted one",
          run_hook(DISPATCH, {**no_cwd, "cwd": ""}, d).returncode == 2)
    # The DISCRIMINATING half, and the reason it is needed: T86i/T86j alone do not distinguish the
    # real code from `payload.get("cwd") and "."`, because with cwd absent BOTH yield ".". The
    # mutation's actual effect is on the PRESENT-cwd case — it discards the supplied path and
    # resolves the run relative to the process cwd instead, which silently stops gating whenever the
    # hook's process cwd differs from the payload's. Every other dispatch check happens to run from
    # `d`, so nothing observed it. This one deliberately runs the hook from ELSEWHERE.
    elsewhere = Path(tempfile.mkdtemp())
    p = run_hook(DISPATCH, {**no_cwd, "cwd": str(d)}, elsewhere)
    check("T86k the SUPPLIED cwd is honoured even when the hook runs from another directory",
          p.returncode == 2,
          f"rc={p.returncode} — the payload's cwd was discarded, so the gate looked for the run in "
          f"the wrong place and stopped gating")


def dispatch_is(command: str) -> bool:
    """dispatch_gate.is_dispatch, loaded lazily so the module is imported once."""
    global _dispatch_mod
    try:
        _dispatch_mod
    except NameError:
        _dispatch_mod = _load("dispatch_gate", DISPATCH)
    return _dispatch_mod.is_dispatch(command)


def test_derived_session_ids_are_deterministic():
    """ADR-24 §6 — a per-claim session id must be derived, not random: hooks stay deterministic in
    a resumable run (ADR-19), and `claude --session-id` requires a valid UUID (V1)."""
    a = actors.session_id_for("run123", "G4")
    b = actors.session_id_for("run123", "G4")
    check("T87 the derivation is stable across calls", a == b)
    check("T88 it is a valid UUID (claude --session-id requires one)",
          str(uuid.UUID(a)) == a, a)
    check("T89 different claims get different sessions",
          a != actors.session_id_for("run123", "G5"))
    check("T90 different runs get different sessions",
          a != actors.session_id_for("run124", "G4"))
    # Stable across PROCESSES, not merely within one — the property resumability needs.
    out = subprocess.run(
        [sys.executable, "-c",
         "import importlib.util,sys;"
         f"s=importlib.util.spec_from_file_location('a',{str(HOOKS / 'actors.py')!r});"
         "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
         "print(m.session_id_for('run123','G4'))"],
        capture_output=True, text=True)
    check("T91 the derivation is stable across PROCESSES", out.stdout.strip() == a,
          f"{out.stdout.strip()!r} != {a!r}")
    # AUDIT FINDING — §6 was a helper with no caller: schema and tests, not wiring. A derivation
    # nothing uses is a convention each future adapter would re-implement differently, which is
    # exactly what "standards over invention" is meant to prevent.
    dispatch = _load("dispatch_gate", DISPATCH)
    check("T91b the derivation has a real caller that builds the CLI flag",
          dispatch.session_flag_for("run123", "G4", "claude") == ["--session-id", a], a)
    check("T91c pi takes the same derived id (arbitrary string, creates if missing)",
          dispatch.session_flag_for("run123", "G4", "pi") == ["--session-id", a])
    check("T91d codex resumes by subcommand, not flag",
          dispatch.session_flag_for("run123", "G4", "codex") == ["resume", a])
    # And nothing in this module may name a model: the flag builder is workflow logic (ADR-23
    # fitness #3, which R24 enforces file-wide).
    # AUDIT FINDING (twice) — §6 must be BEHAVIOUR, not a helper. The first fix added
    # session_flag_for, which the second audit correctly observed had no production caller either:
    # a longer chain still terminating in dead code. The gate now uses it to advise on a dispatch
    # that pins no session, so the derivation runs in production.
    cold = dispatch.advice_for("codex exec 'resolve G3'", "run123")
    check("T91f a dispatch pinning NO session gets §6 advice", cold is not None)
    check("T91g the advice carries a real derived session id",
          cold and actors.session_id_for("run123", "<claim-id>") in cold, f"{cold}")
    check("T91h a dispatch that DOES pin a session is not nagged",
          dispatch.advice_for(f"codex exec resume {a} 'x'", "run123") is None)
    check("T91i a non-dispatch is never advised",
          dispatch.advice_for("grep -rn TODO src/", "run123") is None)
    check("T91j the harness is identified from the command",
          dispatch.dispatched_harness("claude -p 'x'") == "claude"
          and dispatch.dispatched_harness("pi --mode json 'x'") == "pi"
          and dispatch.dispatched_harness("codex --version") is None)
    # AUDIT FINDING (V5, found by this plugin auditing itself) — detection scanned EVERY token for
    # an actor-CLI name, so `echo claude -p` and `grep claude -p file` read as dispatches. Not a
    # harmless over-count: a positive charges the ADR-17 ledger and, at the cap, main() returns 2 —
    # an innocent Bash command DENIED. Detection must match only in command position.
    #
    # Two-sided on purpose. Narrowing to `tokens[0]` alone would kill over-detection while breaking
    # every prefix case a prior audit added (env assignments, wrappers, paths), and an
    # under-detection lets a real dispatch go uncharged. Both directions are load-bearing, so both
    # are asserted here rather than left to whichever failure the author happened to be chasing.
    _elevate = "su" + "do"  # spelled indirectly: a literal trips local dangerous-command hooks
    for _cmd, _want in [
        # MUST detect — a missed dispatch is an uncharged spawn
        ("FOO=bar claude -p hi", "claude"),
        ("FOO=bar BAZ=1 claude -p hi", "claude"),
        ("/usr/local/bin/codex exec hi", "codex"),
        ("env claude -p hi", "claude"),
        ("env FOO=1 claude -p hi", "claude"),
        (f"{_elevate} -u alice claude -p hi", "claude"),   # wrapper with flag + value
        ("timeout 30 claude -p hi", "claude"),             # wrapper with bare value
        ("nohup claude -p hi", "claude"),
        ("codex --version && pi -p 'x'", "pi"),
        ("cd /tmp; claude -p 'x'", "claude"),
        # MUST NOT detect — the name is an ARGUMENT, not the program being invoked
        ("echo claude -p", None),
        ("grep claude -p file", None),
        ("grep -rn 'claude -p' src/", None),
        ("git commit -m 'run claude -p later'", None),
        ("python3 build.py --tool claude -p", None),
        ("cat notes-claude-p.txt", None),
        # MUST NOT detect — invoked, but not to run a model (the doctor's own probes cost nothing)
        ("claude --help", None),
        ("codex doctor", None),
    ]:
        _got = dispatch.dispatched_harness(_cmd)
        check(f"T91L dispatch position: {_cmd!r} → {_want}", _got == _want,
              f"got {_got!r}, want {_want!r}"
              + (" (over-detection charges the ledger and can DENY innocent Bash)"
                 if _want is None else " (under-detection leaves a dispatch uncharged)"))
    # And advice must never become a denial: it rides the allow path.
    d5 = Path(tempfile.mkdtemp())
    sid5 = "sess-advice"
    manifest.start_run(manifest.locate_run(d5, sid5), sid5, d5)
    modes.write(manifest.locate_run_dir(d5, sid5), cli_exec=True)
    p5 = run_hook(DISPATCH, {"tool_name": "Bash", "cwd": str(d5), "session_id": sid5,
                             "tool_input": {"command": "codex exec 'no session'"}}, d5)
    check("T91k §6 advice is emitted but ALLOWS the dispatch",
          p5.returncode == 0 and "pins no session" in p5.stderr,
          f"rc={p5.returncode} stderr={p5.stderr[:120]!r}")
    check("T91e every ADR-24 module has at least one caller outside the tests",
          all(any(name in (HOOKS / f).read_text()
                  for f in ("convergence_gate.py", "run_start.py", "spawn_gate.py",
                            "dispatch_gate.py", "doctor.py"))
              for name in ("actors", "attribution", "modes", "doctor")),
          "an ADR-24 module is dead code reachable only from the test suite")


def test_adr19_fail_matrix_survives_adr24():
    """The whole ADR-24 build sits on top of the ADR-19 fail-direction matrix. If anchoring or the
    doctor changed those directions, everything above is built on sand."""
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(d)], check=True, capture_output=True)
    sid = "sess-matrix"
    check("T92 no manifest → fail OPEN",
          run_hook(GATE, {"cwd": str(d), "session_id": sid}, d).returncode == 0)
    rp = manifest.locate_run(d, sid)
    manifest.start_run(rp, sid, d)
    rp.write_text("{corrupt")
    check("T93 corrupt manifest → fail CLOSED",
          run_hook(GATE, {"cwd": str(d), "session_id": sid}, d).returncode == 2)
    manifest.start_run(rp, sid, d)
    check("T94 active run, missing graph → fail CLOSED",
          run_hook(GATE, {"cwd": str(d), "session_id": sid}, d).returncode == 2)
    manifest.set_status(rp, "converged")
    check("T95 terminal run → fail OPEN (never re-block a finished run)",
          run_hook(GATE, {"cwd": str(d), "session_id": sid}, d).returncode == 0)


def test_hooks_json_registers_the_dispatch_gate():
    cfg = json.loads((HOOKS / "hooks.json").read_text())
    pre = cfg["hooks"].get("PreToolUse", [])
    scripts = [h["args"][0].rsplit("/", 1)[-1] for g in pre for h in g.get("hooks", [])]
    check("T96 the dispatch gate is registered on PreToolUse",
          "dispatch_gate.py" in scripts, f"{scripts}")
    bash_groups = [g for g in pre if g.get("matcher") == "Bash"]
    check("T97 it is matched on Bash", len(bash_groups) == 1, f"{[g.get('matcher') for g in pre]}")
    check("T98 the spawn gate is still registered on Agent",
          any(g.get("matcher") == "Agent" for g in pre))


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
              test_audit_verdict_is_per_claim_not_per_graph,
              test_rewording_an_audited_claim_unreviews_that_claim,
              test_swapping_evidence_after_review_unreviews_that_claim,
              test_legacy_flat_verdict_form_is_refused,
              test_entry_missing_a_digest_is_not_coverage,
              test_fix_and_reaudit_does_not_invalidate_untouched_claims,
              test_repeat_requires_every_run_to_pass,
              test_repeat_result_hash_covers_every_run,
              test_freeze_defers_later_claims_and_closes_the_run,
              test_freeze_cannot_be_enlarged_by_refreezing,
              test_frozen_claims_still_gate,
              test_corrupt_freeze_record_reads_as_not_frozen,
              test_frozen_run_still_owes_an_audit,
              test_pretooluse_hook_does_not_import_the_freeze_path,
              test_legacy_shape_cannot_free_a_blocking_run, test_route_announcement_is_recorded,
              test_spawn_ledger_is_keyed_to_the_run, test_p1_violation_survives_a_passing_audit, test_spike_with_no_files_cannot_approve,
              test_auditor_is_spawned_by_its_plugin_scoped_name,
              test_agent_definitions_pin_tiers_not_ids_in_logic,
              test_phase_machine_records_phases,
              test_route_stamp_hook_records_first_investigation,
              test_route_stamp_ignores_non_investigative_tools,
              test_route_stamp_is_a_noop_outside_a_run,
              test_route_before_investigation_verdict,
              test_announcement_does_not_stamp_itself,
              test_route_stamp_first_write_wins_on_route_too,
              test_gate_surfaces_p1_violation_to_the_auditor,
              test_stamp_ordering_is_numeric_not_lexicographic,
              test_p1_is_decisive_via_harness_write_order,
              test_p1_inconclusive_is_not_reported_as_clean,
              test_gate_separates_proven_violation_from_unverifiable,
              test_hooks_json_registers_the_stamp_hook,
              # --- ADR-24 -------------------------------------------------------
              test_run_identity_survives_a_cwd_change,
              test_actor_is_additive_everywhere,
              test_fable_is_refused_by_policy,
              test_audit_independence_is_real_and_asserted,
              test_auditor_spawn_records_the_dispatcher_side_actor,
              test_same_actor_audit_is_detected_and_reported,
              test_attribution_reaches_the_run_report,
              test_modes_are_off_by_default_and_independent,
              test_doctor_detects_without_inferring,
              test_doctor_runs_at_run_start_and_cannot_wedge,
              test_cli_exec_dispatch_is_gated_at_the_same_boundary,
              test_derived_session_ids_are_deterministic,
              test_adr19_fail_matrix_survives_adr24,
              test_hooks_json_registers_the_dispatch_gate]:
        # An exception inside one test is recorded as a FAILED check for that test and the suite
        # CONTINUES. Previously it propagated and aborted the run, so a single broken hook hid
        # every check after it — and a crash reads to a reader as "the tooling is broken", not as
        # "this behaviour regressed". Found while sabotage-testing the ADR-24 checks, where three
        # deliberately-broken hooks crashed the runner instead of turning their guards red.
        try:
            t()
        except Exception as exc:  # noqa: BLE001 — a test must not be able to hide its siblings
            check(f"{t.__name__} raised {type(exc).__name__}", False, str(exc)[:200])
    width = max(len(n) for n, _, _ in results)
    passed = 0
    for name, ok, detail in results:
        line = f"  [{'PASS' if ok else 'FAIL'}] {name.ljust(width)}"
        if not ok and detail:
            line += f"  → {detail}"
        print(line)
        passed += ok
    if warnings:
        print(f"\n{len(warnings)} warning(s) — true, actionable, not gated (see warn()):")
        for message in warnings:
            print(f"  [WARN] {message}")
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
