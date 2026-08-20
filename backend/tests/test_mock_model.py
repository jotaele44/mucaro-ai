from backend.app.model_router import generate_answer


def test_mock_model_cites_chunks():
    chunks = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "Source lineage requires citations."}]
    answer = generate_answer("What is required?", chunks, model_provider="mock")
    assert answer["model_provider"] == "mock"
    assert answer["cited_chunk_ids"] == ["chunk_a"]
