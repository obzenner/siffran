# Empirica 2.0 D3 implementation spec — architecture validator

**Status:** Frozen writer specification.

**Writer:** one GLM run, sole writer.

**Normative inputs:** D0, D1, D1-H, final DAG §§5–7 and D3, and accepted D2 registry.

## 1. Goal

Add a dependency-free static target-state validator that makes the v2 ownership/subtraction rules
machine-checkable. It must detect architecture drift without becoming a runtime framework or freezing
incidental file layout.

D3 does not refactor production runtime. The target-state validator is expected to report current
v1/compatibility violations until D6/D7 remove them. Therefore its standalone target is not composed
into `check-static` yet; D3-M composes it only after target-state implementation is green.

## 2. Files

Create:

```text
plugins/empirica/architecture.json
scripts/validate_empirica_architecture.py
scripts/tests/test_validate_empirica_architecture.py   # or existing script-test convention
```

Update:

```text
Makefile
```

Add target with help text:

```text
empirica-architecture-check  ## validate Empirica 2.0 target ownership, dependencies, subtraction, and code budget
```

Do not add it to `check-static`, `check`, or `check-ci` in D3. Record the exact future composition in
comments/help only if useful; D3-M performs composition.

## 3. Architecture config

`plugins/empirica/architecture.json` is small inert internal policy, not a public contract. Required
keys:

```text
version
runtime_roots
excluded_test_patterns
layers and allowed dependency directions
forbidden_paths
forbidden_runtime_protocols
forbidden_symbols/fields/actions
forbidden_cross_host_dependencies
effective_runtime baseline and maximum
thin_hook constraints
required_contract/profile references
```

Use exact target values:

- baseline effective runtime: 9,458 physical `.py`/`.ts` lines at `4257c8d`;
- target maximum: 9,457 (net deletion required; generated/vendor executable code counted);
- all `.py`/`.ts` under `plugins/empirica` count except test paths; do not exempt generated/vendor;
- core must not import application, adapters, or hooks;
- application must not import adapters or hooks;
- one host adapter must not import another host adapter;
- hooks may import/delegate to adapters but may not own domain logic and remain thin;
- adapters may translate application/wire/types but may not import/call domain adjudicators:
  `two_fold_verdict`, `state_of`, `adjudicate`, `coverage_check`, `contract_for_graph`;
- no public/runtime v1 protocol references after cutover;
- no persisted obligation-contract pointer/revision/history;
- no phase field/enum/action/machine;
- no separate reservations/audit tickets/nonces/ticket consumption;
- no direct `kind="evidence"` boolean approval;
- no persisted evidence-leaf composite `verdicts`;
- no migration/import compatibility surface.

Forbidden paths include the legacy migration module. Forbidden Make target/text includes
`migrate-legacy` after D6. Contract archive files under `contracts/empirica/v1` may exist until D6;
the target validator concerns runtime/package acceptance and final release inventory, not Git history.

Avoid generic regex-only scanning where AST/import parsing is reliable. Text checks must be scoped to
production/runtime/Make surfaces and report path:line.

## 4. Required checks

1. **Effective runtime inventory:** deterministic path list and line total; tests excluded, executable
   generated/vendor included; fail above configured max and print baseline/current/delta.
2. **Python dependency direction:** AST imports resolved for Empirica package, including relative,
   shorthand, `empirica.*`, and repository-qualified `plugins.empirica.*` spellings; report forbidden
   layer edges path:line. External modules with similar suffixes remain external.
3. **TypeScript dependency direction:** use one narrow lexical/token surface that preserves locations,
   skips comments and regex literals, distinguishes code from strings, and correctly decodes ordinary
   TypeScript quoted strings and non-interpolated template literals. Interpolated template expression
   bodies are tokenized recursively so policy-relevant comparisons remain visible. Recognize static
   import/export module specifiers, import-equals, literal `require(...)`, and literal dynamic
   `import(...)`; derive source and target host from paths and reject cross-host imports. Fail closed
   with a stable diagnostic on nonliteral direct `import(...)`/`require(...)` and common bracketed
   global `require` calls in host adapter runtime. Arbitrary eval/generated code is outside this static
   contract and must not be described as proven.
4. **Parse integrity:** a production Python parse failure is itself a fail-closed, path:line diagnostic;
   no AST-dependent check may silently skip malformed source.
5. **Thin hooks:** recursively discover hook Python files; each only bootstraps/delegates, has no domain
   imports, and has bounded recursively counted executable statements/functions. A nested file or one
   oversized function cannot evade the rule.
