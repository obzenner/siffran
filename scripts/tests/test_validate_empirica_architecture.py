#!/usr/bin/env python3
"""Synthetic suite for scripts/validate_empirica_architecture.py (D3 spec §5).

Each case mutates a temporary synthetic tree/config and asserts exactly one expected rule id (with a
message fragment) so a case cannot pass for an unrelated reason. Run directly:

    python3 scripts/tests/test_validate_empirica_architecture.py

Stdlib only; exit 0 = all pass, 1 = at least one failure.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import validate_empirica_architecture as va  # noqa: E402

# SSOT: tests load the real architecture.json and override only the values a case needs,
# so the synthetic policy cannot drift from the canonical config (d3-sol residual risk).
_REAL_CONFIG = REPO_ROOT / "plugins" / "empirica" / "architecture.json"


def base_config():
    """A fresh deep copy of the real architecture.json; tests override only per-case values."""
    import copy
    return copy.deepcopy(json.loads(_REAL_CONFIG.read_text(encoding="utf-8")))


class ArchitectureValidatorTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(tempfile.mkdtemp(prefix="archval-"))
        self.pkg = self.repo_root / "plugins" / "empirica"

    def _write(self, rel, content):
        path = self.pkg / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _write_repo(self, rel, content):
        path = self.repo_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _py_files(self, cfg):
        return [f for f in va.discover_runtime_files(cfg, self.repo_root) if f.suffix == ".py"]

    def _ts_files(self, cfg):
        return [f for f in va.discover_runtime_files(cfg, self.repo_root) if f.suffix == ".ts"]

    def _ids(self, diags):
        return {d.rule_id for d in diags}

    def _valid_minimal_tree(self):
        """A clean layered tree with no architecture violations."""
        self._write("core/__init__.py", "")
        self._write("core/pure.py", "def f():\n    return 1\n")
        self._write("application/__init__.py", "")
        self._write("application/service.py", "from core import pure\n")
        self._write("adapters/claude/__init__.py", "")
        self._write("adapters/claude/lifecycle.py", "from application import service\nfrom core import pure\n")
        self._write("adapters/state/__init__.py", "")
        self._write("adapters/state/repo.py", "from core import pure\n")
        self._write("hooks/run_start.py",
                    "import sys\nfrom pathlib import Path\n"
                    "ROOT = Path(__file__).resolve().parents[1]\n"
                    "if str(ROOT) not in sys.path:\n    sys.path.insert(0, str(ROOT))\n"
                    "from adapters.claude.lifecycle import main\n"
                    "if __name__ == '__main__':\n    raise SystemExit(main())\n")

    # --- §5 case 1: valid minimal layered tree (green) ------------------------

    def test_01_valid_layered_tree_has_no_dependency_violations(self):
        cfg = base_config()
        self._valid_minimal_tree()
        diags = va.check_python_dependencies(cfg, self.pkg, self._py_files(cfg))
        self.assertNotIn(va.RULE_DEP_PY, self._ids(diags), diags)
        diags_thin = va.check_thin_hooks(cfg, self.pkg)
        self.assertNotIn(va.RULE_THIN_HOOK, self._ids(diags_thin), diags_thin)

    # --- §5 case 2: core imports application -----------------------------------

    def test_02_core_imports_application_is_forbidden(self):
        cfg = base_config()
        self._write("core/__init__.py", "")
        self._write("core/bad.py", "from application import service\n")
        diags = va.check_python_dependencies(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_DEP_PY, self._ids(diags))
        self.assertTrue(any("core" in d.message and "application" in d.message for d in diags), diags)

    # --- §5 case 3: application imports adapter --------------------------------

    def test_03_application_imports_adapter_is_forbidden(self):
        cfg = base_config()
        self._write("application/__init__.py", "")
        self._write("application/service.py", "from adapters.state import repo\n")
        diags = va.check_python_dependencies(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_DEP_PY, self._ids(diags))
        self.assertTrue(any("application" in d.message and "adapters.state" in d.message for d in diags), diags)

    # --- §5 case 4: Claude adapter imports Codex adapter (cross-host) ---------

    def test_04_claude_imports_codex_cross_host(self):
        cfg = base_config()
        self._write("adapters/claude/__init__.py", "")
        self._write("adapters/claude/x.py", "from adapters.codex import y\n")
        diags = va.check_python_dependencies(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_DEP_PY, self._ids(diags))
        self.assertTrue(any("adapters.codex" in d.message for d in diags), diags)

    # --- §5 case 5: adapter imports/calls a domain adjudicator -----------------

    def test_05_adapter_imports_domain_adjudicator(self):
        cfg = base_config()
        self._write("adapters/claude/__init__.py", "")
        self._write("adapters/claude/evidence.py",
                    "from core.evidence import two_fold_verdict\n\n"
                    "def go(leaves):\n    return two_fold_verdict(leaves, 'c', 't', 'k', 'p')\n")
        diags = va.check_adapter_adjudicators(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_ADAPTER_ADJUDICATOR, self._ids(diags))
        self.assertTrue(any("two_fold_verdict" in d.message for d in diags), diags)

    # --- §5 case 6: forbidden migration file ----------------------------------

    def test_06_forbidden_migration_file(self):
        cfg = base_config()
        self._write("adapters/claude/migrate_legacy.py", "def migrate():\n    pass\n")
        diags = va.check_forbidden_paths(cfg, self.pkg)
        self.assertIn(va.RULE_FORBIDDEN_PATH, self._ids(diags))
        self.assertTrue(any("migrate_legacy" in d.message for d in diags), diags)

    # --- §5 case 7: direct evidence boolean approval (one mutation) ----------

    def test_07a_direct_evidence_boolean_approval(self):
        cfg = base_config()
        self._write("application/knowledge.py",
                    "def admit(payload):\n    return payload['kind'] == 'evidence' and payload['approved'] is True\n")
        diags = va.check_direct_evidence_approval(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message and "approval" in d.message for d in diags), diags)

    def test_07b_mirrored_evidence_approval(self):
        # the mirror spelling 'evidence' == kind must also be caught
        cfg = base_config()
        self._write("application/knowledge.py",
                    "def admit(kind):\n    return 'evidence' == kind\n")
        diags = va.check_direct_evidence_approval(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)

    def test_07c_composite_verdicts_field(self):
        # one mutation: only the persisted composite verdicts field
        cfg = base_config()
        self._write("application/knowledge.py",
                    "class Leaf:\n    verdicts = []\n    def __init__(self):\n        self.verdicts = []\n")
        diags = va.check_forbidden_symbols(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        joined = " ".join(d.message for d in diags)
        self.assertIn("verdicts", joined)
        self.assertFalse(any("KIND_EVIDENCE" in d.message for d in diags), "single mutation only")

    def test_07d_kind_evidence_constant(self):
        # one mutation: only the KIND_EVIDENCE constant
        cfg = base_config()
        self._write("application/knowledge.py", "KIND_EVIDENCE = 'evidence'\n")
        diags = va.check_forbidden_symbols(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("KIND_EVIDENCE" in d.message for d in diags), diags)
        self.assertFalse(any("verdicts" in d.message for d in diags), "single mutation only")

    def test_07e_core_relocated_phases_one_mutation(self):
        # one mutation only: PHASES relocated to core; exact rule + message fragment
        cfg = base_config()
        self._write("core/bad.py", "PHASES = ('route',)\n")
        diags = va.check_forbidden_symbols(cfg, self.pkg, self._py_files(cfg))
        self.assertEqual(self._ids(diags), {va.RULE_FORBIDDEN_SYMBOL})
        self.assertTrue(any(d.rule_id == va.RULE_FORBIDDEN_SYMBOL and "PHASES" in d.message
                            and d.path == "core/bad.py" for d in diags), diags)

    def test_07f_core_relocated_verdicts_one_mutation(self):
        # one mutation only: composite verdicts relocated to core; exact rule + message fragment
        cfg = base_config()
        self._write("core/bad.py", "class S:\n    verdicts = []\n")
        diags = va.check_forbidden_symbols(cfg, self.pkg, self._py_files(cfg))
        self.assertEqual(self._ids(diags), {va.RULE_FORBIDDEN_SYMBOL})
        self.assertTrue(any(d.rule_id == va.RULE_FORBIDDEN_SYMBOL and "verdicts" in d.message
                            and d.path == "core/bad.py" for d in diags), diags)

    def test_07g_typescript_relocated_phase_one_mutation(self):
        # one TS mutation only: the 'phase' literal in a host adapter; exact rule + message
        cfg = base_config()
        self._write("adapters/pi/src/bad.ts", "const phase = 'phase';\n")
        diags = va.check_typescript_forbidden_symbols(cfg, self.pkg, self._ts_files(cfg))
        self.assertEqual(self._ids(diags), {va.RULE_FORBIDDEN_SYMBOL})
        self.assertTrue(any(d.rule_id == va.RULE_FORBIDDEN_SYMBOL and "phase" in d.message
                            and d.path == "adapters/pi/src/bad.ts" for d in diags), diags)

    def test_07h_typescript_relocated_verdicts_one_mutation(self):
        # one TS mutation only: the verdicts field in a host adapter; exact rule + message
        cfg = base_config()
        self._write("adapters/pi/src/bad.ts", "class L { verdicts = []; }\n")
        diags = va.check_typescript_forbidden_symbols(cfg, self.pkg, self._ts_files(cfg))
        self.assertEqual(self._ids(diags), {va.RULE_FORBIDDEN_SYMBOL})
        self.assertTrue(any(d.rule_id == va.RULE_FORBIDDEN_SYMBOL and "verdicts" in d.message
                            and d.path == "adapters/pi/src/bad.ts" for d in diags), diags)

    # --- §5 case 8: v1 runtime protocol literal -------------------------------

    def test_08_v1_protocol_literal(self):
        cfg = base_config()
        self._write("application/wire.py", "API_VERSION = 'empirica/v1'\n")
        diags = va.check_v1_protocol(cfg, self.pkg, self._py_files(cfg), [])
        self.assertIn(va.RULE_V1_PROTOCOL, self._ids(diags))
        self.assertTrue(any("empirica/v1" in d.message for d in diags), diags)

    # --- §5 case 9: persisted contract pointer/revision -----------------------

    def test_09_persisted_contract_pointer_revision(self):
        cfg = base_config()
        self._write("application/state.py",
                    "class State:\n    contract_artifact_id = None\n    contract_revision = 0\n"
                    "    def encode(self):\n"
                    "        return {'contract_artifact_id': self.contract_artifact_id,\n"
                    "                'contract_revision': self.contract_revision}\n")
        diags = va.check_forbidden_symbols(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        joined = " ".join(d.message for d in diags)
        self.assertTrue("contract_artifact_id" in joined or "contract_revision" in joined, diags)

    # --- §5 case 10: phase and separate ticket/reservation fields --------------

    def test_10_phase_and_separate_entities(self):
        cfg = base_config()
        self._write("application/state.py",
                    "PHASES = ('route', 'resolve')\n"
                    "class State:\n    phase = 'route'\n    reservations = ()\n"
                    "    audit_tickets = ()\n    reservation_seq = 0\n")
        diags = va.check_forbidden_symbols(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        joined = " ".join(d.message for d in diags)
        self.assertIn("PHASES", joined)
        self.assertIn("reservations", joined)

    # --- §5 case 11: thin hook containing policy ------------------------------

    def test_11_thin_hook_with_policy(self):
        cfg = base_config()
        self._write("hooks/bad.py", "from core.convergence import adjudicate\n")
        diags = va.check_thin_hooks(cfg, self.pkg)
        self.assertIn(va.RULE_THIN_HOOK, self._ids(diags))
        self.assertTrue(any("core.convergence" in d.message or "domain" in d.message for d in diags), diags)

    # --- §5 case 12: code moved to generated/vendor still counts --------------

    def test_12_vendor_code_counts_in_budget(self):
        cfg = base_config()
        cfg["effective_runtime"]["maximum"] = 5
        cfg["effective_runtime"]["baseline"] = 0
        self._write("vendor/obligations/model.py", "x = 0\n" * 6)
        files = va.discover_runtime_files(cfg, self.repo_root)
        diags = va.check_effective_runtime(cfg, self.pkg, files)
        self.assertIn(va.RULE_BUDGET, self._ids(diags), diags)

    # --- §5 case 13: test files excluded --------------------------------------

    def test_13_test_files_excluded(self):
        cfg = base_config()
        cfg["effective_runtime"]["maximum"] = 5
        cfg["effective_runtime"]["baseline"] = 0
        self._write("core/pure.py", "x = 0\n")
        self._write("tests/test_huge.py", "x = 0\n" * 1000)
        self._write("adapters/pi/test/fakes.ts", "x = 0\n" * 1000)
        files = va.discover_runtime_files(cfg, self.repo_root)
        diags = va.check_effective_runtime(cfg, self.pkg, files)
        self.assertNotIn(va.RULE_BUDGET, self._ids(diags), diags)
        rels = [va.rel_posix(f, self.pkg) for f in files]
        self.assertFalse(any(r.startswith("tests/") for r in rels), rels)
        self.assertFalse(any("/test/" in r for r in rels), rels)

    # --- §5 case 14: total exactly max passes; max+1 fails --------------------

    def test_14_budget_boundary(self):
        cfg = base_config()
        cfg["effective_runtime"]["maximum"] = 3
        cfg["effective_runtime"]["baseline"] = 3
        self._write("core/a.py", "x = 0\n" * 3)
        files = va.discover_runtime_files(cfg, self.repo_root)
        self.assertEqual(self._ids(va.check_effective_runtime(cfg, self.pkg, files)), set())
        self._write("core/b.py", "y = 1\n")
        files = va.discover_runtime_files(cfg, self.repo_root)
        self.assertIn(va.RULE_BUDGET, self._ids(va.check_effective_runtime(cfg, self.pkg, files)))

    # --- §5 case 15: unresolved required PublicContract/profile reference -----

    def test_15_unresolved_contract_reference(self):
        cfg = base_config()
        diags = va.check_contract_references(cfg, self.repo_root)
        self.assertIn(va.RULE_CONTRACT_REF, self._ids(diags))
        self.assertTrue(any("not found" in d.message for d in diags), diags)

    def test_15b_unresolved_profile_reference(self):
        cfg = base_config()
        self._write_repo("contracts/empirica/v2/public-contract.json",
                         json.dumps({"id": "empirica/public", "version": "2.0.0", "protocol": "empirica/v2"}))
        self._write_repo("contracts/empirica/v2/host-profiles.json",
                         json.dumps({"protocol": "empirica/v2", "profiles": [{"profile_id": "other@1.0"}]}))
        diags = va.check_contract_references(cfg, self.repo_root)
        self.assertIn(va.RULE_CONTRACT_REF, self._ids(diags))
        self.assertTrue(any("claude-code@2.1.270" in d.message for d in diags), diags)

    # --- §5 case 16: deterministic sorted diagnostics with path:line -----------

    def test_16_deterministic_sorted_diagnostics(self):
        cfg = base_config()
        self._write("adapters/claude/migrate_legacy.py", "def migrate():\n    pass\n")
        self._write("application/wire.py", "API_VERSION = 'empirica/v1'\n")
        self._write("application/state.py", "PHASES = ('route',)\nclass S:\n    phase = 'route'\n")
        diags = va.run_all(cfg, self.repo_root)
        self.assertTrue(diags, "expected target-state violations")
        keys = [d.sort_key() for d in diags]
        self.assertEqual(keys, sorted(keys), "diagnostics must be deterministically sorted")
        for d in diags:
            self.assertTrue(d.path, "every diagnostic must carry a path")
            self.assertIsInstance(d.line, int)
            self.assertGreaterEqual(d.line, 1)
        ids = self._ids(diags)
        self.assertIn(va.RULE_FORBIDDEN_PATH, ids)
        self.assertIn(va.RULE_V1_PROTOCOL, ids)
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, ids)

    # --- extra: TypeScript cross-host import (check 3) ------------------------

    def test_17_typescript_cross_host_import(self):
        cfg = base_config()
        self._write("adapters/pi/src/index.ts", 'import { x } from "../../claude/x.ts"\n')
        diags = va.check_typescript_dependencies(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_DEP_TS, self._ids(diags))
        self.assertTrue(any("claude" in d.message for d in diags), diags)

    # --- extra: make lifecycle forbidden target + history-rewrite -------------

    def test_18_make_forbidden_target_present(self):
        cfg = base_config()
        self._write_repo("Makefile",
                         "empirica-architecture-check: ## validate architecture\n"
                         "\t@python3 scripts/validate_empirica_architecture.py\n"
                         "migrate-legacy: ## legacy import\n"
                         "\t@python3 plugins/empirica/adapters/claude/migrate_legacy.py\n")
        diags = va.check_make_lifecycle(cfg, self.repo_root)
        ids = self._ids(diags)
        self.assertIn(va.RULE_MAKE_LIFECYCLE, ids)
        self.assertTrue(any("migrate-legacy" in d.message for d in diags), diags)
        # the architecture target is defined and invokes the script -> no missing-target diag
        self.assertFalse(any("not defined" in d.message for d in diags), diags)

    def test_19_make_target_missing(self):
        cfg = base_config()
        self._write_repo("Makefile", "other: ## other\n\t@echo hi\n")
        diags = va.check_make_lifecycle(cfg, self.repo_root)
        self.assertIn(va.RULE_MAKE_LIFECYCLE, self._ids(diags))
        self.assertTrue(any("not defined" in d.message for d in diags), diags)

    def test_20_make_history_rewrite_in_recipe(self):
        cfg = base_config()
        self._write_repo("Makefile",
                         "empirica-architecture-check: ## validate architecture\n"
                         "\t@python3 scripts/validate_empirica_architecture.py\n"
                         "bad: ## bad\n\t@git commit -m x\n")
        diags = va.check_make_lifecycle(cfg, self.repo_root)
        ids = self._ids(diags)
        self.assertIn(va.RULE_MAKE_LIFECYCLE, ids)
        self.assertTrue(any("git commit" in d.message for d in diags), diags)

    # --- regressions for accepted MAJOR findings (one expected rule/message each) ---

    def test_21_nested_hook_imports_domain(self):
        # a hook nested in a subdir must still be discovered and checked (rglob)
        cfg = base_config()
        self._write("hooks/nested/policy.py", "from core.evidence import adjudicate\n")
        diags = va.check_thin_hooks(cfg, self.pkg)
        self.assertIn(va.RULE_THIN_HOOK, self._ids(diags))
        self.assertTrue(any("core.evidence" in d.message or "domain" in d.message for d in diags), diags)
        self.assertTrue(any("hooks/nested/policy.py" in d.path for d in diags), diags)

    def test_22_nested_hook_oversized_function(self):
        # a one-function hook exceeding the recursive executable-statement bound fails
        cfg = base_config()
        body = "".join(f"    x{i} = 0\n" for i in range(50))
        self._write("hooks/big.py", "from adapters.claude.lifecycle import main\n"
                    f"def run():\n{body}")
        diags = va.check_thin_hooks(cfg, self.pkg)
        ids = self._ids(diags)
        self.assertIn(va.RULE_THIN_HOOK, ids)
        self.assertTrue(any("executable statements" in d.message for d in diags), diags)
        # no domain-import diagnostic: the hook imports an adapter, which is allowed
        self.assertFalse(any("domain layer" in d.message for d in diags),
                         "fails solely on structural complexity, not import")

    def test_23_malformed_python_fails_closed(self):
        # a syntax error in production python must produce a parse diagnostic at its line
        cfg = base_config()
        self._write("application/broken.py", "API_VERSION = 'empirica/v1'\ndef x(\n")
        diags = va.check_parse_integrity(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_PARSE, self._ids(diags))
        self.assertTrue(any("syntax error" in d.message and "application/broken.py" in d.path for d in diags),
                       diags)
        self.assertTrue(any(d.line >= 2 for d in diags), "must report the syntax-error line")

    def test_24_package_qualified_import_empirica(self):
        cfg = base_config()
        self._write("core/__init__.py", "")
        self._write("core/bad.py", "from empirica.application import service\n")
        diags = va.check_python_dependencies(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_DEP_PY, self._ids(diags))
        self.assertTrue(any("core" in d.message and "application" in d.message for d in diags), diags)

    def test_25_package_qualified_import_plugins_dot_empirica(self):
        cfg = base_config()
        self._write("core/__init__.py", "")
        self._write("core/bad.py", "from plugins.empirica.application import service\n")
        diags = va.check_python_dependencies(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_DEP_PY, self._ids(diags))
        self.assertTrue(any("core" in d.message and "application" in d.message for d in diags), diags)

    def test_26_external_similar_suffix_not_flagged(self):
        # a third-party module with a coincidentally similar suffix is external, not a layer
        cfg = base_config()
        self._write("core/__init__.py", "")
        self._write("core/ok.py", "from myempirica.application import service\n")
        diags = va.check_python_dependencies(cfg, self.pkg, self._py_files(cfg))
        self.assertNotIn(va.RULE_DEP_PY, self._ids(diags), diags)

    def test_27_typescript_import_equals_cross_host(self):
        # import c = require("...") must be recognized as a cross-host edge
        cfg = base_config()
        self._write("adapters/pi/src/imp.ts", 'import c = require("../../claude/x.ts")\n')
        diags = va.check_typescript_dependencies(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_DEP_TS, self._ids(diags))
        self.assertTrue(any("claude" in d.message for d in diags), diags)

    def test_28_typescript_literal_require_cross_host(self):
        # a bare literal require("...") to another host is also a cross-host edge
        cfg = base_config()
        self._write("adapters/pi/src/imp.ts", 'const m = require("../../claude/x.ts")\n')
        diags = va.check_typescript_dependencies(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_DEP_TS, self._ids(diags))
        self.assertTrue(any("claude" in d.message for d in diags), diags)

    def test_29_make_target_lacks_help_annotation(self):
        # a target definition without a '##' help annotation must be flagged
        cfg = base_config()
        self._write_repo("Makefile",
                         "empirica-architecture-check:\n"
                         "\t@python3 scripts/validate_empirica_architecture.py\n")
        diags = va.check_make_lifecycle(cfg, self.repo_root)
        ids = self._ids(diags)
        self.assertIn(va.RULE_MAKE_LIFECYCLE, ids)
        self.assertTrue(any("help annotation" in d.message for d in diags), diags)
        # the target is defined and invokes the script, so no missing-target/missing-script diag
        self.assertFalse(any("not defined" in d.message for d in diags), diags)
        self.assertFalse(any("does not invoke" in d.message for d in diags), diags)

    def test_30_make_echo_prefixed_history_mutation(self):
        # a history mutation after echo/; must not evade the rule by being on an echo line
        cfg = base_config()
        self._write_repo("Makefile",
                         "empirica-architecture-check: ## validate architecture\n"
                         "\t@python3 scripts/validate_empirica_architecture.py\n"
                         "bad: ## bad\n\t@echo git push; git push origin main\n")
        diags = va.check_make_lifecycle(cfg, self.repo_root)
        ids = self._ids(diags)
        self.assertIn(va.RULE_MAKE_LIFECYCLE, ids)
        self.assertTrue(any("git push" in d.message for d in diags), diags)

    def test_31_evidence_construction_not_flagged(self):
        # evidence construction (dict with kind='evidence') is NOT a boolean-approval comparison
        cfg = base_config()
        self._write("application/knowledge.py",
                    "def build():\n    return {'kind': 'evidence', 'approved': False}\n")
        diags = va.check_direct_evidence_approval(cfg, self.pkg, self._py_files(cfg))
        self.assertNotIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags),
                         "dict construction must not be mistaken for a comparison")

    # --- D3 final scanner: focused comment / loader / escape / operand cases ----

    def test_32_ts_comment_import_not_a_load(self):
        # an import-like line inside a // comment must NOT be treated as a module load
        cfg = base_config()
        self._write("adapters/pi/src/c.ts",
                    '// import { x } from "../../claude/x.ts"\nconst a = 1;\n')
        diags = va.check_typescript_dependencies(cfg, self.pkg, self._ts_files(cfg))
        self.assertNotIn(va.RULE_DEP_TS, self._ids(diags), diags)

    def test_33_ts_block_comment_import_not_a_load(self):
        # an import inside a /* */ block comment must NOT be treated as a module load
        cfg = base_config()
        self._write("adapters/pi/src/c.ts",
                    '/* eslint: import { x } from "../../claude/x.ts" */\nconst a = 1;\n')
        diags = va.check_typescript_dependencies(cfg, self.pkg, self._ts_files(cfg))
        self.assertNotIn(va.RULE_DEP_TS, self._ids(diags), diags)

    def test_34_ts_literal_dynamic_import_cross_host(self):
        # literal dynamic import("...") to another host is a cross-host edge
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const m = await import("../../claude/x.ts");\n')
        diags = va.check_typescript_dependencies(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_DEP_TS, self._ids(diags))
        self.assertTrue(any("claude" in d.message for d in diags), diags)

    def test_35_ts_nonliteral_import_fails_closed(self):
        # import(variable) in a host adapter must fail closed (dependency unknown)
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const m = await import(spec);\n')
        diags = va.check_typescript_dependencies(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_DEP_TS, self._ids(diags))
        self.assertTrue(any("nonliteral" in d.message and "fail closed" in d.message for d in diags),
                       diags)

    def test_36_ts_concatenated_require_fails_closed(self):
        # require("./" + name) is nonliteral and must fail closed
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const m = require("./" + name);\n')
        diags = va.check_typescript_dependencies(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_DEP_TS, self._ids(diags))
        self.assertTrue(any("nonliteral" in d.message for d in diags), diags)

    def test_37_ts_nonliteral_require_fails_closed(self):
        # require(variable) is nonliteral and must fail closed
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const m = require(spec);\n')
        diags = va.check_typescript_dependencies(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_DEP_TS, self._ids(diags))
        self.assertTrue(any("nonliteral" in d.message for d in diags), diags)

    def test_38_ts_escaped_forbidden_value_decodes(self):
        # a hex-escaped forbidden literal must decode before exact comparison
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const v = "reserve\\x5fspawn";\n')
        diags = va.check_typescript_forbidden_symbols(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("reserve_spawn" in d.message for d in diags), diags)

    def test_39_ts_unicode_escaped_forbidden_value_decodes(self):
        # a \uNNNN-escaped forbidden literal must decode before exact comparison
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const v = "reserve\\u005fspawn";\n')
        diags = va.check_typescript_forbidden_symbols(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("reserve_spawn" in d.message for d in diags), diags)

    def test_40_py_kind_eq_evidence_attribute_form(self):
        # obj.kind == 'evidence' (attribute form) is a direct approval comparison
        cfg = base_config()
        self._write("application/knowledge.py",
                    "def admit(obj):\n    return obj.kind == 'evidence'\n")
        diags = va.check_direct_evidence_approval(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message and "approval" in d.message for d in diags),
                       diags)

    def test_41_py_kind_eq_evidence_subscript_form(self):
        # obj['kind'] == 'evidence' (subscript form) is a direct approval comparison
        cfg = base_config()
        self._write("application/knowledge.py",
                    "def admit(obj):\n    return obj['kind'] == 'evidence'\n")
        diags = va.check_direct_evidence_approval(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)

    def test_42_py_mirror_evidence_eq_kind(self):
        # the mirror 'evidence' == obj.kind must also be caught
        cfg = base_config()
        self._write("application/knowledge.py",
                    "def admit(obj):\n    return 'evidence' == obj.kind\n")
        diags = va.check_direct_evidence_approval(cfg, self.pkg, self._py_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)

    def test_43_py_non_kind_classification_not_flagged(self):
        # source_type == 'evidence' is NOT a kind operand and must not be flagged
        cfg = base_config()
        self._write("application/knowledge.py",
                    "def classify(row):\n    return row['source_type'] == 'evidence'\n")
        diags = va.check_direct_evidence_approval(cfg, self.pkg, self._py_files(cfg))
        self.assertNotIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags),
                         "non-kind classification must not be mistaken for approval")

    def test_44_py_evidence_construction_not_flagged_subscript(self):
        # constructing/reading obj['evidence'] is NOT a comparison and must not be flagged
        cfg = base_config()
        self._write("application/knowledge.py",
                    "def get(row):\n    return row['evidence']\n")
        diags = va.check_direct_evidence_approval(cfg, self.pkg, self._py_files(cfg))
        self.assertNotIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags), diags)

    def test_45_ts_kind_eq_evidence(self):
        # TS: kind == 'evidence' is a direct approval comparison
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "if (kind == 'evidence') return true;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message and "approval" in d.message for d in diags),
                       diags)

    def test_46_ts_obj_kind_eq_evidence(self):
        # TS: obj.kind === 'evidence' (attribute form)
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "if (obj.kind === 'evidence') return true;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)

    def test_47_ts_subscript_kind_eq_evidence(self):
        # TS: obj['kind'] === 'evidence' (subscript form)
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "if (obj['kind'] === 'evidence') return true;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)

    def test_48_ts_mirror_evidence_eq_obj_kind(self):
        # TS: the mirror 'evidence' === obj.kind must also be caught
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "if ('evidence' === obj.kind) return true;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)

    def test_49_ts_non_kind_classification_not_flagged(self):
        # TS: source_type === 'evidence' is NOT a kind operand and must not be flagged
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "if (source_type === 'evidence') return true;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertNotIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags),
                         "non-kind classification must not be mistaken for approval")

    def test_50_ts_evidence_construction_not_flagged(self):
        # TS: evidence-object construction (no equality) must not be flagged
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "const x = { kind: 'evidence' };\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertNotIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags),
                         "construction must not be mistaken for a comparison")

    def test_51_ts_protocol_in_comment_not_flagged(self):
        # a v1 protocol literal inside a comment must NOT be flagged
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", '// uses the empirica/v1 contract\nconst a = 1;\n')
        diags = va.check_v1_protocol(cfg, self.pkg, [], self._ts_files(cfg))
        self.assertNotIn(va.RULE_V1_PROTOCOL, self._ids(diags), diags)

    def test_52_ts_protocol_in_real_string_flagged(self):
        # a v1 protocol literal in a real string IS flagged (sanity for the token surface)
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const p = "empirica/v1";\n')
        diags = va.check_v1_protocol(cfg, self.pkg, [], self._ts_files(cfg))
        self.assertIn(va.RULE_V1_PROTOCOL, self._ids(diags))
        self.assertTrue(any("empirica/v1" in d.message for d in diags), diags)

    # --- D3 final lexical correction: template/regex/require/operand cases -----

    def test_53_ts_plain_template_literal_decodes(self):
        # a no-substitution template literal carrying a forbidden value decodes
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const v = `reserve_spawn`;\n')
        diags = va.check_typescript_forbidden_symbols(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("reserve_spawn" in d.message for d in diags), diags)

    def test_54_ts_interpolated_template_is_nonliteral(self):
        # an interpolated module-spec template is nonliteral; the specifier must NOT
        # become a flagged literal (interpolated -> opaque, fail-closed on the load).
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const spec = `./${name}.ts`;\n')
        diags = va.check_typescript_forbidden_symbols(cfg, self.pkg, self._ts_files(cfg))
        self.assertNotIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags), diags)

    def test_55_ts_interpolated_template_comparison_is_visible(self):
        # a comparison inside a ${...} body is tokenized recursively and stays visible
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "const msg = `k=${kind === 'evidence'}`;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message and "approval" in d.message for d in diags),
                       diags)

    def test_56_ts_interpolated_obj_kind_comparison_visible(self):
        # obj.kind === 'evidence' inside a ${...} expression body is also visible
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "const msg = `v=${obj.kind === 'evidence'}`;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)

    def test_57_ts_regex_literal_not_code(self):
        # a regex literal containing policy-looking text must NOT be treated as code
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const RE = /reserve_spawn\\s*void_spawn/gi;\n')
        diags = va.check_typescript_forbidden_symbols(cfg, self.pkg, self._ts_files(cfg))
        self.assertNotIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags),
                         "regex contents must not be mistaken for code")

    def test_58_ts_regex_after_keyword(self):
        # a regex literal after a keyword (return) is recognized, not division
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'function f(s) { return /reserve_spawn/i.test(s); }\n')
        diags = va.check_typescript_forbidden_symbols(cfg, self.pkg, self._ts_files(cfg))
        self.assertNotIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags),
                         "regex after keyword must not be scanned as code")

    def test_59_ts_regex_with_class_brackets(self):
        # a regex with a character class containing '/' and ']' is skipped whole
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const RE = /[\\]/reserve_spawn]/g;\n')
        diags = va.check_typescript_forbidden_symbols(cfg, self.pkg, self._ts_files(cfg))
        self.assertNotIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags),
                         "regex char-class contents must not be scanned as code")

    def test_60_ts_division_not_regex(self):
        # a/b where a follows an operand is division, not a regex; 'b' is an ident
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const x = a / b;\n')
        diags = va.check_typescript_forbidden_symbols(cfg, self.pkg, self._ts_files(cfg))
        self.assertNotIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags), diags)

    def test_61_ts_bracketed_global_require_fails_closed(self):
        # globalThis["require"](...) is a common bracketed global require -> fail closed
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const m = globalThis["require"]("./x");\n')
        diags = va.check_typescript_dependencies(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_DEP_TS, self._ids(diags))
        self.assertTrue(any("nonliteral" in d.message for d in diags), diags)

    def test_62_ts_dotted_global_require_fails_closed(self):
        # globalThis.require(...) is an indirect loader -> fail closed
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", 'const m = globalThis.require("./x");\n')
        diags = va.check_typescript_dependencies(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_DEP_TS, self._ids(diags))
        self.assertTrue(any("nonliteral" in d.message for d in diags), diags)

    def test_63_ts_parenthesized_kind_comparison(self):
        # (obj.kind) === 'evidence' — balanced parenthesized operand normalized
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "if ((obj.kind) === 'evidence') return true;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)

    def test_64_ts_parenthesized_mirror_comparison(self):
        # 'evidence' === (obj.kind) — parenthesized right operand, mirror direction
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "if ('evidence' === (obj.kind)) return true;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)

    def test_65_ts_optional_chain_kind_comparison(self):
        # obj?.kind === 'evidence' — optional-chain member access
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "if (obj?.kind === 'evidence') return true;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)

    def test_66_ts_optional_chain_mirror_comparison(self):
        # 'evidence' === obj?.kind — mirror direction with optional chain
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "if ('evidence' === obj?.kind) return true;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)

    def test_67_ts_parenthesized_optional_chain(self):
        # (obj?.kind) === 'evidence' — paren wrapping + optional chain together
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "if ((obj?.kind) === 'evidence') return true;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)

    def test_68_ts_call_paren_not_stripped(self):
        # foo(x) === 'evidence' — a call paren must NOT be stripped (foo is a callee);
        # the operand is the call result, not a kind operand -> not flagged
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "if (foo(x) === 'evidence') return true;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertNotIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags),
                         "call paren must not be mistaken for a wrapping operand")

    def test_69_ts_nested_paren_kind_comparison(self):
        # ((obj.kind)) === 'evidence' — nested wrapping parens stripped
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "if (((obj.kind)) === 'evidence') return true;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)

    def test_70_ts_subscript_kind_optional_chain(self):
        # obj?.['kind'] === 'evidence' — optional-chain subscript: a kind operand
        cfg = base_config()
        self._write("adapters/pi/src/c.ts", "if (obj?.['kind'] === 'evidence') return true;\n")
        diags = va.check_typescript_direct_evidence_approval(cfg, self.pkg, self._ts_files(cfg))
        self.assertIn(va.RULE_FORBIDDEN_SYMBOL, self._ids(diags))
        self.assertTrue(any("evidence" in d.message for d in diags), diags)


if __name__ == "__main__":
    unittest.main(verbosity=2)
