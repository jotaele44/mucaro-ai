from __future__ import annotations

import hashlib
import json
from typing import Any

from .memory import MEMORY_KINDS, active_memory_context
from .config import Settings, settings
from .contracts import validate_contract

MEMORY_SELECTION_SCHEMA_VERSION = "mucaro-memory-selection/1.0"
DEFAULT_MAX_ITEMS = 12
DEFAULT_MAX_CHARS = 4800


class MemorySelectionError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _normalize_content(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def select_memory_context(
    cfg: Settings = settings,
    *,
    items: list[dict[str, Any]] | None = None,
    allowed_kinds: list[str] | None = None,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    if not isinstance(max_items, int) or isinstance(max_items, bool) or max_items <= 0:
        raise MemorySelectionError("ERROR_MEMORY_SELECTION_MAX_ITEMS", "max_items must be a positive integer")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        raise MemorySelectionError("ERROR_MEMORY_SELECTION_MAX_CHARS", "max_chars must be a positive integer")

    if allowed_kinds is None:
        kinds = sorted(MEMORY_KINDS)
    else:
        if not isinstance(allowed_kinds, list) or not allowed_kinds:
            raise MemorySelectionError("ERROR_MEMORY_SELECTION_KINDS", "allowed_kinds must be a non-empty list")
        kinds = []
        for raw in allowed_kinds:
            kind = str(raw).strip().lower()
            if kind not in MEMORY_KINDS:
                raise MemorySelectionError("ERROR_MEMORY_SELECTION_KIND", kind)
            if kind not in kinds:
                kinds.append(kind)

    source_rows = list(items) if items is not None else active_memory_context(cfg)
    accepted: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_content: set[tuple[str, str]] = set()
    char_count = 0

    for index, row in enumerate(source_rows):
        if not isinstance(row, dict):
            excluded.append({"index": index, "reason": "INVALID_ROW"})
            continue
        memory_id = row.get("memory_id")
        kind = str(row.get("kind") or "").strip().lower()
        content = _normalize_content(row.get("content"))
        if row.get("enabled") is not True:
            excluded.append({"memory_id": memory_id, "reason": "DISABLED"})
            continue
        if kind not in kinds:
            excluded.append({"memory_id": memory_id, "reason": "KIND_NOT_ALLOWED"})
            continue
        if not content:
            excluded.append({"memory_id": memory_id, "reason": "EMPTY_CONTENT"})
            continue
        dedupe_key = (kind, content.casefold())
        if dedupe_key in seen_content:
            excluded.append({"memory_id": memory_id, "reason": "DUPLICATE_CONTENT"})
            continue
        if len(accepted) >= max_items:
            excluded.append({"memory_id": memory_id, "reason": "MAX_ITEMS"})
            continue
        proposed_chars = char_count + len(content)
        if proposed_chars > max_chars:
            excluded.append({"memory_id": memory_id, "reason": "MAX_CHARS"})
            continue
        seen_content.add(dedupe_key)
        char_count = proposed_chars
        accepted.append(
            {
                "memory_id": memory_id,
                "kind": kind,
                "content": content,
                "source": row.get("source"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )

    payload: dict[str, Any] = {
        "schema_version": MEMORY_SELECTION_SCHEMA_VERSION,
        "status": "MEMORY_SELECTION_OK",
        "allowed_kinds": kinds,
        "max_items": max_items,
        "max_chars": max_chars,
        "source_count": len(source_rows),
        "selected_count": len(accepted),
        "excluded_count": len(excluded),
        "selected_char_count": char_count,
        "selected": accepted,
        "excluded": excluded,
        "selected_memory_ids": [row["memory_id"] for row in accepted if row.get("memory_id")],
        "invariants": {
            "row_accounting_closed": len(source_rows) == len(accepted) + len(excluded),
            "item_limit_honored": len(accepted) <= max_items,
            "character_limit_honored": char_count <= max_chars,
            "all_selected_enabled": True,
            "all_selected_kinds_allowed": all(row["kind"] in kinds for row in accepted),
            "selected_content_unique_by_kind": len({(row["kind"], row["content"].casefold()) for row in accepted}) == len(accepted),
        },
        "limitations": [
            "Memory remains operator-controlled local context and is not source evidence.",
            "This selector bounds and deduplicates prompt context; it does not infer truth, importance, or sensitivity of a memory item.",
            "Durable memory provenance beyond the stored source label remains a separate evidence-plane concern.",
        ],
        "falsification_criteria": [
            "disabled memories never enter selected context",
            "duplicate normalized content within the same kind is included at most once",
            "selected context never exceeds declared item or character ceilings",
            "memory context must not be promoted into cited source evidence",
        ],
    }
    payload["selection_sha256"] = _stable_hash(payload)
    return validate_contract("memory_selection", payload)
