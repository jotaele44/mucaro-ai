from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.tools import (
    ToolRuntimeError,
    approve_tool_call,
    list_tool_calls,
    list_tool_audit_events,
    list_tools,
    request_tool_call,
    run_tool_call,
)
from backend.app.web_browsing import WebFetchPolicy


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
    assert by_name["web.fetch"]["requires_approval"] is True
    assert by_name["web.fetch"]["mutates_state"] is True
    assert by_name["web.fetch_session"]["requires_approval"] is True
    assert by_name["web.fetch_session"]["mutates_state"] is True


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


def test_tool_runtime_executes_governed_web_fetch(monkeypatch, tmp_path: Path):
    cfg = make_settings(tmp_path)
    calls = []

    def fake_fetch(url: str, *, cfg: Settings, policy: WebFetchPolicy):
        calls.append((url, cfg, policy))
        return {
            "status": "WEB_FETCH_SNAPSHOT_OK",
            "snapshot_path": str(tmp_path / "snapshot.json"),
            "snapshot_sha256": "abc123",
        }

    monkeypatch.setattr("backend.app.tools.fetch_web_snapshot", fake_fetch)
    requested = request_tool_call(
        "web.fetch",
        {"url": "https://example.com/report", "max_bytes": 5000},
        cfg=cfg,
        requested_by="test",
    )
    tool_call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")

    executed = run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert executed["status"] == "TOOL_CALL_EXECUTED"
    assert executed["result"]["status"] == "WEB_FETCH_SNAPSHOT_OK"
    assert calls[0][0] == "https://example.com/report"
    assert calls[0][2].max_bytes == 5000
    events = list_tool_audit_events(cfg)["events"]
    assert events[-1]["event"] == "TOOL_CALL_EXECUTED"
    assert events[-1]["result_status"] == "WEB_FETCH_SNAPSHOT_OK"


def test_tool_runtime_executes_governed_web_fetch_session(monkeypatch, tmp_path: Path):
    cfg = make_settings(tmp_path)
    calls = []

    def fake_fetch_session(urls, *, purpose, cfg, fetch_policy, session_policy):
        calls.append((urls, purpose, cfg, fetch_policy, session_policy))
        return {
            "status": "WEB_BROWSE_SESSION_OK",
            "session_id": "websession_test",
            "receipt_path": str(tmp_path / "session.json"),
            "receipt_sha256": "abc123",
            "selected_count": 2,
            "successful_fetch_count": 2,
        }

    monkeypatch.setattr("backend.app.tools.fetch_web_browse_session", fake_fetch_session)
    requested = request_tool_call(
        "web.fetch_session",
        {
            "urls": ["https://example.com/one", "https://example.com/two"],
            "purpose": "compare sources",
            "max_pages": 2,
            "require_same_host": True,
            "max_bytes": 5000,
        },
        cfg=cfg,
        requested_by="test",
    )
    tool_call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")

    executed = run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert executed["status"] == "TOOL_CALL_EXECUTED"
    assert executed["result"]["status"] == "WEB_BROWSE_SESSION_OK"
    assert calls[0][0] == ["https://example.com/one", "https://example.com/two"]
    assert calls[0][1] == "compare sources"
    assert calls[0][3].max_bytes == 5000
    assert calls[0][4].require_same_host is True
    events = list_tool_audit_events(cfg)["events"]
    assert events[-1]["event"] == "TOOL_CALL_EXECUTED"
    assert events[-1]["result_status"] == "WEB_BROWSE_SESSION_OK"


def test_tool_runtime_rejects_web_fetch_session_without_url_array(tmp_path: Path):
    cfg = make_settings(tmp_path)
    requested = request_tool_call("web.fetch_session", {"urls": "https://example.com"}, cfg=cfg, requested_by="test")
    tool_call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")

    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert exc.value.status == "ERROR_WEB_SESSION_URLS_REQUIRED"


def test_tool_runtime_audits_failed_web_fetch(tmp_path: Path):
    cfg = make_settings(tmp_path)
    requested = request_tool_call(
        "web.fetch",
        {"url": "http://localhost:8000/private"},
        cfg=cfg,
        requested_by="test",
    )
    tool_call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")

    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert exc.value.status == "ERROR_WEB_PRIVATE_HOST_BLOCKED"
    calls = list_tool_calls(cfg)["tool_calls"]
    assert calls[0]["status"] == "FAILED"
    assert calls[0]["result"]["status"] == "ERROR_WEB_PRIVATE_HOST_BLOCKED"
    events = list_tool_audit_events(cfg)["events"]
    assert events[-1]["event"] == "TOOL_CALL_FAILED"
    assert events[-1]["result_status"] == "ERROR_WEB_PRIVATE_HOST_BLOCKED"



def test_tool_runtime_executes_governed_live_web_search(monkeypatch, tmp_path: Path):
    cfg = make_settings(tmp_path)
    calls = []

    def fake_search(query, **kwargs):
        calls.append((query, kwargs))
        return {
            "status": "WEB_SEARCH_OK",
            "search_id": "websearch_test",
            "receipt_path": str(tmp_path / "search.json"),
            "receipt_sha256": "abc123",
        }

    monkeypatch.setattr("backend.app.tools.search_web_brave", fake_search)
    requested = request_tool_call(
        "web.search",
        {
            "query": "Puerto Rico hydrology",
            "count": 5,
            "country": "PR",
            "search_lang": "en",
            "allow_live_search": True,
        },
        cfg=cfg,
        requested_by="test",
    )
    tool_call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")

    executed = run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert executed["status"] == "TOOL_CALL_EXECUTED"
    assert executed["result"]["status"] == "WEB_SEARCH_OK"
    assert calls[0][0] == "Puerto Rico hydrology"
    assert calls[0][1]["count"] == 5
    assert calls[0][1]["country"] == "PR"
    assert calls[0][1]["allow_live_search"] is True


def test_tool_runtime_web_search_stays_fail_closed_without_live_permission(monkeypatch, tmp_path: Path):
    cfg = make_settings(tmp_path)

    def fake_search(query, **kwargs):
        if kwargs.get("allow_live_search") is not True:
            from backend.app.web_search import WebSearchError
            raise WebSearchError("ERROR_WEB_SEARCH_LIVE_NOT_ALLOWED", "explicit permission required")
        return {"status": "WEB_SEARCH_OK"}

    monkeypatch.setattr("backend.app.tools.search_web_brave", fake_search)
    requested = request_tool_call(
        "web.search",
        {"query": "Puerto Rico hydrology"},
        cfg=cfg,
        requested_by="test",
    )
    tool_call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")

    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")
    assert exc.value.status == "ERROR_WEB_SEARCH_LIVE_NOT_ALLOWED"
