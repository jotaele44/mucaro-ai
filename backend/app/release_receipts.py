from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import validate_contract


RECEIPT_SCHEMA_VERSION = "mucaro_release_receipt_v1"
RECEIPT_DIR = Path("data") / "exports" / "release_receipts"


def release_receipt_directory(root: Path) -> Path:
    external_data_dir = os.getenv("MUCARO_AI_DATA_DIR")
    if not external_data_dir:
        return root / RECEIPT_DIR
    data_dir = Path(external_data_dir).expanduser()
    if not data_dir.is_absolute():
        raise ValueError("MUCARO_AI_DATA_DIR must be an absolute path")
    return data_dir.resolve() / "exports" / "release_receipts"


def receipt_path(root: Path, receipt_type: str, head_sha: str) -> Path:
    return release_receipt_directory(root) / f"{receipt_type}-{head_sha[:12]}.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _receipt_file_summary(path: Path, payload: dict[str, Any] | None, error: str | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "receipt_id": path.stem,
        "path": str(path),
        "file_sha256": _sha256_file(path) if path.exists() else None,
        "valid": payload is not None and error is None,
        "validation_error": error,
    }
    if payload is not None:
        summary.update(
            {
                "schema_version": payload.get("schema_version"),
                "receipt_type": payload.get("receipt_type"),
                "status": payload.get("status"),
                "head_sha": payload.get("head_sha"),
                "created_utc": payload.get("created_utc"),
                "command": payload.get("command"),
            }
        )
    return summary


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
    validate_contract("release_receipt", receipt, root=root if (root / "schemas").is_dir() else None)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_release_receipt_file(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, str(exc)
    except json.JSONDecodeError as exc:
        return None, f"json_invalid:{exc.msg}"
    if not isinstance(payload, dict):
        return None, f"receipt_payload_type={type(payload).__name__}"
    required = ["schema_version", "receipt_type", "status", "head_sha", "created_utc", "command", "payload"]
    missing = [field for field in required if field not in payload]
    if missing:
        return payload, f"missing={','.join(missing)}"
    if payload.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        return payload, f"schema_version={payload.get('schema_version')}"
    if not isinstance(payload.get("receipt_type"), str) or not payload.get("receipt_type"):
        return payload, "receipt_type_invalid"
    if not isinstance(payload.get("status"), str) or not payload.get("status"):
        return payload, "status_invalid"
    if not isinstance(payload.get("head_sha"), str) or not payload.get("head_sha"):
        return payload, "head_sha_invalid"
    if not isinstance(payload.get("created_utc"), str) or not payload.get("created_utc"):
        return payload, "created_utc_invalid"
    if not isinstance(payload.get("command"), list):
        return payload, "command_invalid"
    if not isinstance(payload.get("payload"), dict):
        return payload, "payload_invalid"
    return payload, None


def list_release_receipts(root: Path) -> list[dict[str, Any]]:
    directory = release_receipt_directory(root)
    if not directory.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        payload, error = read_release_receipt_file(path)
        rows.append(_receipt_file_summary(path, payload, error))
    rows.sort(key=lambda row: str(row.get("created_utc") or ""), reverse=True)
    return rows


def summarize_release_receipts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    valid = 0
    invalid = 0
    for row in rows:
        if row.get("valid"):
            valid += 1
        else:
            invalid += 1
        receipt_type = str(row.get("receipt_type") or "UNKNOWN")
        status = str(row.get("status") or "UNKNOWN")
        by_type[receipt_type] = by_type.get(receipt_type, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "total": len(rows),
        "valid": valid,
        "invalid": invalid,
        "by_type": by_type,
        "by_status": by_status,
    }


def find_release_receipt(root: Path, receipt_id: str) -> dict[str, Any] | None:
    if Path(receipt_id).name != receipt_id:
        return None
    directory = release_receipt_directory(root)
    candidates = [
        directory / receipt_id,
        directory / f"{receipt_id}.json",
    ]
    for path in candidates:
        if path.exists() and path.is_file():
            payload, error = read_release_receipt_file(path)
            return {"summary": _receipt_file_summary(path, payload, error), "receipt": payload}
    return None


def load_release_receipt(root: Path, *, receipt_type: str, head_sha: str | None) -> dict[str, Any] | None:
    if not head_sha:
        return None
    path = receipt_path(root, receipt_type, head_sha)
    if not path.exists():
        return None
    payload, error = read_release_receipt_file(path)
    if payload is None or error is not None:
        return None
    if payload.get("receipt_type") != receipt_type:
        return None
    if payload.get("head_sha") != head_sha:
        return None
    return payload
