from backend.app.retrieval import lexical_retrieve


def test_lexical_retrieval_returns_match():
    chunks = [
        {"chunk_id": "chunk_a", "source_id": "src_a", "text": "Execution Gate planning approval lock run trace", "chunk_index": 0},
        {"chunk_id": "chunk_b", "source_id": "src_b", "text": "Unrelated weather data", "chunk_index": 0},
    ]
    result = lexical_retrieve("approval lock", chunks, top_k=1)
    assert result["status"] == "RETRIEVAL_OK"
    assert result["retrieved_chunks"][0]["chunk_id"] == "chunk_a"
