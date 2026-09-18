from __future__ import annotations

import pytest

from backend.app.memory_policy import MemorySelectionError, select_memory_context


def rows():
    return [
        {"memory_id": "m1", "kind": "preference", "content": "Prefer concise evidence.", "enabled": True, "source": "operator"},
        {"memory_id": "m2", "kind": "preference", "content": "  prefer   concise evidence. ", "enabled": True, "source": "operator"},
        {"memory_id": "m3", "kind": "fact", "content": "Use source IDs.", "enabled": False, "source": "operator"},
        {"memory_id": "m4", "kind": "instruction", "content": "Preserve provenance.", "enabled": True, "source": "operator"},
        {"memory_id": "m5", "kind": "profile", "content": "Local-first workflow.", "enabled": True, "source": "operator"},
    ]


def test_selection_excludes_disabled_and_duplicate_content() -> None:
    report = select_memory_context(items=rows(), max_items=10, max_chars=1000)

    assert report["status"] == "MEMORY_SELECTION_OK"
    assert report["selected_memory_ids"] == ["m1", "m4", "m5"]
    reasons = {(row.get("memory_id"), row["reason"]) for row in report["excluded"]}
    assert ("m2", "DUPLICATE_CONTENT") in reasons
    assert ("m3", "DISABLED") in reasons
    assert report["source_count"] == 5
    assert report["selected_count"] == 3
    assert report["excluded_count"] == 2
    assert all(report["invariants"].values())
    assert all(len(row["content_sha256"]) == 64 for row in report["selected"])


def test_kind_filter_and_item_limit_are_explicit() -> None:
    report = select_memory_context(items=rows(), allowed_kinds=["instruction", "profile"], max_items=1, max_chars=1000)

    assert report["selected_memory_ids"] == ["m4"]
    assert report["selected_count"] == 1
    assert report["excluded_count"] == 4
    reasons = [row["reason"] for row in report["excluded"]]
    assert "KIND_NOT_ALLOWED" in reasons
    assert "DISABLED" in reasons
    assert "MAX_ITEMS" in reasons


def test_character_limit_never_truncates_memory_silently() -> None:
    report = select_memory_context(items=rows(), max_items=10, max_chars=20)

    assert report["selected_memory_ids"] == ["m4"]
    assert report["selected_count"] == 1
    assert report["selected_char_count"] == len("Preserve provenance.")
    assert any(row["reason"] == "MAX_CHARS" for row in report["excluded"])
    assert report["invariants"]["character_limit_honored"] is True


def test_invalid_limits_and_kinds_fail_closed() -> None:
    with pytest.raises(MemorySelectionError) as items:
        select_memory_context(items=rows(), max_items=0)
    assert items.value.status == "ERROR_MEMORY_SELECTION_MAX_ITEMS"

    with pytest.raises(MemorySelectionError) as chars:
        select_memory_context(items=rows(), max_chars=0)
    assert chars.value.status == "ERROR_MEMORY_SELECTION_MAX_CHARS"

    with pytest.raises(MemorySelectionError) as kind:
        select_memory_context(items=rows(), allowed_kinds=["secret"])
    assert kind.value.status == "ERROR_MEMORY_SELECTION_KIND"
