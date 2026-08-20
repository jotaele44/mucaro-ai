from __future__ import annotations

from typing import Any


def score_confidence(cited_chunk_ids: list[str], source_ids: list[str], retrieval_exists: bool) -> float:
    unique_cited = set(cited_chunk_ids)
    unique_sources = set(source_ids)
    if len(unique_cited) >= 3 and len(unique_sources) >= 2:
        return 0.90
    if len(unique_cited) >= 2:
        return 0.75
    if len(unique_cited) == 1:
        return 0.60
    if retrieval_exists:
        return 0.30
    return 0.0


def validate_citations(answer_trace: dict[str, Any]) -> dict[str, Any]:
    retrieved = set(answer_trace.get("retrieved_chunk_ids", []))
    cited = set(answer_trace.get("cited_chunk_ids", []))
    flags = list(answer_trace.get("validation_flags", []))
    if not cited:
        flags.append("ERROR_UNCITED_ANSWER")
    missing = sorted(cited - retrieved)
    if missing:
        flags.append("ERROR_CITED_CHUNK_NOT_RETRIEVED")
    answer_trace["validation_flags"] = sorted(set(flags))
    answer_trace["citation_status"] = "CITATION_OK" if not any(f.startswith("ERROR_") for f in flags) else "CITATION_ERROR"
    return answer_trace
