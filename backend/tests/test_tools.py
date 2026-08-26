from pathlib import Path

from backend.app.config import Settings
from backend.app.tools import (
    ToolRuntimeError,
    approve_tool_call,
    list_tool_audit_events,
    list_tools,
    request_tool_call,
    run_tool_call,
)


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


def test_tool_runtime_lists_allowlisted_tools():
    tools = list_tools()

    assert tools["status"] == "TOOLS_OK"
    by_name = {tool["name"]: tool for tool in tools["tools"]}
    assert by_name["source_library.status"]["requires_approval"] is True
    assert by_name["source_library.status"]["mutates_state"] is False


def test_tool_runtime_requires_approval_before_execution(tmp_path: Path):
    cfg = make_settings(tmp_path)
    requested = request_tool_call("source_library.status", cfg=cfg, requested_by="test")
    tool_call_id = requested["tool_call"]["tool_call_id"]

    try:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")
    except ToolRuntimeError as exc:
        assert exc.status == "ERROR_TOOL_APPROVAL_REQUIRED"
    else:
        raise AssertionError("expected approval failure")

    approved = approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")
    assert approved["tool_call"]["status"] == "APPROVED"

    executed = run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert executed["status"] == "TOOL_CALL_EXECUTED"
    assert executed["result"]["status"] == "SOURCE_LIBRARY_STATUS_OK"
    assert executed["tool_call"]["status"] == "EXECUTED"


def test_tool_runtime_denies_unknown_tools_and_audits_rejection(tmp_path: Path):
    cfg = make_settings(tmp_path)

    try:
        request_tool_call("shell.exec", {"cmd": "rm -rf /"}, cfg=cfg, requested_by="test")
    except ToolRuntimeError as exc:
        assert exc.status == "ERROR_TOOL_NOT_ALLOWED"
    else:
        raise AssertionError("expected allowlist failure")

    events = list_tool_audit_events(cfg)["events"]
    assert events[0]["event"] == "TOOL_REQUEST_REJECTED"
    assert events[0]["status"] == "ERROR_TOOL_NOT_ALLOWED"
