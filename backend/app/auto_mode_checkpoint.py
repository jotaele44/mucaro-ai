"""Fail-closed, dependency-free Auto Mode checkpoint ledger.

This module never runs commands, makes network requests, or grants permissions.
Snapshots are integrity-checked and atomically replaced within a preselected path.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

from backend.app.auto_mode_scheduler import AutoModeError, Vector, schedule

SCHEMA_VERSION = 1


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _payload(vectors: tuple[Vector, ...], states: Mapping[str, str]) -> dict:
    # Validate all supplied values with the scheduler before serialization.
    schedule(vectors, states)
    return {
        "schema": SCHEMA_VERSION,
        "vectors": [
            {"id": v.id, "dependencies": list(v.dependencies), "isolation_key": v.isolation_key,
             "requires_authorization": v.requires_authorization}
            for v in vectors
        ],
        "states": dict(states),
    }


def encode_checkpoint(vectors: tuple[Vector, ...], states: Mapping[str, str]) -> bytes:
    payload = _payload(vectors, states)
    return _canonical({"payload": payload, "sha256": hashlib.sha256(_canonical(payload)).hexdigest()})


def decode_checkpoint(raw: bytes) -> tuple[tuple[Vector, ...], dict[str, str]]:
    if not isinstance(raw, bytes) or len(raw) > 2_000_000:
        raise AutoModeError("INVALID_CHECKPOINT")
    try:
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "sha256"}:
            raise ValueError("envelope")
        payload = envelope["payload"]
        if not isinstance(payload, dict) or set(payload) != {"schema", "vectors", "states"}:
            raise ValueError("payload")
        if type(payload["schema"]) is not int or payload["schema"] != SCHEMA_VERSION:
            raise ValueError("schema")
        if not isinstance(envelope["sha256"], str) or not hmac.compare_digest(
            envelope["sha256"], hashlib.sha256(_canonical(payload)).hexdigest()
        ):
            raise ValueError("digest")
        if not isinstance(payload["vectors"], list) or not isinstance(payload["states"], dict):
            raise ValueError("shape")
        vectors = []
        for item in payload["vectors"]:
            if not isinstance(item, dict) or set(item) != {
                "id", "dependencies", "isolation_key", "requires_authorization"
            } or not isinstance(item["dependencies"], list):
                raise ValueError("vector")
            vectors.append(Vector(
                id=item["id"], dependencies=tuple(item["dependencies"]),
                isolation_key=item["isolation_key"],
                requires_authorization=item["requires_authorization"],
            ))
        result = (tuple(vectors), payload["states"])
        schedule(*result)
        if encode_checkpoint(*result) != raw:
            raise ValueError("noncanonical")
        return result
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError) as exc:
        raise AutoModeError("INVALID_CHECKPOINT") from exc


def save_checkpoint(path: Path, vectors: tuple[Vector, ...], states: Mapping[str, str]) -> str:
    target = Path(path)
    if target.is_symlink() or not target.parent.is_dir():
        raise AutoModeError("INVALID_CHECKPOINT_PATH")
    data = encode_checkpoint(vectors, states)
    fd, temporary = tempfile.mkstemp(prefix=".auto-mode-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return hashlib.sha256(data).hexdigest()


def load_checkpoint(path: Path) -> tuple[tuple[Vector, ...], dict[str, str]]:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise AutoModeError("INVALID_CHECKPOINT_PATH")
    return decode_checkpoint(target.read_bytes())
