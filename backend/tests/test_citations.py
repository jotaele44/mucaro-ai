from backend.app.citations import build_citation_records, score_confidence, validate_citations


def test_citation_validation_ok():
    trace = {
        "retrieved_chunk_ids": ["chunk_a"],
        "cited_chunk_ids": ["chunk_a"],
        "cited_chunks": [{"chunk_id": "chunk_a", "source_id": "src_a"}],
        "validation_flags": [],
    }
    validated = validate_citations(trace)
    assert validated["citation_status"] == "CITATION_OK"
    assert score_confidence(["chunk_a"], ["src_a"], True) == 0.60


def test_citation_validation_error():
    validated = validate_citations({"retrieved_chunk_ids": ["chunk_a"], "cited_chunk_ids": [], "validation_flags": []})
    assert "ERROR_UNCITED_ANSWER" in validated["validation_flags"]


def test_build_citation_records_preserves_retrieval_trace():
    records = build_citation_records(
        [
            {
                "chunk_id": "chunk_b",
                "source_id": "src_b",
                "chunk_index": 2,
                "text": "Second ranked evidence chunk with enough text for a reviewer.",
                "score": 0.42,
                "retrieval_strategy": "hybrid_tfidf_v1",
                "matched_terms": ["evidence"],
                "source_format": "md",
            },
            {
                "chunk_id": "chunk_a",
                "source_id": "src_a",
                "chunk_index": 0,
                "text": "First cited evidence chunk.",
                "score": 0.91,
                "retrieval_strategy": "hybrid_tfidf_v1",
                "matched_terms": ["cited"],
                "source_format": "txt",
            },
        ],
        ["chunk_a", "chunk_b"],
    )

    assert records == [
        {
            "citation_order": 1,
            "retrieval_rank": 2,
            "chunk_id": "chunk_a",
            "source_id": "src_a",
            "chunk_index": 0,
            "text_snippet": "First cited evidence chunk.",
            "score": 0.91,
            "retrieval_strategy": "hybrid_tfidf_v1",
            "matched_terms": ["cited"],
            "source_format": "txt",
        },
        {
            "citation_order": 2,
            "retrieval_rank": 1,
            "chunk_id": "chunk_b",
            "source_id": "src_b",
            "chunk_index": 2,
            "text_snippet": "Second ranked evidence chunk with enough text for a reviewer.",
            "score": 0.42,
            "retrieval_strategy": "hybrid_tfidf_v1",
            "matched_terms": ["evidence"],
            "source_format": "md",
        },
    ]


def test_citation_validation_requires_trace_records_for_cited_chunks():
    validated = validate_citations(
        {
            "retrieved_chunk_ids": ["chunk_a"],
            "cited_chunk_ids": ["chunk_a"],
            "cited_chunks": [],
            "validation_flags": [],
        }
    )

    assert validated["citation_status"] == "CITATION_ERROR"
    assert "ERROR_CITATION_TRACE_MISSING" in validated["validation_flags"]
