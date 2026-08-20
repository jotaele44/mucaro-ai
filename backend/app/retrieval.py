from __future__ import annotations

import math
from collections import Counter
from typing import Any

from .ingestion import get_chunks
from .utils import tokenize


class RetrievalError(RuntimeError):
    pass


def lexical_retrieve(query: str, chunks: list[dict[str, Any]] | None = None, *, top_k: int = 5) -> dict[str, Any]:
    if not query.strip():
        raise RetrievalError("ERROR_EMPTY_QUERY")
    chunks = get_chunks() if chunks is None else chunks
    if not chunks:
        return {"status": "ERROR_NO_SOURCES", "query": query, "retrieved_chunks": []}
    q_tokens = tokenize(query)
    q_counts = Counter(q_tokens)
    scored: list[dict[str, Any]] = []
    for chunk in chunks:
        c_tokens = tokenize(str(chunk.get("text", "")))
        if not c_tokens:
            continue
        c_counts = Counter(c_tokens)
        overlap = sum(min(q_counts[t], c_counts[t]) for t in q_counts)
        if overlap == 0:
            continue
        norm = math.sqrt(sum(v * v for v in q_counts.values())) * math.sqrt(sum(v * v for v in c_counts.values()))
        score = float(overlap / norm) if norm else 0.0
        item = dict(chunk)
        item["score"] = round(score, 6)
        item["matched_terms"] = sorted(set(q_counts).intersection(c_counts))[:20]
        scored.append(item)
    scored.sort(key=lambda x: (-x["score"], x.get("source_id", ""), x.get("chunk_index", 0)))
    if not scored:
        return {"status": "ERROR_NO_RETRIEVAL_CONTEXT", "query": query, "retrieved_chunks": []}
    return {"status": "RETRIEVAL_OK", "query": query, "retrieved_chunks": scored[:top_k]}
