from pathlib import Path

from backend.app.config import Settings
from backend.app.ingestion import ingest_folder


def test_ingest_folder_skips_internal_src_copies(tmp_path):
    input_dir = tmp_path / "input"
    processed_dir = tmp_path / "processed"
    export_dir = tmp_path / "exports"
    index_dir = tmp_path / "indexes"
    input_dir.mkdir()
    processed_dir.mkdir()
    export_dir.mkdir()
    index_dir.mkdir()
    (input_dir / "sample.txt").write_text("source lineage and execution gate", encoding="utf-8")
    (input_dir / "src_internal_sample.txt").write_text("should not ingest", encoding="utf-8")
    cfg = Settings(
        project_root=tmp_path,
        data_dir=tmp_path,
        input_dir=input_dir,
        processed_dir=processed_dir,
        export_dir=export_dir,
        index_dir=index_dir,
        sqlite_path=processed_dir / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock-cited-synthesizer-v1",
    )
    result = ingest_folder(input_dir, cfg)
    assert result["status"] == "INGEST_FOLDER_OK"
    assert result["files_ingested"] == 1
