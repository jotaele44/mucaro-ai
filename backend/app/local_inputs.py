"""Permissioned byte snapshots shared by local document and spatial skills."""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable

from .artifact_identity import ArtifactRecord, create_artifact_record
from .config import Settings
from .permissions import ensure_directory_permission, is_within_path
from .utils import utc_now, write_json


def snapshot_local_file(
    requested: str, expected_sha256: str | None, cfg: Settings, *,
    extensions: set[str], max_bytes: int, namespace: str, producer: str,
    error_type: Callable[[str, str], Exception],
) -> tuple[Path, dict[str, Any], ArtifactRecord]:
    """Resolve grants and acquire one stable regular file before an engine reads it."""
    def fail(code: str, detail: str) -> Exception:
        return error_type(f"ERROR_{namespace.upper()}_{code}", detail)

    def check_access(path: Path) -> None:
        if not is_within_path(path, cfg.input_dir.resolve()):
            ensure_directory_permission(path, cfg)

    selected = Path(requested).expanduser()
    source = (selected if selected.is_absolute() else cfg.project_root / selected).resolve(strict=True)
    check_access(source)
    if source.suffix.lower() not in extensions:
        raise fail("UNSUPPORTED_INPUT", f"Choose a supported local file: {', '.join(sorted(extensions))}.")
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    limit = min(cfg.max_upload_bytes, max_bytes)
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise fail("INPUT_NOT_FILE", "Input must be a regular file.")
        if before.st_size > limit:
            raise fail("INPUT_TOO_LARGE", f"Input exceeds {limit} bytes.")
        raw = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
    current = source.stat(follow_symlinks=False)
    if source.resolve() != source or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino) or (
        before.st_size, before.st_mtime_ns, before.st_ctime_ns
    ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise fail("SOURCE_CHANGED", "Input changed while being acquired; request the operation again.")
    check_access(source)
    if not raw or len(raw) > limit:
        raise fail("INPUT_INVALID", "Input is empty or exceeds the byte limit.")
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise fail("SOURCE_CHANGED", "Input SHA-256 differs from the requested source identity.")
    cfg.ensure_dirs()
    snapshot_dir = Path(tempfile.mkdtemp(prefix=f"{namespace.lower()}-snapshot-", dir=cfg.input_dir))
    frozen = snapshot_dir / f"input{source.suffix.lower()}"
    with frozen.open("xb") as output:
        output.write(raw)
    frozen.chmod(0o400)
    manifest = {
        "requested_path_raw": requested, "resolved_path": str(source), "snapshot_path": str(frozen),
        "retrieval_utc": utc_now(), "bytes": len(raw), "sha256": digest,
    }
    write_json(snapshot_dir / "source_manifest.json", manifest)
    artifact = create_artifact_record(
        processed_dir=cfg.processed_dir, artifact_type="SOURCE", payload=raw,
        logical_identity=f"{namespace.lower()}_source:{frozen}", producer=producer, producer_version="1.0.0",
        source_path_or_uri=str(frozen), provenance_parameters=manifest,
    )
    return frozen, manifest, artifact
