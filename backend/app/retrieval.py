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


def _error(status: str, query: str, *, warnings: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status, "query": query, "retrieved_chunks": []}
    if warnings:
        result["warnings"] = sorted(set(warnings))
    return result


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
    normalized["chunk_index"] = int(chunk.get("chunk_index", index) or 0)
    return normalized


def lexical_retrieve(query: str, chunks: list[dict[str, Any]] | None = None, *, top_k: int = 5) -> dict[str, Any]:
    if not query.strip():
        raise RetrievalError("ERROR_EMPTY_QUERY")
    if top_k < MIN_TOP_K or top_k > MAX_TOP_K:
        return _error("ERROR_INVALID_TOP_K", query, warnings=[f"TOP_K_RANGE:{MIN_TOP_K}-{MAX_TOP_K}"])
    chunks = get_chunks() if chunks is None else chunks
    if not chunks:
        return _error("ERROR_NO_SOURCES", query)
    q_tokens = tokenize(query)
    q_counts = Counter(q_tokens)
    scored: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, raw_chunk in enumerate(chunks):
        chunk = _normalize_chunk(raw_chunk, index, warnings)
        if chunk is None:
            continue
        c_tokens = tokenize(str(chunk.get("text", "")))
        if not c_tokens:
            continue
        c_counts = Counter(c_tokens)
        overlap = sum(min(q_counts[t], c_counts[t]) for t in q_counts)
        if overlap == 0:
            continue
        norm = math.sqrt(sum(v * v for v in q_counts.values())) * math.sqrt(sum(v * v for v in c_counts.values()))
        cosine = float(overlap / norm) if norm else 0.0
        query_coverage = len(set(q_counts).intersection(c_counts)) / max(1, len(set(q_counts)))
        score = (0.8 * cosine) + (0.2 * query_coverage)
        item = dict(chunk)
        item["score"] = round(score, 6)
        item["retrieval_strategy"] = "lexical_v2"
        item["matched_terms"] = sorted(set(q_counts).intersection(c_counts))[:20]
        scored.append(item)
    scored.sort(key=lambda x: (-x["score"], x.get("source_id", ""), x.get("chunk_index", 0)))
    if not scored:
        status = "ERROR_NO_VALID_CHUNKS" if warnings and len(warnings) >= len(chunks) else "ERROR_NO_RETRIEVAL_CONTEXT"
        return _error(status, query, warnings=warnings)
    result: dict[str, Any] = {"status": "RETRIEVAL_OK", "query": query, "retrieved_chunks": scored[:top_k]}
    if warnings:
        result["warnings"] = sorted(set(warnings))
    return result
