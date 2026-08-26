from __future__ import annotations

import hashlib
import math
from typing import Any

from .config import Settings, settings
from .utils import tokenize, utc_now, write_json, read_json
from .vector_index import cosine_similarity, fingerprint_chunks

STORE_NAME = "semantic_store.json"
STORE_VERSION = "local_semantic_hash_store_v1"
EMBEDDING_MODEL = "local_hash_embedding_v1"
EMBEDDING_DIMENSIONS = 384
MIN_SEMANTIC_SCORE = 0.1


def semantic_store_path(cfg: Settings = settings):
    return cfg.index_dir / STORE_NAME


def _feature_sign_and_index(feature: str, dimensions: int) -> tuple[int, float]:
    digest = hashlib.sha256(feature.encode("utf-8", errors="replace")).digest()
    index = int.from_bytes(digest[:4], "big") % dimensions
    sign = 1.0 if digest[4] % 2 == 0 else -1.0
    return index, sign


def _token_features(text: str) -> list[str]:
    tokens = tokenize(text)
    features: list[str] = []
    for token in tokens:
        features.append(f"tok:{token}")
        if len(token) >= 4:
            features.append(f"pre:{token[:4]}")
            features.append(f"suf:{token[-4:]}")
        padded = f"^{token}$"
        for size in (3, 4, 5):
            if len(padded) >= size:
                features.extend(f"ng{size}:{padded[index:index + size]}" for index in range(len(padded) - size + 1))
    return features


def embed_text(text: str, *, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    for feature in _token_features(text):
        index, sign = _feature_sign_and_index(feature, dimensions)
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [round(value / norm, 8) for value in vector]


def _normalize_chunk(raw_chunk: dict[str, Any], fallback_index: int, warnings: list[str]) -> dict[str, Any] | None:
    chunk_id = raw_chunk.get("chunk_id")
    source_id = raw_chunk.get("source_id")
    text = raw_chunk.get("text")
    if not isinstance(chunk_id, str) or not chunk_id.strip():
        warnings.append(f"SEMANTIC_STORE_SKIP_MISSING_CHUNK_ID:index={fallback_index}")
        return None
    if not isinstance(source_id, str) or not source_id.strip():
        warnings.append(f"SEMANTIC_STORE_SKIP_MISSING_SOURCE_ID:chunk_id={chunk_id}")
        return None
    if not isinstance(text, str) or not text.strip():
        warnings.append(f"SEMANTIC_STORE_SKIP_EMPTY_TEXT:chunk_id={chunk_id}")
        return None
    chunk = dict(raw_chunk)
    chunk["chunk_id"] = chunk_id
    chunk["source_id"] = source_id
    chunk["text"] = text
    try:
        chunk["chunk_index"] = int(raw_chunk.get("chunk_index", fallback_index))
    except (TypeError, ValueError):
        warnings.append(f"SEMANTIC_STORE_COERCED_CHUNK_INDEX:chunk_id={chunk_id}")
        chunk["chunk_index"] = fallback_index
    return chunk


def build_semantic_store(chunks: list[dict[str, Any]], cfg: Settings = settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    warnings: list[str] = []
    entries: list[dict[str, Any]] = []
    for fallback_index, raw_chunk in enumerate(chunks):
        chunk = _normalize_chunk(raw_chunk, fallback_index, warnings)
        if chunk is None:
            continue
        embedding = embed_text(str(chunk.get("text", "")))
        if not any(embedding):
            warnings.append(f"SEMANTIC_STORE_SKIP_EMPTY_EMBEDDING:chunk_id={chunk['chunk_id']}")
            continue
        entries.append(
            {
                "chunk": chunk,
                "embedding": embedding,
                "tokens": tokenize(str(chunk.get("text", ""))),
            }
        )
    payload = {
        "version": STORE_VERSION,
        "status": "SEMANTIC_STORE_OK",
        "built_at": utc_now(),
        "chunk_count": len(chunks),
        "indexed_chunk_count": len(entries),
        "chunks_fingerprint": fingerprint_chunks(chunks),
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "min_semantic_score": MIN_SEMANTIC_SCORE,
        "entries": entries,
        "warnings": sorted(set(warnings)),
    }
    write_json(semantic_store_path(cfg), payload)
    return payload


def load_semantic_store(cfg: Settings = settings) -> dict[str, Any] | None:
    payload = read_json(semantic_store_path(cfg), None)
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != STORE_VERSION:
        return None
    return payload


def get_semantic_store_status(chunks: list[dict[str, Any]], cfg: Settings = settings) -> dict[str, Any]:
    store = load_semantic_store(cfg)
    if store is None:
        return {
            "status": "SEMANTIC_STORE_MISSING",
            "chunk_count": len(chunks),
            "indexed_chunk_count": 0,
            "stale": bool(chunks),
        }
    stale = store.get("chunks_fingerprint") != fingerprint_chunks(chunks)
    return {
        "status": "SEMANTIC_STORE_STALE" if stale else "SEMANTIC_STORE_OK",
        "version": store.get("version"),
        "embedding_model": store.get("embedding_model"),
        "embedding_dimensions": store.get("embedding_dimensions"),
        "min_semantic_score": store.get("min_semantic_score", MIN_SEMANTIC_SCORE),
        "built_at": store.get("built_at"),
        "chunk_count": len(chunks),
        "indexed_chunk_count": store.get("indexed_chunk_count", 0),
        "stale": stale,
        "warnings": store.get("warnings", []),
    }


def query_semantic_store(query: str, store: dict[str, Any], *, top_k: int) -> list[dict[str, Any]]:
    query_embedding = embed_text(query)
    q_tokens = set(tokenize(query))
    scored: list[dict[str, Any]] = []
    for entry in store.get("entries", []):
        if not isinstance(entry, dict):
            continue
        chunk = entry.get("chunk")
        embedding = entry.get("embedding")
        tokens = entry.get("tokens", [])
        if not isinstance(chunk, dict) or not isinstance(embedding, list):
            continue
        score = cosine_similarity(
            {str(index): value for index, value in enumerate(query_embedding) if value},
            {str(index): float(value) for index, value in enumerate(embedding) if value},
        )
        if score < MIN_SEMANTIC_SCORE:
            continue
        item = dict(chunk)
        item["score"] = round(score, 6)
        item["semantic_score"] = round(score, 6)
        item["retrieval_strategy"] = STORE_VERSION
        item["matched_terms"] = sorted(q_tokens.intersection(set(tokens)))[:20] if isinstance(tokens, list) else []
        scored.append(item)
    return sorted(
        scored,
        key=lambda item: (
            -item["score"],
            item.get("source_id", ""),
            item.get("chunk_index", 0),
            item.get("chunk_id", ""),
        ),
    )[:top_k]
