# Coding governance changeset

Canonical baseline: PR #21 on `main`.

Changed source surfaces:
- `backend/app/coding_checks.py`
- `backend/app/coding_review.py`
- `backend/app/coding_mutation.py`
- `backend/app/tools.py`

Regression surfaces:
- `backend/tests/test_coding_checks.py`
- `backend/tests/test_coding_review.py`
- `backend/tests/test_coding_mutation.py`
- `backend/tests/test_coding_tools.py`
- `backend/tests/test_coding_capability_status.py`

Governance surfaces:
- `_architecture/coding_regression_manifest.json`
- coding status, blocker, queue, scope, invariant, receipt, adjudication, and acceptance documents.

No live arbitrary repository-command executor is added.
