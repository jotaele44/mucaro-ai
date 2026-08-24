from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .chunker import chunk_text
from .config import Settings, settings
from .db import init_db, upsert_payload
from .parser import detect_mime, parse_file
from .utils import read_json, sha256_file, stable_id, utc_now, write_json

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
    return {"status": "INGEST_OK", "source": source_payload, "chunks_added": len(chunks)}


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
