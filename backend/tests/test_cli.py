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


def test_cli_validate_text_output(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["validate", "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "JSON_OK" in output
    assert "checked=0" in output


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


def test_cli_ask_no_sources_returns_error_status(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["ask", "What is source lineage?", "--format", "json", "--no-persist"], cfg=cfg) == 2
    output = capsys.readouterr().out
    assert "ERROR_NO_SOURCES" in output


def test_cli_ask_invalid_top_k_returns_error_status(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["ask", "What is source lineage?", "--top-k", "0", "--format", "json"], cfg=cfg) == 2
    output = capsys.readouterr().out
    assert "ERROR_INVALID_TOP_K" in output
