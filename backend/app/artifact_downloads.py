"""Authenticated callers retrieve verified artifact bytes, never arbitrary host paths."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from .artifact_identity import ArtifactIdentityError, list_artifacts
from .config import Settings

MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024


class ArtifactDownloadError(RuntimeError):
    def __init__(self, status: str, detail: str, http_status: int):
        super().__init__(detail)
        self.status, self.detail, self.http_status = status, detail, http_status


def _records(cfg: Settings) -> list[dict[str, Any]]:
    try:
        return list_artifacts(cfg.processed_dir)
    except ArtifactIdentityError as exc:
        raise ArtifactDownloadError(
            "ERROR_ARTIFACT_STORE_INVALID",
            "Artifact lineage is invalid; restore or repair the store before downloading.",
            409,
        ) from exc


def _output_path(record: dict[str, Any], cfg: Settings) -> Path | None:
    if record["artifact_type"] not in {"DERIVED", "EXPORT"}:
        return None
    path = Path(record.get("source_path_or_uri") or "")
    try:
        if path.is_absolute() and not path.is_symlink() and path.resolve().is_relative_to(cfg.export_dir.resolve()):
            return path
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def _summary(record: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    path = _output_path(record, cfg)
    return {
        **{
            key: record[key]
            for key in (
                "artifact_id",
                "artifact_type",
                "sha256",
                "byte_size",
                "producer",
                "producer_version",
                "created_utc",
                "parent_artifact_ids",
            )
        },
        "filename": path.name if path else None,
        "download_available": path is not None,
        "byte_verification": "CHECKED_ON_DOWNLOAD" if path else "NOT_DOWNLOADABLE",
        "provenance_state": "RECORDED" if record.get("provenance") else "LEGACY_WITHOUT_PROVENANCE",
    }


def list_output_artifacts(
    cfg: Settings,
    *,
    limit: int = 50,
    offset: int = 0,
    revision: str | None = None,
    query: str = "",
) -> dict[str, Any]:
    """Page a validated logical store snapshot; filenames are discovery labels only."""
    if not 1 <= limit <= 100 or offset < 0 or len(query) > 200:
        raise ArtifactDownloadError(
            "ERROR_ARTIFACT_QUERY_INVALID", "Choose a page size from 1 to 100 and a filter up to 200 characters.", 422
        )
    records = _records(cfg)
    snapshot = hashlib.sha256(
        json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    if revision is not None and revision != snapshot:
        raise ArtifactDownloadError(
            "ERROR_ARTIFACT_STORE_CHANGED",
            "Saved outputs changed. Refresh the library before loading another page.",
            409,
        )
    rows = [_summary(record, cfg) for record in records if record["artifact_type"] in {"DERIVED", "EXPORT"}]
    needle = query.casefold().strip()
    if needle:
        rows = [
            row
            for row in rows
            if needle in " ".join(str(row[key] or "") for key in ("artifact_id", "filename", "producer")).casefold()
        ]
    rows.sort(key=lambda row: (row["created_utc"], row["artifact_id"]), reverse=True)
    end = offset + limit
    return {
        "status": "ARTIFACT_LIBRARY_OK",
        "revision": snapshot,
        "total": len(rows),
        "offset": offset,
        "next_offset": end if end < len(rows) else None,
        "query": query,
        "artifacts": rows[offset:end],
        "limitations": [
            "Byte integrity is checked when downloading, not by metadata listing.",
            "Text filtering discovers labels; it does not establish identity or completeness outside this store.",
        ],
    }


def inspect_output_artifact(artifact_id: str, cfg: Settings) -> dict[str, Any]:
    records = _records(cfg)
    index = {record["artifact_id"]: record for record in records}
    record = index.get(artifact_id)
    if record is None:
        raise ArtifactDownloadError("ERROR_ARTIFACT_DOWNLOAD_NOT_FOUND", "The saved artifact was not found.", 404)
    return {
        "status": "ARTIFACT_DETAIL_OK",
        "artifact": _summary(record, cfg),
        "parents": [_summary(index[parent], cfg) for parent in record["parent_artifact_ids"]],
    }


def read_artifact_download(artifact_id: str, cfg: Settings) -> tuple[bytes, str, str, str]:
    record = next((item for item in _records(cfg) if item["artifact_id"] == artifact_id), None)
    if record is None or record["artifact_type"] not in {"DERIVED", "EXPORT"}:
        raise ArtifactDownloadError(
            "ERROR_ARTIFACT_DOWNLOAD_NOT_FOUND", "This artifact has no downloadable output file.", 404
        )
    path = _output_path(record, cfg)
    if path is None:
        raise ArtifactDownloadError(
            "ERROR_ARTIFACT_DOWNLOAD_BOUNDARY",
            "Only recorded files inside the app's export directory can be downloaded.",
            403,
        )
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_DOWNLOAD_BYTES:
                raise ArtifactDownloadError(
                    "ERROR_ARTIFACT_DOWNLOAD_LIMIT", "Output is not a regular file or exceeds the download limit.", 413
                )
            payload = source.read(MAX_DOWNLOAD_BYTES + 1)
            after = os.fstat(source.fileno())
    except OSError as exc:
        raise ArtifactDownloadError(
            "ERROR_ARTIFACT_DOWNLOAD_NOT_FOUND", "The recorded output file is unavailable.", 404
        ) from exc
    if (
        len(payload) != record["byte_size"]
        or len(payload) > MAX_DOWNLOAD_BYTES
        or hashlib.sha256(payload).hexdigest() != record["sha256"]
        or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    ):
        raise ArtifactDownloadError(
            "ERROR_ARTIFACT_DOWNLOAD_CHANGED",
            "Output bytes differ from the saved artifact. Re-run the task or restore the original output.",
            409,
        )
    media_type = {
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".geojson": "application/geo+json",
        ".json": "application/json",
    }.get(path.suffix.lower(), "application/octet-stream")
    return payload, path.name, media_type, record["sha256"]
