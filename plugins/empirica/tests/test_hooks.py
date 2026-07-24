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
FIXTURE_SPEC = HERE / "spec.md"

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cg = _load("convergence_gate", GATE)
budget = _load("budget", HOOKS / "budget.py")
manifest = _load("manifest", HOOKS / "manifest.py")
RUN_START = HOOKS / "run_start.py"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


DEFAULT_SID = "sess-test"


def write_spec(body: str, sid: str = DEFAULT_SID, max_passes: int = 8) -> Path:
    """Establish a real empirica run: an active manifest plus the living spec written to the
    run directory the manifest records (`.claude/empirica/<run_id>/spec.md`). The gate reads
    the spec from the manifest, so this is how a run is legitimately established. Returns the
    cwd (session id is DEFAULT_SID unless overridden)."""
    d = Path(tempfile.mkdtemp())
    run = manifest.start_run(manifest.locate_run(d, sid), sid, d, max_passes=max_passes)
    Path(run["spec_path"]).write_text(body)
    return d


def run_hook(script: Path, payload: dict, cwd: Path) -> subprocess.CompletedProcess:
    """Invoke a hook exactly as Claude Code would: JSON on stdin. Block = exit 2 +
    stderr; allow = exit 0 (re-verified against live docs 2026-07-22)."""
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload), capture_output=True, text=True, cwd=str(cwd),
    )


# --- parsing + convergence math (unit) --------------------------------------
def test_parse():
    unk = cg.parse_unknowns(FIXTURE_SPEC.read_text())
    check("A1 parse finds 3 unknowns", len(unk) == 3, f"got {len(unk)}")
    check("A2 two pending below theta", len(cg.pending(unk, 0.8)) == 2,
          f"got {cg.pending(unk, 0.8)}")


