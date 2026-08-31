from __future__ import annotations

from typing import Any

from .config import Settings, settings
from .utils import read_json, stable_id, utc_now, write_json

MEMORY_NAME = "assistant_memory.json"
MEMORY_KINDS = {"preference", "profile", "instruction", "fact"}
MAX_MEMORY_CONTENT_CHARS = 800


class MemoryError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _memory_path(cfg: Settings) -> Any:
    return cfg.processed_dir / MEMORY_NAME


def _normalize_content(content: str) -> str:
    return " ".join(content.strip().split())


def _validate_kind(kind: str) -> str:
    normalized = kind.strip().lower()
    if normalized not in MEMORY_KINDS:
        allowed = ", ".join(sorted(MEMORY_KINDS))
        raise MemoryError("ERROR_MEMORY_KIND_UNSUPPORTED", f"Memory kind must be one of: {allowed}.")
    return normalized


def _load_memory(cfg: Settings) -> list[dict[str, Any]]:
    rows = read_json(_memory_path(cfg), [])
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get("memory_id"), str)]


def _write_memory(rows: list[dict[str, Any]], cfg: Settings) -> None:
    write_json(_memory_path(cfg), rows)


def list_memory_items(cfg: Settings = settings, *, include_disabled: bool = True) -> dict[str, Any]:
    rows = _load_memory(cfg)
    if not include_disabled:
        rows = [row for row in rows if row.get("enabled") is True]
    rows = sorted(rows, key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
    return {
        "status": "MEMORY_OK",
        "memory_count": len(rows),
        "enabled_count": sum(1 for row in rows if row.get("enabled") is True),
        "memories": rows,
    }


def active_memory_context(cfg: Settings = settings) -> list[dict[str, Any]]:
    return list_memory_items(cfg, include_disabled=False)["memories"]


def remember_memory_item(
    content: str,
    cfg: Settings = settings,
    *,
    kind: str = "preference",
    source: str = "operator",
) -> dict[str, Any]:
    cfg.ensure_dirs()
    normalized = _normalize_content(content)
    if not normalized:
        raise MemoryError("ERROR_MEMORY_CONTENT_REQUIRED", "Memory content is required.")
    if len(normalized) > MAX_MEMORY_CONTENT_CHARS:
        raise MemoryError(
            "ERROR_MEMORY_CONTENT_TOO_LONG",
            f"Memory content exceeds {MAX_MEMORY_CONTENT_CHARS} characters.",
        )
    resolved_kind = _validate_kind(kind)
    now = utc_now()
    row = {
        "memory_id": stable_id("mem", resolved_kind, normalized, now, length=12),
        "kind": resolved_kind,
        "content": normalized,
        "enabled": True,
        "source": source,
        "created_at": now,
        "updated_at": now,
    }
    rows = _load_memory(cfg)
    rows.append(row)
    _write_memory(rows, cfg)
    return {"status": "MEMORY_SAVED", "memory": row}


def set_memory_enabled(
    memory_id: str,
    enabled: bool,
    cfg: Settings = settings,
) -> dict[str, Any]:
    rows = _load_memory(cfg)
    for index, row in enumerate(rows):
        if row.get("memory_id") == memory_id:
            updated = {**row, "enabled": enabled, "updated_at": utc_now()}
            rows[index] = updated
            _write_memory(rows, cfg)
            return {"status": "MEMORY_UPDATED", "memory": updated}
    return {"status": "ERROR_MEMORY_NOT_FOUND", "memory_id": memory_id}


def delete_memory_item(memory_id: str, cfg: Settings = settings) -> dict[str, Any]:
    rows = _load_memory(cfg)
    kept = [row for row in rows if row.get("memory_id") != memory_id]
    removed = len(rows) - len(kept)
    if removed:
        _write_memory(kept, cfg)
    return {
        "status": "MEMORY_DELETE_OK" if removed else "ERROR_MEMORY_NOT_FOUND",
        "memory_id": memory_id,
        "removed": removed,
        "remaining_memories": len(kept),
    }


def export_memory(cfg: Settings = settings) -> dict[str, Any]:
    payload = list_memory_items(cfg)
    payload["status"] = "MEMORY_EXPORT_OK"
    payload["exported_at"] = utc_now()
    return payload
