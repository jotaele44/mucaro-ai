from __future__ import annotations

import math
from collections import Counter
from typing import Any

from .config import Settings, settings
from .ingestion import get_chunks
from .utils import tokenize
from .vector_index import (
    cosine_similarity,
    fingerprint_chunks,
    load_persistent_index,
    tfidf_vector,
    tfidf_vectors,
)


class RetrievalError(RuntimeError):
    pass


MIN_TOP_K = 1
MAX_TOP_K = 20
RETRIEVAL_STRATEGIES = {"lexical", "vector", "hybrid", "persistent"}


def _error(status: str, query: str, *, warnings: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status, "query": query, "retrieved_chunks": []}
    if warnings:
        result["warnings"] = sorted(set(warnings))
    return result


def _coerce_chunk_index(value: Any, fallback: int, chunk_id: str, warnings: list[str]) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        warnings.append(f"MALFORMED_CHUNK_INDEX:chunk_id={chunk_id}")
        return fallback


def _normalize_chunk(chunk: dict[str, Any], index: int, warnings: list[str]) -> dict[str, Any] | None:
    chunk_id = chunk.get("chunk_id")
    source_id = chunk.get("source_id")
    text = chunk.get("text")
    if not isinstance(chunk_id, str) or not chunk_id.strip():
        warnings.append(f"MALFORMED_CHUNK_MISSING_CHUNK_ID:index={index}")
        return None
    if not isinstance(source_id, str) or not source_id.strip():
        warnings.append(f"MALFORMED_CHUNK_MISSING_SOURCE_ID:chunk_id={chunk_id}")
        return None
    if not isinstance(text, str) or not text.strip():
        warnings.append(f"MALFORMED_CHUNK_EMPTY_TEXT:chunk_id={chunk_id}")
        return None
    normalized = dict(chunk)
    normalized["chunk_id"] = chunk_id
    normalized["source_id"] = source_id
    normalized["text"] = text
    normalized["chunk_index"] = _coerce_chunk_index(chunk.get("chunk_index", index), index, chunk_id, warnings)
    return normalized


def _normalize_chunks(chunks: list[dict[str, Any]], warnings: list[str]) -> list[dict[str, Any]]:
    normalized_chunks: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    for index, raw_chunk in enumerate(chunks):
        chunk = _normalize_chunk(raw_chunk, index, warnings)
        if chunk is None:
            continue
        chunk_id = chunk["chunk_id"]
        if chunk_id in seen_chunk_ids:
            warnings.append(f"DUPLICATE_CHUNK_ID:chunk_id={chunk_id}")
        seen_chunk_ids.add(chunk_id)
        normalized_chunks.append(chunk)
    return normalized_chunks


def _validate_inputs(query: str, top_k: int) -> dict[str, Any] | None:
    if not query.strip():
        raise RetrievalError("ERROR_EMPTY_QUERY")
    if top_k < MIN_TOP_K or top_k > MAX_TOP_K:
        return _error("ERROR_INVALID_TOP_K", query, warnings=[f"TOP_K_RANGE:{MIN_TOP_K}-{MAX_TOP_K}"])
    return None


def _lexical_score(q_counts: Counter[str], c_counts: Counter[str]) -> float:
    overlap = sum(min(q_counts[t], c_counts[t]) for t in q_counts)
    if overlap == 0:
        return 0.0
    norm = math.sqrt(sum(v * v for v in q_counts.values())) * math.sqrt(sum(v * v for v in c_counts.values()))
    cosine = float(overlap / norm) if norm else 0.0
    query_coverage = len(set(q_counts).intersection(c_counts)) / max(1, len(set(q_counts)))
    return (0.8 * cosine) + (0.2 * query_coverage)


def _rank(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        scored,
        key=lambda x: (
            -x["score"],
            -x.get("vector_score", 0.0),
            -x.get("lexical_score", 0.0),
            x.get("source_id", ""),
            x.get("chunk_index", 0),
            x.get("chunk_id", ""),
        ),
    )