def test_converged_math():
    def u(c, blocked=None): return cg.Unknown("x", c, blocked)
    check("A3 not converged when any < theta", cg.converged([u(0.9), u(0.4)], 0.8) is False)
    check("A4 converged when all >= theta", cg.converged([u(0.8), u(0.95)], 0.8) is True)
    check("A5 vacuous converge (no unknowns)", cg.converged([], 0.8) is True)


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
    d = write_spec(FIXTURE_SPEC.read_text())  # the committed fixture, in a real run
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("A6 gate exits 2 (block) while unconverged", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")
    check("A7 block reason on stderr names theta", "θ=0.8" in p.stderr, f"got {p.stderr!r}")


def test_hook_allows_when_converged():
    d = write_spec("## Unknowns\n- [x] done <!-- confidence: 0.9 -->\n"
                   "- [x] also <!-- confidence: 0.85 -->\n")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("A8 gate exits 0 (allow) when converged", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_hook_fail_open_missing_spec():
    d = Path(tempfile.mkdtemp())
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("A9 fail-open (exit 0) when no spec", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")


# --- adversarial must-fixes -------------------------------------------------
def test_unscored_unknown_blocks():
    d = write_spec("## Unknowns\n- [ ] no confidence comment here\n"
                   "- [x] scored <!-- confidence: 0.95 -->\n")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("F1 unscored unknown BLOCKS (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_out_of_range_confidence_blocks():
    # "8" (fat-finger for 0.8) is out of [0,1] → treated as 0.0 → blocks.
    d = write_spec("## Unknowns\n- [ ] fat finger <!-- confidence: 8 -->\n")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("F2 out-of-range confidence BLOCKS (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_unreadable_spec_fails_closed():
    # Establish a run, then replace the living spec with a directory → read_text raises
    # OSError → the active run fails CLOSED rather than fabricating convergence.
    d = Path(tempfile.mkdtemp())
    run = manifest.start_run(manifest.locate_run(d, DEFAULT_SID), DEFAULT_SID, d)
    Path(run["spec_path"]).mkdir(parents=True)
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("F3 unreadable spec FAILS CLOSED (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_blocked_unknown_allows():
    # A genuinely unresolvable unknown surfaced to the human must NOT wedge the loop.
    d = write_spec("## Unknowns\n"
                   "- [ ] needs a human call <!-- confidence: 0.2, blocked: needs-decision -->\n")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("F4 blocked (surfaced) unknown ALLOWS stop (exit 0)", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_checklist_outside_unknowns_ignored():
    # Checkboxes NOT under ## Unknowns must not block (scoping).
    d = write_spec("## Tasks\n- [ ] some todo\n\n## Unknowns\n- [x] u <!-- confidence: 0.9 -->\n")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("F5 checkbox outside Unknowns does not block", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_malformed_stdin_no_crash():
    proc = subprocess.run([sys.executable, str(GATE)], input="not json",
                          capture_output=True, text=True, cwd=str(HERE))
    check("F6 malformed stdin does not crash (exit in {0,2})", proc.returncode in (0, 2),
          f"rc={proc.returncode} stderr={proc.stderr!r}")


# --- Review fixes: gate integrity -------------------------------------------
def test_invalid_blocked_tag_still_blocks():
    # review 1.1: a made-up blocked tag must NOT bypass the gate.
    d = write_spec("## Unknowns\n"
                   "- [ ] sneaky <!-- confidence: garbage, blocked: totally-made-up -->\n")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("G1 invalid blocked tag does NOT bypass (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_valid_blocked_tags_allow():
    for tag in ("needs-decision", "needs-data", "needs-experiment", "needs-budget"):
        d = write_spec(f"## Unknowns\n- [ ] x <!-- confidence: 0.1, blocked: {tag} -->\n")
        p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
        check(f"G2 valid tag {tag} allows stop", p.returncode == 0, f"rc={p.returncode}")


def test_malformed_confidence_blocks_even_with_valid_tag():
    # review 1.1 residual: a valid tag must NOT exempt an item whose confidence is
    # malformed/out-of-range — a residual has to carry a real score. Fail closed.
    d = write_spec("## Unknowns\n- [ ] x <!-- confidence: garbage, blocked: needs-decision -->\n")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("G1b malformed confidence + valid tag STILL blocks (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_second_unknowns_section_aggregated():
    # review 1.2b: a pending item in a SECOND Unknowns section must still block.
    d = write_spec("## Unknowns\n- [x] a <!-- confidence: 0.9 -->\n\n"
                   "## Notes\ntext\n\n## Unknowns\n- [ ] b <!-- confidence: 0.2 -->\n")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    check("G3 second Unknowns section is aggregated (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


# --- Review fixes: budget hardening -----------------------------------------
def test_strict_coercion_rejects_bad_caps():
    check("G4 string cap → None (unbounded, not 0)", budget._int_or_none("5") is None)
    check("G5 bool cap → None", budget._int_or_none(True) is None)
    check("G6 negative cap → None", budget._int_or_none(-3) is None)
    check("G7 valid int cap kept", budget._int_or_none(5) == 5)


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
def test_state_restore_reinjects_unknowns():
    # Restore resolves the living spec via the manifest, so it needs a real run.
    d = write_spec(FIXTURE_SPEC.read_text())
    proc = subprocess.run([sys.executable, str(RESTORE)],
                          input=json.dumps({"cwd": str(d), "session_id": DEFAULT_SID}),
                          capture_output=True, text=True)
    check("C1 restore exits 0", proc.returncode == 0, f"stderr={proc.stderr!r}")
    check("C2 restore re-injects unknown bodies", "U1" in proc.stdout, f"got {proc.stdout!r}")
    check("C3 restore reports sub-θ status", "below θ" in proc.stdout, f"got {proc.stdout!r}")


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
    d = write_spec("## Unknowns\n- [x] done <!-- confidence: 0.9 -->\n")
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
    d = write_spec("## Unknowns\n"
                   "- [x] resolved <!-- confidence: 0.9 -->\n"
                   "- [ ] ran out <!-- confidence: 0.3, blocked: needs-budget -->\n")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    out = json.loads(p.stdout)
    check("E1 budget-exhausted run ALLOWS stop (exit 0)", p.returncode == 0,
          f"rc={p.returncode}")
    check("E2 budget-exhausted run flagged converged:false", out.get("converged") is False,
          f"got {out}")
    check("E3 note names budget exhaustion", "budget" in out.get("note", "").lower(),
          f"got {out}")


def test_gate_true_convergence_flagged_true():
    d = write_spec("## Unknowns\n- [x] done <!-- confidence: 0.9 -->\n")
    p = run_hook(GATE, {"cwd": str(d), "session_id": DEFAULT_SID}, d)
    out = json.loads(p.stdout)
    check("E4 truly converged run flagged converged:true", out.get("converged") is True,
          f"got {out}")


def test_gate_budget_does_not_stop_healthy_loop():
    # A sub-θ unknown that is NOT blocked must still block the stop, regardless of budget —
    # budget never stops a healthy loop early (ADR-17 fitness #3).
    d = write_spec("## Unknowns\n- [ ] still open <!-- confidence: 0.3 -->\n")
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
def _start_and_spec(body: str, max_passes: int = 8) -> tuple[Path, str]:
    """Activate a run and write its living spec into the run directory — a real empirica run."""
    d = Path(tempfile.mkdtemp())
    sid = "sess-e2e"
    run = manifest.start_run(manifest.locate_run(d, sid), sid, d, max_passes=max_passes)
    Path(run["spec_path"]).write_text(body)
    return d, sid


def test_gate_active_run_missing_spec_fails_closed():
    # 1.2a: an ACTIVE run whose spec is deleted must BLOCK (identity established).
    d = Path(tempfile.mkdtemp())
    sid = "sess-del"
    manifest.start_run(manifest.locate_run(d, sid), sid, d)
    # No spec.md written → active run + missing spec.
    p = run_hook(GATE, {"cwd": str(d), "session_id": sid}, d)
    check("M11 active run + missing spec → BLOCK (1.2a)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_gate_no_manifest_missing_spec_fails_open():
    # No manifest (not an empirica run) + no spec → unchanged fail-OPEN (unrelated repo safe).
    d = Path(tempfile.mkdtemp())
    p = run_hook(GATE, {"cwd": str(d), "session_id": "sess-unrelated"}, d)
    check("M12 no manifest + missing spec → fail-open (exit 0)", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_gate_corrupt_manifest_fails_closed():
    d, sid = _start_and_spec("## Unknowns\n- [x] a <!-- confidence: 0.9 -->\n")
    manifest.locate_run(d, sid).write_text("{ not json")  # corrupt an active run
    p = run_hook(GATE, {"cwd": str(d), "session_id": sid}, d)
    check("M13 corrupt active manifest → BLOCK (2.5)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_spec_path_outside_run_dir_is_rejected():
    # Copilot review PR #8: a manifest spec_path pointing outside the run directory (a corrupt
    # or rewritten manifest aiming the gate at a "converged" file elsewhere) must be ignored
    # in favour of the canonical run-dir spec. Here the run-dir spec is unconverged, but the
    # manifest points at an out-of-run "converged" file — the gate must still BLOCK.
    d = Path(tempfile.mkdtemp())
    sid = "sess-escape"
    run = manifest.start_run(manifest.locate_run(d, sid), sid, d)
    Path(run["spec_path"]).write_text("## Unknowns\n- [ ] open <!-- confidence: 0.1 -->\n")
    decoy = d / "decoy-converged.md"
    decoy.write_text("## Unknowns\n- [x] done <!-- confidence: 0.99 -->\n")
    # Rewrite the manifest to point spec_path at the decoy (outside the run dir).
    rp = manifest.locate_run(d, sid)
    data = json.loads(rp.read_text()); data["spec_path"] = str(decoy.resolve())
    rp.write_text(json.dumps(data))
    resolved = cg.spec_path_for(d, sid, manifest.read_run(rp))
    check("M13b out-of-run spec_path ignored → canonical run-dir spec",
          resolved == manifest.default_spec_path(d, sid), f"got {resolved}")
    p = run_hook(GATE, {"cwd": str(d), "session_id": sid}, d)
    check("M13c decoy cannot fabricate convergence → gate BLOCKS", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_gate_pass_counter_terminates_at_cap():
    # The real termination proof: a never-converging run stops at max_passes as
    # stopped_residual (exit 0, converged:false), not by grinding to the 8-block override.
    # With max_passes=3 the gate blocks twice, then the 3rd pass ticks the counter to the
    # cap and ALLOWS the stop honestly — the variant (max_passes−passes) reaching 0.
    d, sid = _start_and_spec("## Unknowns\n- [ ] never <!-- confidence: 0.1 -->\n", max_passes=3)
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
    d, sid = _start_and_spec("## Unknowns\n- [x] done <!-- confidence: 0.9 -->\n")
    p = run_hook(GATE, {"cwd": str(d), "session_id": sid}, d)
    run = manifest.read_run(manifest.locate_run(d, sid))
    check("M18 converged active run → exit 0", p.returncode == 0, f"rc={p.returncode}")
    check("M19 converged run recorded status=converged", run["status"] == "converged",
          f"got {run}")


def test_gate_stopped_run_does_not_reblock():
    # Once a run is converged/stopped, a later Stop must NOT re-block (fail open).
    d, sid = _start_and_spec("## Unknowns\n- [ ] open <!-- confidence: 0.1 -->\n")
    manifest.set_status(manifest.locate_run(d, sid), "converged")
    p = run_hook(GATE, {"cwd": str(d), "session_id": sid}, d)
    check("M20 stopped run → fail-open even with sub-θ unknown (exit 0)", p.returncode == 0,
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
    check("M22b manifest spec_path is inside the run directory (not the repo)",
          run and run["spec_path"] == str((manifest.locate_run_dir(d, sid) / "spec.md").resolve()),
          f"got {run and run.get('spec_path')}")


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


def main() -> int:
    for t in [test_parse, test_converged_math, test_theta_guard,
              test_hook_blocks_when_unconverged, test_hook_allows_when_converged,
              test_hook_fail_open_missing_spec, test_unscored_unknown_blocks,
              test_out_of_range_confidence_blocks, test_unreadable_spec_fails_closed,
              test_blocked_unknown_allows, test_checklist_outside_unknowns_ignored,
              test_malformed_stdin_no_crash,
              test_invalid_blocked_tag_still_blocks, test_valid_blocked_tags_allow,
              test_malformed_confidence_blocks_even_with_valid_tag,
              test_second_unknowns_section_aggregated, test_strict_coercion_rejects_bad_caps,
              test_infinity_ledger_does_not_crash, test_run_id_sanitised,
              test_gate_pass, test_gate_fail,
              test_gate_is_real_not_judgment, test_gate_timeout_fails,
              test_harness_propagates_exit_code, test_harness_launch_failure_is_fail,
              test_harness_large_output_bounded,
              test_state_restore_reinjects_unknowns, test_state_restore_no_run_is_silent,
              test_state_restore_silent_on_terminal_run,
              test_budget_math_unbounded_and_bounded, test_reserve_spawn_atomic_increment_and_cap,
              test_missing_ledger_fail_open, test_spawn_gate_denies_over_cap,
              test_spawn_gate_ignores_non_agent_tools, test_spawn_gate_unbounded_allows,
              test_gate_budget_exhausted_is_non_converged, test_gate_true_convergence_flagged_true,
              test_gate_budget_does_not_stop_healthy_loop,
              test_manifest_lifecycle_and_idempotent_start, test_manifest_run_id_stable_and_keyed,
              test_manifest_corrupt_sentinel, test_manifest_variant_terminates,
              test_manifest_evidence_slot, test_gate_active_run_missing_spec_fails_closed,
              test_gate_no_manifest_missing_spec_fails_open, test_gate_corrupt_manifest_fails_closed,
              test_spec_path_outside_run_dir_is_rejected,
              test_gate_pass_counter_terminates_at_cap, test_gate_active_run_converges_records_status,
              test_gate_stopped_run_does_not_reblock, test_run_start_hook_creates_manifest,
              test_run_start_no_session_id_is_noop, test_run_start_with_real_captured_payload,
              test_hooks_json_matcher_is_regex_for_namespaced_command]:
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
