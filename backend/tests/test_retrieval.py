from pathlib import Path

from backend.app.config import Settings
from backend.app.retrieval import bm25_retrieve, lexical_retrieve, retrieve, vector_retrieve
from backend.app.semantic_store import STORE_VERSION, build_semantic_store, semantic_store_path
from backend.app.vector_index import build_persistent_index, index_path


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


def test_lexical_retrieval_returns_match():
    chunks = [
        {"chunk_id": "chunk_a", "source_id": "src_a", "text": "Execution Gate planning approval lock run trace", "chunk_index": 0},
        {"chunk_id": "chunk_b", "source_id": "src_b", "text": "Unrelated weather data", "chunk_index": 0},
    ]
    result = lexical_retrieve("approval lock", chunks, top_k=1)
    assert result["status"] == "RETRIEVAL_OK"
    assert result["retrieved_chunks"][0]["chunk_id"] == "chunk_a"
    assert result["retrieved_chunks"][0]["retrieval_strategy"] == "lexical_v2"


def test_lexical_retrieval_empty_corpus():
    result = lexical_retrieve("approval lock", [], top_k=5)
    assert result["status"] == "ERROR_NO_SOURCES"
    assert result["retrieved_chunks"] == []


def test_lexical_retrieval_invalid_top_k():
    result = lexical_retrieve("approval lock", [], top_k=0)
    assert result["status"] == "ERROR_INVALID_TOP_K"
    assert "TOP_K_RANGE:1-20" in result["warnings"]


def test_lexical_retrieval_tie_is_stable():
    chunks = [
        {"chunk_id": "chunk_b", "source_id": "src_b", "text": "approval lock", "chunk_index": 0},
        {"chunk_id": "chunk_a", "source_id": "src_a", "text": "approval lock", "chunk_index": 0},
    ]
    result = lexical_retrieve("approval lock", chunks, top_k=2)
    assert [chunk["chunk_id"] for chunk in result["retrieved_chunks"]] == ["chunk_a", "chunk_b"]


def test_lexical_retrieval_skips_malformed_chunks():
    chunks = [
        {"chunk_id": "chunk_bad", "source_id": "src_bad", "text": "", "chunk_index": 0},
        {"chunk_id": "chunk_good", "source_id": "src_good", "text": "source lineage citations", "chunk_index": 1},
    ]
    result = lexical_retrieve("source lineage", chunks, top_k=5)
    assert result["status"] == "RETRIEVAL_OK"
    assert result["retrieved_chunks"][0]["chunk_id"] == "chunk_good"
    assert "MALFORMED_CHUNK_EMPTY_TEXT:chunk_id=chunk_bad" in result["warnings"]


def test_lexical_retrieval_reports_no_valid_chunks():
    chunks = [{"chunk_id": "chunk_bad", "source_id": "src_bad", "text": ""}]
    result = lexical_retrieve("source lineage", chunks, top_k=5)
    assert result["status"] == "ERROR_NO_VALID_CHUNKS"
    assert result["retrieved_chunks"] == []


def test_vector_retrieval_weights_rare_terms():
    chunks = [
        {
            "chunk_id": "chunk_common",
            "source_id": "src_common",
            "text": "planning planning planning approval review",
            "chunk_index": 0,
        },
        {
            "chunk_id": "chunk_rare",
            "source_id": "src_rare",
            "text": "planning approval lineage",
            "chunk_index": 0,
        },
    ]
    result = vector_retrieve("planning lineage", chunks, top_k=1)
    assert result["status"] == "RETRIEVAL_OK"
    assert result["retrieved_chunks"][0]["chunk_id"] == "chunk_rare"
    assert result["retrieved_chunks"][0]["retrieval_strategy"] == "vector_tfidf_v1"


