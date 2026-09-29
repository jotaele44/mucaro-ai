from __future__ import annotations

from typing import Any

from .config import Settings, settings
from .contracts import validate_contract
from .utils import read_json, stable_id, update_json, utc_now

MEMORY_NAME = "assistant_memory.json"
MEMORY_KINDS = {"preference", "profile", "instruction", "fact"}
MEMORY_SCOPES = {"global", "workspace", "conversation"}
MAX_MEMORY_CONTENT_CHARS = 800
MAX_MEMORY_SCOPE_ID_CHARS = 160


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


def _validate_scope(scope: str, scope_id: str | None, cfg: Settings) -> tuple[str, str | None]:
    normalized = scope.strip().lower()
    if normalized not in MEMORY_SCOPES:
        allowed = ", ".join(sorted(MEMORY_SCOPES))
        raise MemoryError("ERROR_MEMORY_SCOPE_UNSUPPORTED", f"Memory scope must be one of: {allowed}.")
    resolved_id = " ".join(str(scope_id or "").strip().split()) or None
    if resolved_id is not None and len(resolved_id) > MAX_MEMORY_SCOPE_ID_CHARS:
        raise MemoryError(
            "ERROR_MEMORY_SCOPE_ID_TOO_LONG",
            f"Memory scope_id exceeds {MAX_MEMORY_SCOPE_ID_CHARS} characters.",
        )
    if normalized == "global":
        if resolved_id is not None:
            raise MemoryError("ERROR_MEMORY_SCOPE_ID_FOR_GLOBAL", "Global memory must not declare a scope_id.")
        return normalized, None
    if normalized == "workspace":
        return normalized, resolved_id or current_workspace_scope_id(cfg)
    if resolved_id is None:
        raise MemoryError(
            "ERROR_MEMORY_CONVERSATION_SCOPE_REQUIRED",
            "Conversation-scoped memory requires a conversation scope_id.",
        )
    return normalized, resolved_id


def current_workspace_scope_id(cfg: Settings = settings) -> str:
    return stable_id("workspace", cfg.workspace_profile, str(cfg.project_root), length=12)


def _normalize_stored_row(row: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    """Expose legacy rows as explicit global memory without rewriting source bytes."""
    normalized = dict(row)
    scope = normalized.get("scope")
    if scope is None:
        normalized["scope"] = "global"
        normalized["scope_id"] = None
        normalized["scope_migration"] = "LEGACY_DEFAULT_GLOBAL"
        return normalized
    try:
        resolved_scope, resolved_scope_id = _validate_scope(str(scope), normalized.get("scope_id"), cfg)
    except MemoryError:
        normalized["scope"] = "invalid"
        normalized["scope_id"] = normalized.get("scope_id")
        normalized["scope_migration"] = "INVALID_STORED_SCOPE"
        return normalized
    normalized["scope"] = resolved_scope
    normalized["scope_id"] = resolved_scope_id
    return normalized


def _load_memory(cfg: Settings) -> list[dict[str, Any]]:
    rows = read_json(_memory_path(cfg), [])
    return [
        _normalize_stored_row(row, cfg)
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("memory_id"), str)
    ]


def _valid_rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get("memory_id"), str)]


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
    scope: str = "global",
    scope_id: str | None = None,
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
    resolved_scope, resolved_scope_id = _validate_scope(scope, scope_id, cfg)
    now = utc_now()
    row = {
        "memory_id": stable_id("mem", resolved_kind, resolved_scope, str(resolved_scope_id), normalized, now, length=12),
        "kind": resolved_kind,
        "content": normalized,
        "enabled": True,
        "source": source,
        "scope": resolved_scope,
        "scope_id": resolved_scope_id,
        "created_at": now,
        "updated_at": now,
    }
    validate_contract("memory_item", row)
    update_json(_memory_path(cfg), [], lambda rows: rows.append(row))
    return {"status": "MEMORY_SAVED", "memory": row}


def set_memory_enabled(
    memory_id: str,
    enabled: bool,
    cfg: Settings = settings,
) -> dict[str, Any]:
    def mutate(rows: list[Any]) -> dict[str, Any] | None:
        for index, row in enumerate(rows):
            if isinstance(row, dict) and row.get("memory_id") == memory_id:
                canonical = _normalize_stored_row(row, cfg)
                canonical.pop("scope_migration", None)
                updated = validate_contract("memory_item", {**canonical, "enabled": enabled, "updated_at": utc_now()})
                rows[index] = updated
                return updated
        return None

    updated = update_json(_memory_path(cfg), [], mutate)
    if updated is None:
        return {"status": "ERROR_MEMORY_NOT_FOUND", "memory_id": memory_id}
    return {"status": "MEMORY_UPDATED", "memory": updated}


def delete_memory_item(memory_id: str, cfg: Settings = settings) -> dict[str, Any]:
    def mutate(rows: list[Any]) -> tuple[int, int]:
        valid = _valid_rows(rows)
        kept = [row for row in valid if row.get("memory_id") != memory_id]
        rows[:] = kept
        return len(valid) - len(kept), len(kept)

    removed, remaining = update_json(_memory_path(cfg), [], mutate)
    return {
        "status": "MEMORY_DELETE_OK" if removed else "ERROR_MEMORY_NOT_FOUND",
        "memory_id": memory_id,
        "removed": removed,
        "remaining_memories": remaining,
    }


def export_memory(cfg: Settings = settings) -> dict[str, Any]:
    payload = list_memory_items(cfg)
    payload["status"] = "MEMORY_EXPORT_OK"
    payload["exported_at"] = utc_now()
    return payload
