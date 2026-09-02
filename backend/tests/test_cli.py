import json
from pathlib import Path

from backend.app.cli import main
from backend.app.config import Settings


def make_settings(root: Path, *, operator_token: str | None = None) -> Settings:
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
        operator_token=operator_token,
    )


def test_cli_ingest_and_ask(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    input_dir = tmp_path / "data" / "input"
    input_dir.mkdir(parents=True)
    sample = input_dir / "sample.md"
    sample.write_text("Source lineage requires cited chunk IDs and source IDs.", encoding="utf-8")

    assert main(["ingest", "data/input", "--format", "json"], cfg=cfg) == 0
    ingest_output = capsys.readouterr().out
    assert "INGEST_FOLDER_OK" in ingest_output

    assert main(["ask", "What does source lineage require?", "--strategy", "persistent", "--format", "text"], cfg=cfg) == 0
    ask_output = capsys.readouterr().out
    assert "CITATION_OK" in ask_output
    assert "chunk_id=" in ask_output


def test_cli_plan(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["plan", "Add", "CLI", "capability", "--input", "mucaro-ai"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "codebase_build" in output


def test_cli_plan_text_output(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["plan", "Add", "CLI", "capability", "--input", "mucaro-ai", "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "OK" in output
    assert "active_vector=codebase_build" in output
    assert "recommended_mode=CLAUDE_CODE_MODE" in output
    assert "readiness_percent=" in output


def test_cli_help_lists_serve(capsys):
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert "certify" in output
    assert "serve" in output


def test_cli_version(capsys):
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert output == "mucaro 0.1.22\n"


def test_cli_validate_text_output(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["validate", "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "JSON_OK" in output
    assert "checked=0" in output


def test_cli_doctor_reports_warning_state_without_sources(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["doctor", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "DOCTOR_WARN"
    assert payload["summary"]["source_count"] == 0
    assert payload["summary"]["index_status"] == "INDEX_MISSING"
    assert payload["summary"]["semantic_store_status"] == "SEMANTIC_STORE_MISSING"
    assert payload["summary"]["tool_count"] >= 1
    assert any(check["name"] == "source_library" and check["status"] == "WARN" for check in payload["checks"])
    assert any(check["name"] == "tool_runtime" and check["status"] == "PASS" for check in payload["checks"])

    assert main(["doctor", "--strict", "--format", "text"], cfg=cfg) == 2
    strict_output = capsys.readouterr().out
    assert "DOCTOR_WARN" in strict_output
    assert "recommendation=" in strict_output


def test_cli_doctor_reports_ready_state_after_local_setup(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path, operator_token="local-token")
    input_dir = tmp_path / "data" / "input"
    input_dir.mkdir(parents=True)
    sample = input_dir / "sample.md"
    sample.write_text("Source lineage requires cited chunk IDs and source IDs.", encoding="utf-8")

    assert main(["ingest", "data/input", "--format", "json"], cfg=cfg) == 0
    capsys.readouterr()

    assert main(["doctor", "--strict", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "DOCTOR_OK"
    assert payload["summary"]["source_count"] == 1
    assert payload["summary"]["index_status"] == "INDEX_OK"
    assert payload["summary"]["semantic_store_status"] == "SEMANTIC_STORE_OK"
    assert payload["summary"]["auth_required"] is True
    assert payload["summary"]["memory_count"] == 0


def test_cli_product_audit_reports_open_goal_gaps(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["audit", "product", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PRODUCT_AUDIT_OK"
    assert payload["readiness_percent"] == 100.0
    by_id = {row["requirement_id"]: row for row in payload["requirements"]}
    assert by_id["permission_gated_user_directory"]["status"] == "PASS"
    assert by_id["source_lineage_citations"]["status"] == "PASS"
    assert by_id["semantic_vector_store"]["status"] == "PASS"
    assert by_id["tool_use_and_actions"]["status"] == "PASS"
    assert by_id["multimodal_interface"]["status"] == "PASS"
    assert by_id["personalization_memory"]["status"] == "PASS"
    assert by_id["production_security"]["status"] == "PASS"
    assert payload["next_recommended_vector"] == "complete"

    assert main(["audit", "product", "--strict", "--format", "text"], cfg=cfg) == 0
    strict_output = capsys.readouterr().out
    assert "PRODUCT_AUDIT_OK" in strict_output
    assert "PASS production_security" in strict_output


def test_cli_certify_status_reports_release_readiness(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["certify", "status", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "CERTIFICATION_WARN"
    assert payload["version"] == "0.1.22"
    assert payload["project_root"] == str(tmp_path)
    assert payload["product_audit_status"] == "PRODUCT_AUDIT_OK"
    assert payload["json_status"] == "JSON_OK"
    assert payload["git"]["head_sha"] is None
    gates = {gate["name"]: gate for gate in payload["gates"]}
    assert gates["product_audit"]["status"] == "PASS"
    assert gates["json_validation"]["status"] == "PASS"
    assert gates["git_repository"]["status"] == "WARN"
    assert gates["baseline_tag"]["status"] == "WARN"
    assert payload["next_commands"] == [
        "python3 scripts/certify_release.py",
        "python3 scripts/certify_fresh_clone.py --ref main",
    ]

    assert main(["certify", "status", "--strict", "--format", "text"], cfg=cfg) == 2
    strict_output = capsys.readouterr().out
    assert "CERTIFICATION_WARN" in strict_output
    assert "WARN git_repository git metadata unavailable" in strict_output
    assert "command=python3 scripts/certify_release.py" in strict_output


def test_cli_index_status_reports_missing_index(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["index", "status", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "INDEX_MISSING"
    assert payload["chunk_count"] == 0
    assert payload["stale"] is False
    assert payload["semantic_store"]["status"] == "SEMANTIC_STORE_MISSING"


def test_cli_sources_list_reindex_and_delete(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    input_dir = tmp_path / "data" / "input"
    input_dir.mkdir(parents=True)
    sample = input_dir / "sample.md"
    sample.write_text("Local assistants need approved files and rebuildable indexes.", encoding="utf-8")

    assert main(["ingest", "data/input", "--format", "json"], cfg=cfg) == 0
    capsys.readouterr()

    assert main(["sources", "list", "--format", "json"], cfg=cfg) == 0
    sources_payload = json.loads(capsys.readouterr().out)
    assert sources_payload["status"] == "SOURCES_OK"
    assert sources_payload["index"]["status"] == "INDEX_OK"
    assert sources_payload["index"]["semantic_store"]["status"] == "SEMANTIC_STORE_OK"
    source_id = sources_payload["sources"][0]["source_id"]

    assert main(["sources", "reindex", "--format", "text"], cfg=cfg) == 0
    reindex_output = capsys.readouterr().out
    assert "REINDEX_OK" in reindex_output
    assert "index_status=INDEX_OK" in reindex_output
    assert "semantic_store_status=SEMANTIC_STORE_OK" in reindex_output

    assert main(["sources", "delete", source_id, "--format", "json"], cfg=cfg) == 0
    delete_payload = json.loads(capsys.readouterr().out)
    assert delete_payload["status"] == "SOURCE_DELETE_OK"
    assert delete_payload["remaining_sources"] == 0

    assert main(["sources", "list", "--format", "text"], cfg=cfg) == 0
    list_output = capsys.readouterr().out
    assert "SOURCES_OK" in list_output
    assert "sources=0" in list_output


def test_cli_conversations_ask_and_runs(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    input_dir = tmp_path / "data" / "input"
    input_dir.mkdir(parents=True)
    sample = input_dir / "sample.md"
    sample.write_text("Source lineage requires cited chunk IDs and source IDs.", encoding="utf-8")

    assert main(["ingest", "data/input", "--format", "json"], cfg=cfg) == 0
    capsys.readouterr()

    assert main(["conversations", "create", "--title", "Lineage", "--format", "json"], cfg=cfg) == 0
    created = json.loads(capsys.readouterr().out)
    conversation_id = created["conversation_id"]
    assert created["title"] == "Lineage"

    assert main(["conversations", "list", "--format", "text"], cfg=cfg) == 0
    list_output = capsys.readouterr().out
    assert "CONVERSATIONS_OK" in list_output
    assert conversation_id in list_output

    assert (
        main(
            [
                "conversations",
                "ask",
                conversation_id,
                "What",
                "does",
                "source",
                "lineage",
                "require?",
                "--strategy",
                "persistent",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    asked = json.loads(capsys.readouterr().out)
    assert asked["answer"]["citation_status"] == "CITATION_OK"
    assert asked["conversation"]["conversation_id"] == conversation_id
    assert len(asked["conversation"]["turns"]) == 2
    run_id = asked["answer"]["run_id"]

    assert main(["runs", "summary", "--format", "json"], cfg=cfg) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "RUN_HISTORY_OK"
    assert summary["run_count"] == 1
    assert summary["latest_run_id"] == run_id

    assert main(["runs", "show", run_id, "--format", "text"], cfg=cfg) == 0
    run_output = capsys.readouterr().out
    assert "CITATION_OK" in run_output
    assert "persistent_tfidf_v1" in run_output

    assert main(["conversations", "rename", conversation_id, "Renamed", "Lineage", "--format", "json"], cfg=cfg) == 0
    renamed = json.loads(capsys.readouterr().out)
    assert renamed["title"] == "Renamed Lineage"

    assert main(["conversations", "delete", conversation_id, "--format", "json"], cfg=cfg) == 0
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["status"] == "CONVERSATION_DELETE_OK"

    assert main(["runs", "show", run_id, "--format", "json"], cfg=cfg) == 0
    preserved = json.loads(capsys.readouterr().out)
    assert preserved["run_id"] == run_id


def test_cli_conversation_and_run_missing_ids(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["conversations", "show", "conv_missing", "--format", "json"], cfg=cfg) == 2
    missing_conversation = json.loads(capsys.readouterr().out)
    assert missing_conversation["status"] == "ERROR_CONVERSATION_NOT_FOUND"

    assert main(["runs", "show", "run_missing", "--format", "json"], cfg=cfg) == 2
    missing_run = json.loads(capsys.readouterr().out)
    assert missing_run["status"] == "ERROR_RUN_NOT_FOUND"


def test_cli_tools_require_approval_and_audit_execution(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["tools", "list", "--format", "text"], cfg=cfg) == 0
    tools_output = capsys.readouterr().out
    assert "TOOLS_OK" in tools_output
    assert "source_library.status" in tools_output
    assert "memory.list" in tools_output

    assert (
        main(
            [
                "tools",
                "request",
                "source_library.status",
                "--reason",
                "readiness check",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    requested = json.loads(capsys.readouterr().out)
    tool_call_id = requested["tool_call"]["tool_call_id"]
    assert requested["tool_call"]["status"] == "PENDING_APPROVAL"

    assert main(["tools", "run", tool_call_id, "--format", "json"], cfg=cfg) == 2
    denied_run = json.loads(capsys.readouterr().err)
    assert denied_run["status"] == "ERROR_TOOL_APPROVAL_REQUIRED"

    assert main(["tools", "approve", tool_call_id, "--format", "json"], cfg=cfg) == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved["tool_call"]["status"] == "APPROVED"

    assert main(["tools", "run", tool_call_id, "--format", "json"], cfg=cfg) == 0
    executed = json.loads(capsys.readouterr().out)
    assert executed["status"] == "TOOL_CALL_EXECUTED"
    assert executed["result"]["status"] == "SOURCE_LIBRARY_STATUS_OK"

    assert main(["tools", "audit", "--format", "json"], cfg=cfg) == 0
    audit = json.loads(capsys.readouterr().out)
    assert [event["event"] for event in audit["events"]] == [
        "TOOL_CALL_REQUESTED",
        "TOOL_CALL_APPROVED",
        "TOOL_CALL_EXECUTED",
    ]


def test_cli_memory_controls_and_export(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["memory", "remember", "Prefer", "concise", "answers.", "--kind", "preference", "--format", "json"], cfg=cfg) == 0
    saved = json.loads(capsys.readouterr().out)
    memory_id = saved["memory"]["memory_id"]
    assert saved["status"] == "MEMORY_SAVED"
    assert saved["memory"]["enabled"] is True

    assert main(["memory", "list", "--format", "text"], cfg=cfg) == 0
    list_output = capsys.readouterr().out
    assert "MEMORY_OK" in list_output
    assert "enabled=1" in list_output
    assert memory_id in list_output

    assert main(["memory", "disable", memory_id, "--format", "json"], cfg=cfg) == 0
    disabled = json.loads(capsys.readouterr().out)
    assert disabled["memory"]["enabled"] is False

    assert main(["memory", "list", "--active-only", "--format", "json"], cfg=cfg) == 0
    active_only = json.loads(capsys.readouterr().out)
    assert active_only["memory_count"] == 0

    assert main(["memory", "enable", memory_id, "--format", "json"], cfg=cfg) == 0
    assert json.loads(capsys.readouterr().out)["memory"]["enabled"] is True

    assert main(["memory", "export", "--format", "json"], cfg=cfg) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["status"] == "MEMORY_EXPORT_OK"
    assert exported["memory_count"] == 1

    assert main(["memory", "delete", memory_id, "--format", "json"], cfg=cfg) == 0
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["status"] == "MEMORY_DELETE_OK"
    assert deleted["remaining_memories"] == 0


def test_cli_security_status_and_serve_public_bind_guard(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path, operator_token="x" * 32)

    assert main(["security", "status", "--host", "127.0.0.1", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "SECURITY_OK"

    assert main(["security", "status", "--host", "0.0.0.0", "--format", "text"], cfg=cfg) == 2
    output = capsys.readouterr().out
    assert "SECURITY_FAIL" in output
    assert "bind_host" in output

    called = False

    def fake_run(*args, **kwargs) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert main(["serve", "--host", "0.0.0.0"], cfg=cfg) == 2
    assert called is False


def test_cli_serve_passes_host_port_and_reload(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    captured = {}

    def fake_run(app: str, *, host: str, port: int, reload: bool) -> None:
        captured.update({"app": app, "host": host, "port": port, "reload": reload})

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert main(["serve", "--host", "127.0.0.1", "--port", "8123", "--reload"], cfg=cfg) == 0
    assert captured == {
        "app": "backend.app.main:app",
        "host": "127.0.0.1",
        "port": 8123,
        "reload": True,
    }


def test_cli_serve_rejects_invalid_port(capsys):
    try:
        main(["serve", "--port", "70000"])
    except SystemExit as exc:
        assert exc.code == 2
    error_output = capsys.readouterr().err
    assert "PORT_RANGE:1-65535" in error_output


def test_cli_ingest_missing_path_returns_structured_error(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["ingest", "missing.md", "--format", "json"], cfg=cfg) == 2
    error_output = capsys.readouterr().err
    assert "ERROR_INPUT_PATH_NOT_FOUND" in error_output
    assert "missing.md" in error_output


def test_cli_ingest_external_path_requires_permission(tmp_path: Path, capsys):
    project = tmp_path / "project"
    cfg = make_settings(project)
    external = tmp_path / "external"
    external.mkdir()
    sample = external / "sample.md"
    sample.write_text("External directory access is permission-gated.", encoding="utf-8")

    assert main(["ingest", str(external), "--format", "json"], cfg=cfg) == 2
    error_output = capsys.readouterr().err
    assert "ERROR_DIRECTORY_PERMISSION_REQUIRED" in error_output

    assert main(["permissions", "grant", str(external), "--yes", "--format", "json"], cfg=cfg) == 0
    grant_output = capsys.readouterr().out
    assert "DIRECTORY_PERMISSION_GRANTED" in grant_output

    assert main(["ingest", str(external), "--format", "json"], cfg=cfg) == 0
    ingest_output = capsys.readouterr().out
    assert "INGEST_FOLDER_OK" in ingest_output
    assert "PERMISSION_OK" in ingest_output


def test_cli_permission_grant_requires_confirmation_when_not_yes(tmp_path: Path, capsys):
    project = tmp_path / "project"
    cfg = make_settings(project)
    external = tmp_path / "external"
    external.mkdir()

    assert main(["permissions", "grant", str(external), "--format", "json"], cfg=cfg) == 2
    error_output = capsys.readouterr().err
    assert "ERROR_PERMISSION_CONFIRMATION_REQUIRED" in error_output


def test_cli_ask_no_sources_returns_error_status(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["ask", "What is source lineage?", "--format", "json", "--no-persist"], cfg=cfg) == 2
    output = capsys.readouterr().out
    assert "ERROR_NO_SOURCES" in output


def test_cli_ask_invalid_top_k_returns_error_status(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    try:
        main(["ask", "What is source lineage?", "--top-k", "0", "--format", "json"], cfg=cfg)
    except SystemExit as exc:
        assert exc.code == 2
    error_output = capsys.readouterr().err
    assert "TOP_K_RANGE:1-20" in error_output
