from __future__ import annotations

import math
from collections import Counter
from typing import Any

from .ingestion import get_chunks
from .utils import tokenize


class RetrievalError(RuntimeError):
    pass


MIN_TOP_K = 1
MAX_TOP_K = 20
RETRIEVAL_STRATEGIES = {"lexical", "vector", "hybrid"}


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


def _tfidf_vectors(tokenized_chunks: list[list[str]]) -> tuple[list[dict[str, float]], dict[str, float]]:
    document_count = len(tokenized_chunks)
    document_frequencies: Counter[str] = Counter()
    for tokens in tokenized_chunks:
        document_frequencies.update(set(tokens))
    idf = {
        term: math.log((1 + document_count) / (1 + document_frequency)) + 1
        for term, document_frequency in document_frequencies.items()
    }
    return [_tfidf_vector(tokens, idf) for tokens in tokenized_chunks], idf


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    counts = Counter(tokens)
    weighted = {term: (1 + math.log(count)) * idf.get(term, 0.0) for term, count in counts.items()}
    norm = math.sqrt(sum(value * value for value in weighted.values()))
    if not norm:
        return {}
    return {term: value / norm for term, value in weighted.items()}


def _cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(term, 0.0) for term, value in left.items())


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
    chunk_vectors, idf = _tfidf_vectors(tokenized_chunks)
    query_vector = _tfidf_vector(q_tokens, idf)
    scored: list[dict[str, Any]] = []
    for chunk, c_tokens, chunk_vector in zip(normalized_chunks, tokenized_chunks, chunk_vectors, strict=True):
        score = _cosine_similarity(query_vector, chunk_vector)
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
    chunk_vectors, idf = _tfidf_vectors(tokenized_chunks)
    query_vector = _tfidf_vector(q_tokens, idf)
    scored: list[dict[str, Any]] = []
    for chunk, c_tokens, chunk_vector in zip(normalized_chunks, tokenized_chunks, chunk_vectors, strict=True):
        c_counts = Counter(c_tokens)
        lexical_score = _lexical_score(q_counts, c_counts)
        vector_score = _cosine_similarity(query_vector, chunk_vector)
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


def retrieve(
    query: str,
    chunks: list[dict[str, Any]] | None = None,
    *,
    top_k: int = 5,
    strategy: str = "hybrid",
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
    return hybrid_retrieve(query, chunks, top_k=top_k)
