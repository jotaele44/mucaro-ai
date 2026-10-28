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


def test_scope_selection_is_explicit_and_cross_scope_rows_are_excluded() -> None:
    scoped_rows = [
        {"memory_id": "global", "kind": "fact", "content": "Global context.", "enabled": True, "source": "test"},
        {"memory_id": "conv-a", "kind": "fact", "content": "Conversation A context.", "enabled": True, "source": "test", "scope": "conversation", "scope_id": "a"},
        {"memory_id": "conv-b", "kind": "fact", "content": "Conversation B context.", "enabled": True, "source": "test", "scope": "conversation", "scope_id": "b"},
        {"memory_id": "workspace-a", "kind": "fact", "content": "Workspace A context.", "enabled": True, "source": "test", "scope": "workspace", "scope_id": "a"},
    ]

    global_report = select_memory_context(items=scoped_rows)
    assert global_report["selected_memory_ids"] == ["global"]
    assert {row["memory_id"] for row in global_report["excluded"] if row["reason"] == "SCOPE_NOT_ELIGIBLE"} == {
        "conv-a",
        "conv-b",
        "workspace-a",
    }

    conversation_report = select_memory_context(items=scoped_rows, scope="conversation", scope_id="a")
    assert conversation_report["selected_memory_ids"] == ["global", "conv-a"]
    assert all(conversation_report["invariants"].values())
    assert any(row["memory_id"] == "conv-b" and row["reason"] == "SCOPE_NOT_ELIGIBLE" for row in conversation_report["excluded"])


def test_scoped_selection_requires_a_scope_id() -> None:
    with pytest.raises(MemorySelectionError) as exc:
        select_memory_context(items=rows(), scope="conversation")
    assert exc.value.status == "ERROR_MEMORY_SCOPE_ID_REQUIRED"