def _prepare_chunks(
    query: str,
    chunks: list[dict[str, Any]] | None,
    top_k: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    validation_error = _validate_inputs(query, top_k)
    if validation_error is not None:
        return validation_error, [], []
    chunks = get_chunks() if chunks is None else chunks
    if not chunks:
        return _error("ERROR_NO_SOURCES", query), [], []
    warnings: list[str] = []
    normalized_chunks = _normalize_chunks(chunks, warnings)
    if not normalized_chunks:
        return _error("ERROR_NO_VALID_CHUNKS", query, warnings=warnings), [], warnings
    return None, normalized_chunks, warnings


def lexical_retrieve(query: str, chunks: list[dict[str, Any]] | None = None, *, top_k: int = 5) -> dict[str, Any]:
    early_result, normalized_chunks, warnings = _prepare_chunks(query, chunks, top_k)
    if early_result is not None:
        return early_result
    q_tokens = tokenize(query)
    q_counts = Counter(q_tokens)
    scored: list[dict[str, Any]] = []
    for chunk in normalized_chunks:
        c_tokens = tokenize(str(chunk.get("text", "")))
        if not c_tokens:
            continue
        c_counts = Counter(c_tokens)
        score = _lexical_score(q_counts, c_counts)
        if score <= 0:
            continue
        item = dict(chunk)
        item["score"] = round(score, 6)
        item["retrieval_strategy"] = "lexical_v2"
        item["matched_terms"] = sorted(set(q_counts).intersection(c_counts))[:20]
        scored.append(item)
    if not scored:
        return _error("ERROR_NO_RETRIEVAL_CONTEXT", query, warnings=warnings)
    result: dict[str, Any] = {"status": "RETRIEVAL_OK", "query": query, "retrieved_chunks": _rank(scored)[:top_k]}
    if warnings:
        result["warnings"] = sorted(set(warnings))
    return result


def vector_retrieve(query: str, chunks: list[dict[str, Any]] | None = None, *, top_k: int = 5) -> dict[str, Any]:
    early_result, normalized_chunks, warnings = _prepare_chunks(query, chunks, top_k)
    if early_result is not None:
        return early_result
    q_tokens = tokenize(query)
    tokenized_chunks = [tokenize(str(chunk.get("text", ""))) for chunk in normalized_chunks]
    chunk_vectors, idf = tfidf_vectors(tokenized_chunks)
    query_vector = tfidf_vector(q_tokens, idf)
    scored: list[dict[str, Any]] = []
    for chunk, c_tokens, chunk_vector in zip(normalized_chunks, tokenized_chunks, chunk_vectors, strict=True):
        score = cosine_similarity(query_vector, chunk_vector)
        if score <= 0:
            continue
        item = dict(chunk)
        item["score"] = round(score, 6)
        item["retrieval_strategy"] = "vector_tfidf_v1"
        item["matched_terms"] = sorted(set(q_tokens).intersection(c_tokens))[:20]
        scored.append(item)
    if not scored:
        return _error("ERROR_NO_RETRIEVAL_CONTEXT", query, warnings=warnings)
    result: dict[str, Any] = {"status": "RETRIEVAL_OK", "query": query, "retrieved_chunks": _rank(scored)[:top_k]}
    if warnings:
        result["warnings"] = sorted(set(warnings))
    return result


def hybrid_retrieve(query: str, chunks: list[dict[str, Any]] | None = None, *, top_k: int = 5) -> dict[str, Any]:
    early_result, normalized_chunks, warnings = _prepare_chunks(query, chunks, top_k)
    if early_result is not None:
        return early_result
    q_tokens = tokenize(query)
    q_counts = Counter(q_tokens)
    tokenized_chunks = [tokenize(str(chunk.get("text", ""))) for chunk in normalized_chunks]
    chunk_vectors, idf = tfidf_vectors(tokenized_chunks)
    query_vector = tfidf_vector(q_tokens, idf)
    scored: list[dict[str, Any]] = []
    for chunk, c_tokens, chunk_vector in zip(normalized_chunks, tokenized_chunks, chunk_vectors, strict=True):
        c_counts = Counter(c_tokens)
        lexical_score = _lexical_score(q_counts, c_counts)
        vector_score = cosine_similarity(query_vector, chunk_vector)
        if lexical_score <= 0 and vector_score <= 0:
            continue
        score = (0.45 * lexical_score) + (0.55 * vector_score)
        item = dict(chunk)
        item["score"] = round(score, 6)
        item["lexical_score"] = round(lexical_score, 6)
        item["vector_score"] = round(vector_score, 6)
        item["retrieval_strategy"] = "hybrid_tfidf_v1"
        item["matched_terms"] = sorted(set(q_tokens).intersection(c_tokens))[:20]
        scored.append(item)
    if not scored:
        return _error("ERROR_NO_RETRIEVAL_CONTEXT", query, warnings=warnings)
    result: dict[str, Any] = {"status": "RETRIEVAL_OK", "query": query, "retrieved_chunks": _rank(scored)[:top_k]}
    if warnings:
        result["warnings"] = sorted(set(warnings))
    return result


def persistent_retrieve(
    query: str,
    chunks: list[dict[str, Any]] | None = None,
    *,
    top_k: int = 5,
    cfg: Settings = settings,
) -> dict[str, Any]:
    validation_error = _validate_inputs(query, top_k)
    if validation_error is not None:
        return validation_error
    chunks = get_chunks(cfg) if chunks is None else chunks
    if not chunks:
        return _error("ERROR_NO_SOURCES", query)
    index = load_persistent_index(cfg)
    if index is None:
        return _error("ERROR_PERSISTENT_INDEX_MISSING", query, warnings=["RUN_INGEST_TO_BUILD_INDEX"])
    expected_fingerprint = fingerprint_chunks(chunks)
    if index.get("chunks_fingerprint") != expected_fingerprint:
        return _error("ERROR_PERSISTENT_INDEX_STALE", query, warnings=["RUN_INGEST_TO_REBUILD_INDEX"])

    idf = index.get("idf", {})
    entries = index.get("entries", [])
    if not isinstance(idf, dict) or not isinstance(entries, list) or not entries:
        return _error("ERROR_PERSISTENT_INDEX_EMPTY", query)

    q_tokens = tokenize(query)
    query_vector = tfidf_vector(q_tokens, {str(term): float(value) for term, value in idf.items()})
    scored: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        chunk = entry.get("chunk")
        vector = entry.get("vector")
        tokens = entry.get("tokens", [])
        if not isinstance(chunk, dict) or not isinstance(vector, dict):
            continue
        vector_score = cosine_similarity(
            query_vector,
            {str(term): float(value) for term, value in vector.items()},
        )
        if vector_score <= 0:
            continue
        item = dict(chunk)
        item["score"] = round(vector_score, 6)
        item["vector_score"] = round(vector_score, 6)
        item["retrieval_strategy"] = "persistent_tfidf_v1"
        item["matched_terms"] = sorted(set(q_tokens).intersection(set(tokens)))[:20] if isinstance(tokens, list) else []
        scored.append(item)
    if not scored:
        return _error("ERROR_NO_RETRIEVAL_CONTEXT", query, warnings=list(index.get("warnings", [])))
    result: dict[str, Any] = {"status": "RETRIEVAL_OK", "query": query, "retrieved_chunks": _rank(scored)[:top_k]}
    warnings = index.get("warnings", [])
    if warnings:
        result["warnings"] = sorted(set(warnings))
    return result


def retrieve(
    query: str,
    chunks: list[dict[str, Any]] | None = None,
    *,
    top_k: int = 5,
    strategy: str = "hybrid",
    cfg: Settings = settings,
) -> dict[str, Any]:
    if strategy not in RETRIEVAL_STRATEGIES:
        return _error(
            "ERROR_INVALID_RETRIEVAL_STRATEGY",
            query,
            warnings=[f"RETRIEVAL_STRATEGY_ALLOWED:{','.join(sorted(RETRIEVAL_STRATEGIES))}"],
        )
    if strategy == "lexical":
        return lexical_retrieve(query, chunks, top_k=top_k)
    if strategy == "vector":
        return vector_retrieve(query, chunks, top_k=top_k)
    if strategy == "persistent":
        return persistent_retrieve(query, chunks, top_k=top_k, cfg=cfg)
    return hybrid_retrieve(query, chunks, top_k=top_k)
