from __future__ import annotations

import csv
import json
from pathlib import Path

from backend.app.config import Settings
from backend.app.services import answer_question
from backend.app.utils import write_json
from backend.app.vector_index import build_persistent_index


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


def seed_chunks(cfg: Settings) -> None:
    cfg.ensure_dirs()
    chunks = [
        {
            "chunk_id": "chunk_a",
            "source_id": "src_a",
            "chunk_index": 0,
            "text": "Source lineage requires cited chunk IDs.",
        }
    ]
    write_json(cfg.processed_dir / "document_chunks.json", chunks)
    build_persistent_index(chunks, cfg)


def test_answer_question_returns_model_provider_error(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    monkeypatch.delenv("MUCARO_AI_LIVE_MODE", raising=False)

    result = answer_question("What does source lineage require?", model_provider="anthropic", cfg=cfg)

    assert result["status"] == "ERROR_LIVE_MODE_DISABLED"
    assert result["retrieved_chunks"]


def test_answer_question_flags_uncited_live_answer(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)

    def fake_generate_answer(*args, **kwargs):
        return {
            "model_provider": "anthropic",
            "model_name": "fake-live",
            "answer": "This answer intentionally lacks chunk citations.",
            "cited_chunk_ids": [],
        }

    monkeypatch.setattr("backend.app.services.generate_answer", fake_generate_answer)

    result = answer_question("What does source lineage require?", model_provider="anthropic", cfg=cfg, persist=False)

    assert result["citation_status"] == "CITATION_ERROR"
    assert "ERROR_UNCITED_ANSWER" in result["validation_flags"]


def test_answer_question_accepts_persistent_retrieval_strategy(tmp_path: Path):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)

    result = answer_question(
        "What does source lineage require?",
        retrieval_strategy="persistent",
        model_provider="mock",
        cfg=cfg,
        persist=False,
    )

    assert result["citation_status"] == "CITATION_OK"
    assert result["retrieval_strategy"] == "persistent_tfidf_v1"


def test_answer_question_exports_citation_trace_records(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)

    def fake_generate_answer(*args, **kwargs):
        return {
            "model_provider": "mock",
            "model_name": "fake-cited",
            "answer": "The answer cites chunk_a.",
            "cited_chunk_ids": ["chunk_a"],
        }

    monkeypatch.setattr("backend.app.services.generate_answer", fake_generate_answer)

    result = answer_question("What does source lineage require?", model_provider="mock", cfg=cfg)

    assert result["citation_status"] == "CITATION_OK"
    assert result["source_ids"] == ["src_a"]
    citation = result["cited_chunks"][0]
    assert citation["citation_order"] == 1
    assert citation["retrieval_rank"] == 1
    assert citation["chunk_id"] == "chunk_a"
    assert citation["source_id"] == "src_a"
    assert citation["chunk_index"] == 0
    assert citation["text_snippet"] == "Source lineage requires cited chunk IDs."
    assert citation["score"] > 0
    assert citation["retrieval_strategy"] == "hybrid_tfidf_v1"
    assert citation["matched_terms"] == ["lineage", "source"]

    json_path = Path(result["export_paths"]["json"])
    md_path = Path(result["export_paths"]["markdown"])
    csv_path = Path(result["export_paths"]["csv"])
    exported = json.loads(json_path.read_text(encoding="utf-8"))
    assert exported["cited_chunks"][0]["retrieval_rank"] == 1
    assert "source_id=src_a retrieval_rank=1" in md_path.read_text(encoding="utf-8")
    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["run_id"] == result["run_id"]
    assert rows[0]["chunk_id"] == "chunk_a"
    assert rows[0]["source_id"] == "src_a"
    assert rows[0]["retrieval_rank"] == "1"
