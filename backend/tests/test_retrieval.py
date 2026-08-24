from backend.app.retrieval import lexical_retrieve


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
