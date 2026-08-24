from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from typing import Any

from .config import Settings, settings
from .utils import read_json, tokenize, utc_now, write_json

INDEX_NAME = "tfidf_index.json"
INDEX_VERSION = "local_tfidf_index_v1"


def _chunk_fingerprint_payload(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": str(chunk.get("chunk_id", "")),
            "source_id": str(chunk.get("source_id", "")),
            "chunk_index": chunk.get("chunk_index"),
            "text": str(chunk.get("text", "")),
        }
        for chunk in chunks
    ]


def fingerprint_chunks(chunks: list[dict[str, Any]]) -> str:
    payload = json.dumps(_chunk_fingerprint_payload(chunks), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    counts = Counter(tokens)
    weighted = {term: (1 + math.log(count)) * idf.get(term, 0.0) for term, count in counts.items()}
    norm = math.sqrt(sum(value * value for value in weighted.values()))
    if not norm:
        return {}
    return {term: value / norm for term, value in weighted.items()}


def tfidf_vectors(tokenized_chunks: list[list[str]]) -> tuple[list[dict[str, float]], dict[str, float]]:
    document_count = len(tokenized_chunks)
    document_frequencies: Counter[str] = Counter()
    for tokens in tokenized_chunks:
        document_frequencies.update(set(tokens))
    idf = {
        term: math.log((1 + document_count) / (1 + document_frequency)) + 1
        for term, document_frequency in document_frequencies.items()
    }
    return [tfidf_vector(tokens, idf) for tokens in tokenized_chunks], idf


def cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(term, 0.0) for term, value in left.items())


def index_path(cfg: Settings = settings):
    return cfg.index_dir / INDEX_NAME


def build_persistent_index(chunks: list[dict[str, Any]], cfg: Settings = settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    warnings: list[str] = []
    indexed: list[dict[str, Any]] = []
    for fallback_index, raw_chunk in enumerate(chunks):
        chunk_id = raw_chunk.get("chunk_id")
        source_id = raw_chunk.get("source_id")
        text = raw_chunk.get("text")
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            warnings.append(f"INDEX_SKIP_MISSING_CHUNK_ID:index={fallback_index}")
            continue
        if not isinstance(source_id, str) or not source_id.strip():
            warnings.append(f"INDEX_SKIP_MISSING_SOURCE_ID:chunk_id={chunk_id}")
            continue
        if not isinstance(text, str) or not text.strip():
            warnings.append(f"INDEX_SKIP_EMPTY_TEXT:chunk_id={chunk_id}")
            continue
        tokens = tokenize(text)
        if not tokens:
            warnings.append(f"INDEX_SKIP_NO_TOKENS:chunk_id={chunk_id}")
            continue
        chunk = dict(raw_chunk)
        chunk["chunk_id"] = chunk_id
        chunk["source_id"] = source_id
        chunk["text"] = text
        try:
            chunk["chunk_index"] = int(raw_chunk.get("chunk_index", fallback_index))
        except (TypeError, ValueError):
            warnings.append(f"INDEX_COERCED_CHUNK_INDEX:chunk_id={chunk_id}")
            chunk["chunk_index"] = fallback_index
        indexed.append({"chunk": chunk, "tokens": tokens})

    vectors, idf = tfidf_vectors([entry["tokens"] for entry in indexed]) if indexed else ([], {})
    entries = [
        {
            "chunk": entry["chunk"],
            "tokens": entry["tokens"],
            "vector": vector,
        }
        for entry, vector in zip(indexed, vectors, strict=True)
    ]
    payload = {
        "version": INDEX_VERSION,
        "status": "INDEX_OK",
        "built_at": utc_now(),
        "chunk_count": len(chunks),
        "indexed_chunk_count": len(entries),
        "chunks_fingerprint": fingerprint_chunks(chunks),
        "idf": idf,
        "entries": entries,
        "warnings": sorted(set(warnings)),
    }
    write_json(index_path(cfg), payload)
    return payload


def load_persistent_index(cfg: Settings = settings) -> dict[str, Any] | None:
    payload = read_json(index_path(cfg), None)
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != INDEX_VERSION:
        return None
    return payload
