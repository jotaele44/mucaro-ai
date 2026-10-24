from __future__ import annotations

from pathlib import Path

from backend.app.config import Settings
from backend.app.tools import approve_tool_call, request_tool_call, run_tool_call


def make_settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "tools.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def test_task_mutation_uses_approval_ledger_and_list_is_auditable(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    requested = request_tool_call(
        "task.create",
        {"task_id": "tool-task", "title": "Use governed task tool", "priority": "high"},
        cfg=cfg,
        requested_by="test",
    )
    call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(call_id, cfg=cfg, approved_by="test")
    created = run_tool_call(call_id, cfg=cfg, executed_by="test")
    assert created["result"]["status"] == "TASK_CREATED"
    assert created["result"]["task"]["task_id"] == "tool-task"

    listed = request_tool_call("task.list", {}, cfg=cfg, requested_by="test")
    list_id = listed["tool_call"]["tool_call_id"]
    approve_tool_call(list_id, cfg=cfg, approved_by="test")
    result = run_tool_call(list_id, cfg=cfg, executed_by="test")
    assert result["result"]["status"] == "TASK_SUMMARY_OK"
    assert result["result"]["tasks"][0]["task_id"] == "tool-task"
