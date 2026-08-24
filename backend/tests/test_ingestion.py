from pathlib import Path

from backend.app.config import Settings
from backend.app.ingestion import ingest_file


def test_ingest_file_tmp(tmp_path: Path):
    root = tmp_path / "proj"
    cfg = Settings(
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
    src = tmp_path / "note.md"
    src.write_text("Execution Gate source lineage citations.", encoding="utf-8")
    result = ingest_file(src, cfg)
    assert result["status"] == "INGEST_OK"
    assert result["source"]["chunk_count"] >= 1
    assert result["index_status"] == "INDEX_OK"
    assert result["indexed_chunk_count"] >= 1
    assert (cfg.processed_dir / "source_manifest.json").exists()
    assert (cfg.index_dir / "tfidf_index.json").exists()
