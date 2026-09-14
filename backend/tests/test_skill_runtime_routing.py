from __future__ import annotations

import os
from pathlib import Path

from backend.app.config import Settings
from backend.app.skill_runtime import activate_skill, execute_skill, mount_skill


def make_settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def test_skill_fabric_routes_local_worker_skill_out_of_process(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "do-not-forward")
    cfg = make_settings(tmp_path)
    skill_id = "mucaro/local-worker-health"
    mount_skill(skill_id, cfg=cfg)
    activation = activate_skill(skill_id, cfg=cfg)
    assert "runtime.local_worker" in activation["policy_decision"]["granted"]

    execution = execute_skill(skill_id, cfg=cfg)
    event = execution["execution"]
    assert event["execution_route"] == "LOCAL_WORKER"
    assert event["adapter_receipt"]["status"] == "LOCAL_WORKER_OK"
    assert event["runtime_identity"]["runtime"] == "LOCAL_WORKER"
    assert execution["result"]["status"] == "LOCAL_WORKER_HEALTH_OK"
    assert execution["result"]["pid"] != os.getpid()
    assert execution["result"]["sensitive_environment_present"] == []
