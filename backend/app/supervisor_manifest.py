"""Read-only, schema-validated projection of supervisor task state into N03 run records.

This adapter does not authorize execution or change canonical work_runs. A
completed queue task is not certified unless its receipt explicitly verifies it.
"""
from __future__ import annotations

from typing import Any

from .execution_records import build_run_manifest
from .supervisor_queue import STATES


class SupervisorManifestError(RuntimeError):
    pass


_MANIFEST_STATES = {
    "QUEUED": "PLANNING",
    "LEASED": "EXECUTING",
    "NEEDS_RECONCILIATION": "BLOCKED",
    "FAILED": "FAILED",
}


def project_supervisor_manifest(
    task: dict[str, Any],
    *,
    run_id: str,
    started_at: str,
    expected_source_sha: str,
) -> dict[str, Any]:
    """Project a persisted task without promoting unverified outcomes to COMPLETE."""
    if not isinstance(task, dict):
        raise SupervisorManifestError("task record required")
    if not run_id or not started_at:
        raise SupervisorManifestError("run identity and timestamp required")
    if len(expected_source_sha) != 40 or any(c not in "0123456789abcdef" for c in expected_source_sha):
        raise SupervisorManifestError("exact lowercase source SHA required")
    task_id = task.get("task_id")
    state = task.get("state")
    payload = task.get("payload")
    if not isinstance(task_id, str) or not task_id or state not in STATES or not isinstance(payload, dict):
        raise SupervisorManifestError("malformed task record")
    if payload.get("source_sha") != expected_source_sha or payload.get("run_id") != run_id:
        raise SupervisorManifestError("source/run identity mismatch")
    receipt = task.get("receipt")
    if receipt is not None and not isinstance(receipt, dict):
        raise SupervisorManifestError("invalid receipt")
    receipt = receipt or {}
    if state == "COMPLETED":
        manifest_state = "COMPLETE" if receipt.get("verified") is True else "BLOCKED"
    else:
        manifest_state = _MANIFEST_STATES.get(state)
        if manifest_state is None:
            raise SupervisorManifestError("unsupported task state")
    outputs = receipt.get("output_artifact_ids", [])
    if not isinstance(outputs, list) or not all(isinstance(s, str) and s for s in outputs):
        raise SupervisorManifestError("invalid output identities")
    errors = []
    if state == "NEEDS_RECONCILIATION":
        errors.append("EXTERNAL_EFFECT_UNRECONCILED")
    elif state == "COMPLETED" and manifest_state == "BLOCKED":
        errors.append("COMPLETION_RECEIPT_UNVERIFIED")
    elif state == "FAILED":
        errors.append("TASK_FAILED")
    return build_run_manifest(
        run_id=run_id,
        task_id=task_id,
        state=manifest_state,
        inputs=[expected_source_sha],
        outputs=outputs,
        errors=errors,
        started_at=started_at,
        completed_at=receipt.get("finished_at") if manifest_state in {"COMPLETE", "FAILED"} else None,
    )