6. **Forbidden files/symbols:** exact path and AST names/fields/constants across all production Python
   where possible, plus narrow token/string-aware TypeScript equivalents. Detect direct
   `kind="evidence"` comparisons structurally and identically in Python and TypeScript: the nonliteral
   operand must be `kind`, `obj.kind`, `obj?.kind`, or `obj["kind"]`, allowing balanced wrapping
   parentheses and either comparison direction. Do not reject unrelated classification such as
   `source_type == "evidence"` or evidence-object construction. Moving phase, contract persistence,
   tickets/reservations/nonces, evidence approval, or composite verdicts to core or Pi cannot evade it.
7. **Forbidden protocol/action/state text:** scoped runtime checks for v1 acceptance and removed fields;
   exclude docs/tests/contracts/history explicitly.
8. **Adapter policy boundary:** detect direct imports/references to listed domain adjudicators.
9. **Public-contract alignment:** load accepted D2 registry/profile IDs and assert configured required
   protocol/profile references resolve; do not duplicate reason/action tables.
10. **Make lifecycle:** the exact target definition has a `##` help description, invokes one script,
   and commits/pushes/history rewrite are absent from every complete recipe line, including commands
   after `echo`/`printf` and semicolons.
11. **Deterministic diagnostics:** stable sorted output, nonzero on violation, concise summary by rule.

The validator may report several target-state violations in the current tree. It must not have a
baseline-exception allowlist that silently expires later; the violations are the red acceptance list
for D6/D7.

## 5. Synthetic tests

Tests invoke validator functions on temporary synthetic trees/configs and cover at least:

- valid minimal layered tree;
- core imports application using relative/shorthand and both supported package-qualified spellings;
- application imports adapter;
- Claude adapter imports Codex adapter;
- adapter imports/calls a domain adjudicator;
- forbidden migration file;
- independent direct `kind="evidence"` approval, core-relocated forbidden form, composite-verdict, and
  TypeScript forbidden-form cases (one mutation and diagnostic each);
- malformed production Python fails closed at its syntax-error line;
- v1 runtime protocol literal;
- persisted contract pointer/revision;
- phase and separate ticket/reservation fields;
- nested hook containing policy and a one-function hook exceeding the recursive statement bound;
- code moved to generated/vendor still counts;
- test files excluded;
- total exactly max passes; max+1 fails;
- unresolved required PublicContract/profile reference;
- TypeScript import-equals, literal require, and literal dynamic-import cross-host edges; comments and
  strings containing import text do not count, while nonliteral module loading fails closed;
- escaped quoted or plain-template TypeScript literals decode before exact forbidden-form comparison;
  regex literals containing policy-looking text do not count, while interpolated template expression
  bodies are checked;
- Python and TypeScript `kind`, property-kind, optional-property-kind, subscript-kind, and
  parenthesized evidence comparisons fail in either direction, while
  non-kind evidence classification and evidence-object construction pass;
- Make target lacking help annotation and mixed `echo/printf; git push` recipe bypasses;
- deterministic sorted diagnostics with path:line.

Load the real architecture config in synthetic tests and override only values needed by a case; do not
maintain a second copied policy object.

Every mutation has one expected rule ID/message fragment so tests cannot pass for an unrelated reason.

## 6. Red-first and verification

Use Make. Add a focused test target only if needed by project convention; otherwise the architecture
target may support `--self-test` while ordinary target validates the repository.

Required evidence:

```text
make empirica-architecture-check   # expected RED on current pre-D6/D7 tree, with named violations
<focused synthetic test target>    # GREEN
make check-static                  # remains GREEN; architecture target not composed yet
```

The target-state RED is intentional and must be recorded, not weakened with exceptions. Synthetic
validator tests and existing suites must be green.

## 7. Complexity constraints

- dependency-free stdlib Python;
- one CLI shell with explicit pure check inputs/results;
- no plugin/runtime import or execution;
- no generic plugin architecture framework;
- no graph database/rules DSL/code generation;
- config lists facts and boundaries, script owns algorithms;
- no single function loads files, decides all rules, and renders output;
- keep messages rule-coded and actionable.

## 8. Forbidden changes

Do not modify runtime application/core/adapters/hooks, contracts D2 content, plugin manifests/version,
D1/D1-H semantics, or quarantined two-fold files. Do not stage/commit/push/spawn.

## 9. Stop/escalate

Stop if an import direction or forbidden target requires a new architecture decision, existing Make
conventions cannot run synthetic tests without a new dependency, or a rule would necessarily false-
positive valid D1 behavior.

## 10. Handoff

Report files, rule IDs, synthetic cases, current target-state RED violations, commands/exits,
validator/config LOC, runtime LOC unchanged, residual false-positive risks, no staged/committed files,
and confirmation forbidden files were untouched.
