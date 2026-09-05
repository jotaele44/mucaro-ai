from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


RECEIPT_SCHEMA_VERSION = "mucaro_release_receipt_v1"
RECEIPT_DIR = Path("data") / "exports" / "release_receipts"


def receipt_path(root: Path, receipt_type: str, head_sha: str) -> Path:
    return root / RECEIPT_DIR / f"{receipt_type}-{head_sha[:12]}.json"


def write_release_receipt(
    root: Path,
    *,
    receipt_type: str,
    status: str,
    head_sha: str,
    command: list[str],
    payload: dict[str, Any],
) -> Path:
    path = receipt_path(root, receipt_type, head_sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_type": receipt_type,
        "status": status,
        "head_sha": head_sha,
        "created_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "command": command,
        "payload": payload,
    }
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_release_receipt(root: Path, *, receipt_type: str, head_sha: str | None) -> dict[str, Any] | None:
    if not head_sha:
        return None
    path = receipt_path(root, receipt_type, head_sha)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        return None
    if payload.get("receipt_type") != receipt_type:
        return None
    if payload.get("head_sha") != head_sha:
        return None
    return payload
