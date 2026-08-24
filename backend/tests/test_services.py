from __future__ import annotations

from pathlib import Path

from backend.app.config import Settings
from backend.app.services import answer_question
from backend.app.utils import write_json


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
    write_json(
        cfg.processed_dir / "document_chunks.json",
        [
            {
                "chunk_id": "chunk_a",
                "source_id": "src_a",
                "chunk_index": 0,
                "text": "Source lineage requires cited chunk IDs.",
            }
        ],
    )


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
