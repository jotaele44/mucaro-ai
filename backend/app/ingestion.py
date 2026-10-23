from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .chunker import chunk_text
from .config import Settings, settings
from .contracts import validate_contract
from .db import delete_chunks_for_source, delete_payload, init_db, upsert_payload
from .format_registry import SUPPORTED_INGEST_SUFFIXES
from .injection_guard import scan_text
from .parser import ParseError, detect_mime, parse_file
from .provenance import build_provenance_record
from .semantic_store import build_semantic_store, get_semantic_store_status
from .utils import read_json, sha256_file, stable_id, utc_now, write_json
from .vector_index import build_persistent_index, fingerprint_chunks, load_persistent_index
from .web_browsing import WEB_SNAPSHOT_SCHEMA_VERSION

MANIFEST_NAME = "source_manifest.json"
CHUNKS_NAME = "document_chunks.json"


def _source_path_string(path: Path, cfg: Settings) -> str:
    return str(path.relative_to(cfg.project_root)) if path.is_relative_to(cfg.project_root) else str(path)


def _store_source_payload(source_payload: dict[str, Any], chunks: list[dict[str, Any]], cfg: Settings) -> dict[str, Any]:
    manifest_path = cfg.processed_dir / MANIFEST_NAME
    chunk_path = cfg.processed_dir / CHUNKS_NAME
    manifest = read_json(manifest_path, [])
    manifest = [m for m in manifest if m.get("source_id") != source_payload["source_id"]]
    manifest.append(source_payload)
    all_chunks = read_json(chunk_path, [])
    all_chunks = [c for c in all_chunks if c.get("source_id") != source_payload["source_id"]]
    all_chunks.extend(chunks)
    write_json(manifest_path, manifest)
    write_json(chunk_path, all_chunks)
    upsert_payload("sources", "source_id", source_payload, cfg.sqlite_path)
    for chunk in chunks:
        upsert_payload("chunks", "chunk_id", chunk, cfg.sqlite_path)
    index_payload = build_persistent_index(all_chunks, cfg)
    semantic_payload = build_semantic_store(all_chunks, cfg)
    return {
        "index_status": index_payload["status"],
        "index_version": index_payload["version"],
        "indexed_chunk_count": index_payload["indexed_chunk_count"],
        "semantic_store_status": semantic_payload["status"],
        "semantic_store_version": semantic_payload["version"],
        "semantic_store_indexed_chunk_count": semantic_payload["indexed_chunk_count"],
    }


