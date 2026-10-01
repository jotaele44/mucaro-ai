from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.permissions import grant_directory
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


def test_tool_registry_exposes_single_governed_gis_and_ocr_bridges() -> None:
    payload = list_tools()
    by_name = {row["name"]: row for row in payload["tools"]}

    assert by_name["gis.inspect"]["mutates_state"] is True
    assert by_name["gis.inspect"]["requires_approval"] is True
    assert by_name["gis.inspect"]["network"] == "none"
    assert by_name["gis.inspect"]["offline_availability"] == "AVAILABLE_LOCALLY"
    assert by_name["ocr.analyze_image"]["mutates_state"] is True
    assert by_name["ocr.analyze_image"]["requires_approval"] is True
    assert by_name["ocr.analyze_image"]["network"] == "none"
    assert [name for name in by_name if name.startswith("gis.")] == ["gis.inspect"]
    assert [name for name in by_name if name.startswith("ocr.")] == ["ocr.analyze_image"]


def test_gis_tool_resolves_relative_path_against_project_root(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    source = tmp_path / "fixtures" / "sample.geojson"
    source.parent.mkdir()
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    observed = {}

    def fake_inspect(path: Path, *, cfg: Settings):
        observed["path"] = path
        observed["cfg"] = cfg
        return {"status": "GIS_INSPECT_OK", "report_id": "gisreport_relative"}

    monkeypatch.setattr("backend.app.tools.inspect_geojson", fake_inspect)
    requested = request_tool_call(
        "gis.inspect",
        {"path": "fixtures/sample.geojson"},
        cfg=cfg,
        requested_by="test",
    )
    call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(call_id, cfg=cfg, approved_by="test")
    result = run_tool_call(call_id, cfg=cfg, executed_by="test")

    assert result["result"]["status"] == "GIS_INSPECT_OK"
    assert observed == {"path": source.resolve(), "cfg": cfg}


def test_gis_tool_requires_directory_grant_before_subsystem_execution(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    external = tmp_path.parent / f"{tmp_path.name}-gis-external"
    external.mkdir()
    source = external / "sample.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    monkeypatch.setattr(
        "backend.app.tools.inspect_geojson",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("GIS subsystem must not run before grant")),
    )

    requested = request_tool_call("gis.inspect", {"path": str(source)}, cfg=cfg, requested_by="test")
    call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(call_id, cfg=cfg, approved_by="test")

    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(call_id, cfg=cfg, executed_by="test")

    assert exc.value.status == "ERROR_PERMISSION_DENIED"
    call = next(row for row in list_tool_calls(cfg)["tool_calls"] if row["tool_call_id"] == call_id)
    assert call["status"] == "FAILED"
    assert call["result"]["status"] == "ERROR_PERMISSION_DENIED"


