"""Immutable HTTP captures and a serialized, validated observation index."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import Settings
from .utils import file_lock, stable_id, utc_now, write_json

STORE_SCHEMA = "mucaro_web_snapshot_ledger_v1"
MAX_LEDGER_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
ENTRY_FIELDS = (
    "snapshot_id",
    "snapshot_path",
    "snapshot_sha256",
    "requested_url",
    "final_url",
    "retrieval_utc",
    "http_status",
    "content_type",
    "content_sha256",
    "content_size_bytes",
)


class WebCaptureStoreError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail
        self.http_status = (
            403
            if "BOUNDARY" in status
            else 413
            if "LIMIT" in status
            else 404
            if "NOT_FOUND" in status
            else 500
            if "WRITE" in status
            else 422
            if "ARGUMENTS" in status
            else 409
        )


def ledger_path(cfg: Settings) -> Path:
    directory = cfg.export_dir / "web_snapshots"
    if directory.is_symlink():
        raise WebCaptureStoreError(
            "ERROR_WEB_CAPTURE_BOUNDARY",
            "The capture directory cannot be a symbolic link. Preserve the existing collection and choose a regular data directory.",
        )
    return directory / "web_snapshot_ledger.json"


def _invalid() -> WebCaptureStoreError:
    return WebCaptureStoreError(
        "ERROR_WEB_CAPTURE_STORE_INVALID",
        "The web-capture index is damaged or inconsistent. Preserve the data directory and restore a known backup before capturing again.",
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json_constant(value: str) -> None:
    raise ValueError("non-finite JSON value")


def _bytes(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise _invalid()
        payload = source.read(limit + 1)
        after = os.fstat(source.fileno())
    if len(payload) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise _invalid()
    return payload


def _valid_entry(entry: Any) -> bool:
    if not isinstance(entry, dict) or set(entry) != set(ENTRY_FIELDS):
        return False
    strings = [name for name in ENTRY_FIELDS if name not in {"http_status", "content_size_bytes"}]
    if any(not isinstance(entry[key], str) or not entry[key] for key in strings):
        return False
    try:
        timestamp = datetime.fromisoformat(entry["retrieval_utc"].replace("Z", "+00:00"))
        if timestamp.utcoffset() != timedelta(0):
            return False
    except ValueError:
        return False
    expected_id = stable_id(
        "websnap", entry["requested_url"], entry["final_url"], entry["content_sha256"], entry["retrieval_utc"]
    )
    return (
        entry["snapshot_id"] == expected_id
        and Path(entry["snapshot_path"]).is_absolute()
        and "\x00" not in entry["snapshot_path"]
        and Path(entry["snapshot_path"]).name == f"{expected_id}.json"
        and all(re.fullmatch(r"[0-9a-f]{64}", entry[key]) for key in ("snapshot_sha256", "content_sha256"))
        and type(entry["http_status"]) is int
        and 100 <= entry["http_status"] <= 599
        and type(entry["content_size_bytes"]) is int
        and entry["content_size_bytes"] >= 0
    )


def load_capture_ledger(cfg: Settings) -> dict[str, Any]:
    """Validate recorded observations without resolving hosts or reacquiring sources."""
    path = ledger_path(cfg)
    try:
        raw = _bytes(path, MAX_LEDGER_BYTES)
    except FileNotFoundError:
        if path.is_symlink():
            raise _invalid()
        return {"schema_version": STORE_SCHEMA, "entries": [], "entry_count": 0}
    except OSError as exc:
        raise _invalid() from exc
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_json_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc
    if not isinstance(payload, dict) or set(payload) - {"schema_version", "entries", "entry_count", "updated_at"}:
        raise _invalid()
    if "updated_at" in payload:
        try:
            if not isinstance(payload["updated_at"], str) or datetime.fromisoformat(
                payload["updated_at"].replace("Z", "+00:00")
            ).utcoffset() != timedelta(0):
                raise ValueError("index update time must be UTC")
        except ValueError as exc:
            raise _invalid() from exc
    entries = payload.get("entries")
    if (
        payload.get("schema_version") != STORE_SCHEMA
        or not isinstance(entries, list)
        or type(payload.get("entry_count")) is not int
        or payload["entry_count"] != len(entries)
        or any(not _valid_entry(entry) for entry in entries)
    ):
        raise _invalid()
    ids = [entry["snapshot_id"] for entry in entries]
    if len(ids) != len(set(ids)):
        raise _invalid()
    return payload


def _encoded(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def preflight_capture_store(cfg: Settings) -> None:
    try:
        with file_lock(ledger_path(cfg)):
            load_capture_ledger(cfg)
    except OSError as exc:
        raise WebCaptureStoreError(
            "ERROR_WEB_CAPTURE_STORE_WRITE",
            "The web-capture store is unavailable. Check storage permissions and capacity before capturing again.",
        ) from exc


def _publish_capture(path: Path, raw: bytes) -> None:
    """Publish complete bytes exclusively; an existing frozen file is never replaced."""
    descriptor, temporary = tempfile.mkstemp(prefix=".capture-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _bytes(path, MAX_SNAPSHOT_BYTES) != raw:
                raise WebCaptureStoreError(
                    "ERROR_WEB_CAPTURE_IDENTITY_CONFLICT",
                    "A frozen capture with this ID has different bytes. The earlier capture was preserved.",
                )
    finally:
        Path(temporary).unlink(missing_ok=True)


def persist_web_snapshot(snapshot: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    """Commit one exact capture and index row under the shared process/thread lock."""
    try:
        if not isinstance(snapshot["content_base64"], str) or len(snapshot["content_base64"]) > MAX_SNAPSHOT_BYTES:
            raise WebCaptureStoreError(
                "ERROR_WEB_CAPTURE_STORE_LIMIT",
                "The capture exceeds its encoded-byte budget. Lower the capture byte limit.",
            )
        content = base64.b64decode(snapshot["content_base64"], validate=True)
        expected_id = stable_id(
            "websnap",
            snapshot["requested_url"],
            snapshot["final_url"],
            snapshot["content_sha256"],
            snapshot["retrieval_utc"],
        )
        if (
            snapshot["schema_version"] != "mucaro_web_snapshot_v1"
            or snapshot["snapshot_id"] != expected_id
            or hashlib.sha256(content).hexdigest() != snapshot["content_sha256"]
            or type(snapshot["content_size_bytes"]) is not int
            or len(content) != snapshot["content_size_bytes"]
        ):
            raise _invalid()
        raw = _encoded(snapshot)
        digest = hashlib.sha256(raw).hexdigest()
        path = ledger_path(cfg).parent / f"{expected_id}.json"
        entry = {key: snapshot[key] for key in ENTRY_FIELDS if key not in {"snapshot_path", "snapshot_sha256"}}
        entry.update(snapshot_path=str(path), snapshot_sha256=digest)
        if not _valid_entry(entry):
            raise _invalid()
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise WebCaptureStoreError(
            "ERROR_WEB_CAPTURE_STORE_LIMIT",
            "The frozen capture exceeds the 16 MiB JSON budget. Lower the capture byte limit.",
        )
    published = False
    try:
        with file_lock(ledger_path(cfg)):
            ledger = load_capture_ledger(cfg)
            entries = ledger["entries"]
            existing = next((row for row in entries if row["snapshot_id"] == expected_id), None)
            if existing is not None and existing != entry:
                raise WebCaptureStoreError(
                    "ERROR_WEB_CAPTURE_IDENTITY_CONFLICT",
                    "This capture ID already has a different observation binding. The prior index and capture were preserved.",
                )
            updated = {
                "schema_version": STORE_SCHEMA,
                "entries": [*entries, entry],
                "entry_count": len(entries) + 1,
                "updated_at": utc_now(),
            }
            if existing is None and len(_encoded(updated)) > MAX_LEDGER_BYTES:
                raise WebCaptureStoreError(
                    "ERROR_WEB_CAPTURE_STORE_LIMIT",
                    "The capture index exceeds its 32 MiB budget. Preserve and archive the data directory before starting a new collection.",
                )
            _publish_capture(path, raw)
            published = True
            if existing is None:
                write_json(ledger_path(cfg), updated)
            return {
                "snapshot_path": str(path),
                "snapshot_sha256": digest,
                "ledger_path": str(ledger_path(cfg)),
                "ledger_entry_count": len(entries) + (existing is None),
            }
    except OSError as exc:
        retained = (
            f" Saved capture ID: {expected_id}; SHA-256: {digest}. Recover those exact bytes without fetching the URL again."
            if published
            else ""
        )
        raise WebCaptureStoreError(
            "ERROR_WEB_CAPTURE_STORE_WRITE",
            "The capture could not be committed. Existing indexed observations were preserved; check storage permissions or capacity."
            + retained,
        ) from exc


def recover_web_snapshot(snapshot_id: str, expected_sha256: str, cfg: Settings) -> dict[str, Any]:
    """Index exactly selected frozen bytes without reacquisition or rewriting history."""
    if (
        not isinstance(snapshot_id, str)
        or re.fullmatch(r"websnap_[0-9a-f]{16}", snapshot_id) is None
        or not isinstance(expected_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
    ):
        raise WebCaptureStoreError(
            "ERROR_WEB_CAPTURE_ARGUMENTS_INVALID",
            "Use the saved capture ID and its complete SHA-256 from the failed capture receipt.",
        )
    try:
        raw = _bytes(ledger_path(cfg).parent / f"{snapshot_id}.json", MAX_SNAPSHOT_BYTES)
    except FileNotFoundError as exc:
        raise WebCaptureStoreError(
            "ERROR_WEB_CAPTURE_NOT_FOUND", "The selected saved capture is unavailable. No URL was fetched."
        ) from exc
    except OSError as exc:
        raise WebCaptureStoreError(
            "ERROR_WEB_CAPTURE_BOUNDARY", "The selected capture is not an accessible regular saved file."
        ) from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise WebCaptureStoreError(
            "ERROR_WEB_CAPTURE_IDENTITY_CONFLICT",
            "The saved bytes differ from the selected SHA-256. Preserve the file for inspection; no URL was fetched.",
        )
    try:
        snapshot = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_json_constant)
        if not isinstance(snapshot, dict) or snapshot.get("snapshot_id") != snapshot_id or _encoded(snapshot) != raw:
            raise _invalid()
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc
    result = persist_web_snapshot(snapshot, cfg)
    return {"status": "WEB_CAPTURE_RECOVERED", "snapshot_id": snapshot_id, "acquisition_repeated": False, **result}
