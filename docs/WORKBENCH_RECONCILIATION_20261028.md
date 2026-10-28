# PR #241 reconciliation against current main (2026-10-28)

State: PROVISIONAL / AUDIT_ONLY. Never infer release or feature certification.

## Frozen anchors
- PR #241 original merge base: e88038cd6f79ec43f1bce519e3a4ed16ad0e6997
- Current-main source for this branch: 4c72b081fbb851dd2f04c0ec76f86b83915a51bb
- Current-main compared with original PR branch: diverged (12 ahead, 133 behind) at retrieval.
- This is an independent, non-destructive reconciliation branch, NOT a rebase or merge of PR #241.

## Ported components (provenance: PR #241 branch)
- backend/app/development_workbench.py
- backend/app/execution_workbench.py
- backend/tests/test_development_workbench.py
- backend/tests/test_execution_workbench_contract.py
- scripts/standalone_dependency_audit.py
- backend/tests/test_standalone_dependency_audit.py
- docs/STANDALONE_INDEPENDENCE_EXECUTION.md

## Explicitly NOT ported
- TERMINAL_READY.txt: path already exists on current main with a different canonical quick-start contract. Retain current main; reconcile needed commands into an additive document only after reviewing the native task files.

## Boundaries
- Imported workbench compilers are PLAN_ONLY; they do not provide runtime API integration, UI parity, execution authorization, on-device model bundles or platform packaging.
- Existing work_runs.py, execution_gate.py and model_router.py remain canonical. No parallel state authority or integration claim.
- No mandatory external provider is introduced by the imported Python modules.
- Static inventory is AUDIT_ONLY, not proof of outbound-denial policy.
- Full current-head CI, tests, independent review and GUI consumer contract remain OPEN.
- Offline clean-host macOS/Windows, mobile execution, rollback and signed binary artifact certification remain BLOCKED.
- No skill/agent promotion or merge without full in-scope PASS and authorization.

## Test entrypoints
- python -m pytest backend/tests/test_development_workbench.py backend/tests/test_execution_workbench_contract.py backend/tests/test_standalone_dependency_audit.py -q
- python scripts/standalone_dependency_audit.py --root . --output artifacts/standalone-dependencies.json
- python scripts/release_check.py

## Stop conditions
- Any import/schema conflict, failed test or missing runtime/platform evidence keeps certification BLOCKED.
- PR #241 stays preserved and unmerged. Reconciliation is a candidate alternative until independently reviewed.
