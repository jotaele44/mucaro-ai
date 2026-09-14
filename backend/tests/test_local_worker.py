from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.local_worker import LocalWorkerError, run_local_worker


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_local_worker_executes_in_distinct_process_with_minimal_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-propagate")
    monkeypatch.setenv("MUCARO_AI_OPERATOR_TOKEN", "should-not-propagate")
    result = run_local_worker(
        project_root=tmp_path,
        skill_id="mucaro/local-worker-health",
        timeout_seconds=10,
    )
    assert result["status"] == "LOCAL_WORKER_OK"
    assert result["result"]["status"] == "LOCAL_WORKER_HEALTH_OK"
    assert result["result"]["pid"] != os.getpid()
    assert result["result"]["project_root"] == str(tmp_path)
    assert result["result"]["sensitive_environment_present"] == []
    assert result["network_allowed"] is False


def test_local_worker_rejects_in_process_skill() -> None:
    with pytest.raises(LocalWorkerError) as exc:
        run_local_worker(
            project_root=PROJECT_ROOT,
            skill_id="mucaro/intelligence-plane-health",
            timeout_seconds=10,
        )
    assert exc.value.status == "ERROR_LOCAL_WORKER_FAILED"
    assert "ERROR_LOCAL_WORKER_RUNTIME_MISMATCH" in exc.value.detail


def test_local_worker_rejects_oversized_request_before_spawn() -> None:
    with pytest.raises(LocalWorkerError) as exc:
        run_local_worker(
            project_root=PROJECT_ROOT,
            skill_id="mucaro/local-worker-health",
            arguments={"payload": "x" * 200},
            max_output_bytes=64,
        )
    assert exc.value.status == "ERROR_LOCAL_WORKER_REQUEST_TOO_LARGE"


def test_local_worker_requires_positive_limits() -> None:
    with pytest.raises(LocalWorkerError) as exc:
        run_local_worker(
            project_root=PROJECT_ROOT,
            skill_id="mucaro/local-worker-health",
            timeout_seconds=0,
        )
    assert exc.value.status == "ERROR_LOCAL_WORKER_LIMIT"
