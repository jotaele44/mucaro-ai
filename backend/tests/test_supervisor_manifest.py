"""N03 run manifest compatibility is read-only and fail-closed."""
import pytest

from app.supervisor_manifest import SupervisorManifestError, project_supervisor_manifest
from app.supervisor_queue import claim, enqueue, finish, get_task, reconcile_expired

SHA = "a" * 40
TIME = "2026-11-13T18:00:00Z"


def _create(db, *, mutates=False):
    return enqueue(db, task_id="task1", idempotency_key="idem1",
                   payload={"source_sha": SHA, "run_id": "run1"}, mutates_state=mutates, now=10)


def _project(row):
    return project_supervisor_manifest(row, run_id="run1", started_at=TIME, expected_source_sha=SHA)


def test_queue_to_execution_to_verified_completion(tmp_path):
    db = tmp_path / "queue.db"
    assert _project(_create(db))["state"] == "PLANNING"
    leased = claim(db, worker_id="worker", now=10)
    assert _project(leased)["state"] == "EXECUTING"
    assert finish(db, task_id="task1", worker_id="worker", generation=leased["generation"],
                  receipt={"verified": True, "output_artifact_ids": ["sha256:abc"]},
                  succeeded=True, now=11)
    manifest = _project(get_task(db, "task1"))
    assert manifest["state"] == "COMPLETE"
    assert manifest["outputs"] == ["sha256:abc"]
    assert manifest["inputs"] == [SHA]


def test_unverified_completion_never_becomes_complete(tmp_path):
    db = tmp_path / "queue.db"
    _create(db)
    leased = claim(db, worker_id="worker", now=10)
    assert finish(db, task_id="task1", worker_id="worker", generation=leased["generation"],
                  receipt={"exit_code": 0}, succeeded=True, now=11)
    manifest = _project(get_task(db, "task1"))
    assert manifest["state"] == "BLOCKED"
    assert manifest["errors"] == ["COMPLETION_RECEIPT_UNVERIFIED"]


def test_expired_mutation_requires_reconciliation(tmp_path):
    db = tmp_path / "queue.db"
    _create(db, mutates=True)
    claim(db, worker_id="worker", lease_seconds=3, now=10)
    reconcile_expired(db, now=14)
    manifest = _project(get_task(db, "task1"))
    assert manifest["state"] == "BLOCKED"
    assert "EXTERNAL_EFFECT_UNRECONCILED" in manifest["errors"]


def test_rejects_source_or_run_drift(tmp_path):
    db = tmp_path / "queue.db"
    row = _create(db)
    with pytest.raises(SupervisorManifestError, match="mismatch"):
        project_supervisor_manifest(row, run_id="other", started_at=TIME, expected_source_sha=SHA)
    with pytest.raises(SupervisorManifestError, match="mismatch"):
        project_supervisor_manifest(row, run_id="run1", started_at=TIME, expected_source_sha="b" * 40)
