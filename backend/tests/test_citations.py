from backend.app.citations import score_confidence, validate_citations


def test_citation_validation_ok():
    trace = {
        "retrieved_chunk_ids": ["chunk_a"],
        "cited_chunk_ids": ["chunk_a"],
        "validation_flags": [],
    }
    validated = validate_citations(trace)
    assert validated["citation_status"] == "CITATION_OK"
    assert score_confidence(["chunk_a"], ["src_a"], True) == 0.60


def test_citation_validation_error():
    validated = validate_citations({"retrieved_chunk_ids": ["chunk_a"], "cited_chunk_ids": [], "validation_flags": []})
    assert "ERROR_UNCITED_ANSWER" in validated["validation_flags"]