def test_hybrid_retrieval_is_default_strategy():
    chunks = [
        {"chunk_id": "chunk_a", "source_id": "src_a", "text": "source lineage citations", "chunk_index": 0},
    ]
    result = retrieve("source lineage", chunks, top_k=1)
    assert result["status"] == "RETRIEVAL_OK"
    assert result["retrieved_chunks"][0]["retrieval_strategy"] == "hybrid_tfidf_v1"
    assert result["retrieved_chunks"][0]["lexical_score"] > 0
    assert result["retrieved_chunks"][0]["vector_score"] > 0


def test_bm25_retrieval_rewards_focused_matches():
    chunks = [
        {
            "chunk_id": "chunk_repeated",
            "source_id": "src_repeated",
            "text": "planning planning planning approval approval approval general notes",
            "chunk_index": 0,
        },
        {
            "chunk_id": "chunk_focused",
            "source_id": "src_focused",
            "text": "approval lock source lineage citations",
            "chunk_index": 0,
        },
    ]

    result = bm25_retrieve("source lineage citations", chunks, top_k=1)

    assert result["status"] == "RETRIEVAL_OK"
    assert result["retrieved_chunks"][0]["chunk_id"] == "chunk_focused"
    assert result["retrieved_chunks"][0]["retrieval_strategy"] == "bm25_v1"
    assert result["retrieved_chunks"][0]["bm25_score"] > 0


def test_retrieve_rejects_unknown_strategy():
    result = retrieve("source lineage", [], top_k=1, strategy="unknown")
    assert result["status"] == "ERROR_INVALID_RETRIEVAL_STRATEGY"
    assert "RETRIEVAL_STRATEGY_ALLOWED:bm25,hybrid,lexical,persistent,semantic,vector" in result["warnings"]


def test_hybrid_retrieval_reports_duplicate_chunk_ids_and_bad_index():
    chunks = [
        {"chunk_id": "chunk_a", "source_id": "src_b", "text": "approval lock", "chunk_index": "bad"},
        {"chunk_id": "chunk_a", "source_id": "src_a", "text": "approval lock", "chunk_index": 0},
    ]
    result = retrieve("approval lock", chunks, top_k=2)
    assert result["status"] == "RETRIEVAL_OK"
    assert [chunk["source_id"] for chunk in result["retrieved_chunks"]] == ["src_a", "src_b"]
    assert "DUPLICATE_CHUNK_ID:chunk_id=chunk_a" in result["warnings"]
    assert "MALFORMED_CHUNK_INDEX:chunk_id=chunk_a" in result["warnings"]


def test_persistent_retrieval_uses_local_index(tmp_path: Path):
    cfg = make_settings(tmp_path)
    chunks = [
        {"chunk_id": "chunk_common", "source_id": "src_common", "text": "planning planning approval", "chunk_index": 0},
        {"chunk_id": "chunk_rare", "source_id": "src_rare", "text": "planning lineage approval", "chunk_index": 1},
    ]

    index = build_persistent_index(chunks, cfg)
    result = retrieve("planning lineage", chunks, top_k=1, strategy="persistent", cfg=cfg)

    assert index_path(cfg).exists()
    assert index["indexed_chunk_count"] == 2
    assert result["status"] == "RETRIEVAL_OK"
    assert result["retrieved_chunks"][0]["chunk_id"] == "chunk_rare"
    assert result["retrieved_chunks"][0]["retrieval_strategy"] == "persistent_tfidf_v1"


def test_persistent_retrieval_requires_built_index(tmp_path: Path):
    cfg = make_settings(tmp_path)
    chunks = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "source lineage", "chunk_index": 0}]

    result = retrieve("source lineage", chunks, top_k=1, strategy="persistent", cfg=cfg)

    assert result["status"] == "ERROR_PERSISTENT_INDEX_MISSING"
    assert "RUN_INGEST_TO_BUILD_INDEX" in result["warnings"]


def test_persistent_retrieval_rejects_stale_index(tmp_path: Path):
    cfg = make_settings(tmp_path)
    chunks = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "source lineage", "chunk_index": 0}]
    build_persistent_index(chunks, cfg)
    changed_chunks = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "different content", "chunk_index": 0}]

    result = retrieve("source lineage", changed_chunks, top_k=1, strategy="persistent", cfg=cfg)

    assert result["status"] == "ERROR_PERSISTENT_INDEX_STALE"
    assert "RUN_INGEST_TO_REBUILD_INDEX" in result["warnings"]


