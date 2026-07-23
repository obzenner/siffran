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

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def write_spec(body: str) -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "spec.md").write_text(body)
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
    p = run_hook(GATE, {"cwd": str(HERE)}, HERE)
    check("A6 gate exits 2 (block) while unconverged", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")
    check("A7 block reason on stderr names theta", "θ=0.8" in p.stderr, f"got {p.stderr!r}")


def test_hook_allows_when_converged():
    d = write_spec("## Unknowns\n- [x] done <!-- confidence: 0.9 -->\n"
                   "- [x] also <!-- confidence: 0.85 -->\n")
    p = run_hook(GATE, {"cwd": str(d)}, d)
    check("A8 gate exits 0 (allow) when converged", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_hook_fail_open_missing_spec():
    d = Path(tempfile.mkdtemp())
    p = run_hook(GATE, {"cwd": str(d)}, d)
    check("A9 fail-open (exit 0) when no spec", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")


# --- adversarial must-fixes -------------------------------------------------
def test_unscored_unknown_blocks():
    d = write_spec("## Unknowns\n- [ ] no confidence comment here\n"
                   "- [x] scored <!-- confidence: 0.95 -->\n")
    p = run_hook(GATE, {"cwd": str(d)}, d)
    check("F1 unscored unknown BLOCKS (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_out_of_range_confidence_blocks():
    # "8" (fat-finger for 0.8) is out of [0,1] → treated as 0.0 → blocks.
    d = write_spec("## Unknowns\n- [ ] fat finger <!-- confidence: 8 -->\n")
    p = run_hook(GATE, {"cwd": str(d)}, d)
    check("F2 out-of-range confidence BLOCKS (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_unreadable_spec_fails_closed():
    d = Path(tempfile.mkdtemp())
    spec = d / "spec.md"
    spec.mkdir()  # a directory where a file is expected → read_text raises OSError
    p = run_hook(GATE, {"cwd": str(d)}, d)
    check("F3 unreadable spec FAILS CLOSED (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_blocked_unknown_allows():
    # A genuinely unresolvable unknown surfaced to the human must NOT wedge the loop.
    d = write_spec("## Unknowns\n"
                   "- [ ] needs a human call <!-- confidence: 0.2, blocked: needs-decision -->\n")
    p = run_hook(GATE, {"cwd": str(d)}, d)
    check("F4 blocked (surfaced) unknown ALLOWS stop (exit 0)", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_checklist_outside_unknowns_ignored():
    # Checkboxes NOT under ## Unknowns must not block (scoping).
    d = write_spec("## Tasks\n- [ ] some todo\n\n## Unknowns\n- [x] u <!-- confidence: 0.9 -->\n")
    p = run_hook(GATE, {"cwd": str(d)}, d)
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
    p = run_hook(GATE, {"cwd": str(d)}, d)
    check("G1 invalid blocked tag does NOT bypass (exit 2)", p.returncode == 2,
          f"rc={p.returncode} stderr={p.stderr!r}")


def test_valid_blocked_tags_allow():
    for tag in ("needs-decision", "needs-data", "needs-experiment", "needs-budget"):
        d = write_spec(f"## Unknowns\n- [ ] x <!-- confidence: 0.1, blocked: {tag} -->\n")
        p = run_hook(GATE, {"cwd": str(d)}, d)
        check(f"G2 valid tag {tag} allows stop", p.returncode == 0, f"rc={p.returncode}")


def test_second_unknowns_section_aggregated():
    # review 1.2b: a pending item in a SECOND Unknowns section must still block.
    d = write_spec("## Unknowns\n- [x] a <!-- confidence: 0.9 -->\n\n"
                   "## Notes\ntext\n\n## Unknowns\n- [ ] b <!-- confidence: 0.2 -->\n")
    p = run_hook(GATE, {"cwd": str(d)}, d)
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


# --- SessionStart:compact re-injection --------------------------------------
def test_state_restore_reinjects_unknowns():
    proc = subprocess.run([sys.executable, str(RESTORE)],
                          input=json.dumps({"cwd": str(HERE)}), capture_output=True, text=True)
    check("C1 restore exits 0", proc.returncode == 0, f"stderr={proc.stderr!r}")
    check("C2 restore re-injects unknown bodies", "U1" in proc.stdout, f"got {proc.stdout!r}")
    check("C3 restore reports sub-θ status", "below θ" in proc.stdout, f"got {proc.stdout!r}")


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
    p = run_hook(GATE, {"cwd": str(d)}, d)
    out = json.loads(p.stdout)
    check("E1 budget-exhausted run ALLOWS stop (exit 0)", p.returncode == 0,
          f"rc={p.returncode}")
    check("E2 budget-exhausted run flagged converged:false", out.get("converged") is False,
          f"got {out}")
    check("E3 note names budget exhaustion", "budget" in out.get("note", "").lower(),
          f"got {out}")


def test_gate_true_convergence_flagged_true():
    d = write_spec("## Unknowns\n- [x] done <!-- confidence: 0.9 -->\n")
    p = run_hook(GATE, {"cwd": str(d)}, d)
    out = json.loads(p.stdout)
    check("E4 truly converged run flagged converged:true", out.get("converged") is True,
          f"got {out}")


def test_gate_budget_does_not_stop_healthy_loop():
    # A sub-θ unknown that is NOT blocked must still block the stop, regardless of budget —
    # budget never stops a healthy loop early (ADR-17 fitness #3).
    d = write_spec("## Unknowns\n- [ ] still open <!-- confidence: 0.3 -->\n")
    p = run_hook(GATE, {"cwd": str(d)}, d)
    check("E5 healthy sub-θ loop still blocks (exit 2)", p.returncode == 2,
          f"rc={p.returncode}")


def main() -> int:
    for t in [test_parse, test_converged_math, test_theta_guard,
              test_hook_blocks_when_unconverged, test_hook_allows_when_converged,
              test_hook_fail_open_missing_spec, test_unscored_unknown_blocks,
              test_out_of_range_confidence_blocks, test_unreadable_spec_fails_closed,
              test_blocked_unknown_allows, test_checklist_outside_unknowns_ignored,
              test_malformed_stdin_no_crash,
              test_invalid_blocked_tag_still_blocks, test_valid_blocked_tags_allow,
              test_second_unknowns_section_aggregated, test_strict_coercion_rejects_bad_caps,
              test_infinity_ledger_does_not_crash, test_run_id_sanitised,
              test_gate_pass, test_gate_fail,
              test_gate_is_real_not_judgment, test_gate_timeout_fails,
              test_harness_propagates_exit_code, test_harness_launch_failure_is_fail,
              test_state_restore_reinjects_unknowns,
              test_budget_math_unbounded_and_bounded, test_reserve_spawn_atomic_increment_and_cap,
              test_missing_ledger_fail_open, test_spawn_gate_denies_over_cap,
              test_spawn_gate_ignores_non_agent_tools, test_spawn_gate_unbounded_allows,
              test_gate_budget_exhausted_is_non_converged, test_gate_true_convergence_flagged_true,
              test_gate_budget_does_not_stop_healthy_loop]:
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
