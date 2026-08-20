from pathlib import Path

from backend.app.config import Settings
from backend.app.exports import export_answer_trace


def test_export_answer_trace(tmp_path: Path):
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
    trace = {
        "run_id": "run_deadbeef",
        "query": "q",
        "model_provider": "mock",
        "model_name": "mock",
        "prompt_template_id": "p",
        "retrieved_chunk_ids": ["chunk_a"],
        "cited_chunk_ids": ["chunk_a"],
        "source_ids": ["src_a"],
        "answer": "answer",
        "confidence": 0.6,
        "validation_flags": [],
        "created_at": "now",
    }
    paths = export_answer_trace(trace, cfg)
    assert Path(paths["json"]).exists()
    assert Path(paths["markdown"]).exists()
    assert Path(paths["csv"]).exists()
