import json
from pathlib import Path

from backend.app.cli import main
from backend.app.config import Settings


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
    assert "serve" in output


def test_cli_version(capsys):
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert output == "mucaro 0.1.12\n"


def test_cli_validate_text_output(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["validate", "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "JSON_OK" in output
    assert "checked=0" in output


def test_cli_index_status_reports_missing_index(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["index", "status", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "INDEX_MISSING"
    assert payload["chunk_count"] == 0
    assert payload["stale"] is False


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
    source_id = sources_payload["sources"][0]["source_id"]

    assert main(["sources", "reindex", "--format", "text"], cfg=cfg) == 0
    reindex_output = capsys.readouterr().out
    assert "REINDEX_OK" in reindex_output
    assert "index_status=INDEX_OK" in reindex_output

    assert main(["sources", "delete", source_id, "--format", "json"], cfg=cfg) == 0
    delete_payload = json.loads(capsys.readouterr().out)
    assert delete_payload["status"] == "SOURCE_DELETE_OK"
    assert delete_payload["remaining_sources"] == 0

    assert main(["sources", "list", "--format", "text"], cfg=cfg) == 0
    list_output = capsys.readouterr().out
    assert "SOURCES_OK" in list_output
    assert "sources=0" in list_output


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
