from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .retrieval_corpus_certification import ALLOWED_REAL_WORLD_ORIGINS, RETRIEVAL_CORPUS_SCHEMA_VERSION, RetrievalCorpusError


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_real_world_corpus_manifest(
    corpus_root: str | Path,
    *,
    corpus_id: str,
    frozen_at: str,
    sources: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    thresholds: dict[str, int],
) -> dict[str, Any]:
    root = Path(corpus_root).expanduser().resolve()
    if not root.is_dir():
        raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_ROOT", str(root))
    if not isinstance(corpus_id, str) or not corpus_id.strip():
        raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_ID", "corpus_id is required")
    if not isinstance(frozen_at, str) or not frozen_at.strip():
        raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_FROZEN_AT", "frozen_at is required")

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, spec in enumerate(sources):
        if not isinstance(spec, dict):
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_ROW", f"sources[{index}]")
        source_id = spec.get("source_id")
        relative_path = spec.get("path")
        origin = spec.get("origin")
        if not isinstance(source_id, str) or not source_id.strip():
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_ID", f"sources[{index}]")
        if source_id in seen:
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_DUPLICATE", source_id)
        seen.add(source_id)
        if origin not in ALLOWED_REAL_WORLD_ORIGINS:
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_ORIGIN", f"{source_id}:{origin}")
        if spec.get("synthetic") is not False:
            raise RetrievalCorpusError(
                "ERROR_RETRIEVAL_CORPUS_NONSYNTHETIC_REQUIRED",
                f"{source_id} must explicitly declare synthetic=false",
            )
        if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_PATH", source_id)
        target = (root / relative_path).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_ESCAPE", source_id) from exc
        if target.is_symlink() or not target.is_file():
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_MISSING", source_id)
        source_format = str(spec.get("source_format") or target.suffix.lstrip(".")).strip().lower()
        if not source_format:
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_FORMAT", source_id)
        rows.append(
            {
                "source_id": source_id,
                "path": relative_path,
                "sha256": _sha256(target),
                "byte_size": target.stat().st_size,
                "source_format": source_format,
                "origin": origin,
                "synthetic": False,
                "provenance_ref": spec.get("provenance_ref"),
            }
        )

    query_rows: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    for index, query in enumerate(queries):
        if not isinstance(query, dict):
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_QUERY_ROW", f"queries[{index}]")
        query_id = query.get("query_id")
        text = query.get("query")
        expected = query.get("expected_source_ids")
        if not isinstance(query_id, str) or not query_id.strip():
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_QUERY_ID", f"queries[{index}]")
        if query_id in seen_queries:
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_QUERY_DUPLICATE", query_id)
        seen_queries.add(query_id)
        if not isinstance(text, str) or not text.strip():
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_QUERY_TEXT", query_id)
        if not isinstance(expected, list) or not expected or not all(isinstance(item, str) and item in seen for item in expected):
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_QUERY_BINDINGS", query_id)
        query_rows.append(
            {
                "query_id": query_id,
                "query": text.strip(),
                "expected_source_ids": sorted(set(expected)),
                "category": query.get("category"),
            }
        )

    for key in ("min_sources", "min_queries", "min_formats", "min_total_bytes"):
        value = thresholds.get(key) if isinstance(thresholds, dict) else None
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_THRESHOLD", key)

    return {
        "schema_version": RETRIEVAL_CORPUS_SCHEMA_VERSION,
        "corpus_id": corpus_id.strip(),
        "frozen_at": frozen_at.strip(),
        "thresholds": {key: int(thresholds[key]) for key in ("min_sources", "min_queries", "min_formats", "min_total_bytes")},
        "sources": rows,
        "queries": query_rows,
        "builder": {
            "identity": "mucaro_real_world_corpus_materializer_v1",
            "source_count": len(rows),
            "query_count": len(query_rows),
            "manifest_content_sha256": hashlib.sha256(
                json.dumps({"sources": rows, "queries": query_rows}, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        },
    }
