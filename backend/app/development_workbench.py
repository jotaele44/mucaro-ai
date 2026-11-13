"""Pure, model-optional planning document compiler.

This is an adapter to the existing execution gate and work_runs runtime.
It does not approve actions, enqueue runs or create a second orchestration authority.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA_VERSION = "mucaro-workbench-plan/1.0"
STAGES = (
    ("inspect", "Inspect source and freeze exact baseline"),
    ("inventory", "Inventory capabilities, dependencies and gaps"),
    ("design", "Specify contracts, acceptance tests and rollback"),
    ("implement", "Implement only approved gaps on isolated branch"),
    ("verify", "Run positive, negative and integration tests"),
    ("certify", "Certify exact artifacts only on independent full PASS"),
)
REQUIRED_EVIDENCE = ("source_commit", "source_manifest_sha256", "test_receipts", "rollback_receipt", "artifact_sha256")


class WorkbenchPlanError(ValueError):
    pass


def compile_plan(
    objective: str, *, source_commit: str | None = None,
    features: list[str] | None = None, approval: bool = False,
) -> dict[str, Any]:
    """Return a deterministic, non-executing plan; never infer approval or readiness."""
    if not isinstance(objective, str) or not objective.strip():
        raise WorkbenchPlanError("OBJECTIVE_REQUIRED")
    if len(objective) > 20000:
        raise WorkbenchPlanError("OBJECTIVE_TOO_LONG")
    if source_commit is not None and (
        not isinstance(source_commit, str) or len(source_commit) != 40
        or any(c not in "0123456789abcdefABCDEF" for c in source_commit)
    ):
        raise WorkbenchPlanError("SOURCE_COMMIT_MUST_BE_FULL_SHA1")
    if features is None:
        features = []
    if not isinstance(features, list) or any(not isinstance(f, str) or not f.strip() for f in features):
        raise WorkbenchPlanError("FEATURES_MUST_BE_NONEMPTY_STRINGS")
    unique_features = sorted(set(f.strip() for f in features))
    if len(unique_features) > 2000:
        raise WorkbenchPlanError("FEATURE_COUNT_EXCEEDED")
    batches = [
        {"id": f"B{i:02d}", "stage": key, "objective": description,
         "status": "NOT_EXECUTED", "approval_required": key in {"implement", "certify"}}
        for i, (key, description) in enumerate(STAGES, 1)
    ]
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "objective": objective.strip(),
        "source_commit": source_commit.lower() if source_commit else None,
        "features": unique_features,
        "feature_count": len(unique_features),
        "batches": batches,
        "status": "CANDIDATE_NOT_EXECUTION",
        "execution_authorized": False,
        "operator_approval_recorded": bool(approval),
        "certification": "BLOCKED",
        "missing_evidence": list(REQUIRED_EVIDENCE),
        "invariants": {
            "batch_count_closes": len(batches) == len(STAGES),
            "feature_count_closes": len(unique_features) == len(set(unique_features)),
            "no_implicit_execution": all(x["status"] == "NOT_EXECUTED" for x in batches),
        },
    }
    # Exclude digest from own canonical input.
    payload = json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    plan["plan_sha256"] = hashlib.sha256(payload).hexdigest()
    return plan
