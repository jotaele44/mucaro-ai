from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .chunker import chunk_text
from .config import Settings, settings
from .db import delete_chunks_for_source, delete_payload, init_db, upsert_payload
from .parser import detect_mime, parse_file
from .semantic_store import build_semantic_store, get_semantic_store_status
from .utils import read_json, sha256_file, stable_id, utc_now, write_json
from .vector_index import build_persistent_index, fingerprint_chunks, load_persistent_index

MANIFEST_NAME = "source_manifest.json"
CHUNKS_NAME = "document_chunks.json"


def ingest_file(path: Path, cfg: Settings = settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    init_db(cfg.sqlite_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")
    file_sha = sha256_file(path)
    source_id = stable_id("src", file_sha, path.name)
    target = cfg.input_dir / f"{source_id}_{path.name}"
    if path.resolve() != target.resolve():
        shutil.copy2(path, target)
    parse_result = parse_file(target)
    warnings = list(parse_result.get("warnings", []))
    text = str(parse_result.get("text", ""))
    chunks = chunk_text(source_id, text)
    lineage_status = "VALID" if chunks else "PARTIAL"
    if not chunks:
        warnings.append("NO_TEXT_CHUNKS_EXTRACTED")
    source_payload = {
        "source_id": source_id,
        "source_path": str(target.relative_to(cfg.project_root)) if target.is_relative_to(cfg.project_root) else str(target),
        "source_sha256": file_sha,
        "bytes": parse_result.get("bytes", path.stat().st_size),
        "file_name": path.name,
        "mime_type": parse_result.get("mime_type", detect_mime(target)),
        "parser": parse_result.get("parser", "unknown"),
        "parser_warnings": parse_result.get("parser_warnings", warnings),
        "parser_metadata": {
            key: parse_result[key]
            for key in ["delimiter", "headers", "row_count", "page_count", "parse_status"]
            if key in parse_result
        },
        "parse_status": parse_result.get("parse_status", "OK"),
        "ingested_at": utc_now(),
        "lineage_status": lineage_status,
        "chunk_count": len(chunks),
        "errors": [],
        "warnings": warnings,
    }
    manifest_path = cfg.processed_dir / MANIFEST_NAME
    chunk_path = cfg.processed_dir / CHUNKS_NAME
    manifest = read_json(manifest_path, [])
    manifest = [m for m in manifest if m.get("source_id") != source_id]
    manifest.append(source_payload)
    all_chunks = read_json(chunk_path, [])
    all_chunks = [c for c in all_chunks if c.get("source_id") != source_id]
    all_chunks.extend(chunks)
    write_json(manifest_path, manifest)
    write_json(chunk_path, all_chunks)
    upsert_payload("sources", "source_id", source_payload, cfg.sqlite_path)
    for chunk in chunks:
        upsert_payload("chunks", "chunk_id", chunk, cfg.sqlite_path)
    index_payload = build_persistent_index(all_chunks, cfg)
    semantic_payload = build_semantic_store(all_chunks, cfg)
    return {
        "status": "INGEST_OK",
        "source": source_payload,
        "chunks_added": len(chunks),
        "index_status": index_payload["status"],
        "index_version": index_payload["version"],
        "indexed_chunk_count": index_payload["indexed_chunk_count"],
        "semantic_store_status": semantic_payload["status"],
        "semantic_store_version": semantic_payload["version"],
        "semantic_store_indexed_chunk_count": semantic_payload["indexed_chunk_count"],
    }


def ingest_folder(folder: Path, cfg: Settings = settings) -> dict[str, Any]:
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"Input folder not found: {folder}")
    supported = {".txt", ".md", ".csv", ".json", ".pdf"}
    results = []
    for path in sorted(folder.iterdir()):
        if path.name.startswith("src_"):
            continue
        if path.is_file() and path.suffix.lower() in supported:
            results.append(ingest_file(path, cfg))
    return {"status": "INGEST_FOLDER_OK", "files_ingested": len(results), "results": results}


def get_sources(cfg: Settings = settings) -> list[dict[str, Any]]:
    return read_json(cfg.processed_dir / MANIFEST_NAME, [])


def get_chunks(cfg: Settings = settings) -> list[dict[str, Any]]:
    return read_json(cfg.processed_dir / CHUNKS_NAME, [])


def reindex_sources(cfg: Settings = settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    chunks = get_chunks(cfg)
    index_payload = build_persistent_index(chunks, cfg)
    semantic_payload = build_semantic_store(chunks, cfg)
    return {
        "status": "REINDEX_OK",
        "index_status": index_payload["status"],
        "index_version": index_payload["version"],
        "chunk_count": index_payload["chunk_count"],
        "indexed_chunk_count": index_payload["indexed_chunk_count"],
        "semantic_store_status": semantic_payload["status"],
        "semantic_store_version": semantic_payload["version"],
        "semantic_store_indexed_chunk_count": semantic_payload["indexed_chunk_count"],
        "warnings": index_payload.get("warnings", []),
    }


def get_index_status(cfg: Settings = settings) -> dict[str, Any]:
    chunks = get_chunks(cfg)
    index = load_persistent_index(cfg)
    if index is None:
        return {
            "status": "INDEX_MISSING",
            "chunk_count": len(chunks),
            "indexed_chunk_count": 0,
            "stale": bool(chunks),
            "semantic_store": get_semantic_store_status(chunks, cfg),
        }
    current_fingerprint = fingerprint_chunks(chunks)
    stale = index.get("chunks_fingerprint") != current_fingerprint
    return {
        "status": "INDEX_STALE" if stale else "INDEX_OK",
        "version": index.get("version"),
        "built_at": index.get("built_at"),
        "chunk_count": len(chunks),
        "indexed_chunk_count": index.get("indexed_chunk_count", 0),
        "stale": stale,
        "semantic_store": get_semantic_store_status(chunks, cfg),
        "warnings": index.get("warnings", []),
    }


def delete_source(source_id: str, cfg: Settings = settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    init_db(cfg.sqlite_path)
    manifest_path = cfg.processed_dir / MANIFEST_NAME
    chunk_path = cfg.processed_dir / CHUNKS_NAME
    manifest = get_sources(cfg)
    source = next((item for item in manifest if item.get("source_id") == source_id), None)
    if source is None:
        return {"status": "ERROR_SOURCE_NOT_FOUND", "source_id": source_id}

    kept_manifest = [item for item in manifest if item.get("source_id") != source_id]
    chunks = get_chunks(cfg)
    kept_chunks = [chunk for chunk in chunks if chunk.get("source_id") != source_id]
    removed_chunks = len(chunks) - len(kept_chunks)
    write_json(manifest_path, kept_manifest)
    write_json(chunk_path, kept_chunks)

    managed_file_removed = False
    source_path = source.get("source_path")
    if isinstance(source_path, str):
        candidate = (cfg.project_root / source_path).resolve()
        try:
            is_managed_input = candidate.is_relative_to(cfg.input_dir.resolve())
        except ValueError:
            is_managed_input = False
        if is_managed_input and candidate.exists() and candidate.is_file():
            candidate.unlink()
            managed_file_removed = True

    delete_payload("sources", "source_id", source_id, cfg.sqlite_path)
    delete_chunks_for_source(source_id, cfg.sqlite_path)
    index_payload = build_persistent_index(kept_chunks, cfg)
    semantic_payload = build_semantic_store(kept_chunks, cfg)
    return {
        "status": "SOURCE_DELETE_OK",
        "source_id": source_id,
        "removed_chunks": removed_chunks,
        "managed_file_removed": managed_file_removed,
        "remaining_sources": len(kept_manifest),
        "index_status": index_payload["status"],
        "indexed_chunk_count": index_payload["indexed_chunk_count"],
        "semantic_store_status": semantic_payload["status"],
        "semantic_store_indexed_chunk_count": semantic_payload["indexed_chunk_count"],
    }