def test_semantic_retrieval_uses_local_store(tmp_path: Path):
    cfg = make_settings(tmp_path)
    chunks = [
        {"chunk_id": "chunk_common", "source_id": "src_common", "text": "planning planning approval", "chunk_index": 0},
        {"chunk_id": "chunk_semantic", "source_id": "src_semantic", "text": "source lineage citations", "chunk_index": 1},
    ]

    store = build_semantic_store(chunks, cfg)
    result = retrieve("source citation lineage", chunks, top_k=1, strategy="semantic", cfg=cfg)

    assert semantic_store_path(cfg).exists()
    assert store["indexed_chunk_count"] == 2
    assert store["version"] == STORE_VERSION
    assert result["status"] == "RETRIEVAL_OK"
    assert result["retrieved_chunks"][0]["chunk_id"] == "chunk_semantic"
    assert result["retrieved_chunks"][0]["retrieval_strategy"] == "local_semantic_hash_store_v2"
    assert result["retrieved_chunks"][0]["semantic_score"] > 0
    assert result["retrieved_chunks"][0]["matched_term_score"] > 0


def test_semantic_retrieval_weights_rare_lineage_terms(tmp_path: Path):
    cfg = make_settings(tmp_path)
    chunks = [
        {
            "chunk_id": "chunk_expected",
            "source_id": "src_expected",
            "text": "Source lineage records source IDs, chunk IDs, file SHA-256 hashes, parser warnings, byte counts, and source paths for cited answer traces.",
            "chunk_index": 0,
        },
        {
            "chunk_id": "chunk_distractor",
            "source_id": "src_distractor",
            "text": "Conversation ask uses bounded prior turns as chat context while keeping source citations tied to retrieved chunks.",
            "chunk_index": 1,
        },
    ]
    build_semantic_store(chunks, cfg)

    result = retrieve("Which lineage fields tie citations to files?", chunks, top_k=1, strategy="semantic", cfg=cfg)

    assert result["status"] == "RETRIEVAL_OK"
    top = result["retrieved_chunks"][0]
    assert top["chunk_id"] == "chunk_expected"
    assert "lineage" in top["matched_terms"]
    assert "file" in top["matched_terms"]


def test_semantic_retrieval_requires_built_store(tmp_path: Path):
    cfg = make_settings(tmp_path)
    chunks = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "source lineage", "chunk_index": 0}]

    result = retrieve("source lineage", chunks, top_k=1, strategy="semantic", cfg=cfg)

    assert result["status"] == "ERROR_SEMANTIC_STORE_MISSING"
    assert "RUN_SOURCES_REINDEX_TO_BUILD_SEMANTIC_STORE" in result["warnings"]


def test_semantic_retrieval_rejects_stale_store(tmp_path: Path):
    cfg = make_settings(tmp_path)
    chunks = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "source lineage", "chunk_index": 0}]
    build_semantic_store(chunks, cfg)
    changed_chunks = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "different content", "chunk_index": 0}]

    result = retrieve("source lineage", changed_chunks, top_k=1, strategy="semantic", cfg=cfg)

    assert result["status"] == "ERROR_SEMANTIC_STORE_STALE"
    assert "RUN_SOURCES_REINDEX_TO_REBUILD_SEMANTIC_STORE" in result["warnings"]


def test_semantic_retrieval_fails_closed_on_weak_context(tmp_path: Path):
    cfg = make_settings(tmp_path)
    chunks = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "source lineage citations", "chunk_index": 0}]
    build_semantic_store(chunks, cfg)

    result = retrieve("moon minerals", chunks, top_k=1, strategy="semantic", cfg=cfg)

    assert result["status"] == "ERROR_NO_RETRIEVAL_CONTEXT"
    assert result["retrieved_chunks"] == []