def ingest_file(path: Path, cfg: Settings = settings, *, source_kind: str = "LOCAL_FILE") -> dict[str, Any]:
    cfg.ensure_dirs()
    init_db(cfg.sqlite_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")
    size = path.stat().st_size
    if size > cfg.max_ingest_bytes:
        raise IngestLimitError("ERROR_INGEST_FILE_TOO_LARGE", f"{path.name}:{size}>{cfg.max_ingest_bytes}")
    file_sha = sha256_file(path)
    source_id = stable_id("src", file_sha, path.name)
    target = cfg.input_dir / f"{source_id}_{path.name}"
    if path.resolve() != target.resolve():
        shutil.copy2(path, target)
    parse_result = parse_file(target)
    warnings = list(parse_result.get("warnings", []))
    text = str(parse_result.get("text", ""))
    injection_scan = scan_text(text)
    if injection_scan["suspected"]:
        warnings.append("UNTRUSTED_INSTRUCTION_TEXT_DETECTED:" + ",".join(injection_scan["signals"]))
    chunks = chunk_text(source_id, text)
    lineage_status = "VALID" if chunks else "PARTIAL"
    if not chunks:
        warnings.append("NO_TEXT_CHUNKS_EXTRACTED")
    source_payload = {
        "source_id": source_id,
        "source_path": _source_path_string(target, cfg),
        "source_sha256": file_sha,
        "bytes": parse_result.get("bytes", path.stat().st_size),
        "file_name": path.name,
        "mime_type": parse_result.get("mime_type", detect_mime(target)),
        "parser": parse_result.get("parser", "unknown"),
        "parser_warnings": parse_result.get("parser_warnings", warnings),
        "parser_metadata": {
            key: value
            for key, value in parse_result.items()
            if key not in {"text", "parser", "mime_type", "warnings", "parser_warnings", "bytes"}
        },
        "parse_status": parse_result.get("parse_status", "OK"),
        "ingested_at": utc_now(),
        "lineage_status": lineage_status,
        "chunk_count": len(chunks),
        "errors": [],
        "warnings": warnings,
        "provenance": build_provenance_record(
            source_kind=source_kind,
            source_locator=path.name,
            retrieval_utc=utc_now(),
            raw_byte_sha256=file_sha,
            raw_byte_count=size,
            processor=str(parse_result.get("parser", "unknown")),
            row_or_feature_count=parse_result.get("row_count") if isinstance(parse_result.get("row_count"), int) else None,
        ),
    }
    for chunk in chunks:
        chunk.setdefault("trust", "UNTRUSTED_CONTENT")
    validate_contract("source_manifest", source_payload)
    store = _store_source_payload(source_payload, chunks, cfg)
    return {
        "status": "INGEST_OK",
        "source": source_payload,
        "chunks_added": len(chunks),
        **store,
    }


def ingest_web_snapshot(snapshot_path: Path, cfg: Settings = settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    init_db(cfg.sqlite_path)
    if not snapshot_path.exists() or not snapshot_path.is_file():
        raise FileNotFoundError(f"Web snapshot not found: {snapshot_path}")
    snapshot = read_json(snapshot_path, {})
    if snapshot.get("schema_version") != WEB_SNAPSHOT_SCHEMA_VERSION:
        return {
            "status": "ERROR_WEB_SNAPSHOT_SCHEMA",
            "detail": f"Expected {WEB_SNAPSHOT_SCHEMA_VERSION}.",
            "snapshot_path": str(snapshot_path),
        }
    text = str(snapshot.get("text_preview") or "").strip()
    snapshot_file_sha = sha256_file(snapshot_path)
    snapshot_id = str(snapshot.get("snapshot_id") or "")
    final_url = str(snapshot.get("final_url") or "")
    content_sha256 = str(snapshot.get("content_sha256") or "")
    retrieval_utc = str(snapshot.get("retrieval_utc") or "")
    http_error_policy = snapshot.get("http_error_policy") if isinstance(snapshot.get("http_error_policy"), dict) else {}
    if http_error_policy.get("is_http_error") and not http_error_policy.get("citation_allowed"):
        return {
            "status": "ERROR_WEB_SNAPSHOT_NOT_CITABLE",
            "detail": "HTTP error-page snapshots require citation_allowed=true before ingestion.",
            "snapshot_id": snapshot_id,
            "snapshot_path": str(snapshot_path),
        }
    if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
        return {
            "status": "ERROR_WEB_SNAPSHOT_HASH_INVALID",
            "detail": "content_sha256 must be a lowercase hex SHA-256 of the fetched bytes.",
            "snapshot_id": snapshot_id,
            "snapshot_path": str(snapshot_path),
        }
    source_id = stable_id("src_web", snapshot_id, final_url, content_sha256)
    chunks = chunk_text(source_id, text)
    parser_warnings = list(snapshot.get("parser_warnings") or [])
    warnings = list(parser_warnings)
    if not chunks:
        warnings.append("NO_WEB_TEXT_PREVIEW_CHUNKS_EXTRACTED")
    for chunk in chunks:
        chunk.update(
            {
                "source_format": "web_snapshot",
                "source_url": final_url,
                "snapshot_id": snapshot_id,
                "snapshot_sha256": snapshot_file_sha,
                "content_sha256": content_sha256,
                "retrieval_utc": retrieval_utc,
                "parser_warnings": parser_warnings,
                "trust": "UNTRUSTED_CONTENT",
            }
        )
    source_payload = {
        "source_id": source_id,
        "source_path": _source_path_string(snapshot_path, cfg),
        "source_sha256": snapshot_file_sha,
        "bytes": snapshot_path.stat().st_size,
        "file_name": snapshot_path.name,
        "mime_type": str(snapshot.get("content_type") or "application/octet-stream"),
        "parser": "web_snapshot_preview_v1",
        "parser_warnings": parser_warnings,
        "parser_metadata": {
            "source_format": "web_snapshot",
            "snapshot_id": snapshot_id,
            "snapshot_sha256": snapshot_file_sha,
            "requested_url": snapshot.get("requested_url"),
            "fetch_url": snapshot.get("fetch_url"),
            "final_url": final_url,
            "redirect_chain": snapshot.get("redirect_chain", []),
            "retrieval_started_utc": snapshot.get("retrieval_started_utc"),
            "retrieval_utc": retrieval_utc,
            "http_status": snapshot.get("http_status"),
            "content_type": snapshot.get("content_type"),
            "content_sha256": content_sha256,
            "content_size_bytes": snapshot.get("content_size_bytes"),
            "cache_policy": snapshot.get("cache_policy", {}),
            "robots_policy": snapshot.get("robots_policy", {}),
            "http_error_policy": http_error_policy,
            "parser_trace": snapshot.get("parser_trace", {}),
            "charset": snapshot.get("charset"),
            "policy": snapshot.get("policy", {}),
        },
        "parse_status": "OK" if chunks else "PARTIAL",
        "ingested_at": utc_now(),
        "lineage_status": "VALID" if chunks else "PARTIAL",
        "chunk_count": len(chunks),
        "errors": [],
        "warnings": warnings,
        "provenance": build_provenance_record(
            source_kind="WEB_SNAPSHOT",
            source_locator=final_url or str(snapshot.get("requested_url") or snapshot_path.name),
            service_or_repository=None,
            retrieval_utc=retrieval_utc or utc_now(),
            raw_byte_sha256=content_sha256,
            raw_byte_count=int(snapshot.get("content_size_bytes") or 0),
            processor="web_snapshot_preview_v1",
            identity_basis=["BYTE_IDENTITY", "SOURCE_MANIFESTATION_IDENTITY"],
        ),
    }
    validate_contract("source_manifest", source_payload)
    store = _store_source_payload(source_payload, chunks, cfg)
    return {
        "status": "WEB_SNAPSHOT_INGEST_OK",
        "source": source_payload,
        "chunks_added": len(chunks),
        "snapshot_id": snapshot_id,
        "snapshot_sha256": snapshot_file_sha,
        "citation_ready": bool(chunks),
        **store,
    }


FOLDER_SUPPORTED_SUFFIXES = SUPPORTED_INGEST_SUFFIXES


class IngestLimitError(ValueError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(f"{status}:{detail}")
        self.status = status
        self.detail = detail


def ingest_folder(folder: Path, cfg: Settings = settings) -> dict[str, Any]:
    """Ingest direct children of a folder with full accounting.

    Every entry seen is either ingested, skipped with a reason, or failed with a
    status: entries_seen == files_ingested + skipped + failed. Symlinks are never
    followed, so a link inside a granted folder cannot read files outside it.
    """
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"Input folder not found: {folder}")
    root = folder.resolve()
    results = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    entries = sorted(folder.iterdir())
    for path in entries:
        if path.name.startswith("src_"):
            skipped.append({"name": path.name, "reason": "GENERATED_NAME"})
            continue
        if path.is_symlink():
            skipped.append({"name": path.name, "reason": "SYMLINK_NOT_FOLLOWED"})
            continue
        if path.is_dir():
            skipped.append({"name": path.name, "reason": "SUBDIRECTORY_NOT_RECURSED"})
            continue
        if not path.is_file():
            skipped.append({"name": path.name, "reason": "NOT_A_REGULAR_FILE"})
            continue
        if path.resolve().parent != root:
            skipped.append({"name": path.name, "reason": "OUTSIDE_GRANTED_FOLDER"})
            continue
        if path.suffix.lower() not in FOLDER_SUPPORTED_SUFFIXES:
            skipped.append({"name": path.name, "reason": "UNSUPPORTED_FORMAT"})
            continue
        try:
            results.append(ingest_file(path, cfg))
        except (IngestLimitError, ParseError) as exc:
            failed.append({"name": path.name, "status": getattr(exc, "status", "ERROR_PARSE_FAILED"), "detail": str(exc)})
    return {
        "status": "INGEST_FOLDER_OK" if not failed else "INGEST_FOLDER_PARTIAL",
        "entries_seen": len(entries),
        "files_ingested": len(results),
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }


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
