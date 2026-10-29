"""Authenticated callers retrieve verified artifact bytes, never arbitrary host paths."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .artifact_identity import ArtifactIdentityError, list_artifacts
from .config import Settings

MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024


class ArtifactDownloadError(RuntimeError):
    def __init__(self, status: str, detail: str, http_status: int):
        super().__init__(detail)
        self.status, self.detail, self.http_status = status, detail, http_status


def read_artifact_download(artifact_id: str, cfg: Settings) -> tuple[bytes, str, str, str]:
    try:
        record = next((item for item in list_artifacts(cfg.processed_dir) if item["artifact_id"] == artifact_id), None)
    except ArtifactIdentityError as exc:
        raise ArtifactDownloadError(
            "ERROR_ARTIFACT_STORE_INVALID",
            "Artifact lineage is invalid; restore or repair the store before downloading.",
            409,
        ) from exc
    if record is None or record["artifact_type"] not in {"DERIVED", "EXPORT"}:
        raise ArtifactDownloadError(
            "ERROR_ARTIFACT_DOWNLOAD_NOT_FOUND", "This artifact has no downloadable output file.", 404
        )
    path = Path(record.get("source_path_or_uri") or "")
    if not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(cfg.export_dir.resolve()):
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
