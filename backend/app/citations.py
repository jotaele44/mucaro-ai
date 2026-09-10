from __future__ import annotations

from typing import Any

SNIPPET_LIMIT = 320


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


def _snippet(text: Any, *, limit: int = SNIPPET_LIMIT) -> str:
    return " ".join(str(text or "").split())[:limit]


def build_citation_records(retrieved_chunks: list[dict[str, Any]], cited_chunk_ids: list[str]) -> list[dict[str, Any]]:
    retrieved_by_id = {str(chunk.get("chunk_id")): chunk for chunk in retrieved_chunks if chunk.get("chunk_id")}
    retrieval_ranks = {str(chunk.get("chunk_id")): rank for rank, chunk in enumerate(retrieved_chunks, start=1)}
    records: list[dict[str, Any]] = []
    for citation_order, chunk_id in enumerate(cited_chunk_ids, start=1):
        chunk = retrieved_by_id.get(str(chunk_id))
        if chunk is None:
            continue
        record: dict[str, Any] = {
            "citation_order": citation_order,
            "retrieval_rank": retrieval_ranks.get(str(chunk_id)),
            "chunk_id": str(chunk.get("chunk_id")),
            "source_id": str(chunk.get("source_id")),
            "chunk_index": chunk.get("chunk_index"),
            "text_snippet": _snippet(chunk.get("text")),
        }
        for optional_key in (
            "score",
            "retrieval_strategy",
            "matched_terms",
            "source_format",
            "source_url",
            "snapshot_id",
            "snapshot_sha256",
            "content_sha256",
            "retrieval_utc",
            "parser_warnings",
        ):
            if optional_key in chunk:
                record[optional_key] = chunk[optional_key]
        records.append(record)
    return records


def validate_citations(answer_trace: dict[str, Any]) -> dict[str, Any]:
    retrieved = set(answer_trace.get("retrieved_chunk_ids", []))
    cited = set(answer_trace.get("cited_chunk_ids", []))
    citation_records = answer_trace.get("cited_chunks", [])
    flags = list(answer_trace.get("validation_flags", []))
    if not cited:
        flags.append("ERROR_UNCITED_ANSWER")
    missing = sorted(cited - retrieved)
    if missing:
        flags.append("ERROR_CITED_CHUNK_NOT_RETRIEVED")
    if cited and not citation_records:
        flags.append("ERROR_CITATION_TRACE_MISSING")
    elif isinstance(citation_records, list):
        traced_ids = {str(record.get("chunk_id")) for record in citation_records if isinstance(record, dict)}
        untraced = sorted(cited - traced_ids)
        if untraced:
            flags.append("ERROR_CITED_CHUNK_NOT_TRACED")
    answer_trace["validation_flags"] = sorted(set(flags))
    answer_trace["citation_status"] = "CITATION_OK" if not any(f.startswith("ERROR_") for f in flags) else "CITATION_ERROR"
    return answer_trace