def test_gis_tool_executes_after_grant_and_propagates_settings(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    external = tmp_path.parent / f"{tmp_path.name}-gis-granted"
    external.mkdir()
    source = external / "sample.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    grant_directory(external, cfg, granted_by="test", reason="TGT-038 regression")
    observed = {}

    def fake_inspect(path: Path, *, cfg: Settings):
        observed["path"] = path
        observed["cfg"] = cfg
        return {"status": "GIS_INSPECT_OK", "report_id": "gisreport_test"}

    monkeypatch.setattr("backend.app.tools.inspect_geojson", fake_inspect)
    requested = request_tool_call("gis.inspect", {"path": str(source)}, cfg=cfg, requested_by="test")
    call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(call_id, cfg=cfg, approved_by="test")
    executed = run_tool_call(call_id, cfg=cfg, executed_by="test")

    assert executed["status"] == "TOOL_CALL_EXECUTED"
    assert executed["result"]["status"] == "GIS_INSPECT_OK"
    assert observed == {"path": source, "cfg": cfg}


def test_ocr_tool_requires_directory_grant_before_subsystem_execution(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    external = tmp_path.parent / f"{tmp_path.name}-ocr-external"
    external.mkdir()
    source = external / "sample.png"
    source.write_bytes(b"not-an-image")

    monkeypatch.setattr(
        "backend.app.tools.analyze_image_ocr",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("OCR subsystem must not run before grant")),
    )
    requested = request_tool_call("ocr.analyze_image", {"path": str(source)}, cfg=cfg, requested_by="test")
    call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(call_id, cfg=cfg, approved_by="test")

    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(call_id, cfg=cfg, executed_by="test")

    assert exc.value.status == "ERROR_PERMISSION_DENIED"


def test_ocr_tool_executes_after_grant_and_propagates_bounded_arguments(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    external = tmp_path.parent / f"{tmp_path.name}-ocr-granted"
    external.mkdir()
    source = external / "sample.png"
    source.write_bytes(b"fixture")
    grant_directory(external, cfg, granted_by="test", reason="TGT-038 regression")
    observed = {}

    def fake_ocr(path: Path, *, cfg: Settings, language: str, layout_mode: str, timeout_seconds: int):
        observed.update(
            path=path,
            cfg=cfg,
            language=language,
            layout_mode=layout_mode,
            timeout_seconds=timeout_seconds,
        )
        return {"status": "OCR_ANALYSIS_OK", "report_id": "ocrreport_test"}

    monkeypatch.setattr("backend.app.tools.analyze_image_ocr", fake_ocr)
    requested = request_tool_call(
        "ocr.analyze_image",
        {"path": str(source), "language": "eng", "layout_mode": "auto", "timeout_seconds": 45},
        cfg=cfg,
        requested_by="test",
    )
    call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(call_id, cfg=cfg, approved_by="test")
    executed = run_tool_call(call_id, cfg=cfg, executed_by="test")

    assert executed["result"]["status"] == "OCR_ANALYSIS_OK"
    assert observed == {
        "path": source,
        "cfg": cfg,
        "language": "eng",
        "layout_mode": "auto",
        "timeout_seconds": 45,
    }


def test_gis_and_ocr_tool_errors_preserve_typed_statuses(tmp_path: Path, monkeypatch) -> None:
    from backend.app.gis_processing import GISProcessingError
    from backend.app.ocr_analysis import OCRAnalysisError

    cfg = make_settings(tmp_path)
    gis_source = tmp_path / "bad.geojson"
    gis_source.write_text("{}", encoding="utf-8")
    ocr_source = tmp_path / "bad.png"
    ocr_source.write_bytes(b"x")

    monkeypatch.setattr(
        "backend.app.tools.inspect_geojson",
        lambda *args, **kwargs: (_ for _ in ()).throw(GISProcessingError("ERROR_GIS_TEST", "gis failure")),
    )
    monkeypatch.setattr(
        "backend.app.tools.analyze_image_ocr",
        lambda *args, **kwargs: (_ for _ in ()).throw(OCRAnalysisError("ERROR_OCR_TEST", "ocr failure")),
    )

    for tool_name, path, expected in (
        ("gis.inspect", gis_source, "ERROR_GIS_TEST"),
        ("ocr.analyze_image", ocr_source, "ERROR_OCR_TEST"),
    ):
        requested = request_tool_call(tool_name, {"path": str(path)}, cfg=cfg, requested_by="test")
        call_id = requested["tool_call"]["tool_call_id"]
        approve_tool_call(call_id, cfg=cfg, approved_by="test")
        with pytest.raises(ToolRuntimeError) as exc:
            run_tool_call(call_id, cfg=cfg, executed_by="test")
        assert exc.value.status == expected


def test_ocr_tool_rejects_unbounded_timeout_before_subsystem_execution(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    source = tmp_path / "sample.png"
    source.write_bytes(b"x")
    monkeypatch.setattr(
        "backend.app.tools.analyze_image_ocr",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("invalid arguments must fail before OCR")),
    )
    requested = request_tool_call(
        "ocr.analyze_image",
        {"path": str(source), "timeout_seconds": 91},
        cfg=cfg,
        requested_by="test",
    )
    call_id = requested["tool_call"]["tool_call_id"]
    approve_tool_call(call_id, cfg=cfg, approved_by="test")

    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(call_id, cfg=cfg, executed_by="test")

    assert exc.value.status == "ERROR_OCR_TIMEOUT_INVALID"


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


def test_a_hung_handler_times_out_and_is_never_recorded_as_executed(tmp_path: Path, monkeypatch):
    import threading
    from dataclasses import replace

    from backend.app import tools as tools_module
    from backend.app.error_categories import classify_error

    cfg = make_settings(tmp_path)
    release = threading.Event()
    finished = threading.Event()

    def hung_handler(arguments, cfg):
        release.wait(10)
        finished.set()
        return {"status": "LATE_RESULT"}

    slow = replace(tools_module.TOOL_REGISTRY["coding.patch_apply"], handler=hung_handler, timeout_seconds=0.2)
    monkeypatch.setitem(tools_module.TOOL_REGISTRY, "coding.patch_apply", slow)
    call_id = request_tool_call("coding.patch_apply", {"patch": "x"}, cfg=cfg, requested_by="test")["tool_call"]["tool_call_id"]
    approve_tool_call(call_id, cfg=cfg, approved_by="test")

    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(call_id, cfg=cfg, executed_by="test")
    assert exc.value.status == "ERROR_TOOL_TIMEOUT"
    assert classify_error(exc.value.status) == "TOOL_FAILURE"

    call = next(row for row in list_tool_calls(cfg)["tool_calls"] if row["tool_call_id"] == call_id)
    assert call["status"] == "FAILED"
    assert call["result"]["handler_abandoned"] is True
    assert call["result"]["side_effects"] == "UNKNOWN"  # a mutating tool may still finish after the timeout

    # The abandoned handler finishing later changes nothing: its result is discarded.
    release.set()
    assert finished.wait(5)
    call = next(row for row in list_tool_calls(cfg)["tool_calls"] if row["tool_call_id"] == call_id)
    assert call["status"] == "FAILED" and call["result"]["status"] == "ERROR_TOOL_TIMEOUT"
    events = [row for row in list_tool_audit_events(cfg)["events"] if row.get("tool_call_id") == call_id]
    assert [row["event"] for row in events][-2:] == ["TOOL_CALL_STARTED", "TOOL_CALL_FAILED"]
    assert events[-1]["status"] == "ERROR_TOOL_TIMEOUT"
    assert not any(row["event"] == "TOOL_CALL_EXECUTED" for row in events)


def test_remote_timeouts_stay_remote_service_errors():
    from backend.app.error_categories import classify_error

    assert classify_error("ERROR_WEB_FETCH_TIMEOUT") == "REMOTE_SERVICE_UNAVAILABLE"
